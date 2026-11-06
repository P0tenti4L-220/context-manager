"""Explicit projections between feature views; no inferred authority (F10)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .contracts import dump_object
from .inspector import stable_id
from .rules import RuleSource
from .views import freeze, readonly


def rule_sources(inspection):
    return [RuleSource(item["evidence_id"], item["relative_path"], item["harness"], {"include_prefixes": item["scope"]["include_prefixes"], "exclude_prefixes": item["scope"]["exclude_prefixes"], "harnesses": []}, item["kind"]) for item in inspection.rule_files if item["readable"] and item["kind"] != "imported_text" and item["applies_to_task"] is not None]


def inspection_candidates(inspection, store, request):
    candidates = []
    for item in inspection.references + inspection.commands:
        if item["status"] != "missing":
            continue
        rule_file = next((r for r in inspection.rule_files if r["relative_path"] == item["relative_path"]), None)
        if not rule_file or not rule_file["evidence_id"]:
            continue
        ref = dump_object(store.make_ref(store.get(rule_file["evidence_id"]), item["line"], item["line"]))
        target = item.get("target_path") or item.get("subject")
        candidates.append({"candidate_id": stable_id("reference_", item["relative_path"], item["line"], item["column"], target), "origin": "inspector", "kind": "dead_reference", "claim": f"Missing local reference or command target: {target}.", "scope": dump_object(request.task_scope), "facts": {"reference_id": item.get("ref_id", item.get("check_id"))}, "support_refs": [ref], "counter_refs": [], "missing_evidence": [], "suggested_action": "ask_user"})
    return candidates


@dataclass(frozen=True)
class EvidenceCatalog:
    run_id: str
    root: Path
    locations: dict
    index_artifact_path: str = "evidence/index.json"

    def __post_init__(self):
        object.__setattr__(self, "locations", freeze(self.locations))

    def location(self, evidence_id):
        return self.locations.get(evidence_id)

    @readonly
    def all_locations(self):
        return dict(self.locations)

    @classmethod
    def from_store(cls, store):
        return cls(store.run_id, store.run_dir, dict(store.all_locations()))
