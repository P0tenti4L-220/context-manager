"""Approved minimal diffs and application checks in isolated copies (F08)."""
from __future__ import annotations

import hashlib
import os
import re
import subprocess
import tempfile
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .contracts import ContractError, dump_object, normalize_relative_path, parse_object
from .decisions import approval_key, conditions_intersect
from .evidence import EvidenceError
from .inspector import stable_id
from .rules import covers, span_bytes, interpretation_complete
from .views import ReadonlyRecord, readonly


@dataclass
class RepairPlan(ReadonlyRecord):
    run_id: str
    as_of: str
    proposals: list = field(default_factory=list)
    diff: bytes | None = None
    diff_sha256: str | None = None
    diff_proposal_id: str | None = None
    apply_check: dict | None = None
    hunks: list = field(default_factory=list)
    advisories: list = field(default_factory=list)
    rejections: list = field(default_factory=list)
    checks: list = field(default_factory=list)


class _Rejected(Exception):
    def __init__(self, reason, blocked=False, exit_code=None):
        self.reason, self.blocked, self.exit_code = reason, blocked, exit_code


def _replacement(raw: bytes, old: bytes, text: str, start: int) -> bytes:
    crlf, lf = raw.count(b"\r\n"), raw.count(b"\n") - raw.count(b"\r\n")
    newline = b"\r\n" if crlf > lf else b"\n"
    if crlf == lf and start > 0:
        previous = raw[:start]
        newline = b"\r\n" if previous.endswith(b"\r\n") else b"\n"
    normalized = text.replace("\r\n", "\n").rstrip("\n")
    data = normalized.encode("utf-8").replace(b"\n", newline)
    if old.endswith(b"\n"):
        data += newline
    return data


