"""F05: synthetic historical actions, receipt proofs and independent tasks."""
from contextlib import closing
from dataclasses import replace
import copy
import json

import pytest

from acceptance_support import digest, scenario, tree_bytes, update_json, write_json
from context_manager.contracts import ContractError, dump_object
from context_manager.evidence import EvidenceError, prepare_selected_input
from context_manager.incidents import parse_incident_input
from context_manager.pipeline import run_diagnose


def result_for(tmp_path, edit=None, registry_edit=None):
    request, project, case, registry = scenario(tmp_path)
    if edit:
        update_json(case, edit)
    if registry_edit:
        update_json(registry, registry_edit)
    return run_diagnose(request, case)


def test_f05_01_two_tasks_recur(tmp_path):
    result = result_for(tmp_path)
    analysis = result.incident_analysis
    assert analysis.counts["confirmed_violation_actions"] == 2
    assert analysis.recurrence[0]["confirmed_independent_task_count"] == 2
    assert analysis.recurrence[0]["meets_recurrence_threshold"]
    assert all(b["evidence_refs"] for b in analysis.behaviors)


def test_f05_02_retries_duplicates_and_cross_session_task(tmp_path):
    def edit(data):
        data["incidents"][1]["task_id"] = data["incidents"][0]["task_id"]
        data["incidents"].append(copy.deepcopy(data["incidents"][0]))
    result = result_for(tmp_path, edit)
    assert result.incident_analysis.dedup["duplicate_event_count"] == 1
    assert result.incident_analysis.counts["confirmed_independent_tasks"] == 1
    assert not any(f["kind"] == "recurrence" for f in result.diagnosis.findings)


@pytest.mark.parametrize("kind", ["mention", "tool_result", "fixed", "similar_scope"])
def test_f05_03_non_actions_and_fixes_never_count(tmp_path, kind):
    def edit(data):
        data["receipts"] = []
        for event in data["incidents"]:
            if kind in ("mention", "tool_result"):
                event["action"]["kind"] = kind
            elif kind == "similar_scope":
                event["action"]["target_path"] = "clients-old/app.py"
            else:
                file = next(f for f in data["files"] if f["file_ref"] == event["event_id"])
                file["content"] = 'ENDPOINT = "/api/v2/users" # /api/v1/users\n'
    result = result_for(tmp_path, edit)
    assert result.incident_analysis.counts["confirmed_violation_actions"] == 0
    assert not any(f["kind"] == "recurrence" for f in result.diagnosis.findings)


def test_f05_04_explicit_exception_preserves_compatibility(tmp_path):
    request, project, case, registry = scenario(tmp_path)
    line = "- Compatibility clients keep /api/v1/users."
    content = (project / "AGENTS.md").read_text() + line + "\n"
    (project / "AGENTS.md").write_text(content)
    def registry_edit(data):
        for holder in (data["rules"][0], data["confirmations"][0], data["confirmations"][0]["approved_items"][0]):
            holder["source_ref"]["sha256"] = digest(content.encode())
        ref = {"relative_path": "AGENTS.md", "sha256": digest(content.encode()), "line_start": 4, "line_end": 4}
        rule = {"rule_id": "compatibility", "text": line, "text_sha256": digest((line + "\n").encode()), "source_ref": ref, "scope": {"include_prefixes": ["clients/legacy"]}, "harness": "codex", "interpretation_status": "structured_confirmed", "confirmation_ref": "confirm-compatibility", "constraints": [{"constraint_id": "compat-endpoint", "subject": "endpoint", "operator": "equals", "value": "/api/v1/users", "conditions": {}, "span": {"start": 2, "end": len(line)}}]}
        data["rules"].append(rule)
        data["confirmations"].append({"confirmation_id": "confirm-compatibility", "kind": "rule_interpretation", "source_ref": ref, "approved_items": [{"item_id": "compatibility", "source_ref": ref}]})
    update_json(registry, registry_edit)
    def edit(data):
        data["files"][0]["content"] = content
        for event in data["incidents"]:
            path = "clients/legacy/" + event["event_id"] + ".py"
            event.update(exception_rule_ids=["compatibility"])
            event["action"]["target_path"] = path
            next(f for f in data["files"] if f["file_ref"] == event["event_id"])["path"] = path
    update_json(case, edit)
    result = run_diagnose(request, case)
    assert result.incident_analysis.counts["confirmed_violation_actions"] == 0
    assert all(b["status"] == "exception" for b in result.incident_analysis.behaviors)


