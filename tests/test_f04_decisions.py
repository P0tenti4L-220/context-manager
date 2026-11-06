"""F04: authority, precise bindings, time, scope and compound obligations."""
from dataclasses import replace
import json
import os

import pytest

from acceptance_support import digest, policy_session, scenario, update_json, write_json
from context_manager.contracts import dump_object
from context_manager.decisions import RegistryFormatError, parse_registry, scope_relation
from context_manager.pipeline import run_audit


def stale(result):
    return [c for c in result.comparison.candidates if c["kind"] == "stale_rule"]


def test_f04_01_confirmed_supersession(tmp_path):
    with policy_session(tmp_path) as (_, _, _, _, result, _):
        assert len(stale(result)) == 1
        candidate = stale(result)[0]
        assert candidate["facts"]["new_value"] == "/api/v2/users"
        assert list(candidate["facts"]["remaining_obligations"]) == ["tests"]
        assert list(candidate["scope"]["include_prefixes"]) == ["clients"]


@pytest.mark.parametrize("change,reason", [({"status": "draft"}, "status_draft"), ({"status": "withdrawn"}, "status_withdrawn"), ({"effective_from": "2027-01-01T00:00:00Z"}, "not_yet_effective"), ({"effective_until": "2026-11-05T00:00:00Z"}, "expired")])
def test_f04_02_inactive_decisions_never_retire_rule(tmp_path, change, reason):
    with policy_session(tmp_path, edit=lambda d: d["decisions"][0].update(change)) as (_, _, _, _, result, _):
        assert not stale(result)
        assert result.comparison.policy.inactive_decisions[0]["reason"] == reason
        assert not any(c["kind"] == "rule_conflict" for c in result.comparison.candidates)


def test_f04_03_partial_replacement_and_component_scopes(tmp_path):
    def edit(data):
        data["decisions"][0]["scope"]["include_prefixes"] = ["clients/new"]
        data["behavior_checks"] = []
    with policy_session(tmp_path, edit=edit) as (_, _, _, _, result, _):
        assert stale(result)[0]["suggested_action"] == "narrow_scope"
        assert list(stale(result)[0]["scope"]["include_prefixes"]) == ["clients/new"]
    assert scope_relation({"include_prefixes": ["clients"]}, {"include_prefixes": ["clients-old"]})["relation"] == "disjoint"
    assert scope_relation({"include_prefixes": ["."], "exclude_prefixes": ["legacy"]}, {"include_prefixes": ["legacy"]})["relation"] == "disjoint"


def add_decision(data, *, supersedes=()):
    second = json.loads(json.dumps(data["decisions"][0]))
    second.update(decision_id="decision-v3", supersedes=list(supersedes), confirmation_ref="confirm-v3")
    second["constraints"][0].update(constraint_id="endpoint-v3", value="/api/v3/users")
    data["decisions"].append(second)
    data["confirmations"].append({"confirmation_id": "confirm-v3", "kind": "decision_approval", "source_ref": second["source_ref"], "approved_items": [{"item_id": "decision-v3", "source_ref": second["source_ref"]}]})


def test_f04_04_conflicting_decisions_do_not_choose_by_time(tmp_path):
    def edit(data):
        data["rules"] = []
        data["decisions"][0]["supersedes"] = []
        data["behavior_checks"] = []
        add_decision(data)
    with policy_session(tmp_path, edit=edit) as (_, _, _, _, result, _):
        conflicts = [c for c in result.comparison.candidates if c["kind"] == "rule_conflict"]
        assert len(conflicts) == 1
        assert conflicts[0]["suggested_action"] == "ask_user"
        assert not stale(result)


@pytest.mark.parametrize("variant", ["cycle", "dangling", "stale", "not_approved"])
def test_f04_05_invalid_authority_isolated(tmp_path, variant):
    def edit(data):
        if variant == "cycle":
            add_decision(data, supersedes=["decision-v2"])
            data["decisions"][0]["supersedes"] += ["decision-v3"]
        elif variant == "dangling":
            data["decisions"][0]["supersedes"] += ["unknown-rule"]
        elif variant == "stale":
            data["confirmations"][1]["approved_items"][0]["source_ref"]["sha256"] = "f" * 64
        else:
            data["confirmations"][1]["approved_items"][0]["item_id"] = "unknown-rule"
    with policy_session(tmp_path, edit=edit) as (_, _, _, _, result, _):
        assert any(c["kind"] == "insufficient_evidence" for c in result.comparison.candidates)
        assert not any(f["kind"] == "stale_rule" and f["assessment"] == "supported" for f in result.diagnosis.findings)


