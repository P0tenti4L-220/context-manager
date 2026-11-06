"""F07: references alone cannot establish authority, loading or causation."""
from dataclasses import replace
import copy
import json

import pytest

from acceptance_support import current_rule_scenario, digest, model_candidate, policy_session, read_as_model, scenario, update_json
from context_manager.contracts import dump_object
from context_manager.diagnosis import evaluate_findings
from context_manager.evidence import EvidenceStore
from context_manager.pipeline import run_audit, run_diagnose


def evaluate(request, result, store, static=(), models=(), reads=(), incident=None):
    return evaluate_findings(request, static, models, result.comparison, incident, incident.receipts if incident else [], store, inspection=result.inspection, model_reads=reads)


def test_f07_01_fact_and_causal_explanation_have_separate_strength(tmp_path):
    with policy_session(tmp_path) as (request, _, case, _, result, store):
        candidate = model_candidate(result, claim="The old instruction may have contributed to the repeated wrong endpoint.", claim_type="causal_explanation", suggested_action="add_check")
        reads = read_as_model(store, candidate["support_refs"])
        diagnosis = evaluate(replace(request, semantic_mode="local"), result, store, result.comparison.candidates, [candidate], reads)
        assert any(f["kind"] == "stale_rule" and f["assessment"] == "supported" for f in diagnosis.findings)
        causal = next(f for f in diagnosis.findings if f["claim_type"] == "causal_explanation")
        assert causal["assessment"] == "hypothesis" and causal["suggested_action"] == "ask_user"
        assert causal["related_finding_ids"]


def test_f07_02_correct_rule_missing_from_complete_context(tmp_path):
    request, project, case, _ = current_rule_scenario(tmp_path)
    update_json(case, lambda d: [r.update(entries=[]) for r in d["receipts"]])
    result = run_diagnose(request, case)
    findings = [f for f in result.diagnosis.findings if f["kind"] == "missing_load"]
    assert findings and all(f["assessment"] == "supported" and f["suggested_action"] == "fix_loading" for f in findings)
    assert all(f["synthetic"] for f in findings)
    assert not any(f["kind"] == "loaded_violation" for f in result.diagnosis.findings)


def test_f07_03_loaded_correct_rule_with_actual_violation(tmp_path):
    request, _, case, _ = current_rule_scenario(tmp_path)
    result = run_diagnose(request, case)
    findings = [f for f in result.diagnosis.findings if f["kind"] == "loaded_violation"]
    assert len(findings) == 2
    assert all(f["assessment"] == "supported" and f["suggested_action"] == "add_check" for f in findings)


def test_f07_04_missing_receipt_is_unknown(tmp_path):
    request, _, case, _ = current_rule_scenario(tmp_path)
    update_json(case, lambda d: d.update(receipts=[]))
    result = run_diagnose(request, case)
    missing = [f for f in result.diagnosis.findings if f["kind"] == "missing_load"]
    assert missing and all(f["assessment"] == "unknown" and f["missing_evidence"] and f["suggested_action"] == "ask_user" for f in missing)


def test_f07_05_model_confidence_does_not_upgrade(tmp_path):
    with policy_session(tmp_path) as (request, _, _, _, result, store):
        candidate = model_candidate(result, model_confidence=0.99, suggested_action="update")
        diagnosis = evaluate(replace(request, semantic_mode="local"), result, store, models=[candidate], reads=read_as_model(store, candidate["support_refs"]))
        assert diagnosis.findings[0]["assessment"] == "hypothesis"
        assert diagnosis.findings[0]["suggested_action"] == "ask_user"
        assert diagnosis.validations[0]["model_claimed"]["model_confidence"] == 0.99


def test_f07_06_cross_harness_difference_does_not_prove_joint_loading(tmp_path):
    request, project, _, registry = scenario(tmp_path)
    text = (project / "AGENTS.md").read_text().replace("/api/v1/users", "/api/v3/users")
    (project / "CLAUDE.md").write_text(text)
    from acceptance_support import digest
    def edit(data):
        rule = copy.deepcopy(data["rules"][0])
        rule.update(rule_id="claude-rule", harness="claude_code", text=rule["text"].replace("/api/v1/users", "/api/v3/users"), confirmation_ref="claude-confirm")
        rule["source_ref"].update(relative_path="CLAUDE.md", sha256=digest(text.encode()))
        rule["text_sha256"] = digest((rule["text"] + "\n").encode())
        rule["constraints"][0]["value"] = "/api/v3/users"
        data["rules"].append(rule)
        data["confirmations"].append({"confirmation_id": "claude-confirm", "kind": "rule_interpretation", "source_ref": rule["source_ref"], "approved_items": [{"item_id": "claude-rule", "source_ref": rule["source_ref"]}]})
    update_json(registry, edit)
    result = run_audit(request)
    assert any(f["kind"] == "policy_mismatch" and f["assessment"] == "supported" for f in result.diagnosis.findings)
    assert not any(f["kind"] == "rule_conflict" for f in result.diagnosis.findings)


