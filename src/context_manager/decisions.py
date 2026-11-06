"""Explicit registry binding, confirmations and time-scoped policy (F04)."""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from itertools import combinations
from typing import Any, Sequence

from .contracts import ContractError, FieldError, dump_object, parse_object
from .evidence import EvidenceError, EvidenceStore
from .inspector import stable_id
from .rules import covers, intersection, scope_contains, span_bytes, span_text, interpretation_complete
from .views import ReadonlyRecord, readonly


class RegistryFormatError(ContractError):
    pass


def invalid(path: str, code: str = "invalid_field") -> RegistryFormatError:
    return RegistryFormatError(code, "Registry", [FieldError(path, code, "invalid registry field")])


def decode_json(raw: bytes) -> dict:
    def pairs(items):
        data = {}
        for key, value in items:
            if key in data:
                raise invalid("$", "duplicate_json_key")
            data[key] = value
        return data
    def constant(value):
        raise invalid("$", "invalid_json")
    def finite_float(value):
        number = float(value)
        if not math.isfinite(number):
            raise invalid("$", "invalid_json")
        return number
    try:
        data = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=pairs, parse_constant=constant, parse_float=finite_float)
    except (UnicodeError, ValueError, RecursionError):
        raise invalid("$", "invalid_json") from None
    if not isinstance(data, dict):
        raise invalid("$", "invalid_json")
    return data


def instant(value: str | datetime) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    if result.tzinfo is None:
        raise ValueError("timezone required")
    return result.astimezone(timezone.utc)


def approval_key(approval: dict) -> str:
    fields = ("decision_id", "target_path", "operation", "line_start", "line_end", "old_text_sha256", "replacement_sha256", "rule_ids")
    payload = {key: approval[key] for key in fields}
    payload["rule_ids"] = sorted(set(payload["rule_ids"]), key=lambda s: s.encode("utf-8"))
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass
class RegistryInput(ReadonlyRecord):
    project_id: str
    rules: list[dict] = field(default_factory=list)
    decisions: list[dict] = field(default_factory=list)
    confirmations: list[dict] = field(default_factory=list)
    behavior_checks: list[dict] = field(default_factory=list)
    sha256: str | None = None
    synthetic: bool = False
    schema_version: int = 1
    provided: bool = True


@dataclass
class Registry(RegistryInput):
    issues: list[dict] = field(default_factory=list)


