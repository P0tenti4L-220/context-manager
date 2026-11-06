"""Small acceptance smoke set for the newly connected formal workflow.

The full per-feature matrices remain deferred. These exercise real components,
real temporary Git application and the command-line entry, using synthetic data.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from context_manager.contracts import build_request, dump_object
from context_manager.evidence import EvidenceStore
from context_manager.pipeline import run_audit, run_diagnose
from context_manager.reporting import ArtifactCollisionError


def digest(data):
    return hashlib.sha256(data).hexdigest()


def scenario(tmp_path, *, approval=True, receipts=True, same_task=False):
    project = tmp_path / "project"
    project.mkdir()
    (project / "clients").mkdir()
    (project / "docs").mkdir()
    line = "- New clients must use `/api/v1/users`; keep tests."
    text = "# Rules\n\n" + line + "\n"
    (project / "AGENTS.md").write_text(text)
    adr = "# Approved decision\n\nNew clients must use /api/v2/users.\n"
    (project / "docs/decision.md").write_text(adr)
    scope = {"include_prefixes": ["clients"], "exclude_prefixes": []}
    rule_ref = {"relative_path": "AGENTS.md", "sha256": digest(text.encode()), "line_start": 3, "line_end": 3}
    decision_ref = {"relative_path": "docs/decision.md", "sha256": digest(adr.encode()), "line_start": 3, "line_end": 3}
    registry = {
        "schema_version": 1, "synthetic": True, "project_id": "synthetic",
        "rules": [{"rule_id": "rule-v1", "source_ref": rule_ref, "text": line, "text_sha256": digest((line + "\n").encode()), "scope": scope, "harness": "codex", "interpretation_status": "structured_confirmed", "confirmation_ref": "confirm-rule", "constraints": [
            {"constraint_id": "endpoint", "subject": "endpoint", "operator": "equals", "value": "/api/v1/users", "conditions": {}, "span": {"start": 2, "end": line.index(";")}},
            {"constraint_id": "tests", "subject": "test_required", "operator": "equals", "value": True, "conditions": {}, "span": {"start": line.index("keep"), "end": len(line)}}]}],
        "decisions": [{"decision_id": "decision-v2", "status": "accepted", "scope": scope, "effective_from": "2026-10-01T00:00:00Z", "effective_until": None, "supersedes": ["rule-v1"], "source_ref": decision_ref, "confirmation_ref": "confirm-decision", "constraints": [{"constraint_id": "endpoint-v2", "subject": "endpoint", "operator": "equals", "value": "/api/v2/users", "conditions": {}}]}],
        "confirmations": [
            {"confirmation_id": "confirm-rule", "kind": "rule_interpretation", "source_ref": rule_ref, "approved_items": [{"item_id": "rule-v1", "source_ref": rule_ref}]},
            {"confirmation_id": "confirm-decision", "kind": "decision_approval", "source_ref": decision_ref, "approved_items": [{"item_id": "decision-v2", "source_ref": decision_ref}], "edit_approvals": [{"decision_id": "decision-v2", "target_path": "AGENTS.md", "operation": "replace_span", "line_start": 3, "line_end": 3, "old_text_sha256": digest((line + "\n").encode()), "replacement_sha256": digest((line.replace("/api/v1/users", "/api/v2/users") + "\n").encode()), "rule_ids": ["rule-v1"]}] if approval else []},
            {"confirmation_id": "confirm-check", "kind": "check_configuration", "source_ref": decision_ref, "approved_items": [{"item_id": "check-endpoint", "source_ref": decision_ref}]},
        ],
        "behavior_checks": [{"check_id": "check-endpoint", "subject": "endpoint", "detector": "python_assignment_literal", "scope": scope, "parameters": {"symbol": "ENDPOINT", "decision_id": "decision-v2"}, "confirmation_ref": "confirm-check"}],
    }
    registry_path = tmp_path / "decisions.json"
    registry_path.write_text(json.dumps(registry))
    channels = ["project_instructions", "imported_instructions", "rule_directories", "session_injection"]
    package = {"format": "context-manager-incidents", "schema_version": 1, "synthetic": True, "project_id": "synthetic", "task_scope": ["clients"], "channel_profile": {"profile_id": "synthetic", "profile_version": "1", "source": "synthetic", "channels": {"codex": channels}}, "files": [{"file_ref": "rules", "path": "AGENTS.md", "revision": "before", "kind": "rule", "content": text}], "incidents": [], "receipts": []}
    for index in (1, 2):
        path, eid = f"clients/client{index}.py", f"event-{index}"
        content = 'ENDPOINT = "/api/v1/users"\n'
        package["files"].append({"file_ref": eid, "path": path, "revision": eid, "kind": "code", "content": content})
        package["incidents"].append({"event_id": eid, "session_id": f"session-{index}", "task_id": "same-task" if same_task else f"task-{index}", "attempt_id": "attempt-1", "occurred_at": f"2026-10-0{index + 1}T00:00:00Z", "context_epoch": "epoch-1", "action": {"kind": "edit", "target_path": path, "after_ref": {"file_ref": eid, "line_start": 1, "line_end": 1}}})
        if receipts:
            package["receipts"].append({"receipt_id": f"receipt-{index}", "session_id": f"session-{index}", "harness": "codex", "context_epoch": "epoch-1", "before_event_id": eid, "coverage": "complete", "channels_covered": channels, "entries": [{"rule_id": "rule-v1", "source_ref": {"file_ref": "rules", "line_start": 3, "line_end": 3}, "text_sha256": digest((line + "\n").encode()), "channel": "project_instructions"}], "provenance": {"synthetic": True, "adapter": "fixture", "protocol_version": "1"}})
        package["incidents"][-1]["harness"] = "codex"
    case = tmp_path / "case.json"
    case.write_text(json.dumps(package))
    request = build_request({"repo": str(project), "project_id": "synthetic", "out": str(tmp_path / "out"), "decisions": str(registry_path), "semantic": "off", "scope": ["clients"], "as_of": "2026-11-05T00:00:00Z"}, cwd=tmp_path)
    return request, project, case, registry_path


def tree_bytes(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_formal_diagnose_generates_approved_diff_and_keeps_project(tmp_path):
    request, project, case, _ = scenario(tmp_path)
    before = tree_bytes(project)
    result = run_diagnose(request, case, run_id="approved")
    assert result.artifacts.exit_code == 0
    assert result.repair_plan.diff is not None
    patch = result.repair_plan.diff.decode()
    assert "+- New clients must use `/api/v2/users`; keep tests." in patch
    assert result.repair_plan.apply_check["applied_in_temp_copy"] is True
    assert tree_bytes(project) == before
    assert {f["kind"] for f in result.diagnosis.findings} >= {"stale_rule", "recurrence"}
    assert "loaded_violation" not in {f["kind"] for f in result.diagnosis.findings}
    run = json.loads((result.artifacts.run_dir / "run.json").read_text())
    assert run["artifact_manifest_status"] == "final"
    for item in run["artifact_manifest"]:
        assert digest((result.artifacts.run_dir / item["path"]).read_bytes()) == item["sha256"]


def test_missing_approval_and_receipts_are_distinct(tmp_path):
    request, project, case, _ = scenario(tmp_path, approval=False, receipts=False)
    result = run_diagnose(request, case)
    assert result.artifacts.exit_code == 3
    assert result.repair_plan.diff is None
    assert any(p["status"] == "needs_confirmation" for p in result.repair_plan.proposals)
    assert all(load["state"] == "unknown" for load in result.incident_analysis.loads)
    assert any(c["check_id"] == "f05.loads" and c["status"] == "not_run" for c in dump_object(result.run_result)["coverage"])


def test_retries_do_not_become_independent_recurrence(tmp_path):
    request, _, case, _ = scenario(tmp_path, same_task=True)
    result = run_diagnose(request, case)
    assert all(g["confirmed_independent_task_count"] == 1 for g in result.incident_analysis.recurrence)
    assert "recurrence" not in {f["kind"] for f in result.diagnosis.findings}


def test_formal_cli_and_empty_registry(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "AGENTS.md").write_text("# Rules\n\n[Missing](docs/missing.md)\n\n`npm run absent`\n")
    registry = tmp_path / "registry.json"
    registry.write_text('{"schema_version": 1}')
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    run = subprocess.run([sys.executable, "-m", "context_manager", "audit", "--repo", str(project), "--project-id", "synthetic", "--out", str(tmp_path / "out"), "--decisions", str(registry)], capture_output=True, text=True, env=env)
    assert run.returncode == 0, run.stderr
    report = Path(run.stdout.split("report: ", 1)[1].strip())
    findings = json.loads(report.with_name("findings.json").read_text())["findings"]
    assert any(f["kind"] == "dead_reference" and f["assessment"] == "supported" for f in findings)


def test_bad_input_has_no_output_and_run_collision_preserves_bytes(tmp_path):
    request, _, case, registry = scenario(tmp_path)
    result = run_audit(request, run_id="stable")
    before = tree_bytes(result.artifacts.run_dir)
    with pytest.raises(ArtifactCollisionError):
        run_audit(request, run_id="stable")
    assert tree_bytes(result.artifacts.run_dir) == before
    case.write_text('{"format":"context-manager-incidents","schema_version":1,"synthetic":false}')
    with pytest.raises(Exception) as error:
        run_diagnose(request, case, run_id="bad")
    assert getattr(error.value, "code") == "not_synthetic"
    assert not (Path(request.out) / "bad").exists()


def test_legacy_case_stays_unverified(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    registry = tmp_path / "decisions.json"
    registry.write_text('{"schema_version": 1}')
    request = build_request({"repo": str(project), "project_id": "legacy", "out": str(tmp_path / "out"), "decisions": str(registry)}, cwd=tmp_path)
    case = Path(__file__).resolve().parents[1] / "examples/stale-users-api/case.json"
    result = run_diagnose(request, case)
    assert result.artifacts.exit_code == 3
    assert result.incident_analysis.legacy["requires_migration"]
    assert not result.incident_analysis.loads
    assert not result.incident_analysis.behaviors
    assert all(c["verified_state"] == "unknown" for c in result.incident_analysis.legacy["claims"])


def test_missing_event_time_cannot_be_reported_complete(tmp_path):
    request, _, case, _ = scenario(tmp_path)
    data = json.loads(case.read_text())
    for event in data["incidents"]:
        event.pop("occurred_at")
    case.write_text(json.dumps(data))
    result = run_diagnose(request, case)
    assert result.artifacts.exit_code == 3
    assert result.incident_analysis.behaviors
    assert all(b["status"] == "unknown" for b in result.incident_analysis.behaviors)
    assert not any(f["kind"] == "recurrence" for f in result.diagnosis.findings)


def test_model_candidates_require_actual_reads(tmp_path):
    from context_manager.analyst import AnalystRequest, analyze_context
    from context_manager.model_transport import ChatResponse

    request, project, _, _ = scenario(tmp_path)
    class FixedTransport:
        def __init__(self, actions):
            self.actions = iter(actions)
        def complete(self, messages, deadline):
            return ChatResponse(json.dumps(next(self.actions)))
    from dataclasses import replace
    request = replace(request, semantic_mode="local")
    material = {"rules": [], "decisions": [], "confirmations": [], "checks": []}
    with_store = EvidenceStore(project, tmp_path / "model-out", "model")
    try:
        evidence = with_store.capture_file("AGENTS.md", kind="rule_file")
        candidate = {"kind": "cleanup_candidate", "claim": "Review this rule.", "scope": dump_object(request.task_scope), "support_refs": [{"evidence_id": evidence.evidence_id, "line_start": 3, "line_end": 3}], "counter_refs": [], "missing_evidence": [], "suggested_action": "ask_user"}
        submission = {"action": "submit_candidates", "args": {"candidates": [candidate]}}
        rejected = analyze_context(AnalystRequest("model", request, "audit", material), with_store, FixedTransport([submission]))
        assert not rejected.candidates
        assert rejected.status == "partial"
        valid = analyze_context(AnalystRequest("model", request, "audit", material), with_store, FixedTransport([{"action": "read_evidence", "args": {"evidence_id": evidence.evidence_id, "line_start": 3, "line_end": 3}}, submission]))
        assert valid.status == "complete"
        assert valid.candidates[0]["support_refs"][0]["sha256"] == evidence.sha256
        assert valid.usage["model_calls"] == 2
    finally:
        with_store.close()


def test_runtime_failure_produces_failed_artifacts(tmp_path, monkeypatch):
    from context_manager import pipeline
    request, _, _, _ = scenario(tmp_path)
    def fail(*args, **kwargs):
        raise RuntimeError("synthetic stage failure")
    monkeypatch.setattr(pipeline, "inspect_project", fail)
    result = run_audit(request)
    assert result.artifacts.exit_code == 1
    run = json.loads((result.artifacts.run_dir / "run.json").read_text())
    assert run["status"] == "failed"
    assert any(e["code"] == "pipeline_stage_failed" for e in run["errors"])
