"""Synthetic incident packages, verified load observations and recurrence (F05)."""
from __future__ import annotations

import ast
import copy
import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field

from .contracts import ContractError, FieldError, Provenance, ProvenanceKind, dump_object, normalize_relative_path, parse_object
from .decisions import DecisionResolver, decode_json, instant, resolve_applicable_rules
from .evidence import EvidenceError, EvidenceStore, prepare_selected_input
from .inspector import stable_id
from .rules import intersection, scope_contains, span_bytes, span_text
from .views import ReadonlyRecord, readonly

CHANNELS = {"project_instructions", "imported_instructions", "rule_directories", "session_injection"}


class IncidentInputError(ContractError):
    def __init__(self, code, field, message="invalid incident input"):
        super().__init__(code, "IncidentInput", [FieldError(field, code, message)])


def _path(value, field):
    if not isinstance(value, str) or not value or value == "." or value.endswith("/") or any(part in ("", ".", "..") for part in value.split("/")):
        raise IncidentInputError("unsafe_path", field)
    try:
        return normalize_relative_path(value)
    except ValueError:
        raise IncidentInputError("unsafe_path", field) from None


@readonly
def parse_incident_input(raw_bytes: bytes, *, project_id: str) -> dict:
    if len(raw_bytes) > 10 * 1024 * 1024:
        raise IncidentInputError("input_too_large", "$")
    try:
        raw_bytes.decode("utf-8-sig")
    except UnicodeError:
        raise IncidentInputError("invalid_utf8", "$") from None
    try:
        data = decode_json(raw_bytes)
    except ContractError as exc:
        raise IncidentInputError(exc.code, "$") from None
    if data.get("synthetic") is not True:
        raise IncidentInputError("not_synthetic", "synthetic")
    if type(data.get("schema_version", 1)) is not int or data.get("schema_version", 1) != 1:
        raise IncidentInputError("unsupported_schema_version", "schema_version")
    if not isinstance(data.get("incidents"), list):
        raise IncidentInputError("invalid_field", "incidents")
    legacy = "format" not in data and "case_id" in data and any(isinstance(i, dict) and "rule_observation" in i for i in data.get("incidents", []))
    if legacy:
        for index, event in enumerate(data["incidents"]):
            if not isinstance(event, dict) or any(not isinstance(event.get(k), str) or not event[k] for k in ("event_id", "session_id", "harness")) or not isinstance(event.get("action"), dict):
                raise IncidentInputError("invalid_field", f"incidents[{index}]")
            _path(event["action"].get("path"), f"incidents[{index}].action.path")
        data["package_format"] = "legacy-demo-v0"
        return data
    if data.get("format") != "context-manager-incidents":
        raise IncidentInputError("unsupported_format", "format")
    if "schema_version" not in data:
        raise IncidentInputError("invalid_field", "schema_version")
    if data.get("project_id") != project_id:
        raise IncidentInputError("project_id_mismatch", "project_id")
    for name, limit in (("files", 1000), ("incidents", 10000), ("receipts", 10000), ("check_results", 10000)):
        if name in ("files", "incidents") and name not in data:
            raise IncidentInputError("invalid_field", name)
        items = data.setdefault(name, [])
        if not isinstance(items, list) or len(items) > limit or any(not isinstance(i, dict) for i in items):
            raise IncidentInputError("max_items_exceeded" if isinstance(items, list) and len(items) > limit else "invalid_field", name)
    file_ids = set()
    for index, item in enumerate(data["files"]):
        base = f"files[{index}]"
        if any(not isinstance(item.get(k), str) or not item[k] for k in ("file_ref", "path", "revision", "kind")) or not isinstance(item.get("content"), str):
            raise IncidentInputError("invalid_field", base)
        _path(item["path"], base + ".path")
        _path(item["revision"], base + ".revision")
        if item["kind"] not in ("rule", "code", "log", "config", "diff", "event_source") or item.get("encoding", "utf-8") != "utf-8" or item["file_ref"] in file_ids:
            raise IncidentInputError("invalid_field", base)
        file_ids.add(item["file_ref"])
        try:
            item["content"].encode("utf-8")
        except UnicodeError:
            raise IncidentInputError("invalid_utf8", base + ".content") from None
    def references(value, path):
        if isinstance(value, dict):
            if "file_ref" in value:
                if set(value) - {"file_ref", "line_start", "line_end"} or not isinstance(value["file_ref"], str):
                    raise IncidentInputError("invalid_field", path)
            for key, child in value.items():
                references(child, path + "." + key)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                references(child, f"{path}[{index}]")
    for index, event in enumerate(data["incidents"]):
        base = f"incidents[{index}]"
        for key in ("event_id", "session_id", "harness"):
            if not isinstance(event.get(key), str) or not event[key]:
                raise IncidentInputError("invalid_field", base + "." + key)
        if not re.fullmatch(r'[a-z][a-z0-9_]{0,31}', event["harness"]):
            raise IncidentInputError("invalid_field", base + ".harness")
        action = event.get("action")
        if not isinstance(action, dict) or action.get("kind") not in ("edit", "tool_result", "mention"):
            raise IncidentInputError("invalid_field", base + ".action")
        _path(action.get("target_path"), base + ".action.target_path")
        if "code_version" in event:
            version = event["code_version"]
            field = base + ".code_version"
            if not isinstance(version, dict) or set(version) - {"observed_ref", "required_ref", "decision_id"}:
                raise IncidentInputError("invalid_field", field)
            for key in ("observed_ref", "required_ref"):
                ref = version.get(key)
                if (key == "observed_ref" or ref is not None) and (not isinstance(ref, dict) or "file_ref" not in ref):
                    raise IncidentInputError("invalid_field", field + "." + key)
            decision_id = version.get("decision_id")
            if decision_id is not None and (not isinstance(decision_id, str) or not decision_id):
                raise IncidentInputError("invalid_field", field + ".decision_id")
        if "change" in action and action["change"] not in ("create", "modify", "delete"):
            raise IncidentInputError("invalid_field", base + ".action.change")
        if "scope" in event:
            parse_object("Scope", {"include_prefixes": event["scope"]})
        if not isinstance(event.get("exception_rule_ids", []), list) or any(not isinstance(rid, str) or not rid for rid in event.get("exception_rule_ids", [])):
            raise IncidentInputError("invalid_field", base + ".exception_rule_ids")
        if event.get("occurred_at") is not None:
            try:
                instant(event["occurred_at"])
            except (TypeError, ValueError, AttributeError):
                raise IncidentInputError("invalid_field", base + ".occurred_at") from None
        for key in ("task_id", "attempt_id", "context_epoch", "snapshot_id"):
            if event.get(key) is not None and (not isinstance(event[key], str) or not event[key]):
                raise IncidentInputError("invalid_field", base + "." + key)
        references(event, base)
    for index, receipt in enumerate(data["receipts"]):
        base = f"receipts[{index}]"
        for key in ("receipt_id", "session_id", "harness", "before_event_id"):
            if not isinstance(receipt.get(key), str) or not receipt[key]:
                raise IncidentInputError("invalid_field", base + "." + key)
        if receipt.get("coverage") not in ("complete", "partial", "unknown") or not isinstance(receipt.get("entries"), list):
            raise IncidentInputError("invalid_field", base)
        if not isinstance(receipt.get("channels_covered", []), list) or any(not isinstance(c, str) for c in receipt.get("channels_covered", [])):
            raise IncidentInputError("invalid_field", base + ".channels_covered")
        if not isinstance(receipt.get("provenance", {}), dict):
            raise IncidentInputError("invalid_field", base + ".provenance")
        for entry in receipt["entries"]:
            if not isinstance(entry, dict) or any(not isinstance(entry.get(k), str) or not entry[k] for k in ("rule_id", "text_sha256", "channel")) or not re.fullmatch(r'[0-9a-f]{64}', entry["text_sha256"]):
                raise IncidentInputError("invalid_field", base + ".entries")
            if entry.get("scope") is not None:
                parse_object("Scope", {"include_prefixes": entry["scope"]})
        if receipt.get("captured_at") is not None:
            try:
                instant(receipt["captured_at"])
            except (TypeError, ValueError, AttributeError):
                raise IncidentInputError("invalid_field", base + ".captured_at") from None
        references(receipt, base)
    for index, row in enumerate(data["check_results"]):
        base = f"check_results[{index}]"
        if any(not isinstance(row.get(k), str) or not row[k] for k in ("check_id", "event_id", "input_sha256")) or row.get("status") not in ("passed", "failed", "error", "not_run") or not re.fullmatch(r'[0-9a-f]{64}', row["input_sha256"]):
            raise IncidentInputError("invalid_field", base)
        references(row, base)
    if "channel_profile" in data:
        profile = data["channel_profile"]
        if not isinstance(profile, dict) or profile.get("source") != "synthetic" or not isinstance(profile.get("channels"), dict):
            raise IncidentInputError("invalid_field", "channel_profile")
        for channels in profile["channels"].values():
            if not isinstance(channels, list) or any(not isinstance(c, str) or c not in CHANNELS for c in channels):
                raise IncidentInputError("invalid_field", "channel_profile.channels")
    for name in ("task_scope",):
        if name in data:
            parse_object("Scope", {"include_prefixes": data[name]})
    data["package_format"] = "v1"
    return data


