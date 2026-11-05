"""Shared r3 contracts: scalar rules, objects, validation and serialization.

The public surface is documented in ``docs/features/F01-foundation.md``.  Downstream
modules must construct objects through :func:`parse_object` instead of relying on
dataclass constructors; the constructors stay public so tests and adapters can
compare values, but they do not validate.
"""

from __future__ import annotations

import json
import math
import os
import re
from dataclasses import dataclass, fields, is_dataclass
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterator, Mapping, Sequence

SCHEMA_VERSION = 1

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_PARTIAL = 3

# Scalar aliases are intentionally strings.  The validators below document and
# enforce the actual lexical rules; keeping the aliases simple lets dataclasses
# stay lightweight and JSON-friendly.
Timestamp = str
Sha256 = str
RelativePath = str
ScopePrefix = str
PathString = str
IdString = str
RunId = str
HarnessId = str
OpenToken = str
Scalar = str | int | float | bool | None

_TIMESTAMP_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$"
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_HARNESS_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_OPEN_TOKEN_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_ENCODING_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_DRIVE_RE = re.compile(r"^[A-Za-z]:")


class _FrozenDict(Mapping[str, Any]):
    """Immutable mapping used for open-ended contract payloads."""

    __slots__ = ("_data", "_hash")

    def __init__(self, data: Mapping[str, Any]) -> None:
        object.__setattr__(self, "_data", dict(data))
        object.__setattr__(self, "_hash", None)

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def __repr__(self) -> str:
        return f"_FrozenDict({self._data!r})"

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Mapping):
            return dict(self._data) == dict(other)
        return NotImplemented

    def __hash__(self) -> int:
        cached = self._hash
        if cached is None:
            cached = hash(frozenset(self._data.items()))
            object.__setattr__(self, "_hash", cached)
        return cached


