"""Regressions for concrete defects addressed by the F03–F10 execution plan."""
from dataclasses import replace
from contextlib import closing
import json
from pathlib import Path
import shutil

import pytest

from context_manager.adapters import rule_sources
from context_manager.contracts import ContractError, Scope, build_request, dump_object
from context_manager.decisions import DecisionResolver, bind_registry, compare_constraint_pair, parse_registry
from context_manager.evidence import EvidenceStore
from context_manager.inspector import inspect_project
from context_manager.pipeline import run_audit, run_diagnose
from context_manager.rules import extract_rule_spans
from test_workflow import scenario, digest


def test_published_views_cannot_change_approval_or_cached_policy(tmp_path):
    request, _, case, _ = scenario(tmp_path, approval=False)
    result = run_diagnose(request, case)
    policy = result.comparison.policy
    attempts = [
        lambda: setattr(result.inspection, "status", "complete"),
        lambda: policy.editable_files.append("clients/client1.py"),
        lambda: policy.get_rule("rule-v1")["scope"]["include_prefixes"].append("."),
        lambda: policy.get_confirmation("confirm-decision")["edit_approvals"].append({}),
        lambda: dict.update(policy.get_confirmation("confirm-decision"), edit_approvals=[{}]),
        lambda: result.incident_analysis.events[0]["action"].update(target_path="outside.py"),
        lambda: result.artifacts.files.clear(),
    ]
    for attempt in attempts:
        with pytest.raises((TypeError, AttributeError)):
            attempt()
    projection = dump_object(policy)
    projection["registry"]["confirmations"][1]["edit_approvals"].append({"decision_id": "forged"})
    assert not policy.get_confirmation("confirm-decision")["edit_approvals"]
    assert result.repair_plan.diff is None
    store = EvidenceStore(request.repo, request.out, result.run_id)
    try:
        mutable_spans = dump_object(extract_rule_spans(rule_sources(result.inspection), store).spans)
        resolver = DecisionResolver(mutable_spans, policy.registry, store)
        mutable_spans.clear()
        cached = resolver.policy_at(request.as_of)
        assert cached.get_rule("rule-v1")
        with pytest.raises((TypeError, AttributeError)):
            cached.effective_decisions[0]["constraints"][0]["value"] = "forged"
        assert resolver.policy_at(request.as_of) is cached
    finally:
        store.close()


def test_task_exclusion_does_not_change_rule_declaration_scope(tmp_path):
    request, _, _, _ = scenario(tmp_path)
    request = replace(request, task_scope=Scope(("clients",), ("clients/legacy",), ()))
    result = run_audit(request)
    assert result.comparison.policy.get_rule("rule-v1")
    root = next(r for r in result.inspection.rule_files if r["relative_path"] == "AGENTS.md")
    assert not root["scope"]["exclude_prefixes"]
    assert all("clients/legacy" in f["scope"]["exclude_prefixes"] for f in result.diagnosis.findings)


def test_blank_rules_and_inline_comments_keep_correct_block_coverage(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "AGENTS.md").write_text("\n")
    clause = "- Keep tests. <!-- explanatory comment -->"
    (project / "CLAUDE.md").write_text("# Rules\n\n" + clause + "\n")
    request = build_request({"repo": str(project), "out": str(tmp_path / "out"), "project_id": "p"}, cwd=tmp_path)
    store = EvidenceStore(project, request.out, "blocks")
    try:
        extraction = extract_rule_spans(rule_sources(inspect_project(request, store)), store)
        assert not extraction.gaps
        assert len(extraction.spans) == 1
        assert extraction.spans[0]["text"] == clause
        assert extraction.spans[0]["source_ref"]["line_start"] == 3
    finally:
        store.close()


@pytest.mark.parametrize("prefix,valid", [("/", False), ("clients//api", False), ("clients/*.py", False), ("clients/**", True)])
def test_frontmatter_scope_is_normalized_or_unknown(tmp_path, prefix, valid):
    project = tmp_path / "project"
    (project / ".claude/rules").mkdir(parents=True)
    (project / "clients").mkdir()
    (project / ".claude/rules/api.md").write_text('---\npaths: [' + json.dumps(prefix) + ']\n---\n- Keep tests.\n')
    request = build_request({"repo": str(project), "out": str(tmp_path / "out"), "project_id": "p", "scope": ["clients"]}, cwd=tmp_path)
    store = EvidenceStore(project, request.out, "paths")
    try:
        result = inspect_project(request, store)
        rule = result.rule_files[0]
        assert rule["applies_to_task"] is (True if valid else None)
        assert result.status == ("complete" if valid else "partial")
    finally:
        store.close()


