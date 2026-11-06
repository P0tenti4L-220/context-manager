"""F10: official process entry, coverage, concurrency and no project writes."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
import os
from pathlib import Path
import socket
import shutil
import subprocess
import sys

import pytest

from acceptance_support import scenario, simple_project, tree_bytes, update_json
from context_manager.cli import main
from context_manager.contracts import build_request, dump_object
from context_manager.pipeline import run_audit

SOURCE = Path(__file__).resolve().parents[1] / "src"


def cli_args(request, *, command="audit", case=None):
    args = [command, "--repo", request.repo, "--project-id", request.project_id, "--out", request.out, "--as-of", request.as_of]
    for prefix in request.task_scope.include_prefixes:
        args += ["--scope", prefix]
    if request.decision_registry:
        args += ["--decisions", request.decision_registry]
    if case:
        args += ["--case", str(case)]
    return args


def process(args, cwd=None, executable=None):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(SOURCE)
    return subprocess.run([executable or sys.executable, "-m", "context_manager", *args], cwd=cwd, env=env, capture_output=True, text=True, timeout=120)


def read_run(request):
    paths = list(Path(request.out).glob("*/run.json"))
    assert len(paths) == 1
    return paths[0].parent, json.loads(paths[0].read_text())


def formal_fixture(tmp_path):
    source = Path(__file__).parent / "fixtures/stale_users_api"
    root = tmp_path / "fixture"
    shutil.copytree(source, root)
    project, decisions, case = root / "project", root / "decisions.json", root / "incidents.json"
    request = build_request({"repo": str(project), "project_id": "synthetic", "out": str(tmp_path / "out"), "decisions": str(decisions), "scope": ["clients"], "as_of": "2026-11-05T00:00:00Z"}, cwd=tmp_path)
    return request, project, case


def test_f10_01_official_audit_produces_only_targeted_diff(tmp_path):
    request, project, _ = formal_fixture(tmp_path)
    before = tree_bytes(project)
    run = process(cli_args(request))
    assert run.returncode == 0, run.stderr
    root, manifest = read_run(request)
    findings = json.loads((root / "findings.json").read_text())["findings"]
    assert any(f["kind"] == "stale_rule" and f["assessment"] == "supported" for f in findings)
    patch = (root / "proposal.diff").read_text()
    assert "+- New clients must use `/api/v2/users`; keep tests." in patch
    assert not any(line.startswith(("+", "-")) and "Legacy compatibility clients" in line for line in patch.splitlines())
    assert tree_bytes(project) == before


def test_f10_02_official_diagnose_has_two_task_recurrence(tmp_path):
    request, _, case = formal_fixture(tmp_path)
    run = process(cli_args(request, command="diagnose", case=case))
    assert run.returncode == 0, run.stderr
    root, manifest = read_run(request)
    incidents = json.loads((root / "incidents.json").read_text())
    assert incidents["counts"]["confirmed_independent_tasks"] == 2
    assert incidents["counts"]["duplicate_event_count"] == 1
    assert all(load["state"] == "observed_loaded" for load in incidents["loads"])
    assert manifest["mode"]["semantic"] == "off"


@pytest.mark.parametrize("variant,code", [("receipt", 3), ("draft", 0)])
def test_f10_03_missing_receipts_and_draft_decisions(tmp_path, variant, code):
    request, project, case, registry = scenario(tmp_path)
    before = tree_bytes(project)
    if variant == "receipt":
        update_json(case, lambda d: d.update(receipts=[]))
    else:
        update_json(registry, lambda d: d["decisions"][0].update(status="draft"))
    run = process(cli_args(request, command="diagnose", case=case))
    assert run.returncode == code
    root, manifest = read_run(request)
    if variant == "draft":
        assert not (root / "proposal.diff").exists()
        assert "draft" in (root / "report.md").read_text()
    assert tree_bytes(project) == before


def test_f10_04_default_off_never_connects(tmp_path, monkeypatch):
    request, _, _, _ = scenario(tmp_path)
    def no_connection(*args, **kwargs):
        pytest.fail("semantic off attempted to create a socket")
    monkeypatch.setattr(socket, "socket", no_connection)
    result = run_audit(request)
    assert result.artifacts.exit_code == 0
    assert result.analyst_result is None
    assert not (result.artifacts.run_dir / "trace.jsonl").exists()


def test_f10_05_unavailable_model_retains_static_results(tmp_path):
    request, _, _, _ = scenario(tmp_path)
    config = tmp_path / "model.toml"
    config.write_text('[model]\nbase_url="http://127.0.0.1:1/v1"\nmodel="synthetic"\ntimeout_seconds=1\n')
    run = process([*cli_args(request), "--semantic", "local", "--model-config", str(config)])
    assert run.returncode == 3, run.stderr
    root, manifest = read_run(request)
    assert (root / "proposal.diff").exists() and (root / "trace.jsonl").exists()
    assert manifest["status"] == "partial"
    assert "transport_failure" in (root / "trace.jsonl").read_text()


@pytest.mark.local_model
def test_f10_06_real_local_model_formal_cli(tmp_path, request, local_model_config):
    audit, project, _, _ = scenario(tmp_path)
    before = tree_bytes(project)
    config = str(Path(request.config.getoption("--local-model-config")).resolve())
    run = process([*cli_args(audit), "--semantic", "local", "--model-config", config])
    root, manifest = read_run(audit)
    # Save the actual official artifacts; tmp_path will eventually be cleaned.
    import shutil
    from datetime import datetime, timezone
    destination = Path(__file__).resolve().parents[1] / "local/verification/f01-f10-final-20261105" / ("formal-local-" + datetime.now(timezone.utc).strftime("%H%M%S%f"))
    shutil.copytree(root, destination)
    (destination / "cli-output.json").write_text(json.dumps({"exit_code": run.returncode, "stdout": run.stdout, "stderr": run.stderr}))
    assert run.returncode == 0, str(destination) + ": " + run.stderr
    assert manifest["status"] == "complete"
    findings = json.loads((root / "findings.json").read_text())["findings"]
    assert any(f["basis"] == "model_candidate" for f in findings)
    assert all(f["assessment"] in ("hypothesis", "unknown") for f in findings if f["basis"] == "model_candidate")
    assert tree_bytes(project) == before


@pytest.mark.parametrize("variant", ["missing_case", "bad_time", "inside_out", "local_missing", "invalid_registry", "invalid_case"])
def test_f10_07_preflight_errors_have_no_output_side_effects(tmp_path, variant):
    request, project, case, registry = scenario(tmp_path)
    args = cli_args(request)
    if variant == "missing_case":
        args[0] = "diagnose"
    elif variant == "bad_time":
        args[args.index("--as-of") + 1] = "SENTINEL-invalid-time"
    elif variant == "inside_out":
        args[args.index("--out") + 1] = str(project / "out")
    elif variant == "local_missing":
        args += ["--semantic", "local"]
    elif variant == "invalid_registry":
        registry.write_text('{"schema_version":2,"secret":"SENTINEL"}')
    else:
        args[0] = "diagnose"
        args += ["--case", str(case)]
        case.write_text('{"synthetic":false,"secret":"SENTINEL"}')
    before = tree_bytes(project)
    run = process(args)
    assert run.returncode == 2
    assert "SENTINEL" not in run.stdout + run.stderr
    assert not Path(request.out).exists() and not (project / "out").exists()
    assert tree_bytes(project) == before


def test_f10_09_multiple_runs_leave_project_tree_unchanged(tmp_path):
    request, project, case, _ = scenario(tmp_path)
    before = tree_bytes(project)
    for args in (cli_args(request), cli_args(request, command="diagnose", case=case)):
        assert process(args).returncode == 0
    assert tree_bytes(project) == before


def test_f10_10_entrypoints_work_from_other_directory(tmp_path):
    request, project, _, _ = scenario(tmp_path)
    cwd = tmp_path / "elsewhere"
    cwd.mkdir()
    assert process(["--help"], cwd).returncode == 0
    assert process(["--version"], cwd).returncode == 0
    assert process(cli_args(request), cwd).returncode == 0
    assert not list(cwd.iterdir())


def test_f10_11_exit_code_matches_every_run_artifact(tmp_path):
    request, _, _, _ = scenario(tmp_path)
    run = process(cli_args(request))
    root, manifest = read_run(request)
    assert run.returncode == manifest["exit_code"] == 0
    assert manifest["status"] in (root / "report.md").read_text()
    coverage = json.loads((root / "checks.json").read_text())["checks"]
    assert all(c["reason"] for c in coverage if c["status"] != "passed")


def test_f10_12_parallel_processes_get_isolated_directories(tmp_path):
    request, project, _, _ = scenario(tmp_path)
    before = tree_bytes(project)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: process(cli_args(request)), range(2)))
    assert [r.returncode for r in results] == [0, 0]
    roots = list(Path(request.out).iterdir())
    assert len(roots) == 2
    assert len({json.loads((root / "run.json").read_text())["run_id"] for root in roots}) == 2
    assert (roots[0] / "proposal.diff").read_bytes() == (roots[1] / "proposal.diff").read_bytes()
    assert tree_bytes(project) == before


def test_f10_13_no_registry_is_honest_gap(tmp_path):
    request, _ = simple_project(tmp_path, {"AGENTS.md": "- Keep tests.\n"})
    run = process(cli_args(request))
    assert run.returncode == 3
    root, manifest = read_run(request)
    assert any(c["reason"] == "registry_not_provided" for c in json.loads((root / "checks.json").read_text())["checks"])
    assert not (root / "proposal.diff").exists()


def test_f10_output_artifacts_do_not_disclose_absolute_project_path(tmp_path):
    request, project, _, _ = scenario(tmp_path)
    result = run_audit(request)
    for path in result.artifacts.run_dir.rglob("*"):
        if path.is_file():
            assert str(project).encode() not in path.read_bytes(), str(path)
    from context_manager.evidence import EvidenceStore
    with __import__("contextlib").closing(EvidenceStore.open_existing(result.artifacts.run_dir)) as store:
        assert store.list_evidence()
    with __import__("contextlib").closing(EvidenceStore.open_existing(result.artifacts.run_dir, project_root=project)) as store:
        assert store.capture_file("AGENTS.md")
