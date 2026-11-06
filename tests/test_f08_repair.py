"""F08: exact approvals, byte-preserving diffs and isolated real Git checks."""
from dataclasses import replace
import copy
import json
from pathlib import Path
import subprocess

import pytest

from acceptance_support import digest, policy_session, scenario, tree_bytes, update_json
from context_manager.contracts import dump_object
from context_manager.evidence import EvidenceStore
from context_manager.intents import build_edit_intents
from context_manager.pipeline import run_audit
from context_manager.repair import plan_repairs


def test_f08_01_one_approved_minimal_diff(tmp_path):
    with policy_session(tmp_path) as (_, project, _, _, result, _):
        assert len([p for p in result.repair_plan.proposals if p["status"] == "preview_ready"]) == 1
        assert result.repair_plan.apply_check["exit_code"] == 0
        assert result.repair_plan.apply_check["applied_in_temp_copy"]
        assert result.repair_plan.hunks[0]["finding_ids"]
        assert b"/api/v2/users" in result.repair_plan.diff
        assert b"/api/v1/users" in (project / "AGENTS.md").read_bytes()


def test_f08_02_independent_obligation_cannot_be_removed(tmp_path):
    with policy_session(tmp_path) as (_, _, _, _, result, store):
        intents = dump_object(build_edit_intents(result.comparison, result.diagnosis))
        intents[0]["replacement_text"] = "- New clients use `/api/v2/users`."
        intents[0]["preserved_fragments"] = []
        plan = plan_repairs(result.diagnosis.findings, intents, result.comparison.policy, store)
        assert plan.diff is None
        assert not any(p["status"] == "preview_ready" for p in plan.proposals)


def test_f08_03_exact_approved_single_rule_retirement(tmp_path):
    request, project, _, registry = scenario(tmp_path)
    line = "- Use /api/v1/users."
    content = "# Rules\n\n" + line + "\n"
    (project / "AGENTS.md").write_text(content)
    def edit(data):
        rule = data["rules"][0]
        for holder in (rule, data["confirmations"][0], data["confirmations"][0]["approved_items"][0]):
            holder["source_ref"]["sha256"] = digest(content.encode())
        rule.update(text=line, text_sha256=digest((line + "\n").encode()))
        rule["constraints"] = [rule["constraints"][0]]
        rule["constraints"][0]["span"] = {"start": 2, "end": len(line)}
        data["decisions"][0]["constraints"] = []
        data["behavior_checks"] = []
        approval = data["confirmations"][1]["edit_approvals"][0]
        approval.update(operation="delete_span", old_text_sha256=digest((line + "\n").encode()), replacement_sha256=digest(b""))
    update_json(registry, edit)
    result = run_audit(request)
    assert result.repair_plan.diff and result.repair_plan.apply_check["exit_code"] == 0
    assert result.repair_plan.proposals[0]["edits"][0]["operation"] == "delete_span"
    assert (project / "AGENTS.md").read_text() == content


def test_f08_04_unconfirmed_findings_cannot_reuse_approved_intent(tmp_path):
    with policy_session(tmp_path) as (_, _, _, _, result, store):
        findings = dump_object(result.diagnosis.findings)
        findings[0]["assessment"] = "hypothesis"
        intents = build_edit_intents(result.comparison, result.diagnosis)
        plan = plan_repairs(findings, intents, result.comparison.policy, store)
        assert plan.diff is None
        assert any(p["status"] == "needs_confirmation" for p in plan.proposals)


@pytest.mark.parametrize("change", ["current", "span"])
def test_f08_05_changed_bytes_or_span_block_preview(tmp_path, change):
    with policy_session(tmp_path) as (_, project, _, _, result, store):
        intents = dump_object(build_edit_intents(result.comparison, result.diagnosis))
        if change == "current":
            (project / "AGENTS.md").write_text("# changed by another process\n")
        else:
            intents[0]["old_text_sha256"] = "f" * 64
        before = tree_bytes(project)
        plan = plan_repairs(result.diagnosis.findings, intents, result.comparison.policy, store)
        assert plan.diff is None and any(p["status"] == "blocked" for p in plan.proposals)
        assert tree_bytes(project) == before