def _freeze(value: Any) -> Any:
    """Return a deep copy of *value* whose mappings and sequences are immutable."""

    if isinstance(value, _FrozenDict):
        return value
    if isinstance(value, Mapping):
        return _FrozenDict({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


class ContractIssue(ValueError):
    """Internal path error carrying the public error code."""

    def __init__(self, code: str, message: str, expected: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.expected = expected


@dataclass(frozen=True, slots=True)
class FieldError:
    path: str
    code: str
    message: str
    expected: str | None = None
    actual_type: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "code": self.code,
            "message": self.message,
            "expected": self.expected,
            "actual_type": self.actual_type,
        }


class ContractError(Exception):
    """A structure or argument error.  All such errors exit with code 2."""

    exit_code = EXIT_USAGE

    def __init__(
        self,
        code: str,
        object_name: str | None = None,
        errors: Sequence[FieldError] = (),
        message: str | None = None,
    ) -> None:
        self.code = code
        self.object_name = object_name
        self.errors = tuple(errors)
        self.message = message or code
        super().__init__(self.message)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "object_name": self.object_name,
            "exit_code": self.exit_code,
            "errors": [error.to_dict() for error in self.errors],
        }


# ---------------------------------------------------------------------------
# Closed enums


class SemanticMode(StrEnum):
    OFF = "off"
    LOCAL = "local"


class RuleInterpretationStatus(StrEnum):
    STRUCTURED_CONFIRMED = "structured_confirmed"
    MODEL_CANDIDATE = "model_candidate"
    UNRESOLVED = "unresolved"


class ConstraintOperator(StrEnum):
    EQUALS = "equals"
    NOT_EQUALS = "not_equals"


class BehaviorDetector(StrEnum):
    PYTHON_ASSIGNMENT_LITERAL = "python_assignment_literal"
    STRUCTURED_CHECK_RESULT = "structured_check_result"


class DecisionStatus(StrEnum):
    DRAFT = "draft"
    ACCEPTED = "accepted"
    WITHDRAWN = "withdrawn"


class ActionKind(StrEnum):
    EDIT = "edit"
    TOOL_RESULT = "tool_result"
    MENTION = "mention"


class ReceiptCoverage(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    UNKNOWN = "unknown"


class CoverageStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    NOT_RUN = "not_run"


class FindingKind(StrEnum):
    STALE_RULE = "stale_rule"
    RULE_CONFLICT = "rule_conflict"
    POLICY_MISMATCH = "policy_mismatch"
    DEAD_REFERENCE = "dead_reference"
    MISSING_LOAD = "missing_load"
    LOADED_VIOLATION = "loaded_violation"
    STALE_CODE_EVIDENCE = "stale_code_evidence"
    RECURRENCE = "recurrence"
    CLEANUP_CANDIDATE = "cleanup_candidate"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class FindingAssessment(StrEnum):
    SUPPORTED = "supported"
    HYPOTHESIS = "hypothesis"
    UNKNOWN = "unknown"


class FindingBasis(StrEnum):
    DETERMINISTIC = "deterministic"
    CONFIRMED_INTERPRETATION = "confirmed_interpretation"
    MODEL_CANDIDATE = "model_candidate"


class SuggestedAction(StrEnum):
    KEEP = "keep"
    UPDATE = "update"
    DELETE = "delete"
    NARROW_SCOPE = "narrow_scope"
    RELOCATE = "relocate"
    FIX_LOADING = "fix_loading"
    ADD_CHECK = "add_check"
    FIX_RETRIEVAL = "fix_retrieval"
    ASK_USER = "ask_user"


class EditOperation(StrEnum):
    REPLACE_SPAN = "replace_span"
    DELETE_SPAN = "delete_span"


class RepairStatus(StrEnum):
    PREVIEW_READY = "preview_ready"
    NEEDS_CONFIRMATION = "needs_confirmation"
    BLOCKED = "blocked"


class RunStatus(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED = "failed"


class ModeCommand(StrEnum):
    AUDIT = "audit"
    DIAGNOSE = "diagnose"


class ProvenanceKind(StrEnum):
    PROJECT_FILE = "project_file"
    SYNTHETIC = "synthetic"
    USER_SUPPLIED = "user_supplied"
    ADAPTER = "adapter"


class ConfirmationKind(StrEnum):
    DECISION_APPROVAL = "decision_approval"
    RULE_INTERPRETATION = "rule_interpretation"
    CHECK_CONFIGURATION = "check_configuration"


class EvidenceOrigin(StrEnum):
    PROJECT = "project"
    SELECTED_INPUT = "selected_input"
    IMPORTED = "imported"


class RunErrorSeverity(StrEnum):
    ERROR = "error"
    WARNING = "warning"


class ClaimType(StrEnum):
    FACT = "fact"
    CAUSAL_EXPLANATION = "causal_explanation"
    GAP = "gap"


class InputKind(StrEnum):
    DECISIONS = "decisions"
    INCIDENTS = "incidents"
    HARNESS_PROFILE = "harness_profile"


# ---------------------------------------------------------------------------
# Dataclasses


@dataclass(frozen=True, slots=True)
class Scope:
    include_prefixes: tuple[str, ...]
    exclude_prefixes: tuple[str, ...] = ()
    harnesses: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class EvidenceRef:
    evidence_id: str
    sha256: str
    line_start: int
    line_end: int


@dataclass(frozen=True, slots=True)
class SourceLocator:
    relative_path: str
    sha256: str
    line_start: int
    line_end: int


@dataclass(frozen=True, slots=True)
class Provenance:
    kind: ProvenanceKind
    adapter: str | None
    version: str | None


@dataclass(frozen=True, slots=True)
class RunError:
    code: str
    path: str | None
    message: str
    severity: RunErrorSeverity = RunErrorSeverity.ERROR


@dataclass(frozen=True, slots=True)
class AuditRequest:
    project_id: str
    repo: str
    task_scope: Scope
    as_of: str
    semantic_mode: SemanticMode
    decision_registry: str | None = None
    harness_profile: str | None = None
    out: str | None = None


@dataclass(frozen=True, slots=True)
class CodePointSpan:
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class RuleSpan:
    rule_id: str
    source_ref: EvidenceRef
    text: str
    scope: Scope
    harness: str
    interpretation_status: RuleInterpretationStatus
    confirmation_ref: str | None = None


@dataclass(frozen=True, slots=True)
class Constraint:
    subject: str
    operator: ConstraintOperator
    value: Scalar
    conditions: Mapping[str, Scalar]
    constraint_id: str | None = None
    span: CodePointSpan | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "conditions", _freeze(self.conditions))


@dataclass(frozen=True, slots=True)
class BehaviorCheck:
    check_id: str
    subject: str
    detector: BehaviorDetector
    scope: Scope
    parameters: Mapping[str, Any]
    confirmation_ref: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "parameters", _freeze(self.parameters))


@dataclass(frozen=True, slots=True)
class ApprovedItem:
    item_id: str
    source_ref: EvidenceRef


@dataclass(frozen=True, slots=True)
class EditApproval:
    decision_id: str
    target_path: str
    operation: EditOperation
    line_start: int
    line_end: int
    old_text_sha256: str
    replacement_sha256: str
    rule_ids: tuple[str, ...]
    valid_until: str | None = None


@dataclass(frozen=True, slots=True)
class Confirmation:
    confirmation_id: str
    kind: ConfirmationKind
    source_ref: EvidenceRef
    approved_items: tuple[ApprovedItem, ...]
    edit_approvals: tuple[EditApproval, ...] = ()


@dataclass(frozen=True, slots=True)
class Decision:
    decision_id: str
    status: DecisionStatus
    scope: Scope
    effective_from: str
    supersedes: tuple[str, ...]
    source_ref: EvidenceRef
    confirmation_ref: str | None
    constraints: tuple[Constraint, ...]
    effective_until: str | None = None


@dataclass(frozen=True, slots=True)
class Action:
    kind: ActionKind
    target_path: str
    evidence_ref: EvidenceRef | None
    before_ref: EvidenceRef | None = None
    after_ref: EvidenceRef | None = None
    payload: Mapping[str, Any] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        payload = _freeze(self.payload) if self.payload is not None else _FrozenDict({})
        object.__setattr__(self, "payload", payload)


@dataclass(frozen=True, slots=True)
class Incident:
    event_id: str
    session_id: str
    task_id: str | None
    attempt_id: str | None
    occurred_at: str | None
    context_epoch: int | None
    harness: str
    snapshot_id: str | None
    action: Action
    source_ref: EvidenceRef | None
    scope: Scope | None = None


@dataclass(frozen=True, slots=True)
class LoadReceiptEntry:
    rule_id: str
    source_ref: EvidenceRef
    text_sha256: str
    channel: str
    scope: Scope | None = None


@dataclass(frozen=True, slots=True)
class LoadReceipt:
    receipt_id: str
    session_id: str
    context_epoch: int
    before_event_id: str
    harness: str
    channels_covered: tuple[str, ...]
    entries: tuple[LoadReceiptEntry, ...]
    coverage: ReceiptCoverage
    provenance: Provenance
    captured_at: str | None = None


@dataclass(frozen=True, slots=True)
class Evidence:
    evidence_id: str
    run_id: str
    kind: str
    relative_path: str
    sha256: str
    snapshot_id: str
    captured_at: str
    source_revision: str | None
    byte_length: int
    line_count: int | None
    encoding: str | None
    provenance: Provenance
    origin: EvidenceOrigin = EvidenceOrigin.PROJECT
    input_id: str | None = None
    expires_at: str | None = None


@dataclass(frozen=True, slots=True)
class CoverageItem:
    check_id: str
    status: CoverageStatus
    inputs: tuple[str, ...]
    reason: str | None
    artifact_refs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Finding:
    finding_id: str
    kind: FindingKind
    claim: str
    scope: Scope
    assessment: FindingAssessment
    basis: FindingBasis
    support_refs: tuple[EvidenceRef, ...]
    counter_refs: tuple[EvidenceRef, ...]
    missing_evidence: tuple[str, ...]
    suggested_action: SuggestedAction
    checks: tuple[CoverageItem, ...]
    related_event_ids: tuple[str, ...] = ()
    related_rule_ids: tuple[str, ...] = ()
    related_finding_ids: tuple[str, ...] = ()
    claim_type: ClaimType = ClaimType.FACT
    synthetic: bool = False


@dataclass(frozen=True, slots=True)
class BaseFile:
    relative_path: str
    sha256: str
    evidence_id: str | None


@dataclass(frozen=True, slots=True)
class Edit:
    edit_id: str
    finding_id: str
    relative_path: str
    operation: EditOperation
    line_start: int
    line_end: int
    old_text_sha256: str
    new_text: str | None


@dataclass(frozen=True, slots=True)
class UnresolvedQuestion:
    question_id: str
    prompt: str
    finding_ids: tuple[str, ...]
    needed: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RepairProposal:
    proposal_id: str
    run_id: str
    finding_ids: tuple[str, ...]
    base_files: tuple[BaseFile, ...]
    edits: tuple[Edit, ...]
    diff_sha256: str | None
    status: RepairStatus
    checks: tuple[CoverageItem, ...]
    unresolved_questions: tuple[UnresolvedQuestion, ...]


@dataclass(frozen=True, slots=True)
class Mode:
    command: ModeCommand
    semantic: SemanticMode


@dataclass(frozen=True, slots=True)
class ModelSummary:
    name: str
    transport: str
    temperature: float
    timeout_s: float
    prompt_version: str | None
    prompt_sha256: str | None


@dataclass(frozen=True, slots=True)
class RunParameters:
    scope: Scope
    model: ModelSummary | None


@dataclass(frozen=True, slots=True)
class InputSummary:
    input_id: str
    kind: InputKind
    logical_name: str
    sha256: str
    byte_length: int
    synthetic: bool


@dataclass(frozen=True, slots=True)
class RunResult:
    run_id: str
    project_id: str
    mode: Mode
    as_of: str
    status: RunStatus
    coverage: tuple[CoverageItem, ...]
    findings: tuple[Finding, ...]
    proposals: tuple[RepairProposal, ...]
    artifacts: tuple[str, ...]
    errors: tuple[RunError, ...]
    parameters: RunParameters
    synthetic: bool = False
    inputs: tuple[InputSummary, ...] = ()
    project_revision: str | None = None


# ---------------------------------------------------------------------------
# Validation context and value primitives


class _StopValidation(Exception):
    pass


class _Ctx:
    def __init__(self, max_errors: int) -> None:
        if isinstance(max_errors, bool) or not isinstance(max_errors, int):
            raise ValueError("max_errors must be an integer")
        if max_errors < 0:
            raise ValueError("max_errors must be non-negative")
        self.max_errors = max_errors
        self.errors: list[FieldError] = []
        self.seen: set[tuple[str, str]] = set()
        self.truncated = False

    def add(
        self,
        path: str,
        code: str,
        message: str,
        expected: str | None = None,
        actual_type: str | None = None,
    ) -> None:
        key = (path, code)
        if key in self.seen:
            return
        if len(self.errors) >= self.max_errors:
            self.truncated = True
            raise _StopValidation
        self.seen.add(key)
        self.errors.append(FieldError(path, code, message, expected, actual_type))


def _type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _join(path: str, key: str) -> str:
    return f"{path}.{key}" if path else str(key)


def _index(path: str, index: int) -> str:
    return f"{path}[{index}]" if path else f"[{index}]"


def _wrong_type(path: str, ctx: _Ctx, expected: str, value: Any) -> None:
    ctx.add(
        path,
        "wrong_type",
        f"expected {expected}; got {_type_name(value)}",
        expected,
        _type_name(value),
    )


def _has_forbidden_control(value: str, allow_newlines: bool) -> bool:
    for index, char in enumerate(value):
        code = ord(char)
        if code == 0x7F:
            return True
        if code < 0x20:
            if allow_newlines and char == "\n":
                continue
            if allow_newlines and char == "\r" and index + 1 < len(value) and value[index + 1] == "\n":
                continue
            return True
    return False


def _valid_utf8(value: str) -> bool:
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _value_string(
    value: Any,
    path: str,
    ctx: _Ctx,
    *,
    allow_empty: bool = False,
    allow_newlines: bool = False,
    max_len: int | None = None,
    empty_code: str = "invalid_format",
    label: str = "string",
) -> str | None:
    if not isinstance(value, str):
        _wrong_type(path, ctx, "string", value)
        return None
    if not _valid_utf8(value):
        ctx.add(path, "invalid_format", "expected valid UTF-8 string; got string", label, "string")
        return None
    if max_len is not None and len(value) > max_len:
        ctx.add(
            path,
            "invalid_format",
            f"expected {label} no longer than {max_len} characters; got string",
            f"{label} (<= {max_len} characters)",
            "string",
        )
        return None
    if not allow_empty and value == "":
        ctx.add(
            path,
            empty_code,
            f"expected non-empty {label}; got empty string",
            f"non-empty {label}",
            "string",
        )
        return None
    if _has_forbidden_control(value, allow_newlines):
        ctx.add(
            path,
            "invalid_format",
            f"expected {label} without control characters; got string",
            f"{label} without control characters",
            "string",
        )
        return None
    return value


def _value_id(value: Any, path: str, ctx: _Ctx) -> str | None:
    text = _value_string(value, path, ctx, label="identifier")
    if text is None:
        return None
    if text.strip() == "":
        ctx.add(path, "invalid_format", "expected non-empty identifier; got string", "identifier", "string")
        return None
    if len(text) > 128:
        ctx.add(path, "invalid_format", "expected identifier no longer than 128 characters; got string", "identifier", "string")
        return None
    if "/" in text:
        ctx.add(path, "invalid_format", "expected identifier without '/'; got string", "identifier", "string")
        return None
    return text


def _value_run_id(value: Any, path: str, ctx: _Ctx) -> str | None:
    text = _value_id(value, path, ctx)
    if text is None:
        return None
    if not _RUN_ID_RE.fullmatch(text):
        ctx.add(
            path,
            "invalid_format",
            "expected run identifier matching [A-Za-z0-9][A-Za-z0-9._-]{0,63}; got string",
            "run identifier",
            "string",
        )
        return None
    return text


def _value_harness(value: Any, path: str, ctx: _Ctx) -> str | None:
    text = _value_string(value, path, ctx, label="harness identifier")
    if text is None:
        return None
    if text == "shared" or _HARNESS_RE.fullmatch(text):
        return text
    ctx.add(
        path,
        "invalid_format",
        "expected 'shared' or a lowercase harness identifier; got string",
        "harness identifier",
        "string",
    )
    return None


def _value_open_token(value: Any, path: str, ctx: _Ctx) -> str | None:
    text = _value_string(value, path, ctx, label="token")
    if text is None:
        return None
    if not _OPEN_TOKEN_RE.fullmatch(text):
        ctx.add(
            path,
            "invalid_format",
            "expected lowercase token matching [a-z][a-z0-9_]{1,63}; got string",
            "token",
            "string",
        )
        return None
    return text


def _value_sha256(value: Any, path: str, ctx: _Ctx) -> str | None:
    text = _value_string(value, path, ctx, label="SHA-256 digest")
    if text is None:
        return None
    if not _SHA256_RE.fullmatch(text):
        ctx.add(
            path,
            "invalid_format",
            "expected 64 lowercase hexadecimal characters; got string",
            "SHA-256 digest",
            "string",
        )
        return None
    return text


def _value_timestamp(value: Any, path: str, ctx: _Ctx) -> str | None:
    text = _value_string(value, path, ctx, label="timestamp")
    if text is None:
        return None
    if not _TIMESTAMP_RE.fullmatch(text):
        ctx.add(
            path,
            "invalid_format",
            "expected timezone-aware ISO 8601 timestamp; got string",
            "timezone-aware ISO 8601 timestamp",
            "string",
        )
        return None
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        ctx.add(
            path,
            "invalid_format",
            "expected a real timezone-aware ISO 8601 timestamp; got string",
            "timezone-aware ISO 8601 timestamp",
            "string",
        )
        return None
    return text


def _value_int(value: Any, path: str, ctx: _Ctx, *, minimum: int | None = None) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        _wrong_type(path, ctx, "integer", value)
        return None
    if minimum is not None and value < minimum:
        ctx.add(
            path,
            "invalid_value",
            f"expected integer >= {minimum}; got integer",
            f"integer >= {minimum}",
            "integer",
        )
        return None
    return value


def _value_number(
    value: Any,
    path: str,
    ctx: _Ctx,
    *,
    positive: bool = False,
) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _wrong_type(path, ctx, "number", value)
        return None
    try:
        number = float(value)
    except (OverflowError, ValueError):
        ctx.add(
            path,
            "invalid_value",
            "expected a representable finite number; got number",
            "finite number",
            "number",
        )
        return None
    if not math.isfinite(number):
        ctx.add(path, "invalid_value", "expected a finite number; got number", "finite number", "number")
        return None
    if positive and number <= 0:
        ctx.add(path, "invalid_value", "expected a positive number; got number", "positive number", "number")
        return None
    return value


def _value_bool(value: Any, path: str, ctx: _Ctx) -> bool | None:
    if not isinstance(value, bool):
        _wrong_type(path, ctx, "boolean", value)
        return None
    return value


def _value_enum(value: Any, path: str, ctx: _Ctx, enum_type: type[StrEnum]) -> Any:
    if not isinstance(value, str):
        _wrong_type(path, ctx, "string", value)
        return None
    try:
        return enum_type(value)
    except ValueError:
        allowed = ", ".join(member.value for member in enum_type)
        ctx.add(
            path,
            "unknown_enum",
            f"expected one of {allowed}; got string",
            f"one of {allowed}",
            "string",
        )
        return None


def _value_scalar(value: Any, path: str, ctx: _Ctx) -> Any:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            ctx.add(path, "invalid_value", "expected a finite number; got number", "scalar", "number")
            return None
        return value
    if isinstance(value, str):
        return _value_string(value, path, ctx, allow_empty=True, label="scalar string")
    _wrong_type(path, ctx, "scalar", value)
    return None


def _value_path_string(value: Any, path: str, ctx: _Ctx) -> str | None:
    if not isinstance(value, str):
        _wrong_type(path, ctx, "string", value)
        return None
    if value == "":
        ctx.add(path, "empty_path", "expected non-empty path; got empty string", "path", "string")
        return None
    if len(value) > 4096:
        ctx.add(path, "invalid_path", "expected path no longer than 4096 characters; got string", "path", "string")
        return None
    if not _valid_utf8(value) or _has_forbidden_control(value, allow_newlines=False):
        ctx.add(path, "invalid_format", "expected path without control characters; got string", "path", "string")
        return None
    return value


def normalize_relative_path(value: str) -> str:
    """Return a canonical project-relative path or raise :class:`ValueError`."""

    if not isinstance(value, str):
        raise ContractIssue("invalid_path", "expected relative path string")
    if value == "":
        raise ContractIssue("empty_path", "path must not be empty")
    if len(value) > 4096:
        raise ContractIssue("invalid_path", "path must be at most 4096 characters")
    if not _valid_utf8(value) or _has_forbidden_control(value, allow_newlines=False):
        raise ContractIssue("invalid_path", "path must not contain control characters")
    if "\\" in value:
        raise ContractIssue("invalid_path", "path must use '/' separators")
    if value.startswith("/"):
        raise ContractIssue("invalid_path", "path must be relative")
    if _DRIVE_RE.match(value):
        raise ContractIssue("invalid_path", "path must not contain a drive letter")
    if value.endswith("/"):
        raise ContractIssue("invalid_path", "relative path must not end with '/'")
    components = value.split("/")
    if any(component == "" for component in components):
        raise ContractIssue("invalid_path", "path must not contain empty components")
    if any(component in {".", ".."} for component in components):
        raise ContractIssue("invalid_path", "path must not contain '.' or '..' components")
    return value


def normalize_scope_prefix(value: str) -> str:
    """Return a canonical scope prefix or raise :class:`ValueError`.

    Canonicalisation happens exactly once: an optional ``./`` prefix and one
    trailing slash are removed, and ``"."`` means the project root.
    """

    if not isinstance(value, str):
        raise ContractIssue("invalid_path", "expected scope prefix string")
    if value == "":
        raise ContractIssue("empty_path", "scope prefix must not be empty")
    if len(value) > 4096:
        raise ContractIssue("invalid_path", "scope prefix must be at most 4096 characters")
    if not _valid_utf8(value) or _has_forbidden_control(value, allow_newlines=False):
        raise ContractIssue("invalid_path", "scope prefix must not contain control characters")
    if "\\" in value:
        raise ContractIssue("invalid_path", "scope prefix must use '/' separators")
    if value.startswith("/"):
        raise ContractIssue("invalid_path", "scope prefix must be relative")
    if _DRIVE_RE.match(value):
        raise ContractIssue("invalid_path", "scope prefix must not contain a drive letter")
    if value.endswith("//"):
        raise ContractIssue("invalid_path", "scope prefix may end with at most one '/'")
    if value.endswith("/"):
        value = value[:-1]
    if value.startswith("./"):
        value = value[2:]
    if value == "":
        value = "."
    if value == ".":
        return "."
    components = value.split("/")
    if any(component == "" for component in components):
        raise ContractIssue("invalid_path", "scope prefix must not contain empty components")
    if any(component in {".", ".."} for component in components):
        raise ContractIssue("invalid_path", "scope prefix must not contain '.' or '..' components")
    return value


def _value_relative_path(value: Any, path: str, ctx: _Ctx) -> str | None:
    if not isinstance(value, str):
        _wrong_type(path, ctx, "string", value)
        return None
    try:
        return normalize_relative_path(value)
    except ContractIssue as exc:
        ctx.add(path, exc.code, exc.message, exc.expected, "string")
        return None


def _value_scope_prefix(value: Any, path: str, ctx: _Ctx) -> str | None:
    if not isinstance(value, str):
        _wrong_type(path, ctx, "string", value)
        return None
    try:
        return normalize_scope_prefix(value)
    except ContractIssue as exc:
        ctx.add(path, exc.code, exc.message, exc.expected, "string")
        return None


# ---------------------------------------------------------------------------
# Field readers


def _key_check(
    data: dict[str, Any],
    path: str,
    ctx: _Ctx,
    required: Mapping[str, str],
    optional: Sequence[str] = (),
) -> None:
    allowed = set(required) | set(optional)
    for key in data:
        if key not in allowed:
            ctx.add(
                _join(path, str(key)),
                "unknown_field",
                "unexpected field",
                "known field",
                _type_name(data[key]),
            )
    for key, expected in required.items():
        if key not in data:
            ctx.add(
                _join(path, key),
                "missing_required",
                "required key is missing",
                expected,
                "missing",
            )


def _read(
    data: dict[str, Any], key: str, path: str
) -> tuple[bool, Any, str]:
    return (key in data, data.get(key), _join(path, key))


def _read_string(
    data: dict[str, Any],
    key: str,
    path: str,
    ctx: _Ctx,
    *,
    nullable: bool = False,
    allow_empty: bool = False,
    allow_newlines: bool = False,
    max_len: int | None = None,
    default: str | None = None,
) -> str | None:
    present, value, field_path = _read(data, key, path)
    if not present:
        return default
    if value is None:
        if nullable:
            return None
        _wrong_type(field_path, ctx, "string", value)
        return None
    return _value_string(
        value,
        field_path,
        ctx,
        allow_empty=allow_empty,
        allow_newlines=allow_newlines,
        max_len=max_len,
    )


def _read_id(
    data: dict[str, Any], key: str, path: str, ctx: _Ctx, *, nullable: bool = False, default: str | None = None
) -> str | None:
    present, value, field_path = _read(data, key, path)
    if not present:
        return default
    if value is None:
        if nullable:
            return None
        _wrong_type(field_path, ctx, "string", value)
        return None
    return _value_id(value, field_path, ctx)


def _read_run_id(data: dict[str, Any], key: str, path: str, ctx: _Ctx) -> str | None:
    present, value, field_path = _read(data, key, path)
    if not present:
        return None
    if value is None:
        _wrong_type(field_path, ctx, "string", value)
        return None
    return _value_run_id(value, field_path, ctx)


def _read_harness(data: dict[str, Any], key: str, path: str, ctx: _Ctx) -> str | None:
    present, value, field_path = _read(data, key, path)
    if not present:
        return None
    if value is None:
        _wrong_type(field_path, ctx, "string", value)
        return None
    return _value_harness(value, field_path, ctx)


def _read_open_token(data: dict[str, Any], key: str, path: str, ctx: _Ctx) -> str | None:
    present, value, field_path = _read(data, key, path)
    if not present:
        return None
    if value is None:
        _wrong_type(field_path, ctx, "string", value)
        return None
    return _value_open_token(value, field_path, ctx)


def _read_sha256(
    data: dict[str, Any], key: str, path: str, ctx: _Ctx, *, nullable: bool = False, default: str | None = None
) -> str | None:
    present, value, field_path = _read(data, key, path)
    if not present:
        return default
    if value is None:
        if nullable:
            return None
        _wrong_type(field_path, ctx, "string", value)
        return None
    return _value_sha256(value, field_path, ctx)


def _read_timestamp(
    data: dict[str, Any], key: str, path: str, ctx: _Ctx, *, nullable: bool = False, default: str | None = None
) -> str | None:
    present, value, field_path = _read(data, key, path)
    if not present:
        return default
    if value is None:
        if nullable:
            return None
        _wrong_type(field_path, ctx, "string", value)
        return None
    return _value_timestamp(value, field_path, ctx)


def _read_enum(
    data: dict[str, Any],
    key: str,
    path: str,
    ctx: _Ctx,
    enum_type: type[StrEnum],
    *,
    default: Any = None,
) -> Any:
    present, value, field_path = _read(data, key, path)
    if not present:
        return default
    if value is None:
        _wrong_type(field_path, ctx, "string", value)
        return default
    return _value_enum(value, field_path, ctx, enum_type)


def _read_int(
    data: dict[str, Any],
    key: str,
    path: str,
    ctx: _Ctx,
    *,
    minimum: int | None = None,
    nullable: bool = False,
    default: int | None = None,
) -> int | None:
    present, value, field_path = _read(data, key, path)
    if not present:
        return default
    if value is None:
        if nullable:
            return None
        _wrong_type(field_path, ctx, "integer", value)
        return None
    return _value_int(value, field_path, ctx, minimum=minimum)


def _read_bool(
    data: dict[str, Any], key: str, path: str, ctx: _Ctx, *, default: bool = False
) -> bool:
    present, value, field_path = _read(data, key, path)
    if not present:
        return default
    if value is None:
        _wrong_type(field_path, ctx, "boolean", value)
        return default
    parsed = _value_bool(value, field_path, ctx)
    return bool(parsed) if parsed is not None else default


def _read_relative_path(
    data: dict[str, Any], key: str, path: str, ctx: _Ctx, *, default: str | None = None
) -> str | None:
    present, value, field_path = _read(data, key, path)
    if not present:
        return default
    return _value_relative_path(value, field_path, ctx)


def _read_path_string(
    data: dict[str, Any], key: str, path: str, ctx: _Ctx, *, nullable: bool = False, default: str | None = None
) -> str | None:
    present, value, field_path = _read(data, key, path)
    if not present:
        return default
    if value is None:
        if nullable:
            return None
        _wrong_type(field_path, ctx, "string", value)
        return None
    return _value_path_string(value, field_path, ctx)


def _read_dict(
    data: dict[str, Any], key: str, path: str, ctx: _Ctx, *, default: Mapping[str, Any] | None = None
) -> Mapping[str, Any] | None:
    present, value, field_path = _read(data, key, path)
    if not present:
        return _freeze(dict(default or {}))
    if value is None:
        _wrong_type(field_path, ctx, "object", value)
        return None
    if not isinstance(value, dict):
        _wrong_type(field_path, ctx, "object", value)
        return None
    try:
        return _freeze(value)
    except RecursionError:
        ctx.add(
            field_path,
            "invalid_value",
            "nested object exceeds the supported depth",
            "shallower object",
            "object",
        )
        return None


def _validate_one(name: str, value: Any, path: str, ctx: _Ctx) -> Any:
    try:
        validator = _VALIDATORS[name]
    except KeyError as exc:
        raise ValueError(f"unknown contract object: {name}") from exc
    start = len(ctx.errors)
    result = validator(value, path, ctx)
    if len(ctx.errors) != start:
        return None
    return result


def _read_object(
    data: dict[str, Any],
    key: str,
    path: str,
    ctx: _Ctx,
    name: str,
    *,
    nullable: bool = False,
    default: Any = None,
) -> Any:
    present, value, field_path = _read(data, key, path)
    if not present:
        return default
    if value is None:
        if nullable:
            return None
        _wrong_type(field_path, ctx, "object", value)
        return None
    return _validate_one(name, value, field_path, ctx)


def _read_object_list(
    data: dict[str, Any],
    key: str,
    path: str,
    ctx: _Ctx,
    name: str,
    *,
    nullable: bool = False,
    default: tuple[Any, ...] = (),
    nonempty: bool = False,
) -> tuple[Any, ...]:
    present, value, field_path = _read(data, key, path)
    if not present:
        return default
    if value is None:
        if nullable:
            return default
        _wrong_type(field_path, ctx, "array", value)
        return default
    if not isinstance(value, list):
        _wrong_type(field_path, ctx, "array", value)
        return default
    result: list[Any] = []
    for index, item in enumerate(value):
        parsed = _validate_one(name, item, _index(field_path, index), ctx)
        if parsed is not None:
            result.append(parsed)
    if nonempty and len(value) == 0:
        ctx.add(field_path, "invalid_value", "expected at least one item", "non-empty array", "array")
    return tuple(result)


def _read_scalar_list(
    data: dict[str, Any],
    key: str,
    path: str,
    ctx: _Ctx,
    *,
    default: tuple[str, ...] = (),
    nonempty: bool = False,
    kind: str = "id",
    nullable: bool = False,
) -> tuple[str, ...]:
    present, value, field_path = _read(data, key, path)
    if not present:
        return default
    if value is None:
        if nullable:
            return default
        _wrong_type(field_path, ctx, "array", value)
        return default
    if not isinstance(value, list):
        _wrong_type(field_path, ctx, "array", value)
        return default
    result: list[str] = []
    for index, item in enumerate(value):
        item_path = _index(field_path, index)
        if kind == "harness":
            parsed = _value_harness(item, item_path, ctx)
        elif kind == "relative_path":
            parsed = _value_relative_path(item, item_path, ctx)
        elif kind == "open_token":
            parsed = _value_open_token(item, item_path, ctx)
        elif kind == "text":
            parsed = _value_string(item, item_path, ctx, label="text")
        else:
            parsed = _value_id(item, item_path, ctx)
        if parsed is not None:
            result.append(parsed)
    if nonempty and len(value) == 0:
        ctx.add(field_path, "invalid_value", "expected at least one item", "non-empty array", "array")
    return tuple(result)


# ---------------------------------------------------------------------------
# Validators


def _v_scope(value: Any, path: str, ctx: _Ctx) -> Scope | None:
    if not isinstance(value, dict):
        _wrong_type(path, ctx, "object", value)
        return None
    _key_check(
        value,
        path,
        ctx,
        required={"include_prefixes": "array"},
        optional=("exclude_prefixes", "harnesses"),
    )
    present, raw_include, include_path = _read(value, "include_prefixes", path)
    include: tuple[str, ...] = ()
    if present:
        if not isinstance(raw_include, list):
            _wrong_type(include_path, ctx, "array", raw_include)
        else:
            parsed: list[str] = []
            for index, item in enumerate(raw_include):
                prefix = _value_scope_prefix(item, _index(include_path, index), ctx)
                if prefix is not None:
                    parsed.append(prefix)
            include = tuple(parsed)
            if not raw_include:
                ctx.add(include_path, "invalid_value", "expected at least one include prefix", "non-empty array", "array")
    exclude: tuple[str, ...] = ()
    present_exclude, raw_exclude, exclude_path = _read(value, "exclude_prefixes", path)
    if present_exclude:
        if not isinstance(raw_exclude, list):
            _wrong_type(exclude_path, ctx, "array", raw_exclude)
        else:
            parsed_exclude: list[str] = []
            for index, item in enumerate(raw_exclude):
                prefix = _value_scope_prefix(item, _index(exclude_path, index), ctx)
                if prefix is not None:
                    parsed_exclude.append(prefix)
            exclude = tuple(parsed_exclude)
    harnesses: tuple[str, ...] = ()
    present_harnesses, raw_harnesses, harness_path = _read(value, "harnesses", path)
    if present_harnesses:
        if not isinstance(raw_harnesses, list):
            _wrong_type(harness_path, ctx, "array", raw_harnesses)
        else:
            parsed_harnesses: list[str] = []
            for index, item in enumerate(raw_harnesses):
                harness = _value_harness(item, _index(harness_path, index), ctx)
                if harness is not None:
                    parsed_harnesses.append(harness)
            harnesses = tuple(parsed_harnesses)
    return Scope(include, exclude, harnesses)


def _v_evidence_ref(value: Any, path: str, ctx: _Ctx) -> EvidenceRef | None:
    if not isinstance(value, dict):
        _wrong_type(path, ctx, "object", value)
        return None
    _key_check(
        value,
        path,
        ctx,
        required={
            "evidence_id": "string",
            "sha256": "string",
            "line_start": "integer",
            "line_end": "integer",
        },
    )
    evidence_id = _read_id(value, "evidence_id", path, ctx)
    sha256 = _read_sha256(value, "sha256", path, ctx)
    line_start = _read_int(value, "line_start", path, ctx, minimum=1)
    line_end = _read_int(value, "line_end", path, ctx, minimum=1)
    if line_start is not None and line_end is not None and line_end < line_start:
        ctx.add(
            _join(path, "line_end"),
            "invalid_value",
            "expected line_end >= line_start",
            "line_end >= line_start",
            "integer",
        )
    return EvidenceRef(evidence_id or "", sha256 or "", line_start or 0, line_end or 0)


def _v_source_locator(value: Any, path: str, ctx: _Ctx) -> SourceLocator | None:
    if not isinstance(value, dict):
        _wrong_type(path, ctx, "object", value)
        return None
    _key_check(
        value,
        path,
        ctx,
        required={
            "relative_path": "string",
            "sha256": "string",
            "line_start": "integer",
            "line_end": "integer",
        },
    )
    relative_path = _read_relative_path(value, "relative_path", path, ctx)
    sha256 = _read_sha256(value, "sha256", path, ctx)
    line_start = _read_int(value, "line_start", path, ctx, minimum=1)
    line_end = _read_int(value, "line_end", path, ctx, minimum=1)
    if line_start is not None and line_end is not None and line_end < line_start:
        ctx.add(
            _join(path, "line_end"),
            "invalid_value",
            "expected line_end >= line_start",
            "line_end >= line_start",
            "integer",
        )
    return SourceLocator(relative_path or "", sha256 or "", line_start or 0, line_end or 0)


def _v_provenance(value: Any, path: str, ctx: _Ctx) -> Provenance | None:
    if not isinstance(value, dict):
        _wrong_type(path, ctx, "object", value)
        return None
    _key_check(
        value,
        path,
        ctx,
        required={"kind": "string", "adapter": "string or null", "version": "string or null"},
    )
    kind = _read_enum(value, "kind", path, ctx, ProvenanceKind)
    adapter_present = "adapter" in value
    version_present = "version" in value
    adapter = _read_string(value, "adapter", path, ctx, nullable=True, allow_empty=True)
    version = _read_string(value, "version", path, ctx, nullable=True, allow_empty=True)
    if kind is not None:
        adapter_path = _join(path, "adapter")
        if kind in (ProvenanceKind.PROJECT_FILE, ProvenanceKind.USER_SUPPLIED):
            if (adapter_present and adapter not in (None, "")) or (
                version_present and version not in (None, "")
            ):
                ctx.add(adapter_path, "invalid_value", "expected adapter and version to be null", "null", _type_name(adapter))
        elif kind is ProvenanceKind.ADAPTER:
            if adapter_present and version_present and (not adapter or not version):
                ctx.add(adapter_path, "invalid_value", "expected non-empty adapter and version", "non-empty strings", _type_name(adapter))
        else:  # synthetic
            if adapter_present and version_present:
                if (adapter is None) != (version is None):
                    ctx.add(adapter_path, "invalid_value", "expected adapter and version both null or both non-empty", "paired values", _type_name(adapter))
                elif adapter == "" or version == "":
                    ctx.add(adapter_path, "invalid_value", "expected non-empty adapter and version", "non-empty strings", _type_name(adapter))
    return Provenance(kind or ProvenanceKind.PROJECT_FILE, adapter, version)


def _v_run_error(value: Any, path: str, ctx: _Ctx) -> RunError | None:
    if not isinstance(value, dict):
        _wrong_type(path, ctx, "object", value)
        return None
    _key_check(
        value,
        path,
        ctx,
        required={"code": "string", "path": "string or null", "message": "string"},
        optional=("severity",),
    )
    code = _read_id(value, "code", path, ctx)
    error_path_value = _read_string(value, "path", path, ctx, nullable=True, allow_empty=True)
    message = _read_string(value, "message", path, ctx)
    severity = _read_enum(value, "severity", path, ctx, RunErrorSeverity, default=RunErrorSeverity.ERROR)
    return RunError(
        code or "",
        error_path_value,
        message or "",
        severity or RunErrorSeverity.ERROR,
    )


def _v_audit_request(value: Any, path: str, ctx: _Ctx) -> AuditRequest | None:
    if not isinstance(value, dict):
        _wrong_type(path, ctx, "object", value)
        return None
    _key_check(
        value,
        path,
        ctx,
        required={
            "project_id": "string",
            "repo": "string",
            "task_scope": "object",
            "as_of": "string",
            "semantic_mode": "string",
        },
        optional=("decision_registry", "harness_profile", "out"),
    )
    project_id = _read_id(value, "project_id", path, ctx)
    repo = _read_path_string(value, "repo", path, ctx)
    task_scope = _read_object(value, "task_scope", path, ctx, "Scope")
    as_of = _read_timestamp(value, "as_of", path, ctx)
    semantic_mode = _read_enum(value, "semantic_mode", path, ctx, SemanticMode)
    decision_registry = _read_path_string(value, "decision_registry", path, ctx, nullable=True)
    harness_profile = _read_path_string(value, "harness_profile", path, ctx, nullable=True)
    out = _read_path_string(value, "out", path, ctx, nullable=True)
    return AuditRequest(
        project_id or "",
        repo or "",
        task_scope or Scope((),),
        as_of or "",
        semantic_mode or SemanticMode.OFF,
        decision_registry,
        harness_profile,
        out,
    )


def _v_code_point_span(value: Any, path: str, ctx: _Ctx) -> CodePointSpan | None:
    if not isinstance(value, dict):
        _wrong_type(path, ctx, "object", value)
        return None
    _key_check(value, path, ctx, required={"start": "integer", "end": "integer"})
    start = _read_int(value, "start", path, ctx, minimum=0)
    end = _read_int(value, "end", path, ctx, minimum=0)
    if start is not None and end is not None and end < start:
        ctx.add(_join(path, "end"), "invalid_value", "expected end >= start", "end >= start", "integer")
    return CodePointSpan(start or 0, end or 0)


def _v_rule_span(value: Any, path: str, ctx: _Ctx) -> RuleSpan | None:
    if not isinstance(value, dict):
        _wrong_type(path, ctx, "object", value)
        return None
    _key_check(
        value,
        path,
        ctx,
        required={
            "rule_id": "string",
            "source_ref": "object",
            "text": "string",
            "scope": "object",
            "harness": "string",
            "interpretation_status": "string",
        },
        optional=("confirmation_ref",),
    )
    rule_id = _read_id(value, "rule_id", path, ctx)
    source_ref = _read_object(value, "source_ref", path, ctx, "EvidenceRef")
    text = _read_string(value, "text", path, ctx, allow_newlines=True)
    scope = _read_object(value, "scope", path, ctx, "Scope")
    harness = _read_harness(value, "harness", path, ctx)
    status = _read_enum(value, "interpretation_status", path, ctx, RuleInterpretationStatus)
    confirmation_ref = _read_id(value, "confirmation_ref", path, ctx, nullable=True)
    return RuleSpan(
        rule_id or "",
        source_ref or EvidenceRef("", "", 0, 0),
        text or "",
        scope or Scope((),),
        harness or "",
        status or RuleInterpretationStatus.UNRESOLVED,
        confirmation_ref,
    )


def _v_constraint(value: Any, path: str, ctx: _Ctx) -> Constraint | None:
    if not isinstance(value, dict):
        _wrong_type(path, ctx, "object", value)
        return None
    _key_check(
        value,
        path,
        ctx,
        required={
            "subject": "string",
            "operator": "string",
            "value": "scalar",
            "conditions": "object",
        },
        optional=("constraint_id", "span"),
    )
    subject = _read_string(value, "subject", path, ctx, max_len=128)
    operator = _read_enum(value, "operator", path, ctx, ConstraintOperator)
    scalar_value: Any = None
    present_value, raw_value, value_path = _read(value, "value", path)
    if present_value:
        scalar_value = _value_scalar(raw_value, value_path, ctx)
    conditions: dict[str, Scalar] = {}
    present_conditions, raw_conditions, conditions_path = _read(value, "conditions", path)
    if present_conditions:
        if not isinstance(raw_conditions, dict):
            _wrong_type(conditions_path, ctx, "object", raw_conditions)
        else:
            for key, item in raw_conditions.items():
                if not isinstance(key, str):
                    _wrong_type(_join(conditions_path, str(key)), ctx, "string", key)
                    continue
                if key == "" or _has_forbidden_control(key, allow_newlines=False):
                    ctx.add(
                        _join(conditions_path, key),
                        "invalid_format",
                        "expected non-empty condition key without control characters",
                        "condition key",
                        "string",
                    )
                    continue
                parsed_item = _value_scalar(item, _join(conditions_path, key), ctx)
                if parsed_item is not None or item is None:
                    conditions[key] = parsed_item
    constraint_id = _read_id(value, "constraint_id", path, ctx, nullable=True)
    span = _read_object(value, "span", path, ctx, "CodePointSpan", nullable=True)
    return Constraint(
        subject or "",
        operator or ConstraintOperator.EQUALS,
        scalar_value,
        _freeze(conditions),
        constraint_id,
        span,
    )


def _v_behavior_check(value: Any, path: str, ctx: _Ctx) -> BehaviorCheck | None:
    if not isinstance(value, dict):
        _wrong_type(path, ctx, "object", value)
        return None
    _key_check(
        value,
        path,
        ctx,
        required={
            "check_id": "string",
            "subject": "string",
            "detector": "string",
            "scope": "object",
            "parameters": "object",
        },
        optional=("confirmation_ref",),
    )
    check_id = _read_id(value, "check_id", path, ctx)
    subject = _read_string(value, "subject", path, ctx, max_len=128)
    detector = _read_enum(value, "detector", path, ctx, BehaviorDetector)
    scope = _read_object(value, "scope", path, ctx, "Scope")
    parameters = _read_dict(value, "parameters", path, ctx)
    confirmation_ref = _read_id(value, "confirmation_ref", path, ctx, nullable=True)
    return BehaviorCheck(
        check_id or "",
        subject or "",
        detector or BehaviorDetector.PYTHON_ASSIGNMENT_LITERAL,
        scope or Scope((),),
        parameters if parameters is not None else _FrozenDict({}),
        confirmation_ref,
    )


def _v_approved_item(value: Any, path: str, ctx: _Ctx) -> ApprovedItem | None:
    if not isinstance(value, dict):
        _wrong_type(path, ctx, "object", value)
        return None
    _key_check(value, path, ctx, required={"item_id": "string", "source_ref": "object"})
    item_id = _read_id(value, "item_id", path, ctx)
    source_ref = _read_object(value, "source_ref", path, ctx, "EvidenceRef")
    return ApprovedItem(item_id or "", source_ref or EvidenceRef("", "", 0, 0))


def _v_edit_approval(value: Any, path: str, ctx: _Ctx) -> EditApproval | None:
    if not isinstance(value, dict):
        _wrong_type(path, ctx, "object", value)
        return None
    _key_check(
        value,
        path,
        ctx,
        required={
            "decision_id": "string",
            "target_path": "string",
            "operation": "string",
            "line_start": "integer",
            "line_end": "integer",
            "old_text_sha256": "string",
            "replacement_sha256": "string",
            "rule_ids": "array",
        },
        optional=("valid_until",),
    )
    decision_id = _read_id(value, "decision_id", path, ctx)
    target_path = _read_relative_path(value, "target_path", path, ctx)
    operation = _read_enum(value, "operation", path, ctx, EditOperation)
    line_start = _read_int(value, "line_start", path, ctx, minimum=1)
    line_end = _read_int(value, "line_end", path, ctx, minimum=1)
    old_text_sha256 = _read_sha256(value, "old_text_sha256", path, ctx)
    replacement_sha256 = _read_sha256(value, "replacement_sha256", path, ctx)
    rule_ids = _read_scalar_list(value, "rule_ids", path, ctx, kind="id", nonempty=True)
    valid_until = _read_timestamp(value, "valid_until", path, ctx, nullable=True)
    if line_start is not None and line_end is not None and line_end < line_start:
        ctx.add(
            _join(path, "line_end"),
            "invalid_value",
            "expected line_end >= line_start",
            "line_end >= line_start",
            "integer",
        )
    return EditApproval(
        decision_id or "",
        target_path or "",
        operation or EditOperation.REPLACE_SPAN,
        line_start or 0,
        line_end or 0,
        old_text_sha256 or "",
        replacement_sha256 or "",
        rule_ids,
        valid_until,
    )


def _v_confirmation(value: Any, path: str, ctx: _Ctx) -> Confirmation | None:
    if not isinstance(value, dict):
        _wrong_type(path, ctx, "object", value)
        return None
    _key_check(
        value,
        path,
        ctx,
        required={
            "confirmation_id": "string",
            "kind": "string",
            "source_ref": "object",
            "approved_items": "array",
        },
        optional=("edit_approvals",),
    )
    confirmation_id = _read_id(value, "confirmation_id", path, ctx)
    kind = _read_enum(value, "kind", path, ctx, ConfirmationKind)
    source_ref = _read_object(value, "source_ref", path, ctx, "EvidenceRef")
    approved_items = _read_object_list(
        value, "approved_items", path, ctx, "ApprovedItem", nonempty=True
    )
    edit_approvals = _read_object_list(value, "edit_approvals", path, ctx, "EditApproval")
    if kind is not None and kind is not ConfirmationKind.DECISION_APPROVAL and edit_approvals:
        ctx.add(
            _join(path, "edit_approvals"),
            "invalid_value",
            "edit approvals are only allowed for decision_approval",
            "empty array",
            "array",
        )
    return Confirmation(
        confirmation_id or "",
        kind or ConfirmationKind.DECISION_APPROVAL,
        source_ref or EvidenceRef("", "", 0, 0),
        approved_items,
        edit_approvals,
    )


def _v_decision(value: Any, path: str, ctx: _Ctx) -> Decision | None:
    if not isinstance(value, dict):
        _wrong_type(path, ctx, "object", value)
        return None
    _key_check(
        value,
        path,
        ctx,
        required={
            "decision_id": "string",
            "status": "string",
            "scope": "object",
            "effective_from": "string",
            "supersedes": "array",
            "source_ref": "object",
            "confirmation_ref": "string or null",
            "constraints": "array",
        },
        optional=("effective_until",),
    )
    decision_id = _read_id(value, "decision_id", path, ctx)
    status = _read_enum(value, "status", path, ctx, DecisionStatus)
    scope = _read_object(value, "scope", path, ctx, "Scope")
    effective_from = _read_timestamp(value, "effective_from", path, ctx)
    supersedes = _read_scalar_list(value, "supersedes", path, ctx, kind="id")
    source_ref = _read_object(value, "source_ref", path, ctx, "EvidenceRef")
    confirmation_ref = _read_id(value, "confirmation_ref", path, ctx, nullable=True)
    constraints = _read_object_list(value, "constraints", path, ctx, "Constraint")
    effective_until = _read_timestamp(value, "effective_until", path, ctx, nullable=True)
    if effective_from is not None and effective_until is not None:
        if _parse_timestamp(effective_until) <= _parse_timestamp(effective_from):
            ctx.add(
                _join(path, "effective_until"),
                "invalid_value",
                "expected effective_until > effective_from",
                "timestamp later than effective_from",
                "string",
            )
    return Decision(
        decision_id or "",
        status or DecisionStatus.DRAFT,
        scope or Scope((),),
        effective_from or "",
        supersedes,
        source_ref or EvidenceRef("", "", 0, 0),
        confirmation_ref,
        constraints,
        effective_until,
    )


def _v_action(value: Any, path: str, ctx: _Ctx) -> Action | None:
    if not isinstance(value, dict):
        _wrong_type(path, ctx, "object", value)
        return None
    _key_check(
        value,
        path,
        ctx,
        required={"kind": "string", "target_path": "string", "evidence_ref": "object or null"},
        optional=("before_ref", "after_ref", "payload"),
    )
    kind = _read_enum(value, "kind", path, ctx, ActionKind)
    target_path = _read_relative_path(value, "target_path", path, ctx)
    evidence_ref = _read_object(value, "evidence_ref", path, ctx, "EvidenceRef", nullable=True)
    before_ref = _read_object(value, "before_ref", path, ctx, "EvidenceRef", nullable=True)
    after_ref = _read_object(value, "after_ref", path, ctx, "EvidenceRef", nullable=True)
    payload = _read_dict(value, "payload", path, ctx)
    return Action(
        kind or ActionKind.EDIT,
        target_path or "",
        evidence_ref,
        before_ref,
        after_ref,
        payload if payload is not None else _FrozenDict({}),
    )


def _v_incident(value: Any, path: str, ctx: _Ctx) -> Incident | None:
    if not isinstance(value, dict):
        _wrong_type(path, ctx, "object", value)
        return None
    _key_check(
        value,
        path,
        ctx,
        required={
            "event_id": "string",
            "session_id": "string",
            "task_id": "string or null",
            "attempt_id": "string or null",
            "occurred_at": "string or null",
            "context_epoch": "integer or null",
            "harness": "string",
            "snapshot_id": "string or null",
            "action": "object",
            "source_ref": "object or null",
        },
        optional=("scope",),
    )
    event_id = _read_id(value, "event_id", path, ctx)
    session_id = _read_id(value, "session_id", path, ctx)
    task_id = _read_id(value, "task_id", path, ctx, nullable=True)
    attempt_id = _read_id(value, "attempt_id", path, ctx, nullable=True)
    occurred_at = _read_timestamp(value, "occurred_at", path, ctx, nullable=True)
    context_epoch = _read_int(value, "context_epoch", path, ctx, minimum=0, nullable=True)
    harness = _read_harness(value, "harness", path, ctx)
    snapshot_id = _read_id(value, "snapshot_id", path, ctx, nullable=True)
    action = _read_object(value, "action", path, ctx, "Action")
    source_ref = _read_object(value, "source_ref", path, ctx, "EvidenceRef", nullable=True)
    scope = _read_object(value, "scope", path, ctx, "Scope", nullable=True)
    return Incident(
        event_id or "",
        session_id or "",
        task_id,
        attempt_id,
        occurred_at,
        context_epoch,
        harness or "",
        snapshot_id,
        action or Action(ActionKind.EDIT, "", None),
        source_ref,
        scope,
    )


def _v_load_receipt_entry(value: Any, path: str, ctx: _Ctx) -> LoadReceiptEntry | None:
    if not isinstance(value, dict):
        _wrong_type(path, ctx, "object", value)
        return None
    _key_check(
        value,
        path,
        ctx,
        required={
            "rule_id": "string",
            "source_ref": "object",
            "text_sha256": "string",
            "channel": "string",
        },
        optional=("scope",),
    )
    rule_id = _read_id(value, "rule_id", path, ctx)
    source_ref = _read_object(value, "source_ref", path, ctx, "EvidenceRef")
    text_sha256 = _read_sha256(value, "text_sha256", path, ctx)
    channel = _read_open_token(value, "channel", path, ctx)
    scope = _read_object(value, "scope", path, ctx, "Scope", nullable=True)
    return LoadReceiptEntry(
        rule_id or "",
        source_ref or EvidenceRef("", "", 0, 0),
        text_sha256 or "",
        channel or "",
        scope,
    )


def _v_load_receipt(value: Any, path: str, ctx: _Ctx) -> LoadReceipt | None:
    if not isinstance(value, dict):
        _wrong_type(path, ctx, "object", value)
        return None
    _key_check(
        value,
        path,
        ctx,
        required={
            "receipt_id": "string",
            "session_id": "string",
            "context_epoch": "integer",
            "before_event_id": "string",
            "harness": "string",
            "channels_covered": "array",
            "entries": "array",
            "coverage": "string",
            "provenance": "object",
        },
        optional=("captured_at",),
    )
    receipt_id = _read_id(value, "receipt_id", path, ctx)
    session_id = _read_id(value, "session_id", path, ctx)
    context_epoch = _read_int(value, "context_epoch", path, ctx, minimum=0)
    before_event_id = _read_id(value, "before_event_id", path, ctx)
    harness = _read_harness(value, "harness", path, ctx)
    channels_covered = _read_scalar_list(value, "channels_covered", path, ctx, kind="open_token")
    entries = _read_object_list(value, "entries", path, ctx, "LoadReceiptEntry")
    coverage = _read_enum(value, "coverage", path, ctx, ReceiptCoverage)
    provenance = _read_object(value, "provenance", path, ctx, "Provenance")
    captured_at = _read_timestamp(value, "captured_at", path, ctx, nullable=True)
    return LoadReceipt(
        receipt_id or "",
        session_id or "",
        context_epoch if context_epoch is not None else 0,
        before_event_id or "",
        harness or "",
        channels_covered,
        entries,
        coverage or ReceiptCoverage.UNKNOWN,
        provenance or Provenance(ProvenanceKind.SYNTHETIC, None, None),
        captured_at,
    )


def _v_evidence(value: Any, path: str, ctx: _Ctx) -> Evidence | None:
    if not isinstance(value, dict):
        _wrong_type(path, ctx, "object", value)
        return None
    _key_check(
        value,
        path,
        ctx,
        required={
            "evidence_id": "string",
            "run_id": "string",
            "kind": "string",
            "relative_path": "string",
            "sha256": "string",
            "snapshot_id": "string",
            "captured_at": "string",
            "source_revision": "string or null",
            "byte_length": "integer",
            "line_count": "integer or null",
            "encoding": "string or null",
            "provenance": "object",
        },
        optional=("origin", "input_id", "expires_at"),
    )
    evidence_id = _read_id(value, "evidence_id", path, ctx)
    run_id = _read_run_id(value, "run_id", path, ctx)
    kind = _read_open_token(value, "kind", path, ctx)
    relative_path = _read_relative_path(value, "relative_path", path, ctx)
    sha256 = _read_sha256(value, "sha256", path, ctx)
    snapshot_id = _read_id(value, "snapshot_id", path, ctx)
    captured_at = _read_timestamp(value, "captured_at", path, ctx)
    source_revision = _read_string(value, "source_revision", path, ctx, nullable=True)
    byte_length = _read_int(value, "byte_length", path, ctx, minimum=0)
    line_count = _read_int(value, "line_count", path, ctx, minimum=0, nullable=True)
    encoding: str | None = None
    present_encoding, raw_encoding, encoding_path = _read(value, "encoding", path)
    if present_encoding:
        if raw_encoding is None:
            encoding = None
        elif not isinstance(raw_encoding, str):
            _wrong_type(encoding_path, ctx, "string or null", raw_encoding)
        elif not _ENCODING_RE.fullmatch(raw_encoding):
            ctx.add(
                encoding_path,
                "invalid_format",
                "expected lowercase encoding name; got string",
                "lowercase encoding name",
                "string",
            )
        else:
            encoding = raw_encoding
    provenance = _read_object(value, "provenance", path, ctx, "Provenance")
    origin = _read_enum(value, "origin", path, ctx, EvidenceOrigin, default=EvidenceOrigin.PROJECT)
    input_id = _read_id(value, "input_id", path, ctx, nullable=True)
    expires_at = _read_timestamp(value, "expires_at", path, ctx, nullable=True)
    if origin in (EvidenceOrigin.SELECTED_INPUT, EvidenceOrigin.IMPORTED) and input_id is None:
        ctx.add(
            _join(path, "input_id"),
            "invalid_value",
            "external evidence requires input_id",
            "string",
            "null",
        )
    return Evidence(
        evidence_id or "",
        run_id or "",
        kind or "",
        relative_path or "",
        sha256 or "",
        snapshot_id or "",
        captured_at or "",
        source_revision,
        byte_length if byte_length is not None else 0,
        line_count,
        encoding,
        provenance or Provenance(ProvenanceKind.PROJECT_FILE, None, None),
        origin or EvidenceOrigin.PROJECT,
        input_id,
        expires_at,
    )


def _v_coverage_item(value: Any, path: str, ctx: _Ctx) -> CoverageItem | None:
    if not isinstance(value, dict):
        _wrong_type(path, ctx, "object", value)
        return None
    _key_check(
        value,
        path,
        ctx,
        required={
            "check_id": "string",
            "status": "string",
            "inputs": "array",
            "reason": "string or null",
            "artifact_refs": "array",
        },
    )
    check_id = _read_id(value, "check_id", path, ctx)
    status = _read_enum(value, "status", path, ctx, CoverageStatus)
    inputs = _read_scalar_list(value, "inputs", path, ctx, kind="id")
    present_reason, raw_reason, reason_path = _read(value, "reason", path)
    reason: str | None = None
    if present_reason:
        if raw_reason is None:
            reason = None
        else:
            reason = _read_string(value, "reason", path, ctx, nullable=True, allow_empty=True)
    if status is not None and status is not CoverageStatus.PASSED:
        if present_reason and (raw_reason is None or raw_reason == ""):
            ctx.add(
                reason_path,
                "invalid_value",
                "failed or not_run coverage requires a reason",
                "non-empty string",
                _type_name(raw_reason),
            )
    artifact_refs = _read_scalar_list(value, "artifact_refs", path, ctx, kind="relative_path")
    return CoverageItem(check_id or "", status or CoverageStatus.NOT_RUN, inputs, reason, artifact_refs)


def _v_finding(value: Any, path: str, ctx: _Ctx) -> Finding | None:
    if not isinstance(value, dict):
        _wrong_type(path, ctx, "object", value)
        return None
    _key_check(
        value,
        path,
        ctx,
        required={
            "finding_id": "string",
            "kind": "string",
            "claim": "string",
            "scope": "object",
            "assessment": "string",
            "basis": "string",
            "support_refs": "array",
            "counter_refs": "array",
            "missing_evidence": "array",
            "suggested_action": "string",
            "checks": "array",
        },
        optional=(
            "related_event_ids",
            "related_rule_ids",
            "related_finding_ids",
            "claim_type",
            "synthetic",
        ),
    )
    finding_id = _read_id(value, "finding_id", path, ctx)
    kind = _read_enum(value, "kind", path, ctx, FindingKind)
    claim = _read_string(value, "claim", path, ctx, allow_newlines=True)
    scope = _read_object(value, "scope", path, ctx, "Scope")
    assessment = _read_enum(value, "assessment", path, ctx, FindingAssessment)
    basis = _read_enum(value, "basis", path, ctx, FindingBasis)
    support_refs = _read_object_list(value, "support_refs", path, ctx, "EvidenceRef")
    counter_refs = _read_object_list(value, "counter_refs", path, ctx, "EvidenceRef")
    missing_evidence = _read_scalar_list(value, "missing_evidence", path, ctx, kind="text")
    suggested_action = _read_enum(value, "suggested_action", path, ctx, SuggestedAction)
    checks = _read_object_list(value, "checks", path, ctx, "CoverageItem")
    related_event_ids = _read_scalar_list(value, "related_event_ids", path, ctx, kind="id")
    related_rule_ids = _read_scalar_list(value, "related_rule_ids", path, ctx, kind="id")
    related_finding_ids = _read_scalar_list(value, "related_finding_ids", path, ctx, kind="id")
    claim_type = _read_enum(value, "claim_type", path, ctx, ClaimType, default=ClaimType.FACT)
    synthetic = _read_bool(value, "synthetic", path, ctx, default=False)
    return Finding(
        finding_id or "",
        kind or FindingKind.INSUFFICIENT_EVIDENCE,
        claim or "",
        scope or Scope((),),
        assessment or FindingAssessment.UNKNOWN,
        basis or FindingBasis.DETERMINISTIC,
        support_refs,
        counter_refs,
        missing_evidence,
        suggested_action or SuggestedAction.ASK_USER,
        checks,
        related_event_ids,
        related_rule_ids,
        related_finding_ids,
        claim_type or ClaimType.FACT,
        synthetic,
    )


def _v_base_file(value: Any, path: str, ctx: _Ctx) -> BaseFile | None:
    if not isinstance(value, dict):
        _wrong_type(path, ctx, "object", value)
        return None
    _key_check(
        value,
        path,
        ctx,
        required={"relative_path": "string", "sha256": "string"},
        optional=("evidence_id",),
    )
    relative_path = _read_relative_path(value, "relative_path", path, ctx)
    sha256 = _read_sha256(value, "sha256", path, ctx)
    evidence_id = _read_id(value, "evidence_id", path, ctx, nullable=True)
    return BaseFile(relative_path or "", sha256 or "", evidence_id)


def _v_edit(value: Any, path: str, ctx: _Ctx) -> Edit | None:
    if not isinstance(value, dict):
        _wrong_type(path, ctx, "object", value)
        return None
    _key_check(
        value,
        path,
        ctx,
        required={
            "edit_id": "string",
            "finding_id": "string",
            "relative_path": "string",
            "operation": "string",
            "line_start": "integer",
            "line_end": "integer",
            "old_text_sha256": "string",
            "new_text": "string or null",
        },
    )
    edit_id = _read_id(value, "edit_id", path, ctx)
    finding_id = _read_id(value, "finding_id", path, ctx)
    relative_path = _read_relative_path(value, "relative_path", path, ctx)
    operation = _read_enum(value, "operation", path, ctx, EditOperation)
    line_start = _read_int(value, "line_start", path, ctx, minimum=1)
    line_end = _read_int(value, "line_end", path, ctx, minimum=1)
    old_text_sha256 = _read_sha256(value, "old_text_sha256", path, ctx)
    new_text = _read_string(
        value, "new_text", path, ctx, nullable=True, allow_empty=True, allow_newlines=True
    )
    if line_start is not None and line_end is not None and line_end < line_start:
        ctx.add(
            _join(path, "line_end"),
            "invalid_value",
            "expected line_end >= line_start",
            "line_end >= line_start",
            "integer",
        )
    new_text_present = "new_text" in value
    if operation is EditOperation.REPLACE_SPAN:
        if new_text_present and (new_text is None or new_text == ""):
            ctx.add(
                _join(path, "new_text"),
                "invalid_value",
                "replace_span requires non-empty new_text",
                "non-empty string",
                "null" if new_text is None else "string",
            )
    elif operation is EditOperation.DELETE_SPAN:
        if new_text_present and new_text is not None:
            ctx.add(
                _join(path, "new_text"),
                "invalid_value",
                "delete_span requires new_text to be null",
                "null",
                "string",
            )
    return Edit(
        edit_id or "",
        finding_id or "",
        relative_path or "",
        operation or EditOperation.REPLACE_SPAN,
        line_start or 0,
        line_end or 0,
        old_text_sha256 or "",
        new_text,
    )


def _v_unresolved_question(value: Any, path: str, ctx: _Ctx) -> UnresolvedQuestion | None:
    if not isinstance(value, dict):
        _wrong_type(path, ctx, "object", value)
        return None
    _key_check(
        value,
        path,
        ctx,
        required={
            "question_id": "string",
            "prompt": "string",
            "finding_ids": "array",
            "needed": "array",
        },
    )
    question_id = _read_id(value, "question_id", path, ctx)
    prompt = _read_string(value, "prompt", path, ctx, allow_newlines=True)
    finding_ids = _read_scalar_list(value, "finding_ids", path, ctx, kind="id")
    needed = _read_scalar_list(value, "needed", path, ctx, kind="text")
    return UnresolvedQuestion(question_id or "", prompt or "", finding_ids, needed)


def _v_repair_proposal(value: Any, path: str, ctx: _Ctx) -> RepairProposal | None:
    if not isinstance(value, dict):
        _wrong_type(path, ctx, "object", value)
        return None
    _key_check(
        value,
        path,
        ctx,
        required={
            "proposal_id": "string",
            "run_id": "string",
            "finding_ids": "array",
            "base_files": "array",
            "edits": "array",
            "diff_sha256": "string or null",
            "status": "string",
            "checks": "array",
            "unresolved_questions": "array",
        },
    )
    proposal_id = _read_id(value, "proposal_id", path, ctx)
    run_id = _read_run_id(value, "run_id", path, ctx)
    finding_ids = _read_scalar_list(value, "finding_ids", path, ctx, kind="id", nonempty=True)
    base_files = _read_object_list(value, "base_files", path, ctx, "BaseFile")
    edits = _read_object_list(value, "edits", path, ctx, "Edit")
    diff_sha256 = _read_sha256(value, "diff_sha256", path, ctx, nullable=True)
    status = _read_enum(value, "status", path, ctx, RepairStatus)
    checks = _read_object_list(value, "checks", path, ctx, "CoverageItem")
    unresolved_questions = _read_object_list(
        value, "unresolved_questions", path, ctx, "UnresolvedQuestion"
    )
    if status is RepairStatus.PREVIEW_READY:
        if "diff_sha256" in value and diff_sha256 is None:
            ctx.add(
                _join(path, "diff_sha256"),
                "invalid_value",
                "preview_ready requires diff_sha256",
                "SHA-256 digest",
                "null",
            )
        if "base_files" in value and not base_files:
            ctx.add(
                _join(path, "base_files"),
                "invalid_value",
                "preview_ready requires at least one base file",
                "non-empty array",
                "array",
            )
        if "edits" in value and not edits:
            ctx.add(
                _join(path, "edits"),
                "invalid_value",
                "preview_ready requires at least one edit",
                "non-empty array",
                "array",
            )
    elif status in (RepairStatus.NEEDS_CONFIRMATION, RepairStatus.BLOCKED):
        if "diff_sha256" in value and diff_sha256 is not None:
            ctx.add(
                _join(path, "diff_sha256"),
                "invalid_value",
                "non-preview proposal must have diff_sha256=null",
                "null",
                "string",
            )
    return RepairProposal(
        proposal_id or "",
        run_id or "",
        finding_ids,
        base_files,
        edits,
        diff_sha256,
        status or RepairStatus.BLOCKED,
        checks,
        unresolved_questions,
    )


def _v_mode(value: Any, path: str, ctx: _Ctx) -> Mode | None:
    if not isinstance(value, dict):
        _wrong_type(path, ctx, "object", value)
        return None
    _key_check(value, path, ctx, required={"command": "string", "semantic": "string"})
    command = _read_enum(value, "command", path, ctx, ModeCommand)
    semantic = _read_enum(value, "semantic", path, ctx, SemanticMode)
    return Mode(command or ModeCommand.AUDIT, semantic or SemanticMode.OFF)


def _v_model_summary(value: Any, path: str, ctx: _Ctx) -> ModelSummary | None:
    if not isinstance(value, dict):
        _wrong_type(path, ctx, "object", value)
        return None
    _key_check(
        value,
        path,
        ctx,
        required={
            "name": "string",
            "transport": "string",
            "temperature": "number",
            "timeout_s": "number",
            "prompt_version": "string or null",
            "prompt_sha256": "string or null",
        },
    )
    name = _read_string(value, "name", path, ctx)
    transport = _read_string(value, "transport", path, ctx)
    temperature: float | None = None
    present_temperature, raw_temperature, temperature_path = _read(value, "temperature", path)
    if present_temperature:
        temperature = _value_number(raw_temperature, temperature_path, ctx)
    timeout_s: float | None = None
    present_timeout, raw_timeout, timeout_path = _read(value, "timeout_s", path)
    if present_timeout:
        timeout_s = _value_number(raw_timeout, timeout_path, ctx, positive=True)
    prompt_version = _read_string(value, "prompt_version", path, ctx, nullable=True)
    prompt_sha256 = _read_sha256(value, "prompt_sha256", path, ctx, nullable=True)
    return ModelSummary(
        name or "",
        transport or "",
        temperature if temperature is not None else 0.0,
        timeout_s if timeout_s is not None else 0.0,
        prompt_version,
        prompt_sha256,
    )


def _v_run_parameters(value: Any, path: str, ctx: _Ctx) -> RunParameters | None:
    if not isinstance(value, dict):
        _wrong_type(path, ctx, "object", value)
        return None
    _key_check(value, path, ctx, required={"scope": "object", "model": "object or null"})
    scope = _read_object(value, "scope", path, ctx, "Scope")
    model = _read_object(value, "model", path, ctx, "ModelSummary", nullable=True)
    return RunParameters(scope or Scope((),), model)


def _v_input_summary(value: Any, path: str, ctx: _Ctx) -> InputSummary | None:
    if not isinstance(value, dict):
        _wrong_type(path, ctx, "object", value)
        return None
    _key_check(
        value,
        path,
        ctx,
        required={
            "input_id": "string",
            "kind": "string",
            "logical_name": "string",
            "sha256": "string",
            "byte_length": "integer",
            "synthetic": "boolean",
        },
    )
    input_id = _read_id(value, "input_id", path, ctx)
    kind = _read_enum(value, "kind", path, ctx, InputKind)
    logical_name = _read_path_string(value, "logical_name", path, ctx)
    sha256 = _read_sha256(value, "sha256", path, ctx)
    byte_length = _read_int(value, "byte_length", path, ctx, minimum=0)
    synthetic = _read_bool(value, "synthetic", path, ctx)
    return InputSummary(
        input_id or "",
        kind or InputKind.DECISIONS,
        logical_name or "",
        sha256 or "",
        byte_length if byte_length is not None else 0,
        synthetic,
    )


def _v_run_result(value: Any, path: str, ctx: _Ctx) -> RunResult | None:
    if not isinstance(value, dict):
        _wrong_type(path, ctx, "object", value)
        return None
    _key_check(
        value,
        path,
        ctx,
        required={
            "run_id": "string",
            "project_id": "string",
            "mode": "object",
            "as_of": "string",
            "status": "string",
            "coverage": "array",
            "findings": "array",
            "proposals": "array",
            "artifacts": "array",
            "errors": "array",
            "parameters": "object",
        },
        optional=("synthetic", "inputs", "project_revision"),
    )
    run_id = _read_run_id(value, "run_id", path, ctx)
    project_id = _read_id(value, "project_id", path, ctx)
    mode = _read_object(value, "mode", path, ctx, "Mode")
    as_of = _read_timestamp(value, "as_of", path, ctx)
    status = _read_enum(value, "status", path, ctx, RunStatus)
    coverage = _read_object_list(value, "coverage", path, ctx, "CoverageItem")
    findings = _read_object_list(value, "findings", path, ctx, "Finding")
    proposals = _read_object_list(value, "proposals", path, ctx, "RepairProposal")
    artifacts = _read_scalar_list(value, "artifacts", path, ctx, kind="relative_path")
    errors = _read_object_list(value, "errors", path, ctx, "RunError")
    parameters = _read_object(value, "parameters", path, ctx, "RunParameters")
    synthetic = _read_bool(value, "synthetic", path, ctx, default=False)
    inputs = _read_object_list(value, "inputs", path, ctx, "InputSummary")
    project_revision = _read_string(value, "project_revision", path, ctx, nullable=True)
    return RunResult(
        run_id or "",
        project_id or "",
        mode or Mode(ModeCommand.AUDIT, SemanticMode.OFF),
        as_of or "",
        status or RunStatus.FAILED,
        coverage,
        findings,
        proposals,
        artifacts,
        errors,
        parameters or RunParameters(Scope((),), None),
        synthetic,
        inputs,
        project_revision,
    )


def _v_unknown(value: Any, path: str, ctx: _Ctx) -> Any:
    _wrong_type(path, ctx, "object", value)
    return None


_VALIDATORS = {
    "Scope": _v_scope,
    "EvidenceRef": _v_evidence_ref,
    "SourceLocator": _v_source_locator,
    "Provenance": _v_provenance,
    "RunError": _v_run_error,
    "AuditRequest": _v_audit_request,
    "CodePointSpan": _v_code_point_span,
    "RuleSpan": _v_rule_span,
    "Constraint": _v_constraint,
    "BehaviorCheck": _v_behavior_check,
    "ApprovedItem": _v_approved_item,
    "EditApproval": _v_edit_approval,
    "Confirmation": _v_confirmation,
    "Decision": _v_decision,
    "Action": _v_action,
    "Incident": _v_incident,
    "LoadReceiptEntry": _v_load_receipt_entry,
    "LoadReceipt": _v_load_receipt,
    "Evidence": _v_evidence,
    "CoverageItem": _v_coverage_item,
    "Finding": _v_finding,
    "BaseFile": _v_base_file,
    "Edit": _v_edit,
    "UnresolvedQuestion": _v_unresolved_question,
    "RepairProposal": _v_repair_proposal,
    "Mode": _v_mode,
    "ModelSummary": _v_model_summary,
    "RunParameters": _v_run_parameters,
    "InputSummary": _v_input_summary,
    "RunResult": _v_run_result,
}

CONTRACT_OBJECTS = MappingProxyType(
    {
        "Scope": Scope,
        "EvidenceRef": EvidenceRef,
        "SourceLocator": SourceLocator,
        "Provenance": Provenance,
        "RunError": RunError,
        "AuditRequest": AuditRequest,
        "CodePointSpan": CodePointSpan,
        "RuleSpan": RuleSpan,
        "Constraint": Constraint,
        "BehaviorCheck": BehaviorCheck,
        "ApprovedItem": ApprovedItem,
        "EditApproval": EditApproval,
        "Confirmation": Confirmation,
        "Decision": Decision,
        "Action": Action,
        "Incident": Incident,
        "LoadReceiptEntry": LoadReceiptEntry,
        "LoadReceipt": LoadReceipt,
        "Evidence": Evidence,
        "CoverageItem": CoverageItem,
        "Finding": Finding,
        "BaseFile": BaseFile,
        "Edit": Edit,
        "UnresolvedQuestion": UnresolvedQuestion,
        "RepairProposal": RepairProposal,
        "Mode": Mode,
        "ModelSummary": ModelSummary,
        "RunParameters": RunParameters,
        "InputSummary": InputSummary,
        "RunResult": RunResult,
    }
)


def _finish(ctx: _Ctx) -> list[FieldError]:
    if ctx.truncated and ("$", "too_many_errors") not in ctx.seen:
        ctx.errors.append(
            FieldError(
                "$",
                "too_many_errors",
                f"validation stopped after {ctx.max_errors} errors",
                f"at most {ctx.max_errors} errors",
                None,
            )
        )
    return ctx.errors


def validate_object(
    name: str, data: Any, *, max_errors: int = 20
) -> list[FieldError]:
    if name not in _VALIDATORS:
        raise ValueError(f"unknown contract object: {name}")
    ctx = _Ctx(max_errors)
    if not isinstance(data, dict):
        try:
            _wrong_type("$", ctx, "object", data)
        except _StopValidation:
            pass
        return _finish(ctx)
    try:
        _VALIDATORS[name](data, "", ctx)
    except _StopValidation:
        pass
    return _finish(ctx)


def parse_object(name: str, data: Any, *, max_errors: int = 20) -> Any:
    if name not in _VALIDATORS:
        raise ValueError(f"unknown contract object: {name}")
    ctx = _Ctx(max_errors)
    if not isinstance(data, dict):
        try:
            _wrong_type("$", ctx, "object", data)
        except _StopValidation:
            pass
        raise ContractError("contract_invalid", name, _finish(ctx))
    result: Any = None
    try:
        result = _VALIDATORS[name](data, "", ctx)
    except _StopValidation:
        pass
    errors = _finish(ctx)
    if errors:
        raise ContractError("contract_invalid", name, errors)
    if result is None:
        raise ContractError(
            "contract_invalid",
            name,
            (FieldError("$", "wrong_type", "expected object; got null", "object", "null"),),
        )
    return result


def parse_list(
    name: str, items: Any, *, path_prefix: str = ""
) -> tuple[list[Any], list[FieldError]]:
    if name not in _VALIDATORS:
        raise ValueError(f"unknown contract object: {name}")
    if not isinstance(items, list):
        path = path_prefix or "$"
        return [], [
            FieldError(
                path,
                "wrong_type",
                f"expected array; got {_type_name(items)}",
                "array",
                _type_name(items),
            )
        ]
    ctx = _Ctx(20)
    result: list[Any] = []
    try:
        for index, item in enumerate(items):
            parsed = _validate_one(name, item, _index(path_prefix, index), ctx)
            if parsed is not None:
                result.append(parsed)
    except _StopValidation:
        pass
    return result, _finish(ctx)


# ---------------------------------------------------------------------------
# Serialization


def _dump(value: Any) -> Any:
    if is_dataclass(value):
        return {field.name: _dump(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _dump(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_dump(item) for item in value]
    return value


def dump_object(obj: Any) -> Any:
    return _dump(obj)


def dumps(obj: Any) -> str:
    text = json.dumps(
        _dump(obj),
        ensure_ascii=False,
        indent=2,
        allow_nan=False,
    )
    return text + "\n"


# ---------------------------------------------------------------------------
# JSON loading


class _DuplicateKey(Exception):
    pass


class _InvalidConstant(Exception):
    pass


def _json_error(code: str, message: str) -> ContractError:
    return ContractError(
        code,
        None,
        (FieldError("$", code, message, None, None),),
    )


def _reject_non_finite(value: Any) -> None:
    if isinstance(value, float):
        if not math.isfinite(value):
            raise _InvalidConstant
        return
    if isinstance(value, list):
        for item in value:
            _reject_non_finite(item)
        return
    if isinstance(value, dict):
        for item in value.values():
            _reject_non_finite(item)


def load_json(path: str | os.PathLike[str], *, max_bytes: int = 32 * 1024 * 1024) -> Any:
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 0:
        raise ValueError("max_bytes must be a non-negative integer")
    raw: bytes
    try:
        with open(path, "rb") as handle:
            raw = handle.read(max_bytes + 1)
    except FileNotFoundError as exc:
        raise _json_error("input_not_found", "input file does not exist") from exc
    except IsADirectoryError as exc:
        raise _json_error("input_not_found", "input path is not a regular file") from exc
    except PermissionError as exc:
        raise _json_error("input_not_found", "input file is not readable") from exc
    except OSError as exc:
        raise _json_error("input_not_found", "input file could not be read") from exc
    if len(raw) > max_bytes:
        raise _json_error("input_too_large", "input exceeds the maximum allowed size")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise _json_error("invalid_encoding", "input is not valid UTF-8") from exc

    def pairs_hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise _DuplicateKey
            result[key] = value
        return result

    def constant_hook(value: str) -> Any:
        raise _InvalidConstant

    try:
        parsed = json.loads(text, object_pairs_hook=pairs_hook, parse_constant=constant_hook)
        _reject_non_finite(parsed)
        return parsed
    except _DuplicateKey as exc:
        raise _json_error("duplicate_key", "JSON object contains a duplicate key") from exc
    except _InvalidConstant as exc:
        raise _json_error("invalid_json", "NaN and Infinity are not valid JSON") from exc
    except RecursionError as exc:
        raise _json_error("invalid_json", "JSON nesting exceeds the supported depth") from exc
    except json.JSONDecodeError as exc:
        raise _json_error("invalid_json", "invalid JSON syntax") from exc
    except UnicodeError as exc:
        raise _json_error("invalid_json", "invalid JSON text encoding") from exc
    except ValueError as exc:
        raise _json_error("invalid_json", "JSON number is outside the supported range") from exc


def require_schema_version(data: Any) -> Any:
    if not isinstance(data, dict):
        raise ContractError(
            "contract_invalid",
            None,
            (
                FieldError(
                    "$",
                    "wrong_type",
                    f"expected object; got {_type_name(data)}",
                    "object",
                    _type_name(data),
                ),
            ),
        )
    if "schema_version" not in data:
        raise ContractError(
            "contract_invalid",
            None,
            (
                FieldError(
                    "schema_version",
                    "missing_required",
                    "required key is missing",
                    "integer 1",
                    "missing",
                ),
            ),
        )
    version = data["schema_version"]
    if isinstance(version, bool) or not isinstance(version, int):
        raise ContractError(
            "contract_invalid",
            None,
            (
                FieldError(
                    "schema_version",
                    "wrong_type",
                    f"expected integer; got {_type_name(version)}",
                    "integer 1",
                    _type_name(version),
                ),
            ),
        )
    if version != SCHEMA_VERSION:
        raise ContractError(
            "contract_invalid",
            None,
            (
                FieldError(
                    "schema_version",
                    "unsupported_schema_version",
                    "expected schema_version 1",
                    "integer 1",
                    "integer",
                ),
            ),
        )
    return data


def load_document(
    path: str | os.PathLike[str], *, max_bytes: int = 32 * 1024 * 1024
) -> dict[str, Any]:
    data = load_json(path, max_bytes=max_bytes)
    require_schema_version(data)
    return data


# ---------------------------------------------------------------------------
# CLI request construction


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _format_utc(value: datetime) -> str:
    value = value.astimezone(timezone.utc)
    base = value.strftime("%Y-%m-%dT%H:%M:%S")
    if value.microsecond:
        base += "." + f"{value.microsecond:06d}".rstrip("0")
    return base + "Z"


def resolve_cli_path(value: str | os.PathLike[str], cwd: str | os.PathLike[str]) -> Path:
    if value is None:
        raise ValueError("path must not be null")
    text = os.fspath(value)
    if text == "":
        raise ValueError("path must not be empty")
    path = Path(text)
    if not path.is_absolute():
        path = Path(cwd) / path
    try:
        return path.resolve(strict=False)
    except (OSError, ValueError) as exc:
        raise ValueError("path could not be resolved") from exc


def _ns_get(namespace: Any, key: str, default: Any = None) -> Any:
    if isinstance(namespace, Mapping):
        return namespace.get(key, default)
    return getattr(namespace, key, default)


def _cli_error(
    code: str,
    path: str,
    message: str,
    expected: str | None = None,
    actual_type: str | None = None,
) -> ContractError:
    return ContractError(
        code,
        "AuditRequest",
        (FieldError(path, code, message, expected, actual_type),),
    )


def _contains(parent: Path, child: Path) -> bool:
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True


def build_request(namespace: Any, cwd: str | os.PathLike[str]) -> AuditRequest:
    repo_raw = _ns_get(namespace, "repo")
    project_id = _ns_get(namespace, "project_id")
    out_raw = _ns_get(namespace, "out")
    decisions_raw = _ns_get(namespace, "decisions")
    case_raw = _ns_get(namespace, "case")
    model_config_raw = _ns_get(namespace, "model_config")
    semantic_raw = _ns_get(namespace, "semantic", "off")
    as_of_raw = _ns_get(namespace, "as_of")
    scope_raw = _ns_get(namespace, "scope")
    command = _ns_get(namespace, "command")
    harness_profile_raw = _ns_get(namespace, "harness_profile")

    if repo_raw in (None, ""):
        raise _cli_error("invalid_argument", "repo", "--repo is required", "path", "null")
    if out_raw in (None, ""):
        raise _cli_error("invalid_argument", "out", "--out is required", "path", "null")

    try:
        repo_path = resolve_cli_path(repo_raw, cwd)
    except ValueError as exc:
        raise _cli_error("invalid_argument", "repo", "repo path is invalid", "path", "string") from exc
    try:
        out_path = resolve_cli_path(out_raw, cwd)
    except ValueError as exc:
        raise _cli_error("invalid_argument", "out", "out path is invalid", "path", "string") from exc

    if not repo_path.exists():
        raise _cli_error("input_not_found", "repo", "project directory does not exist", "directory", "missing")
    if not repo_path.is_dir():
        raise _cli_error("invalid_value", "repo", "project path is not a directory", "directory", "file")

    if as_of_raw is None:
        as_of = _format_utc(datetime.now(timezone.utc))
    else:
        if not isinstance(as_of_raw, str):
            raise _cli_error("invalid_format", "as_of", "expected timestamp string", "timestamp", _type_name(as_of_raw))
        if not _TIMESTAMP_RE.fullmatch(as_of_raw):
            raise _cli_error(
                "invalid_format",
                "as_of",
                "expected timezone-aware ISO 8601 timestamp; got string",
                "timezone-aware ISO 8601 timestamp",
                "string",
            )
        try:
            as_of = _format_utc(_parse_timestamp(as_of_raw))
        except ValueError as exc:
            raise _cli_error(
                "invalid_format",
                "as_of",
                "expected a real timezone-aware ISO 8601 timestamp; got string",
                "timezone-aware ISO 8601 timestamp",
                "string",
            ) from exc

    if semantic_raw is None:
        semantic_raw = "off"
    if semantic_raw not in ("off", "local"):
        raise _cli_error(
            "invalid_argument",
            "semantic",
            "expected one of off, local",
            "one of off, local",
            "string",
        )
    model_config_path: Path | None = None
    if model_config_raw not in (None, ""):
        try:
            model_config_path = resolve_cli_path(model_config_raw, cwd)
        except ValueError as exc:
            raise _cli_error(
                "invalid_argument", "model_config", "model config path is invalid", "path", "string"
            ) from exc
    if semantic_raw == "local" and model_config_path is None:
        raise _cli_error(
            "invalid_argument",
            "model_config",
            "--model-config is required when --semantic local",
            "path",
            "null",
        )

    if out_path == repo_path or _contains(repo_path, out_path) or _contains(out_path, repo_path):
        raise _cli_error(
            "out_inside_repo",
            "out",
            "--out must be outside the project root",
            "path outside repo",
            "string",
        )

    decision_path: Path | None = None
    if decisions_raw is not None:
        if decisions_raw == "":
            raise _cli_error("empty_path", "decisions", "decisions path must not be empty", "path", "string")
        try:
            decision_path = resolve_cli_path(decisions_raw, cwd)
        except ValueError as exc:
            raise _cli_error(
                "invalid_argument", "decisions", "decisions path is invalid", "path", "string"
            ) from exc
        try:
            load_document(decision_path)
        except ContractError as error:
            errors = list(error.errors)
            if errors and errors[0].path == "$":
                first = errors[0]
                errors[0] = FieldError(
                    "decisions",
                    first.code,
                    first.message,
                    first.expected,
                    first.actual_type,
                )
            raise ContractError(error.code, "AuditRequest", errors, error.message) from error

    case_path: Path | None = None
    if case_raw is not None:
        if case_raw == "":
            raise _cli_error("empty_path", "case", "case path must not be empty", "path", "string")
        if command == "audit":
            raise _cli_error(
                "invalid_argument", "case", "--case is only valid for diagnose", "null", "string"
            )
        try:
            case_path = resolve_cli_path(case_raw, cwd)
        except ValueError as exc:
            raise _cli_error("invalid_argument", "case", "case path is invalid", "path", "string") from exc
        if not case_path.is_file():
            raise _cli_error("input_not_found", "case", "case file does not exist", "file", "missing")
        try:
            with open(case_path, "rb") as handle:
                handle.read(0)
        except OSError as exc:
            raise _cli_error("input_not_found", "case", "case file is not readable", "file", "unreadable") from exc

    harness_profile_path: Path | None = None
    if harness_profile_raw not in (None, ""):
        try:
            harness_profile_path = resolve_cli_path(harness_profile_raw, cwd)
        except ValueError as exc:
            raise _cli_error(
                "invalid_argument", "harness_profile", "harness profile path is invalid", "path", "string"
            ) from exc

    if scope_raw is None or (isinstance(scope_raw, (list, tuple)) and len(scope_raw) == 0):
        raw_scopes = ["."]
    elif isinstance(scope_raw, str):
        raw_scopes = [scope_raw]
    else:
        raw_scopes = list(scope_raw)
    scopes: list[str] = []
    scope_errors: list[FieldError] = []
    for index, raw_scope in enumerate(raw_scopes):
        try:
            scopes.append(normalize_scope_prefix(raw_scope))
        except ContractIssue as exc:
            scope_errors.append(
                FieldError(
                    f"task_scope.include_prefixes[{index}]",
                    exc.code,
                    exc.message,
                    exc.expected,
                    "string",
                )
            )
    if scope_errors:
        raise ContractError(scope_errors[0].code, "AuditRequest", scope_errors)

    request_data = {
        "project_id": project_id,
        "repo": str(repo_path),
        "task_scope": {
            "include_prefixes": scopes,
            "exclude_prefixes": [],
            "harnesses": [],
        },
        "as_of": as_of,
        "semantic_mode": semantic_raw,
        "decision_registry": str(decision_path) if decision_path is not None else None,
        "harness_profile": str(harness_profile_path) if harness_profile_path is not None else None,
        "out": str(out_path),
    }
    return parse_object("AuditRequest", request_data)


__all__ = [
    "SCHEMA_VERSION",
    "EXIT_OK",
    "EXIT_FAILED",
    "EXIT_USAGE",
    "EXIT_PARTIAL",
    "Action",
    "ActionKind",
    "ApprovedItem",
    "AuditRequest",
    "BaseFile",
    "BehaviorCheck",
    "BehaviorDetector",
    "ClaimType",
    "CodePointSpan",
    "Confirmation",
    "ConfirmationKind",
    "Constraint",
    "ConstraintOperator",
    "ContractError",
    "CONTRACT_OBJECTS",
    "CoverageItem",
    "CoverageStatus",
    "Decision",
    "DecisionStatus",
    "Edit",
    "EditApproval",
    "EditOperation",
    "Evidence",
    "EvidenceOrigin",
    "EvidenceRef",
    "FieldError",
    "Finding",
    "FindingAssessment",
    "FindingBasis",
    "FindingKind",
    "HarnessId",
    "IdString",
    "Incident",
    "InputKind",
    "InputSummary",
    "LoadReceipt",
    "LoadReceiptEntry",
    "Mode",
    "ModeCommand",
    "ModelSummary",
    "OpenToken",
    "PathString",
    "Provenance",
    "ProvenanceKind",
    "ReceiptCoverage",
    "RelativePath",
    "RepairProposal",
    "RepairStatus",
    "RuleInterpretationStatus",
    "RuleSpan",
    "RunError",
    "RunErrorSeverity",
    "RunId",
    "RunParameters",
    "RunResult",
    "RunStatus",
    "Scalar",
    "Scope",
    "SemanticMode",
    "ScopePrefix",
    "Sha256",
    "SourceLocator",
    "SuggestedAction",
    "Timestamp",
    "UnresolvedQuestion",
    "build_request",
    "dump_object",
    "dumps",
    "load_document",
    "load_json",
    "normalize_relative_path",
    "normalize_scope_prefix",
    "parse_list",
    "parse_object",
    "require_schema_version",
    "resolve_cli_path",
    "validate_object",
]