"""F10-08: offline wheel, isolated install, both actual entrypoints."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

from acceptance_support import scenario
from test_f10_cli import cli_args, process


def test_f10_08_offline_wheel_install_and_source_equivalence(tmp_path):
    repo = Path(__file__).resolve().parents[1]
    source = tmp_path / "source"
    source.mkdir()
    shutil.copy(repo / "pyproject.toml", source)
    shutil.copy(repo / "README.md", source)
    shutil.copytree(repo / "src", source / "src", ignore=shutil.ignore_patterns("__pycache__", "*.egg-info"))
    wheel_dir = tmp_path / "wheels"
    env = os.environ.copy()
    env.update(PIP_NO_INDEX="1", PYTHONPATH="")
    build = subprocess.run([sys.executable, "-m", "pip", "wheel", "--no-deps", "--no-build-isolation", "-w", str(wheel_dir), str(source)], env=env, capture_output=True, text=True)
    assert build.returncode == 0, build.stderr
    wheel = next(wheel_dir.glob("*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        prompt_files = ("analyst.md", "incident.md", "instruction_review.md", "instruction_pairs.md")
        assert {"context_manager/prompts/" + filename for filename in prompt_files} <= set(names)
        assert not any(name.startswith(("local/", "tests/", "examples/")) or "__pycache__" in name or "model.toml" in name for name in names)
        for filename in prompt_files:
            path = "context_manager/prompts/" + filename
            assert archive.read(path) == (repo / "src" / path).read_bytes()
    venv = tmp_path / "installed"
    created = subprocess.run([sys.executable, "-m", "venv", str(venv)], capture_output=True, text=True)
    assert created.returncode == 0, created.stderr
    installed = subprocess.run([str(venv / "bin/python"), "-m", "pip", "install", "--no-index", "--no-deps", str(wheel)], env=env, capture_output=True, text=True)
    assert installed.returncode == 0, installed.stderr
    for command in ([str(venv / "bin/context-manager"), "--help"], [str(venv / "bin/python"), "-m", "context_manager", "--version"]):
        result = subprocess.run(command, cwd=tmp_path, env=env, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    request, _, _, _ = scenario(case_dir)
    source_run = process(cli_args(request))
    assert source_run.returncode == 0
    installed_out = tmp_path / "installed-output"
    args = cli_args(request)
    args[args.index("--out") + 1] = str(installed_out)
    result = subprocess.run([str(venv / "bin/context-manager"), *args], cwd=tmp_path, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    first = next(Path(request.out).iterdir())
    second = next(installed_out.iterdir())
    assert (first / "proposal.diff").read_bytes() == (second / "proposal.diff").read_bytes()
    def normalize(value):
        if isinstance(value, dict):
            return {k: normalize(v) for k, v in value.items() if k not in ("evidence_id", "finding_id", "proposal_id", "question_id", "run_id", "generated_at", "created_at", "elapsed_ms", "checked_at", "read_id", "snapshot_id", "captured_at", "artifact_refs", "related_finding_ids", "support_refs", "counter_refs", "finding_ids", "checks", "apply_check", "hunks", "repair_checks", "base_files", "rejections", "unresolved_questions", "summary")}
        if isinstance(value, list):
            return [normalize(v) for v in value]
        return value
    for filename in ("findings.json", "checks.json"):
        assert normalize(json.loads((first / filename).read_text())) == normalize(json.loads((second / filename).read_text()))
    # Persist build/install evidence outside pytest's temporary cleanup.
    record = repo / "local/verification/f01-f10-final-20261105/packaging"
    record.mkdir(exist_ok=True)
    shutil.copy(wheel, record / wheel.name)
    (record / "build.log").write_text(build.stdout + build.stderr)
    (record / "install.log").write_text(installed.stdout + installed.stderr)
    (record / "wheel.json").write_text(json.dumps({"filename": wheel.name, "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(), "byte_length": wheel.stat().st_size, "build_exit_code": build.returncode, "install_exit_code": installed.returncode, "entrypoints": "passed", "source_equivalence": "passed"}, indent=2) + "\n")
