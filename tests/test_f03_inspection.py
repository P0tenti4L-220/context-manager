"""F03: real discovery, imports, scopes and inert command parsing."""
from contextlib import closing
from dataclasses import replace
import json

import pytest

from context_manager.contracts import dump_object, parse_object
from context_manager.evidence import EvidenceStore
from context_manager.inspector import InspectionOptions, inspect_project
from acceptance_support import profile, simple_project, tree_bytes


def inspect(request, *, options=None, run_id="inspection"):
    with closing(EvidenceStore(request.repo, request.out, run_id)) as store:
        return inspect_project(request, store, options=options)


def test_f03_01_scope_ancestors_exclusions_and_similar_names(tmp_path):
    request, project = simple_project(tmp_path, {"AGENTS.md": "- root\n", "clients/AGENTS.md": "- clients\n", "clients/legacy/AGENTS.md": "- legacy\n", "clients-old/AGENTS.md": "- unrelated\n"}, scope=["clients/legacy"])
    request = replace(request, task_scope=parse_object("Scope", {"include_prefixes": ["clients/legacy"], "exclude_prefixes": ["clients/legacy"]}))
    result = inspect(request)
    assert {r["relative_path"] for r in result.rule_files} == {"AGENTS.md", "clients/AGENTS.md", "clients/legacy/AGENTS.md"}
    assert all(r["applies_to_task"] is False and r["reason"] == "excluded_scope" for r in result.rule_files)
    assert all(r["evidence_id"] and r["scope"]["include_prefixes"] for r in result.rule_files)


def test_f03_02_no_profile_preserves_inventory(tmp_path):
    request, _ = simple_project(tmp_path, {"AGENTS.md": "- rule\n", "CLAUDE.md": "- rule\n"})
    result = inspect(request)
    assert result.status == "complete"
    assert all(r["harness"] == "shared" and r["load_expectation"] == "unknown" for r in result.rule_files)
    assert not result.expected_load_order


def test_f03_03_override_and_profile_limit(tmp_path):
    request, _ = simple_project(tmp_path, {"AGENTS.override.md": "- first\n", "AGENTS.md": "- second\n", "CLAUDE.md": "x" * 50})
    data = profile()
    data["harnesses"][1]["max_file_bytes"] = 10
    result = inspect(request, options=InspectionOptions(profile=data))
    files = {r["relative_path"]: r for r in result.rule_files}
    assert files["AGENTS.md"]["precedence"] == "shadowed"
    assert files["AGENTS.md"]["shadowed_by"] == files["AGENTS.override.md"]["candidate_id"]
    assert files["CLAUDE.md"]["load_expectation"] == "not_expected"
    assert not files["CLAUDE.md"]["readable"]


def test_f03_03_declared_order_and_custom_rule_directories(tmp_path):
    request, _ = simple_project(tmp_path, {"Z.md": "- Z\n", "A.md": "- A\n", ".custom/rules/api.md": "- custom\n"})
    data = profile()
    data["harnesses"][0].update(candidate_files=[{"filename": "Z.md", "role": "root"}, {"filename": "A.md", "role": "root"}], candidate_order=["Z.md", "A.md"], one_per_level=False)
    data["harnesses"][1]["rules_dirs"] = [".custom/rules"]
    result = inspect(request, options=InspectionOptions(profile=data))
    ids = {r["candidate_id"]: r["relative_path"] for r in result.rule_files}
    order = next(o for o in result.expected_load_order if o["harness"] == "codex")
    assert [ids[e["candidate_id"]] for e in order["entries"]] == ["Z.md", "A.md"]
    assert {r["relative_path"] for r in result.registered_instruction_files} == {"Z.md", "A.md", ".custom/rules/api.md"}
    assert next(r for r in result.rule_files if r["relative_path"].startswith(".custom"))["harness"] == "claude_code"


def test_f03_04_imports_cycle_duplicate_missing_and_escape(tmp_path):
    request, _ = simple_project(tmp_path, {"AGENTS.md": "@docs/first.md\n@docs/first.md\n@missing\n@../secret.md\n", "docs/first.md": "@../AGENTS.md\n"})
    result = inspect(request, options=InspectionOptions(profile=profile()))
    assert {e["traversal"] for e in result.imports} >= {"followed", "cycle", "duplicate"}
    assert {e["target_state"] for e in result.imports} >= {"missing", "outside_project"}
    assert any(r["status"] == "missing" and r["line"] == 3 for r in result.references)
    assert len(result.rule_files) == 2


def test_f03_05_reference_forms_do_not_treat_examples_as_files(tmp_path):
    request, _ = simple_project(tmp_path, {"AGENTS.md": "Use `/api/v1/users` and `docs/inline.md`.\n```python\npath = 'docs/example.md'\n```\n[Missing](docs/missing)\n![Image](missing.png)\n"})
    result = inspect(request)
    assert [(r["target_path"], r["line"]) for r in result.references if r["status"] == "missing"] == [("docs/missing", 5)]
    assert any(r["classification"] == "api_literal" for r in result.references)
    assert not result.imports