def test_arbitrary_condition_keys_are_not_parsed_as_source_locators(tmp_path):
    request, _, _, registry_path = scenario(tmp_path)
    data = json.loads(registry_path.read_text())
    data["rules"][0]["constraints"][0]["conditions"] = {"source_ref": "literal", "scope": "some-mode"}
    registry_path.write_text(json.dumps(data))
    result = run_audit(request)
    assert result.artifacts.exit_code != 1
    assert not result.comparison.policy.registry.issues
    assert result.comparison.policy.get_rule("rule-v1")["constraints"][0]["conditions"]["source_ref"] == "literal"


def test_failed_confirmation_binding_does_not_publish_half_bound_refs(tmp_path):
    request, project, _, registry_path = scenario(tmp_path)
    data = json.loads(registry_path.read_text())
    data["confirmations"][1]["approved_items"][0]["source_ref"]["sha256"] = "f" * 64
    parsed = parse_registry(json.dumps(data).encode(), project_id=request.project_id)
    before = dump_object(parsed)
    store = EvidenceStore(project, request.out, "binding")
    try:
        bound = bind_registry(parsed, store)
        failed = bound.confirmations[1]
        assert failed["binding_failed"]
        assert "relative_path" in failed["source_ref"]
        assert "evidence_id" not in failed["source_ref"]
        assert dump_object(parsed) == before
        assert any(i["field_path"] == "confirmations[1].source_ref" for i in bound.issues)
    finally:
        store.close()


def test_unknown_runtime_conditions_do_not_become_definite_values(tmp_path):
    request, _, _, registry_path = scenario(tmp_path)
    data = json.loads(registry_path.read_text())
    data["decisions"][0]["constraints"][0]["conditions"] = {"operation": "create"}
    registry_path.write_text(json.dumps(data))
    result = run_audit(request)
    store = EvidenceStore(request.repo, request.out, result.run_id)
    try:
        spans = extract_rule_spans(rule_sources(result.inspection), store).spans
        resolver = DecisionResolver(spans, result.comparison.policy.registry, store)
        resolution = resolver.resolve_constraint("endpoint", "clients/x.py", as_of=request.as_of, harness="codex")
        assert resolution["status"] == "unknown"
        assert "condition_context_missing" in resolution["gaps"]
    finally:
        store.close()
    base = {"constraint_id": "a", "subject": "a", "operator": "equals", "value": True, "conditions": {}}
    assert compare_constraint_pair(base, base | {"subject": "b", "value": False})["status"] == "compatible"
    assert compare_constraint_pair(base, base | {"value": 1})["status"] == "incompatible"
    assert compare_constraint_pair(base | {"conditions": {"mode": True}}, base | {"conditions": {"mode": 1}, "value": False})["status"] == "compatible"


@pytest.mark.parametrize("option", ["--decisions", "--harness-profile"])
def test_selected_input_symlink_is_rejected_before_output(tmp_path, capsys, option):
    from context_manager.cli import main

    project = tmp_path / "project"
    project.mkdir()
    original = tmp_path / "input.json"
    original.write_text('{"schema_version": 1}')
    alias = tmp_path / "alias.json"
    alias.symlink_to(original)
    out = tmp_path / "out"
    assert main(["audit", "--repo", str(project), "--out", str(out), "--project-id", "p", option, str(alias)]) == 2
    assert capsys.readouterr().out == ""
    assert not out.exists()


def test_invalid_profile_shape_and_output_file_are_preflight_errors(tmp_path):
    request, _, _, _ = scenario(tmp_path)
    profile = tmp_path / "profile.json"
    for invalid in ([], {"schema_version": 1, "profile_id": "p", "profile_version": "1", "source": "synthetic", "harnesses": [{"id": "codex", "context_channels": "project_instructions"}]}):
        profile.write_text(json.dumps(invalid))
        with pytest.raises(ContractError) as error:
            run_audit(replace(request, harness_profile=str(profile)))
        assert error.value.code in ("invalid_harness_profile", "invalid_json")
        assert not Path(request.out).exists()
    Path(request.out).write_text("existing output sentinel")
    with pytest.raises(ContractError):
        run_audit(request)
    assert Path(request.out).read_text() == "existing output sentinel"


