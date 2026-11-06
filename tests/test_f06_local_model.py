"""Real local semantics: seven synthetic cases, three independent runs each.

No fixed model responses. Outputs/transcripts stay in ignored local/verification.
Automatic results still require the recorded, per-output semantic review.
"""
from __future__ import annotations

import hashlib
import json
import re
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

import pytest

from context_manager.analyst import AnalystBudget, AnalystRequest, analyze_context
from context_manager.contracts import build_request, dump_object
from context_manager.evidence import EvidenceStore
from context_manager.model_transport import LocalChatTransport, load_model_config
from context_manager.rules import RuleSource, extract_rule_spans

FIXTURE = Path(__file__).parent / "fixtures/f06/cases.json"
CASES = json.loads(FIXTURE.read_text())["cases"]


class RecordingTransport:
    def __init__(self, inner, transcript):
        self.inner, self.config, self.transcript = inner, inner.config, transcript

    def with_limits(self, **limits):
        return RecordingTransport(self.inner.with_limits(**limits), self.transcript)

    def complete(self, messages, deadline):
        response = self.inner.complete(messages, deadline)
        with self.transcript.open("a") as handle:
            handle.write(json.dumps({"messages": messages, "response": response.content}, ensure_ascii=False) + "\n")
        return response


def run_case(case, directory, config):
    project = directory / "project"
    (project / "clients").mkdir(parents=True)
    (project / "legacy").mkdir()
    for path, content in case["files"].items():
        file = project / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(content, encoding="utf-8")
    request = build_request({"repo": str(project), "project_id": "semantic-synthetic", "out": str(directory / "out"), "scope": ["clients"], "semantic": "local", "model_config": str(Path("local/model.toml").resolve()), "as_of": "2026-11-05T00:00:00Z"}, cwd=Path.cwd())
    before = {p.relative_to(project).as_posix(): p.read_bytes() for p in project.rglob("*") if p.is_file()}
    with closing(EvidenceStore(project, directory / "out", "semantic")) as store:
        evidence = {path: store.capture_file(path, kind="rule_file" if path == "AGENTS.md" else "document") for path in sorted(case["files"])}
        sources = [RuleSource(evidence["AGENTS.md"].evidence_id, "AGENTS.md", "codex", {"include_prefixes": ["clients"], "exclude_prefixes": [], "harnesses": []}, "agents_md")]
        extraction = extract_rule_spans(sources, store)
        material = {"rules": dump_object(extraction.spans), "decisions": [], "confirmations": [], "checks": []}
        if "docs/decision.md" in evidence:
            ev = evidence["docs/decision.md"]
            ref = dump_object(store.make_ref(ev, 1, ev.line_count))
            approved = case["name"] != "draft-decision"
            material["decisions"] = [{"decision_id": "migration", "source_ref": ref, "status": "accepted" if approved else "draft", "confirmation_ref": "approval" if approved else None, "effective_from": "2026-10-01T00:00:00Z", "scope": dump_object(request.task_scope), "supersedes": [r["rule_id"] for r in material["rules"][:1]]}]
            if approved:
                material["confirmations"] = [{"confirmation_id": "approval", "kind": "decision_approval", "source_ref": ref, "approved_items": [{"item_id": "migration", "source_ref": ref}]}]
        events = []
        for path, content in case["files"].items():
            if not path.startswith("events/"):
                continue
            source = dump_object(store.make_ref(evidence[path], 1, evidence[path].line_count))
            document = json.loads(content)
            for record in document.get("events", [document]):
                events.append({
                    "event_id": record["event_id"], "task_id": record["task_id"],
                    "action": {"kind": record["action"], "target_path": record.get("target_path")},
                    "source_ref": source, "receipt_id": record.get("receipt_id"),
                })
        incident_material = {"synthetic": True, "proof_scope": "synthetic_case", "events": events, "receipts": [], "loads": [], "behaviors": []} if events else None
        goal = "diagnose" if incident_material else "audit"
        analysis = analyze_context(AnalystRequest("semantic", request, goal, material, incident_material), store, RecordingTransport(LocalChatTransport(config), directory / "transcript.jsonl"), AnalystBudget(max_wall_seconds=config.max_wall_seconds))
        raw = dump_object(analysis)
        raw["reference_paths"] = {e.evidence_id: e.relative_path for e in store.list_evidence()}
        raw["references_valid"] = all(store.validate_ref(ref, require_covered=True).ok for c in analysis.candidates for ref in [*c["support_refs"], *c["counter_refs"]])
        raw["project_unchanged"] = before == {p.relative_to(project).as_posix(): p.read_bytes() for p in project.rglob("*") if p.is_file()}
        (directory / "result.json").write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n")
        return raw