def test_f05_04_superseded_main_rule_is_not_an_exception(tmp_path):
    result = result_for(tmp_path, lambda data: [event.update(exception_rule_ids=["rule-v1"]) for event in data["incidents"]])
    assert result.incident_analysis.counts["confirmed_violation_actions"] == 2


def test_f05_05_matching_receipt_text_is_load_proof(tmp_path):
    result = result_for(tmp_path)
    assert all(l["state"] == "observed_loaded" and l["load_proof"] == "matched_entry" for l in result.incident_analysis.loads)
    assert all(l["synthetic"] and l["proof_scope"] == "synthetic_case" for l in result.incident_analysis.loads)


@pytest.mark.parametrize("variant", ["digest", "session", "epoch", "after"])
def test_f05_06_bad_receipts_are_unknown(tmp_path, variant):
    def edit(data):
        for receipt in data["receipts"]:
            if variant == "digest":
                receipt["entries"][0]["text_sha256"] = "f" * 64
            elif variant == "session":
                receipt["session_id"] = "wrong-session"
            elif variant == "epoch":
                receipt["context_epoch"] = "wrong-epoch"
            else:
                receipt["captured_at"] = "2027-01-01T00:00:00Z"
    result = result_for(tmp_path, edit)
    counts = result.incident_analysis.counts
    assert counts["raw_event_count"] == counts["canonical_event_count"] + counts["duplicate_event_count"] + counts["conflicted_event_count"]
    assert all(l["state"] == "unknown" for l in result.incident_analysis.loads)
    assert result.artifacts.exit_code == 3


@pytest.mark.parametrize("coverage,channels,expected", [("complete", True, "observed_not_loaded"), ("partial", True, "unknown"), ("complete", False, "unknown")])
def test_f05_07_only_complete_channels_prove_absence(tmp_path, coverage, channels, expected):
    def edit(data):
        for receipt in data["receipts"]:
            receipt.update(entries=[], coverage=coverage)
            if not channels:
                receipt["channels_covered"] = ["project_instructions"]
    result = result_for(tmp_path, edit)
    assert all(l["state"] == expected for l in result.incident_analysis.loads)


@pytest.mark.parametrize("field", ["task_id", "occurred_at", "after_ref"])
def test_f05_08_missing_metadata_is_not_invented(tmp_path, field):
    def edit(data):
        for event in data["incidents"]:
            (event["action"] if field == "after_ref" else event).pop(field, None)
    result = result_for(tmp_path, edit)
    assert not any(f["kind"] == "recurrence" for f in result.diagnosis.findings)
    if field != "task_id":
        assert result.artifacts.exit_code == 3
        assert all(b["status"] == "unknown" for b in result.incident_analysis.behaviors)
    else:
        assert all(g["task_identity_complete"] is False for g in result.incident_analysis.recurrence)


def test_f05_09_legacy_never_becomes_verified(tmp_path):
    from pathlib import Path
    request, _, _, _ = scenario(tmp_path)
    source = Path(__file__).resolve().parents[1] / "examples/stale-users-api/case.json"
    before = source.read_bytes()
    result = run_diagnose(request, source)
    assert result.incident_analysis.package_format == "legacy-demo-v0"
    assert not result.incident_analysis.receipts and not result.incident_analysis.loads
    assert source.read_bytes() == before and result.artifacts.exit_code == 3