def test_event_scope_and_check_path_do_not_inflate_recurrence(tmp_path):
    request, _, case, registry = scenario(tmp_path)
    data = json.loads(registry.read_text())
    data["behavior_checks"][0]["parameters"]["path"] = "clients/client1.py"
    registry.write_text(json.dumps(data))
    package = json.loads(case.read_text())
    package["incidents"][1]["scope"] = ["unrelated"]
    case.write_text(json.dumps(package))
    result = run_diagnose(request, case)
    assert result.artifacts.exit_code == 0
    assert {load["event_id"] for load in result.incident_analysis.loads} == {"event-1"}
    assert {b["event_id"] for b in result.incident_analysis.behaviors} == {"event-1"}
    assert result.incident_analysis.counts["confirmed_independent_tasks"] == 1
    assert not any(f["kind"] == "recurrence" for f in result.diagnosis.findings)


def test_structured_failure_must_use_the_actions_exact_after_snapshot(tmp_path):
    request, _, case, registry = scenario(tmp_path)
    data = json.loads(registry.read_text())
    data["behavior_checks"][0].update(detector="structured_check_result", parameters={"result_check_id": "external", "decision_id": "decision-v2"})
    registry.write_text(json.dumps(data))
    package = json.loads(case.read_text())
    package["files"].append({"file_ref": "wrong", "path": "clients/other.py", "revision": "old", "kind": "code", "content": "old input\n"})
    package["check_results"] = [{"check_id": "external", "event_id": "event-1", "input_sha256": digest(b"old input\n"), "input_ref": {"file_ref": "wrong"}, "status": "failed"}]
    case.write_text(json.dumps(package))
    result = run_diagnose(request, case)
    first = next(b for b in result.incident_analysis.behaviors if b["event_id"] == "event-1")
    assert first["status"] == "unknown"
    assert "check_action_mismatch" in first["reasons"]
    assert result.artifacts.exit_code == 3
    assert not any(f["kind"] == "recurrence" for f in result.diagnosis.findings)


def test_model_non_issues_are_retained_but_reasoned_cleanup_survives(tmp_path):
    from context_manager.analyst import AnalystRequest, analyze_context
    from context_manager.model_transport import ChatResponse

    request, project, _, _ = scenario(tmp_path)
    with closing(EvidenceStore(project, tmp_path / "model-out", "quality")) as store:
        evidence = store.capture_file("AGENTS.md", kind="rule_file")
        base = {"kind": "cleanup_candidate", "claim": "New clients must use `/api/v1/users`; keep tests.", "scope": dump_object(request.task_scope), "support_refs": [{"evidence_id": evidence.evidence_id, "line_start": 3, "line_end": 3}], "suggested_action": "ask_user"}
        candidates = [base | {"candidate_id": "echo"}, base | {"candidate_id": "keep", "claim": "Keep the test obligation.", "suggested_action": "keep"}, base | {"candidate_id": "reasoned", "claim": "Move temporary progress notes out of durable instructions.", "rationale": "The cited task is complete and its remaining progress note no longer instructs future work.", "suggested_action": "relocate"}]
        class Transport:
            actions = iter([{"action": "read_evidence", "args": {"evidence_id": evidence.evidence_id, "line_start": 3, "line_end": 3}}, {"action": "submit_candidates", "args": {"candidates": candidates}}])
            def complete(self, messages, deadline):
                return ChatResponse(json.dumps(next(self.actions)))
        analysis = analyze_context(AnalystRequest("quality", replace(request, semantic_mode="local"), "audit", {k: [] for k in ("rules", "decisions", "confirmations", "checks")}), store, Transport())
        assert analysis.status == "complete", dump_object(analysis.rejected_candidates)
        assert [c["candidate_id"] for c in analysis.candidates] == ["reasoned"]
        assert {c["reason"] for c in analysis.filtered_candidates} == {"no_action_recommended", "verbatim_restatement_without_rationale"}
        assert len([e for e in analysis.trace if e["event"] == "candidate_filtered"]) == 2


