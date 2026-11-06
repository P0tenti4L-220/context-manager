"""Quality gates that distinguish model review notes from reported issues."""
from __future__ import annotations

import re
from collections.abc import Mapping

from .rules import span_text


def read_covers(reference, reads):
    """Only ranges returned to this Analyst invocation establish model reads."""
    cursor = reference["line_start"]
    ranges = [(read["line_start"], read["line_end"]) for read in reads if isinstance(read, Mapping) and read.get("evidence_id") == reference["evidence_id"] and read.get("sha256") == reference["sha256"] and isinstance(read.get("read_id"), str) and type(read.get("line_start")) is int and type(read.get("line_end")) is int and 1 <= read["line_start"] <= read["line_end"] and (read.get("text_bytes") or 0) > 0]
    for start, end in sorted(ranges):
        if start > cursor:
            break
        if end >= cursor:
            cursor = end + 1
        if cursor > reference["line_end"]:
            return True
    return False


def _inside(reference, source):
    return (reference["evidence_id"] == source["evidence_id"]
            and reference["sha256"] == source["sha256"]
            and source["line_start"] <= reference["line_start"] <= reference["line_end"] <= source["line_end"])


def cites_historical_material(references, rule_spans):
    """Identify cleanup evidence that includes a historical or quoted block."""
    def cited(source):
        return any(ref["evidence_id"] == source["evidence_id"]
                   and ref["sha256"] == source["sha256"]
                   and ref["line_start"] <= source["line_end"]
                   and source["line_start"] <= ref["line_end"]
                   for ref in references)

    for rule in rule_spans:
        if rule.get("context") in ("historical", "quoted"):
            if cited(rule["source_ref"]) or any(cited(item["source_ref"]) for item in rule.get("attachments", ())):
                return True
    return False


def _historical_conflict(candidate, rule_spans):
    """Use F04's block roles to screen comparisons with examples or quotations."""
    historical, active = [], []
    for rule in rule_spans:
        source = rule["source_ref"]
        if rule.get("context") in ("historical", "quoted"):
            historical.append(source)
            historical.extend(item["source_ref"] for item in rule.get("attachments", ()))
        elif rule.get("context") == "active":
            active.append(source)
    current, historical_count = [], 0
    for ref in candidate["support_refs"]:
        if any(_inside(ref, source) for source in historical):
            historical_count += 1
        else:
            current.append(ref)
    if not historical_count:
        return False
    return not current or (len(current) == 1 and any(_inside(current[0], source) for source in active))


def _has_independent_edit_sources(candidate, incident_material):
    tasks = set()
    events = incident_material.get("events", ())
    if not isinstance(events, (list, tuple)):
        return False
    for event in events:
        if not isinstance(event, Mapping):
            continue
        action = event.get("action", {})
        task_id = event.get("task_id")
        if (not isinstance(action, Mapping) or action.get("kind") != "edit"
                or not isinstance(task_id, str) or not task_id
                or event.get("status", "normalized") != "normalized"
                or "scope" in event and not event["scope"]):
            continue
        sources = [event.get("source_ref"), action.get("after_ref"), action.get("evidence_ref")]
        for source in sources:
            if not isinstance(source, Mapping):
                continue
            if any(_inside(ref, source) or _inside(source, ref) for ref in candidate["support_refs"]):
                tasks.add(task_id)
                break
        if len(tasks) >= 2:
            return True
    return False


def non_issue_reason(candidate, evidence_store, *, rule_spans=(), incident_material=None):
    """Filter only mechanically identifiable no-action/echo suggestions.

    This is not a semantic truth judgement. Concrete cleanup hypotheses remain
    hypotheses and still require human review; original notes are retained.
    """
    if candidate.get("kind") in ("stale_rule", "rule_conflict", "policy_mismatch") and _historical_conflict(candidate, rule_spans):
        return "historical_material_is_not_conflicting_instruction"
    if candidate.get("kind") == "recurrence" and incident_material is not None and not _has_independent_edit_sources(candidate, incident_material):
        return "independent_edit_sources_not_established"
    if candidate.get("kind") != "cleanup_candidate":
        return None
    if candidate.get("suggested_action") == "keep":
        return "no_action_recommended"
    if candidate.get("rationale", "").strip():
        return None
    def normalize(text):
        text = re.sub(r'^\s*(?:[-*+>]\s+|\d+[.)]\s+)', "", text)
        return " ".join(text.replace("`", "").split()).casefold()
    claim = normalize(candidate["claim"])
    for reference in candidate.get("support_refs", []):
        text = span_text(evidence_store, reference)
        if claim and (claim == normalize(text) or any(claim == normalize(line) for line in text.split("\n"))):
            return "verbatim_restatement_without_rationale"
    return None
