"""F02 acceptance tests: evidence snapshots, bounded reads and reopening."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
from datetime import datetime, timezone
from pathlib import Path

import pytest

from context_manager.contracts import (
    SCHEMA_VERSION,
    CoverageStatus,
    EvidenceOrigin,
    EvidenceRef,
    Provenance,
    ProvenanceKind,
    dump_object,
)
from context_manager.evidence import (
    EvidenceError,
    EvidenceLimits,
    EvidenceStore,
    prepare_selected_input,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "f02"
NOW = datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc)
PAST = "2026-01-01T00:00:00+00:00"
RUN_ID = "run-20261105-a1b2c3"


def _clock() -> datetime:
    return NOW


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _file_sha(path: Path) -> str:
    return _sha(path.read_bytes())


def _copy_tree(tmp_path: Path) -> tuple[Path, Path, Path]:
    project = tmp_path / "project"
    outside = tmp_path / "outside"
    selected = tmp_path / "selected"
    shutil.copytree(FIXTURES / "project", project)
    shutil.copytree(FIXTURES / "outside", outside)
    shutil.copytree(FIXTURES / "selected", selected)
    return project, outside, selected


def _make_store(
    tmp_path: Path,
    *,
    run_id: str = RUN_ID,
    limits: EvidenceLimits | None = None,
    out_name: str = "project-out",
) -> tuple[EvidenceStore, Path, Path]:
    project, outside, selected = _copy_tree(tmp_path)
    out = tmp_path / out_name
    store = EvidenceStore(
        project,
        out,
        run_id,
        limits=limits,
        clock=_clock,
    )
    return store, project, out


def _meta(store: EvidenceStore, evidence) -> dict:
    path = (
        store.evidence_dir
        / "snapshots"
        / evidence.snapshot_id
        / "meta.json"
    )
    return json.loads(path.read_text(encoding="utf-8"))


def _snapshot_bytes(store: EvidenceStore, evidence) -> bytes:
    return (
        store.evidence_dir
        / "snapshots"
        / evidence.snapshot_id
        / "content.bin"
    ).read_bytes()


def _index_lines(store: EvidenceStore) -> list[dict]:
    path = store.evidence_dir / "index.jsonl"
    if not path.is_file():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _read_lines(store: EvidenceStore) -> list[dict]:
    path = store.evidence_dir / "reads.jsonl"
    if not path.is_file():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _error(callable_, code: str) -> EvidenceError:
    with pytest.raises(EvidenceError) as captured:
        callable_()
    assert captured.value.code == code
    return captured.value


def _full_ref(store: EvidenceStore, evidence) -> EvidenceRef:
    return store.make_ref(evidence, 1, evidence.line_count or 1)


# ---------------------------------------------------------------------------
# F02-01 line endings, Unicode and missing final newline


def test_f02_01_lf_crlf_chinese_and_no_final_newline(tmp_path: Path) -> None:
    store, project, _out = _make_store(tmp_path)

    agents_bytes = (project / "AGENTS.md").read_bytes()
    crlf_bytes = (project / "docs/crlf.md").read_bytes()
    zh_bytes = (project / "docs/zh.md").read_bytes()

    agents = store.capture_file("AGENTS.md", kind="rule_file")
    crlf = store.capture_file("docs/crlf.md", kind="document")
    zh = store.capture_file("docs/zh.md", kind="document")

    assert agents.sha256 == _sha(agents_bytes)
    assert agents.line_count == 5
    assert crlf.sha256 == _sha(crlf_bytes)
    assert crlf.line_count == 2
    assert _meta(store, crlf)["newline_style"] == "crlf"
    assert zh.sha256 == _sha(zh_bytes)
    assert zh.line_count == 2

    crlf_read = store.read_evidence(
        store.make_ref(crlf, 1, 2), caller="inspector"
    )
    assert crlf_read.text == "a\nb"
    assert crlf_read.raw_bytes == len(crlf_bytes)
    assert crlf_read.span_sha256 == _sha(crlf_bytes)
    assert crlf_read.text.split("\n") == ["a", "b"]

    zh_read = store.read_evidence(store.make_ref(zh, 1, 2), caller="inspector")
    assert zh_read.text == "第一行\n第二行"
    assert zh_read.text.split("\n")[-1] == "第二行"
    assert zh_read.span_sha256 == _sha(zh_bytes)

    agents_read = store.read_evidence(_full_ref(store, agents), caller="inspector")
    assert "新客户端使用" in agents_read.text
    assert agents_read.span_sha256 == _sha(agents_bytes)
    store.close()


# ---------------------------------------------------------------------------
# F02-02 captured bytes survive later source changes


def test_f02_02_old_evidence_survives_source_changes(tmp_path: Path) -> None:
    store, project, _out = _make_store(tmp_path)
    source = project / "AGENTS.md"
    original = source.read_bytes()
    first = store.capture_file("AGENTS.md", kind="rule_file")
    first_snapshot = _snapshot_bytes(store, first)

    source.write_bytes(original + b"- new rule\n")
    changed = source.read_bytes()

    old_read = store.read_evidence(_full_ref(store, first), caller="inspector")
    assert old_read.sha256 == _sha(original)
    assert old_read.span_sha256 == _sha(original)
    assert "new rule" not in old_read.text
    assert _snapshot_bytes(store, first) == first_snapshot
    assert _file_sha(source) == _sha(changed)

    second = store.capture_file("AGENTS.md", kind="rule_file")
    assert second.evidence_id != first.evidence_id
    assert second.sha256 == _sha(changed)
    assert second.snapshot_id != first.snapshot_id

    listed = {item.evidence_id: item for item in store.list_evidence()}
    assert set(listed) >= {first.evidence_id, second.evidence_id}
    assert listed[first.evidence_id].sha256 == _sha(original)
    assert listed[second.evidence_id].sha256 == _sha(changed)
    assert _snapshot_bytes(store, first) == first_snapshot
    store.close()


# ---------------------------------------------------------------------------
# F02-03 path boundaries, symlinks and look-alike directories


def test_f02_03_path_traversal_symlinks_and_similar_directories(
    tmp_path: Path,
) -> None:
    store, project, out = _make_store(tmp_path)
    agents_sha = _file_sha(project / "AGENTS.md")
    secret_sha = _file_sha(tmp_path / "outside/secret.md")

    escaped = _error(
        lambda: store.capture_file("../outside/secret.md"), "evidence_path_invalid"
    )
    assert escaped.details["field"] == "relative_path"
    absolute = _error(
        lambda: store.capture_file(str(tmp_path / "outside/secret.md")),
        "evidence_path_invalid",
    )
    assert absolute.details["field"] == "relative_path"

    os.symlink(project / "AGENTS.md", project / "link.md")
    link_error = _error(lambda: store.capture_file("link.md"), "evidence_symlink_rejected")
    assert link_error.details["component"] == "link.md"

    os.symlink(project / "docs", project / "docs-link")
    directory_link = _error(
        lambda: store.capture_file("docs-link/crlf.md"), "evidence_symlink_rejected"
    )
    assert directory_link.details["component"] == "docs-link"

    assert _file_sha(project / "AGENTS.md") == agents_sha
    assert _file_sha(tmp_path / "outside/secret.md") == secret_sha
    assert store.list_evidence() == []

    users = store.capture_file("clients/users.py", kind="code")
    old_users = store.capture_file("clients-old/users.py", kind="code")
    assert users.relative_path == "clients/users.py"
    assert old_users.relative_path == "clients-old/users.py"
    assert users.snapshot_id != old_users.snapshot_id
    assert [item.relative_path for item in store.list_evidence(prefix="clients")] == [
        "clients/users.py"
    ]
    assert [item.relative_path for item in store.list_evidence(prefix="clients-old")] == [
        "clients-old/users.py"
    ]

    # A sibling whose name starts with "project" is not inside the project.
    sibling = EvidenceStore(project, tmp_path / "project-out-2", "run-sibling", clock=_clock)
    sibling.close()

    inside_error = _error(
        lambda: EvidenceStore(project, project / "inside-out", "run-inside", clock=_clock),
        "evidence_output_inside_project",
    )
    assert Path(inside_error.details["output_root"]).is_absolute()
    contains_error = _error(
        lambda: EvidenceStore(project, tmp_path, "run-contains", clock=_clock),
        "evidence_output_contains_project",
    )
    assert Path(contains_error.details["project_root"]).is_absolute()
    assert not (out / "run-inside").exists()
    assert not (tmp_path / "run-contains").exists()
    assert _file_sha(project / "AGENTS.md") == agents_sha
    store.close()


# ---------------------------------------------------------------------------
# F02-04 limits, non-text encodings and read failures


def test_f02_04_limits_binary_invalid_utf8_and_unreadable(tmp_path: Path) -> None:
    project, _outside, _selected = _copy_tree(tmp_path)
    out = tmp_path / "out"
    limited = EvidenceStore(
        project,
        out,
        "run-limit",
        limits=EvidenceLimits(max_project_file_bytes=10),
        clock=_clock,
    )
    agents_sha = _file_sha(project / "AGENTS.md")
    too_large = _error(
        lambda: limited.capture_file("AGENTS.md"), "evidence_too_large"
    )
    assert too_large.details["limit"] == 10
    assert too_large.details["byte_length"] == len((project / "AGENTS.md").read_bytes())
    snapshots = limited.evidence_dir / "snapshots"
    assert not snapshots.exists() or list(snapshots.iterdir()) == []
    assert _file_sha(project / "AGENTS.md") == agents_sha
    coverage = limited.coverage_items()
    assert len(coverage) == 1
    assert coverage[0].status is CoverageStatus.NOT_RUN
    assert coverage[0].reason == "evidence_too_large"
    limited.close()

    store = EvidenceStore(project, tmp_path / "out-default", "run-encoding", clock=_clock)
    binary = store.capture_file("docs/binary.bin", kind="data")
    bad = store.capture_file("docs/bad-utf8.txt", kind="document")
    assert binary.encoding == "binary"
    assert bad.encoding == "invalid-utf8"
    binary_read = _error(
        lambda: store.read_evidence(store.make_ref(binary, 1, 1)), "evidence_not_text"
    )
    assert binary_read.details["encoding"] == "binary"
    bad_read = _error(
        lambda: store.read_evidence(store.make_ref(bad, 1, 1)), "evidence_not_text"
    )
    assert bad_read.details["encoding"] == "invalid-utf8"
    reasons = {item.reason for item in store.coverage_items()}
    assert {"binary", "invalid_utf8"} <= reasons
    passed = [item for item in store.coverage_items() if item.status is CoverageStatus.PASSED]
    assert passed == []

    if os.geteuid() != 0:
        blocked = project / "docs" / "blocked.md"
        blocked.write_text("blocked\n", encoding="utf-8")
        blocked_sha = _file_sha(blocked)
        blocked.chmod(0)
        try:
            unreadable = _error(
                lambda: store.capture_file("docs/blocked.md"), "evidence_unreadable"
            )
            assert "relative_path" in unreadable.details or "component" in unreadable.details
        finally:
            blocked.chmod(stat.S_IRUSR | stat.S_IWUSR)
        assert store.coverage_items()[-1].reason == "evidence_unreadable"
        assert _file_sha(blocked) == blocked_sha
    store.close()


# ---------------------------------------------------------------------------
# F02-05 cross-run, digest, range and expiry errors


def test_f02_05_cross_run_digest_range_and_expiry(tmp_path: Path) -> None:
    store, project, out = _make_store(tmp_path)
    evidence = store.capture_file("AGENTS.md", kind="rule_file")
    source_sha = _file_sha(project / "AGENTS.md")
    good = _full_ref(store, evidence)

    other = EvidenceStore(project, out, "run-other", clock=_clock)
    other_evidence = other.capture_file("AGENTS.md", kind="rule_file")
    cross = _error(
        lambda: store.read_evidence(_full_ref(other, other_evidence)), "evidence_cross_run"
    )
    assert cross.details["run_id"] == "run-other"
    assert other_evidence.snapshot_id == evidence.snapshot_id
    other.close()

    underscored = EvidenceStore(project, out, "run_with_parts", clock=_clock)
    underscored_evidence = underscored.capture_file("docs/zh.md")
    assert underscored_evidence.evidence_id == "ev_run_with_parts_0001"
    assert (
        underscored.read_evidence(
            underscored.make_ref(underscored_evidence, 1, 2), caller="test"
        ).text
        == "第一行\n第二行"
    )
    cross_embedded = _error(
        lambda: store.read_evidence(
            underscored.make_ref(underscored_evidence, 1, 2)
        ),
        "evidence_cross_run",
    )
    assert cross_embedded.details["run_id"] == "run_with_parts"
    underscored.close()

    wrong_sha = EvidenceRef(
        evidence.evidence_id, "f" * 64, 1, evidence.line_count or 1
    )
    mismatch = _error(
        lambda: store.read_evidence(wrong_sha), "evidence_digest_mismatch"
    )
    assert mismatch.details["expected_sha256"] == evidence.sha256
    assert mismatch.details["actual_sha256"] == "f" * 64

    zero = _error(
        lambda: store.read_evidence(
            EvidenceRef(evidence.evidence_id, evidence.sha256, 0, 1)
        ),
        "evidence_range_invalid",
    )
    assert zero.details["line_start"] == 0
    too_far = _error(
        lambda: store.read_evidence(
            EvidenceRef(
                evidence.evidence_id,
                evidence.sha256,
                1,
                (evidence.line_count or 0) + 1,
            )
        ),
        "evidence_range_invalid",
    )
    assert too_far.details["line_count"] == evidence.line_count
    reversed_range = _error(
        lambda: store.read_evidence(
            EvidenceRef(evidence.evidence_id, evidence.sha256, 3, 2)
        ),
        "evidence_range_invalid",
    )
    assert reversed_range.details["line_start"] == 3
    assert reversed_range.details["line_end"] == 2

    missing = _error(
        lambda: store.read_evidence(
            EvidenceRef(f"ev_{RUN_ID}_9999", evidence.sha256, 1, 1)
        ),
        "evidence_not_found",
    )
    assert missing.details["evidence_id"] == f"ev_{RUN_ID}_9999"

    expired_evidence = store.capture_file(
        "docs/zh.md", kind="document", expires_at=PAST
    )
    expired_ref = EvidenceRef(
        expired_evidence.evidence_id,
        expired_evidence.sha256,
        1,
        expired_evidence.line_count or 1,
    )
    expired = _error(lambda: store.read_evidence(expired_ref), "evidence_expired")
    assert expired.details["reason"] == "retention_elapsed"

    missing_snapshot = store.capture_file("docs/crlf.md", kind="document")
    snapshot_path = (
        store.evidence_dir
        / "snapshots"
        / missing_snapshot.snapshot_id
        / "content.bin"
    )
    snapshot_path.unlink()
    missing_read = _error(
        lambda: store.read_evidence(
            store.make_ref(missing_snapshot, 1, missing_snapshot.line_count or 1)
        ),
        "evidence_expired",
    )
    assert missing_read.details["reason"] == "snapshot_missing"
    assert _file_sha(project / "AGENTS.md") == source_sha
    assert store.read_raw(evidence.evidence_id, max_bytes=4096) == (
        project / "AGENTS.md"
    ).read_bytes()
    store.close()


# ---------------------------------------------------------------------------
# F02-06 truncation and continuation


def test_f02_06_line_and_byte_truncation_resume(tmp_path: Path) -> None:
    store, project, _out = _make_store(tmp_path)
    lines = store.capture_file("docs/lines-200.md", kind="document")
    source_bytes = (project / "docs/lines-200.md").read_bytes()
    assert lines.line_count == 200
    assert lines.sha256 == _sha(source_bytes)

    first = store.read_evidence(
        store.make_ref(lines, 1, 200), caller="inspector"
    )
    assert (first.line_start, first.line_end) == (1, 80)
    assert first.truncated is True
    assert first.truncation_reason == "line_limit"
    assert first.next_line == 81
    assert len(first.text.split("\n")) == 80

    second = store.read_evidence(
        store.make_ref(lines, first.next_line, 200), caller="inspector"
    )
    assert (second.line_start, second.line_end) == (81, 160)
    assert second.truncated is True
    assert second.truncation_reason == "line_limit"
    assert second.next_line == 161

    third = store.read_evidence(
        store.make_ref(lines, second.next_line, 200), caller="inspector"
    )
    assert (third.line_start, third.line_end) == (161, 200)
    assert third.truncated is False
    assert third.truncation_reason is None
    assert third.next_line is None
    assert third.span_sha256 == _sha(source_bytes[first_span_end(source_bytes, 160) :])

    wide = project / "docs" / "wide.md"
    wide.write_bytes(b"".join(b"x" * 199 + b"\n" for _ in range(100)))
    wide_evidence = store.capture_file("docs/wide.md", kind="document")
    byte_limited = store.read_evidence(
        store.make_ref(wide_evidence, 1, 100), caller="inspector"
    )
    assert (byte_limited.line_start, byte_limited.line_end) == (1, 40)
    assert byte_limited.truncated is True
    assert byte_limited.truncation_reason == "byte_limit"
    assert byte_limited.next_line == 41
    assert byte_limited.raw_bytes == 40 * 200

    short = store.read_evidence(
        store.make_ref(lines, 1, 200), max_lines=10, caller="inspector"
    )
    assert (short.line_start, short.line_end) == (1, 10)
    too_many = _error(
        lambda: store.read_evidence(
            store.make_ref(lines, 1, 200), max_lines=200
        ),
        "evidence_limit_invalid",
    )
    assert too_many.details["field"] == "max_lines"
    store.close()


def first_span_end(data: bytes, line_end: int) -> int:
    """Return the byte offset just after *line_end* (1-based, newline included)."""

    position = -1
    for _ in range(line_end):
        position = data.find(b"\n", position + 1)
    assert position != -1
    return position + 1


# ---------------------------------------------------------------------------
# F02-07 reads that change concurrently


def test_f02_07_unstable_reads_retry_and_never_mix(tmp_path: Path, monkeypatch) -> None:
    store, project, _out = _make_store(tmp_path)
    source_sha = _file_sha(project / "AGENTS.md")
    versions = [
        (b"version-one\n", (1, 2, 3, 4)),
        (b"version-two\n", (1, 2, 5, 6)),
    ]

    def fake_read(path, limit):
        return versions.pop(0)

    monkeypatch.setattr(store, "_read_once", fake_read)
    changed = _error(
        lambda: store.capture_file("AGENTS.md"), "evidence_changed_during_read"
    )
    assert changed.exit_code == 3
    assert changed.details["relative_path"] == "AGENTS.md"
    assert store.list_evidence() == []
    snapshots = store.evidence_dir / "snapshots"
    assert not snapshots.exists() or list(snapshots.iterdir()) == []
    assert store.coverage_items()[-1].status is CoverageStatus.NOT_RUN
    assert store.coverage_items()[-1].reason == "evidence_changed_during_read"
    assert _file_sha(project / "AGENTS.md") == source_sha
    store.close()

    project2 = tmp_path / "project2"
    shutil.copytree(FIXTURES / "project", project2)
    out2 = tmp_path / "out2"
    stable = EvidenceStore(project2, out2, "run-stable", clock=_clock)
    same_version = b"same-version\n"
    calls = {"n": 0}

    def same_read(path, limit):
        calls["n"] += 1
        return same_version, (1, 2, 99, 100)

    monkeypatch.setattr(stable, "_read_once", same_read)
    accepted = stable.capture_file("AGENTS.md")
    assert calls["n"] == 2
    assert accepted.sha256 == _sha(same_version)
    assert _snapshot_bytes(stable, accepted) == same_version
    assert accepted.sha256 != _sha(b"version-one\nversion-two\n")
    stable.close()

    project3 = tmp_path / "project3"
    shutil.copytree(FIXTURES / "project", project3)
    no_retry = EvidenceStore(
        project3,
        tmp_path / "out3",
        "run-no-retry",
        limits=EvidenceLimits(capture_retry_limit=0),
        clock=_clock,
    )
    calls["n"] = 0

    def unstable(path, limit):
        calls["n"] += 1
        return b"one\n", (9, 9, 4, 1)

    monkeypatch.setattr(no_retry, "_read_once", unstable)
    _error(
        lambda: no_retry.capture_file("AGENTS.md"),
        "evidence_changed_during_read",
    )
    assert calls["n"] == 1
    no_retry.close()


# ---------------------------------------------------------------------------
# F02-08 selected external inputs and output boundaries


def test_f02_08_selected_inputs_prepared_bytes_and_conflicts(
    tmp_path: Path,
) -> None:
    store, project, out = _make_store(tmp_path)
    case_path = tmp_path / "selected" / "case.json"
    original = case_path.read_bytes()

    prepared = prepare_selected_input(case_path)
    assert prepared.sha256 == _sha(original)
    assert prepared.basename == "case.json"
    # The original changes after pre-reading; capture must use the prepared bytes.
    case_path.write_bytes(original + b"\n")
    assert _file_sha(case_path) != prepared.sha256

    evidence = store.capture_selected_input(prepared, input_id="case-1")
    assert evidence.origin is EvidenceOrigin.SELECTED_INPUT
    assert evidence.input_id == "case-1"
    assert evidence.relative_path == "selected-input/case-1/case.json"
    assert evidence.sha256 == _sha(original)
    assert _snapshot_bytes(store, evidence) == original
    read = store.read_evidence(_full_ref(store, evidence), caller="inspector")
    assert json.loads(read.text) == {"case_id": "case-1", "events": []}

    (tmp_path / "selected" / "other.json").write_text("{}\n", encoding="utf-8")
    listed = [item.evidence_id for item in store.list_evidence()]
    assert listed == [evidence.evidence_id]
    serialized = json.dumps(dump_object(evidence), ensure_ascii=False)
    assert str(tmp_path) not in serialized
    assert "selected-input/case-1/case.json" in serialized

    direct = store.capture_selected_input(
        tmp_path / "selected" / "case.json", input_id="case-2"
    )
    assert direct.sha256 == _sha(original + b"\n")
    assert direct.relative_path == "selected-input/case-2/case.json"

    inside_project = store.capture_selected_input(
        project / "docs/zh.md", input_id="inside-1"
    )
    assert inside_project.origin is EvidenceOrigin.SELECTED_INPUT
    assert inside_project.relative_path == "selected-input/inside-1/zh.md"

    os.symlink(tmp_path / "selected", tmp_path / "selected-link")
    prepared_link = prepare_selected_input(tmp_path / "selected-link/case.json")
    parent_linked = store.capture_selected_input(prepared_link, input_id="case-3")
    assert parent_linked.relative_path == "selected-input/case-3/case.json"
    assert parent_linked.sha256 == prepared_link.sha256

    second = tmp_path / "selected" / "case-alt.json"
    second.write_bytes(b'{"case_id": "case-2"}\n')
    prepared_alt = prepare_selected_input(second)
    conflict = _error(
        lambda: store.capture_selected_input(prepared_alt, input_id="case-1"),
        "evidence_input_id_conflict",
    )
    assert conflict.details["input_id"] == "case-1"
    assert conflict.details["existing_evidence_id"] == evidence.evidence_id

    traversal = _error(
        lambda: store.capture_file("../outside/secret.md"), "evidence_path_invalid"
    )
    assert traversal.details["field"] == "relative_path"
    bad_input = _error(
        lambda: store.capture_selected_input(project / "AGENTS.md", input_id="bad id"),
        "evidence_input_id_invalid",
    )
    assert bad_input.details["input_id"] == "bad id"

    inside = _error(
        lambda: EvidenceStore(project, project / "out", "run-inner", clock=_clock),
        "evidence_output_inside_project",
    )
    assert inside.details["output_root"].endswith("out")
    equal = _error(
        lambda: EvidenceStore(project, project, "run-equal", clock=_clock),
        "evidence_output_inside_project",
    )
    assert Path(equal.details["output_root"]) == project.resolve()
    contains = _error(
        lambda: EvidenceStore(project, tmp_path, "run-outer", clock=_clock),
        "evidence_output_contains_project",
    )
    assert contains.exit_code == 2

    assert store.probe_path("AGENTS.md").status == "file"
    assert store.probe_path("AGENTS.md").byte_length == (project / "AGENTS.md").stat().st_size
    assert store.probe_path("docs").status == "directory"
    assert store.probe_path("docs/missing.md").status == "missing"
    assert store.probe_path("../outside/secret.md").status == "unsafe_path"
    link = project / "docs" / "link.md"
    os.symlink(project / "AGENTS.md", link)
    probe = store.probe_path("docs/link.md")
    assert probe.status == "symlink"
    assert probe.is_symlink is True
    assert out.exists()
    store.close()


# ---------------------------------------------------------------------------
# F02-09 oversized single lines


def test_f02_09_oversized_line_rejected_but_neighbours_readable(
    tmp_path: Path,
) -> None:
    store, project, _out = _make_store(tmp_path)
    target = project / "docs" / "long-line.md"
    long_line = b"x" * 9216
    target.write_bytes(b"short before\n" + long_line + b"\nshort after\n")
    source_sha = _file_sha(target)
    evidence = store.capture_file("docs/long-line.md")

    error = _error(
        lambda: store.read_evidence(store.make_ref(evidence, 2, 2)),
        "evidence_line_too_long",
    )
    assert error.details["line_no"] == 2
    assert error.details["line_bytes"] == len(long_line) + 1
    assert error.details["limit"] == store.limits.max_read_bytes

    before = store.read_evidence(store.make_ref(evidence, 1, 1), caller="test")
    after = store.read_evidence(store.make_ref(evidence, 3, 3), caller="test")
    assert before.text == "short before"
    assert after.text == "short after"

    records = _read_lines(store)
    rejected = [item for item in records if item["read_id"] is None]
    assert rejected[-1]["code"] == "evidence_line_too_long"
    assert rejected[-1]["requested_line_start"] == 2
    ranges = store.covered_ranges(evidence.evidence_id)
    assert ranges == [(1, 1), (3, 3)]
    assert all(not (start <= 2 <= end) for start, end in ranges)
    assert _file_sha(target) == source_sha
    store.close()


# ---------------------------------------------------------------------------
# F02-10 empty files, BOM and exact limits


def test_f02_10_empty_bom_and_exact_limit(tmp_path: Path) -> None:
    store, project, _out = _make_store(tmp_path)
    empty = store.capture_file("docs/empty.md", kind="document")
    assert empty.line_count == 0
    empty_read = store.read_evidence(store.make_ref(empty, 1, 1), caller="inspector")
    assert empty_read.empty is True
    assert empty_read.text == ""
    assert (empty_read.line_start, empty_read.line_end) == (1, 1)
    assert empty_read.raw_bytes == 0
    assert empty_read.span_sha256 == _sha(b"")
    omitted = store.read_evidence(
        {
            "evidence_id": empty.evidence_id,
            "sha256": empty.sha256,
            "line_start": 1,
        },
        caller="test",
    )
    assert omitted.empty is True
    assert omitted.requested_line_end is None
    too_far = _error(
        lambda: store.read_evidence(store.make_ref(empty, 2, 2)),
        "evidence_range_invalid",
    )
    assert too_far.details["line_count"] == 0

    bom_bytes = (project / "docs/bom.md").read_bytes()
    bom = store.capture_file("docs/bom.md", kind="document")
    assert bom.encoding == "utf-8-bom"
    assert bom.line_count == 1
    bom_read = store.read_evidence(store.make_ref(bom, 1, 1), caller="inspector")
    assert "\ufeff" not in bom_read.text
    assert bom_read.text == "带 BOM 的首行"
    assert bom_read.span_sha256 == _sha(bom_bytes)

    exact = project / "docs" / "exact.txt"
    exact.write_bytes(b"z" * 32)
    store32 = EvidenceStore(
        project,
        tmp_path / "out32",
        "run-32",
        limits=EvidenceLimits(max_project_file_bytes=32),
        clock=_clock,
    )
    accepted = store32.capture_file("docs/exact.txt")
    assert accepted.byte_length == 32
    exact.write_bytes(b"z" * 33)
    over = _error(
        lambda: store32.capture_file("docs/exact.txt"), "evidence_too_large"
    )
    assert over.details["byte_length"] == 33
    assert over.details["limit"] == 32
    assert _file_sha(exact) == _sha(b"z" * 33)
    snapshot_dirs = {
        path.name for path in (store32.evidence_dir / "snapshots").iterdir()
    }
    assert accepted.snapshot_id in snapshot_dirs
    assert len(snapshot_dirs) == 1
    store32.close()
    store.close()


# ---------------------------------------------------------------------------
# F02-11 idempotence, corruption, reopening and concurrent stores


def test_f02_11_duplicate_corruption_reopen_and_two_stores(tmp_path: Path) -> None:
    store, project, out = _make_store(tmp_path)
    first = store.capture_file("AGENTS.md", kind="rule_file")
    duplicate = store.capture_file("AGENTS.md", kind="rule_file")
    assert duplicate.evidence_id == first.evidence_id
    assert len(_index_lines(store)) == 1
    assert _meta(store, first)["capture_count"] == 2

    # Internal full-byte helpers must not create model read coverage.
    assert store.covered_ranges(first.evidence_id) == []
    assert _read_lines(store) == []
    assert store.read_raw(first.evidence_id, max_bytes=4096) == (
        project / "AGENTS.md"
    ).read_bytes()
    assert store.snapshot_bytes(first.evidence_id) == (project / "AGENTS.md").read_bytes()
    assert store.verify_current_digest("AGENTS.md", first.sha256).ok is True
    assert store.covered_ranges(first.evidence_id) == []
    assert _read_lines(store) == []

    content_path = (
        store.evidence_dir / "snapshots" / first.snapshot_id / "content.bin"
    )
    original_bytes = content_path.read_bytes()
    content_path.write_bytes(b"X" + original_bytes[1:])
    corrupt = _error(
        lambda: store.read_evidence(_full_ref(store, first)), "evidence_snapshot_corrupt"
    )
    assert corrupt.details["evidence_id"] == first.evidence_id
    content_path.write_bytes(original_bytes)
    assert store.read_evidence(_full_ref(store, first)).text.startswith("# 项目规则")

    reopened_project = tmp_path / "reopened"
    shutil.copytree(FIXTURES / "project", reopened_project)
    reopened_out = tmp_path / "reopened-out"
    original_store = EvidenceStore(reopened_project, reopened_out, "run-reopen", clock=_clock)
    kept = original_store.capture_file("docs/zh.md", kind="document")
    original_store.close()
    run_dir = reopened_out / "run-reopen"
    shutil.rmtree(reopened_project)
    assert not reopened_project.exists()
    reopened = EvidenceStore.open_existing(run_dir)
    assert [item.evidence_id for item in reopened.list_evidence()] == [
        kept.evidence_id
    ]
    kept_read = reopened.read_evidence(
        reopened.make_ref(reopened.get(kept.evidence_id), 1, 2), caller="report"
    )
    assert kept_read.text == "第一行\n第二行"
    assert reopened.get(kept.evidence_id).sha256 == kept.sha256
    assert reopened.location(kept.evidence_id).logical_name == "docs/zh.md"
    reopened.close()

    multi_project = tmp_path / "multi-project"
    shutil.copytree(FIXTURES / "project", multi_project)
    multi_out = tmp_path / "multi-out"
    store_a = EvidenceStore(multi_project, multi_out, "run-multi", clock=_clock)
    store_b = EvidenceStore(multi_project, multi_out, "run-multi", clock=_clock)
    ids = [
        store_a.capture_file("AGENTS.md").evidence_id,
        store_b.capture_file("docs/crlf.md").evidence_id,
        store_a.capture_file("docs/zh.md").evidence_id,
        store_b.capture_file("clients/users.py", kind="code").evidence_id,
    ]
    assert ids == [f"ev_run-multi_{index:04d}" for index in range(1, 5)]
    assert len(set(ids)) == 4
    records = _index_lines(store_a)
    assert len(records) == 4
    assert [record["evidence_id"] for record in records] == ids
    assert [item.evidence_id for item in store_b.list_evidence()] == sorted(
        ids, key=lambda value: (
            store_b.get(value).relative_path,
            value,
        )
    )
    assert _file_sha(multi_project / "AGENTS.md") == _sha(
        (FIXTURES / "project" / "AGENTS.md").read_bytes()
    )
    multi_read = store_a.read_evidence(
        store_a.make_ref(store_a.get(ids[0]), 1, 1), caller="inspector"
    )
    reopened_multi = EvidenceStore.open_existing(multi_out / "run-multi")
    assert reopened_multi.covered_ranges(ids[0]) == [(1, 1)]
    assert len(reopened_multi.coverage_items()) == 4
    continued = reopened_multi.capture_file("docs/empty.md")
    assert continued.evidence_id == "ev_run-multi_0005"
    assert multi_read.text.startswith("# 项目规则")
    reopened_multi.close()

    conflict_project = tmp_path / "conflict-project"
    shutil.copytree(FIXTURES / "project", conflict_project)
    conflict = _error(
        lambda: EvidenceStore(
            conflict_project, multi_out, "run-multi", clock=_clock
        ),
        "evidence_run_conflict",
    )
    assert conflict.details["run_dir"].endswith("run-multi")

    store_a.close()
    store_b.close()
    store.close()
    assert out.exists()


# ---------------------------------------------------------------------------
# Current-file digest verification (F08 seam)


def test_verify_current_digest_reports_without_capturing(tmp_path: Path) -> None:
    store, project, _out = _make_store(tmp_path)
    source = project / "AGENTS.md"
    good = store.verify_current_digest("AGENTS.md", _file_sha(source))
    assert good.ok is True
    assert good.code is None
    assert good.actual_sha256 == _file_sha(source)

    wrong = store.verify_current_digest("AGENTS.md", "b" * 64)
    assert wrong.ok is False
    assert wrong.code == "evidence_digest_mismatch"
    assert wrong.actual_sha256 == _file_sha(source)

    missing = store.verify_current_digest("docs/nope.md", _file_sha(source))
    assert missing.ok is False
    assert missing.code == "evidence_not_found"
    assert missing.actual_sha256 is None

    bad_expected = store.verify_current_digest("AGENTS.md", "not-a-digest")
    assert bad_expected.ok is False
    assert bad_expected.code == "evidence_ref_invalid"

    os.symlink(source, project / "link-verify.md")
    linked = store.verify_current_digest("link-verify.md", _file_sha(source))
    assert linked.ok is False
    assert linked.code == "evidence_symlink_rejected"

    assert store.coverage_items() == []
    assert _read_lines(store) == []
    store.close()


# ---------------------------------------------------------------------------
# Structured errors and serialisation


def test_evidence_error_serialises_without_content(tmp_path: Path) -> None:
    store, project, _out = _make_store(tmp_path)
    secret_text = (project / "outside-secret.md")
    secret_text.write_text("TOP-SECRET-CONTENT\n", encoding="utf-8")
    error = _error(
        lambda: store.read_evidence(
            EvidenceRef(f"ev_{RUN_ID}_0001", "a" * 64, 1, 1)
        ),
        "evidence_not_found",
    )
    payload = error.to_dict()
    assert payload["schema_version"] == SCHEMA_VERSION
    assert payload["error"]["code"] == "evidence_not_found"
    assert "TOP-SECRET-CONTENT" not in json.dumps(payload)
    assert error.details["evidence_id"] == f"ev_{RUN_ID}_0001"
    store.close()


def test_ref_validation_reports_unread_ranges(tmp_path: Path) -> None:
    store, _project, _out = _make_store(tmp_path)
    evidence = store.capture_file("AGENTS.md", kind="rule_file")
    ref = _full_ref(store, evidence)
    before = store.validate_ref(ref, require_covered=True)
    assert before.ok is False
    assert before.code == "evidence_not_read"
    assert before.covered is False
    assert before.detail["covered_ranges"] == []

    read = store.read_evidence(store.make_ref(evidence, 1, 2), caller="analyst")
    assert read.line_end == 2
    partial = store.validate_ref(ref, require_covered=True)
    assert partial.ok is False
    assert partial.code == "evidence_not_read"
    after = store.read_evidence(ref, caller="analyst")
    assert after.text
    validated = store.validate_ref(ref, require_covered=True)
    assert validated.ok is True
    assert validated.code is None
    assert validated.covered is True
    assert validated.detail["covered_ranges"] == [(1, 5)]
    assert store.covered_ranges(evidence.evidence_id) == [(1, 5)]
    store.close()


def test_provenance_and_imported_bytes(tmp_path: Path) -> None:
    store, _project, _out = _make_store(tmp_path)
    payload = b'{"events": []}\n'
    evidence = store.capture_imported_bytes(
        payload,
        logical_name="history/events.json",
        input_id="pkg-1",
        provenance=Provenance(ProvenanceKind.SYNTHETIC, "fixture", "1"),
    )
    assert evidence.origin is EvidenceOrigin.IMPORTED
    assert evidence.relative_path == "imported/pkg-1/history/events.json"
    assert evidence.sha256 == _sha(payload)
    assert store.read_raw(evidence.evidence_id, max_bytes=100) == payload
    snapshot_error = _error(
        lambda: store.snapshot_bytes(evidence.evidence_id), "evidence_not_found"
    )
    assert snapshot_error.details["reason"] == "not_project_file"
    assert store.coverage_items()[-1].status is CoverageStatus.PASSED
    assert store.read_evidence(store.make_ref(evidence, 1, 1)).text == payload.decode().strip()
    store.close()


def test_limit_validation_and_unknown_enums(tmp_path: Path) -> None:
    project, _outside, _selected = _copy_tree(tmp_path)
    too_high = _error(
        lambda: EvidenceLimits(max_project_file_bytes=1_048_577),
        "evidence_limit_invalid",
    )
    assert too_high.details["field"] == "max_project_file_bytes"
    zero_lines = _error(
        lambda: EvidenceLimits(max_read_lines=0),
        "evidence_limit_invalid",
    )
    assert zero_lines.details["field"] == "max_read_lines"
    negative_retry = _error(
        lambda: EvidenceLimits(capture_retry_limit=-1), "evidence_limit_invalid"
    )
    assert negative_retry.details["field"] == "capture_retry_limit"
    store = EvidenceStore(project, tmp_path / "out", "run-limits", clock=_clock)
    unknown_kind = _error(
        lambda: store.capture_file("AGENTS.md", kind="unknown"),
        "evidence_ref_invalid",
    )
    assert unknown_kind.details["field"] == "kind"
    captured = store.capture_file("AGENTS.md")
    upper_sha = _error(
        lambda: store.read_evidence(
            EvidenceRef(captured.evidence_id, "A" * 64, 1, 1)
        ),
        "evidence_ref_invalid",
    )
    assert upper_sha.details["field"] == "sha256"
    store.close()

# ---------------------------------------------------------------------------
# Review follow-up: imported history shares the selected input_id (F05 seam)


def test_f02_r1_imported_history_shares_input_id(tmp_path: Path) -> None:
    store, project, _out = _make_store(tmp_path)
    first = store.capture_imported_bytes(
        b'{"n": 1}\n', logical_name="case/a.json", input_id="pkg-1"
    )
    second = store.capture_imported_bytes(
        b'{"n": 2}\n', logical_name="case/b.json", input_id="pkg-1"
    )
    assert first.evidence_id != second.evidence_id
    duplicate = store.capture_imported_bytes(
        b'{"n": 1}\n', logical_name="case/copy.json", input_id="pkg-1"
    )
    assert duplicate.evidence_id == first.evidence_id

    prepared = prepare_selected_input(tmp_path / "selected" / "case.json")
    package = store.capture_selected_input(prepared, input_id="pkg-1")
    history = store.capture_imported_bytes(
        b'{"history": true}\n', logical_name="case/old.md", input_id="pkg-1"
    )
    assert history.evidence_id != package.evidence_id
    same_package = store.capture_selected_input(prepared, input_id="pkg-1")
    assert same_package.evidence_id == package.evidence_id

    conflict = _error(
        lambda: store.capture_selected_input(
            project / "AGENTS.md", input_id="pkg-1"
        ),
        "evidence_input_id_conflict",
    )
    assert conflict.details["input_id"] == "pkg-1"
    assert conflict.details["existing_evidence_id"] == package.evidence_id
    assert len(store.list_evidence(origin="imported")) == 3
    store.close()


# ---------------------------------------------------------------------------
# Review follow-up: project reads must not follow a parent swapped after check


def test_f02_r2_project_parent_swap_is_rejected(
    tmp_path: Path, monkeypatch
) -> None:
    store, project, _out = _make_store(tmp_path)
    outside = tmp_path / "outside"
    (outside / "sub").mkdir()
    (outside / "sub" / "file.txt").write_bytes(b"OUTSIDE-SECRET\n")
    (project / "sub").mkdir()
    (project / "sub" / "file.txt").write_bytes(b"INSIDE\n")
    outside_sha = _file_sha(outside / "sub" / "file.txt")
    inside_sha = _file_sha(project / "sub" / "file.txt")

    original_check = store._walker.check

    def check_then_swap(relative_path: str) -> str:
        checked = original_check(relative_path)
        os.rename(project / "sub", project / "sub-real")
        os.symlink(outside / "sub", project / "sub")
        return checked

    monkeypatch.setattr(store._walker, "check", check_then_swap)
    error = _error(
        lambda: store.capture_file("sub/file.txt"),
        "evidence_symlink_rejected",
    )
    assert error.details["component"] == "sub"
    assert store.list_evidence() == []
    snapshots = store.evidence_dir / "snapshots"
    assert not snapshots.exists() or list(snapshots.iterdir()) == []
    coverage = store.coverage_items()[-1]
    assert coverage.status is CoverageStatus.NOT_RUN
    assert coverage.reason == "evidence_symlink_rejected"
    assert _file_sha(outside / "sub" / "file.txt") == outside_sha
    assert _file_sha(project / "sub-real" / "file.txt") == inside_sha
    store.close()
