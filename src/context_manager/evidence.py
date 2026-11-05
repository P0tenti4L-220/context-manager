"""Evidence snapshots and bounded reads (F02).

The store is the only supported path for capturing project bytes and for
reading them back.  It deliberately keeps the model-facing read contract small:
``list_evidence``, ``read_evidence`` and ``validate_ref``.  Full-byte and digest
helpers exist for internal components (F05/F08) and never add model coverage.

Only the standard library is used.  No network, Git or subprocess calls are
made, and project files are opened component by component without following
symbolic links below the resolved project root.
"""

from __future__ import annotations

import errno
import fcntl
import hashlib
import json
import os
import re
import stat
import unicodedata
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Sequence

from .contracts import (
    SCHEMA_VERSION,
    ContractError,
    CoverageItem,
    CoverageStatus,
    Evidence,
    EvidenceOrigin,
    EvidenceRef,
    Provenance,
    ProvenanceKind,
    dump_object,
    parse_object,
)

TOOL_VERSION = "0.1.0"
STORE_VERSION = 1

ALLOWED_KINDS = frozenset(
    {
        "rule_file",
        "document",
        "code",
        "test",
        "data",
        "event_package",
        "decision_registry",
        "other",
    }
)
ALLOWED_CALLERS = frozenset({"inspector", "analyst", "report", "test", "unknown"})

_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_INPUT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_DRIVE_RE = re.compile(r"^[A-Za-z]:")
_TIMESTAMP_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$"
)
_EVIDENCE_ID_RE = re.compile(r"^ev_(?P<run>.+)_(?P<seq>[0-9]{4,})$")

_TEXT_ENCODINGS = frozenset({"utf-8", "utf-8-bom"})

_EXIT_CODES = {
    "evidence_snapshot_corrupt": 1,
    "evidence_index_corrupt": 1,
    "evidence_path_invalid": 2,
    "evidence_path_escape": 2,
    "evidence_limit_invalid": 2,
    "evidence_output_inside_project": 2,
    "evidence_output_contains_project": 2,
    "evidence_run_conflict": 2,
    "evidence_run_id_invalid": 2,
    "evidence_input_id_invalid": 2,
    "evidence_input_id_conflict": 2,
    "evidence_ref_invalid": 2,
    "evidence_range_invalid": 2,
}


# ---------------------------------------------------------------------------
# Errors and value objects


class EvidenceError(Exception):
    """A structured evidence error carrying a stable code and details."""

    def __init__(
        self, code: str, message: str, details: Mapping[str, Any] | None = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = dict(details or {})

    @property
    def exit_code(self) -> int:
        return _EXIT_CODES.get(self.code, 3)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "error": {
                "code": self.code,
                "message": self.message,
                "details": _jsonable(self.details),
            },
        }


def _jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _error(code: str, message: str, **details: Any) -> EvidenceError:
    return EvidenceError(code, message, details)


@dataclass(frozen=True)
class EvidenceLimits:
    max_project_file_bytes: int = 1_048_576
    max_selected_input_bytes: int = 10_485_760
    max_read_lines: int = 80
    max_read_bytes: int = 8_192
    capture_retry_limit: int = 1

    def __post_init__(self) -> None:
        _validate_limits(self)



_LIMIT_FIELDS = (
    ("max_project_file_bytes", 1_048_576, False),
    ("max_selected_input_bytes", 10_485_760, False),
    ("max_read_lines", 80, False),
    ("max_read_bytes", 8_192, False),
    ("capture_retry_limit", 1, True),
)


def _validate_limits(limits: EvidenceLimits) -> None:
    for name, ceiling, allows_zero in _LIMIT_FIELDS:
        value = getattr(limits, name)
        if isinstance(value, bool) or not isinstance(value, int):
            raise _error(
                "evidence_limit_invalid",
                "limit must be an integer",
                field=name,
                value=repr(value),
            )
        if value < 0:
            raise _error(
                "evidence_limit_invalid",
                "limit must not be negative",
                field=name,
                value=value,
            )
        if value == 0 and not allows_zero:
            raise _error(
                "evidence_limit_invalid",
                "limit must be greater than zero",
                field=name,
                value=value,
            )
        if value > ceiling:
            raise _error(
                "evidence_limit_invalid",
                "limit must not exceed the default",
                field=name,
                value=value,
                maximum=ceiling,
            )


@dataclass(frozen=True)
class SelectedInput:
    data: bytes
    basename: str
    sha256: str

    @property
    def byte_length(self) -> int:
        return len(self.data)


@dataclass(frozen=True)
class CaptureOutcome:
    evidence: Evidence | None
    coverage: CoverageItem
    error: EvidenceError | None


@dataclass(frozen=True)
class EvidenceRead:
    read_id: str
    evidence_id: str
    relative_path: str
    sha256: str
    text: str
    line_start: int
    line_end: int
    requested_line_start: int
    requested_line_end: int | None
    empty: bool
    truncated: bool
    truncation_reason: str | None
    next_line: int | None
    raw_bytes: int
    text_bytes: int
    span_sha256: str
    at: str
    caller: str

    def as_receipt(self) -> dict[str, Any]:
        return {
            "read_id": self.read_id,
            "requested_line_start": self.requested_line_start,
            "requested_line_end": self.requested_line_end,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "raw_bytes": self.raw_bytes,
            "truncated": self.truncated,
            "at": self.at,
            "caller": self.caller,
        }


@dataclass(frozen=True)
class RefValidation:
    ok: bool
    code: str | None
    detail: dict[str, Any]
    covered: bool


@dataclass(frozen=True)
class PathProbe:
    status: str
    is_symlink: bool
    is_dir: bool
    byte_length: int | None


@dataclass(frozen=True)
class DigestCheck:
    ok: bool
    code: str | None
    actual_sha256: str | None


@dataclass(frozen=True)
class EvidenceLocation:
    evidence_id: str
    artifact_path: str
    sha256: str
    byte_length: int
    line_count: int
    logical_name: str
    encoding: str
    provenance_kind: str


class _ReadChanged(Exception):
    """Internal marker: the file changed while it was being read."""

    def __init__(self, data: bytes | None = None, signature: tuple | None = None) -> None:
        super().__init__("changed during read")
        self.data = data
        self.signature = signature


# ---------------------------------------------------------------------------
# Small helpers


def _now(clock: Callable[[], datetime] | None) -> datetime:
    value = clock() if clock is not None else datetime.now().astimezone()
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value


def _timestamp(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def _parse_timestamp(value: str) -> datetime:
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    return datetime.fromisoformat(text)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _signature(st: os.stat_result) -> tuple[int, int, int, int]:
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)


def _nfc(value: str) -> str:
    return unicodedata.normalize("NFC", value)


def _line_starts(data: bytes) -> list[int]:
    if not data:
        return []
    starts = [0]
    position = data.find(b"\n")
    while position != -1:
        next_start = position + 1
        if next_start < len(data):
            starts.append(next_start)
        position = data.find(b"\n", next_start)
    return starts


def _line_starts_sha256(starts: Sequence[int]) -> str:
    return _sha256(",".join(str(value) for value in starts).encode("ascii"))


def _count_lines(data: bytes) -> int:
    if not data:
        return 0
    newlines = data.count(b"\n")
    return newlines if data.endswith(b"\n") else newlines + 1


def _newline_style(data: bytes) -> str:
    crlf = data.count(b"\r\n")
    lf = data.count(b"\n") - crlf
    if crlf and lf:
        return "mixed"
    if crlf:
        return "crlf"
    if lf:
        return "lf"
    return "none"


def _encoding_of(data: bytes) -> str:
    if b"\x00" in data[:8192]:
        return "binary"
    has_bom = data.startswith(b"\xef\xbb\xbf")
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return "invalid-utf8"
    return "utf-8-bom" if has_bom else "utf-8"


def _snapshot_id(relative_path: str, sha256: str) -> str:
    digest = hashlib.sha256(
        b"context-manager/snapshot/v1\n"
        + relative_path.encode("utf-8")
        + b"\n"
        + sha256.encode("ascii")
    ).hexdigest()
    return "snap_" + digest[:32]


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8")


def _parse_evidence_id(evidence_id: str) -> tuple[str, int]:
    match = _EVIDENCE_ID_RE.fullmatch(evidence_id)
    if match is None or not _RUN_ID_RE.fullmatch(match.group("run")):
        raise _error(
            "evidence_ref_invalid",
            "evidence_id is not a valid evidence identifier",
            field="evidence_id",
        )
    return match.group("run"), int(match.group("seq"))


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return child != parent


# ---------------------------------------------------------------------------
# Path validation and component-safe opening


def _validate_relative_path(value: Any, *, field_name: str = "relative_path") -> str:
    """Validate a lexical project-relative path and return its NFC form."""

    if not isinstance(value, str):
        raise _error(
            "evidence_path_invalid",
            "path must be a string",
            field=field_name,
        )
    if value == "":
        raise _error("evidence_path_invalid", "path must not be empty", field=field_name)
    if "\x00" in value:
        raise _error("evidence_path_invalid", "path must not contain NUL", field=field_name)
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in value):
        raise _error(
            "evidence_path_invalid",
            "path must not contain control characters",
            field=field_name,
        )
    if len(value) > 1024:
        raise _error(
            "evidence_path_invalid",
            "path must be at most 1024 characters",
            field=field_name,
            length=len(value),
        )
    if value.startswith("/"):
        raise _error(
            "evidence_path_invalid",
            "absolute paths are not allowed; use capture_selected_input",
            field=field_name,
        )
    if _DRIVE_RE.match(value):
        raise _error(
            "evidence_path_invalid",
            "drive letters are not allowed",
            field=field_name,
        )
    if "\\" in value:
        raise _error(
            "evidence_path_invalid",
            "path must use '/' separators",
            field=field_name,
        )
    if value.endswith("/"):
        raise _error(
            "evidence_path_invalid",
            "path must not end with '/'",
            field=field_name,
        )
    parts = value.split("/")
    if any(part == "" for part in parts):
        raise _error(
            "evidence_path_invalid",
            "path must not contain empty components",
            field=field_name,
        )
    if any(part in {".", ".."} for part in parts):
        raise _error(
            "evidence_path_invalid",
            "path must not contain '.' or '..' components",
            field=field_name,
        )
    return _nfc(value)


def _validate_input_id(value: Any) -> str:
    if not isinstance(value, str) or not _INPUT_ID_RE.fullmatch(value):
        raise _error(
            "evidence_input_id_invalid",
            "input_id must match [a-z0-9][a-z0-9._-]{0,63}",
            input_id=value if isinstance(value, str) else repr(value),
        )
    return value


def _validate_kind(value: Any) -> str:
    if not isinstance(value, str) or value not in ALLOWED_KINDS:
        raise _error(
            "evidence_ref_invalid",
            "unknown evidence kind",
            field="kind",
            value=value if isinstance(value, str) else repr(value),
            allowed=sorted(ALLOWED_KINDS),
        )
    return value


