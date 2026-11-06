"""Synthetic acceptance helpers; assertions live in individual feature tests."""
from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

from context_manager.contracts import build_request, dump_object
from context_manager.evidence import EvidenceStore
from context_manager.pipeline import run_audit
from context_manager.rules import extract_rule_spans
from context_manager.adapters import rule_sources
from test_workflow import digest, scenario, tree_bytes


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def update_json(path, edit):
    value = json.loads(path.read_text())
    edit(value)
    write_json(path, value)
    return value


def profile():
    return {"schema_version": 1, "profile_id": "synthetic", "profile_version": "1", "source": "synthetic", "harnesses": [
        {"id": "codex", "candidate_files": [{"filename": "AGENTS.override.md", "role": "override"}, {"filename": "AGENTS.md", "role": "root"}], "candidate_order": ["AGENTS.override.md", "AGENTS.md"], "known_versions": ["0.synthetic"], "one_per_level": True, "parent_child": "nearest_first", "import_syntax": ["at_path", "markdown_link"], "rules_dirs": [], "max_file_bytes": 1048576},
        {"id": "claude_code", "candidate_files": [{"filename": "CLAUDE.md", "role": "root"}], "candidate_order": ["CLAUDE.md"], "known_versions": ["0.synthetic"], "one_per_level": True, "parent_child": "root_first", "import_syntax": ["at_path", "markdown_link"], "rules_dirs": [".claude/rules"], "max_file_bytes": 1048576}
    ]}


def simple_project(tmp_path, files=None, **parameters):
    project = tmp_path / "project"
    project.mkdir(parents=True)
    for path, value in (files or {}).items():
        target = project / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(value if isinstance(value, bytes) else value.encode())
    request = build_request({"repo": str(project), "project_id": "synthetic", "out": str(tmp_path / "out"), "as_of": "2026-11-05T00:00:00Z", **parameters}, cwd=tmp_path)
    return request, project


@contextmanager
def policy_session(tmp_path, *, edit=None, **options):
    request, project, case, registry = scenario(tmp_path, **options)
    if edit:
        update_json(registry, edit)
    result = run_audit(request)
    store = EvidenceStore(project, request.out, result.run_id)
    try:
        yield request, project, case, registry, result, store
    finally:
        store.close()


def model_candidate(result, **overrides):
    rule = result.comparison.policy.current_rules[0]
    return {"candidate_id": "model_issue", "kind": "stale_rule", "claim": "The old instruction may conflict with the newer requirement.", "scope": dump_object(rule["scope"]), "support_refs": [dump_object(rule["source_ref"])], "counter_refs": [], "missing_evidence": [], "suggested_action": "ask_user", **overrides}


def read_as_model(store, references):
    records = []
    for reference in references:
        read = store.read_evidence(reference, caller="analyst")
        records.append({key: getattr(read, key) for key in ("read_id", "evidence_id", "sha256", "line_start", "line_end", "text_bytes", "span_sha256")})
    return records


def current_rule_scenario(tmp_path):
    request, project, case, registry = scenario(tmp_path)
    original = (project / "AGENTS.md").read_bytes()
    new = original.replace(b"/api/v1/users", b"/api/v2/users")
    (project / "AGENTS.md").write_bytes(new)
    def edit(data):
        def refs(item):
            if isinstance(item, dict):
                if item.get("relative_path") == "AGENTS.md":
                    item["sha256"] = digest(new)
                for value in item.values():
                    refs(value)
            elif isinstance(item, list):
                for value in item:
                    refs(value)
        refs(data)
        rule = data["rules"][0]
        rule["text"] = rule["text"].replace("/api/v1/users", "/api/v2/users")
        rule["text_sha256"] = digest((rule["text"] + "\n").encode())
        rule["constraints"][0]["value"] = "/api/v2/users"
    data = update_json(registry, edit)
    def case_edit(package):
        package["files"][0]["content"] = new.decode()
        for receipt in package["receipts"]:
            receipt["entries"][0]["text_sha256"] = data["rules"][0]["text_sha256"]
    update_json(case, case_edit)
    return request, project, case, registry