@readonly
def parse_registry(raw_bytes: bytes, *, project_id: str) -> RegistryInput:
    if len(raw_bytes) > 1024 * 1024:
        raise invalid("$", "limit_exceeded")
    data = decode_json(raw_bytes)
    allowed = {"schema_version", "project_id", "synthetic", "rules", "decisions", "confirmations", "behavior_checks"}
    if set(data) - allowed:
        raise invalid(sorted(set(data) - allowed)[0], "unknown_field")
    if type(data.get("schema_version")) is not int or data["schema_version"] != 1:
        raise invalid("schema_version", "unsupported_schema_version")
    if data.get("project_id", project_id) != project_id:
        raise invalid("project_id", "project_id_mismatch")
    if type(data.get("synthetic", False)) is not bool:
        raise invalid("synthetic")
    seen, constraint_count = set(), 0
    limits = {"rules": 1000, "decisions": 1000, "confirmations": 2000, "behavior_checks": 500}
    def checked(name, value, path):
        try:
            return dump_object(parse_object(name, value))
        except ContractError as error:
            fields = [FieldError(path + ("." + e.path if e.path != "$" else ""), e.code, e.message, e.expected, e.actual_type) for e in error.errors]
            raise RegistryFormatError(error.code, "Registry", fields, error.message) from None
    for group, object_name, id_key in (("rules", "RuleSpan", "rule_id"), ("decisions", "Decision", "decision_id"), ("confirmations", "Confirmation", "confirmation_id"), ("behavior_checks", "BehaviorCheck", "check_id")):
        items = data.setdefault(group, [])
        if not isinstance(items, list):
            raise invalid(group, "wrong_type")
        if len(items) > limits[group]:
            raise invalid(group, "limit_exceeded")
        for index, item in enumerate(items):
            path = f"{group}[{index}]"
            if not isinstance(item, dict):
                raise invalid(path)
            if "scope" in item:
                item["scope"] = checked("Scope", item["scope"], path + ".scope")
                if any(len(item["scope"][key]) > 100 for key in ("include_prefixes", "exclude_prefixes")):
                    raise invalid(path + ".scope", "limit_exceeded")
            if "source_ref" in item:
                item["source_ref"] = checked("SourceLocator", item["source_ref"], path + ".source_ref")
            if group == "confirmations" and isinstance(item.get("approved_items"), list):
                for number, approved in enumerate(item["approved_items"]):
                    if isinstance(approved, dict) and "source_ref" in approved:
                        approved["source_ref"] = checked("SourceLocator", approved["source_ref"], f"{path}.approved_items[{number}].source_ref")
            if "constraints" in item:
                if not isinstance(item["constraints"], list):
                    raise invalid(path + ".constraints", "wrong_type")
                item["constraints"] = [checked("Constraint", c, f"{path}.constraints[{number}]") for number, c in enumerate(item["constraints"])]
                constraint_count += len(item["constraints"])
                if constraint_count > 10000 or any(len(c["conditions"]) > 20 for c in item["constraints"]):
                    raise invalid(path + ".constraints", "limit_exceeded")
            candidate = copy.deepcopy(item)
            if group == "rules":
                if set(candidate) - {"rule_id", "source_ref", "text", "text_sha256", "scope", "harness", "interpretation_status", "confirmation_ref", "constraints", "relative_path"}:
                    raise invalid(path, "unknown_field")
                if candidate.get("interpretation_status") not in ("structured_confirmed", "unresolved"):
                    raise invalid(path + ".interpretation_status", "unknown_enum")
                constraints = candidate.pop("constraints", [])
                digest = candidate.pop("text_sha256", None)
                candidate.pop("relative_path", None)
                if not isinstance(constraints, list):
                    raise invalid(path + ".constraints")
                if candidate.get("interpretation_status") == "structured_confirmed" and (not constraints or not candidate.get("confirmation_ref") or not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest)):
                    raise invalid(path + ".text_sha256")
                if candidate.get("interpretation_status") == "unresolved" and constraints:
                    raise invalid(path + ".constraints")
                if digest is not None and (not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest)):
                    raise invalid(path + ".text_sha256")
                if "relative_path" in item and item["relative_path"] != item["source_ref"]["relative_path"]:
                    raise invalid(path + ".relative_path")
                item["constraints"] = constraints
            references = [candidate] if "source_ref" in candidate else []
            if group == "confirmations":
                approved_items = candidate.get("approved_items", [])
                if not isinstance(approved_items, list):
                    raise invalid(path + ".approved_items", "wrong_type")
                references.extend(approved_items)
            for holder in references:
                if isinstance(holder, dict) and "source_ref" in holder:
                    loc = holder["source_ref"]
                    holder["source_ref"] = {k: v for k, v in loc.items() if k != "relative_path"} | {"evidence_id": "registry-source"}
            checked(object_name, candidate, path)
            item_id = item[id_key]
            if item_id in seen:
                raise invalid(path + "." + id_key, "duplicate_id")
            seen.add(item_id)
            if group == "behavior_checks":
                parameters = item["parameters"]
                expected_keys = {"decision_id", "symbol"} if item["detector"] == "python_assignment_literal" else {"decision_id", "result_check_id"}
                if not expected_keys <= set(parameters) or set(parameters) - expected_keys - {"path"} or any(not isinstance(v, str) or not v for v in parameters.values()):
                    raise invalid(path + ".parameters")
                if "symbol" in parameters and not parameters["symbol"].isidentifier():
                    raise invalid(path + ".parameters.symbol")
                if "path" in parameters:
                    parameters["path"] = checked("SourceLocator", {"relative_path": parameters["path"], "sha256": "0" * 64, "line_start": 1, "line_end": 1}, path + ".parameters.path")["relative_path"]
            for number, constraint in enumerate(item.get("constraints", [])):
                constraint["constraint_id"] = constraint.get("constraint_id") or f"{item_id}:c{number + 1}"
                span = constraint.get("span")
                if span and "text" in item and span["end"] > len(item["text"]):
                    raise invalid(path + ".constraints.span")
            if len({c["constraint_id"] for c in item.get("constraints", [])}) != len(item.get("constraints", [])):
                raise invalid(path + ".constraints", "duplicate_id")
            if group == "decisions" and len(set(item["supersedes"])) != len(item["supersedes"]):
                raise invalid(path + ".supersedes", "duplicate_id")
            if group == "confirmations":
                approved_ids = [a["item_id"] for a in item["approved_items"]]
                if len(set(approved_ids)) != len(approved_ids):
                    raise invalid(path + ".approved_items", "duplicate_id")
                keys = [approval_key(a) for a in item.get("edit_approvals", [])]
                if len(set(keys)) != len(keys):
                    raise invalid(path + ".edit_approvals", "duplicate_id")
    return RegistryInput(project_id, **{k: data[k] for k in ("rules", "decisions", "confirmations", "behavior_checks")}, sha256=hashlib.sha256(raw_bytes).hexdigest(), synthetic=data.get("synthetic", False))