def _validate_provenance(value: Any, default_kind: ProvenanceKind) -> Provenance:
    if value is None:
        return Provenance(default_kind, None, None)
    if not isinstance(value, Provenance):
        raise _error(
            "evidence_ref_invalid",
            "provenance must be a Provenance object",
            field="provenance",
        )
    if not isinstance(value.kind, ProvenanceKind):
        try:
            kind = ProvenanceKind(value.kind)
        except (TypeError, ValueError):
            raise _error(
                "evidence_ref_invalid",
                "unknown provenance kind",
                field="provenance.kind",
            ) from None
        value = Provenance(kind, value.adapter, value.version)
    kind = value.kind
    adapter = value.adapter
    version = value.version
    if adapter is not None and not isinstance(adapter, str):
        raise _error(
            "evidence_ref_invalid",
            "provenance adapter must be a string or null",
            field="provenance.adapter",
        )
    if version is not None and not isinstance(version, str):
        raise _error(
            "evidence_ref_invalid",
            "provenance version must be a string or null",
            field="provenance.version",
        )
    if kind in (ProvenanceKind.PROJECT_FILE, ProvenanceKind.USER_SUPPLIED):
        if adapter not in (None, "") or version not in (None, ""):
            raise _error(
                "evidence_ref_invalid",
                "project and user provenance must not carry adapter metadata",
                field="provenance.adapter",
            )
    elif kind is ProvenanceKind.ADAPTER:
        if not adapter or not version:
            raise _error(
                "evidence_ref_invalid",
                "adapter provenance requires adapter and version",
                field="provenance.adapter",
            )
    else:  # synthetic
        if (adapter is None) != (version is None):
            raise _error(
                "evidence_ref_invalid",
                "synthetic provenance needs adapter and version together",
                field="provenance.adapter",
            )
        if adapter == "" or version == "":
            raise _error(
                "evidence_ref_invalid",
                "synthetic provenance fields must not be empty",
                field="provenance.adapter",
            )
    return Provenance(kind, adapter, version)


