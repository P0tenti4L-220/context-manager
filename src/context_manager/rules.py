"""Markdown rule blocks and component-based scope operations (F04)."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Sequence

from .contracts import Scope, dump_object
from .evidence import EvidenceError, EvidenceStore
from .inspector import beneath, stable_id
from .views import ReadonlyRecord, freeze, readonly


def scope_data(scope: Scope | dict) -> dict:
    return dump_object(scope)


def scope_contains(scope: Scope | dict, path: str, harness: str = "shared") -> bool:
    data = scope_data(scope)
    tools = data.get("harnesses", [])
    return (not tools or "shared" in tools or harness in tools) and any(beneath(path, p) for p in data["include_prefixes"]) and not any(beneath(path, p) for p in data.get("exclude_prefixes", []))


def intersection(left: Scope | dict, right: Scope | dict) -> dict | None:
    a, b = scope_data(left), scope_data(right)
    ah, bh = set(a.get("harnesses", [])), set(b.get("harnesses", []))
    ah = set() if "shared" in ah else ah
    bh = set() if "shared" in bh else bh
    if ah and bh and not ah & bh:
        return None
    excluded = sorted(set(a.get("exclude_prefixes", []) + b.get("exclude_prefixes", [])))
    include = set()
    for x in a["include_prefixes"]:
        for y in b["include_prefixes"]:
            narrow = y if beneath(y, x) else x if beneath(x, y) else None
            if narrow is not None and not any(beneath(narrow, e) for e in excluded):
                include.add(narrow.rstrip("/"))
    if not include:
        return None
    return {"include_prefixes": sorted(include), "exclude_prefixes": excluded, "harnesses": sorted(ah & bh if ah and bh else ah or bh)}


def covers(left: Scope | dict, right: Scope | dict) -> bool:
    a, b = scope_data(left), scope_data(right)
    ah, bh = set(a.get("harnesses", [])), set(b.get("harnesses", []))
    if ah and "shared" not in ah and (not bh or "shared" in bh or not bh <= ah):
        return False
    for prefix in b["include_prefixes"]:
        if any(beneath(prefix, e) for e in b.get("exclude_prefixes", [])):
            continue
        if not any(beneath(prefix, p) for p in a["include_prefixes"]):
            return False
        for excluded in a.get("exclude_prefixes", []):
            if (beneath(prefix, excluded) or beneath(excluded, prefix)) and not any(beneath(excluded, e) for e in b.get("exclude_prefixes", [])):
                return False
    return True


def span_bytes(store: EvidenceStore, ref: dict) -> bytes:
    validation = store.validate_ref(ref)
    if not validation.ok:
        raise EvidenceError(validation.code or "evidence_ref_invalid", "invalid source reference")
    evidence = store.get(ref["evidence_id"])
    raw = store.read_raw(evidence.evidence_id, max_bytes=store.limits.max_selected_input_bytes)
    parts = raw.split(b"\n")
    lines = [line + b"\n" for line in parts[:-1]] + ([parts[-1]] if parts[-1] else [])
    return b"".join(lines[ref["line_start"] - 1:ref["line_end"]])


def span_text(store: EvidenceStore, ref: dict) -> str:
    raw = span_bytes(store, ref)
    if ref["line_start"] == 1:
        raw = raw.removeprefix(b"\xef\xbb\xbf")
    return raw.decode("utf-8").replace("\r\n", "\n").removesuffix("\n")


def interpretation_complete(rule) -> bool:
    covered = set()
    for constraint in rule.get("constraints", []):
        span = constraint.get("span")
        if not span or not 0 <= span["start"] < span["end"] <= len(rule["text"]):
            return False
        covered.update(range(span["start"], span["end"]))
    visible = re.sub(r'^\s*(?:[-*+]|\d+[.)])\s+', lambda match: " " * len(match[0]), rule["text"])
    return bool(rule.get("constraints")) and all(char.isspace() or offset in covered for offset, char in enumerate(visible))


@dataclass(frozen=True)
class RuleSource:
    evidence_id: str
    relative_path: str
    harness: str
    source_scope: Scope | dict
    kind: str

    def __post_init__(self):
        object.__setattr__(self, "source_scope", freeze(self.source_scope))


@dataclass
class RuleExtraction(ReadonlyRecord):
    spans: list[dict] = field(default_factory=list)
    gaps: list[dict] = field(default_factory=list)
    sources: list[RuleSource] = field(default_factory=list)
    coverage: list[dict] = field(default_factory=list)


@readonly
def extract_rule_spans(sources: Sequence[RuleSource], evidence_store: EvidenceStore) -> RuleExtraction:
    result = RuleExtraction(sources=list(sources))
    for source in sources:
        try:
            evidence = evidence_store.get(source.evidence_id)
            if not evidence.line_count:
                continue
            cursor, chunks = 1, []
            while cursor <= evidence.line_count:
                ref = evidence_store.make_ref(evidence, cursor, evidence.line_count)
                read = evidence_store.read_evidence(ref, caller="inspector")
                if read.empty or read.line_end < cursor:
                    raise EvidenceError("text_truncated", "rule source could not be read completely")
                chunks.extend(read.text.split("\n"))
                if read.truncated and read.truncation_reason == "line_too_long":
                    raise EvidenceError("text_truncated", "rule source contains an oversized line")
                cursor = read.line_end + 1
            visible = re.sub(r'<!--[\s\S]*?(?:-->|\Z)', lambda m: re.sub(r'[^\n]', " ", m[0]), "\n".join(chunks))
            lines, section, index, last, parents = visible.split("\n"), [], 0, None, []
            while index < len(lines):
                line = lines[index]
                if index == 0 and line.strip() == "---":
                    index += 1
                    while index < len(lines) and lines[index].strip() != "---":
                        index += 1
                    if index >= len(lines):
                        result.gaps.append({"code": "frontmatter_unterminated", "relative_path": source.relative_path, "line_start": 1, "line_end": len(lines), "reason": "frontmatter_unterminated"})
                    index += 1
                    continue
                heading = re.match(r'^\s*(#{1,6})\s+(.+)', line)
                setext = index + 1 < len(lines) and re.fullmatch(r'\s*(?:=+|-+)\s*', lines[index + 1]) and line.strip()
                if heading or setext:
                    level, title = (len(heading[1]), heading[2]) if heading else (1 if lines[index + 1].strip()[0] == "=" else 2, line)
                    section = section[:level - 1] + [title.strip()]
                    index += 1 if heading else 2
                    last, parents = None, []
                    continue
                if not line.strip():
                    index += 1
                    continue
                fence = re.match(r'^\s*(`{3,}|~{3,})', line)
                if fence:
                    start, marker = index, fence[1]
                    index += 1
                    closing = r'^\s*' + re.escape(marker[0]) + "{" + str(len(marker)) + r',}\s*$'
                    while index < len(lines) and not re.match(closing, lines[index]):
                        index += 1
                    index = min(index + 1, len(lines))
                    if last is not None:
                        ref = dump_object(evidence_store.make_ref(evidence, start + 1, index))
                        last["attachments"].append({"source_ref": ref, "sha256": hashlib.sha256(span_bytes(evidence_store, ref)).hexdigest()})
                    continue
                if "|" in line and index + 1 < len(lines) and re.match(r'^[| :\-]+$', lines[index + 1]):
                    result.gaps.append({"code": "unsupported_table", "relative_path": source.relative_path, "line_start": index + 1, "line_end": index + 2, "reason": "table_interpretation_not_supported"})
                    while index < len(lines) and "|" in lines[index]:
                        index += 1
                    continue
                start = index
                list_item = re.match(r'^(\s*)(?:[-*+]|\d+[.)])\s+', line)
                kind = "list_item" if list_item else "quote" if line.lstrip().startswith(">") else "paragraph"
                indent = len(list_item[1]) if list_item else 0
                index += 1
                while index < len(lines) and lines[index].strip() and not re.match(r'^\s*(?:#{1,6}\s|[-*+]\s|\d+[.)]\s|`{3,}|~{3,})', lines[index]):
                    if list_item and len(lines[index]) - len(lines[index].lstrip()) <= indent:
                        break
                    index += 1
                ref = dump_object(evidence_store.make_ref(evidence, start + 1, index))
                digest = hashlib.sha256(span_bytes(evidence_store, ref)).hexdigest()
                rule_id = stable_id("rule_", source.relative_path, start + 1, index, source.harness, digest)
                while parents and parents[-1][0] >= indent:
                    parents.pop()
                historical = any(re.search(r'History|Historical|Changelog|Deprecated|Superseded|历史|变更记录|已废弃', title, re.I) for title in section)
                last = {"rule_id": rule_id, "relative_path": source.relative_path, "source_ref": ref, "text": span_text(evidence_store, ref), "text_sha256": digest, "scope": scope_data(source.source_scope), "harness": source.harness, "interpretation_status": "unresolved", "confirmation_ref": None, "block_kind": kind, "section_path": list(section), "parent_rule_id": parents[-1][1] if list_item and parents else None, "context": "quoted" if kind == "quote" else "historical" if historical else "active", "attachments": []}
                result.spans.append(last)
                if list_item:
                    parents.append((indent, rule_id))
        except (EvidenceError, UnicodeError) as error:
            result.gaps.append({"code": getattr(error, "code", "encoding_invalid"), "relative_path": source.relative_path, "line_start": 1, "line_end": None, "reason": "source_read_incomplete"})
    blocking = [g for g in result.gaps if g["code"] != "unsupported_table"]
    result.coverage = [{"check_id": "f04.rule_extraction", "status": "not_run" if blocking else "passed", "inputs": [], "reason": "source_read_incomplete" if blocking else "none_found" if not sources else None, "artifact_refs": []}]
    return result