def issue(code: str, *ids: str, blocking: bool = True) -> dict:
    return {"code": code, "severity": "error" if blocking else "warning", "message": code, "field_path": None, "related_ids": list(ids), "evidence_refs": [], "blocking": blocking}


@readonly
def bind_registry(registry_input: RegistryInput, evidence_store: EvidenceStore) -> Registry:
    result = Registry(**dump_object(registry_input))
    captures = {}
    for group in ("rules", "decisions", "confirmations", "behavior_checks"):
        for index, original in enumerate(getattr(result, group)):
            item = copy.deepcopy(original)
            item_id = item.get("rule_id", item.get("decision_id", item.get("confirmation_id", item.get("check_id"))))
            try:
                holders = [item] if "source_ref" in item else []
                if group == "confirmations":
                    holders.extend(item["approved_items"])
                for holder in holders:
                    child = holder["source_ref"]
                    path = child["relative_path"]
                    if path not in captures:
                        captures[path] = evidence_store.capture_file(path, kind="document", purpose="registry_source")
                    ev = captures[path]
                    if child["sha256"] != ev.sha256:
                        raise EvidenceError("evidence_digest_mismatch", "registry source changed")
                    reference = dump_object(evidence_store.make_ref(ev, child["line_start"], child["line_end"]))
                    check = evidence_store.validate_ref(reference)
                    if not check.ok:
                        raise EvidenceError(check.code or "line_out_of_range", "invalid registry source range")
                    holder["source_ref"] = reference
                    if holder is item:
                        item["relative_path"] = path
                getattr(result, group)[index] = item
            except EvidenceError:
                getattr(result, group)[index] = original | {"binding_failed": True}
                result.issues.append(issue("registry_binding_failed", item_id) | {"field_path": f"{group}[{index}].source_ref"})
    return result


def _equal(a, b) -> bool:
    return type(a) is type(b) and a == b


def conditions_intersect(left: dict, right: dict) -> bool:
    return all(_equal(left[k], right[k]) for k in left.keys() & right.keys())


@readonly
def compare_constraint_pair(left: dict, right: dict) -> dict:
    same = _equal(left["value"], right["value"])
    incompatible = (left["operator"] == right["operator"] == "equals" and not same) or (left["operator"] != right["operator"] and same)
    subject_matches = left["subject"] == right["subject"]
    conditions_match = conditions_intersect(left.get("conditions", {}), right.get("conditions", {}))
    if not subject_matches or not conditions_match:
        incompatible = False
    return {"left_id": left.get("constraint_id"), "right_id": right.get("constraint_id"), "subject": left["subject"], "status": "incompatible" if incompatible else "compatible", "reason": "subject_mismatch" if not subject_matches else "conditions_disjoint" if not conditions_match else "operator_value_comparison", "conditions": left.get("conditions", {}) | right.get("conditions", {}) if conditions_match else {}, "left_value": left["value"], "right_value": right["value"]}