def test_f07_07_counter_evidence_survives(tmp_path):
    with policy_session(tmp_path) as (request, _, _, _, result, store):
        candidate = model_candidate(result)
        decision_ref = dump_object(result.comparison.policy.effective_decisions[0]["source_ref"])
        candidate["counter_refs"] = [decision_ref]
        diagnosis = evaluate(replace(request, semantic_mode="local"), result, store, models=[candidate], reads=read_as_model(store, candidate["support_refs"] + candidate["counter_refs"]))
        assert dump_object(diagnosis.findings[0]["counter_refs"]) == [decision_ref]


def test_f07_08_unknown_model_note_cannot_replace_supported_fact(tmp_path):
    with policy_session(tmp_path) as (request, _, _, _, result, store):
        candidate = model_candidate(result)
        diagnosis = evaluate(replace(request, semantic_mode="local"), result, store, result.comparison.candidates, [candidate], read_as_model(store, candidate["support_refs"]))
        assert {f["assessment"] for f in diagnosis.findings} >= {"supported", "hypothesis"}
        assert len({f["finding_id"] for f in diagnosis.findings}) == len(diagnosis.findings)


def test_f07_09_unverified_required_version_stays_unknown(tmp_path):
    with policy_session(tmp_path) as (request, _, _, _, result, store):
        candidate = model_candidate(result, kind="stale_code_evidence", suggested_action="fix_retrieval", origin="incidents", facts={"event_id": "event-1"})
        diagnosis = evaluate(request, result, store, static=[candidate])
        assert diagnosis.findings[0]["assessment"] == "unknown"
        assert diagnosis.findings[0]["suggested_action"] == "ask_user"


@pytest.mark.parametrize("variant", ["different", "equal", "draft", "wrong_snapshot"])
def test_f07_09_confirmed_required_version_through_formal_pipeline(tmp_path, variant):
    request, project, case, registry = current_rule_scenario(tmp_path)
    target = "clients/client1.py"
    old = 'ENDPOINT = "/api/v1/users"\n'
    required = old if variant == "equal" else 'ENDPOINT = "/api/v2/users"\n'
    required_sha = digest(required.encode())
    text = f"This task requires {target} at digest {required_sha}.\n"
    (project / "docs/version.md").write_text(text)
    source = {"relative_path": "docs/version.md", "sha256": digest(text.encode()), "line_start": 1, "line_end": 1}
    def add_requirement(data):
        data["decisions"].append({"decision_id": "required-version", "status": "draft" if variant == "draft" else "accepted", "scope": {"include_prefixes": ["clients"]}, "effective_from": "2026-10-01T00:00:00Z", "supersedes": [], "source_ref": source, "confirmation_ref": "version-confirmation", "constraints": [{"subject": "code_snapshot_sha256", "operator": "equals", "value": required_sha, "conditions": {"relative_path": target}}]})
        data["confirmations"].append({"confirmation_id": "version-confirmation", "kind": "decision_approval", "source_ref": source, "approved_items": [{"item_id": "required-version", "source_ref": source}]})
    update_json(registry, add_requirement)
    def add_version_snapshots(data):
        data["files"].append({"file_ref": "required", "path": target, "revision": "required-revision", "kind": "code", "content": required})
        event = data["incidents"][0]
        event["action"] = {"kind": "tool_result", "target_path": target, "result_ref": {"file_ref": "event-1"}}
        event["code_version"] = {"observed_ref": {"file_ref": "required" if variant == "wrong_snapshot" else "event-1"}, "required_ref": {"file_ref": "required"}, "decision_id": "required-version"}
    update_json(case, add_version_snapshots)
    result = run_diagnose(request, case)
    assert result.diagnosis is not None
    findings = [f for f in result.diagnosis.findings if f["kind"] == "stale_code_evidence"]
    if variant == "different":
        assert len(findings) == 1 and findings[0]["assessment"] == "supported"
        assert findings[0]["suggested_action"] == "fix_retrieval"
        assert any(c["check_id"] == "version.compare" and c["status"] == "passed" for c in findings[0]["checks"])
        assert not result.repair_plan.diff
    elif variant == "equal":
        assert not findings
    else:
        assert len(findings) == 1 and findings[0]["assessment"] == "unknown"
        assert findings[0]["suggested_action"] == "ask_user"


