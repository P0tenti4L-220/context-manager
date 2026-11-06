"""F01 acceptance tests: shared contracts, validation and CLI skeleton."""

from __future__ import annotations

import copy
import importlib
import importlib.metadata
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from context_manager import __version__
from context_manager.contracts import (
    SCHEMA_VERSION,
    ContractError,
    build_request,
    dump_object,
    dumps,
    load_document,
    load_json,
    normalize_relative_path,
    normalize_scope_prefix,
    parse_list,
    parse_object,
    validate_object,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
TS = "2026-11-04T00:00:00Z"
TS_LATER = "2026-12-01T08:30:00+08:00"


def _scope() -> dict:
    return {"include_prefixes": ["."], "exclude_prefixes": [], "harnesses": []}


def _evref() -> dict:
    return {"evidence_id": "ev-1", "sha256": SHA_A, "line_start": 1, "line_end": 2}


def _valid_samples() -> dict[str, dict]:
    scope = _scope()
    evidence_ref = _evref()
    provenance_project = {"kind": "project_file", "adapter": None, "version": None}
    provenance_synthetic = {"kind": "synthetic", "adapter": "fixture", "version": "1"}
    coverage = {
        "check_id": "f03.rules",
        "status": "passed",
        "inputs": ["ev-1"],
        "reason": None,
        "artifact_refs": [],
    }
    constraint = {
        "subject": "theme",
        "operator": "equals",
        "value": "dark",
        "conditions": {"环境": "生产"},
        "constraint_id": None,
        "span": {"start": 0, "end": 5},
    }
    approved = {"item_id": "d-1", "source_ref": evidence_ref}
    edit_approval = {
        "decision_id": "d-1",
        "target_path": "docs/AGENTS.md",
        "operation": "replace_span",
        "line_start": 1,
        "line_end": 2,
        "old_text_sha256": SHA_A,
        "replacement_sha256": SHA_B,
        "rule_ids": ["rule-1"],
        "valid_until": None,
    }
    action = {
        "kind": "edit",
        "target_path": "src/app.py",
        "evidence_ref": None,
        "before_ref": None,
        "after_ref": None,
        "payload": {},
    }
    incident = {
        "event_id": "e-1",
        "session_id": "s-1",
        "task_id": None,
        "attempt_id": None,
        "occurred_at": None,
        "context_epoch": None,
        "scope": None,
        "harness": "codex",
        "snapshot_id": None,
        "action": action,
        "source_ref": None,
    }
    receipt_entry = {
        "rule_id": "rule-1",
        "source_ref": evidence_ref,
        "text_sha256": SHA_A,
        "channel": "project_instructions",
        "scope": None,
    }
    receipt = {
        "receipt_id": "r-1",
        "session_id": "s-1",
        "context_epoch": 0,
        "before_event_id": "e-1",
        "harness": "codex",
        "channels_covered": ["project_instructions"],
        "captured_at": None,
        "entries": [receipt_entry],
        "coverage": "partial",
        "provenance": provenance_synthetic,
    }
    evidence = {
        "evidence_id": "ev-1",
        "run_id": "run-1",
        "kind": "file",
        "relative_path": "docs/AGENTS.md",
        "sha256": SHA_A,
        "snapshot_id": "snap-1",
        "captured_at": TS,
        "source_revision": None,
        "byte_length": 12,
        "line_count": 2,
        "encoding": "utf-8",
        "provenance": provenance_project,
        "origin": "project",
        "input_id": None,
        "expires_at": None,
    }
    finding = {
        "finding_id": "f-1",
        "kind": "stale_rule",
        "claim": "中文规则可能过时\n第二行",
        "scope": scope,
        "assessment": "hypothesis",
        "basis": "model_candidate",
        "support_refs": [evidence_ref],
        "counter_refs": [],
        "missing_evidence": ["缺少新决策"],
        "suggested_action": "ask_user",
        "checks": [coverage],
        "related_event_ids": [],
        "related_rule_ids": ["rule-1"],
        "related_finding_ids": [],
        "claim_type": "fact",
        "synthetic": False,
    }
    base_file = {"relative_path": "docs/AGENTS.md", "sha256": SHA_A, "evidence_id": "ev-1"}
    edit = {
        "edit_id": "edit-1",
        "finding_id": "f-1",
        "relative_path": "docs/AGENTS.md",
        "operation": "replace_span",
        "line_start": 1,
        "line_end": 2,
        "old_text_sha256": SHA_A,
        "new_text": "新文本\n第二行",
    }
    question = {
        "question_id": "q-1",
        "prompt": "是否批准这条修改？",
        "finding_ids": ["f-1"],
        "needed": ["用户确认"],
    }
    proposal = {
        "proposal_id": "p-1",
        "run_id": "run-1",
        "finding_ids": ["f-1"],
        "base_files": [base_file],
        "edits": [edit],
        "diff_sha256": SHA_C,
        "status": "preview_ready",
        "checks": [coverage],
        "unresolved_questions": [question],
    }
    model = {
        "name": "local-model",
        "transport": "openai_compatible",
        "temperature": 0.2,
        "timeout_s": 120.0,
        "prompt_version": "f06-v1",
        "prompt_sha256": SHA_B,
    }
    input_summary = {
        "input_id": "in-1",
        "kind": "decisions",
        "logical_name": "decisions/registry.json",
        "sha256": SHA_B,
        "byte_length": 100,
        "synthetic": True,
    }
    return {
        "Scope": scope,
        "EvidenceRef": evidence_ref,
        "SourceLocator": {
            "relative_path": "docs/AGENTS.md",
            "sha256": SHA_A,
            "line_start": 1,
            "line_end": 2,
        },
        "Provenance": provenance_project,
        "RunError": {
            "code": "warning_code",
            "path": None,
            "message": "missing material",
            "severity": "warning",
        },
        "AuditRequest": {
            "project_id": "project-1",
            "repo": "/tmp/project",
            "task_scope": scope,
            "as_of": TS,
            "semantic_mode": "off",
            "decision_registry": None,
            "harness_profile": None,
            "out": "/tmp/out",
        },
        "CodePointSpan": {"start": 0, "end": 5},
        "RuleSpan": {
            "rule_id": "rule-1",
            "source_ref": evidence_ref,
            "text": "规则一\r\n第二行",
            "scope": scope,
            "harness": "shared",
            "interpretation_status": "structured_confirmed",
            "confirmation_ref": None,
        },
        "Constraint": constraint,
        "BehaviorCheck": {
            "check_id": "check-1",
            "subject": "theme",
            "detector": "structured_check_result",
            "scope": scope,
            "parameters": {"expected": "dark"},
            "confirmation_ref": None,
        },
        "ApprovedItem": approved,
        "EditApproval": edit_approval,
        "Confirmation": {
            "confirmation_id": "c-1",
            "kind": "decision_approval",
            "source_ref": evidence_ref,
            "approved_items": [approved],
            "edit_approvals": [edit_approval],
        },
        "Decision": {
            "decision_id": "d-1",
            "status": "accepted",
            "scope": scope,
            "effective_from": TS,
            "effective_until": None,
            "supersedes": [],
            "source_ref": evidence_ref,
            "confirmation_ref": "c-1",
            "constraints": [constraint],
        },
        "Action": action,
        "Incident": incident,
        "LoadReceiptEntry": receipt_entry,
        "LoadReceipt": receipt,
        "Evidence": evidence,
        "CoverageItem": coverage,
        "Finding": finding,
        "BaseFile": base_file,
        "Edit": edit,
        "UnresolvedQuestion": question,
        "RepairProposal": proposal,
        "Mode": {"command": "audit", "semantic": "off"},
        "ModelSummary": model,
        "RunParameters": {"scope": scope, "model": model},
        "InputSummary": input_summary,
        "RunResult": {
            "run_id": "run-1",
            "project_id": "project-1",
            "mode": {"command": "audit", "semantic": "off"},
            "as_of": TS,
            "status": "complete",
            "coverage": [coverage],
            "findings": [finding],
            "proposals": [proposal],
            "artifacts": ["report.md"],
            "errors": [],
            "synthetic": False,
            "parameters": {"scope": scope, "model": model},
            "inputs": [input_summary],
            "project_revision": None,
        },
    }


def _incident() -> dict:
    return copy.deepcopy(_valid_samples()["Incident"])


def _receipt() -> dict:
    return copy.deepcopy(_valid_samples()["LoadReceipt"])


def _run_cli(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(SRC)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        [sys.executable, "-m", "context_manager", *args],
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
    )


def _pytest_subprocess(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(SRC)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        [sys.executable, "-m", "pytest", *args],
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
    )


def _assert_empty_dir(path: Path) -> None:
    assert list(path.iterdir()) == []


# F01-01
def test_f01_01_roundtrip() -> None:
    samples = _valid_samples()
    assert len(samples) >= 14
    for name, sample in samples.items():
        before = copy.deepcopy(sample)
        parsed = parse_object(name, sample)
        dumped = dump_object(parsed)
        assert dumped == sample, name
        assert sample == before, f"parse mutated input for {name}"
        parsed_again = parse_object(name, dumped)
        assert parsed_again == parsed, name
        assert dumps(parsed) == dumps(parsed_again)
        assert dumps(parsed).encode("utf-8").decode("utf-8") == dumps(parsed)

    minimal_scope = parse_object("Scope", {"include_prefixes": ["."]})
    assert dump_object(minimal_scope) == {
        "include_prefixes": ["."],
        "exclude_prefixes": [],
        "harnesses": [],
    }

    # R2: open-ended mappings are copied on parse, frozen, and dumped as new containers.
    behavior = copy.deepcopy(_valid_samples()["BehaviorCheck"])
    behavior["parameters"] = {"symbol": "USERS_PATH", "nested": {"items": [1]}}
    parsed_behavior = parse_object("BehaviorCheck", behavior)
    behavior["parameters"]["symbol"] = "CHANGED"
    behavior["parameters"]["nested"]["items"].append(2)
    assert dump_object(parsed_behavior)["parameters"] == {
        "symbol": "USERS_PATH",
        "nested": {"items": [1]},
    }
    with pytest.raises(TypeError):
        parsed_behavior.parameters["symbol"] = "CHANGED"  # type: ignore[index]
    with pytest.raises(TypeError):
        parsed_behavior.parameters["nested"]["items"][0] = 2  # type: ignore[index]

    constraint = copy.deepcopy(_valid_samples()["Constraint"])
    parsed_constraint = parse_object("Constraint", constraint)
    constraint["conditions"]["环境"] = "测试"
    assert parsed_constraint.conditions == {"环境": "生产"}
    with pytest.raises(TypeError):
        parsed_constraint.conditions["环境"] = "测试"  # type: ignore[index]

    action = copy.deepcopy(_valid_samples()["Action"])
    action["payload"] = {"nested": {"items": [1]}}
    parsed_action = parse_object("Action", action)
    action["payload"]["nested"]["items"].append(2)
    assert dump_object(parsed_action)["payload"] == {"nested": {"items": [1]}}
    with pytest.raises(TypeError):
        parsed_action.payload["nested"]["items"][0] = 2  # type: ignore[index]

    dumped_behavior = dump_object(parsed_behavior)
    dumped_behavior["parameters"]["nested"]["items"].append(3)
    assert dump_object(parsed_behavior)["parameters"]["nested"]["items"] == [1]


# F01-02
def test_f01_02_rejections_and_cli_exit_two(tmp_path: Path) -> None:
    missing = copy.deepcopy(_valid_samples()["EvidenceRef"])
    missing.pop("sha256")
    with pytest.raises(ContractError) as missing_error:
        parse_object("EvidenceRef", missing)
    assert missing_error.value.exit_code == 2
    assert missing_error.value.code == "contract_invalid"
    assert (missing_error.value.errors[0].path, missing_error.value.errors[0].code) == (
        "sha256",
        "missing_required",
    )

    wrong_type = copy.deepcopy(_valid_samples()["EvidenceRef"])
    wrong_type["line_start"] = "1"
    errors = validate_object("EvidenceRef", wrong_type)
    assert any(error.path == "line_start" and error.code == "wrong_type" for error in errors)

    unknown_enum = copy.deepcopy(_valid_samples()["Finding"])
    unknown_enum["kind"] = "not_a_finding_kind"
    errors = validate_object("Finding", unknown_enum)
    assert any(error.path == "kind" and error.code == "unknown_enum" for error in errors)

    unknown_field = copy.deepcopy(_valid_samples()["EvidenceRef"])
    unknown_field["unexpected"] = 1
    errors = validate_object("EvidenceRef", unknown_field)
    assert any(error.path == "unexpected" and error.code == "unknown_field" for error in errors)

    locator = copy.deepcopy(_valid_samples()["SourceLocator"])
    locator["evidence_id"] = "run-local-evidence"
    errors = validate_object("SourceLocator", locator)
    assert any(error.path == "evidence_id" and error.code == "unknown_field" for error in errors)

    bad_approval = copy.deepcopy(_valid_samples()["EditApproval"])
    bad_approval["line_start"] = 5
    bad_approval["line_end"] = 4
    errors = validate_object("EditApproval", bad_approval)
    assert any(error.path == "line_end" and error.code == "invalid_value" for error in errors)

    wrong_kind = copy.deepcopy(_valid_samples()["Confirmation"])
    wrong_kind["kind"] = "rule_interpretation"
    errors = validate_object("Confirmation", wrong_kind)
    assert any(error.path == "edit_approvals" and error.code == "invalid_value" for error in errors)

    document = tmp_path / "document.json"
    document.write_text(json.dumps({"schema_version": 2}), encoding="utf-8")
    with pytest.raises(ContractError) as version_error:
        load_document(document)
    assert version_error.value.errors[0].code == "unsupported_schema_version"
    assert version_error.value.errors[0].path == "schema_version"

    repo = tmp_path / "repo"
    repo.mkdir()
    out = tmp_path / "out"
    bad_time = _run_cli(
        ["audit", "--repo", str(repo), "--project-id", "p", "--out", str(out), "--as-of", "2026-11-04T00:00:00"],
        tmp_path,
    )
    assert bad_time.returncode == 2
    assert bad_time.stdout == ""
    assert "invalid_format" in bad_time.stderr
    assert not out.exists()

    bad_decisions = _run_cli(
        [
            "audit",
            "--repo",
            str(repo),
            "--project-id",
            "p",
            "--out",
            str(out),
            "--decisions",
            str(document),
        ],
        tmp_path,
    )
    assert bad_decisions.returncode == 2
    assert bad_decisions.stdout == ""
    assert "unsupported_schema_version" in bad_decisions.stderr
    assert not out.exists()

    # R1: a structurally valid empty receipt passes; F05 verifies effective coverage.
    empty_receipt = _receipt()
    empty_receipt["entries"] = []
    empty_receipt["coverage"] = "complete"
    empty_receipt["channels_covered"] = [
        "project_instructions",
        "imported_instructions",
        "rule_directories",
        "session_injection",
    ]
    parsed_empty = parse_object("LoadReceipt", empty_receipt)
    assert parsed_empty.entries == ()
    assert dump_object(parsed_empty)["entries"] == []

    bad_channel = _receipt()
    bad_channel["entries"][0]["channel"] = "NOT A CHANNEL"
    errors = validate_object("LoadReceipt", bad_channel)
    assert any(
        error.path == "entries[0].channel" and error.code == "invalid_format"
        for error in errors
    )

    empty_with_unknown = copy.deepcopy(empty_receipt)
    empty_with_unknown["unexpected"] = True
    errors = validate_object("LoadReceipt", empty_with_unknown)
    assert any(error.path == "unexpected" and error.code == "unknown_field" for error in errors)


# F01-03
def test_f01_03_unicode_empty_path_timezone_and_lines() -> None:
    finding = copy.deepcopy(_valid_samples()["Finding"])
    finding["claim"] = "中文声明：规则与代码不一致。"
    finding["support_refs"][0]["evidence_id"] = "证据-1"
    parsed = parse_object("Finding", finding)
    assert parsed.claim.startswith("中文声明")
    assert parsed.support_refs[0].evidence_id == "证据-1"

    parsed_path = parse_object(
        "SourceLocator",
        {
            "relative_path": "中文目录/规则.md",
            "sha256": SHA_A,
            "line_start": 1,
            "line_end": 2,
        },
    )
    assert parsed_path.relative_path == "中文目录/规则.md"

    errors = validate_object("Scope", {"include_prefixes": [""]})
    assert any(error.path == "include_prefixes[0]" and error.code == "empty_path" for error in errors)

    request = copy.deepcopy(_valid_samples()["AuditRequest"])
    request["as_of"] = "2026-11-04T00:00:00"
    errors = validate_object("AuditRequest", request)
    assert any(error.path == "as_of" and error.code == "invalid_format" for error in errors)

    bad_lines = copy.deepcopy(_valid_samples()["EvidenceRef"])
    bad_lines["line_end"] = 1
    bad_lines["line_start"] = 2
    errors = validate_object("EvidenceRef", bad_lines)
    assert any(error.path == "line_end" and error.code == "invalid_value" for error in errors)


# F01-04
def test_f01_04_help_version_and_missing_command(tmp_path: Path) -> None:
    help_result = _run_cli(["--help"], tmp_path)
    assert help_result.returncode == 0
    assert "audit" in help_result.stdout
    assert "diagnose" in help_result.stdout
    _assert_empty_dir(tmp_path)

    version_result = _run_cli(["--version"], tmp_path)
    assert version_result.returncode == 0
    lines = version_result.stdout.splitlines()
    assert len(lines) == 1
    assert re.fullmatch(r"context-manager \S+ \(schema_version 1\)", lines[0])
    _assert_empty_dir(tmp_path)

    no_args = _run_cli([], tmp_path)
    assert no_args.returncode == 2
    assert no_args.stdout == ""
    _assert_empty_dir(tmp_path)


# F01-05
def test_f01_05_integrated_pipeline_and_invalid_input(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    out = tmp_path / "out"
    case = tmp_path / "case.json"
    case.write_text("{}", encoding="utf-8")

    audit = _run_cli(
        ["audit", "--repo", str(repo), "--project-id", "p", "--out", str(out)],
        tmp_path,
    )
    assert audit.returncode == 3
    assert "status: partial" in audit.stdout
    assert "not_implemented" not in audit.stderr
    reports = list(out.glob("*/report.md"))
    assert len(reports) == 1
    before = {p: p.read_bytes() for p in out.rglob("*") if p.is_file()}

    diagnose = _run_cli(
        [
            "diagnose",
            "--repo",
            str(repo),
            "--project-id",
            "p",
            "--out",
            str(out),
            "--case",
            str(case),
        ],
        tmp_path,
    )
    assert diagnose.returncode == 2
    assert diagnose.stdout == ""
    assert "not_synthetic" in diagnose.stderr
    assert before == {p: p.read_bytes() for p in out.rglob("*") if p.is_file()}


# F01-06
def test_f01_06_pytest_configuration(
    request: pytest.FixtureRequest, repo_root: Path, tmp_path: Path
) -> None:
    markers = "\n".join(str(item) for item in request.config.getini("markers"))
    assert "local_model" in markers
    addopts = request.config.getini("addopts")
    addopts_text = " ".join(addopts) if isinstance(addopts, (list, tuple)) else str(addopts)
    assert "--strict-markers" in addopts_text

    collected = _pytest_subprocess(
        [
            "tests/test_f01_foundation.py",
            "--collect-only",
            "-q",
            "-m",
            "not local_model",
            "-p",
            "no:cacheprovider",
        ],
        repo_root,
    )
    assert collected.returncode == 0, collected.stdout + collected.stderr
    assert "test_f01_06_local_model_gate" not in collected.stdout

    gated = _pytest_subprocess(
        [
            "tests/test_f01_foundation.py::test_f01_06_local_model_gate",
            "-q",
            "-p",
            "no:cacheprovider",
        ],
        repo_root,
    )
    output = gated.stdout + gated.stderr
    assert gated.returncode == 0, output
    assert "local_model_test: not_run" in output
    assert "--local-model-config" in output

    invalid_config = tmp_path / "bad-model.toml"
    invalid_config.write_text("not valid = [", encoding="utf-8")
    invalid = _pytest_subprocess(
        [
            "tests/test_f01_foundation.py::test_f01_06_local_model_gate",
            "-q",
            "-p",
            "no:cacheprovider",
            f"--local-model-config={invalid_config}",
        ],
        repo_root,
    )
    invalid_output = invalid.stdout + invalid.stderr
    assert invalid.returncode != 0, invalid_output
    assert "invalid TOML" in invalid_output

    missing_config = tmp_path / "missing-model.toml"
    missing = _pytest_subprocess(
        [
            "tests/test_f01_foundation.py::test_f01_06_local_model_gate",
            "-q",
            "-p",
            "no:cacheprovider",
            f"--local-model-config={missing_config}",
        ],
        repo_root,
    )
    missing_output = missing.stdout + missing.stderr
    assert missing.returncode != 0, missing_output
    assert "config file not found" in missing_output

    valid_config = tmp_path / "model.toml"
    valid_config.write_text('name = "local-model"\n', encoding="utf-8")
    valid = _pytest_subprocess(
        [
            "tests/test_f01_foundation.py::test_f01_06_local_model_gate",
            "-q",
            "-p",
            "no:cacheprovider",
            f"--local-model-config={valid_config}",
        ],
        repo_root,
    )
    valid_output = valid.stdout + valid.stderr
    assert valid.returncode == 0, valid_output
    assert "1 passed" in valid_output


@pytest.mark.local_model
def test_f01_06_local_model_gate(local_model_config: dict) -> None:
    # This probe exists to make the marker and fixture gate observable.  It is
    # skipped by default and never initiates a model call in F01.
    assert isinstance(local_model_config, dict)


# F01-07
def test_f01_07_nested_paths_order_and_truncation() -> None:
    valid = _incident()
    invalid = copy.deepcopy(valid)
    invalid["event_id"] = "e-2"
    invalid["occurred_at"] = "2026-11-04"
    parsed, errors = parse_list("Incident", [valid, invalid], path_prefix="incidents")
    assert len(parsed) == 1
    assert any(
        error.path == "incidents[1].occurred_at" and error.code == "invalid_format"
        for error in errors
    )

    receipt = _receipt()
    while len(receipt["entries"]) < 3:
        receipt["entries"].append(copy.deepcopy(receipt["entries"][0]))
    receipt["entries"][2]["source_ref"]["line_start"] = 3
    receipt["entries"][2]["source_ref"]["line_end"] = 1
    parsed_receipts, errors = parse_list("LoadReceipt", [receipt], path_prefix="receipts")
    assert parsed_receipts == []
    assert any(
        error.path == "receipts[0].entries[2].source_ref.line_end"
        and error.code == "invalid_value"
        for error in errors
    )

    confirmation = copy.deepcopy(_valid_samples()["Confirmation"])
    confirmation["edit_approvals"][0]["line_start"] = 9
    confirmation["edit_approvals"][0]["line_end"] = 2
    errors = validate_object("Confirmation", confirmation)
    assert any(
        error.path == "edit_approvals[0].line_end" and error.code == "invalid_value"
        for error in errors
    )

    multi = _incident()
    multi["occurred_at"] = "bad"
    multi["harness"] = "BAD"
    multi["action"]["kind"] = "not_an_action"
    first = validate_object("Incident", multi)
    second = validate_object("Incident", multi)
    assert [(e.path, e.code) for e in first] == [(e.path, e.code) for e in second]
    assert len(first) >= 3

    truncated = validate_object("Scope", {"include_prefixes": [""] * 21})
    assert len(truncated) == 21
    assert all(error.code == "empty_path" for error in truncated[:20])
    assert truncated[-1].path == "$"
    assert truncated[-1].code == "too_many_errors"


# F01-08
def test_f01_08_sentinel_never_echoed(tmp_path: Path) -> None:
    sentinel = "SENTINEL_NEVER_ECHO_7f3a"
    request = copy.deepcopy(_valid_samples()["AuditRequest"])
    request["semantic_mode"] = sentinel
    with pytest.raises(ContractError) as caught:
        parse_object("AuditRequest", request)
    payload = json.dumps(caught.value.to_dict(), ensure_ascii=False)
    messages = " ".join(error.message for error in caught.value.errors)
    assert sentinel not in payload
    assert sentinel not in messages
    assert sentinel not in repr(caught.value)

    repo = tmp_path / "repo"
    repo.mkdir()
    out = tmp_path / "out"
    cli = _run_cli(
        ["audit", "--repo", str(repo), "--project-id", "p", "--out", str(out), "--as-of", sentinel],
        tmp_path,
    )
    assert cli.returncode == 2
    assert sentinel not in cli.stdout
    assert sentinel not in cli.stderr
    assert not out.exists()

    decisions = tmp_path / "bad.json"
    decisions.write_text('{"schema_version": 1, "value": "' + sentinel, encoding="utf-8")
    cli = _run_cli(
        [
            "audit",
            "--repo",
            str(repo),
            "--project-id",
            "p",
            "--out",
            str(out),
            "--decisions",
            str(decisions),
        ],
        tmp_path,
    )
    assert cli.returncode == 2
    assert sentinel not in cli.stdout
    assert sentinel not in cli.stderr

    # R4: argparse errors are restricted and never repeat invalid values.
    invalid_enum = _run_cli(
        [
            "audit",
            "--repo",
            str(repo),
            "--project-id",
            "p",
            "--out",
            str(out),
            "--semantic",
            sentinel,
        ],
        tmp_path,
    )
    assert invalid_enum.returncode == 2
    assert invalid_enum.stdout == ""
    assert sentinel not in invalid_enum.stderr
    assert "--semantic" in invalid_enum.stderr

    unknown_argument = _run_cli(
        [
            "audit",
            "--repo",
            str(repo),
            "--project-id",
            "p",
            "--out",
            str(out),
            f"--{sentinel}",
            sentinel,
        ],
        tmp_path,
    )
    assert unknown_argument.returncode == 2
    assert unknown_argument.stdout == ""
    assert sentinel not in unknown_argument.stderr
    assert "unrecognized arguments" in unknown_argument.stderr

    invalid_command = _run_cli([sentinel], tmp_path)
    assert invalid_command.returncode == 2
    assert invalid_command.stdout == ""
    assert sentinel not in invalid_command.stderr
    assert "COMMAND" in invalid_command.stderr
    assert not out.exists()


# F01-09
def test_f01_09_path_normalisation_and_rejection() -> None:
    assert normalize_scope_prefix("./clients/") == "clients"
    assert normalize_scope_prefix("clients/") == "clients"
    assert normalize_scope_prefix(".") == "."
    assert normalize_scope_prefix("./中文/规则/") == "中文/规则"
    assert normalize_relative_path("中文/规则.md") == "中文/规则.md"

    for invalid in ["", "clients//x", "..", "../x", "/x", "C:\\x", "a\\b", "a//b", "a/./b"]:
        with pytest.raises(ValueError):
            normalize_scope_prefix(invalid)

    for invalid in ["", "clients//x", "..", "../x", "/x", "C:\\x", "a\\b", "a/", "./a"]:
        with pytest.raises(ValueError):
            normalize_relative_path(invalid)

    for invalid in ["C:/x", "a//b"]:
        with pytest.raises(ValueError):
            normalize_scope_prefix(invalid)


# F01-10
def test_f01_10_request_resolution_and_argument_combinations(tmp_path: Path) -> None:
    repo = tmp_path / "project"
    repo.mkdir()
    out = tmp_path / "out"
    decisions = tmp_path / "decisions.json"
    decisions.write_text(json.dumps({"schema_version": 1}), encoding="utf-8")
    case = tmp_path / "case.json"
    case.write_text("{}", encoding="utf-8")

    namespace = SimpleNamespace(
        command="diagnose",
        repo="project",
        project_id="p-1",
        scope=["clients", "clients/", "."],
        as_of="2026-11-04T08:00:00+08:00",
        decisions="decisions.json",
        case="case.json",
        semantic="off",
        model_config=None,
        out="out",
    )
    request = build_request(namespace, tmp_path)
    assert request.repo == str(repo.resolve())
    assert request.decision_registry == str(decisions.resolve())
    assert request.out == str(out.resolve())
    assert request.task_scope.include_prefixes == ("clients", "clients", ".")
    assert request.as_of == "2026-11-04T00:00:00Z"
    assert request.semantic_mode.value == "off"

    inside = SimpleNamespace(**{**namespace.__dict__, "out": "project/out"})
    with pytest.raises(ContractError) as inside_error:
        build_request(inside, tmp_path)
    assert inside_error.value.code == "out_inside_repo"

    parent = SimpleNamespace(**{**namespace.__dict__, "out": "."})
    with pytest.raises(ContractError) as parent_error:
        build_request(parent, tmp_path)
    assert parent_error.value.code == "out_inside_repo"

    local_without_config = SimpleNamespace(
        **{**namespace.__dict__, "semantic": "local", "model_config": None}
    )
    with pytest.raises(ContractError) as local_error:
        build_request(local_without_config, tmp_path)
    assert local_error.value.code == "invalid_argument"
    assert local_error.value.errors[0].path == "model_config"

    missing_case = _run_cli(
        [
            "diagnose",
            "--repo",
            str(repo),
            "--project-id",
            "p",
            "--out",
            str(out),
        ],
        tmp_path,
    )
    assert missing_case.returncode == 2
    assert missing_case.stdout == ""
    assert "--case" in missing_case.stderr

    audit_with_case = _run_cli(
        [
            "audit",
            "--repo",
            str(repo),
            "--project-id",
            "p",
            "--out",
            str(out),
            "--case",
            str(case),
        ],
        tmp_path,
    )
    assert audit_with_case.returncode == 2
    assert audit_with_case.stdout == ""
    assert "--case" in audit_with_case.stderr


# F01-11
def test_f01_11_json_input_rules(tmp_path: Path) -> None:
    def write(name: str, payload: bytes | str) -> Path:
        path = tmp_path / name
        if isinstance(payload, str):
            path.write_text(payload, encoding="utf-8")
        else:
            path.write_bytes(payload)
        return path

    with pytest.raises(ContractError) as duplicate:
        load_json(write("duplicate.json", '{"schema_version": 1, "schema_version": 1}'))
    assert duplicate.value.code == "duplicate_key"
    assert duplicate.value.errors[0].code == "duplicate_key"

    with pytest.raises(ContractError) as nan:
        load_json(write("nan.json", '{"value": NaN}'))
    assert nan.value.code == "invalid_json"

    with pytest.raises(ContractError) as empty:
        load_json(write("empty.json", b""))
    assert empty.value.code == "invalid_json"

    with pytest.raises(ContractError) as too_large:
        load_json(write("large.json", '{"value": "' + "x" * 50 + '"}'), max_bytes=10)
    assert too_large.value.code == "input_too_large"

    with pytest.raises(ContractError) as bad_encoding:
        load_json(write("latin1.json", b"\xff\xfe"))
    assert bad_encoding.value.code == "invalid_encoding"

    bom = write("bom.json", b"\xef\xbb\xbf" + json.dumps({"schema_version": 1}).encode())
    assert load_document(bom) == {"schema_version": 1}

    with pytest.raises(ContractError) as not_object:
        load_document(write("array.json", "[]"))
    assert not_object.value.errors[0].path == "$"
    assert not_object.value.errors[0].code == "wrong_type"
    root_errors = validate_object("Finding", [])
    assert root_errors[0].path == "$"
    assert root_errors[0].code == "wrong_type"

    with pytest.raises(ContractError) as missing_version:
        load_document(write("missing-version.json", "{}"))
    assert missing_version.value.errors[0].path == "schema_version"
    assert missing_version.value.errors[0].code == "missing_required"

    with pytest.raises(ContractError) as string_version:
        load_document(write("string-version.json", '{"schema_version": "1"}'))
    assert string_version.value.errors[0].code == "wrong_type"

    with pytest.raises(ContractError) as version_two:
        load_document(write("version-2.json", '{"schema_version": 2}'))
    assert version_two.value.errors[0].code == "unsupported_schema_version"

    sentinel = "SENTINEL_BODY_9c1e"
    with pytest.raises(ContractError) as syntax:
        load_json(write("syntax.json", '{"value": "' + sentinel))
    assert sentinel not in " ".join(error.message for error in syntax.value.errors)
    assert sentinel not in json.dumps(syntax.value.to_dict())

    unknown_extra = copy.deepcopy(_valid_samples()["AuditRequest"])
    unknown_extra["unexpected"] = "value"
    errors = validate_object("AuditRequest", unknown_extra)
    assert any(error.path == "unexpected" and error.code == "unknown_field" for error in errors)

    # R3: parser depth, integer length and numeric conversion errors stay structured.
    deep = write("deep.json", '{"schema_version": 1, "data": ' + "[" * 1200 + "0" + "]" * 1200 + "}")
    with pytest.raises(ContractError) as deep_error:
        load_json(deep)
    assert deep_error.value.code == "invalid_json"
    assert deep_error.value.errors[0].code == "invalid_json"
    assert deep_error.value.exit_code == 2

    huge_integer = write(
        "huge-integer.json", '{"schema_version": 1, "data": ' + "9" * 5000 + "}"
    )
    with pytest.raises(ContractError) as huge_error:
        load_json(huge_integer)
    assert huge_error.value.code == "invalid_json"
    assert huge_error.value.errors[0].code == "invalid_json"

    huge_number = copy.deepcopy(_valid_samples()["ModelSummary"])
    huge_number["temperature"] = 10 ** 400
    number_errors = validate_object("ModelSummary", huge_number)
    assert any(
        error.path == "temperature" and error.code == "invalid_value"
        for error in number_errors
    )
    with pytest.raises(ContractError) as number_error:
        parse_object("ModelSummary", huge_number)
    assert number_error.value.exit_code == 2
    assert number_error.value.code == "contract_invalid"
    assert [error.path for error in number_error.value.errors] == ["temperature"]

    repo = tmp_path / "repo"
    repo.mkdir()
    out = tmp_path / "out"
    for label, document in (("deep", deep), ("huge-integer", huge_integer)):
        cli = _run_cli(
            [
                "audit",
                "--repo",
                str(repo),
                "--project-id",
                "p",
                "--out",
                str(out),
                "--decisions",
                str(document),
            ],
            tmp_path,
        )
        assert cli.returncode == 2, label
        assert cli.stdout == "", label
        assert "invalid_json" in cli.stderr, label
        assert "Traceback" not in cli.stderr, label
    assert not out.exists()


# F01-12
def test_f01_12_package_entry_and_version(tmp_path: Path) -> None:
    imported = importlib.import_module("context_manager")
    assert re.fullmatch(r"\d+\.\d+\.\d+", imported.__version__)
    try:
        installed_version = importlib.metadata.version("context-manager")
    except importlib.metadata.PackageNotFoundError:
        installed_version = None
    if installed_version is not None:
        assert installed_version == imported.__version__

    result = _run_cli(["--version"], tmp_path)
    assert result.returncode == 0
    assert imported.__version__ in result.stdout
    assert "schema_version 1" in result.stdout

    compile_result = subprocess.run(
        [sys.executable, "-m", "compileall", "-q", str(SRC / "context_manager")],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )
    assert compile_result.returncode == 0, compile_result.stdout + compile_result.stderr

    env = os.environ.copy()
    env["PYTHONPATH"] = str(SRC)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    import_result = subprocess.run(
        [sys.executable, "-c", "import context_manager"],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
    )
    assert import_result.returncode == 0, import_result.stdout + import_result.stderr
    _assert_empty_dir(tmp_path)