@dataclass
class IncidentAnalysis(ReadonlyRecord):
    project_id: str
    input_id: str
    package_sha256: str
    package_format: str = "v1"
    format: str = "context-manager-incidents"
    schema_version: int = 1
    synthetic: bool = True
    proof_scope: str = "synthetic_case"
    status: str = "complete"
    events: list[dict] = field(default_factory=list)
    receipts: list[dict] = field(default_factory=list)
    loads: list[dict] = field(default_factory=list)
    behaviors: list[dict] = field(default_factory=list)
    recurrence: list[dict] = field(default_factory=list)
    exclusions: list[dict] = field(default_factory=list)
    gaps: list[dict] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)
    coverage_items: list[dict] = field(default_factory=list)
    counts: dict = field(default_factory=dict)
    coverage: dict = field(default_factory=dict)
    dedup: dict = field(default_factory=dict)
    legacy: dict | None = None
    extra_fields: dict = field(default_factory=dict)

    def to_dict(self):
        return dump_object(self)


def _analyze_incidents(data: dict, evidence_store: EvidenceStore, resolver: DecisionResolver, *, project_id: str, input_id: str, package_sha256: str, task_scope, channel_profile=None) -> IncidentAnalysis:
    data = dump_object(data)
    result = IncidentAnalysis(project_id, input_id, package_sha256, package_format=data["package_format"])
    result.extra_fields = {k: copy.deepcopy(v) for k, v in data.items() if k not in {"format", "schema_version", "synthetic", "project_id", "task_scope", "channel_profile", "files", "incidents", "receipts", "check_results", "package_format", "case_id", "symptom", "scope", "snapshot", "decisions"}}
    def gap(code, *, event_id=None, receipt_id=None, effect="unknown", field="$", **extra):
        item = {"code": code, "field": field, "message": code, "effect": effect, "event_id": event_id, "receipt_id": receipt_id} | extra
        result.gaps.append(item)
        return item
    if data["package_format"] == "legacy-demo-v0":
        claims = []
        for event in data["incidents"]:
            observation = event.get("rule_observation", {})
            claims.append({"event_id": event["event_id"], "receipt_id": observation.get("receipt_id"), "claimed_state": observation.get("state"), "verified_state": "unknown", "reason": "legacy_receipt_only"})
            result.events.append({"event_id": event["event_id"], "session_id": event["session_id"], "harness": event["harness"], "task_id": event.get("task_id"), "attempt_id": None, "occurred_at": event.get("occurred_at"), "context_epoch": None, "snapshot_id": None, "source_ref": None, "scope": dump_object(task_scope), "status": "clue_only", "action": {"kind": "edit", "target_path": event["action"]["path"], "before_ref": None, "after_ref": None, "evidence_ref": None, "change": "create", "claims": copy.deepcopy(event["action"]), "claim_only": True}})
            result.exclusions.append({"event_id": event["event_id"], "reason": "legacy_claim", "detail": "historical example has no verifiable action snapshots"})
        result.legacy = {"detected_format": "legacy-demo-v0", "requires_migration": True, "message": "Supply embedded files, action snapshots and full load receipts.", "metadata": {k: data.get(k) for k in ("case_id", "symptom", "scope", "snapshot", "decisions")}, "claims": claims, "warnings": ["legacy_snapshot_label"]}
        gap("legacy_format")
    else:
        files = {}
        file_paths = {}
        for item in sorted(data["files"], key=lambda i: i["file_ref"]):
            evidence = evidence_store.capture_imported_bytes(item["content"].encode("utf-8"), input_id=input_id, logical_name="case/" + item["revision"] + "/" + item["path"], kind="rule_file" if item["kind"] == "rule" else "code" if item["kind"] == "code" else "document", purpose="incident_material")
            files[item["file_ref"]], file_paths[item["file_ref"]] = evidence, item["path"]
            if item.get("sha256") and evidence.sha256 != item["sha256"]:
                gap("embedded_digest_mismatch", effect="warning", field=item["file_ref"])
        def resolve(ref, *, path=None):
            if not isinstance(ref, dict) or ref.get("file_ref") not in files:
                return None
            ev = files[ref["file_ref"]]
            if path is not None and file_paths[ref["file_ref"]] != path:
                return None
            try:
                reference = dump_object(evidence_store.make_ref(ev, ref.get("line_start", 1), ref.get("line_end", ev.line_count)))
                return reference if evidence_store.validate_ref(reference).ok else None
            except (EvidenceError, TypeError, ValueError):
                return None
        originals = defaultdict(list)
        for event in data["incidents"]:
            originals[event["event_id"]].append(event)
        conflicted = {eid for eid, events in originals.items() if len({json.dumps(e, sort_keys=True) for e in events}) > 1}
        signatures, aliases, handled_ids = {}, {}, set()
        duplicates = 0
        for original in sorted(data["incidents"], key=lambda e: (e.get("occurred_at") is None, instant(e["occurred_at"]).isoformat() if e.get("occurred_at") else "", e["event_id"])):
            event = dump_object(original)
            eid = event["event_id"]
            if eid in handled_ids:
                if eid not in conflicted:
                    duplicates += 1
                continue
            handled_ids.add(eid)
            for key in ("task_id", "attempt_id", "occurred_at", "context_epoch", "snapshot_id", "source_ref"):
                event.setdefault(key, None)
                if event[key] is None:
                    gap("missing_" + ("snapshot" if key == "snapshot_id" else key), event_id=eid, field=key)
            action = event["action"]
            for key in ("before_ref", "after_ref", "result_ref", "diff_ref", "quote_ref", "evidence_ref"):
                if key in action:
                    ref = resolve(action[key], path=action["target_path"] if key in ("before_ref", "after_ref") else None)
                    if ref is None:
                        gap("ref_unresolved", event_id=eid, field="action." + key, effect="rejected")
                    action[key] = ref
            action.setdefault("evidence_ref", action.get("after_ref") or action.get("result_ref") or action.get("quote_ref"))
            if "code_version" in event:
                version = event["code_version"]
                for key in ("observed_ref", "required_ref"):
                    version[key] = resolve(version.get(key), path=action["target_path"])
            action.setdefault("change", "modify" if action.get("before_ref") and action.get("after_ref") else "delete" if action.get("before_ref") else "create")
            event["source_ref"] = resolve(event["source_ref"]) if event["source_ref"] else None
            event_scope = {"include_prefixes": original.get("scope", data.get("task_scope", scope_data(task_scope)["include_prefixes"])), "exclude_prefixes": [], "harnesses": []}
            event["scope"] = intersection(event_scope, task_scope)
            event["status"] = "conflicted" if eid in conflicted else "normalized"
            if event["status"] == "conflicted":
                gap("duplicate_event_conflict", event_id=eid, effect="rejected")
            version = event.get("code_version", {})
            version_identity = {key: (ref.get("sha256") if isinstance(ref, dict) else ref) for key, ref in version.items()}
            signature = json.dumps([event.get("task_id"), action["kind"], action["target_path"], action.get("change"), [(key, action[key].get("sha256") if isinstance(action[key], dict) else None) for key in sorted(action) if key.endswith("_ref")], version_identity], sort_keys=True)
            key = signature if event.get("task_id") else eid
            if key in signatures and eid not in conflicted:
                aliases[eid] = signatures[key]
                duplicates += 1
                result.exclusions.append({"event_id": eid, "reason": "duplicate", "detail": signatures[key]})
                continue
            signatures[key], aliases[eid] = eid, eid
            result.events.append(event)
        event_map = {e["event_id"]: e for e in result.events}
        raw_receipts = defaultdict(list)
        for receipt in data["receipts"]:
            raw_receipts[receipt["receipt_id"]].append(receipt)
        profile = channel_profile or data.get("channel_profile")
        if not isinstance(profile, Mapping) or profile.get("source") not in ("synthetic", "f03"):
            profile = {}
        for rid, entries in sorted(raw_receipts.items()):
            receipt = dump_object(entries[0])
            rejected = len({json.dumps(r, sort_keys=True) for r in entries}) > 1
            anchor = event_map.get(aliases.get(receipt["before_event_id"], receipt["before_event_id"]))
            reasons = ["duplicate_receipt_id"] if rejected else []
            provenance = receipt.get("provenance", {})
            if not anchor:
                reasons.append("dangling_receipt")
            elif receipt["session_id"] != anchor["session_id"]:
                reasons.append("receipt_session_mismatch")
            elif receipt["harness"] != anchor["harness"]:
                reasons.append("receipt_harness_mismatch")
            elif receipt.get("captured_at") and anchor.get("occurred_at") and instant(receipt["captured_at"]) > instant(anchor["occurred_at"]):
                reasons.append("receipt_after_action")
            if not isinstance(provenance, dict) or provenance.get("synthetic") is not True or not provenance.get("adapter"):
                reasons.append("provenance_missing")
            required = profile.get("channels", {}).get(receipt["harness"], [])
            valid_entries = []
            for index, entry in enumerate(receipt["entries"]):
                ref = resolve(entry.get("source_ref"))
                errors = []
                digest = None
                if ref is None:
                    errors.append("entry_unresolved")
                else:
                    try:
                        read = evidence_store.read_evidence(ref, caller="inspector")
                        if read.truncated:
                            errors.append("entry_unresolved")
                        else:
                            digest = read.span_sha256
                            if digest != entry.get("text_sha256"):
                                errors.append("entry_digest_mismatch")
                    except EvidenceError:
                        errors.append("entry_unresolved")
                if entry.get("channel") not in (required or CHANNELS):
                    errors.append("entry_channel_mismatch")
                valid_entries.append({"index": index, "rule_id": entry.get("rule_id"), "evidence_ref": ref, "span_sha256": digest, "channel": entry.get("channel"), "scope": entry.get("scope"), "status": "invalid" if errors else "valid", "issues": errors})
            complete = receipt["coverage"] == "complete" and bool(required) and set(required) <= set(receipt.get("channels_covered", [])) and bool(provenance.get("adapter")) and all(e["status"] == "valid" for e in valid_entries)
            epoch_unknown = anchor and (not anchor.get("context_epoch") or not receipt.get("context_epoch") or anchor["context_epoch"] != receipt["context_epoch"] or not anchor.get("occurred_at") or anchor["status"] == "conflicted")
            if epoch_unknown:
                reasons.append("receipt_epoch_mismatch")
            status = "unknown" if epoch_unknown and not rejected and set(reasons) <= {"receipt_epoch_mismatch"} else "rejected" if rejected or reasons else "verified" if complete or any(e["status"] == "valid" for e in valid_entries) else "unknown"
            validation = {"receipt_id": rid, "session_id": receipt["session_id"], "harness": receipt["harness"], "context_epoch": receipt.get("context_epoch"), "before_event_id": anchor["event_id"] if anchor else receipt["before_event_id"], "captured_at": receipt.get("captured_at"), "coverage_declared": receipt["coverage"], "coverage_effective": "complete" if complete else "partial" if receipt["coverage"] == "complete" else receipt["coverage"], "channels_covered": receipt.get("channels_covered", []), "required_channels": required, "entries": valid_entries, "status": status, "usable": status == "verified", "referenced_by_event_ids": [], "provenance": {"kind": "synthetic", "adapter": provenance.get("adapter"), "version": provenance.get("protocol_version")}, "issues": reasons, "synthetic": True}
            if not required:
                validation["issues"].append("channel_set_unknown")
            elif receipt["coverage"] == "complete" and not set(required) <= set(receipt.get("channels_covered", [])):
                validation["issues"].append("coverage_channel_gap")
            if receipt["coverage"] != "complete":
                validation["issues"].append("coverage_incomplete")
            validation["issues"].extend(code for entry in valid_entries for code in entry["issues"])
            result.receipts.append(validation)
            if status == "rejected":
                gap(reasons[0] if reasons else "invalid_receipt", receipt_id=rid, effect="rejected")
        literal_cache, assignment_refs = {}, {}
        def literal(ref, symbol):
            if not ref:
                return None, "missing_after_snapshot"
            key = (ref["evidence_id"], symbol)
            if key in literal_cache:
                return literal_cache[key]
            ev = evidence_store.get(ref["evidence_id"])
            text = evidence_store.read_raw(ev.evidence_id, max_bytes=10 * 1024 * 1024).decode("utf-8-sig")
            try:
                tree = ast.parse(text)
            except (SyntaxError, ValueError, RecursionError):
                return None, "parse_error"
            values = []
            for node in tree.body:
                target = node.targets[0] if isinstance(node, ast.Assign) and len(node.targets) == 1 else node.target if isinstance(node, ast.AnnAssign) else None
                if isinstance(target, ast.Name) and target.id == symbol:
                    values.append(node.value.value if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str) else None)
                    assignment_refs[key] = dump_object(evidence_store.make_ref(ev, node.lineno, node.end_lineno or node.lineno))
            literal_cache[key] = (values[-1], "symbol_not_string" if values[-1] is None else "multiple_assignments" if len(set(values)) > 1 else None) if values else (None, "symbol_not_found")
            return literal_cache[key]
        for event in result.events:
            eid, action = event["event_id"], event["action"]
            time = event["occurred_at"]
            in_scope = bool(event["scope"]) and scope_contains(event["scope"], action["target_path"], event["harness"])
            if not time:
                for rule in resolver.registry.rules:
                    if in_scope and not rule.get("binding_failed") and rule["harness"] in ("shared", event["harness"]) and scope_contains(rule["scope"], action["target_path"], event["harness"]):
                        result.loads.append({"event_id": eid, "rule_id": rule["rule_id"], "state": "unknown", "load_proof": "none", "receipt_ids": [], "matched_receipt_id": None, "matched_entry_index": None, "proof_receipt_id": None, "proof_checks": {}, "coverage": "unknown", "reasons": ["missing_occurred_at"], "gaps": [], "synthetic": True, "proof_scope": "synthetic_case"})
                for check in resolver.registry.behavior_checks:
                    if in_scope and scope_contains(check["scope"], action["target_path"], event["harness"]):
                        result.behaviors.append({"observation_id": stable_id("bo_", eid, check["check_id"]), "event_id": eid, "check_id": check["check_id"], "rule_id": None, "status": "unknown", "reasons": ["missing_occurred_at"], "evidence_refs": [], "counts_toward_recurrence": False, "synthetic": True})
                continue
            policy = resolver.policy_at(time)
            rules = resolve_applicable_rules(policy, action["target_path"], harness=event["harness"]) if in_scope else ()
            applicable_receipts = []
            for receipt in result.receipts:
                anchor = event_map.get(receipt["before_event_id"])
                if receipt["session_id"] == event["session_id"] and anchor and anchor.get("occurred_at") and instant(anchor["occurred_at"]) <= instant(time):
                    applicable_receipts.append(receipt)
                elif receipt["before_event_id"] == eid:
                    applicable_receipts.append(receipt)
            for rule in rules:
                proofs, reasons = [], []
                for receipt in applicable_receipts:
                    checks = {"same_session": receipt["session_id"] == event["session_id"], "same_harness": receipt["harness"] == event["harness"], "same_epoch": bool(event.get("context_epoch")) and receipt["context_epoch"] == event["context_epoch"], "before_action": not receipt["captured_at"] or instant(receipt["captured_at"]) <= instant(time), "order_verified": True, "channels_complete": receipt["coverage_effective"] == "complete", "entry_present": False, "text_sha_matches": False}
                    receipt["referenced_by_event_ids"].append(eid)
                    if not receipt["usable"] or not all(checks[k] for k in ("same_session", "same_harness", "same_epoch", "before_action")) or event["status"] == "conflicted":
                        reasons += receipt["issues"] + (["receipt_epoch_mismatch"] if not checks["same_epoch"] else []) + (["receipt_after_action"] if not checks["before_action"] else [])
                        continue
                    if receipt["before_event_id"] != eid:
                        result.warnings.append({"code": "receipt_anchor_offset", "event_id": eid, "receipt_id": receipt["receipt_id"], "message": "receipt is anchored before an earlier action in the same context epoch"})
                    matching = next((entry for entry in receipt["entries"] if entry["status"] == "valid" and entry["span_sha256"] == rule["text_sha256"] and (not entry["scope"] or scope_contains({"include_prefixes": entry["scope"], "exclude_prefixes": []}, action["target_path"]))), None)
                    if matching:
                        if matching["rule_id"] != rule["rule_id"]:
                            reasons.append("entry_rule_id_mismatch")
                        checks.update(entry_present=True, text_sha_matches=True)
                        proofs.append(("observed_loaded", receipt, matching, checks))
                    elif checks["channels_complete"] and all(not entry["scope"] or scope_contains({"include_prefixes": entry["scope"], "exclude_prefixes": []}, action["target_path"]) for entry in receipt["entries"]):
                        checks["text_sha_matches"] = True
                        proofs.append(("observed_not_loaded", receipt, None, checks))
                        if any(entry["rule_id"] == rule["rule_id"] and entry["span_sha256"] != rule["text_sha256"] for entry in receipt["entries"]):
                            reasons.append("different_version_in_context")
                    else:
                        reasons.extend(["coverage_incomplete"] + [code for entry in receipt["entries"] for code in entry["issues"]])
                states = {p[0] for p in proofs}
                selected = sorted(proofs, key=lambda p: p[1]["receipt_id"])[0] if len(states) == 1 else None
                if len(states) > 1:
                    reasons.append("conflicting_receipts")
                state = selected[0] if selected else "unknown"
                result.loads.append({"event_id": eid, "rule_id": rule["rule_id"], "state": state, "load_proof": "matched_entry" if state == "observed_loaded" else "complete_exclusion" if state == "observed_not_loaded" else "none", "receipt_ids": [r["receipt_id"] for r in applicable_receipts], "matched_receipt_id": selected[1]["receipt_id"] if selected and selected[2] else None, "matched_entry_index": selected[2]["index"] if selected and selected[2] else None, "proof_receipt_id": selected[1]["receipt_id"] if selected else None, "proof_checks": selected[3] if selected else {}, "coverage": selected[1]["coverage_effective"] if selected else "unknown", "reasons": sorted(set(reasons or (["coverage_incomplete" if applicable_receipts else "missing_receipt"] if not selected else []))), "gaps": [], "synthetic": True, "proof_scope": "synthetic_case"})
            for check in policy.effective_checks:
                if check["parameters"].get("path") and check["parameters"]["path"] != action["target_path"]:
                    continue
                applicable_ids = {rule["rule_id"] for rule in rules}
                rule_ids = sorted(set(check.get("rule_ids", [])) & applicable_ids)
                rid = rule_ids[0] if rule_ids else check.get("rule_id")
                obs = {"observation_id": stable_id("bo_", eid, check["check_id"]), "event_id": eid, "rule_id": rid, "constraint_id": check["constraint"]["constraint_id"], "check_id": check["check_id"], "detector": check["detector"], "status": "unknown", "violation_kind": None, "operator": check["constraint"]["operator"], "observed_value": None, "expected_value": check["expected_value"], "decision_id": check["decision_id"], "decision_status": "accepted", "change": {}, "evidence_refs": [action["evidence_ref"]] if action.get("evidence_ref") else [], "reasons": [], "gaps": [], "counts_toward_recurrence": False, "synthetic": True}
                if event["status"] == "conflicted":
                    obs["reasons"] = ["conflicted_event"]
                elif not event["scope"] or not scope_contains(event["scope"], action["target_path"], event["harness"]):
                    obs.update(status="not_applicable", reasons=["out_of_scope"])
                elif event.get("exception_rule_ids") and (exception := resolver.resolve_constraint(check["subject"], action["target_path"], as_of=time, harness=event["harness"], exception_rule_ids=event["exception_rule_ids"]))["exception_applied"] and exception["status"] == "resolved":
                    obs.update(status="exception", reasons=["exception_applied"], exception_applied=True)
                elif not scope_contains(check["scope"], action["target_path"], event["harness"]):
                    obs.update(status="not_applicable", reasons=["out_of_scope"])
                elif not rule_ids:
                    obs.update(status="not_applicable", reasons=["check_rule_not_in_scope"])
                elif action["kind"] == "mention":
                    obs.update(status="not_applicable", reasons=["mention"])
                elif check["detector"] == "python_assignment_literal" and action["kind"] == "edit":
                    resolution = resolver.resolve_constraint(check["subject"], action["target_path"], as_of=time, harness=event["harness"])
                    if not action["target_path"].endswith(".py"):
                        obs["reasons"] = ["unsupported_language"]
                    elif action.get("change") == "delete" and action.get("before_ref"):
                        obs.update(status="no_violation", reasons=["symbol_removed"])
                    else:
                        value, reason = literal(action.get("after_ref"), check["parameters"]["symbol"])
                        if action.get("after_ref"):
                            obs["input_evidence_id"] = action["after_ref"]["evidence_id"]
                            obs["input_sha256"] = action["after_ref"]["sha256"]
                            actual_ref = assignment_refs.get((action["after_ref"]["evidence_id"], check["parameters"]["symbol"]))
                            if actual_ref and actual_ref not in obs["evidence_refs"]:
                                obs["evidence_refs"].append(actual_ref)
                        if reason == "multiple_assignments":
                            obs["reasons"].append(reason)
                            reason = None
                        if resolution["operator"] and resolution["operator"] != check["constraint"]["operator"]:
                            reason = "operator_mismatch"
                        if reason or resolution["status"] != "resolved":
                            before, _ = literal(action.get("before_ref"), check["parameters"]["symbol"])
                            if reason == "symbol_not_found" and before is not None:
                                obs.update(status="no_violation", reasons=["symbol_removed"])
                            else:
                                obs["reasons"] = [reason or "decision_unresolved"]
                        else:
                            expected = resolution["value"]
                            violation = (value != expected) if resolution["operator"] == "equals" else (value == expected)
                            obs.update(status="violation" if violation else "no_violation", observed_value=value, expected_value=expected, violation_kind="literal_mismatch" if violation else None)
                            before_value, _ = literal(action.get("before_ref"), check["parameters"]["symbol"])
                            obs["change"] = {"before_value": before_value, "after_value": value, "symbol": check["parameters"]["symbol"], "path": action["target_path"]}
                            if not violation and before_value is not None and ((before_value != expected) if resolution["operator"] == "equals" else (before_value == expected)):
                                obs["reasons"].append("value_corrected")
                elif check["detector"] == "structured_check_result":
                    rows = [r for r in data["check_results"] if r.get("check_id") in (check["check_id"], check["parameters"].get("result_check_id")) and aliases.get(r.get("event_id"), r.get("event_id")) == eid]
                    if len({(r.get("status"), r.get("input_sha256")) for r in rows}) > 1:
                        obs["reasons"] = ["conflicting_check_results"]
                    elif not rows:
                        obs["reasons"] = ["check_result_missing"]
                    else:
                        row = rows[0]
                        raw_ref = row.get("input_ref")
                        reference = resolve(raw_ref) if raw_ref else action.get("after_ref") or action.get("result_ref")
                        if raw_ref and ("line_start" in raw_ref or "line_end" in raw_ref):
                            obs["reasons"] = ["check_input_invalid"]
                        elif not reference or reference["sha256"] != row.get("input_sha256"):
                            obs["reasons"] = ["check_input_digest_mismatch"]
                        elif action["kind"] == "edit" and (not action.get("after_ref") or reference["evidence_id"] != action["after_ref"]["evidence_id"]):
                            obs["reasons"] = ["check_action_mismatch"]
                        elif raw_ref and file_paths.get(raw_ref["file_ref"]) != action["target_path"]:
                            obs["reasons"] = ["check_action_mismatch"]
                        elif row.get("status") in ("passed", "failed"):
                            obs["input_evidence_id"], obs["input_sha256"] = reference["evidence_id"], reference["sha256"]
                            if reference not in obs["evidence_refs"]:
                                obs["evidence_refs"].append(reference)
                            obs.update(status="violation" if row["status"] == "failed" else "no_violation", violation_kind="check_failed" if row["status"] == "failed" else None)
                        else:
                            obs["reasons"] = ["check_result_inconclusive"]
                else:
                    obs.update(status="clue_only", reasons=["tool_result_only"])
                obs["counts_toward_recurrence"] = obs["status"] == "violation" and bool(rule_ids)
                for related_rule in rule_ids or [rid]:
                    result.behaviors.append(obs | {"rule_id": related_rule, "observation_id": stable_id("bo_", eid, check["check_id"], related_rule)})
                if obs["status"] != "violation":
                    result.exclusions.append({"event_id": eid, "reason": obs["reasons"][0] if obs["reasons"] else obs["status"], "check_id": check["check_id"], "rule_id": rid, "detail": "not_counted"})
        groups = defaultdict(list)
        for obs in result.behaviors:
            event = event_map[obs["event_id"]]
            if obs["counts_toward_recurrence"]:
                key = (obs["rule_id"], obs["constraint_id"] or obs["check_id"], json.dumps(event["scope"], sort_keys=True), obs["violation_kind"])
                groups[key].append(obs)
        for key, observations in sorted(groups.items()):
            events = [event_map[eid] for eid in sorted({o["event_id"] for o in observations})]
            tasks = sorted({e["task_id"] for e in events if e["task_id"]})
            complete = all(e["task_id"] for e in events)
            result.recurrence.append({"group_id": stable_id("rec-", project_id, *key), "project_id": project_id, "rule_id": key[0], "constraint_id": key[1], "check_ids": sorted({o["check_id"] for o in observations}), "task_scope": events[0]["scope"]["include_prefixes"], "scope_unknown": False, "violation_kind": key[3], "status": "recurrence" if len(tasks) >= 2 else "single_task" if complete else "insufficient_task_identity", "meets_recurrence_threshold": len(tasks) >= 2, "confirmed_violation_event_ids": [e["event_id"] for e in events], "related_event_ids": sorted(eid for eid, canonical in aliases.items() if canonical in {e["event_id"] for e in events}), "confirmed_violation_count": len(events), "independent_task_ids": tasks, "confirmed_independent_task_count": len(tasks), "task_identity_complete": complete, "duplicate_event_ids": [], "exception_event_ids": [], "unknown_event_ids": [], "gaps": [], "evidence_refs": [ref for o in observations for ref in o["evidence_refs"]], "synthetic": True})
        result.dedup = {"signature_version": 1, "raw_event_count": len(data["incidents"]), "canonical_event_count": sum(event["status"] == "normalized" for event in result.events), "duplicate_event_count": duplicates, "conflicted_event_count": sum(len(originals[eid]) for eid in conflicted), "groups": [{"canonical_event_id": eid, "event_ids": sorted(k for k, v in aliases.items() if v == eid), "reason": "same_task_action_content", "signature_strength": "strong"} for eid in sorted(set(aliases.values()))]}
    result.counts = {"raw_event_count": len(data["incidents"]), "canonical_event_count": result.dedup.get("canonical_event_count", len(result.events)), "duplicate_event_count": result.dedup.get("duplicate_event_count", 0), "conflicted_event_count": result.dedup.get("conflicted_event_count", 0), "invalid_event_count": 0, "confirmed_violation_actions": len({b["event_id"] for b in result.behaviors if b["counts_toward_recurrence"]}), "confirmed_independent_tasks": len({e.get("task_id") for e in result.events if e.get("task_id") and any(b["event_id"] == e["event_id"] and b["counts_toward_recurrence"] for b in result.behaviors)}), "excluded_event_count": len({e["event_id"] for e in result.exclusions})}
    result.coverage = {"events": {"total": len(data["incidents"]), "normalized": len(result.events), "evaluated": len({b["event_id"] for b in result.behaviors if b["status"] != "unknown"}), "unknown": len({b["event_id"] for b in result.behaviors if b["status"] == "unknown"})}, "receipts": {"total": len(result.receipts), **{state: sum(r["status"] == state for r in result.receipts) for state in ("verified", "rejected", "unknown")}}, "loads": {state: sum(l["state"] == state for l in result.loads) for state in ("observed_loaded", "observed_not_loaded", "unknown")}, "behaviors": {state: sum(b["status"] == state for b in result.behaviors) for state in ("violation", "no_violation", "exception", "not_applicable", "unknown", "clue_only")}, "mechanical": {"detectors_supported": ["python_assignment_literal", "structured_check_result"], "checks_run": len(result.behaviors), "checks_unsupported": sum("unsupported_detector" in b.get("reasons", []) for b in result.behaviors), "checks_unconfirmed": sum(i["code"] == "check_not_confirmed" for i in result.gaps), "languages": ["python"]}}
    if not result.events:
        result.warnings.append({"code": "empty_package", "message": "no incident events supplied"})
    for name in ("bundle", "receipts", "loads", "behaviors", "recurrence"):
        rejected = name == "bundle" and any(g["effect"] == "rejected" for g in result.gaps)
        missing = result.legacy is not None or (name in ("receipts", "loads") and any(l["state"] == "unknown" for l in result.loads)) or (name == "behaviors" and any(b["status"] == "unknown" for b in result.behaviors))
        result.coverage_items.append({"check_id": "f05." + name, "status": "failed" if rejected else "not_run" if missing else "passed", "inputs": [], "reason": "invalid_incident_material" if rejected else "legacy_material_gap" if result.legacy else "incident_material_gap" if missing else "none_found" if not result.events or name in ("receipts", "loads") and not result.loads else None, "artifact_refs": []})
    result.status = "partial" if any(c["status"] != "passed" for c in result.coverage_items) else "complete"
    result.loads.sort(key=lambda o: (o["event_id"], o["rule_id"]))
    for receipt in result.receipts:
        receipt["referenced_by_event_ids"] = sorted(set(receipt["referenced_by_event_ids"]))
    result.behaviors.sort(key=lambda o: (o["event_id"], o["check_id"]))
    result.exclusions.sort(key=lambda o: (o["event_id"], o["reason"], o.get("check_id", "")))
    return result


