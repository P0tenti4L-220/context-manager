"""F09: reopenable evidence, honest status, rendering safety and atomic writes."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil

import pytest

from acceptance_support import digest, policy_session, scenario, simple_project, tree_bytes
from context_manager.contracts import ContractError, dump_object, parse_object
from context_manager.pipeline import run_audit
from context_manager.reporting import ArtifactCollisionError, build_artifacts, write_run_artifacts


def altered(run, **changes):
    return parse_object("RunResult", dump_object(run) | changes)


def test_f09_01_mixed_findings_and_counter_refs_are_consistent(tmp_path):
    with policy_session(tmp_path, approval=False) as (_, _, _, _, result, _):
        findings = dump_object(result.diagnosis.findings)
        base = findings[0]
        findings += [base | {"finding_id": "hypothesis", "assessment": "hypothesis", "basis": "model_candidate", "suggested_action": "ask_user"}, base | {"finding_id": "unknown", "assessment": "unknown", "missing_evidence": ["receipt"], "suggested_action": "ask_user"}]
        run = altered(result.run_result, findings=findings)
        bundle = build_artifacts(run, result.evidence_catalog, result.repair_plan)
        document = json.loads(bundle.files["findings.json"])
        assert document["summary"] == {"total": 3, "supported": 1, "hypothesis": 1, "unknown": 1}
        assert bundle.run["findings_summary"] == document["summary"]
        assert b"hypothesis" in bundle.files["report.md"] and b"receipt" in bundle.files["report.md"]


def test_f09_02_semantic_off_is_complete_without_trace(tmp_path):
    with policy_session(tmp_path) as (_, _, _, _, result, _):
        bundle = build_artifacts(result.run_result, result.evidence_catalog, result.repair_plan)
        assert bundle.exit_code == 0 and "trace.jsonl" not in bundle.files
        assert "未请求" in bundle.files["report.md"].decode()


def test_f09_03_empty_rule_inventory_is_explicit(tmp_path):
    request, _ = simple_project(tmp_path)
    registry = tmp_path / "registry.json"
    registry.write_text('{"schema_version":1,"synthetic":true}')
    result = run_audit(replace(request, decision_registry=str(registry)))
    assert result.artifacts.exit_code == 0
    assert "没有可检查的规则" in (result.artifacts.run_dir / "report.md").read_text()


@pytest.mark.parametrize("approval", [True, False])
def test_f09_04_diff_and_manifest_match_actual_files(tmp_path, approval):
    with policy_session(tmp_path, approval=approval) as (_, _, _, _, result, _):
        root = result.artifacts.run_dir
        run = json.loads((root / "run.json").read_text())
        proposals = json.loads((root / "proposals.json").read_text())
        actual = {p.name for p in root.iterdir() if p.is_file()} - {"run.json"}
        assert {i["path"] for i in run["artifact_manifest"]} == actual
        for item in run["artifact_manifest"]:
            assert digest((root / item["path"]).read_bytes()) == item["sha256"]
        if approval:
            assert proposals["diff_sha256"] == digest((root / "proposal.diff").read_bytes())
        else:
            assert run["artifacts"]["diff"] is None and proposals["no_preview_reason"]
            assert not (root / "proposal.diff").exists()


def test_f09_05_untrusted_text_is_escaped_but_json_retains_original(tmp_path):
    with policy_session(tmp_path, approval=False) as (_, _, _, _, result, _):
        payload = '<script>alert(1)</script> [bad](javascript:evil)\u202e' + '中' * 2000
        findings = dump_object(result.diagnosis.findings)
        findings[0]["claim"] = payload
        run = altered(result.run_result, findings=findings)
        bundle = build_artifacts(run, result.evidence_catalog, result.repair_plan)
        report = bundle.files["report.md"].decode()
        assert "<script>" not in report and "\x00" not in report and "\x1b" not in report and "\u202e" not in report
        assert "[bad](javascript:" not in report
        assert json.loads(bundle.files["findings.json"])["findings"][0]["claim"] == payload
        from context_manager.reporting import safe_text
        assert "\x00" not in safe_text("\x00\x1b") and "\x1b" not in safe_text("\x00\x1b")


def copy_destination(result, output):
    directory = output / result.run_id
    directory.mkdir(parents=True)
    shutil.copytree(result.artifacts.run_dir / "evidence", directory / "evidence")
    return replace(result.evidence_catalog, root=directory)


def test_f09_06_same_run_concurrent_writers_never_mix(tmp_path):
    with policy_session(tmp_path) as (_, _, _, _, result, _):
        output = tmp_path / "concurrent"
        catalog = copy_destination(result, output)
        def write():
            try:
                write_run_artifacts(result.run_result, output, catalog, result.repair_plan)
                return "written"
            except ArtifactCollisionError:
                return "collision"
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(lambda _: write(), range(2)))
        assert sorted(outcomes) == ["collision", "written"]
        run = json.loads((catalog.root / "run.json").read_text())
        assert run["artifact_manifest_status"] == "final"
        assert all(digest((catalog.root / i["path"]).read_bytes()) == i["sha256"] for i in run["artifact_manifest"])


def test_f09_07_report_reopens_after_original_project_moved(tmp_path):
    request, project, _, _ = scenario(tmp_path)
    result = run_audit(request)
    project.rename(tmp_path / "moved-project")
    report = (result.artifacts.run_dir / "report.md").read_text()
    from urllib.parse import unquote
    for target in re.findall(r'\]\(([^)]+)\)', report):
        assert (result.artifacts.run_dir / unquote(target)).is_file()
    bundle = build_artifacts(result.run_result, result.evidence_catalog, result.repair_plan)
    assert bundle.exit_code == 0


@pytest.mark.parametrize("variant", ["empty_coverage", "corrupt_snapshot", "trace_missing"])
def test_f09_08_false_complete_is_downgraded(tmp_path, variant):
    with policy_session(tmp_path, approval=False) as (_, _, _, _, result, _):
        run = result.run_result
        if variant == "empty_coverage":
            run = altered(run, coverage=[])
        elif variant == "corrupt_snapshot":
            ref = run.findings[0].support_refs[0]
            location = result.evidence_catalog.location(ref.evidence_id)
            (result.evidence_catalog.root / location.artifact_path).write_bytes(b"wrong snapshot\n")
        else:
            raw = dump_object(run)
            raw["mode"]["semantic"] = "local"
            raw["parameters"]["model"] = {"name": "synthetic", "transport": "local_chat", "temperature": 0.0, "timeout_s": 5.0, "prompt_version": "test", "prompt_sha256": "a" * 64}
            raw["coverage"] = [c | {"status": "passed", "reason": None} if c["check_id"] == "f06.semantic" else c for c in raw["coverage"]]
            run = parse_object("RunResult", raw)
        bundle = build_artifacts(run, result.evidence_catalog, result.repair_plan)
        assert bundle.exit_code == 3 and bundle.run["requested_status"] == "complete"
        assert bundle.run["status_reason"] and bundle.run["errors"]


def test_f09_09_fixed_clock_build_is_deterministic(tmp_path):
    with policy_session(tmp_path) as (_, _, _, _, result, _):
        clock = lambda: datetime(2026, 11, 5, tzinfo=timezone.utc)
        first = build_artifacts(result.run_result, result.evidence_catalog, result.repair_plan, clock=clock)
        second = build_artifacts(result.run_result, result.evidence_catalog, result.repair_plan, clock=clock)
        assert first.files == second.files and first.run == second.run


@pytest.mark.parametrize("run_id", ["../outside", "/absolute", "x\\y", "x" * 65])
def test_f09_10_unsafe_run_identity_rejected_before_writes(tmp_path, run_id):
    with policy_session(tmp_path, approval=False) as (_, _, _, _, result, _):
        raw = dump_object(result.run_result)
        raw["run_id"] = run_id
        with pytest.raises(ContractError):
            build_artifacts(raw, result.evidence_catalog, result.repair_plan)


@pytest.mark.parametrize("status,code", [("complete", 0), ("partial", 3), ("failed", 1)])
def test_f09_11_status_mapping_and_json_bytes(tmp_path, status, code):
    with policy_session(tmp_path) as (_, _, _, _, result, _):
        bundle = build_artifacts(altered(result.run_result, status=status), result.evidence_catalog, result.repair_plan)
        assert bundle.exit_code == code
        for name, data in bundle.files.items():
            if name.endswith(".json"):
                assert data.endswith(b"\n") and b"\r\n" not in data
                json.loads(data)


def test_f09_12_blocked_repair_does_not_fail_audit(tmp_path):
    with policy_session(tmp_path, approval=False) as (_, _, _, _, result, _):
        bundle = build_artifacts(result.run_result, result.evidence_catalog, result.repair_plan)
        assert bundle.exit_code == 0
        proposal = json.loads(bundle.files["proposals.json"])
        assert proposal["rejections"] and b"Rejected edit" in bundle.files["report.md"]
        assert not any(c["check_id"].startswith("repair") for c in json.loads(bundle.files["checks.json"])["checks"])
