"""Assemble exact, line-bound repair intents from confirmed comparisons (F10)."""
from __future__ import annotations

from .inspector import stable_id
from .views import readonly


@readonly
def build_edit_intents(comparison, diagnosis, model_candidates=()):
    intents = []
    candidates = {c["candidate_id"]: c for c in comparison.candidates}
    for validation in diagnosis.validations:
        cid = validation["candidate_id"]
        target = comparison.edit_targets.get(cid)
        if not target or not validation["finding_ids"]:
            continue
        candidate = candidates[cid]
        rule = comparison.policy.get_rule(target["rule_id"])
        decision = comparison.policy.get_decision(candidate["facts"]["decision_id"])
        if not rule or not decision:
            continue
        replacement = None
        span = target.get("clause_span")
        old, new = candidate["facts"].get("old_value"), target["replacement_value"]
        if target["edit_ready"] and span and isinstance(old, str) and isinstance(new, str):
            clause = rule["text"][span["start"]:span["end"]]
            if clause.count(old) == 1:
                replacement = rule["text"][:span["start"]] + clause.replace(old, new, 1) + rule["text"][span["end"]:]
        deleting = candidate["suggested_action"] == "delete"
        intent = {"edit_intent_id": stable_id("intent_", cid), "finding_ids": validation["finding_ids"], "target_path": rule["relative_path"], "operation": "delete_span" if deleting else "replace_span", "line_start": target["line_start"], "line_end": target["line_end"], "base_evidence_id": rule["evidence_id"], "base_sha256": rule["source_ref"]["sha256"], "old_text_sha256": target["old_text_sha256"], "replacement_text": "" if deleting else replacement, "rule_ids": [rule["rule_id"]], "preserved_rule_ids": [], "decision_ids": candidate["facts"]["decision_ids"], "basis_refs": candidate["support_refs"], "confirmation_id": decision["confirmation_ref"], "origin": "registry", "scope_condition": None, "preserved_fragments": [rule["text"][c["span"]["start"]:c["span"]["end"]] for c in rule["constraints"] if c["constraint_id"] in target["preserved_constraint_ids"] and c.get("span")]}
        intents.append(intent)
    for candidate in model_candidates:
        if not candidate.get("edit_intent"):
            continue
        validation = next((v for v in diagnosis.validations if v["candidate_id"] == candidate["candidate_id"]), None)
        if validation and validation["finding_ids"]:
            intents.append(candidate["edit_intent"] | {"edit_intent_id": stable_id("intent_", candidate["candidate_id"]), "finding_ids": validation["finding_ids"], "origin": "model"})
    return intents
