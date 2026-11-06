"""Read-only instruction discovery, scope, imports, references and commands (F03)."""
from __future__ import annotations

import hashlib
import ast
import json
import os
import posixpath as pp
import re
import shlex
import stat
from dataclasses import dataclass, field
from typing import Any, Mapping

from .contracts import AuditRequest, ContractError, FieldError, dump_object, normalize_relative_path, normalize_scope_prefix
from .evidence import EvidenceError, EvidenceStore, prepare_selected_input
from .views import ReadonlyRecord, freeze, readonly

IGNORED = frozenset(".git node_modules .venv venv __pycache__ .pytest_cache .mypy_cache .ruff_cache dist build target .next .cache vendor .context-manager".split())
NAMES = {"AGENTS.md": "agents_md", "AGENTS.override.md": "agents_override_md", "CLAUDE.md": "claude_md"}
LINK = re.compile(r'(!?)\[[^\]\n]*\]\((<[^>\n]+>|[^)\n]*)\)')
INLINE = re.compile(r'(?<!`)`([^`\n]+)`(?!`)')
TEXT_EXTENSIONS = {".md", ".markdown", ".txt", ".rst", ".json", ".toml", ".yaml", ".yml"}


@readonly
def validate_profile(data):
    """Validate the same bounded profile for CLI and component callers."""
    def fail(field="harness_profile"):
        raise ContractError("invalid_harness_profile", "HarnessProfile", [FieldError(field, "invalid_value", "invalid harness profile")])

    def text(value):
        return isinstance(value, str) and 0 < len(value) <= 200 and not any(ord(c) < 32 for c in value)

    if not isinstance(data, Mapping):
        fail()
    data = dump_object(data)
    if type(data.get("schema_version")) is not int or data["schema_version"] != 1 or not all(text(data.get(k)) for k in ("profile_id", "profile_version", "source")) or not isinstance(data.get("harnesses"), list) or len(data["harnesses"]) > 32:
        fail()
    seen = set()
    for index, harness in enumerate(data["harnesses"]):
        field = f"harness_profile.harnesses[{index}]"
        if not isinstance(harness, dict) or not isinstance(harness.get("id"), str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", harness["id"]) or harness["id"] in seen:
            fail(field)
        seen.add(harness["id"])
        for name in ("candidate_files", "candidate_order", "rules_dirs", "import_syntax", "known_versions", "context_channels"):
            values = harness.get(name, [])
            if not isinstance(values, list) or len(values) > 100:
                fail(field + "." + name)
        files = harness.get("candidate_files", [])
        filenames = []
        for item in files:
            name = item.get("filename") if isinstance(item, dict) else None
            if not text(name) or name in (".", "..") or "/" in name or "\\" in name:
                fail(field + ".candidate_files")
            filenames.append(name)
        if len(filenames) != len(set(filenames)):
            fail(field + ".candidate_files")
        for name in ("candidate_order", "known_versions", "import_syntax", "context_channels", "rules_dirs"):
            values = harness.get(name, [])
            if any(not text(value) for value in values) or len(values) != len(set(values)):
                fail(field + "." + name)
        if any(name not in filenames for name in harness.get("candidate_order", [])) or set(harness.get("import_syntax", [])) - {"at_path", "markdown_link"}:
            fail(field)
        for directory in harness.get("rules_dirs", []):
            try:
                if normalize_relative_path(directory) != directory or ignored(directory):
                    fail(field + ".rules_dirs")
            except ValueError:
                fail(field + ".rules_dirs")
        if type(harness.get("max_file_bytes", 1048576)) is not int or not 1 <= harness.get("max_file_bytes", 1048576) <= 10 * 1024 * 1024:
            fail(field + ".max_file_bytes")
        if type(harness.get("one_per_level", False)) is not bool or harness.get("parent_child") not in (None, "unknown", "nearest_first", "root_first"):
            fail(field)
    return data


def stable_id(prefix: str, *parts: object) -> str:
    return prefix + hashlib.sha256("\0".join(map(str, parts)).encode()).hexdigest()[:16]


def beneath(path: str, prefix: str) -> bool:
    path, prefix = path.rstrip("/"), prefix.rstrip("/")
    return prefix == "." or path == prefix or path.startswith(prefix + "/")


def ignored(path: str) -> bool:
    return any(part in IGNORED for part in path.split("/"))


@dataclass(frozen=True)
class InspectionOptions:
    entry_limit: int = 2000
    max_import_depth: int = 8
    max_imported_files: int = 200
    rules_dir_names: tuple[str, ...] = (".claude/rules",)
    profile: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        for name in ("entry_limit", "max_import_depth", "max_imported_files"):
            value = getattr(self, name)
            if type(value) is not int or value < 1:
                raise ContractError("invalid_value", "InspectionOptions", [FieldError("options." + name, "invalid_value", "expected a positive integer")])
        if not isinstance(self.rules_dir_names, (tuple, list)) or any(not isinstance(p, str) or p.startswith("/") or ".." in p.split("/") or "\\" in p or not p for p in self.rules_dir_names):
            raise ContractError("invalid_value", "InspectionOptions", [FieldError("options.rules_dir_names", "invalid_value", "expected project-relative directories")])
        object.__setattr__(self, "rules_dir_names", tuple(self.rules_dir_names))
        object.__setattr__(self, "profile", freeze(self.profile))


@dataclass
class InspectionResult(ReadonlyRecord):
    run_id: str
    project_id: str
    schema_version: int = 1
    status: str = "complete"
    rule_files: list[dict] = field(default_factory=list)
    registered_instruction_files: list[dict] = field(default_factory=list)
    imports: list[dict] = field(default_factory=list)
    references: list[dict] = field(default_factory=list)
    commands: list[dict] = field(default_factory=list)
    observations: list[dict] = field(default_factory=list)
    expected_load_order: list[dict] = field(default_factory=list)
    coverage: list[dict] = field(default_factory=list)
    counts: dict = field(default_factory=dict)
    errors: list[dict] = field(default_factory=list)


class _Inspector:
    def __init__(self, request: AuditRequest, store: EvidenceStore, options: InspectionOptions):
        self.request, self.store, self.options = request, store, options
        self.result = InspectionResult(store.run_id, request.project_id)
        self.files: dict[str, dict] = {}
        self.texts: dict[str, str] = {}
        self.failed: dict[str, str] = {}
        self.entries: set[str] = set()
        self.directories: set[str] = set()
        self.visited: set[str] = set()
        self.imported_count = 0
        self.truncated = False
        self.profile = options.profile
        if self.profile is None and request.harness_profile:
            from .decisions import decode_json
            self.profile = decode_json(prepare_selected_input(request.harness_profile).data)
        if self.profile is not None:
            self.profile = validate_profile(self.profile)
        self.harnesses = list((self.profile or {}).get("harnesses", []))
        self.names = dict(NAMES)
        self.rules_dirs = set(options.rules_dir_names)
        for harness in self.harnesses:
            for item in harness.get("candidate_files", []):
                name = item["filename"]
                if "/" not in name and "\\" not in name:
                    self.names.setdefault(name, "agents_md" if harness["id"] == "codex" else "claude_md" if harness["id"] == "claude_code" else "imported_text")
            self.rules_dirs.update(harness.get("rules_dirs", []))

    def error(self, check: str, code: str, path: str | None, line: int | None = None, *, failure: bool = True) -> dict:
        item = {"code": code, "relative_path": path, "line": line, "detail": code.lower()}
        self.result.errors.append(item)
        if failure:
            self.failed[check] = code
        return item

    def target(self, source: str, target: str, *, allow_spaces=False) -> tuple[str | None, str]:
        target = target.split("#", 1)[0]
        if not target:
            return None, "unsupported_syntax"
        if target.startswith(("/", "~")) or re.match(r'^[A-Za-z]:', target):
            return None, "absolute_or_rooted"
        if "%" in target or "?" in target:
            return None, "encoded_target"
        if "\\" in target or (not allow_spaces and any(c.isspace() for c in target)):
            return None, "unsupported_syntax"
        path = pp.normpath(pp.join(pp.dirname(source), target))
        if path == ".." or path.startswith("../"):
            return None, "outside_project"
        if ignored(path):
            return path, "ignored_path"
        if path == ".":
            return path, "directory"
        probe = self.store.probe_path(path)
        return path, {"file": "resolved", "directory": "directory", "symlink": "rejected_symlink", "unsafe_path": "outside_project", "not_regular_file": "unreadable"}.get(probe.status, probe.status)

    def reference(self, item: dict, line: int, column: int, raw: str, target: str, form: str, *, example: bool = False, force_local=False) -> dict | None:
        path, reason = None, None
        if re.match(r'^[A-Za-z][A-Za-z0-9+.-]*:', target) or target.startswith("//"):
            classification, status = "external_url", "skipped"
        elif target.startswith("#"):
            classification, status = "anchor_only", "skipped"
        elif target.startswith("/"):
            classification, status = ("rooted_path", "unverified") if pp.splitext(target.split("#")[0])[1] in TEXT_EXTENSIONS else ("api_literal", "skipped")
        elif not force_local and "/" not in target and not pp.splitext(target.split("#")[0])[1]:
            return None
        elif example or form == "markdown_image":
            classification, status = "code_example", "skipped"
        elif form == "html_link":
            classification, status = "local_file", "unverified"
            reason = "unsupported_syntax"
        else:
            classification = "local_file"
            path, reason = self.target(item["relative_path"], target, allow_spaces=form == "markdown_link")
            status = "missing" if reason == "missing" else "resolved" if reason in ("resolved", "directory") else "unverified"
        ref = {"ref_id": stable_id("rc_", item["relative_path"], line, column, raw), "source_candidate_id": item["candidate_id"], "relative_path": item["relative_path"], "line": line, "column": column, "raw_text": raw, "form": form, "classification": classification, "status": status, "target_path": path, "reason": reason}
        self.result.references.append(ref)
        return ref

    def import_target(self, item: dict, line: int, column: int, raw: str, target: str, syntax: str, stack: tuple[str, ...]) -> None:
        if any(e["from_candidate_id"] == item["candidate_id"] and e["line"] == line and e["raw_text"] == raw for e in self.result.imports):
            return
        path, state = self.target(item["relative_path"], target, allow_spaces=syntax == "markdown_link")
        edge = {"edge_id": stable_id("ie_", item["relative_path"], line, raw)[:15], "from_candidate_id": item["candidate_id"], "from_relative_path": item["relative_path"], "line": line, "column": column, "raw_text": raw, "syntax": syntax, "target_text": target, "target_path": path, "target_state": state, "traversal": "skipped", "target_candidate_id": None, "reason": None}
        self.result.imports.append(edge)
        if state != "resolved":
            if syntax == "at_path" and state == "missing":
                self.reference(item, line, column, raw, target, "markdown_link", force_local=True)
            return
        if path in stack:
            edge["traversal"] = "cycle"
        elif path in self.visited:
            edge["traversal"] = "duplicate"
        elif len(stack) > self.options.max_import_depth or (path not in self.files and self.imported_count >= self.options.max_imported_files):
            edge["traversal"] = "limit_reached"
            edge["reason"] = "E_IMPORT_DEPTH" if len(stack) > self.options.max_import_depth else "E_IMPORT_LIMIT"
            self.error("imports", edge["reason"], item["relative_path"], line)
            return
        else:
            if path not in self.files:
                self.imported_count += 1
            child = self.capture(path, "import", len(stack))
            edge["target_candidate_id"] = child["candidate_id"]
            if not child["readable"]:
                edge["target_state"] = "binary" if child["encoding"] == "binary" else "unreadable"
                return
            self.import_expectation(item, child, syntax, edge)
            edge["traversal"] = "followed"
            self.parse(child, stack)
        if path in self.files:
            edge["target_candidate_id"] = self.files[path]["candidate_id"]
            self.import_expectation(item, self.files[path], syntax, edge)

    def import_expectation(self, parent, child, syntax, edge):
        if child["discovery"] != "import" or not child["readable"]:
            return
        harness = next((h for h in self.harnesses if h["id"] == parent["harness"]), None)
        if not harness or not harness.get("known_versions") or not self.profile:
            return
        expected = parent["load_expectation"] in ("expected", "expected_if_parent_loaded") and syntax in harness.get("import_syntax", [])
        if expected or child["load_expectation"] != "expected_if_parent_loaded":
            child["harness"] = parent["harness"]
            child["load_expectation"] = "expected_if_parent_loaded" if expected else "not_expected"
            child["load_basis"] = {"profile_id": self.profile["profile_id"], "profile_version": self.profile["profile_version"], "rule": "import_from:" + edge["edge_id"]}

    def command(self, item: dict, line: int, column: int, raw: str) -> None:
        command = raw.strip().removeprefix("$ ")
        if not re.match(r'^(npm|pnpm|yarn|make)(?:\s|$)', command):
            return
        family = command.split()[0]
        check = {"check_id": stable_id("cc_", item["relative_path"], line, column, raw), "source_candidate_id": item["candidate_id"], "relative_path": item["relative_path"], "line": line, "column": column, "raw_text": raw, "family": family, "subject": None, "manifest_path": None, "manifest_kind": "makefile" if family == "make" else "package_json", "status": "unsupported_syntax", "reason": None, "executed": False}
        self.result.commands.append(check)
        if re.search(r'[&|<>`$\\;\n]', command):
            return
        try:
            args = shlex.split(command)
        except ValueError:
            return
        builtin = False
        if family == "make":
            if len(args) != 2 or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', args[1]):
                return
            subject = args[1]
        elif len(args) >= 3 and args[1] == "run" and (len(args) == 3 or args[3] == "--"):
            subject = args[2]
        elif len(args) == 2 and family == "yarn" and not args[1].startswith("-"):
            subject = args[1]
        elif len(args) == 2 and family in ("npm", "pnpm") and args[1] in ("test", "start", "stop", "restart", "install", "ci"):
            subject, builtin = args[1], True
        else:
            return
        check["subject"] = subject
        directory = pp.dirname(item["relative_path"]) or "."
        manifests = ("Makefile", "makefile", "GNUmakefile") if family == "make" else ("package.json",)
        while True:
            for name in manifests:
                path = name if directory == "." else directory + "/" + name
                probe = self.store.probe_path(path)
                if probe.status == "missing":
                    continue
                check["manifest_path"] = path
                if probe.status != "file":
                    check["status"], check["reason"] = "unresolved_manifest", probe.status
                    self.error("commands", "E_MANIFEST_INVALID", path)
                    return
                try:
                    evidence = self.store.capture_file(path, kind="data", purpose="command_manifest")
                    text = self.store.snapshot_bytes(evidence.evidence_id).decode("utf-8-sig")
                    if family == "make":
                        targets = re.findall(r'^([A-Za-z0-9][A-Za-z0-9_.-]*)\s*:(?!=)', text, re.M)
                        dynamic = bool(re.search(r'^\s*(?:-?include|sinclude|define)\b|\$\(eval', text, re.M))
                        check["status"] = "resolved" if subject in targets else "dynamic_manifest" if dynamic else "missing"
                    else:
                        from .decisions import decode_json
                        data = decode_json(text.encode())
                        scripts = data.get("scripts", {})
                        if not isinstance(scripts, dict):
                            raise ValueError("invalid scripts")
                        check["status"] = "resolved" if subject in scripts else "workspace_scope_unknown" if "workspaces" in data else "builtin_command" if builtin else "unsupported_syntax" if family == "yarn" and args[1] != "run" else "missing"
                except (EvidenceError, ContractError, ValueError, UnicodeError, RecursionError):
                    check["status"] = "dynamic_manifest"
                    self.error("commands", "E_MANIFEST_INVALID", path)
                return
            if directory == ".":
                check["status"] = "builtin_command" if builtin else "unresolved_manifest"
                return
            directory = pp.dirname(directory) or "."

    def parse(self, item: dict, stack: tuple[str, ...] = ()) -> None:
        path = item["relative_path"]
        if path in self.visited or not item["readable"]:
            return
        self.visited.add(path)
        stack = (*stack, path)
        text = re.sub(r'<!--[\s\S]*?-->', lambda m: re.sub(r'[^\n]', " ", m.group()), self.texts[path])
        fence, language = "", ""
        for number, line in enumerate(text.split("\n"), 1):
            marker = re.match(r'^\s*(`{3,}|~{3,})(.*)$', line)
            if marker:
                if fence and marker[1][0] == fence[0] and len(marker[1]) >= len(fence):
                    fence = ""
                elif not fence:
                    fence, language = marker[1], marker[2].strip().lower()
                continue
            if fence:
                if language in ("", "sh", "bash", "shell", "zsh", "console"):
                    self.command(item, number, 1, line)
                for token in re.finditer(r'(?<!\w)(?:/|\./)?[\w.-]+(?:/[\w.-]+)+|[\w.-]+\.(?:md|py|json|txt)', line):
                    self.reference(item, number, token.start() + 1, token[0], token[0], "fenced_code", example=True)
                continue
            inline_ranges = []
            for match in INLINE.finditer(line):
                inline_ranges.append((match.start(), match.end()))
                self.reference(item, number, match.start() + 1, match[0], match[1], "inline_code", example=True)
                self.command(item, number, match.start() + 1, match[1])
            at = re.match(r'^\s*(?:[-*+]\s+|\d+[.)]\s+)?@(.+?)\s*$', line)
            if at:
                self.import_target(item, number, line.index("@") + 1, line.strip(), at[1], "at_path", stack)
            for match in LINK.finditer(line):
                if any(start <= match.start() < end for start, end in inline_ranges):
                    continue
                target = match[2].strip()
                if target.startswith("<"):
                    target = target[1:target.index(">")]
                else:
                    target = re.split(r'\s+[\"\']', target, maxsplit=1)[0]
                form = "markdown_image" if match[1] else "markdown_link"
                ref = self.reference(item, number, match.start() + 1, match[0], target, form, force_local=True)
                if not match[1] and ref and ref["classification"] not in ("external_url", "anchor_only", "api_literal", "code_example") and pp.splitext(target.split("#")[0])[1].lower() in (".md", ".markdown"):
                    self.import_target(item, number, match.start() + 1, match[0], target, "markdown_link", stack)
            for match in re.finditer(r'<(?:a|img)\s+[^>]*(?:href|src)=[\"\']([^\"\']+)[\"\'][^>]*>', line, re.I):
                self.reference(item, number, match.start() + 1, match[0], match[1], "html_link")

    def run(self) -> InspectionResult:
        roots = {"."}
        for prefix in self.request.task_scope.include_prefixes:
            current = prefix.rstrip("/")
            if current != "." and (ignored(current) or self.store.probe_path(current).status != "directory"):
                self.error("scan", "E_SCOPE_MISSING", current)
                continue
            while current not in (".", ""):
                roots.add(current)
                current = pp.dirname(current)
        def walk_rules(directory: str) -> None:
            if self.truncated:
                return
            try:
                entries = self.list_directory(directory)
            except OSError:
                self.error("scan", "E_FILE_UNREADABLE", directory)
                return
            for path, mode in entries:
                if stat.S_ISDIR(mode) and not ignored(path):
                    walk_rules(path)
                elif stat.S_ISREG(mode) and path.endswith(".md"):
                    self.capture(path, "rules_dir", 0, "claude_rule")
        for directory in sorted(roots):
            if self.truncated:
                break
            try:
                entries = self.list_directory(directory)
            except OSError:
                self.error("scan", "E_FILE_UNREADABLE", directory)
                continue
            for path, mode in entries:
                if stat.S_ISREG(mode) and pp.basename(path) in self.names:
                    self.capture(path, "scan_ancestor", 0)
            for rules_dir in sorted(self.rules_dirs):
                path = pp.join(directory, rules_dir).removeprefix("./")
                if not ignored(path) and self.store.probe_path(path).status == "directory":
                    walk_rules(path)
        for harness in self.harnesses:
            if not harness.get("known_versions"):
                continue
            order = harness.get("candidate_order", [])
            grouped: dict[str, list[dict]] = {}
            for item in self.files.values():
                if item["harness"] == harness["id"] and pp.basename(item["relative_path"]) in order:
                    grouped.setdefault(pp.dirname(item["relative_path"]) or ".", []).append(item)
            for directory, items in grouped.items():
                items.sort(key=lambda i: order.index(pp.basename(i["relative_path"])))
                for index, item in enumerate(items):
                    item["precedence"] = "shadowed" if index and harness.get("one_per_level") else "selected"
                    if item["precedence"] == "shadowed":
                        item.update(shadowed_by=items[0]["candidate_id"], load_expectation="not_expected")
                        self.result.observations.append({"observation_id": stable_id("so_", "shadowed", item["relative_path"]), "kind": "shadowed_candidate", "relative_paths": [item["relative_path"], items[0]["relative_path"]], "detail": "profile_declared_precedence"})
            expected = [i for i in self.files.values() if i["harness"] == harness["id"] and i["load_expectation"] == "expected"]
            direction = harness.get("parent_child")
            expected.sort(key=lambda i: ((-1 if direction == "nearest_first" else 1) * len(i["relative_path"].split("/")), pp.dirname(i["relative_path"]), order.index(pp.basename(i["relative_path"])) if pp.basename(i["relative_path"]) in order else len(order), i["relative_path"]))
            self.result.expected_load_order.append({"harness": harness["id"], "parent_child": direction, "one_per_level": harness.get("one_per_level", False), "entries": [{"candidate_id": item["candidate_id"], "directory": pp.dirname(item["relative_path"]) or ".", "rank": index if direction in ("nearest_first", "root_first") else None} for index, item in enumerate(expected)]})
        for path in sorted(list(self.files)):
            self.parse(self.files[path])
        for directory in sorted({pp.dirname(p) for p in self.files}):
            peers = [p for p in self.files if pp.dirname(p) == directory and pp.basename(p) in ("AGENTS.md", "CLAUDE.md")]
            if len(peers) == 2:
                self.result.observations.append({"observation_id": stable_id("so_", "parallel", directory), "kind": "parallel_harness_rules", "relative_paths": sorted(peers), "detail": "parallel_files_do_not_prove_joint_loading_or_conflict"})
        self.result.rule_files = [self.files[p] for p in sorted(self.files)]
        self.result.registered_instruction_files = [{"relative_path": i["relative_path"], "evidence_id": i["evidence_id"]} for i in self.result.rule_files if i["readable"] and i["kind"] != "imported_text" and i["applies_to_task"] is not None]
        for name, keys in (("imports", ("from_relative_path", "line", "column")), ("references", ("relative_path", "line", "column")), ("commands", ("relative_path", "line", "column")), ("observations", ("observation_id",))):
            getattr(self.result, name).sort(key=lambda item: tuple(item[key] for key in keys))
        self.result.coverage = [{"check_id": "f03." + check, "status": "failed" if check in self.failed else "passed", "inputs": [], "reason": self.failed.get(check) or ("none_found" if not self.files else None), "artifact_refs": []} for check in ("scan", "rule_files", "imports", "references", "commands", "scope")]
        self.result.status = "partial" if self.failed else "complete"
        self.result.counts = {"directories_visited": len(self.directories), "entries_visited": len(self.entries), "truncated": self.truncated, "rule_files": len(self.files), "imports": len(self.result.imports), "references_checked": len(self.result.references), "references_missing": sum(r["status"] == "missing" for r in self.result.references), "commands_checked": len(self.result.commands), "commands_missing": sum(c["status"] == "missing" for c in self.result.commands)}
        return self.result


    def list_directory(self, path: str) -> list[tuple[str, int]]:
        """Open every directory component with no-follow; never traverse a link."""
        if ignored(path):
            return []
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        fd = os.open(self.store.project_root, flags)
        try:
            for part in (() if path == "." else path.split("/")):
                child = os.open(part, flags, dir_fd=fd)
                os.close(fd)
                fd = child
            self.directories.add(path)
            found = []
            for name in sorted(os.listdir(fd)):
                relative = name if path == "." else path + "/" + name
                if relative not in self.entries:
                    if len(self.entries) >= self.options.entry_limit:
                        if not self.truncated:
                            self.error("scan", "E_SCAN_TRUNCATED", path)
                        self.truncated = True
                        break
                    self.entries.add(relative)
                try:
                    mode = os.stat(name, dir_fd=fd, follow_symlinks=False).st_mode
                except OSError:
                    self.error("scan", "E_FILE_UNREADABLE", relative)
                    continue
                found.append((relative, mode))
            return found
        finally:
            os.close(fd)

    def scope(self, path: str, text: str, kind: str) -> tuple[dict, bool | None, str | None]:
        prefixes = [pp.dirname(path) or "."]
        declared = "directory"
        if kind == "claude_rule":
            prefixes, declared = ["."], "profile_default"
            if text.startswith("---\n") or text.startswith("---\r\n"):
                block = re.split(r'^---[ \t]*\r?$', text, maxsplit=2, flags=re.M)
                if len(block) < 3:
                    self.error("scope", "E_SCOPE_UNRESOLVED", path)
                    return {"include_prefixes": [], "exclude_prefixes": [], "declared_by": "frontmatter_paths", "scope_match": "unresolved"}, None, "scope_unresolved"
                front = block[1] if len(block) > 2 else ""
                match = re.search(r'^paths:[ \t]*(.*)$', front, re.M)
                if match:
                    declared = "frontmatter_paths"
                    tail = match.group(1).strip()
                    if tail.startswith("[") and tail.endswith("]"):
                        try:
                            values = ast.literal_eval(tail)
                        except (ValueError, SyntaxError, RecursionError):
                            values = []
                    elif tail:
                        values = [tail]
                    else:
                        following = front[match.end():]
                        values = []
                        for line in following.splitlines():
                            if not line.strip():
                                continue
                            entry = re.fullmatch(r'[ \t]*-[ \t]+(.+)', line)
                            if not entry:
                                break
                            values.append(entry.group(1))
                    try:
                        if not isinstance(values, list) or not values or any(not isinstance(v, str) for v in values):
                            raise ValueError("unsupported paths")
                        prefixes = []
                        for value in values:
                            prefix = value.strip().strip("\"'")
                            if prefix == "**":
                                prefix = "."
                            else:
                                prefix = prefix.removesuffix("/**")
                            if re.search(r'[*?\[\]{}\\]', prefix) or prefix.startswith(("/", "~")):
                                raise ValueError("unsupported paths")
                            prefixes.append(normalize_scope_prefix(prefix).rstrip("/") or ".")
                    except ValueError:
                        self.error("scope", "E_SCOPE_UNRESOLVED", path)
                        return {"include_prefixes": [], "exclude_prefixes": [], "declared_by": declared, "scope_match": "unresolved"}, None, "scope_unresolved"
        matches, excluded = [], False
        for prefix in prefixes:
            for task in self.request.task_scope.include_prefixes:
                if beneath(task, prefix) or beneath(prefix, task):
                    intersection = task if beneath(task, prefix) else prefix
                    if any(beneath(intersection, exc) for exc in self.request.task_scope.exclude_prefixes):
                        excluded = True
                    else:
                        matches.append("definite" if beneath(task, prefix) else "partial")
        match = "definite" if "definite" in matches else "partial" if matches else "none"
        return {"include_prefixes": prefixes, "exclude_prefixes": [], "declared_by": declared, "scope_match": match}, bool(matches), None if matches else "excluded_scope" if excluded else "outside_task_scope"

    def capture(self, path: str, discovery: str, depth: int, kind: str | None = None) -> dict:
        if path in self.files:
            return self.files[path]
        kind = kind or self.names.get(pp.basename(path), "imported_text")
        harness = next((h for h in self.harnesses if pp.basename(path) in h.get("candidate_order", []) or (kind == "claude_rule" and any(("/" + directory + "/") in ("/" + path) for directory in h.get("rules_dirs", [])))), None)
        item = {"candidate_id": stable_id("rf_", "rule_file", path)[:15], "relative_path": path, "kind": kind, "harness": harness["id"] if harness else "shared", "discovery": discovery, "depth": depth, "evidence_id": None, "sha256": None, "byte_length": None, "line_count": None, "encoding": None, "readable": False, "scope": {}, "applies_to_task": None, "load_expectation": "unknown", "load_basis": None, "precedence": "unknown", "shadowed_by": None, "reason": None, "error": None}
        self.files[path] = item
        try:
            probe = self.store.probe_path(path)
            if harness and probe.byte_length is not None and probe.byte_length > harness.get("max_file_bytes", 1048576):
                item.update(reason="profile_size_limit", load_expectation="not_expected", error=self.error("rule_files", "E_PROFILE_SIZE_LIMIT", path, failure=False))
            else:
                evidence = self.store.capture_file(path, kind="rule_file" if kind != "imported_text" else "document", purpose="inspection")
                item.update({key: getattr(evidence, key) for key in ("evidence_id", "sha256", "byte_length", "line_count", "encoding")})
                if evidence.encoding not in ("utf-8", "utf-8-bom"):
                    raise EvidenceError("evidence_not_text", "instruction is not UTF-8 text")
                self.texts[path] = self.store.snapshot_bytes(evidence.evidence_id).decode("utf-8-sig")
                item["readable"] = True
        except EvidenceError as exc:
            code = "E_TOO_LARGE" if "too_large" in exc.code else "E_ENCODING" if "text" in exc.code or "encoding" in exc.code else "E_FILE_UNREADABLE"
            item.update(reason="unreadable", error=self.error("rule_files", code, path))
            self.failed.update(imports=code, references=code, commands=code)
        item["scope"], item["applies_to_task"], reason = self.scope(path, self.texts.get(path, ""), kind)
        item["reason"] = item["reason"] or reason
        if harness and harness.get("known_versions") and self.profile and item["readable"]:
            item["load_expectation"] = "expected" if item["applies_to_task"] else "unknown" if item["applies_to_task"] is None else "not_expected"
            item["load_basis"] = {"profile_id": self.profile["profile_id"], "profile_version": self.profile["profile_version"], "rule": "filename_match", "source": self.profile.get("source", "user_supplied")}
        return item


@readonly
def inspect_project(request: AuditRequest, evidence_store: EvidenceStore, *, options: InspectionOptions | None = None) -> InspectionResult:
    if str(evidence_store.project_root) != str(os.path.realpath(request.repo)):
        raise ContractError("invalid_value", "AuditRequest", [FieldError("repo", "invalid_value", "evidence store belongs to another project")])
    return _Inspector(request, evidence_store, options or InspectionOptions()).run()