def test_initial_model_context_budget_stops_before_transport(tmp_path):
    from context_manager.analyst import AnalystBudget, AnalystRequest, analyze_context

    request, project, _, _ = scenario(tmp_path)
    class NoCalls:
        def complete(self, *args):
            pytest.fail("oversized initial context must not call the model")
    with closing(EvidenceStore(project, tmp_path / "model-out", "budget")) as store:
        analysis = analyze_context(AnalystRequest("budget", replace(request, semantic_mode="local"), "audit", {k: [] for k in ("rules", "decisions", "confirmations", "checks")}), store, NoCalls(), AnalystBudget(max_brief_bytes=1))
        assert analysis.stop_reason == "brief_budget_exhausted"
        assert analysis.usage["model_calls"] == 0
        assert analysis.trace[-1]["event"] == "run_end"


def test_inspector_reads_cannot_authorize_model_citations_or_empty_audit(tmp_path):
    from context_manager.analyst import AnalystRequest, analyze_context
    from context_manager.diagnosis import evaluate_findings
    from context_manager.model_transport import ChatResponse

    request, _, _, _ = scenario(tmp_path)
    result = run_audit(request)
    request = replace(request, semantic_mode="local")
    with closing(EvidenceStore(request.repo, request.out, result.run_id)) as store:
        reference = dump_object(result.comparison.policy.get_rule("rule-v1")["source_ref"])
        assert store.validate_ref(reference, require_covered=True).ok
        candidate = {"candidate_id": "unseen", "kind": "cleanup_candidate", "claim": "Review a specific temporary task note.", "scope": dump_object(request.task_scope), "support_refs": [reference], "suggested_action": "ask_user"}
        material = {k: [] for k in ("rules", "decisions", "confirmations", "checks")}
        class Empty:
            def complete(self, messages, deadline):
                return ChatResponse('{"action":"submit_candidates","args":{"candidates":[]}}')
        empty = analyze_context(AnalystRequest(result.run_id, request, "audit", material), store, Empty())
        assert empty.status == "partial" and empty.stop_reason == "protocol_failure"
        assert empty.usage["model_calls"] == 2
        class Unseen:
            actions = iter([{"action": "read_evidence", "args": {"evidence_id": reference["evidence_id"], "line_start": 1, "line_end": 1}}, {"action": "submit_candidates", "args": {"candidates": [candidate]}}])
            def complete(self, messages, deadline):
                return ChatResponse(json.dumps(next(self.actions)))
        unread = analyze_context(AnalystRequest(result.run_id, request, "audit", material), store, Unseen())
        assert not unread.candidates
        assert unread.rejected_candidates[0]["reason"] == "evidence_not_read_by_analyst"
        assert any(event["event"] == "candidate_rejected" and event["code"] == "evidence_not_read_by_analyst" for event in unread.trace)
        diagnosis = evaluate_findings(request, [], [candidate], result.comparison, None, [], store, model_reads=unread.reads)
        assert not diagnosis.findings
        assert diagnosis.validations[0]["status"] == "rejected"


def test_self_claimed_missing_fact_is_unknown_and_finding_ids_are_stable(tmp_path):
    from context_manager.diagnosis import evaluate_findings

    request, _, _, _ = scenario(tmp_path)
    result = run_audit(request)
    repeat = run_audit(request)
    assert [f["finding_id"] for f in result.diagnosis.findings] == [f["finding_id"] for f in repeat.diagnosis.findings]
    with closing(EvidenceStore(request.repo, request.out, result.run_id)) as store:
        candidate = {"candidate_id": "forged", "kind": "dead_reference", "claim": "A local file is definitely missing.", "scope": dump_object(request.task_scope), "support_refs": [dump_object(result.comparison.policy.get_rule("rule-v1")["source_ref"])], "facts": {"verified_missing": True}, "suggested_action": "delete"}
        diagnosis = evaluate_findings(request, [candidate], [], result.comparison, None, [], store, inspection=result.inspection)
        assert diagnosis.findings[0]["assessment"] == "unknown"
        assert diagnosis.findings[0]["suggested_action"] == "ask_user"