@dataclass
class PolicyState(ReadonlyRecord):
    as_of: str
    registry: Registry
    current_rules: list[dict] = field(default_factory=list)
    effective_decisions: list[dict] = field(default_factory=list)
    inactive_decisions: list[dict] = field(default_factory=list)
    effective_checks: list[dict] = field(default_factory=list)
    suggested_checks: list[dict] = field(default_factory=list)
    supersessions: list[dict] = field(default_factory=list)
    editable_files: list[str] = field(default_factory=list)
    issues: list[dict] = field(default_factory=list)
    valid_confirmations: dict[str, set[str]] = field(default_factory=dict)

    @property
    def rule_bindings(self):
        return self.current_rules

    def get_rule(self, rule_id):
        return next((r for r in self.current_rules if r["rule_id"] == rule_id), None)

    def get_decision(self, decision_id):
        return next((d for d in self.effective_decisions if d["decision_id"] == decision_id), None)

    def get_confirmation(self, confirmation_id):
        return next((c for c in self.registry.confirmations if c["confirmation_id"] == confirmation_id), None)

    @readonly
    def confirmation_approves(self, confirmation_id: str, key: str, *, as_of) -> dict:
        if instant(as_of) != instant(self.as_of):
            return {"status": "stale"}
        confirmation = self.get_confirmation(confirmation_id)
        if not confirmation or confirmation.get("binding_failed"):
            return {"status": "missing"}
        approvals = confirmation.get("edit_approvals", [])
        for approval in approvals:
            if approval_key(approval) == key and approval["decision_id"] in self.valid_confirmations.get(confirmation_id, set()) and self.get_decision(approval["decision_id"]):
                return {"status": "expired" if approval.get("valid_until") and instant(as_of) >= instant(approval["valid_until"]) else "valid"}
        return {"status": "mismatch" if approvals else "missing"}

    @readonly
    def clauses(self, rule_id: str) -> list[dict]:
        rule = self.get_rule(rule_id)
        return [] if not rule else [{"clause_id": c["constraint_id"], "span": c.get("span"), "text": rule["text"][c["span"]["start"]:c["span"]["end"]] if c.get("span") else rule["text"], "text_sha256": hashlib.sha256((rule["text"][c["span"]["start"]:c["span"]["end"]] if c.get("span") else rule["text"]).encode()).hexdigest(), "constraints": [c]} for c in rule["constraints"]]

    @readonly
    def decision_applies(self, decision_id, rule_id_or_record, task_scope, *, as_of=None):
        rule = self.get_rule(rule_id_or_record) if isinstance(rule_id_or_record, str) else rule_id_or_record
        decision = self.get_decision(decision_id)
        if not decision or not rule:
            return {"status": "missing"}
        if as_of is not None and instant(as_of) != instant(self.as_of):
            return {"status": "inactive"}
        if not intersection(decision["scope"], task_scope) or not intersection(decision["scope"], rule["scope"]):
            return {"status": "out_of_scope"}
        if any(edge["target_id"] == decision_id and intersection(edge["scope"], task_scope) for edge in self.supersessions):
            return {"status": "conflict"}
        return {"status": "accepted" if rule["rule_id"] in decision["supersedes"] else "not_superseding"}