@pytest.mark.parametrize("path", ["../outside", "/tmp/outside", "clients/app.py", "docs/decision.md"])
def test_f08_06_only_registered_instruction_files_editable(tmp_path, path):
    with policy_session(tmp_path) as (_, project, _, _, result, store):
        intents = dump_object(build_edit_intents(result.comparison, result.diagnosis))
        intents[0]["target_path"] = path
        before = tree_bytes(project)
        plan = plan_repairs(result.diagnosis.findings, intents, result.comparison.policy, store)
        assert not plan.diff and not plan.checks
        assert tree_bytes(project) == before


def test_f08_07_duplicate_intents_are_one_edit(tmp_path):
    with policy_session(tmp_path) as (_, _, _, _, result, store):
        intents = dump_object(build_edit_intents(result.comparison, result.diagnosis))
        plan = plan_repairs(result.diagnosis.findings, intents * 2, result.comparison.policy, store)
        assert plan.diff_sha256 == result.repair_plan.diff_sha256
        assert len(next(p for p in plan.proposals if p["status"] == "preview_ready")["edits"]) == 1


def test_f08_07_two_identical_clauses_at_distinct_lines_edit_only_those_lines(tmp_path):
    request, project, _, registry = scenario(tmp_path)
    data = json.loads(registry.read_text())
    line = data["rules"][0]["text"]
    content = (project / "AGENTS.md").read_text() + line + "\n"
    (project / "AGENTS.md").write_text(content)
    def edit(data):
        for holder in (data["rules"][0], data["confirmations"][0], data["confirmations"][0]["approved_items"][0]):
            holder["source_ref"]["sha256"] = digest(content.encode())
        rule = copy.deepcopy(data["rules"][0])
        rule.update(rule_id="second-rule", confirmation_ref="confirm-second")
        rule["source_ref"].update(line_start=4, line_end=4)
        data["rules"].append(rule)
        data["confirmations"].append({"confirmation_id": "confirm-second", "kind": "rule_interpretation", "source_ref": rule["source_ref"], "approved_items": [{"item_id": "second-rule", "source_ref": rule["source_ref"]}]})
        data["decisions"][0]["supersedes"].append("second-rule")
        approval = copy.deepcopy(data["confirmations"][1]["edit_approvals"][0])
        approval.update(line_start=4, line_end=4, rule_ids=["second-rule"])
        data["confirmations"][1]["edit_approvals"].append(approval)
    update_json(registry, edit)
    result = run_audit(request)
    preview = next(p for p in result.repair_plan.proposals if p["status"] == "preview_ready")
    assert len(preview["edits"]) == 2
    assert {e["line_start"] for e in preview["edits"]} == {3, 4}
    assert result.repair_plan.apply_check["applied_in_temp_copy"]
    assert (project / "AGENTS.md").read_text() == content


def test_f08_07_conflicting_approved_replacements_cannot_overlap(tmp_path):
    with policy_session(tmp_path) as (_, _, _, _, result, store):
        intents = dump_object(build_edit_intents(result.comparison, result.diagnosis))
        alternate = copy.deepcopy(intents[0])
        alternate["replacement_text"] += " Extra approved note."
        confirmations = dump_object(result.comparison.policy.registry.confirmations)
        approval = copy.deepcopy(confirmations[1]["edit_approvals"][0])
        approval["replacement_sha256"] = digest((alternate["replacement_text"] + "\n").encode())
        confirmations[1]["edit_approvals"].append(approval)
        policy = replace(result.comparison.policy, registry=replace(result.comparison.policy.registry, confirmations=confirmations).freeze()).freeze()
        plan = plan_repairs(result.diagnosis.findings, [*intents, alternate], policy, store)
        assert not plan.diff
        assert any(q["prompt"] == "overlap_conflict" for p in plan.proposals for q in p["unresolved_questions"])