@pytest.mark.parametrize("ending", [b"\r\n", b""])
def test_approved_first_line_edit_preserves_bom_and_line_ending(tmp_path, ending):
    request, project, _, registry_path = scenario(tmp_path)
    registry = json.loads(registry_path.read_text())
    line = registry["rules"][0]["text"]
    original = b"\xef\xbb\xbf" + line.encode() + ending
    (project / "AGENTS.md").write_bytes(original)
    replacement = line.replace("/api/v1/users", "/api/v2/users").encode() + ending
    def update(value):
        if isinstance(value, dict):
            if value.get("relative_path") == "AGENTS.md":
                value.update(sha256=digest(original), line_start=1, line_end=1)
            for child in value.values():
                update(child)
        elif isinstance(value, list):
            for child in value:
                update(child)
    update(registry)
    registry["rules"][0]["text_sha256"] = digest(original)
    registry["confirmations"][1]["edit_approvals"][0].update(line_start=1, line_end=1, old_text_sha256=digest(original), replacement_sha256=digest(replacement))
    registry_path.write_text(json.dumps(registry))
    result = run_audit(request)
    assert result.artifacts.exit_code == 0
    assert result.repair_plan.apply_check["applied_in_temp_copy"]
    assert b"+\xef\xbb\xbf" + replacement in result.repair_plan.diff
    assert (project / "AGENTS.md").read_bytes() == original


def test_one_unready_intent_cannot_leave_half_of_a_findings_diff(tmp_path):
    from context_manager.intents import build_edit_intents
    from context_manager.repair import plan_repairs

    request, _, _, _ = scenario(tmp_path)
    result = run_audit(request)
    intents = dump_object(build_edit_intents(result.comparison, result.diagnosis))
    broken = intents[0] | {"edit_intent_id": "broken", "base_sha256": "f" * 64}
    with closing(EvidenceStore(request.repo, request.out, result.run_id)) as store:
        plan = plan_repairs(result.diagnosis.findings, [intents[0], broken], result.comparison.policy, store)
    assert plan.diff is None
    assert not any(p["status"] == "preview_ready" for p in plan.proposals)
    assert any(r["reason"] == "base_mismatch" for r in plan.rejections)


def test_report_rejects_missing_diff_and_downgrades_corrupt_snapshot(tmp_path):
    from context_manager.reporting import build_artifacts

    request, _, _, _ = scenario(tmp_path)
    result = run_audit(request)
    with pytest.raises(ContractError) as error:
        build_artifacts(result.run_result, result.evidence_catalog)
    assert error.value.code == "diff_missing"
    first = result.diagnosis.findings[0]["support_refs"][0]
    location = result.evidence_catalog.location(first["evidence_id"])
    (result.evidence_catalog.root / location.artifact_path).write_bytes(b"corrupt snapshot\n")
    bundle = build_artifacts(result.run_result, result.evidence_catalog, result.repair_plan)
    assert bundle.exit_code == 3
    assert bundle.run["integrity"]["evidence_links"] == "failed"


def test_artifact_failure_keeps_incomplete_manifest_and_never_overwrites(tmp_path, monkeypatch):
    from context_manager import reporting

    request, _, _, _ = scenario(tmp_path)
    result = run_audit(request)
    output = tmp_path / "second-output"
    directory = output / result.run_id
    directory.mkdir(parents=True)
    shutil.copytree(result.artifacts.run_dir / "evidence", directory / "evidence")
    catalog = replace(result.evidence_catalog, root=directory)
    real_write = reporting._write_artifact
    def fail_checks(path, data, **kwargs):
        if path.name == "checks.json":
            raise PermissionError("synthetic disk failure")
        return real_write(path, data, **kwargs)
    monkeypatch.setattr(reporting, "_write_artifact", fail_checks)
    with pytest.raises(reporting.ArtifactWriteError) as error:
        reporting.write_run_artifacts(result.run_result, output, catalog, result.repair_plan)
    assert error.value.path == "checks.json"
    manifest = json.loads((directory / "run.json").read_text())
    assert manifest["artifact_manifest_status"] == "incomplete"
    assert {item["path"] for item in manifest["artifact_manifest"]} == {"findings.json"}
    sentinel = directory / "sentinel"
    sentinel.write_bytes(b"preserve existing bytes")
    with pytest.raises(FileExistsError):
        real_write(sentinel, b"replacement")
    assert sentinel.read_bytes() == b"preserve existing bytes"