@readonly
def resolve_policy(rule_spans: Sequence[dict], registry: Registry, evidence_store: EvidenceStore, *, as_of) -> PolicyState:
    policy = PolicyState(instant(as_of).isoformat(), registry, issues=list(registry.issues))
    all_items = {r["rule_id"]: r for r in registry.rules} | {d["decision_id"]: d for d in registry.decisions} | {c["check_id"]: c for c in registry.behavior_checks}
    for confirmation in registry.confirmations:
        cid = confirmation["confirmation_id"]
        if confirmation.get("binding_failed"):
            continue
        valid = set()
        for approved in confirmation["approved_items"]:
            item_id = approved["item_id"]
            item = all_items.get(item_id)
            if not item or item.get("binding_failed"):
                if not item:
                    policy.issues.append(issue("unknown_approved_item", cid, item_id, blocking=False))
                continue
            kind = "rule_interpretation" if "rule_id" in item else "decision_approval" if "decision_id" in item else "check_configuration"
            origin = item.get("source_ref")
            if kind == "check_configuration":
                decision = all_items.get(item["parameters"]["decision_id"])
                origin = decision.get("source_ref") if decision else None
            outer, inner = confirmation["source_ref"], approved["source_ref"]
            reason = "confirmation_kind_mismatch" if confirmation["kind"] != kind else "confirmation_backlink_missing" if item.get("confirmation_ref") != cid else "confirmation_source_not_covered" if origin != inner or outer["evidence_id"] != inner["evidence_id"] or outer["sha256"] != inner["sha256"] or not outer["line_start"] <= inner["line_start"] <= inner["line_end"] <= outer["line_end"] else None
            if reason is None:
                valid.add(item_id)
            else:
                policy.issues.append(issue(reason, cid, item_id))
        policy.valid_confirmations[cid] = valid
    bound = set()
    for rule in registry.rules:
        rid = rule["rule_id"]
        if rule.get("binding_failed"):
            continue
        matching = next((s for s in rule_spans if s["source_ref"] == rule["source_ref"] and s["harness"] in ("shared", rule["harness"])), None)
        reason = None
        if not matching:
            reason = "rule_span_not_found"
        elif matching["context"] != "active":
            reason = "rule_not_current"
        elif not covers(matching["scope"], rule["scope"]):
            reason = "source_scope_mismatch"
        elif rule["interpretation_status"] != "structured_confirmed":
            reason = "unresolved_interpretation"
        elif rule.get("text_sha256") != matching["text_sha256"] or rule["text"] != matching["text"]:
            reason = "rule_text_mismatch"
        elif rule["source_ref"]["evidence_id"] + str(rule["source_ref"]["line_start"]) + rule["harness"] in bound:
            reason = "duplicate_rule_binding"
        elif rid not in policy.valid_confirmations.get(rule.get("confirmation_ref"), set()):
            reason = "unresolved_interpretation"
        if reason:
            policy.issues.append(issue(reason, rid, blocking=reason != "unresolved_interpretation"))
            continue
        bound.add(rule["source_ref"]["evidence_id"] + str(rule["source_ref"]["line_start"]) + rule["harness"])
        policy.current_rules.append(rule | {"line_start": rule["source_ref"]["line_start"], "line_end": rule["source_ref"]["line_end"], "evidence_id": rule["source_ref"]["evidence_id"], "constraint_ids": [c["constraint_id"] for c in rule["constraints"]]})
    for decision in registry.decisions:
        did, reason = decision["decision_id"], None
        if decision["status"] != "accepted":
            reason = "status_" + decision["status"]
        elif decision.get("binding_failed") or did not in policy.valid_confirmations.get(decision.get("confirmation_ref"), set()):
            reason = "confirmation_invalid"
            policy.issues.append(issue("confirmation_missing" if not decision.get("confirmation_ref") else "confirmation_stale", did))
        elif instant(decision["effective_from"]) > instant(as_of):
            reason = "not_yet_effective"
        elif decision.get("effective_until") and instant(as_of) >= instant(decision["effective_until"]):
            reason = "expired"
        if reason:
            policy.inactive_decisions.append({"decision_id": did, "reason": reason})
        else:
            policy.effective_decisions.append(decision)
    effective = {d["decision_id"]: d for d in policy.effective_decisions}
    def cyclic(start, target):
        pending, seen = [target], set()
        while pending:
            node = pending.pop()
            if node == start:
                return True
            if node in seen or node not in effective:
                continue
            seen.add(node)
            pending.extend(effective[node]["supersedes"])
        return False
    for decision in policy.effective_decisions:
        for target in decision["supersedes"]:
            if target not in all_items:
                policy.issues.append(issue("unknown_supersedes", decision["decision_id"], target))
            elif cyclic(decision["decision_id"], target):
                policy.issues.append(issue("supersession_cycle", decision["decision_id"], target))
            elif policy.get_rule(target) or target in effective:
                policy.supersessions.append({"decision_id": decision["decision_id"], "target_id": target, "scope": decision["scope"]})
            elif "rule_id" in all_items[target]:
                policy.issues.append(issue("supersedes_non_current_rule", decision["decision_id"], target))
            elif "decision_id" in all_items[target]:
                policy.issues.append(issue("supersedes_inactive_decision", decision["decision_id"], target, blocking=False))
            else:
                policy.issues.append(issue("unknown_supersedes", decision["decision_id"], target))
    # An incomplete authority chain cannot authorize even its otherwise valid
    # edges. Keep its diagnostics, while allowing unrelated decisions to work.
    invalid_authority = {identifier for problem in policy.issues if problem["blocking"] and problem["code"] in ("unknown_supersedes", "supersession_cycle", "supersedes_non_current_rule") for identifier in problem["related_ids"] if identifier in effective}
    policy.supersessions = [edge for edge in policy.supersessions if edge["decision_id"] not in invalid_authority and edge["target_id"] not in invalid_authority]
    if invalid_authority:
        policy.effective_decisions = [decision for decision in policy.effective_decisions if decision["decision_id"] not in invalid_authority]
        policy.inactive_decisions.extend({"decision_id": did, "reason": "authority_chain_invalid"} for did in sorted(invalid_authority))
        effective = {d["decision_id"]: d for d in policy.effective_decisions}
    for check in registry.behavior_checks:
        decision = effective.get(check["parameters"]["decision_id"])
        constraints = [c for c in (decision or {}).get("constraints", []) if c["subject"] == check["subject"] and not c.get("conditions")]
        valid = decision and len(constraints) == 1 and covers(decision["scope"], check["scope"]) and check["check_id"] in policy.valid_confirmations.get(check.get("confirmation_ref"), set())
        if valid:
            rule_ids = [r["rule_id"] for r in policy.current_rules if r["rule_id"] in decision["supersedes"] and any(c["subject"] == check["subject"] for c in r["constraints"])]
            policy.effective_checks.append(check | {"constraint": constraints[0], "expected_value": constraints[0]["value"], "decision_id": decision["decision_id"], "rule_ids": rule_ids, "rule_id": rule_ids[0] if len(rule_ids) == 1 else None})
        else:
            policy.suggested_checks.append(check)
            reason = "behavior_check_scope_mismatch" if decision and not covers(decision["scope"], check["scope"]) else "behavior_check_unconfirmed"
            policy.issues.append(issue(reason, check["check_id"], blocking=False))
    return policy