def _validate_expires_at(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not _TIMESTAMP_RE.fullmatch(value):
        raise _error(
            "evidence_ref_invalid",
            "expires_at must be a timezone-aware ISO 8601 timestamp",
            field="expires_at",
        )
    try:
        _parse_timestamp(value)
    except ValueError:
        raise _error(
            "evidence_ref_invalid",
            "expires_at must be a real timezone-aware ISO 8601 timestamp",
            field="expires_at",
        ) from None
    return value


def _validate_purpose(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise _error("evidence_ref_invalid", "purpose must be a string", field="purpose")
    return value


def _validate_source_revision(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or value == "":
        raise _error(
            "evidence_ref_invalid",
            "source_revision must be a non-empty string or null",
            field="source_revision",
        )
    return value


class _ProjectWalker:
    """Open project paths component by component without following links."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def _open_root(self) -> int:
        try:
            return os.open(self.root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        except FileNotFoundError:
            raise _error(
                "evidence_not_found", "project root is no longer available"
            ) from None
        except PermissionError:
            raise _error("evidence_unreadable", "project root is not readable") from None
        except OSError as exc:
            if exc.errno in (errno.ELOOP, errno.ENOTDIR):
                raise _error("evidence_not_found", "project root is not a directory") from None
            raise _error("evidence_unreadable", "project root cannot be opened") from None

    def _lstat_at(self, fd: int, component: str) -> os.stat_result:
        try:
            return os.stat(component, dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError:
            raise _error(
                "evidence_not_found",
                "path component does not exist",
                component=component,
            ) from None
        except NotADirectoryError:
            raise _error(
                "evidence_not_found",
                "path component is not a directory",
                component=component,
            ) from None
        except PermissionError:
            raise _error(
                "evidence_unreadable",
                "path component is not readable",
                component=component,
            ) from None
        except OSError as exc:
            if exc.errno in (errno.ELOOP, errno.ENOTDIR):
                raise _error(
                    "evidence_not_found",
                    "path component is not usable",
                    component=component,
                ) from None
            raise _error(
                "evidence_unreadable",
                "path component cannot be inspected",
                component=component,
            ) from None

    def check(self, relative_path: str) -> str:
        """Validate one project-relative path; return it unchanged."""

        parts = relative_path.split("/")
        fd = self._open_root()
        try:
            for component in parts[:-1]:
                st = self._lstat_at(fd, component)
                if stat.S_ISLNK(st.st_mode):
                    raise _error(
                        "evidence_symlink_rejected",
                        "path contains a symbolic link",
                        component=component,
                    )
                if not stat.S_ISDIR(st.st_mode):
                    raise _error(
                        "evidence_not_found",
                        "path component is not a directory",
                        component=component,
                    )
                try:
                    child = os.open(
                        component,
                        os.O_RDONLY
                        | getattr(os, "O_DIRECTORY", 0)
                        | getattr(os, "O_NOFOLLOW", 0),
                        dir_fd=fd,
                    )
                except OSError as exc:
                    if exc.errno == errno.ELOOP:
                        raise _error(
                            "evidence_symlink_rejected",
                            "path contains a symbolic link",
                            component=component,
                        ) from None
                    raise _error(
                        "evidence_unreadable",
                        "path component cannot be opened",
                        component=component,
                    ) from None
                os.close(fd)
                fd = child
            final = parts[-1]
            st = self._lstat_at(fd, final)
            if stat.S_ISLNK(st.st_mode):
                raise _error(
                    "evidence_symlink_rejected",
                    "path ends in a symbolic link",
                    component=final,
                )
            if not stat.S_ISREG(st.st_mode):
                raise _error(
                    "evidence_not_regular_file",
                    "path is not a regular file",
                    component=final,
                )
        finally:
            os.close(fd)

        self._check_escape(relative_path)
        return relative_path

    def _check_escape(self, relative_path: str) -> None:
        try:
            real = Path(os.path.realpath(self.root / relative_path))
        except OSError:
            raise _error(
                "evidence_unreadable", "path cannot be resolved"
            ) from None
        if not _is_within(real, self.root) and real != self.root:
            raise _error(
                "evidence_path_escape",
                "resolved path leaves the project root",
                relative_path=relative_path,
            )


# ---------------------------------------------------------------------------
# Stable file reads


def _read_file_once(path: Path, limit: int) -> tuple[bytes, tuple[int, int, int, int]]:
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        raise _error("evidence_not_found", "file does not exist") from None
    except PermissionError:
        raise _error("evidence_unreadable", "file is not readable") from None
    except OSError:
        raise _error("evidence_unreadable", "file cannot be inspected") from None
    if stat.S_ISLNK(st.st_mode):
        raise _error("evidence_symlink_rejected", "path is a symbolic link")
    if not stat.S_ISREG(st.st_mode):
        raise _error("evidence_not_regular_file", "path is not a regular file")
    if st.st_size > limit:
        raise _error(
            "evidence_too_large",
            "file exceeds the configured limit",
            byte_length=st.st_size,
            limit=limit,
        )

    signature = _signature(st)
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        raise _error("evidence_not_found", "file does not exist") from None
    except PermissionError:
        raise _error("evidence_unreadable", "file is not readable") from None
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise _error("evidence_symlink_rejected", "path is a symbolic link") from None
        raise _error("evidence_unreadable", "file cannot be opened") from None

    try:
        opened = os.fstat(fd)
        if (opened.st_dev, opened.st_ino) != (st.st_dev, st.st_ino):
            raise _ReadChanged()
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            total += len(chunk)
            if total > limit:
                raise _error(
                    "evidence_too_large",
                    "file exceeds the configured limit",
                    byte_length=total,
                    limit=limit,
                )
            chunks.append(chunk)
        after = os.fstat(fd)
        data = b"".join(chunks)
        if after.st_size != len(data):
            raise _ReadChanged(data, _signature(after))
        try:
            current = os.lstat(path)
        except OSError:
            raise _ReadChanged(data, _signature(after)) from None
        if _signature(current) != signature:
            raise _ReadChanged(data, _signature(current))
        return data, signature
    except OSError:
        raise _error("evidence_unreadable", "file cannot be read") from None
    finally:
        os.close(fd)


def _signature_matches(path: Path, signature: tuple[int, int, int, int] | None) -> bool:
    if signature is None:
        return False
    try:
        st = os.lstat(path)
    except OSError:
        return False
    if stat.S_ISLNK(st.st_mode):
        return False
    return _signature(st) == signature


def _project_relative_parts(root: Path, path: Path) -> tuple[str, ...]:
    try:
        relative = Path(path).relative_to(root)
    except ValueError:
        raise _error(
            "evidence_path_escape",
            "path is outside the project root",
            relative_path=str(path),
        ) from None
    parts = relative.parts
    if not parts or parts == (".",) or ".." in parts:
        raise _error(
            "evidence_path_escape",
            "path is outside the project root",
            relative_path=str(path),
        )
    return parts


def _open_project_dir(root: Path) -> int:
    try:
        return os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    except FileNotFoundError:
        raise _error("evidence_not_found", "project root is no longer available") from None
    except PermissionError:
        raise _error("evidence_unreadable", "project root is not readable") from None
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            raise _error("evidence_not_found", "project root is not a directory") from None
        raise _error("evidence_unreadable", "project root cannot be opened") from None


def _open_project_parent_fd(root: Path, path: Path) -> tuple[int, tuple[str, ...]]:
    """Open every parent component with O_NOFOLLOW and return (dirfd, parts)."""

    parts = _project_relative_parts(root, path)
    fd = _open_project_dir(root)
    try:
        for component in parts[:-1]:
            try:
                st = os.stat(component, dir_fd=fd, follow_symlinks=False)
            except FileNotFoundError:
                raise _error(
                    "evidence_not_found",
                    "path component does not exist",
                    component=component,
                ) from None
            except NotADirectoryError:
                raise _error(
                    "evidence_not_found",
                    "path component is not a directory",
                    component=component,
                ) from None
            except PermissionError:
                raise _error(
                    "evidence_unreadable",
                    "path component is not readable",
                    component=component,
                ) from None
            except OSError as exc:
                if exc.errno in (errno.ELOOP, errno.ENOTDIR):
                    raise _error(
                        "evidence_not_found",
                        "path component is not usable",
                        component=component,
                    ) from None
                raise _error(
                    "evidence_unreadable",
                    "path component cannot be inspected",
                    component=component,
                ) from None
            if stat.S_ISLNK(st.st_mode):
                raise _error(
                    "evidence_symlink_rejected",
                    "path contains a symbolic link",
                    component=component,
                )
            if not stat.S_ISDIR(st.st_mode):
                raise _error(
                    "evidence_not_found",
                    "path component is not a directory",
                    component=component,
                )
            try:
                child = os.open(
                    component,
                    os.O_RDONLY
                    | getattr(os, "O_DIRECTORY", 0)
                    | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=fd,
                )
            except OSError as exc:
                if exc.errno == errno.ELOOP:
                    raise _error(
                        "evidence_symlink_rejected",
                        "path contains a symbolic link",
                        component=component,
                    ) from None
                if exc.errno in (errno.ENOENT, errno.ENOTDIR):
                    raise _error(
                        "evidence_not_found",
                        "path component does not exist",
                        component=component,
                    ) from None
                if exc.errno in (errno.EACCES, errno.EPERM):
                    raise _error(
                        "evidence_unreadable",
                        "path component is not readable",
                        component=component,
                    ) from None
                raise _error(
                    "evidence_unreadable",
                    "path component cannot be opened",
                    component=component,
                ) from None
            os.close(fd)
            fd = child
        return fd, parts
    except BaseException:
        os.close(fd)
        raise


def _lstat_project_file(root: Path, path: Path) -> os.stat_result:
    fd, parts = _open_project_parent_fd(root, path)
    final = parts[-1]
    try:
        try:
            st = os.stat(final, dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError:
            raise _error(
                "evidence_not_found",
                "file does not exist",
                component=final,
            ) from None
        except PermissionError:
            raise _error(
                "evidence_unreadable",
                "file is not readable",
                component=final,
            ) from None
        except OSError as exc:
            if exc.errno in (errno.ELOOP, errno.ENOTDIR):
                raise _error(
                    "evidence_not_found",
                    "file is not usable",
                    component=final,
                ) from None
            raise _error(
                "evidence_unreadable",
                "file cannot be inspected",
                component=final,
            ) from None
        if stat.S_ISLNK(st.st_mode):
            raise _error(
                "evidence_symlink_rejected",
                "path ends in a symbolic link",
                component=final,
            )
        if not stat.S_ISREG(st.st_mode):
            raise _error(
                "evidence_not_regular_file",
                "path is not a regular file",
                component=final,
            )
        return st
    finally:
        os.close(fd)


def _read_file_once_project(
    root: Path, path: Path, limit: int
) -> tuple[bytes, tuple[int, int, int, int]]:
    """Read a project file through a dirfd chain, without following symlinks."""

    fd, parts = _open_project_parent_fd(root, path)
    final = parts[-1]
    file_fd: int | None = None
    try:
        try:
            st = os.stat(final, dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError:
            raise _error("evidence_not_found", "file does not exist") from None
        except PermissionError:
            raise _error("evidence_unreadable", "file is not readable") from None
        except OSError as exc:
            if exc.errno in (errno.ELOOP, errno.ENOTDIR):
                raise _error("evidence_not_found", "file is not usable") from None
            raise _error("evidence_unreadable", "file cannot be inspected") from None
        if stat.S_ISLNK(st.st_mode):
            raise _error("evidence_symlink_rejected", "path is a symbolic link")
        if not stat.S_ISREG(st.st_mode):
            raise _error("evidence_not_regular_file", "path is not a regular file")
        if st.st_size > limit:
            raise _error(
                "evidence_too_large",
                "file exceeds the configured limit",
                byte_length=st.st_size,
                limit=limit,
            )
        signature = _signature(st)
        try:
            file_fd = os.open(
                final,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=fd,
            )
        except FileNotFoundError:
            raise _error("evidence_not_found", "file does not exist") from None
        except PermissionError:
            raise _error("evidence_unreadable", "file is not readable") from None
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise _error(
                    "evidence_symlink_rejected", "path is a symbolic link"
                ) from None
            if exc.errno == errno.EISDIR:
                raise _error(
                    "evidence_not_regular_file", "path is not a regular file"
                ) from None
            raise _error("evidence_unreadable", "file cannot be opened") from None

        opened = os.fstat(file_fd)
        if (opened.st_dev, opened.st_ino) != (st.st_dev, st.st_ino):
            raise _ReadChanged()
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(file_fd, 65536)
            if not chunk:
                break
            total += len(chunk)
            if total > limit:
                raise _error(
                    "evidence_too_large",
                    "file exceeds the configured limit",
                    byte_length=total,
                    limit=limit,
                )
            chunks.append(chunk)
        after = os.fstat(file_fd)
        data = b"".join(chunks)
        if after.st_size != len(data):
            raise _ReadChanged(data, _signature(after))
        # Re-check the final name against the already verified parent dirfd.
        # This catches replacement during the read without re-resolving the path.
        try:
            current = os.stat(final, dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError:
            raise _ReadChanged(data, _signature(after)) from None
        except OSError:
            raise _ReadChanged(data, _signature(after)) from None
        if stat.S_ISLNK(current.st_mode):
            raise _error("evidence_symlink_rejected", "path is a symbolic link")
        if not stat.S_ISREG(current.st_mode):
            raise _error("evidence_not_regular_file", "path is not a regular file")
        if _signature(current) != signature:
            raise _ReadChanged(data, _signature(current))
        return data, signature
    except OSError:
        raise _error("evidence_unreadable", "file cannot be read") from None
    finally:
        if file_fd is not None:
            os.close(file_fd)
        os.close(fd)


def _read_stable_once(
    path: Path, limit: int, retries: int = 1
) -> tuple[bytes, tuple[int, int, int, int]]:
    """Stable read used by ``prepare_selected_input`` (outside a store)."""

    last: bytes | None = None
    same_content = False
    for _attempt in range(retries + 1):
        try:
            data, signature = _read_file_once(path, limit)
        except _ReadChanged as exc:
            data = exc.data
            signature = None
        if data is not None and _signature_matches(path, signature):
            if len(data) > limit:
                raise _error(
                    "evidence_too_large",
                    "file exceeds the configured limit",
                    byte_length=len(data),
                    limit=limit,
                )
            return data, signature  # type: ignore[return-value]
        if last is not None and data == last:
            same_content = True
            if len(data) > limit:
                raise _error(
                    "evidence_too_large",
                    "file exceeds the configured limit",
                    byte_length=len(data),
                    limit=limit,
                )
            return data, signature or (0, 0, len(data), 0)  # type: ignore[return-value]
        last = data
    if same_content:  # pragma: no cover - kept for defensive clarity
        return last or b"", (0, 0, len(last or b""), 0)
    raise _error(
        "evidence_changed_during_read",
        "file changed while it was being read",
    )


# ---------------------------------------------------------------------------
# Store


class EvidenceStore:
    """A run-scoped evidence store."""

    def __init__(
        self,
        project_root: str | os.PathLike[str],
        output_root: str | os.PathLike[str],
        run_id: str,
        *,
        limits: EvidenceLimits | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.clock = clock
        self._closed = False
        self._validate_run_id(run_id)
        try:
            root = Path(project_root).resolve(strict=True)
        except (OSError, RuntimeError):
            raise _error(
                "evidence_not_found", "project root does not exist"
            ) from None
        if not root.is_dir():
            raise _error("evidence_not_found", "project root is not a directory")
        self.project_root = root

        out = Path(output_root).expanduser().resolve()
        self._validate_output_root(out, root)

        requested_limits = limits if limits is not None else EvidenceLimits()
        _validate_limits(requested_limits)

        self.run_dir = out / run_id
        self.evidence_dir = self.run_dir / "evidence"
        self.run_id = run_id
        self.output_root = out
        self.limits = requested_limits
        self.created_at = _timestamp(_now(clock))
        self._walker = _ProjectWalker(root)

        self._create_or_open_run()

    # -- construction -------------------------------------------------

    @staticmethod
    def _validate_run_id(run_id: Any) -> None:
        if not isinstance(run_id, str) or not _RUN_ID_RE.fullmatch(run_id):
            raise _error(
                "evidence_run_id_invalid",
                "run_id must match [A-Za-z0-9][A-Za-z0-9._-]{0,63}",
                run_id=run_id if isinstance(run_id, str) else repr(run_id),
            )

    @staticmethod
    def _validate_output_root(out: Path, project_root: Path) -> None:
        if out == project_root or _is_within(out, project_root):
            raise _error(
                "evidence_output_inside_project",
                "output root must not be inside the project root",
                output_root=str(out),
                project_root=str(project_root),
            )
        if _is_within(project_root, out):
            raise _error(
                "evidence_output_contains_project",
                "project root must not be inside the output root",
                output_root=str(out),
                project_root=str(project_root),
            )

    def _store_document(self) -> dict[str, Any]:
        return {
            "schema_version": STORE_VERSION,
            "run_id": self.run_id,
            "project_root": str(self.project_root),
            "created_at": self.created_at,
            "limits": asdict(self.limits),
            "tool_version": TOOL_VERSION,
        }

    def _create_or_open_run(self) -> None:
        store_path = self.run_dir / "_store.json"
        if store_path.exists():
            stored = self._read_store_document(store_path)
            if stored["run_id"] != self.run_id or stored["project_root"] != str(
                self.project_root
            ):
                raise _error(
                    "evidence_run_conflict",
                    "run directory already belongs to another run",
                    run_dir=str(self.run_dir),
                )
            self.created_at = stored["created_at"]
            try:
                self.limits = EvidenceLimits(**stored["limits"])
            except (TypeError, EvidenceError):
                raise _error(
                    "evidence_index_corrupt",
                    "stored limits are invalid",
                    run_dir=str(self.run_dir),
                ) from None
            self.evidence_dir.mkdir(parents=True, exist_ok=True)
        else:
            self.evidence_dir.mkdir(parents=True, exist_ok=True)
            self._write_json_atomic(store_path, self._store_document())

    @staticmethod
    def _read_store_document(path: Path) -> dict[str, Any]:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            raise _error(
                "evidence_index_corrupt", "_store.json cannot be parsed"
            ) from None
        if not isinstance(raw, dict) or raw.get("schema_version") != STORE_VERSION:
            raise _error("evidence_index_corrupt", "_store.json has an unknown version")
        required = {"run_id", "project_root", "created_at", "limits"}
        if not required.issubset(raw):
            raise _error("evidence_index_corrupt", "_store.json is incomplete")
        if not isinstance(raw["limits"], dict):
            raise _error("evidence_index_corrupt", "_store.json limits are invalid")
        return raw

    @classmethod
    def open_existing(
        cls,
        run_dir: str | os.PathLike[str],
        *,
        limits: EvidenceLimits | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> "EvidenceStore":
        path = Path(run_dir).expanduser().resolve()
        store_path = path / "_store.json"
        if not store_path.is_file():
            raise _error(
                "evidence_not_found",
                "run directory has no _store.json",
                run_dir=str(path),
            )
        stored = cls._read_store_document(store_path)
        cls._validate_run_id(stored["run_id"])
        evidence_dir = path / "evidence"
        if not evidence_dir.is_dir():
            raise _error(
                "evidence_index_corrupt",
                "run directory has no evidence directory",
                run_dir=str(path),
            )
        try:
            stored_limits = EvidenceLimits(**stored["limits"])
        except (TypeError, EvidenceError):
            raise _error(
                "evidence_index_corrupt", "stored limits are invalid"
            ) from None

        obj = cls.__new__(cls)
        obj.clock = clock
        obj._closed = False
        obj.run_dir = path
        obj.evidence_dir = evidence_dir
        obj.run_id = stored["run_id"]
        obj.output_root = path.parent
        obj.project_root = Path(stored["project_root"])
        obj.created_at = stored["created_at"]
        obj.limits = limits if limits is not None else stored_limits
        _validate_limits(obj.limits)
        obj._walker = _ProjectWalker(obj.project_root)
        # Touch the indexes now so corruption is reported at open time.
        obj._load_all_evidence()
        obj._load_reads()
        obj._load_coverage()
        return obj

    # -- small IO helpers --------------------------------------------

    @staticmethod
    def _write_json_atomic(path: Path, data: Mapping[str, Any]) -> None:
        tmp = path.with_name(path.name + ".tmp")
        payload = json.dumps(
            _jsonable(data), ensure_ascii=False, indent=2, allow_nan=False
        )
        try:
            with open(tmp, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
        except OSError:
            try:
                tmp.unlink()
            except OSError:
                pass
            raise _error(
                "evidence_unreadable", "cannot write run metadata"
            ) from None
        self_dir = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(self_dir)
        finally:
            os.close(self_dir)

    @contextmanager
    def _locked(self, name: str) -> Iterator[Any]:
        path = self.evidence_dir / name
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            handle = open(path, "a+b")
        except OSError:
            raise _error(
                "evidence_unreadable", "cannot open the evidence log"
            ) from None
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            yield handle
        finally:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()

    def _append_jsonl_locked(self, handle: Any, record: Mapping[str, Any]) -> None:
        payload = (
            json.dumps(
                _jsonable(record),
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
        try:
            handle.seek(0, os.SEEK_END)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        except OSError:
            raise _error(
                "evidence_unreadable", "cannot append the evidence log"
            ) from None

    @staticmethod
    def _read_jsonl(handle: Any) -> list[dict[str, Any]]:
        try:
            handle.seek(0)
            raw = handle.read()
        except OSError:
            raise _error(
                "evidence_index_corrupt", "evidence log cannot be read"
            ) from None
        if not raw:
            return []
        if not raw.endswith(b"\n"):
            raise _error(
                "evidence_index_corrupt", "evidence log has a partial line"
            )
        records: list[dict[str, Any]] = []
        for line in raw.splitlines():
            if not line.strip():
                continue
            try:
                value = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise _error(
                    "evidence_index_corrupt", "evidence log contains invalid JSON"
                ) from None
            if not isinstance(value, dict):
                raise _error(
                    "evidence_index_corrupt", "evidence log line is not an object"
                )
            records.append(value)
        return records

    @staticmethod
    def _read_jsonl_path(path: Path) -> list[dict[str, Any]]:
        if not path.is_file():
            return []
        try:
            raw = path.read_bytes()
        except OSError:
            raise _error(
                "evidence_index_corrupt", "evidence log cannot be read"
            ) from None
        if not raw:
            return []
        if not raw.endswith(b"\n"):
            raise _error(
                "evidence_index_corrupt", "evidence log has a partial line"
            )
        records: list[dict[str, Any]] = []
        for line in raw.splitlines():
            if not line.strip():
                continue
            try:
                value = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise _error(
                    "evidence_index_corrupt", "evidence log contains invalid JSON"
                ) from None
            if not isinstance(value, dict):
                raise _error(
                    "evidence_index_corrupt", "evidence log line is not an object"
                )
            records.append(value)
        return records

    # -- index --------------------------------------------------------

    def _decode_evidence_record(self, record: Mapping[str, Any]) -> Evidence:
        value = dict(record)
        if value.get("schema_version") != SCHEMA_VERSION:
            raise _error(
                "evidence_index_corrupt", "index record has an unknown schema version"
            )
        value.pop("schema_version", None)
        try:
            parsed = parse_object("Evidence", value)
        except ContractError as exc:
            raise _error(
                "evidence_index_corrupt",
                "index record does not match the Evidence contract",
                errors=[error.to_dict() for error in exc.errors],
            ) from None
        if not isinstance(parsed, Evidence):
            raise _error("evidence_index_corrupt", "index record is not evidence")
        if parsed.run_id != self.run_id:
            raise _error(
                "evidence_index_corrupt",
                "index record belongs to another run",
                evidence_id=parsed.evidence_id,
            )
        return parsed

    def _load_index_jsonl(self) -> list[Evidence]:
        records = self._read_jsonl_path(self.evidence_dir / "index.jsonl")
        return [self._decode_evidence_record(record) for record in records]

    def _load_checkpoint(self) -> list[Evidence]:
        path = self.evidence_dir / "index.json"
        if not path.is_file():
            return []
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            raise _error(
                "evidence_index_corrupt", "index.json cannot be parsed"
            ) from None
        if not isinstance(raw, dict):
            raise _error("evidence_index_corrupt", "index.json is not an object")
        if raw.get("schema_version") != SCHEMA_VERSION:
            raise _error(
                "evidence_index_corrupt", "index.json has an unknown schema version"
            )
        if raw.get("run_id") != self.run_id:
            raise _error(
                "evidence_index_corrupt", "index.json belongs to another run"
            )
        items = raw.get("evidence")
        if not isinstance(items, list):
            raise _error("evidence_index_corrupt", "index.json evidence is not a list")
        result: list[Evidence] = []
        for item in items:
            if not isinstance(item, dict):
                raise _error("evidence_index_corrupt", "index.json item is not an object")
            try:
                parsed = parse_object("Evidence", item)
            except ContractError as exc:
                raise _error(
                    "evidence_index_corrupt",
                    "index.json item does not match the Evidence contract",
                    errors=[error.to_dict() for error in exc.errors],
                ) from None
            if not isinstance(parsed, Evidence) or parsed.run_id != self.run_id:
                raise _error(
                    "evidence_index_corrupt", "index.json item belongs to another run"
                )
            result.append(parsed)
        return result

    def _load_all_evidence(self) -> dict[str, Evidence]:
        merged: dict[str, Evidence] = {}
        for item in self._load_checkpoint():
            merged[item.evidence_id] = item
        for item in self._load_index_jsonl():
            merged[item.evidence_id] = item
        return merged

    def _load_index_locked(self, handle: Any) -> dict[str, Evidence]:
        merged: dict[str, Evidence] = {}
        for item in self._load_checkpoint():
            merged[item.evidence_id] = item
        for record in self._read_jsonl(handle):
            item = self._decode_evidence_record(record)
            merged[item.evidence_id] = item
        return merged

    def _next_sequence(self, evidence: Mapping[str, Evidence]) -> int:
        maximum = 0
        for evidence_id in evidence:
            try:
                run_id, sequence = _parse_evidence_id(evidence_id)
            except EvidenceError:
                raise _error(
                    "evidence_index_corrupt",
                    "index contains a malformed evidence id",
                    evidence_id=evidence_id,
                ) from None
            if run_id != self.run_id:
                raise _error(
                    "evidence_index_corrupt",
                    "index contains an evidence id from another run",
                    evidence_id=evidence_id,
                )
            maximum = max(maximum, sequence)
        return maximum + 1

    def _make_evidence_id(self, sequence: int) -> str:
        return f"ev_{self.run_id}_{sequence:04d}"

    def _evidence_line(self, evidence: Evidence) -> dict[str, Any]:
        record = dump_object(evidence)
        record["schema_version"] = SCHEMA_VERSION
        return record

    def _index_summary(self) -> dict[str, Any]:
        merged = self._load_all_evidence()
        items = [
            dump_object(merged[evidence_id]) for evidence_id in sorted(merged)
        ]
        return {
            "schema_version": SCHEMA_VERSION,
            "run_id": self.run_id,
            "evidence": items,
        }

    def flush_index(self) -> None:
        with self._locked("index.jsonl") as handle:
            _ = self._load_index_locked(handle)
        summary = self._index_summary()
        self._write_json_atomic(self.evidence_dir / "index.json", summary)

    def close(self) -> None:
        if self._closed:
            return
        try:
            self.flush_index()
        finally:
            self._closed = True

    # -- coverage and reads logs -------------------------------------

    def _append_coverage(self, item: CoverageItem) -> CoverageItem:
        record = dump_object(item)
        record["schema_version"] = SCHEMA_VERSION
        with self._locked("coverage.jsonl") as handle:
            self._append_jsonl_locked(handle, record)
        return item

    def _load_coverage(self) -> list[CoverageItem]:
        records = self._read_jsonl_path(self.evidence_dir / "coverage.jsonl")
        items: list[CoverageItem] = []
        for record in records:
            value = dict(record)
            if value.get("schema_version") != SCHEMA_VERSION:
                raise _error(
                    "evidence_index_corrupt",
                    "coverage record has an unknown schema version",
                )
            value.pop("schema_version", None)
            try:
                parsed = parse_object("CoverageItem", value)
            except ContractError as exc:
                raise _error(
                    "evidence_index_corrupt",
                    "coverage record does not match the contract",
                    errors=[error.to_dict() for error in exc.errors],
                ) from None
            if isinstance(parsed, CoverageItem):
                items.append(parsed)
        return items

    def _load_reads(self) -> list[dict[str, Any]]:
        records = self._read_jsonl_path(self.evidence_dir / "reads.jsonl")
        for record in records:
            if record.get("schema_version") != SCHEMA_VERSION:
                raise _error(
                    "evidence_index_corrupt",
                    "read record has an unknown schema version",
                )
        return records

    def _append_read(self, record: Mapping[str, Any]) -> str:
        with self._locked("reads.jsonl") as handle:
            records = self._read_jsonl(handle)
            maximum = 0
            for existing in records:
                read_id = existing.get("read_id")
                if isinstance(read_id, str) and read_id.startswith("rd_"):
                    try:
                        maximum = max(maximum, int(read_id[3:]))
                    except ValueError:
                        continue
            read_id = f"rd_{maximum + 1:04d}"
            payload = dict(record)
            payload["schema_version"] = SCHEMA_VERSION
            payload["read_id"] = read_id
            self._append_jsonl_locked(handle, payload)
        return read_id

    def _append_rejected_read(
        self,
        *,
        evidence_id: Any,
        requested_start: Any,
        requested_end: Any,
        code: str,
        caller: str,
    ) -> None:
        record = {
            "schema_version": SCHEMA_VERSION,
            "read_id": None,
            "evidence_id": evidence_id if isinstance(evidence_id, str) else None,
            "requested_line_start": requested_start
            if isinstance(requested_start, int) and not isinstance(requested_start, bool)
            else None,
            "requested_line_end": requested_end
            if isinstance(requested_end, int) and not isinstance(requested_end, bool)
            else None,
            "code": code,
            "at": _timestamp(_now(self.clock)),
            "caller": caller,
        }
        with self._locked("reads.jsonl") as handle:
            self._append_jsonl_locked(handle, record)

    def covered_ranges(self, evidence_id: str) -> list[tuple[int, int]]:
        ranges: list[tuple[int, int]] = []
        for record in self._load_reads():
            if record.get("read_id") is None:
                continue
            if record.get("evidence_id") != evidence_id:
                continue
            start = record.get("line_start")
            end = record.get("line_end")
            if (
                isinstance(start, int)
                and not isinstance(start, bool)
                and isinstance(end, int)
                and not isinstance(end, bool)
                and start >= 1
                and end >= start
            ):
                ranges.append((start, end))
        return _merge_ranges(ranges)

    def coverage_items(self) -> list[CoverageItem]:
        return self._load_coverage()

    # -- coverage helpers --------------------------------------------

    @staticmethod
    def _coverage_check_id(
        origin: str, logical_path: str, purpose: str | None
    ) -> str:
        key = _sha256(_canonical_json([origin, logical_path, purpose]))
        return "evidence.capture:" + key

    def _coverage_item(
        self,
        *,
        origin: str,
        logical_path: str,
        purpose: str | None,
        status: CoverageStatus,
        reason: str | None,
        inputs: Sequence[str] = (),
        artifact_refs: Sequence[str] = (),
    ) -> CoverageItem:
        return CoverageItem(
            self._coverage_check_id(origin, logical_path, purpose),
            status,
            tuple(inputs),
            reason,
            tuple(artifact_refs),
        )

    def _artifact_ref(self, evidence: Evidence) -> str:
        return f"evidence/snapshots/{evidence.snapshot_id}/content.bin"

    def _success_coverage(
        self,
        *,
        origin: str,
        logical_path: str,
        purpose: str | None,
        evidence: Evidence,
    ) -> CoverageItem:
        if evidence.encoding in _TEXT_ENCODINGS:
            return self._coverage_item(
                origin=origin,
                logical_path=logical_path,
                purpose=purpose,
                status=CoverageStatus.PASSED,
                reason=None,
                inputs=(evidence.evidence_id,),
                artifact_refs=(self._artifact_ref(evidence),),
            )
        reason = "binary" if evidence.encoding == "binary" else "invalid_utf8"
        return self._coverage_item(
            origin=origin,
            logical_path=logical_path,
            purpose=purpose,
            status=CoverageStatus.NOT_RUN,
            reason=reason,
            inputs=(evidence.evidence_id,),
            artifact_refs=(self._artifact_ref(evidence),),
        )

    def _failure_coverage(
        self,
        *,
        origin: str,
        logical_path: str,
        purpose: str | None,
        code: str,
    ) -> CoverageItem:
        return self._coverage_item(
            origin=origin,
            logical_path=logical_path,
            purpose=purpose,
            status=CoverageStatus.NOT_RUN,
            reason=code,
        )

    # -- snapshots ----------------------------------------------------

    def _snapshot_dir(self, snapshot_id: str) -> Path:
        return self.evidence_dir / "snapshots" / snapshot_id

    def _read_snapshot_bytes(self, evidence: Evidence) -> bytes:
        path = self._snapshot_dir(evidence.snapshot_id) / "content.bin"
        if not path.is_file():
            raise _error(
                "evidence_expired",
                "snapshot content is missing",
                evidence_id=evidence.evidence_id,
                reason="snapshot_missing",
            )
        try:
            return path.read_bytes()
        except OSError:
            raise _error(
                "evidence_unreadable",
                "snapshot content cannot be read",
                evidence_id=evidence.evidence_id,
            ) from None

    def _verify_snapshot(self, evidence: Evidence) -> tuple[bytes, dict[str, Any], list[int]]:
        path = self._snapshot_dir(evidence.snapshot_id) / "content.bin"
        if not path.is_file():
            raise _error(
                "evidence_expired",
                "snapshot content is missing",
                evidence_id=evidence.evidence_id,
                reason="snapshot_missing",
            )
        data = self._read_snapshot_bytes(evidence)
        if _sha256(data) != evidence.sha256:
            raise _error(
                "evidence_snapshot_corrupt",
                "snapshot bytes do not match the recorded digest",
                evidence_id=evidence.evidence_id,
                snapshot_id=evidence.snapshot_id,
            )
        meta_path = self._snapshot_dir(evidence.snapshot_id) / "meta.json"
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            raise _error(
                "evidence_snapshot_corrupt",
                "snapshot metadata cannot be read",
                evidence_id=evidence.evidence_id,
                snapshot_id=evidence.snapshot_id,
            ) from None
        if not isinstance(meta, dict):
            raise _error(
                "evidence_snapshot_corrupt",
                "snapshot metadata is not an object",
                evidence_id=evidence.evidence_id,
            )
        starts = _line_starts(data)
        expected = {
            "snapshot_id": evidence.snapshot_id,
            "relative_path": evidence.relative_path,
            "sha256": evidence.sha256,
            "byte_length": len(data),
            "encoding": evidence.encoding,
            "line_count": _count_lines(data),
            "line_starts_sha256": _line_starts_sha256(starts),
        }
        for key, wanted in expected.items():
            if meta.get(key) != wanted:
                raise _error(
                    "evidence_snapshot_corrupt",
                    "snapshot metadata does not match the snapshot",
                    evidence_id=evidence.evidence_id,
                    field=key,
                )
        return data, meta, starts

    def _write_meta(self, path: Path, meta: Mapping[str, Any]) -> None:
        self._write_json_atomic(path, meta)

    def _increment_capture_count(self, meta_path: Path) -> None:
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            raise _error(
                "evidence_snapshot_corrupt", "snapshot metadata cannot be read"
            ) from None
        if not isinstance(meta, dict):
            raise _error("evidence_snapshot_corrupt", "snapshot metadata is not an object")
        meta["capture_count"] = int(meta.get("capture_count", 0)) + 1
        self._write_meta(meta_path, meta)

    def _ensure_snapshot(
        self,
        *,
        snapshot_id: str,
        relative_path: str,
        sha256: str,
        data: bytes,
        encoding: str,
        newline_style: str,
        line_count: int,
        line_starts_sha256: str,
        captured_at: str,
        expires_at: str | None,
    ) -> tuple[Path, bool]:
        target = self._snapshot_dir(snapshot_id)
        content_path = target / "content.bin"
        meta_path = target / "meta.json"
        if target.exists():
            if not content_path.is_file():
                raise _error(
                    "evidence_snapshot_corrupt",
                    "existing snapshot has no content",
                    snapshot_id=snapshot_id,
                )
            try:
                existing = content_path.read_bytes()
            except OSError:
                raise _error(
                    "evidence_snapshot_corrupt",
                    "existing snapshot cannot be read",
                    snapshot_id=snapshot_id,
                ) from None
            if _sha256(existing) != sha256:
                raise _error(
                    "evidence_snapshot_corrupt",
                    "existing snapshot bytes do not match the new capture",
                    snapshot_id=snapshot_id,
                )
            if not meta_path.is_file():
                raise _error(
                    "evidence_snapshot_corrupt",
                    "existing snapshot has no metadata",
                    snapshot_id=snapshot_id,
                )
            self._increment_capture_count(meta_path)
            return target, False

        created = False
        try:
            target.mkdir(parents=True)
            created = True
            tmp = target / "content.bin.tmp"
            with open(tmp, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, content_path)
            meta = {
                "schema_version": SCHEMA_VERSION,
                "snapshot_id": snapshot_id,
                "relative_path": relative_path,
                "sha256": sha256,
                "byte_length": len(data),
                "encoding": encoding,
                "newline_style": newline_style,
                "line_count": line_count,
                "line_starts_sha256": line_starts_sha256,
                "captured_at": captured_at,
                "capture_count": 1,
                "expires_at": expires_at,
            }
            try:
                self._write_meta(meta_path, meta)
            finally:
                try:
                    if tmp.exists():
                        tmp.unlink()
                except OSError:
                    pass
            return target, created
        except Exception:
            if created:
                try:
                    import shutil

                    shutil.rmtree(target, ignore_errors=True)
                except Exception:
                    pass
            raise

    # -- capture ------------------------------------------------------

    def _find_duplicate(
        self,
        evidence: Mapping[str, Evidence],
        *,
        origin: str,
        relative_path: str,
        sha256: str,
        input_id: str | None,
    ) -> Evidence | None:
        if origin == EvidenceOrigin.PROJECT.value:
            for item in evidence.values():
                if (
                    item.origin == EvidenceOrigin.PROJECT
                    and item.relative_path == relative_path
                    and item.sha256 == sha256
                ):
                    return item
            return None
        for item in evidence.values():
            if (
                item.input_id == input_id
                and item.sha256 == sha256
                and item.origin.value == origin
            ):
                return item
        return None

    def _check_input_conflict(
        self,
        evidence: Mapping[str, Evidence],
        *,
        input_id: str,
        sha256: str,
    ) -> None:
        for item in evidence.values():
            if (
                item.origin is EvidenceOrigin.SELECTED_INPUT
                and item.input_id == input_id
            ):
                if item.sha256 != sha256:
                    raise _error(
                        "evidence_input_id_conflict",
                        "input_id was already captured with different content",
                        input_id=input_id,
                        existing_evidence_id=item.evidence_id,
                        existing_sha256=item.sha256,
                    )

    def _capture_bytes(
        self,
        *,
        origin: str,
        relative_path: str,
        input_id: str | None,
        data: bytes,
        sha256: str,
        kind: str,
        provenance: Provenance,
        source_revision: str | None,
        expires_at: str | None,
        captured_at: str,
    ) -> Evidence:
        encoding = _encoding_of(data)
        line_count = _count_lines(data)
        starts = _line_starts(data)
        newline_style = _newline_style(data)
        snapshot_id = _snapshot_id(relative_path, sha256)

        with self._locked("index.jsonl") as handle:
            existing_map = self._load_index_locked(handle)
            if origin == EvidenceOrigin.SELECTED_INPUT.value and input_id is not None:
                self._check_input_conflict(
                    existing_map,
                    input_id=input_id,
                    sha256=sha256,
                )
            duplicate = self._find_duplicate(
                existing_map,
                origin=origin,
                relative_path=relative_path,
                sha256=sha256,
                input_id=input_id,
            )
            if duplicate is not None:
                target = self._snapshot_dir(duplicate.snapshot_id)
                content_path = target / "content.bin"
                if not content_path.is_file():
                    raise _error(
                        "evidence_snapshot_corrupt",
                        "duplicate capture lost its snapshot",
                        evidence_id=duplicate.evidence_id,
                    )
                try:
                    current = content_path.read_bytes()
                except OSError:
                    raise _error(
                        "evidence_snapshot_corrupt",
                        "duplicate capture cannot read its snapshot",
                        evidence_id=duplicate.evidence_id,
                    ) from None
                if _sha256(current) != duplicate.sha256:
                    raise _error(
                        "evidence_snapshot_corrupt",
                        "duplicate capture snapshot is corrupt",
                        evidence_id=duplicate.evidence_id,
                    )
                meta_path = target / "meta.json"
                if not meta_path.is_file():
                    raise _error(
                        "evidence_snapshot_corrupt",
                        "duplicate capture lost its metadata",
                        evidence_id=duplicate.evidence_id,
                    )
                self._increment_capture_count(meta_path)
                return duplicate

            sequence = self._next_sequence(existing_map)
            evidence = Evidence(
                evidence_id=self._make_evidence_id(sequence),
                run_id=self.run_id,
                kind=kind,
                relative_path=relative_path,
                sha256=sha256,
                snapshot_id=snapshot_id,
                captured_at=captured_at,
                source_revision=source_revision,
                byte_length=len(data),
                line_count=line_count,
                encoding=encoding,
                provenance=provenance,
                origin=EvidenceOrigin(origin),
                input_id=input_id,
                expires_at=expires_at,
            )
            _target, created = self._ensure_snapshot(
                snapshot_id=snapshot_id,
                relative_path=relative_path,
                sha256=sha256,
                data=data,
                encoding=encoding,
                newline_style=newline_style,
                line_count=line_count,
                line_starts_sha256=_line_starts_sha256(starts),
                captured_at=captured_at,
                expires_at=expires_at,
            )
            try:
                self._append_jsonl_locked(handle, self._evidence_line(evidence))
            except EvidenceError:
                if created:
                    import shutil

                    shutil.rmtree(self._snapshot_dir(snapshot_id), ignore_errors=True)
                raise
            return evidence

    # -- public capture API ------------------------------------------

    def _capture_file_attempt(
        self,
        relative_path: Any,
        *,
        kind: Any,
        source_revision: Any,
        provenance: Any,
        purpose: Any,
        expires_at: Any,
    ) -> tuple[Evidence | None, CoverageItem, EvidenceError | None]:
        logical_for_coverage = relative_path if isinstance(relative_path, str) else ""
        perhaps_purpose = purpose if isinstance(purpose, str) else None
        try:
            logical = _validate_relative_path(relative_path)
            logical_for_coverage = logical
            clean_kind = _validate_kind(kind)
            clean_source = _validate_source_revision(source_revision)
            clean_provenance = _validate_provenance(provenance, ProvenanceKind.PROJECT_FILE)
            clean_purpose = _validate_purpose(purpose)
            clean_expires = _validate_expires_at(expires_at)
            self._walker.check(relative_path)
            data = self._read_stable(relative_path, self.limits.max_project_file_bytes)
            sha = _sha256(data)
            evidence = self._capture_bytes(
                origin=EvidenceOrigin.PROJECT.value,
                relative_path=logical,
                input_id=None,
                data=data,
                sha256=sha,
                kind=clean_kind,
                provenance=clean_provenance,
                source_revision=clean_source,
                expires_at=clean_expires,
                captured_at=_timestamp(_now(self.clock)),
            )
        except EvidenceError as exc:
            if "relative_path" not in exc.details:
                exc.details["relative_path"] = logical_for_coverage
            coverage = self._append_coverage(
                self._failure_coverage(
                    origin=EvidenceOrigin.PROJECT.value,
                    logical_path=logical_for_coverage,
                    purpose=perhaps_purpose,
                    code=exc.code,
                )
            )
            return None, coverage, exc
        coverage = self._append_coverage(
            self._success_coverage(
                origin=EvidenceOrigin.PROJECT.value,
                logical_path=logical,
                purpose=clean_purpose,
                evidence=evidence,
            )
        )
        return evidence, coverage, None

    def capture_file(
        self,
        relative_path: str,
        *,
        kind: str = "document",
        source_revision: str | None = None,
        provenance: Provenance = Provenance(ProvenanceKind.PROJECT_FILE, None, None),
        purpose: str | None = None,
        expires_at: str | None = None,
    ) -> Evidence:
        evidence, _coverage, error = self._capture_file_attempt(
            relative_path,
            kind=kind,
            source_revision=source_revision,
            provenance=provenance,
            purpose=purpose,
            expires_at=expires_at,
        )
        if error is not None:
            raise error
        assert evidence is not None
        return evidence

    def try_capture_file(self, relative_path: str, **kwargs: Any) -> CaptureOutcome:
        evidence, coverage, error = self._capture_file_attempt(
            relative_path, **kwargs
        )
        return CaptureOutcome(evidence, coverage, error)

    def _read_stable(self, relative_path: str, limit: int) -> bytes:
        path = self.project_root / relative_path
        last: bytes | None = None
        for _attempt in range(self.limits.capture_retry_limit + 1):
            try:
                data, signature = self._read_once(path, limit)
            except _ReadChanged as exc:
                data = exc.data
                signature = None
            if data is not None and self._project_signature_matches(
                relative_path, signature
            ):
                if len(data) > limit:
                    raise _error(
                        "evidence_too_large",
                        "file exceeds the configured limit",
                        byte_length=len(data),
                        limit=limit,
                    )
                return data
            if last is not None and data == last:
                if len(data) > limit:
                    raise _error(
                        "evidence_too_large",
                        "file exceeds the configured limit",
                        byte_length=len(data),
                        limit=limit,
                    )
                return data  # type: ignore[return-value]
            last = data
        raise _error(
            "evidence_changed_during_read",
            "file changed while it was being read",
            relative_path=relative_path,
        )

    def _read_once(
        self, path: Path, limit: int
    ) -> tuple[bytes, tuple[int, int, int, int]]:
        return _read_file_once_project(self.project_root, path, limit)

    def _project_signature_matches(
        self, relative_path: str, signature: tuple[int, int, int, int] | None
    ) -> bool:
        if signature is None:
            return False
        try:
            st = _lstat_project_file(self.project_root, self.project_root / relative_path)
        except EvidenceError as exc:
            if exc.code in {"evidence_symlink_rejected", "evidence_path_escape"}:
                raise
            return False
        return _signature(st) == signature

    def _capture_selected_attempt(
        self,
        path_or_prepared: Any,
        *,
        input_id: Any,
        kind: Any,
        provenance: Any,
        source_revision: Any,
        purpose: Any,
        expires_at: Any,
    ) -> tuple[Evidence | None, CoverageItem, EvidenceError | None]:
        raw_input = input_id if isinstance(input_id, str) else ""
        logical_for_coverage = f"selected-input/{raw_input}" if raw_input else "selected-input"
        purpose_value = purpose if isinstance(purpose, str) else None
        try:
            clean_input_id = _validate_input_id(input_id)
            clean_kind = _validate_kind(kind)
            clean_provenance = _validate_provenance(
                provenance, ProvenanceKind.USER_SUPPLIED
            )
            clean_source = _validate_source_revision(source_revision)
            clean_purpose = _validate_purpose(purpose)
            clean_expires = _validate_expires_at(expires_at)
            if isinstance(path_or_prepared, SelectedInput):
                prepared = path_or_prepared
                if not isinstance(prepared.data, bytes):
                    raise _error(
                        "evidence_ref_invalid",
                        "prepared input data must be bytes",
                        field="data",
                    )
                if _sha256(prepared.data) != prepared.sha256:
                    raise _error(
                        "evidence_digest_mismatch",
                        "prepared input digest does not match its data",
                        input_id=clean_input_id,
                    )
                if len(prepared.data) > self.limits.max_selected_input_bytes:
                    raise _error(
                        "evidence_too_large",
                        "selected input exceeds the configured limit",
                        byte_length=len(prepared.data),
                        limit=self.limits.max_selected_input_bytes,
                    )
            elif isinstance(path_or_prepared, (str, os.PathLike)):
                prepared = prepare_selected_input(
                    os.fspath(path_or_prepared),
                    max_bytes=self.limits.max_selected_input_bytes,
                )
            else:
                raise _error(
                    "evidence_path_invalid",
                    "expected a path or a prepared selected input",
                    field="path_or_prepared",
                )
            if "/" in prepared.basename or "\\" in prepared.basename:
                raise _error(
                    "evidence_path_invalid",
                    "selected input basename must not contain separators",
                    field="logical_name",
                )
            logical = _validate_relative_path(
                f"selected-input/{clean_input_id}/{_nfc(prepared.basename)}",
                field_name="logical_name",
            )
            logical_for_coverage = logical
            sha = prepared.sha256
            evidence = self._capture_bytes(
                origin=EvidenceOrigin.SELECTED_INPUT.value,
                relative_path=logical,
                input_id=clean_input_id,
                data=prepared.data,
                sha256=sha,
                kind=clean_kind,
                provenance=clean_provenance,
                source_revision=clean_source,
                expires_at=clean_expires,
                captured_at=_timestamp(_now(self.clock)),
            )
        except EvidenceError as exc:
            coverage = self._append_coverage(
                self._failure_coverage(
                    origin=EvidenceOrigin.SELECTED_INPUT.value,
                    logical_path=logical_for_coverage,
                    purpose=purpose_value,
                    code=exc.code,
                )
            )
            return None, coverage, exc
        coverage = self._append_coverage(
            self._success_coverage(
                origin=EvidenceOrigin.SELECTED_INPUT.value,
                logical_path=logical,
                purpose=clean_purpose,
                evidence=evidence,
            )
        )
        return evidence, coverage, None

    def capture_selected_input(
        self,
        path_or_prepared: str | SelectedInput,
        *,
        input_id: str,
        kind: str = "event_package",
        provenance: Provenance = Provenance(ProvenanceKind.USER_SUPPLIED, None, None),
        source_revision: str | None = None,
        purpose: str | None = None,
        expires_at: str | None = None,
    ) -> Evidence:
        evidence, _coverage, error = self._capture_selected_attempt(
            path_or_prepared,
            input_id=input_id,
            kind=kind,
            provenance=provenance,
            source_revision=source_revision,
            purpose=purpose,
            expires_at=expires_at,
        )
        if error is not None:
            raise error
        assert evidence is not None
        return evidence

    def _capture_imported_attempt(
        self,
        data: Any,
        *,
        logical_name: Any,
        input_id: Any,
        kind: Any,
        provenance: Any,
        purpose: Any,
        expires_at: Any,
    ) -> tuple[Evidence | None, CoverageItem, EvidenceError | None]:
        raw_input = input_id if isinstance(input_id, str) else ""
        logical_for_coverage = f"imported/{raw_input}" if raw_input else "imported"
        purpose_value = purpose if isinstance(purpose, str) else None
        try:
            clean_input_id = _validate_input_id(input_id)
            clean_name = _validate_relative_path(logical_name, field_name="logical_name")
            if not isinstance(data, bytes):
                raise _error(
                    "evidence_path_invalid",
                    "imported data must be bytes",
                    field="data",
                )
            clean_kind = _validate_kind(kind)
            clean_provenance = _validate_provenance(
                provenance, ProvenanceKind.SYNTHETIC
            )
            clean_purpose = _validate_purpose(purpose)
            clean_expires = _validate_expires_at(expires_at)
            if len(data) > self.limits.max_selected_input_bytes:
                raise _error(
                    "evidence_too_large",
                    "imported bytes exceed the configured limit",
                    byte_length=len(data),
                    limit=self.limits.max_selected_input_bytes,
                )
            logical = f"imported/{clean_input_id}/{clean_name}"
            logical_for_coverage = logical
            evidence = self._capture_bytes(
                origin=EvidenceOrigin.IMPORTED.value,
                relative_path=logical,
                input_id=clean_input_id,
                data=data,
                sha256=_sha256(data),
                kind=clean_kind,
                provenance=clean_provenance,
                source_revision=None,
                expires_at=clean_expires,
                captured_at=_timestamp(_now(self.clock)),
            )
        except EvidenceError as exc:
            coverage = self._append_coverage(
                self._failure_coverage(
                    origin=EvidenceOrigin.IMPORTED.value,
                    logical_path=logical_for_coverage,
                    purpose=purpose_value,
                    code=exc.code,
                )
            )
            return None, coverage, exc
        coverage = self._append_coverage(
            self._success_coverage(
                origin=EvidenceOrigin.IMPORTED.value,
                logical_path=logical,
                purpose=clean_purpose,
                evidence=evidence,
            )
        )
        return evidence, coverage, None

    def capture_imported_bytes(
        self,
        data: bytes,
        *,
        logical_name: str,
        input_id: str,
        kind: str = "event_package",
        provenance: Provenance = Provenance(ProvenanceKind.SYNTHETIC, None, None),
        purpose: str | None = None,
        expires_at: str | None = None,
    ) -> Evidence:
        evidence, _coverage, error = self._capture_imported_attempt(
            data,
            logical_name=logical_name,
            input_id=input_id,
            kind=kind,
            provenance=provenance,
            purpose=purpose,
            expires_at=expires_at,
        )
        if error is not None:
            raise error
        assert evidence is not None
        return evidence

    # -- query --------------------------------------------------------

    def list_evidence(
        self,
        *,
        kind: str | None = None,
        origin: str | None = None,
        prefix: str | None = None,
    ) -> list[Evidence]:
        items = list(self._load_all_evidence().values())
        if kind is not None:
            items = [item for item in items if item.kind == kind]
        if origin is not None:
            items = [item for item in items if item.origin.value == origin]
        if prefix not in (None, "", "."):
            clean_prefix = _validate_relative_path(prefix, field_name="prefix")
            items = [
                item
                for item in items
                if item.relative_path == clean_prefix
                or item.relative_path.startswith(clean_prefix + "/")
            ]
        items.sort(key=lambda item: (item.relative_path, item.evidence_id))
        return items

    def get(self, evidence_id: str) -> Evidence:
        if not isinstance(evidence_id, str) or not evidence_id:
            raise _error(
                "evidence_ref_invalid",
                "evidence_id must be a non-empty string",
                field="evidence_id",
            )
        try:
            run_id, _sequence = _parse_evidence_id(evidence_id)
        except EvidenceError:
            raise _error(
                "evidence_ref_invalid",
                "evidence_id is not a valid evidence identifier",
                field="evidence_id",
            ) from None
        if run_id != self.run_id:
            raise _error(
                "evidence_cross_run",
                "evidence belongs to another run",
                evidence_id=evidence_id,
                run_id=run_id,
            )
        item = self._load_all_evidence().get(evidence_id)
        if item is None:
            raise _error(
                "evidence_not_found",
                "evidence is not recorded in this run",
                evidence_id=evidence_id,
            )
        return item

    # -- probing ------------------------------------------------------

    def probe_path(self, relative_path: str) -> PathProbe:
        try:
            _validate_relative_path(relative_path)
        except EvidenceError:
            return PathProbe("unsafe_path", False, False, None)
        # Validate the NFC form but always access the caller's original string.
        parts = relative_path.split("/")

        try:
            fd = os.open(
                self.project_root,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
            )
        except FileNotFoundError:
            return PathProbe("missing", False, False, None)
        except PermissionError:
            return PathProbe("unreadable", False, False, None)
        except OSError:
            return PathProbe("unreadable", False, False, None)
        try:
            for component in parts[:-1]:
                try:
                    st = os.stat(component, dir_fd=fd, follow_symlinks=False)
                except FileNotFoundError:
                    return PathProbe("missing", False, False, None)
                except PermissionError:
                    return PathProbe("unreadable", False, False, None)
                except OSError:
                    return PathProbe("missing", False, False, None)
                if stat.S_ISLNK(st.st_mode):
                    return PathProbe("symlink", True, False, None)
                if not stat.S_ISDIR(st.st_mode):
                    return PathProbe("not_regular_file", False, False, None)
                try:
                    child = os.open(
                        component,
                        os.O_RDONLY
                        | getattr(os, "O_DIRECTORY", 0)
                        | getattr(os, "O_NOFOLLOW", 0),
                        dir_fd=fd,
                    )
                except FileNotFoundError:
                    return PathProbe("missing", False, False, None)
                except OSError as exc:
                    if exc.errno == errno.ELOOP:
                        return PathProbe("symlink", True, False, None)
                    return PathProbe("unreadable", False, False, None)
                os.close(fd)
                fd = child
            final = parts[-1]
            try:
                st = os.stat(final, dir_fd=fd, follow_symlinks=False)
            except FileNotFoundError:
                return PathProbe("missing", False, False, None)
            except PermissionError:
                return PathProbe("unreadable", False, False, None)
            except OSError:
                return PathProbe("missing", False, False, None)
            if stat.S_ISLNK(st.st_mode):
                return PathProbe("symlink", True, False, None)
            if stat.S_ISDIR(st.st_mode):
                return PathProbe("directory", False, True, None)
            if not stat.S_ISREG(st.st_mode):
                return PathProbe("not_regular_file", False, False, None)
            try:
                real = Path(os.path.realpath(self.project_root / relative_path))
            except OSError:
                return PathProbe("unreadable", False, False, None)
            if not _is_within(real, self.project_root) and real != self.project_root:
                return PathProbe("outside_project", False, False, None)
            return PathProbe("file", False, False, st.st_size)
        finally:
            os.close(fd)

    # -- raw / digest / locations ------------------------------------

    def _source_limit(self, evidence: Evidence) -> int:
        if evidence.origin == EvidenceOrigin.PROJECT:
            return self.limits.max_project_file_bytes
        return self.limits.max_selected_input_bytes

    def _check_expired(self, evidence: Evidence) -> None:
        if evidence.expires_at:
            try:
                expires = _parse_timestamp(evidence.expires_at)
            except ValueError:
                raise _error(
                    "evidence_snapshot_corrupt",
                    "stored expiry is invalid",
                    evidence_id=evidence.evidence_id,
                ) from None
            if _now(self.clock) >= expires:
                raise _error(
                    "evidence_expired",
                    "evidence retention period has elapsed",
                    evidence_id=evidence.evidence_id,
                    reason="retention_elapsed",
                    expires_at=evidence.expires_at,
                )
        if not (self._snapshot_dir(evidence.snapshot_id) / "content.bin").is_file():
            raise _error(
                "evidence_expired",
                "snapshot content is missing",
                evidence_id=evidence.evidence_id,
                reason="snapshot_missing",
            )

    def read_raw(self, evidence_id: str, *, max_bytes: int) -> bytes:
        if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
            raise _error(
                "evidence_limit_invalid",
                "max_bytes must be a positive integer",
                field="max_bytes",
                value=repr(max_bytes),
            )
        evidence = self.get(evidence_id)
        self._check_expired(evidence)
        effective = min(max_bytes, self._source_limit(evidence))
        if evidence.byte_length > effective:
            raise _error(
                "evidence_too_large",
                "evidence exceeds the requested byte limit",
                evidence_id=evidence_id,
                byte_length=evidence.byte_length,
                limit=effective,
            )
        data, _meta, _starts = self._verify_snapshot(evidence)
        return data

    def snapshot_bytes(self, evidence_id: str) -> bytes:
        evidence = self.get(evidence_id)
        if evidence.origin != EvidenceOrigin.PROJECT:
            raise _error(
                "evidence_not_found",
                "snapshot_bytes only accepts captured project files",
                evidence_id=evidence_id,
                reason="not_project_file",
            )
        self._check_expired(evidence)
        if evidence.byte_length > self.limits.max_project_file_bytes:
            raise _error(
                "evidence_too_large",
                "evidence exceeds the project file limit",
                evidence_id=evidence_id,
                byte_length=evidence.byte_length,
                limit=self.limits.max_project_file_bytes,
            )
        data, _meta, _starts = self._verify_snapshot(evidence)
        return data

    def verify_current_digest(
        self, relative_path: str, expected_sha256: str
    ) -> DigestCheck:
        if not isinstance(expected_sha256, str) or not _SHA256_RE.fullmatch(
            expected_sha256
        ):
            return DigestCheck(False, "evidence_ref_invalid", None)
        try:
            _validate_relative_path(relative_path)
            self._walker.check(relative_path)
            data = self._read_stable(relative_path, self.limits.max_project_file_bytes)
        except EvidenceError as exc:
            return DigestCheck(False, exc.code, None)
        actual = _sha256(data)
        if actual != expected_sha256:
            return DigestCheck(False, "evidence_digest_mismatch", actual)
        return DigestCheck(True, None, actual)

    def location(self, evidence_id: str) -> EvidenceLocation | None:
        item = self._load_all_evidence().get(evidence_id)
        if item is None:
            return None
        return self._location_for(item)

    def all_locations(self) -> Mapping[str, EvidenceLocation]:
        return {
            evidence_id: self._location_for(item)
            for evidence_id, item in self._load_all_evidence().items()
        }

    def _location_for(self, evidence: Evidence) -> EvidenceLocation:
        return EvidenceLocation(
            evidence_id=evidence.evidence_id,
            artifact_path=f"evidence/snapshots/{evidence.snapshot_id}/content.bin",
            sha256=evidence.sha256,
            byte_length=evidence.byte_length,
            line_count=evidence.line_count or 0,
            logical_name=evidence.relative_path,
            encoding=evidence.encoding or "utf-8",
            provenance_kind=evidence.provenance.kind.value,
        )

    # -- reference validation ----------------------------------------

    @staticmethod
    def _ref_fields(ref: EvidenceRef | Mapping[str, object]) -> dict[str, Any]:
        if isinstance(ref, Mapping):
            data = dict(ref)
        elif isinstance(ref, EvidenceRef):
            data = {
                "evidence_id": ref.evidence_id,
                "sha256": ref.sha256,
                "line_start": ref.line_start,
                "line_end": ref.line_end,
            }
        else:
            raise _error(
                "evidence_ref_invalid",
                "reference must be an EvidenceRef or a mapping",
                field="$",
            )
        evidence_id = data.get("evidence_id")
        sha256 = data.get("sha256")
        line_start = data.get("line_start")
        line_end = data.get("line_end")
        if not isinstance(evidence_id, str) or not evidence_id:
            raise _error(
                "evidence_ref_invalid",
                "reference evidence_id is missing or invalid",
                field="evidence_id",
            )
        if not isinstance(sha256, str) or not _SHA256_RE.fullmatch(sha256):
            raise _error(
                "evidence_ref_invalid",
                "reference sha256 must be 64 lowercase hexadecimal characters",
                field="sha256",
            )
        if isinstance(line_start, bool) or not isinstance(line_start, int):
            raise _error(
                "evidence_ref_invalid",
                "reference line_start must be an integer",
                field="line_start",
            )
        if line_end is not None and (
            isinstance(line_end, bool) or not isinstance(line_end, int)
        ):
            raise _error(
                "evidence_ref_invalid",
                "reference line_end must be an integer or null",
                field="line_end",
            )
        return {
            "evidence_id": evidence_id,
            "sha256": sha256,
            "line_start": line_start,
            "line_end": line_end,
        }

    def _prepare_ref(
        self, ref: EvidenceRef | Mapping[str, object]
    ) -> tuple[Evidence, bytes, dict[str, Any], list[int], int, int | None]:
        fields = self._ref_fields(ref)
        evidence = self.get(fields["evidence_id"])
        self._check_expired(evidence)
        if fields["sha256"] != evidence.sha256:
            raise _error(
                "evidence_digest_mismatch",
                "reference digest does not match the captured evidence",
                evidence_id=evidence.evidence_id,
                expected_sha256=evidence.sha256,
                actual_sha256=fields["sha256"],
            )
        data, meta, starts = self._verify_snapshot(evidence)
        return (
            evidence,
            data,
            meta,
            starts,
            fields["line_start"],
            fields["line_end"],
        )

    @staticmethod
    def _range_detail(
        evidence: Evidence,
        line_start: int,
        line_end: int | None,
    ) -> dict[str, Any]:
        return {
            "evidence_id": evidence.evidence_id,
            "line_start": line_start,
            "line_end": line_end,
            "line_count": evidence.line_count or 0,
        }

    def _check_range(
        self, evidence: Evidence, line_start: int, line_end: int | None
    ) -> tuple[int, int]:
        count = evidence.line_count or 0
        if count == 0:
            if line_start == 1 and line_end in (None, 1):
                return 1, 1
            raise _error(
                "evidence_range_invalid",
                "line range is outside the empty file",
                **self._range_detail(evidence, line_start, line_end),
            )
        effective_end = count if line_end is None else line_end
        if (
            line_start < 1
            or effective_end < line_start
            or line_start > count
            or effective_end > count
        ):
            raise _error(
                "evidence_range_invalid",
                "line range is outside the captured file",
                **self._range_detail(evidence, line_start, line_end),
            )
        return line_start, effective_end

    def validate_ref(
        self,
        ref: EvidenceRef | Mapping[str, object],
        *,
        require_covered: bool = False,
    ) -> RefValidation:
        evidence_id: Any = None
        requested_start: Any = None
        requested_end: Any = None
        try:
            fields = self._ref_fields(ref)
            evidence_id = fields["evidence_id"]
            requested_start = fields["line_start"]
            requested_end = fields["line_end"]
            evidence, _data, _meta, _starts, start, end = self._prepare_ref(ref)
            start, end = self._check_range(evidence, start, end)
            covered_ranges = self.covered_ranges(evidence.evidence_id)
            covered = _range_is_covered(start, end, covered_ranges)
            detail = {
                "evidence_id": evidence.evidence_id,
                "requested_line_start": requested_start,
                "requested_line_end": requested_end,
                "line_start": start,
                "line_end": end,
                "covered_ranges": covered_ranges,
            }
            if require_covered and not covered:
                detail["code"] = "evidence_not_read"
                return RefValidation(False, "evidence_not_read", detail, False)
            return RefValidation(True, None, detail, covered)
        except EvidenceError as exc:
            detail = dict(exc.details)
            detail.setdefault("evidence_id", evidence_id)
            detail.setdefault("requested_line_start", requested_start)
            detail.setdefault("requested_line_end", requested_end)
            detail.setdefault("code", exc.code)
            if isinstance(evidence_id, str):
                detail.setdefault("covered_ranges", self._safe_covered(evidence_id))
            else:
                detail.setdefault("covered_ranges", [])
            return RefValidation(False, exc.code, detail, False)

    def _safe_covered(self, evidence_id: str) -> list[tuple[int, int]]:
        try:
            return self.covered_ranges(evidence_id)
        except EvidenceError:
            return []

    # -- reads --------------------------------------------------------

    def _validate_read_limits(
        self, max_lines: int | None, max_bytes: int | None
    ) -> tuple[int, int]:
        if max_lines is None:
            lines = self.limits.max_read_lines
        else:
            if isinstance(max_lines, bool) or not isinstance(max_lines, int):
                raise _error(
                    "evidence_limit_invalid",
                    "max_lines must be an integer",
                    field="max_lines",
                )
            if max_lines <= 0:
                raise _error(
                    "evidence_limit_invalid",
                    "max_lines must be positive",
                    field="max_lines",
                    value=max_lines,
                )
            if max_lines > self.limits.max_read_lines:
                raise _error(
                    "evidence_limit_invalid",
                    "max_lines must not exceed the configured limit",
                    field="max_lines",
                    value=max_lines,
                    limit=self.limits.max_read_lines,
                )
            lines = max_lines
        if max_bytes is None:
            byte_limit = self.limits.max_read_bytes
        else:
            if isinstance(max_bytes, bool) or not isinstance(max_bytes, int):
                raise _error(
                    "evidence_limit_invalid",
                    "max_bytes must be an integer",
                    field="max_bytes",
                )
            if max_bytes <= 0:
                raise _error(
                    "evidence_limit_invalid",
                    "max_bytes must be positive",
                    field="max_bytes",
                    value=max_bytes,
                )
            if max_bytes > self.limits.max_read_bytes:
                raise _error(
                    "evidence_limit_invalid",
                    "max_bytes must not exceed the configured limit",
                    field="max_bytes",
                    value=max_bytes,
                    limit=self.limits.max_read_bytes,
                )
            byte_limit = max_bytes
        return lines, byte_limit

    def _line_span(
        self, starts: Sequence[int], data_length: int, line_no: int
    ) -> tuple[int, int]:
        start = starts[line_no - 1]
        end = starts[line_no] if line_no < len(starts) else data_length
        return start, end

    @staticmethod
    def _line_text(raw: bytes, encoding: str, line_no: int) -> str:
        value = raw
        if value.endswith(b"\n"):
            value = value[:-1]
            if value.endswith(b"\r"):
                value = value[:-1]
        text = value.decode("utf-8")
        if encoding == "utf-8-bom" and line_no == 1 and text.startswith("\ufeff"):
            text = text[1:]
        return text

    def read_evidence(
        self,
        ref: EvidenceRef | Mapping[str, object],
        *,
        max_lines: int | None = None,
        max_bytes: int | None = None,
        caller: str = "unknown",
    ) -> EvidenceRead:
        clean_caller = caller if caller in ALLOWED_CALLERS else "unknown"
        evidence_id: Any = None
        requested_start: Any = None
        requested_end: Any = None
        try:
            lines_limit, byte_limit = self._validate_read_limits(max_lines, max_bytes)
            fields = self._ref_fields(ref)
            evidence_id = fields["evidence_id"]
            requested_start = fields["line_start"]
            requested_end = fields["line_end"]
            evidence, data, _meta, starts, start, end = self._prepare_ref(ref)
            start, effective_end = self._check_range(evidence, start, end)
            if evidence.encoding not in _TEXT_ENCODINGS:
                raise _error(
                    "evidence_not_text",
                    "evidence is not valid UTF-8 text",
                    evidence_id=evidence.evidence_id,
                    encoding=evidence.encoding,
                )
            at = _timestamp(_now(self.clock))
            if effective_end == 1 and (evidence.line_count or 0) == 0:
                span = b""
                text = ""
                raw_bytes = 0
                actual_start = 1
                actual_end = 1
                empty = True
                truncated = False
                reason = None
                next_line = None
            else:
                empty = False
                selected: list[int] = []
                used = 0
                reason = None
                for line_no in range(start, effective_end + 1):
                    span_start, span_end = self._line_span(starts, len(data), line_no)
                    span_length = span_end - span_start
                    if span_length > byte_limit and not selected:
                        raise _error(
                            "evidence_line_too_long",
                            "single line exceeds the read byte limit",
                            evidence_id=evidence.evidence_id,
                            line_no=line_no,
                            line_bytes=span_length,
                            limit=byte_limit,
                        )
                    if selected and (
                        len(selected) >= lines_limit
                        or used + span_length > byte_limit
                    ):
                        reason = (
                            "line_limit"
                            if len(selected) >= lines_limit
                            else "byte_limit"
                        )
                        break
                    selected.append(line_no)
                    used += span_length
                if not selected:
                    raise _error(
                        "evidence_range_invalid",
                        "line range selected no lines",
                        evidence_id=evidence.evidence_id,
                    )
                actual_start = selected[0]
                actual_end = selected[-1]
                truncated = actual_end < effective_end
                if not truncated:
                    reason = None
                next_line = actual_end + 1 if truncated else None
                first_start, _ = self._line_span(starts, len(data), actual_start)
                _, last_end = self._line_span(starts, len(data), actual_end)
                span = data[first_start:last_end]
                raw_bytes = len(span)
                text_lines = [
                    self._line_text(
                        data[
                            self._line_span(starts, len(data), line_no)[0] :
                            self._line_span(starts, len(data), line_no)[1]
                        ],
                        evidence.encoding,
                        line_no,
                    )
                    for line_no in selected
                ]
                text = "\n".join(text_lines)
            text_bytes = len(text.encode("utf-8"))
            span_sha256 = _sha256(span)
            record = {
                "schema_version": SCHEMA_VERSION,
                "read_id": None,
                "evidence_id": evidence.evidence_id,
                "relative_path": evidence.relative_path,
                "sha256": evidence.sha256,
                "requested_line_start": requested_start,
                "requested_line_end": requested_end,
                "line_start": actual_start,
                "line_end": actual_end,
                "empty": empty,
                "truncated": truncated,
                "truncation_reason": reason,
                "next_line": next_line,
                "raw_bytes": raw_bytes,
                "text_bytes": text_bytes,
                "span_sha256": span_sha256,
                "at": at,
                "caller": clean_caller,
            }
            read_id = self._append_read(record)
            return EvidenceRead(
                read_id=read_id,
                evidence_id=evidence.evidence_id,
                relative_path=evidence.relative_path,
                sha256=evidence.sha256,
                text=text,
                line_start=actual_start,
                line_end=actual_end,
                requested_line_start=requested_start,
                requested_line_end=requested_end,
                empty=empty,
                truncated=truncated,
                truncation_reason=reason,
                next_line=next_line,
                raw_bytes=raw_bytes,
                text_bytes=text_bytes,
                span_sha256=span_sha256,
                at=at,
                caller=clean_caller,
            )
        except EvidenceError as exc:
            self._append_rejected_read(
                evidence_id=evidence_id if evidence_id is not None else _ref_probe(ref, "evidence_id"),
                requested_start=requested_start
                if requested_start is not None
                else _ref_probe(ref, "line_start"),
                requested_end=requested_end
                if requested_end is not None
                else _ref_probe(ref, "line_end"),
                code=exc.code,
                caller=clean_caller,
            )
            raise

    # -- references ---------------------------------------------------

    def make_ref(self, evidence: Evidence, line_start: int, line_end: int) -> EvidenceRef:
        if (
            isinstance(line_start, bool)
            or not isinstance(line_start, int)
            or isinstance(line_end, bool)
            or not isinstance(line_end, int)
            or line_start < 1
            or line_end < line_start
        ):
            raise _error(
                "evidence_range_invalid",
                "reference line range is invalid",
                evidence_id=evidence.evidence_id,
                line_start=line_start,
                line_end=line_end,
                line_count=evidence.line_count or 0,
            )
        return EvidenceRef(evidence.evidence_id, evidence.sha256, line_start, line_end)


# ---------------------------------------------------------------------------
# Module helpers


def _ref_probe(ref: Any, field_name: str) -> Any:
    if isinstance(ref, Mapping):
        return ref.get(field_name)
    if isinstance(ref, EvidenceRef):
        return getattr(ref, field_name)
    return None


def _merge_ranges(ranges: Sequence[tuple[int, int]]) -> list[tuple[int, int]]:
    ordered = sorted(ranges)
    merged: list[tuple[int, int]] = []
    for start, end in ordered:
        if not merged or start > merged[-1][1] + 1:
            merged.append((start, end))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
    return merged


def _range_is_covered(
    start: int, end: int, ranges: Sequence[tuple[int, int]]
) -> bool:
    return any(range_start <= start and end <= range_end for range_start, range_end in ranges)


def prepare_selected_input(
    path: str | os.PathLike[str], *, max_bytes: int = 10 * 1024 * 1024
) -> SelectedInput:
    """Read one explicitly selected external file without run-side effects."""

    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
        raise _error(
            "evidence_limit_invalid",
            "max_bytes must be a positive integer",
            field="max_bytes",
            value=repr(max_bytes),
        )
    if not isinstance(path, (str, os.PathLike)):
        raise _error(
            "evidence_path_invalid",
            "path must be a string",
            field="path",
        )
    raw = os.fspath(path)
    if raw == "" or "\x00" in raw:
        raise _error("evidence_path_invalid", "path is invalid", field="path")
    target = Path(raw)
    try:
        st = os.lstat(target)
    except FileNotFoundError:
        raise _error("evidence_not_found", "selected input does not exist") from None
    except PermissionError:
        raise _error("evidence_unreadable", "selected input is not readable") from None
    except OSError:
        raise _error("evidence_unreadable", "selected input cannot be inspected") from None
    if stat.S_ISLNK(st.st_mode):
        raise _error(
            "evidence_symlink_rejected", "selected input is a symbolic link"
        )
    if not stat.S_ISREG(st.st_mode):
        raise _error(
            "evidence_not_regular_file", "selected input is not a regular file"
        )
    if st.st_size > max_bytes:
        raise _error(
            "evidence_too_large",
            "selected input exceeds the configured limit",
            byte_length=st.st_size,
            limit=max_bytes,
        )
    data, _signature = _read_stable_once(target, max_bytes, retries=1)
    basename = _nfc(target.name)
    if not basename:
        raise _error("evidence_path_invalid", "selected input has no basename")
    return SelectedInput(data=data, basename=basename, sha256=_sha256(data))


# Re-exported for callers that only need the frozen value objects.
__all__ = [
    "CaptureOutcome",
    "DigestCheck",
    "EvidenceError",
    "EvidenceLimits",
    "EvidenceLocation",
    "EvidenceRead",
    "EvidenceStore",
    "PathProbe",
    "RefValidation",
    "SelectedInput",
    "prepare_selected_input",
]