@pytest.mark.parametrize("ending", [b"\r\n", b""])
def test_f08_08_newline_encoding_and_exact_application(tmp_path, ending):
    # The first-line BOM regression also verifies real Git application.
    from test_f03_f10_hardening import test_approved_first_line_edit_preserves_bom_and_line_ending
    test_approved_first_line_edit_preserves_bom_and_line_ending(tmp_path, ending)


def test_f08_08_unicode_and_space_path_is_applicable(tmp_path):
    from context_manager.contracts import parse_object
    request, project, _, registry = scenario(tmp_path)
    directory = "clients/中文 目录"
    target = directory + "/AGENTS.md"
    (project / directory).mkdir()
    (project / "AGENTS.md").rename(project / target)
    def edit(data):
        def visit(item):
            if isinstance(item, dict):
                if item.get("relative_path") == "AGENTS.md":
                    item["relative_path"] = target
                if item.get("target_path") == "AGENTS.md":
                    item["target_path"] = target
                for value in item.values():
                    visit(value)
            elif isinstance(item, list):
                for value in item:
                    visit(value)
        visit(data)
        data["rules"][0]["scope"]["include_prefixes"] = [directory]
    update_json(registry, edit)
    request = replace(request, task_scope=parse_object("Scope", {"include_prefixes": [directory]}))
    result = run_audit(request)
    assert result.repair_plan.diff and result.repair_plan.apply_check["exit_code"] == 0
    assert target.encode() in result.repair_plan.diff


def test_f08_09_project_untouched_and_diff_deterministic(tmp_path):
    request, project, _, _ = scenario(tmp_path)
    before = tree_bytes(project)
    first, second = run_audit(request), run_audit(request)
    assert first.repair_plan.diff_sha256 == second.repair_plan.diff_sha256
    assert tree_bytes(project) == before
    assert not (project / ".git").exists()


@pytest.mark.parametrize("unavailable", [False, True])
def test_f08_10_git_failures_preserve_real_exit_code(tmp_path, monkeypatch, unavailable):
    with policy_session(tmp_path) as (_, project, _, _, result, store):
        before = tree_bytes(project)
        def fail(*args, **kwargs):
            if unavailable:
                raise FileNotFoundError("synthetic unavailable Git")
            return subprocess.CompletedProcess(args[0], 17, b"", b"synthetic Git failure")
        monkeypatch.setattr(subprocess, "run", fail)
        plan = plan_repairs(result.diagnosis.findings, build_edit_intents(result.comparison, result.diagnosis), result.comparison.policy, store)
        assert plan.diff is None and plan.apply_check["exit_code"] == (None if unavailable else 17)
        assert tree_bytes(project) == before


@pytest.mark.parametrize("variant", ["missing", "expired", "mismatch"])
def test_f08_11_edit_approval_is_exact_and_current(tmp_path, variant):
    def edit(data):
        approval = data["confirmations"][1]["edit_approvals"][0]
        if variant == "missing":
            data["confirmations"][1]["edit_approvals"] = []
        elif variant == "expired":
            approval["valid_until"] = "2026-11-05T00:00:00Z"
        else:
            approval["replacement_sha256"] = "f" * 64
    with policy_session(tmp_path, edit=edit) as (_, _, _, _, result, _):
        assert not result.repair_plan.diff
        assert any(p["status"] == "needs_confirmation" for p in result.repair_plan.proposals)


@pytest.mark.parametrize("action", ["fix_loading", "fix_retrieval", "add_check", "relocate"])
def test_f08_12_non_instruction_fixes_are_advisories(tmp_path, action):
    with policy_session(tmp_path) as (_, _, _, _, result, store):
        finding = dump_object(result.diagnosis.findings[0])
        finding.update(kind="cleanup_candidate", suggested_action=action)
        plan = plan_repairs([finding], [], result.comparison.policy, store)
        assert plan.advisories and not plan.diff
        assert plan.advisories[0]["finding_ids"] == (finding["finding_id"],)
        assert plan.advisories[0]["kind"] == ("migrate_clause" if action == "relocate" else action)
        assert plan.advisories[0]["requires_confirmation"]