@readonly
def resolve_applicable_rules(policy, relative_path, *, harness):
    return tuple(r for r in policy.current_rules if r["harness"] in ("shared", harness) and scope_contains(r["scope"], relative_path, harness))


@readonly
def resolve_constraint(policy, subject, relative_path, *, harness, exception_rule_ids=()):
    applicable = resolve_applicable_rules(policy, relative_path, harness=harness)
    values, conditional = [], []
    decisions = [d for d in policy.effective_decisions if scope_contains(d["scope"], relative_path, harness)]
    replaced = {edge["target_id"] for edge in policy.supersessions if any(d["decision_id"] == edge["decision_id"] for d in decisions)}
    for rule in applicable:
        replacing = [d for d in decisions if any(edge["decision_id"] == d["decision_id"] and edge["target_id"] == rule["rule_id"] for edge in policy.supersessions)]
        for constraint in rule["constraints"]:
            if constraint["subject"] != subject:
                continue
            superseded = any(not d["constraints"] or any(c["subject"] == subject and not c.get("conditions") for c in d["constraints"]) for d in replacing)
            if not superseded:
                if constraint.get("conditions"):
                    conditional.append(constraint["constraint_id"])
                else:
                    values.append((None, rule["rule_id"], constraint))
    for decision in decisions:
        replacing = [d for d in decisions if any(edge["decision_id"] == d["decision_id"] and edge["target_id"] == decision["decision_id"] for edge in policy.supersessions)]
        for constraint in decision["constraints"]:
            if constraint["subject"] == subject and not any(not d["constraints"] or any(c["subject"] == subject and not c.get("conditions") for c in d["constraints"]) for d in replacing):
                if constraint.get("conditions"):
                    conditional.append(constraint["constraint_id"])
                else:
                    values.append((decision["decision_id"], None, constraint))
    explicit_exceptions = [(None, rule["rule_id"], constraint) for rule in applicable if rule["rule_id"] in exception_rule_ids and rule["rule_id"] not in replaced for constraint in rule["constraints"] if constraint["subject"] == subject and not constraint.get("conditions")]
    if explicit_exceptions:
        values, conditional = explicit_exceptions, []
    result = {"status": "none", "operator": None, "value": None, "decision_id": None, "constraint_id": None, "rule_ids": [r["rule_id"] for r in applicable], "exception_applied": bool(explicit_exceptions), "gaps": []}
    if conditional:
        return result | {"status": "unknown", "gaps": ["condition_context_missing"], "conditional_constraint_ids": sorted(set(conditional))}
    if values:
        if any(compare_constraint_pair(x[2], y[2])["status"] == "incompatible" for x, y in combinations(values, 2)):
            result["status"] = "conflict"
        elif len({(c["operator"], type(c["value"]).__name__, json.dumps(c["value"], ensure_ascii=False)) for _, _, c in values}) == 1:
            did, _, constraint = values[0]
            result.update(status="resolved", operator=constraint["operator"], value=constraint["value"], decision_id=did, constraint_id=constraint["constraint_id"])
        else:
            result["status"] = "unknown"
    return result