def test_f04_06_compound_obligation_survives(tmp_path):
    with policy_session(tmp_path) as (_, project, _, _, result, _):
        target = result.comparison.edit_targets[stale(result)[0]["candidate_id"]]
        assert target["preserved_constraint_ids"] == ("tests",)
        assert b"keep tests." in result.repair_plan.diff
        assert "/api/v1/users" in (project / "AGENTS.md").read_text()


def test_f04_07_mtime_never_establishes_authority(tmp_path):
    request, project, _, registry = scenario(tmp_path)
    first = run_audit(request)
    os.utime(project / "AGENTS.md", (1, 1))
    os.utime(project / "docs/decision.md", (2000000000, 2000000000))
    second = run_audit(request)
    assert [c["facts"] for c in stale(first)] == [c["facts"] for c in stale(second)]


def test_f04_08_history_is_not_current(tmp_path):
    request, project, _, registry = scenario(tmp_path)
    original = (project / "AGENTS.md").read_text()
    changed = original.replace("# Rules", "# Historical rules")
    (project / "AGENTS.md").write_text(changed)
    def edit(data):
        for holder in [data["rules"][0], data["confirmations"][0], data["confirmations"][0]["approved_items"][0]]:
            holder["source_ref"]["sha256"] = digest(changed.encode())
    update_json(registry, edit)
    result = run_audit(request)
    assert not stale(result)
    assert any(i["code"] == "rule_not_current" for i in result.comparison.issues)


@pytest.mark.parametrize("confirmed,in_scope", [(True, True), (False, True), (True, False)])
def test_f04_09_check_requires_authority_and_scope(tmp_path, confirmed, in_scope):
    def edit(data):
        if not confirmed:
            data["behavior_checks"][0]["confirmation_ref"] = None
        if not in_scope:
            data["behavior_checks"][0]["scope"]["include_prefixes"] = ["unrelated"]
    with policy_session(tmp_path, edit=edit) as (_, _, _, _, result, _):
        assert bool(result.comparison.policy.effective_checks) is (confirmed and in_scope)
        if not (confirmed and in_scope):
            assert result.comparison.policy.suggested_checks


@pytest.mark.parametrize("complete", [True, False])
def test_f04_10_explicit_retirement_requires_complete_interpretation(tmp_path, complete):
    def edit(data):
        rule = data["rules"][0]
        data["decisions"][0]["constraints"] = []
        data["behavior_checks"] = []
        rule["constraints"] = [rule["constraints"][0]]
        rule["constraints"][0]["span"] = {"start": 2, "end": len(rule["text"]) if complete else 5}
    with policy_session(tmp_path, edit=edit) as (_, _, _, _, result, _):
        assert bool(stale(result)) is complete
        if complete:
            assert stale(result)[0]["suggested_action"] == "delete"


@pytest.mark.parametrize("data", [{"schema_version": 2}, {"schema_version": 1, "unknown": True}, {"schema_version": 1, "rules": [None]}, {"schema_version": 1, "rules": [{}]}])
def test_f04_11_registry_structure_rejected(data):
    with pytest.raises(RegistryFormatError):
        parse_registry(json.dumps(data).encode(), project_id="synthetic")


@pytest.mark.parametrize("value", [None, 1, "invalid"])
def test_f04_11_malformed_approval_list_is_contract_error(tmp_path, value):
    request, _, _, registry = scenario(tmp_path)
    raw = update_json(registry, lambda data: data["confirmations"][0].update(approved_items=value))
    with pytest.raises(RegistryFormatError) as error:
        parse_registry(json.dumps(raw).encode(), project_id=request.project_id)
    assert error.value.errors[0].path == "confirmations[0].approved_items"


def test_f04_11_overflow_number_is_not_valid_json_data():
    with pytest.raises(RegistryFormatError):
        parse_registry(b'{"schema_version":1,"rules":[],"unknown":1e9999}', project_id="synthetic")
