"""Reference validation and deterministic evidence gates for findings (F07)."""
from __future__ import annotations

import json
import hashlib
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from itertools import combinations

from .contracts import ContractError, FieldError, FindingKind, SuggestedAction, dump_object, parse_object
from .decisions import compare_constraint_pair, conditions_intersect, instant, resolve_policy, resolve_constraint
from .inspector import stable_id
from .rules import intersection, covers, scope_contains, span_bytes, interpretation_complete
from .evidence import EvidenceError
from .views import ReadonlyRecord, readonly
from .candidates import non_issue_reason, read_covers


class DiagnosisInputError(ContractError):
    pass


@dataclass
class DiagnosisResult(ReadonlyRecord):
    findings: list = field(default_factory=list)
    validations: list = field(default_factory=list)
    coverage: list = field(default_factory=list)
    stats: dict = field(default_factory=dict)


@readonly
def evaluate_findings(request, static_candidates, model_candidates, registry, incidents, receipts, evidence_store, *, inspection=None, model_reads=()):
    result = DiagnosisResult()
    policy = registry.policy if hasattr(registry, "policy") else registry
    if not hasattr(policy, "get_rule") or instant(policy.as_of) != instant(request.as_of):
        raise DiagnosisInputError("invalid_policy_view", "Diagnosis", [FieldError("registry", "invalid_value", "policy must be evaluated at the requested time")])
    incident = incidents.to_dict() if hasattr(incidents, "to_dict") else incidents or {}
    gate_errors = []
    all_candidates = [("static", dump_object(c)) for c in static_candidates]
    all_candidates += [("model", dump_object(c)) for c in model_candidates]
    seen_candidates = set()
    def add(kind, claim, scope, refs, *, assessment="supported", basis="deterministic", action="ask_user", missing=(), rule_ids=(), event_ids=(), counter=(), candidate_id=None, fact_key=None, claim_type=None, checks=()):
        refs = [dump_object(ref) for ref in refs]
        counter = [dump_object(ref) for ref in counter]
        if any(not evidence_store.validate_ref(ref).ok for ref in refs + counter):
            gate_errors.append("derived_reference_invalid")
            return None
        identity = lambda ref: (evidence_store.get(ref["evidence_id"]).relative_path, ref["sha256"], ref["line_start"], ref["line_end"])
        refs.sort(key=identity)
        counter.sort(key=identity)
        refs = list({identity(ref): ref for ref in refs}.values())
        counter = list({identity(ref): ref for ref in counter}.values())
        if assessment != "supported" and action not in ("keep", "ask_user") and not (kind == "cleanup_candidate" and action == "relocate"):
            action = "ask_user"
        payload = json.dumps([kind, assessment, basis, scope, fact_key if fact_key is not None else [identity(ref) for ref in refs], sorted(rule_ids), sorted(event_ids), action, claim if fact_key is None else None, claim_type], sort_keys=True, ensure_ascii=False)
        fid = stable_id("finding_", payload)
        finding = {"finding_id": fid, "kind": kind, "claim": claim, "scope": scope, "assessment": assessment, "basis": basis, "support_refs": refs, "counter_refs": list(counter), "missing_evidence": list(missing), "suggested_action": action, "checks": [{"check_id": "diagnosis.evidence_gate", "status": "passed", "inputs": [], "reason": None, "artifact_refs": []}, *checks], "related_event_ids": list(event_ids), "related_rule_ids": list(rule_ids), "related_finding_ids": [], "claim_type": claim_type or ("gap" if kind == "insufficient_evidence" else "fact"), "synthetic": bool(incident.get("synthetic"))}
        parse_object("Finding", dump_object(finding))
        merged = any(f["finding_id"] == fid for f in result.findings)
        if not merged:
            result.findings.append(finding)
        else:
            existing = next(f for f in result.findings if f["finding_id"] == fid)
            for key in ("support_refs", "counter_refs"):
                existing[key] = sorted({identity(ref): ref for ref in existing[key] + finding[key]}.values(), key=identity)
            existing["missing_evidence"] = sorted(set(existing["missing_evidence"] + finding["missing_evidence"]))
        if candidate_id:
            result.validations.append({"candidate_id": candidate_id, "origin": "model" if basis == "model_candidate" else "static", "status": "merged" if merged else "accepted" if assessment == "supported" else "downgraded", "reason_code": "evidence_gate", "finding_ids": [fid], "checks": finding["checks"], "model_claimed": None, "notes": []})
        return finding
    for origin, candidate in all_candidates[:1000]:
        cid = candidate.get("candidate_id", "unknown") if isinstance(candidate, dict) else "unknown"
        if not isinstance(cid, str) or not 1 <= len(cid) <= 128 or not cid.isprintable():
            cid = "invalid-candidate"
        if request.semantic_mode == "off" and (origin == "model" or isinstance(candidate, dict) and candidate.get("kind") == "cleanup_candidate"):
            result.validations.append({"candidate_id": cid, "origin": origin, "status": "rejected", "reason_code": "semantic_disabled", "finding_ids": [], "checks": [], "model_claimed": None, "notes": []})
            continue
        if cid in seen_candidates:
            result.validations.append({"candidate_id": cid, "origin": origin, "status": "rejected", "reason_code": "duplicate_candidate_id", "finding_ids": [], "checks": [], "model_claimed": None, "notes": []})
            continue
        seen_candidates.add(cid)
        try:
            if not isinstance(candidate, dict) or candidate.get("kind") not in {k.value for k in FindingKind} or candidate.get("suggested_action", "ask_user") not in {a.value for a in SuggestedAction} or not isinstance(candidate.get("claim"), str) or not candidate["claim"].strip() or len(candidate["claim"]) > 4000:
                raise ValueError("candidate structure")
            refs, counter = candidate.get("support_refs", []), candidate.get("counter_refs", [])
            if not isinstance(refs, list) or not isinstance(counter, list) or len(refs) > 20 or len(counter) > 20 or not isinstance(candidate.get("facts", {}), dict):
                raise ValueError("candidate references")
            normalized_scope = dump_object(parse_object("Scope", candidate.get("scope", dump_object(request.task_scope))))
            scope = intersection(normalized_scope, request.task_scope)
            if scope is None or origin == "model" and not covers(request.task_scope, normalized_scope):
                raise ValueError("candidate scope")
            for ref in refs + counter:
                parse_object("EvidenceRef", ref)
                if not evidence_store.validate_ref(ref, require_covered=origin == "model").ok:
                    raise ValueError("candidate reference")
                if origin == "model" and not read_covers(ref, model_reads):
                    raise ValueError("candidate reference not displayed to analyst")
            missing = candidate.get("missing_evidence", [])
            if not isinstance(missing, list) or any(not isinstance(m, str) or not m for m in missing) or candidate["kind"] == "insufficient_evidence" and not missing or not refs and candidate["kind"] != "insufficient_evidence":
                raise ValueError("candidate gap")
        except (ContractError, ValueError, TypeError):
            result.validations.append({"candidate_id": cid, "origin": origin, "status": "rejected", "reason_code": "invalid_reference_or_scope", "finding_ids": [], "checks": [], "model_claimed": None, "notes": []})
            continue
        kind, facts = candidate["kind"], candidate.get("facts", {})
        if origin == "model" and kind in ("missing_load", "loaded_violation", "recurrence", "stale_code_evidence", "dead_reference"):
            code = {"missing_load": "model_cannot_establish_load", "loaded_violation": "model_cannot_establish_violation", "recurrence": "model_cannot_establish_recurrence", "stale_code_evidence": "model_cannot_establish_version", "dead_reference": "model_cannot_establish_reference"}[kind]
            result.validations.append({"candidate_id": cid, "origin": origin, "status": "rejected", "reason_code": code, "finding_ids": [], "checks": [], "model_claimed": {"kind": kind, "model_confidence": candidate.get("model_confidence")}, "notes": []})
            continue
        basis = "model_candidate" if origin == "model" else "deterministic"
        assessment = "hypothesis" if origin == "model" else "unknown"
        action, rule_ids = candidate.get("suggested_action", "ask_user"), []
        claim_type = candidate.get("claim_type", "fact")
        if claim_type not in ("fact", "causal_explanation", "gap"):
            result.validations.append({"candidate_id": cid, "origin": origin, "status": "rejected", "reason_code": "invalid_claim_type", "finding_ids": [], "checks": [], "model_claimed": None, "notes": []})
            continue
        if origin == "model":
            rule_ids = [r["rule_id"] for r in policy.current_rules if r["source_ref"] in refs]
        claim = candidate["claim"]
        if kind == "cleanup_candidate" and (reason := non_issue_reason(candidate, evidence_store)):
            result.validations.append({"candidate_id": cid, "origin": origin, "status": "accepted", "reason_code": reason, "finding_ids": [], "checks": [], "model_claimed": {"kind": kind}, "notes": ["informational_review_note"]})
            continue
        if origin == "static" and kind == "stale_rule":
            rule = policy.get_rule(facts.get("rule_id"))
            decision = policy.get_decision(facts.get("decision_id"))
            if not decision:
                result.validations.append({"candidate_id": cid, "origin": origin, "status": "rejected", "reason_code": "decision_not_effective", "finding_ids": [], "checks": [], "model_claimed": None, "notes": []})
                continue
            rule_ids = [rule["rule_id"]] if rule else []
            verified_scope = intersection(rule["scope"], decision["scope"]) if rule and decision else None
            verified_scope = intersection(verified_scope, scope) if verified_scope else None
            valid = rule and decision and verified_scope and any(e["decision_id"] == decision["decision_id"] and e["target_id"] == rule["rule_id"] for e in policy.supersessions) and rule["source_ref"] in refs and decision["source_ref"] in refs
            old = next((c for c in (rule or {}).get("constraints", []) if c["constraint_id"] == facts.get("clause_id")), None)
            matches = [c for c in (decision or {}).get("constraints", []) if old and old["subject"] == facts.get("subject") and compare_constraint_pair(old, c)["status"] == "incompatible"]
            others = [d for d in policy.effective_decisions if d != decision and rule and rule["rule_id"] in d["supersedes"] and intersection(d["scope"], scope)]
            authority_conflict = any(compare_constraint_pair(a, b)["status"] == "incompatible" for d in others for a in d["constraints"] for b in (decision or {}).get("constraints", []))
            authority_conflict = authority_conflict or any(e["target_id"] == facts.get("decision_id") and intersection(e["scope"], scope) for e in policy.supersessions)
            if valid and not authority_conflict and (matches or not decision["constraints"]):
                scope = verified_scope
                assessment, basis = "supported", "confirmed_interpretation"
                claim = f"Confirmed decision {decision['decision_id']} directly supersedes rule {rule['rule_id']} for {facts.get('subject', 'the retired obligations')}."
                if matches and (conditions := old.get("conditions", {}) | matches[0].get("conditions", {})):
                    claim += " Conditions: " + json.dumps(dump_object(conditions), sort_keys=True, ensure_ascii=False)
                covered_ids = set(facts.get("covered_clause_ids", []))
                expected_ids = {c["constraint_id"] for c in rule["constraints"]}
                if action == "delete" and (decision["constraints"] or covered_ids != expected_ids or facts.get("remaining_obligations") or not interpretation_complete(rule) or not covers(decision["scope"], rule["scope"])):
                    action = "ask_user"
            else:
                action = "ask_user"
        elif origin == "static" and kind in ("rule_conflict", "policy_mismatch"):
            left = policy.get_rule(facts.get("left_id")) or policy.get_decision(facts.get("left_id"))
            right = policy.get_rule(facts.get("right_id")) or policy.get_decision(facts.get("right_id"))
            rule_ids = [item["rule_id"] for item in (left, right) if item and "rule_id" in item]
            overlap = intersection(left["scope"], right["scope"]) if left and right else None
            overlap = intersection(overlap, scope) if overlap else None
            pairs = [(a, b) for a in (left or {}).get("constraints", []) for b in (right or {}).get("constraints", []) if a["subject"] == b["subject"] == facts.get("subject") and compare_constraint_pair(a, b)["status"] == "incompatible"]
            replaced = any({e["decision_id"], e["target_id"]} == {facts.get("left_id"), facts.get("right_id")} and overlap and covers(e["scope"], overlap) for e in policy.supersessions)
            if left and right and left["source_ref"] in refs and right["source_ref"] in refs and overlap and pairs and not replaced:
                scope = overlap
                assessment, basis = "supported", "confirmed_interpretation"
                kind = "policy_mismatch" if left.get("harness", "shared") != right.get("harness", "shared") else "rule_conflict"
                claim = f"Confirmed requirements {facts['left_id']} and {facts['right_id']} for {facts['subject']} cannot both be satisfied under the overlapping conditions."
            action = "ask_user"
        elif origin == "static" and kind == "dead_reference":
            observation = next((item for item in [*inspection.references, *inspection.commands] if item.get("ref_id", item.get("check_id")) == facts.get("reference_id")), None) if inspection is not None and inspection.run_id == evidence_store.run_id else None
            if observation and inspection.status == "complete" and observation["status"] == "missing" and any(ref["line_start"] <= observation["line"] <= ref["line_end"] and evidence_store.get(ref["evidence_id"]).relative_path == observation["relative_path"] for ref in refs) and (not observation.get("target_path") or evidence_store.probe_path(observation["target_path"]).status == "missing"):
                assessment = "supported"
                claim = "Missing local reference or command target: " + str(observation.get("target_path") or observation.get("subject")) + "."
            action = "ask_user"
        elif kind == "insufficient_evidence":
            assessment, action = "unknown", "ask_user"
            rule_ids = list(facts.get("related_rule_ids", []))
            if origin == "model":
                claim = "Additional evidence is needed to reach a conclusion."
                claim_type = "gap"
        elif kind == "cleanup_candidate":
            assessment, basis = "hypothesis", "model_candidate"
            action = action if action in ("keep", "relocate", "ask_user") else "ask_user"
            rationale = candidate.get("rationale")
            if action == "relocate" and isinstance(rationale, str) and len(rationale) <= 2000:
                explanation = " ".join(rationale.split())
                if explanation and explanation.isprintable():
                    claim = claim.rstrip() + "\n" + explanation
        elif kind == "stale_code_evidence":
            assessment, action = "unknown", "ask_user"
        if origin == "model" and action in ("delete", "update", "narrow_scope"):
            action = "ask_user"
        if claim_type == "causal_explanation":
            if origin != "model":
                result.validations.append({"candidate_id": cid, "origin": origin, "status": "rejected", "reason_code": "causal_explanation_requires_model", "finding_ids": [], "checks": [], "model_claimed": None, "notes": []})
                continue
            assessment, basis, action = "hypothesis", "model_candidate", "ask_user"
        add(kind, claim, scope, refs, assessment=assessment, basis=basis, action=action, missing=candidate.get("missing_evidence", []), rule_ids=rule_ids, counter=counter, candidate_id=cid, fact_key=facts if origin == "static" and facts else None, claim_type=claim_type)
        if result.validations and result.validations[-1]["candidate_id"] == cid and origin == "model":
            result.validations[-1]["model_claimed"] = {"kind": kind, "suggested_action": candidate.get("suggested_action"), "model_confidence": candidate.get("model_confidence")}
    # Cross-harness differences are facts about separate policies, not joint loading.
    for left, right in combinations(policy.current_rules, 2):
        if left["harness"] == right["harness"] or "shared" in (left["harness"], right["harness"]):
            continue
        scope = intersection(intersection(left["scope"], right["scope"]) or {"include_prefixes": []}, request.task_scope)
        if scope and any(compare_constraint_pair(a, b)["status"] == "incompatible" for a in left["constraints"] for b in right["constraints"]):
            add("policy_mismatch", f"Confirmed requirements differ between {left['harness']} and {right['harness']}; joint loading is unknown.", scope, [left["source_ref"], right["source_ref"]], basis="confirmed_interpretation", rule_ids=[left["rule_id"], right["rule_id"]])
    receipts_by_id = {r["receipt_id"]: r for r in incident.get("receipts", [])}
    events = {e["event_id"]: e for e in incident.get("events", [])}
    if receipts and dump_object(receipts) != incident.get("receipts", []):
        gate_errors.append("receipt_view_mismatch")
        receipts_by_id = {}
    package = next((e for e in evidence_store.list_evidence(origin="selected_input", kind="event_package") if e.input_id == incident.get("input_id") and e.sha256 == incident.get("package_sha256")), None)
    package_ref = dump_object(evidence_store.make_ref(package, 1, package.line_count)) if package and package.line_count else None
    history = {}
    def policy_at(event):
        at = event.get("occurred_at")
        if not at:
            return None
        try:
            key = instant(at).isoformat()
        except (TypeError, ValueError, AttributeError):
            return None
        if key not in history:
            spans = [dump_object(r) | {"context": "active"} for r in policy.current_rules]
            history[key] = resolve_policy(spans, policy.registry, evidence_store, as_of=at)
        return history[key]
    def event_applicable(event):
        return event.get("status") == "normalized" and bool(event.get("scope")) and scope_contains(event["scope"], event["action"]["target_path"], event["harness"]) and scope_contains(request.task_scope, event["action"]["target_path"], event["harness"])
    def valid_code_snapshot(ref):
        if not ref or not evidence_store.validate_ref(ref).ok:
            return False
        item = evidence_store.get(ref["evidence_id"])
        return (item.origin == "imported" and item.input_id == incident.get("input_id") and item.kind == "code"
                and ref["line_start"] == 1 and ref["line_end"] == item.line_count)

    def check_code_version(event):
        version = event.get("code_version")
        if not version or not package_ref or not event_applicable(event) or event["action"]["kind"] == "mention":
            return
        path, harness = event["action"]["target_path"], event["harness"]
        historical = policy_at(event)
        observed, required = version.get("observed_ref"), version.get("required_ref")
        action_ref = event["action"].get("before_ref" if event["action"]["kind"] == "edit" else "result_ref")
        observed_ok = observed == action_ref and valid_code_snapshot(observed)
        required_ok = valid_code_snapshot(required)
        authority = {}
        if historical:
            replaced = {edge["target_id"] for edge in historical.supersessions if scope_contains(edge["scope"], path, harness)}
            for decision in historical.effective_decisions:
                if decision["decision_id"] in replaced or not scope_contains(decision["scope"], path, harness):
                    continue
                values = {c["value"] for c in decision["constraints"] if c["subject"] == "code_snapshot_sha256" and c["operator"] == "equals" and c.get("conditions") == {"relative_path": path}}
                if values:
                    authority[decision["decision_id"]] = (decision, values)
        selected = authority.get(version.get("decision_id"))
        required_values = set().union(*(item[1] for item in authority.values()))
        confirmed = bool(selected and required_ok and selected[1] == required_values == {required["sha256"]})
        refs = [package_ref, *([observed] if observed_ok else []), *([required] if required_ok else [])]
        if observed_ok and confirmed:
            if observed["sha256"] != required["sha256"]:
                refs.append(selected[0]["source_ref"])
                check = {"check_id": "version.compare", "status": "passed", "inputs": [event["event_id"]], "reason": None, "artifact_refs": []}
                add("stale_code_evidence", f"The code snapshot used for {path} differs from the version required by the confirmed decision.", event["scope"], refs, action="fix_retrieval", event_ids=[event["event_id"]], checks=[check])
            return
        missing = [name for ok, name in ((observed_ok, "observed_code_snapshot"), (required_ok, "required_code_snapshot"), (confirmed, "confirmed_code_version_requirement")) if not ok]
        add("stale_code_evidence", "The observed code cannot be compared with a confirmed required version.", event["scope"], refs, assessment="unknown", missing=missing, event_ids=[event["event_id"]])

    for event in events.values():
        check_code_version(event)
    def valid_entry(entry, receipt, event):
        reference = entry.get("evidence_ref")
        if entry.get("status") != "valid" or not reference or not evidence_store.validate_ref(reference).ok or entry.get("channel") not in (receipt.get("required_channels") or ("project_instructions", "imported_instructions", "rule_directories", "session_injection")):
            return False
        if entry.get("scope") and not scope_contains({"include_prefixes": entry["scope"]}, event["action"]["target_path"]):
            return False
        return hashlib.sha256(span_bytes(evidence_store, reference)).hexdigest() == entry.get("span_sha256")
    def verified_violation(behavior, event, historical):
        if not historical or not event_applicable(event) or behavior.get("status") != "violation" or not behavior.get("counts_toward_recurrence"):
            return False
        check = next((c for c in historical.effective_checks if c["check_id"] == behavior.get("check_id")), None)
        if not check or behavior.get("rule_id") not in check["rule_ids"] or check["constraint"]["constraint_id"] != behavior.get("constraint_id") or not scope_contains(check["scope"], event["action"]["target_path"], event["harness"]):
            return False
        if check["parameters"].get("path") and check["parameters"]["path"] != event["action"]["target_path"]:
            return False
        resolution = resolve_constraint(historical, check["subject"], event["action"]["target_path"], harness=event["harness"], exception_rule_ids=event.get("exception_rule_ids", []))
        if resolution["status"] != "resolved" or resolution["exception_applied"] or resolution["operator"] != behavior.get("operator") or type(resolution["value"]) is not type(behavior.get("expected_value")) or resolution["value"] != behavior.get("expected_value"):
            return False
        try:
            source = evidence_store.get(behavior.get("input_evidence_id"))
        except EvidenceError:
            return False
        if source.sha256 != behavior.get("input_sha256") or not behavior.get("evidence_refs") or any(not evidence_store.validate_ref(r).ok for r in behavior["evidence_refs"]):
            return False
        if event["action"]["kind"] == "edit" and source.evidence_id != (event["action"].get("after_ref") or {}).get("evidence_id"):
            return False
        observed = behavior.get("observed_value")
        return behavior.get("violation_kind") == "check_failed" if check["detector"] == "structured_check_result" else isinstance(observed, str) and ((type(observed) is not type(resolution["value"]) or observed != resolution["value"]) if resolution["operator"] == "equals" else (type(observed) is type(resolution["value"]) and observed == resolution["value"]))
    for load in incident.get("loads", []):
        event = events.get(load["event_id"])
        historical = policy_at(event) if event else None
        rule = historical.get_rule(load["rule_id"]) if historical else None
        receipt = receipts_by_id.get(load.get("proof_receipt_id"))
        proof = load.get("proof_checks", {})
        if load.get("state") == "unknown" and package_ref and rule and event and event_applicable(event):
            scope = intersection(rule["scope"], event["scope"])
            if scope:
                add("missing_load", f"Loading of rule {rule['rule_id']} before {event['event_id']} cannot be determined.", scope, [rule["source_ref"], package_ref], assessment="unknown", action="ask_user", missing=load.get("reasons") or ["pre_action_receipt_missing"], rule_ids=[rule["rule_id"]], event_ids=[event["event_id"]])
        if not package_ref or not rule or not event or not event_applicable(event) or not receipt or receipt.get("status") != "verified" or not receipt.get("usable") or receipt.get("provenance", {}).get("kind") != "synthetic" or not receipt.get("provenance", {}).get("adapter") or incident.get("synthetic") is not True or incident.get("proof_scope") != "synthetic_case" or not all(proof.get(k) is True for k in ("same_session", "same_harness", "same_epoch", "before_action", "order_verified", "text_sha_matches")):
            continue
        anchor = events.get(receipt.get("before_event_id"))
        if not anchor or not anchor.get("occurred_at") or anchor.get("status") != "normalized" or receipt.get("session_id") != event["session_id"] or anchor["session_id"] != event["session_id"] or receipt.get("harness") != event["harness"] or anchor["harness"] != event["harness"] or not event.get("context_epoch") or receipt.get("context_epoch") != event["context_epoch"] or anchor.get("context_epoch") != event["context_epoch"] or instant(anchor["occurred_at"]) > instant(event["occurred_at"]) or receipt.get("captured_at") and instant(receipt["captured_at"]) > instant(event["occurred_at"]):
            continue
        if not evidence_store.validate_ref(rule["source_ref"]).ok or hashlib.sha256(span_bytes(evidence_store, rule["source_ref"])).hexdigest() != rule["text_sha256"]:
            continue
        refs = [rule["source_ref"], package_ref]
        if event.get("action", {}).get("evidence_ref"):
            refs.append(event["action"]["evidence_ref"])
        scope = intersection(intersection(rule["scope"], request.task_scope) or {"include_prefixes": []}, event["scope"])
        if not scope:
            continue
        entries = receipt.get("entries", [])
        matching = [entry for entry in entries if valid_entry(entry, receipt, event) and entry["span_sha256"] == rule["text_sha256"]]
        complete = receipt.get("coverage_effective") == "complete" and bool(receipt.get("required_channels")) and set(receipt["required_channels"]) <= set(receipt.get("channels_covered", [])) and receipt.get("provenance", {}).get("kind") == "synthetic" and bool(receipt.get("provenance", {}).get("adapter")) and all(valid_entry(entry, receipt, event) for entry in entries)
        if load["state"] == "observed_not_loaded" and load["load_proof"] == "complete_exclusion" and proof.get("channels_complete") is True and complete and not matching:
            # An obsolete rule needs an instruction update; absence of its old
            # text must never recommend loading it into a new task.
            if not any(edge["target_id"] == rule["rule_id"] and scope_contains(edge["scope"], event["action"]["target_path"], event["harness"]) and (not historical.get_decision(edge["decision_id"])["constraints"] or any(compare_constraint_pair(old, new)["status"] == "incompatible" for old in rule["constraints"] for new in historical.get_decision(edge["decision_id"])["constraints"])) for edge in historical.supersessions):
                add("missing_load", f"Rule {rule['rule_id']} is absent from the complete pre-action context for {event['event_id']}.", scope, refs, action="fix_loading", rule_ids=[rule["rule_id"]], event_ids=[event["event_id"]])
        elif load["state"] == "observed_loaded" and load["load_proof"] == "matched_entry" and matching and load.get("matched_receipt_id") == receipt["receipt_id"]:
            index = load.get("matched_entry_index")
            if type(index) is not int or not 0 <= index < len(entries) or entries[index] not in matching:
                continue
            refs.append(entries[index]["evidence_ref"])
            behaviors = [b for b in incident.get("behaviors", []) if b["event_id"] == event["event_id"] and b["rule_id"] == rule["rule_id"] and verified_violation(b, event, historical)]
            correct_rule = any(c["subject"] == next(check["subject"] for check in historical.effective_checks if check["check_id"] == b["check_id"]) and c["value"] == b["expected_value"] and type(c["value"]) is type(b["expected_value"]) and c["operator"] == b["operator"] for c in rule["constraints"] for b in behaviors)
            if behaviors and correct_rule:
                refs.extend(ref for behavior in behaviors for ref in behavior["evidence_refs"])
                add("loaded_violation", f"The confirmed behavior check failed after rule {rule['rule_id']} was loaded.", scope, refs, action="add_check", rule_ids=[rule["rule_id"]], event_ids=[event["event_id"]])
    for group in incident.get("recurrence", []):
        event_ids = group["confirmed_violation_event_ids"]
        actual = sorted({e for e in event_ids if e in events and events[e].get("scope", {}).get("include_prefixes") == group["task_scope"] and any(b["event_id"] == e and b["rule_id"] == group["rule_id"] and b.get("constraint_id") == group.get("constraint_id") and b.get("violation_kind") == group.get("violation_kind") and verified_violation(b, events[e], policy_at(events[e])) for b in incident.get("behaviors", []))})
        if actual and any(events[e]["scope"] != events[actual[0]]["scope"] for e in actual):
            continue
        tasks = {events[e].get("task_id") for e in actual if e in events and events[e].get("task_id")}
        scope = intersection(events[actual[0]]["scope"], request.task_scope) if actual else None
        if package_ref and len(tasks) >= 2 and group["meets_recurrence_threshold"] and scope:
            evidence = [ref for b in incident.get("behaviors", []) if b["event_id"] in actual and b["rule_id"] == group["rule_id"] for ref in b["evidence_refs"]]
            add("recurrence", f"Confirmed violations recur across {len(tasks)} independent tasks.", scope, [package_ref, *evidence], action="add_check", rule_ids=[group["rule_id"]], event_ids=actual)
    for left, right in combinations(result.findings, 2):
        if set(left["related_rule_ids"]) & set(right["related_rule_ids"]) or set(left["related_event_ids"]) & set(right["related_event_ids"]):
            left["related_finding_ids"].append(right["finding_id"])
            right["related_finding_ids"].append(left["finding_id"])
    for finding in result.findings:
        finding["related_finding_ids"].sort()
    kinds = {kind.value: index for index, kind in enumerate(FindingKind)}
    result.findings.sort(key=lambda f: ({"supported": 0, "hypothesis": 1, "unknown": 2}[f["assessment"]], kinds[f["kind"]], f["finding_id"]))
    for check in ("candidate_validation", "reference_validation", "gate_evaluation"):
        result.coverage.append({"check_id": "diagnosis." + check, "status": "failed" if len(all_candidates) > 1000 or gate_errors else "passed", "inputs": [], "reason": "candidate_limit" if len(all_candidates) > 1000 else ";".join(sorted(set(gate_errors))) if gate_errors else "no_candidates" if not all_candidates else None, "artifact_refs": []})
    result.stats = {"total": len(result.findings), **{state: sum(f["assessment"] == state for f in result.findings) for state in ("supported", "hypothesis", "unknown")}, "rejected": sum(v["status"] == "rejected" for v in result.validations)}
    return result