class DecisionResolver:
    def __init__(self, rule_spans, registry, evidence_store):
        from .views import freeze
        self.rule_spans, self.registry, self.store = freeze(rule_spans), freeze(registry), evidence_store
        self._cache = {}

    def policy_at(self, as_of):
        key = instant(as_of).isoformat()
        if key not in self._cache:
            self._cache[key] = resolve_policy(self.rule_spans, self.registry, self.store, as_of=as_of)
        return self._cache[key]

    def resolve_constraint(self, subject, relative_path, *, as_of, harness, exception_rule_ids=()):
        return resolve_constraint(self.policy_at(as_of), subject, relative_path, harness=harness, exception_rule_ids=exception_rule_ids)


@dataclass
class ComparisonResult(ReadonlyRecord):
    as_of: str
    policy: PolicyState
    candidates: list[dict] = field(default_factory=list)
    evaluations: list[dict] = field(default_factory=list)
    issues: list[dict] = field(default_factory=list)
    coverage: list[dict] = field(default_factory=list)
    edit_targets: dict = field(default_factory=dict)


@readonly
def compare_constraints(rule_spans, registry, evidence_store, *, as_of):
    policy = resolve_policy(rule_spans, registry, evidence_store, as_of=as_of)
    result = ComparisonResult(policy.as_of, policy, issues=list(policy.issues))
    def candidate(kind, scope, facts, refs, action="ask_user", missing=()):
        cid = stable_id("rules_", kind, json.dumps(facts, sort_keys=True, ensure_ascii=False), json.dumps(scope, sort_keys=True), policy.as_of)
        item = {"candidate_id": cid, "origin": "rules", "kind": kind, "claim": f"{kind}: " + ", ".join(str(facts[k]) for k in ("rule_id", "decision_id", "left_id", "right_id", "gap_key") if k in facts), "scope": scope, "facts": facts, "support_refs": refs, "counter_refs": [], "missing_evidence": list(missing), "suggested_action": action, "details": {}}
        result.candidates.append(item)
        return item
    for edge in policy.supersessions:
        rule, decision = policy.get_rule(edge["target_id"]), policy.get_decision(edge["decision_id"])
        if rule is None:
            continue
        overlap = intersection(rule["scope"], decision["scope"])
        if overlap is None:
            continue
        for index, old in enumerate(rule["constraints"]):
            if not decision["constraints"] and index:
                continue
            replacements = [c for c in decision["constraints"] if c["subject"] == old["subject"] and conditions_intersect(old.get("conditions", {}), c.get("conditions", {}))]
            if decision["constraints"] and not replacements and not any(c["subject"] == r["subject"] for c in decision["constraints"] for r in rule["constraints"]):
                result.issues.append(issue("replacement_missing", rule["rule_id"], decision["decision_id"]))
            for new in replacements or ([None] if not decision["constraints"] else []):
                if new is not None:
                    evaluation = compare_constraint_pair(old, new) | {"scope": overlap}
                    result.evaluations.append(evaluation)
                    if evaluation["status"] != "incompatible":
                        continue
                full = covers(decision["scope"], rule["scope"])
                complete = interpretation_complete(rule)
                remaining = [c["constraint_id"] for c in rule["constraints"] if c != old] if new is not None else []
                if new is None and not complete:
                    candidate("insufficient_evidence", overlap, {"gap_key": "retirement_incomplete", "missing_items": ["complete_interpretation"], "related_rule_ids": [rule["rule_id"]]}, [rule["source_ref"], decision["source_ref"]], missing=["complete_interpretation"])
                    continue
                action = ("update" if new else "delete") if full else "narrow_scope"
                facts = {"rule_id": rule["rule_id"], "decision_id": decision["decision_id"], "decision_ids": [decision["decision_id"]], "subject": old["subject"], "clause_id": old["constraint_id"], "covered_clause_ids": [old["constraint_id"]], "remaining_obligations": remaining, "old_value": old["value"], "new_value": new["value"] if new else None, "conditions": old.get("conditions", {}) | (new.get("conditions", {}) if new else {}), "interpretation_complete": complete}
                facts.update(old_operator=old["operator"], new_operator=new["operator"] if new else None)
                if new is None:
                    facts["covered_clause_ids"] = [c["constraint_id"] for c in rule["constraints"]]
                cand = candidate("stale_rule", overlap, facts, [rule["source_ref"], decision["source_ref"]], action)
                result.edit_targets[cand["candidate_id"]] = {"rule_id": rule["rule_id"], "source_ref": rule["source_ref"], "line_start": rule["line_start"], "line_end": rule["line_end"], "old_text_sha256": rule["text_sha256"], "clause_span": old.get("span"), "clause_text_sha256": hashlib.sha256(rule["text"][old["span"]["start"]:old["span"]["end"]].encode()).hexdigest() if old.get("span") else None, "replacement_value": new["value"] if new else None, "preserved_constraint_ids": remaining, "edit_ready": full and (complete if new is None else bool(old.get("span")) and old["operator"] == new["operator"] == "equals" and isinstance(old["value"], str) and isinstance(new["value"], str)) and not facts["conditions"]}
    owners = [(r["rule_id"], r) for r in policy.current_rules] + [(d["decision_id"], d) for d in policy.effective_decisions]
    constraints = [(oid, owner, c) for oid, owner in owners for c in owner["constraints"]]
    for (lid, left, a), (rid, right, b) in combinations(constraints, 2):
        if a["subject"] != b["subject"]:
            continue
        overlap = intersection(left["scope"], right["scope"])
        if not overlap or (left.get("harness", "shared") != right.get("harness", "shared") and "shared" not in (left.get("harness", "shared"), right.get("harness", "shared"))):
            continue
        directly_replaced = any({edge["decision_id"], edge["target_id"]} == {lid, rid} and covers(edge["scope"], overlap) for edge in policy.supersessions)
        for owner_id, constraint in ((lid, a), (rid, b)):
            for edge in policy.supersessions:
                decision = policy.get_decision(edge["decision_id"])
                if edge["target_id"] == owner_id and decision and covers(edge["scope"], overlap) and (not decision["constraints"] or any(c["subject"] == constraint["subject"] and conditions_intersect(c.get("conditions", {}), constraint.get("conditions", {})) for c in decision["constraints"])):
                    directly_replaced = True
        evaluation = compare_constraint_pair(a, b) | {"scope": overlap}
        result.evaluations.append(evaluation)
        if evaluation["status"] == "incompatible" and not directly_replaced:
            candidate("rule_conflict", overlap, {"left_id": lid, "right_id": rid, "subject": a["subject"], "authority": "unresolved"}, [left["source_ref"], right["source_ref"]])
    for problem in result.issues:
        if problem["blocking"]:
            scope = {"include_prefixes": ["."], "exclude_prefixes": [], "harnesses": []}
            candidate("insufficient_evidence", scope, {"gap_key": problem["code"], "missing_items": [problem["code"]], "related_rule_ids": problem["related_ids"]}, [], missing=[problem["code"]])
    blocking = any(i["blocking"] for i in result.issues)
    for check in ("registry", "supersession", "constraint_comparison", "behavior_checks"):
        inactive_ids = {item["decision_id"] for item in policy.inactive_decisions if item["reason"] in ("status_draft", "status_withdrawn", "not_yet_effective", "expired")}
        requested_unresolved_checks = [item for item in policy.suggested_checks if item["parameters"]["decision_id"] not in inactive_ids]
        missing = not registry.provided or blocking or (check == "behavior_checks" and bool(requested_unresolved_checks))
        status = "failed" if check == "registry" and registry.issues else "not_run" if missing else "passed"
        result.coverage.append({"check_id": "f04." + check, "status": status, "inputs": [], "reason": "registry_not_provided" if not registry.provided else "registry_gaps" if missing else None, "artifact_refs": []})
    result.policy = replace(policy, issues=list(result.issues)).freeze()
    return result


@readonly
def scope_relation(left, right):
    overlap = intersection(left, right)
    return {"relation": "disjoint" if not overlap else "equal" if covers(left, right) and covers(right, left) else "contains" if covers(left, right) else "contained_by" if covers(right, left) else "overlaps", "intersection": overlap}


def clauses(policy, rule_id):
    return policy.clauses(rule_id)