def test_f03_06_commands_never_execute(tmp_path, monkeypatch):
    import subprocess
    request, project = simple_project(tmp_path, {"AGENTS.md": "`npm run test`\n`npm run absent`\n`npm install`\n`make check`\n`npm run test && touch OWNED`\n", "package.json": '{"scripts":{"test":"touch OWNED"}}', "Makefile": "check:\n\ttouch OWNED\n"})
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("Inspector executed a command"))
    before = tree_bytes(project)
    result = inspect(request)
    assert {c["status"] for c in result.commands} == {"resolved", "missing", "builtin_command", "unsupported_syntax"}
    assert all(c["executed"] is False for c in result.commands)
    assert tree_bytes(project) == before


def test_f03_07_truncation_and_unreadable_are_partial(tmp_path):
    request, _ = simple_project(tmp_path, {"AGENTS.md": "- rule", "a": "", "b": ""})
    result = inspect(request, options=InspectionOptions(entry_limit=1))
    assert result.status == "partial" and result.counts["truncated"]
    assert any(e["code"] == "E_SCAN_TRUNCATED" for e in result.errors)


def test_f03_08_parallel_harness_rules_are_observations(tmp_path):
    request, _ = simple_project(tmp_path, {"AGENTS.md": "- v1", "CLAUDE.md": "- v2"})
    result = inspect(request, options=InspectionOptions(profile=profile()))
    assert {r["harness"] for r in result.rule_files} == {"codex", "claude_code"}
    assert [o["kind"] for o in result.observations] == ["parallel_harness_rules"]


def test_f03_09_ids_and_order_are_stable(tmp_path):
    request, _ = simple_project(tmp_path, {"AGENTS.md": "@a.md\n[broken](missing.txt)\n", "a.md": "- a\n"})
    first, second = inspect(request, run_id="a"), inspect(request, run_id="b")
    for field, key in (("rule_files", "candidate_id"), ("imports", "edge_id"), ("references", "ref_id")):
        assert [r[key] for r in getattr(first, field)] == [r[key] for r in getattr(second, field)]


@pytest.mark.parametrize("limit", ["depth", "files"])
def test_f03_10_import_limits_and_ignored_directories(tmp_path, limit):
    request, _ = simple_project(tmp_path, {"AGENTS.md": "@a.md\n@node_modules/no.md\n@c.md\n", "a.md": "@b.md\n", "b.md": "@d.md\n", "c.md": "- c", "d.md": "- d", "node_modules/no.md": "- hidden"})
    options = InspectionOptions(max_import_depth=1) if limit == "depth" else InspectionOptions(max_imported_files=1)
    result = inspect(request, options=options)
    assert result.status == "partial"
    assert any(e["code"] == ("E_IMPORT_DEPTH" if limit == "depth" else "E_IMPORT_LIMIT") for e in result.errors)
    assert all("node_modules" not in r["relative_path"] for r in result.rule_files)


def test_f03_11_unsafe_imports_and_links_never_capture_outside(tmp_path):
    request, project = simple_project(tmp_path, {"AGENTS.md": "@link.md\n@alias/a.md\n@/tmp/no.md\n@~/no.md\n@x%20.md\n@../secret.md\n"})
    (tmp_path / "secret.md").write_text("OUTSIDE-SENTINEL")
    (project / "link.md").symlink_to(tmp_path / "secret.md")
    (project / "alias").symlink_to(tmp_path, target_is_directory=True)
    result = inspect(request)
    assert {e["target_state"] for e in result.imports} >= {"rejected_symlink", "absolute_or_rooted", "encoded_target", "outside_project"}
    assert [r["relative_path"] for r in result.rule_files] == ["AGENTS.md"]


@pytest.mark.parametrize("content", [None, b"\xff", b"x" * (1048576 + 1)])
def test_f03_12_empty_bad_encoding_and_size(tmp_path, content):
    request, _ = simple_project(tmp_path, {} if content is None else {"AGENTS.md": content})
    result = inspect(request)
    assert result.status == ("complete" if content is None else "partial")
    assert bool(result.rule_files) is (content is not None)


@pytest.mark.parametrize("manifest,expected", [('{"workspaces":["packages/*"]}', "workspace_scope_unknown"), ("{bad", "dynamic_manifest"), (None, "unresolved_manifest"), ('{"scripts":{"test":"echo ok"}}', "resolved")])
def test_f03_13_manifest_boundaries(tmp_path, manifest, expected):
    request, _ = simple_project(tmp_path, {"AGENTS.md": "`npm run test -- --watch`\n", **({"package.json": manifest} if manifest is not None else {})})
    result = inspect(request)
    assert result.commands[0]["status"] == expected


@pytest.mark.parametrize("paths,valid", [('["clients/**"]', True), ("\n - clients\n - legacy/**", True), ('["clients/*.py"]', False)])
def test_f03_14_rule_frontmatter(tmp_path, paths, valid):
    request, _ = simple_project(tmp_path, {".claude/rules/api.md": f"---\npaths: {paths}\n---\n- rule\n", "clients/a.py": ""}, scope=["clients"])
    result = inspect(request)
    assert result.status == ("complete" if valid else "partial")
    assert result.rule_files[0]["applies_to_task"] is (True if valid else None)