def test_f05_10_ast_beats_comments_and_diff_claims(tmp_path):
    def edit(data):
        for f in data["files"]:
            if f["kind"] == "code":
                f["content"] = '# ENDPOINT = "/api/v1/users"\nENDPOINT = "/api/v2/users"\n'
    result = result_for(tmp_path, edit)
    assert all(b["status"] == "no_violation" for b in result.incident_analysis.behaviors)


def test_f05_11_input_size_symlink_and_directory(tmp_path):
    source = tmp_path / "source"
    source.write_bytes(b" " * (10 * 1024 * 1024 + 1))
    with pytest.raises(EvidenceError) as error:
        prepare_selected_input(source)
    assert error.value.code == "evidence_too_large"
    with pytest.raises(EvidenceError):
        prepare_selected_input(tmp_path)
    link = tmp_path / "link"
    link.symlink_to(source)
    with pytest.raises(EvidenceError):
        prepare_selected_input(link)


@pytest.mark.parametrize("mutation,code", [(lambda d: d.update(synthetic=False), "not_synthetic"), (lambda d: d.update(schema_version=2), "unsupported_schema_version"), (lambda d: d.update(project_id="other"), "project_id_mismatch"), (lambda d: d["files"][0].update(path="../secret"), "unsafe_path")])
def test_f05_12_bad_package_rejected_before_output(tmp_path, mutation, code):
    request, _, case, _ = scenario(tmp_path)
    data = json.loads(case.read_text())
    mutation(data)
    with pytest.raises(ContractError) as error:
        parse_incident_input(json.dumps(data).encode(), project_id=request.project_id)
    assert error.value.code == code
    assert not __import__("pathlib").Path(request.out).exists()


@pytest.mark.parametrize("variant", ["same_receipt", "conflicting_receipt_id", "conflicting_event", "conflicting_receipts"])
def test_f05_13_duplicates_and_conflicts(tmp_path, variant):
    def edit(data):
        if variant == "conflicting_event":
            duplicate = copy.deepcopy(data["incidents"][0])
            duplicate["task_id"] = "other-task"
            data["incidents"].append(duplicate)
        else:
            duplicate = copy.deepcopy(data["receipts"][0])
            if variant != "same_receipt":
                duplicate["entries"] = []
            if variant == "conflicting_receipts":
                duplicate["receipt_id"] = "additional-receipt"
            data["receipts"].append(duplicate)
    result = result_for(tmp_path, edit)
    if variant == "same_receipt":
        assert len(result.incident_analysis.receipts) == 2
    else:
        assert not any(l["state"] != "unknown" for l in result.incident_analysis.loads if l["event_id"] == "event-1")
        assert not any(f["kind"] == "recurrence" for f in result.diagnosis.findings) if variant == "conflicting_event" else True


@pytest.mark.parametrize("status,expected", [("passed", "no_violation"), ("failed", "violation"), ("error", "unknown")])
def test_f05_14_structured_checks_match_actual_inputs(tmp_path, status, expected):
    def registry_edit(data):
        data["behavior_checks"][0].update(detector="structured_check_result", parameters={"decision_id": "decision-v2", "result_check_id": "external"})
    def edit(data):
        data["check_results"] = [{"check_id": "external", "event_id": e["event_id"], "input_sha256": digest(next(f["content"] for f in data["files"] if f["file_ref"] == e["event_id"]).encode()), "status": status} for e in data["incidents"]]
    result = result_for(tmp_path, edit, registry_edit)
    assert all(b["status"] == expected for b in result.incident_analysis.behaviors)


def test_f05_15_input_order_does_not_change_semantics(tmp_path):
    request, _, case, _ = scenario(tmp_path)
    first = run_diagnose(request, case)
    update_json(case, lambda d: (d["incidents"].reverse(), d["receipts"].reverse()))
    second = run_diagnose(request, case)
    for field in ("counts", "dedup"):
        assert dump_object(getattr(first.incident_analysis, field)) == dump_object(getattr(second.incident_analysis, field))
    assert [g["group_id"] for g in first.incident_analysis.recurrence] == [g["group_id"] for g in second.incident_analysis.recurrence]