def case_passes(name, raw):
    if raw["status"] != "complete" or not raw["references_valid"] or not raw["project_unchanged"]:
        return False
    candidates = raw["candidates"]
    def paths(candidate):
        return {raw["reference_paths"][r["evidence_id"]] for r in candidate["support_refs"] + candidate["counter_refs"]}
    if name in ("api-wording", "compound-rule"):
        aligned = [c for c in candidates if c["kind"] in ("stale_rule", "rule_conflict") and {"AGENTS.md", "docs/decision.md"} <= paths(c)]
        if name == "api-wording":
            return bool(aligned)
        return all(c["suggested_action"] != "delete" for c in candidates) and any(
            c["suggested_action"] == "update"
            and all(fragment in c["claim"].lower() for fragment in ("/api/v1/users", "/api/v2/users", "test", "legacy"))
            for c in aligned
        )
    if name == "draft-decision":
        return not any(c["kind"] == "stale_rule" for c in candidates) and any(c["kind"] in ("insufficient_evidence", "rule_conflict") and c["suggested_action"] == "ask_user" for c in candidates)
    if name == "doc-example":
        return not any(c["kind"] in ("loaded_violation", "recurrence", "rule_conflict", "stale_rule") for c in candidates)
    if name == "log-wording":
        return any(c["kind"] == "recurrence" and {"events/first.json", "events/second.json"} <= paths(c) and not re.search(r"(?:two|2|两个)\s*(?:independent|独立)|independent\s+tasks?\s*[:=]\s*2", c["claim"], re.I) for c in candidates)
    if name == "retry-mention":
        return not any(c["kind"] in ("recurrence", "missing_load", "loaded_violation") or re.search(r"\b(?:is|was|were|are) not loaded\b|确认未加载", c["claim"], re.I) for c in candidates)
    return not any(c["kind"] in ("missing_load", "loaded_violation") or re.search(r"\b(?:is|was|were|are) not loaded\b|确认未加载", c["claim"], re.I) for c in candidates) and any(c["kind"] == "insufficient_evidence" and c["missing_evidence"] for c in candidates)


@pytest.mark.local_model
@pytest.mark.parametrize("case", CASES, ids=[case["name"] for case in CASES])
def test_f06_semantic_case(case, request, local_model_config):
    config = load_model_config(request.config.getoption("--local-model-config"))
    assert config.temperature == 0.0
    root = Path(__file__).resolve().parents[1] / "local/verification/f06"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    directory = root / (stamp + "-" + case["name"])
    directory.mkdir(parents=True)
    results = []
    for index in range(1, 4):
        result = run_case(case, directory / f"run-{index}", config)
        results.append(result)
    flags = [case_passes(case["name"], result) for result in results]
    summary = {"case": case["name"], "automatic_passes": flags, "fixture_sha256": hashlib.sha256(FIXTURE.read_bytes()).hexdigest(), "review": "pending"}
    (directory / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    assert all(result["references_valid"] and result["project_unchanged"] for result in results), directory
    minimum = 2 if case["name"] in ("api-wording", "compound-rule", "log-wording") else 3
    assert sum(flags) >= minimum, str(directory) + ": " + str(flags)