def scope_data(value):
    return dump_object(value)


@dataclass
class IncidentPackage(ReadonlyRecord):
    raw: dict
    project_id: str
    input_id: str
    package_sha256: str
    files: dict = field(default_factory=dict)

    @property
    def package_format(self):
        return self.raw["package_format"]

    def to_dict(self):
        return dump_object(self)


@readonly
def load_incident_package(path_or_prepared, *, project_id, evidence_store, input_id=None, max_bytes=10 * 1024 * 1024):
    prepared = path_or_prepared if hasattr(path_or_prepared, "data") else prepare_selected_input(path_or_prepared, max_bytes=max_bytes)
    if len(prepared.data) > max_bytes:
        raise IncidentInputError("input_too_large", "$")
    # Parsing precedes capture even when this helper is called independently.
    data = parse_incident_input(prepared.data, project_id=project_id)
    identity = input_id or "case-" + hashlib.sha256(prepared.basename.encode()).hexdigest()[:8]
    selected = evidence_store.capture_selected_input(prepared, input_id=identity, kind="event_package", provenance=Provenance(ProvenanceKind.SYNTHETIC, None, None), purpose="selected_input")
    verified = evidence_store.read_raw(selected.evidence_id, max_bytes=max_bytes)
    if hashlib.sha256(verified).hexdigest() != prepared.sha256:
        raise IncidentInputError("embedded_digest_mismatch", "$")
    package = IncidentPackage(data, project_id, identity, prepared.sha256)
    for item in sorted(data.get("files", []) if data["package_format"] == "v1" else [], key=lambda item: item["file_ref"]):
        package.files[item["file_ref"]] = evidence_store.capture_imported_bytes(item["content"].encode(), input_id=identity, logical_name="case/" + item["revision"] + "/" + item["path"], kind="rule_file" if item["kind"] == "rule" else "code" if item["kind"] == "code" else "document", purpose="incident_material")
    return package


@readonly
def analyze_incidents(package, *, evidence_store, rules=(), checks=(), decisions, channel_profile=None, task_scope=()):
    scope = task_scope if isinstance(task_scope, Mapping) or hasattr(task_scope, "include_prefixes") else {"include_prefixes": list(task_scope) or ["."], "exclude_prefixes": [], "harnesses": []}
    return _analyze_incidents(package.raw, evidence_store, decisions, project_id=package.project_id, input_id=package.input_id, package_sha256=package.package_sha256, task_scope=scope, channel_profile=dump_object(channel_profile) if channel_profile else None)


def prepare_incidents(case_path, *, project_id, task_scope, evidence_store, rules=(), checks=(), decisions, channel_profile=None, input_id=None):
    package = load_incident_package(case_path, project_id=project_id, evidence_store=evidence_store, input_id=input_id)
    return analyze_incidents(package, evidence_store=evidence_store, rules=rules, checks=checks, decisions=decisions, channel_profile=channel_profile, task_scope=task_scope)