@pytest.mark.parametrize("key,value", [("evidence_id", "other-run"), ("sha256", "f" * 64), ("line_start", 0), ("line_end", 5000)])
def test_f07_10_invalid_refs_rejected_without_hiding_valid_candidate(tmp_path, key, value):
    with policy_session(tmp_path) as (request, _, _, _, result, store):
        valid = dump_object(result.comparison.candidates[0])
        broken = copy.deepcopy(valid)
        broken["candidate_id"] = "broken"
        broken["support_refs"][0][key] = value
        diagnosis = evaluate(request, result, store, static=[broken, valid])
        assert len(diagnosis.findings) == 1
        assert any(v["status"] == "rejected" for v in diagnosis.validations)


def test_f07_11_direct_supersession_is_not_unresolved_conflict(tmp_path):
    with policy_session(tmp_path) as (_, _, _, _, result, _):
        assert any(f["kind"] == "stale_rule" for f in result.diagnosis.findings)
        assert not any(f["kind"] == "rule_conflict" for f in result.diagnosis.findings)


def test_f07_12_historical_events_do_not_use_future_decision(tmp_path):
    request, _, case, registry = scenario(tmp_path)
    update_json(case, lambda d: [e.update(occurred_at="2026-09-01T00:00:00Z") for e in d["incidents"]])
    result = run_diagnose(request, case)
    assert any(f["kind"] == "stale_rule" for f in result.diagnosis.findings)
    assert not any(f["kind"] in ("recurrence", "loaded_violation") for f in result.diagnosis.findings)


def test_f07_13_unresolved_rule_cannot_authorize_destructive_model_action(tmp_path):
    with policy_session(tmp_path) as (request, _, _, _, result, store):
        candidate = model_candidate(result, suggested_action="delete", missing_evidence=["interpretation approval"])
        diagnosis = evaluate(replace(request, semantic_mode="local"), result, store, models=[candidate], reads=read_as_model(store, candidate["support_refs"]))
        assert diagnosis.findings[0]["suggested_action"] == "ask_user"


def test_f07_14_duplicates_merge_and_repeated_id_is_rejected(tmp_path):
    with policy_session(tmp_path) as (request, _, _, _, result, store):
        original = dump_object(result.comparison.candidates[0])
        duplicate = original | {"candidate_id": "same-fact-other-id"}
        diagnosis = evaluate(request, result, store, static=[original, duplicate, original])
        assert len(diagnosis.findings) == 1
        assert any(v["status"] == "merged" for v in diagnosis.validations)
        assert any(v["reason_code"] == "duplicate_candidate_id" for v in diagnosis.validations)


@pytest.mark.parametrize("kind,reason", [("missing_load", "model_cannot_establish_load"), ("loaded_violation", "model_cannot_establish_violation"), ("recurrence", "model_cannot_establish_recurrence")])
def test_f07_15_model_cannot_establish_action_facts(tmp_path, kind, reason):
    with policy_session(tmp_path) as (request, _, _, _, result, store):
        candidate = model_candidate(result, kind=kind)
        diagnosis = evaluate(replace(request, semantic_mode="local"), result, store, models=[candidate], reads=read_as_model(store, candidate["support_refs"]))
        assert not diagnosis.findings
        assert diagnosis.validations[0]["reason_code"] == reason


def test_f07_16_empty_all_invalid_and_candidate_limit(tmp_path):
    with policy_session(tmp_path) as (request, _, _, _, result, store):
        empty = evaluate(request, result, store)
        assert not empty.findings and all(c["reason"] == "no_candidates" for c in empty.coverage)
        invalid = evaluate(request, result, store, static=[None, {}])
        assert not invalid.findings and len(invalid.validations) == 2
        model = model_candidate(result, kind="cleanup_candidate", suggested_action="relocate")
        disabled = evaluate(request, result, store, static=result.comparison.candidates, models=[model])
        assert len(disabled.findings) == 1 and disabled.findings[0]["kind"] == "stale_rule"
        assert disabled.validations[-1]["reason_code"] == "semantic_disabled"
        limited = evaluate(request, result, store, static=[{}] * 1001)
        assert all(c["status"] == "failed" for c in limited.coverage)


def test_f07_17_dead_reference_requires_actual_inspection(tmp_path):
    with policy_session(tmp_path) as (request, _, _, _, result, store):
        forged = model_candidate(result, kind="dead_reference", origin="inspector", facts={"verified_missing": True})
        diagnosis = evaluate(request, result, store, static=[forged])
        assert diagnosis.findings[0]["assessment"] == "unknown"


def test_f07_18_non_synthetic_receipt_never_proves_load(tmp_path):
    request, _, case, _ = current_rule_scenario(tmp_path)
    update_json(case, lambda d: [r["provenance"].update(synthetic=False) for r in d["receipts"]])
    result = run_diagnose(request, case)
    assert not any(f["kind"] == "loaded_violation" for f in result.diagnosis.findings)
    assert all(l["state"] == "unknown" for l in result.incident_analysis.loads)