@readonly
def plan_repairs(findings, edit_intents, registry, evidence_store) -> RepairPlan:
    plan = RepairPlan(evidence_store.run_id, registry.as_of)
    findings = {f["finding_id"]: f for f in map(dump_object, findings)}
    ready = []
    def proposal(finding_ids, status, reason, *, edits=(), bases=()):
        pid = stable_id("rp_", plan.run_id, *sorted(finding_ids), status, reason, *sorted(edit["edit_id"] for edit in edits))
        existing = next((item for item in plan.proposals if item["proposal_id"] == pid), None)
        if existing:
            return existing
        item = {"proposal_id": pid, "run_id": plan.run_id, "finding_ids": sorted(set(finding_ids)), "base_files": list(bases), "edits": list(edits), "diff_sha256": None, "status": status, "checks": [], "unresolved_questions": [] if status == "preview_ready" else [{"question_id": stable_id("question_", pid), "prompt": reason, "finding_ids": sorted(set(finding_ids)), "needed": [reason]}]}
        plan.proposals.append(item)
        return item
    for intent in edit_intents:
        if not isinstance(intent, Mapping):
            plan.rejections.append({"edit_intent_id": None, "finding_ids": [], "target_path": None, "reason": "invalid_edit_intent", "detail": "expected a structured edit intent", "checks": []})
            continue
        try:
            path = intent["target_path"]
            if intent.get("origin") not in ("registry", "user", "model"):
                raise _Rejected("unknown_edit_origin")
            for key in ("rule_ids", "decision_ids", "preserved_rule_ids"):
                values = intent.get(key, [])
                if not isinstance(values, (list, tuple)) or any(not isinstance(value, str) or not value for value in values) or len(set(values)) != len(values):
                    raise _Rejected("invalid_edit_identifiers")
            if normalize_relative_path(path) != path or path not in registry.editable_files or path.startswith((".git/", "examples/")):
                raise _Rejected("edit_not_whitelisted", True)
            ev = evidence_store.get(intent["base_evidence_id"])
            if ev.relative_path != path or ev.sha256 != intent["base_sha256"] or ev.origin != "project" or ev.encoding not in ("utf-8", "utf-8-bom"):
                raise _Rejected("base_mismatch", True)
            if evidence_store.probe_path(path).status != "file" or not evidence_store.verify_current_digest(path, ev.sha256).ok:
                raise _Rejected("target_changed", True)
            if not isinstance(intent.get("finding_ids"), (list, tuple)):
                raise _Rejected("finding_not_editable")
            linked = [findings.get(fid) if isinstance(fid, str) else None for fid in intent["finding_ids"]]
            if not linked or any(not f or f["assessment"] != "supported" or f["kind"] not in ("stale_rule", "rule_conflict") or f["suggested_action"] not in ("update", "delete", "narrow_scope") or not f["checks"] or any(c["status"] != "passed" for c in f["checks"]) or not f["support_refs"] or any(not evidence_store.validate_ref(ref).ok for ref in f["support_refs"]) for f in linked):
                raise _Rejected("finding_not_editable")
            if not intent.get("rule_ids") or not set(intent["rule_ids"]) <= {rid for f in linked for rid in f["related_rule_ids"]}:
                raise _Rejected("finding_rule_link_missing")
            operation = intent["operation"]
            if operation not in ("replace_span", "delete_span") or (operation == "delete_span" and any(f["suggested_action"] != "delete" for f in linked)):
                raise _Rejected("operation_mismatch")
            narrowing = any(f["suggested_action"] == "narrow_scope" for f in linked)
            if narrowing:
                if not intent.get("scope_condition") or not intent.get("interpretation_ref") or not evidence_store.validate_ref(intent["interpretation_ref"]).ok:
                    raise _Rejected("scope_condition_requires_confirmation")
                try:
                    scope_condition = dump_object(parse_object("Scope", dump_object(intent["scope_condition"])))
                except ContractError:
                    raise _Rejected("scope_condition_requires_confirmation") from None
            ref = {"evidence_id": ev.evidence_id, "sha256": ev.sha256, "line_start": intent["line_start"], "line_end": intent["line_end"]}
            old = span_bytes(evidence_store, ref)
            if hashlib.sha256(old).hexdigest() != intent["old_text_sha256"]:
                raise _Rejected("span_digest_mismatch", True)
            text = intent.get("replacement_text")
            if text is None or (operation == "replace_span" and not text):
                raise _Rejected("constraint_span_or_exact_replacement_missing")
            if not isinstance(text, str) or "\0" in text or len(text.encode()) > 8192 or (operation == "delete_span" and text):
                raise _Rejected("replacement_invalid", True)
            raw = evidence_store.snapshot_bytes(ev.evidence_id)
            parts = raw.split(b"\n")
            lines = [line + b"\n" for line in parts[:-1]] + ([parts[-1]] if parts[-1] else [])
            if len(lines) != ev.line_count:
                raise _Rejected("line_index_mismatch", True)
            start, end = sum(map(len, lines[:intent["line_start"] - 1])), sum(map(len, lines[:intent["line_end"]]))
            bom = b"\xef\xbb\xbf" if start == 0 and raw.startswith(b"\xef\xbb\xbf") else b""
            old_body = old[len(bom):]
            new_body = b"" if operation == "delete_span" else _replacement(raw, old_body, text, start)
            new = bom + new_body
            if old == new:
                raise _Rejected("no_effect", True)
            if any(fragment not in text for fragment in intent.get("preserved_fragments", [])):
                raise _Rejected("preserved_rule_changed")
            affected_rules = [r for r in registry.current_rules if r["relative_path"] == path and r["line_start"] <= intent["line_end"] and r["line_end"] >= intent["line_start"]]
            for rule in affected_rules:
                if rule["rule_id"] not in intent["rule_ids"]:
                    if rule["rule_id"] not in intent.get("preserved_rule_ids", []) or rule["text"] not in text:
                        raise _Rejected("missing_preserved_rule")
                if operation == "delete_span" and (rule["line_start"] != intent["line_start"] or rule["line_end"] != intent["line_end"]):
                    raise _Rejected("delete_span_not_exact")
            for preserved_id in intent.get("preserved_rule_ids", []):
                preserved = registry.get_rule(preserved_id)
                if not preserved or preserved["relative_path"] != path:
                    raise _Rejected("preserved_rule_scope_unsupported")
            if not intent.get("decision_ids") or not intent.get("basis_refs") or any(not evidence_store.validate_ref(ref).ok for ref in intent["basis_refs"]):
                raise _Rejected("basis_ref_invalid")
            if any(i["blocking"] and set(i["related_ids"]) & set(intent["decision_ids"]) for i in registry.issues):
                raise _Rejected("authority_conflict")
            for did in intent["decision_ids"]:
                decision = registry.get_decision(did)
                if not decision:
                    raise _Rejected("decision_not_effective")
                if operation == "delete_span" and decision["constraints"]:
                    raise _Rejected("retirement_not_explicit")
                for rid in intent["rule_ids"]:
                    rule = registry.get_rule(rid)
                    authorized_scope = linked[0]["scope"] if narrowing else rule["scope"] if rule else None
                    if not rule or registry.decision_applies(did, rule, authorized_scope)["status"] != "accepted" or not covers(decision["scope"], authorized_scope):
                        raise _Rejected("decision_scope_or_supersession_missing")
                    if narrowing and not covers(rule["scope"], scope_condition):
                        raise _Rejected("scope_condition_not_narrower")
                    if operation == "delete_span" and not interpretation_complete(rule):
                        raise _Rejected("retirement_incomplete")
                approval = {"decision_id": did, "target_path": path, "operation": operation, "line_start": intent["line_start"], "line_end": intent["line_end"], "old_text_sha256": intent["old_text_sha256"], "replacement_sha256": hashlib.sha256(new_body).hexdigest(), "rule_ids": intent["rule_ids"]}
                confirmation = registry.confirmation_approves(intent.get("confirmation_id"), approval_key(approval), as_of=registry.as_of)
                if confirmation["status"] != "valid":
                    raise _Rejected("edit_approval_" + confirmation["status"])
            for rid in intent["rule_ids"]:
                rule = registry.get_rule(rid)
                for constraint in rule["constraints"]:
                    replaced = any(not registry.get_decision(did)["constraints"] or any(c["subject"] == constraint["subject"] and conditions_intersect(c.get("conditions", {}), constraint.get("conditions", {})) for c in registry.get_decision(did)["constraints"]) for did in intent["decision_ids"])
                    if not replaced:
                        span = constraint.get("span")
                        if not span:
                            raise _Rejected("preserved_obligation_span_missing")
                        fragment = rule["text"][span["start"]:span["end"]]
                        if fragment not in text.replace("\r\n", "\n"):
                            raise _Rejected("preserved_rule_changed")
            edit = {"edit_id": stable_id("edit_", path, start, end, hashlib.sha256(new).hexdigest()), "finding_id": intent["finding_ids"][0], "relative_path": path, "operation": operation, "line_start": intent["line_start"], "line_end": intent["line_end"], "old_text_sha256": intent["old_text_sha256"], "new_text": text if operation == "replace_span" else None}
            ready.append({"intent": intent, "path": path, "raw": raw, "new": new, "start": start, "end": end, "ev": ev, "edit": edit})
        except (_Rejected, EvidenceError, ValueError, KeyError, TypeError) as error:
            reason = getattr(error, "reason", getattr(error, "code", "invalid_edit_intent"))
            status = "blocked" if getattr(error, "blocked", False) or isinstance(error, EvidenceError) else "needs_confirmation"
            known_ids = [fid for fid in intent.get("finding_ids", []) if isinstance(fid, str) and fid in findings] if isinstance(intent.get("finding_ids", []), (list, tuple)) else []
            if known_ids:
                proposal(known_ids, status, reason)
            rejected_path = intent.get("target_path")
            try:
                rejected_path = normalize_relative_path(rejected_path)
            except (TypeError, ValueError):
                rejected_path = None
            plan.rejections.append({"edit_intent_id": intent.get("edit_intent_id") if isinstance(intent.get("edit_intent_id"), str) else None, "finding_ids": known_ids, "target_path": rejected_path, "reason": reason, "detail": reason, "checks": []})
    # Treat every finding's intents as a unit and reject overlapping edits together.
    bad = {fid for p in plan.proposals for fid in p["finding_ids"]}
    duplicate_keys = {}
    deduplicated = []
    for item in sorted(ready, key=lambda i: (i["path"], i["start"], i["end"], i["edit"]["edit_id"])):
        key = (item["path"], item["start"], item["end"], item["new"], tuple(sorted(item["intent"].get("preserved_rule_ids", []))), tuple(sorted(item["intent"].get("preserved_fragments", []))))
        if key in duplicate_keys:
            existing = duplicate_keys[key]
            existing["intent"] = existing["intent"] | {"finding_ids": sorted(set([*existing["intent"]["finding_ids"], *item["intent"]["finding_ids"]]))}
            continue
        duplicate_keys[key] = item
        deduplicated.append(item)
    ready = deduplicated
    for index, item in enumerate(ready):
        for other in ready[index + 1:]:
            if item["path"] == other["path"] and max(item["start"], other["start"]) < min(item["end"], other["end"]):
                for conflict in (item, other):
                    bad.update(conflict["intent"]["finding_ids"])
                    proposal(conflict["intent"]["finding_ids"], "needs_confirmation", "overlap_conflict")
    # Shared edits connect the atomic groups of all linked findings. Propagate
    # failure across this graph before selecting any remaining edit.
    adjacent = {}
    for item in ready:
        ids = set(item["intent"]["finding_ids"])
        for fid in ids:
            adjacent.setdefault(fid, set()).update(ids - {fid})
    pending = list(bad)
    while pending:
        fid = pending.pop()
        for linked_id in adjacent.get(fid, set()) - bad:
            bad.add(linked_id)
            pending.append(linked_id)
            proposal([linked_id], "needs_confirmation", "dependent_finding_not_ready")
    ready = [item for item in ready if not set(item["intent"]["finding_ids"]) & bad]
    if ready:
        originals, finals = {}, {}
        for item in ready:
            originals[item["path"]] = item["raw"]
        for path, raw in originals.items():
            final = raw
            for item in sorted((i for i in ready if i["path"] == path), key=lambda i: i["start"], reverse=True):
                final = final[:item["start"]] + item["new"] + final[item["end"]:]
            finals[path] = final
        fids = sorted({fid for item in ready for fid in item["intent"]["finding_ids"]})
        bases = [{"relative_path": path, "sha256": hashlib.sha256(raw).hexdigest(), "evidence_id": next(i["ev"].evidence_id for i in ready if i["path"] == path)} for path, raw in sorted(originals.items())]
        try:
            if any(not data.removeprefix(b"\xef\xbb\xbf") or len(data) > 1048576 for data in finals.values()):
                raise _Rejected("file_would_be_empty_or_too_large", True)
            if all(finals[path] == raw for path, raw in originals.items()):
                raise _Rejected("no_effect", True)
            with tempfile.TemporaryDirectory(prefix="context-manager-repair-") as temporary:
                root = Path(temporary)
                build, check = root / "build", root / "check"
                build.mkdir()
                check.mkdir()
                env = {k: os.environ[k] for k in ("PATH", "TMPDIR") if k in os.environ}
                env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, LC_ALL="C", TZ="UTC")
                def git(directory, *args):
                    started = time.monotonic()
                    run = subprocess.run(["git", "-C", str(directory), "-c", "core.autocrlf=false", "-c", "core.safecrlf=false", "-c", "core.quotePath=false", *args], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
                    clean = lambda value: str(value).replace(str(root), "<temp>")
                    plan.checks.append({"command": ["git", *[clean(arg) for arg in args]], "exit_code": run.returncode, "elapsed_ms": round((time.monotonic() - started) * 1000), "stdout_tail": clean(run.stdout[-4096:].decode("utf-8", errors="replace")) if args[0] != "diff" else "", "stderr_tail": clean(run.stderr[-4096:].decode("utf-8", errors="replace"))})
                    if run.returncode:
                        raise _Rejected("git_apply_check_failed", True, run.returncode)
                    return run.stdout
                for directory in (build, check):
                    git(directory, "init", "-q")
                    for path, raw in originals.items():
                        target = directory / path
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(raw)
                git(build, "add", "--", *sorted(originals))
                for path, raw in finals.items():
                    (build / path).write_bytes(raw)
                diff = git(build, "diff", "--no-ext-diff", "--no-textconv", "--no-color", "--unified=3", "--src-prefix=a/", "--dst-prefix=b/", "--", *sorted(originals))
                patch = root / "proposal.diff"
                patch.write_bytes(diff)
                git(check, "apply", "--check", "--whitespace=nowarn", str(patch))
                git(check, "apply", "--whitespace=nowarn", str(patch))
                if any((check / path).read_bytes() != raw for path, raw in finals.items()):
                    raise _Rejected("temp_apply_mismatch", True)
                if any(not evidence_store.verify_current_digest(path, hashlib.sha256(raw).hexdigest()).ok for path, raw in originals.items()):
                    raise _Rejected("target_changed_after_preview", True)
                preview = proposal(fids, "preview_ready", "approved", edits=[i["edit"] for i in ready], bases=bases)
                plan.diff = diff
                plan.diff_sha256 = preview["diff_sha256"] = hashlib.sha256(diff).hexdigest()
                plan.diff_proposal_id = preview["proposal_id"]
                plan.apply_check = {"exit_code": 0, "checked_at": datetime.now(timezone.utc).isoformat(), "applied_in_temp_copy": True, "checked_files": [{"path": b["relative_path"], "sha256": b["sha256"]} for b in bases]}
                plan.hunks = [{"path": i["path"], "old_start": i["intent"]["line_start"], "old_end": i["intent"]["line_end"], "edit_ids": [i["edit"]["edit_id"]], "finding_ids": i["intent"]["finding_ids"]} for i in ready]
        except (_Rejected, OSError, subprocess.SubprocessError) as error:
            reason = getattr(error, "reason", "git_unavailable")
            proposal(fids, "blocked", reason)
            plan.apply_check = {"exit_code": getattr(error, "exit_code", None), "checked_at": datetime.now(timezone.utc).isoformat(), "applied_in_temp_copy": False, "checked_files": []}
    linked_ids = {fid for p in plan.proposals for fid in p["finding_ids"]}
    for finding in findings.values():
        if finding["finding_id"] in linked_ids:
            continue
        if finding["kind"] == "stale_rule":
            proposal([finding["finding_id"]], "needs_confirmation", "exact_edit_intent_missing")
        else:
            action = finding["suggested_action"]
            kind = action if action in ("fix_loading", "fix_retrieval", "add_check") else "manual_edit"
            if finding["kind"] == "cleanup_candidate" and action == "relocate":
                kind = "migrate_clause"
            plan.advisories.append({"advisory_id": stable_id("advisory_", finding["finding_id"]), "finding_ids": [finding["finding_id"]], "kind": kind, "summary": finding["claim"], "rationale": finding["assessment"], "suggested_target": None, "basis_refs": finding["support_refs"], "requires_confirmation": True})
    for item in plan.proposals:
        parse_object("RepairProposal", item)
    return plan
