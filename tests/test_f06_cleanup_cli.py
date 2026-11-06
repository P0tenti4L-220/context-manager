"""Rule cleanup through the official audit entry point and a real local model."""
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil

import pytest

from acceptance_support import simple_project, tree_bytes
from context_manager.evidence import EvidenceStore
from test_f10_cli import cli_args, process, read_run


FIXTURE = Path(__file__).parent / "fixtures/f06/cleanup.json"
CASES = json.loads(FIXTURE.read_text())["cases"]


@pytest.mark.local_model
@pytest.mark.parametrize("case", CASES, ids=[case["name"] for case in CASES])
def test_instruction_cleanup_in_audit(case, tmp_path, request, local_model_config):
    registry = tmp_path / "decisions.json"
    registry.write_text('{"schema_version":1}\n')
    audit, project = simple_project(tmp_path, {"AGENTS.md": case["text"]}, decisions=str(registry))
    before = tree_bytes(project)
    config = str(Path(request.config.getoption("--local-model-config")).resolve())
    run = process([*cli_args(audit), "--semantic", "local", "--model-config", config])
    root, manifest = read_run(audit)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    destination = Path(__file__).resolve().parents[1] / "local/verification/f06" / f"audit-{stamp}-{case['name']}"
    shutil.copytree(root, destination)
    (destination / "cli-output.json").write_text(json.dumps({"exit_code": run.returncode, "stdout": run.stdout, "stderr": run.stderr}))

    assert run.returncode == manifest["exit_code"] == 0, str(destination)
    assert manifest["status"] == "complete"
    assert manifest["integrity"]["evidence_links"] == "passed"
    assert tree_bytes(project) == before
    assert not (root / "proposal.diff").exists()
    findings = json.loads((root / "findings.json").read_text())["findings"]
    proposals = json.loads((root / "proposals.json").read_text())
    assert not proposals["proposals"]
    if case["expected"] is None:
        assert not findings and not proposals["advisories"], str(destination)
        return

    expected = case["expected"]
    assert len(findings) == 1, (str(destination), findings)
    finding = findings[0]
    assert (finding["kind"], finding["suggested_action"]) == (expected["kind"], expected["action"]), str(destination)
    assert (finding["assessment"], finding["basis"]) == ("hypothesis", "model_candidate")
    source_lines = case["text"].splitlines()
    with closing(EvidenceStore.open_existing(root)) as store:
        lines = set()
        for ref in finding["support_refs"]:
            assert store.get(ref["evidence_id"]).relative_path == "AGENTS.md"
            assert store.validate_ref(ref, require_covered=True).ok
            lines.update(number for number in range(ref["line_start"], ref["line_end"] + 1)
                         if source_lines[number - 1].strip() and not source_lines[number - 1].startswith("#"))
    assert lines == set(expected["lines"]), str(destination)
    if expected["action"] == "relocate":
        advisory, = proposals["advisories"]
        assert advisory["kind"] == "migrate_clause" and advisory["requires_confirmation"]
        assert advisory["finding_ids"] == [finding["finding_id"]]
        assert advisory["basis_refs"] == finding["support_refs"]
        assert advisory["summary"] == finding["claim"]
        claim, rationale = finding["claim"].split("\n", maxsplit=1)
        assert all(fragment in claim for fragment in ("TASK-42", "closed yesterday", "send the review link")), str(destination)
        assert rationale.strip(), str(destination)
