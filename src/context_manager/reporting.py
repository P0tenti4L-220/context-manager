"""Self-contained reports and collision-safe artifact manifests (F09)."""
from __future__ import annotations

import hashlib
import html
import json
import os
import re
import tempfile
import stat
import codecs
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from .contracts import ContractError, FieldError, dump_object, normalize_relative_path, parse_object
from .views import ReadonlyRecord, readonly
from .decisions import instant


class ArtifactCollisionError(Exception):
    code = "artifact_collision"
    exit_code = 1


class ArtifactWriteError(Exception):
    code = "artifact_write_failed"
    exit_code = 1

    def __init__(self, path, completed=()):
        super().__init__("artifact write failed; incomplete manifest retained")
        self.path = path
        self.completed_files = tuple(completed)


def _snapshot_matches(root, relative_path, metadata):
    """Verify bounded snapshot bytes through a no-follow directory fd chain."""
    if type(metadata.byte_length) is not int or not 0 <= metadata.byte_length <= 10 * 1024 * 1024:
        return False
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    directory = os.open(root, flags)
    source = None
    try:
        parts = relative_path.split("/")
        for component in parts[:-1]:
            child = os.open(component, flags, dir_fd=directory)
            os.close(directory)
            directory = child
        source = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        initial = os.fstat(source)
        if not stat.S_ISREG(initial.st_mode) or initial.st_size != metadata.byte_length:
            return False
        digest, decoder, count, newlines, tail = hashlib.sha256(), codecs.getincrementaldecoder("utf-8")("strict"), 0, 0, b""
        while True:
            block = os.read(source, min(65536, metadata.byte_length + 1 - count))
            if not block:
                break
            count += len(block)
            if count > metadata.byte_length:
                return False
            digest.update(block)
            decoder.decode(block)
            newlines += block.count(b"\n")
            tail = block[-1:]
        decoder.decode(b"", final=True)
        final = os.fstat(source)
        stable = (initial.st_dev, initial.st_ino, initial.st_size, initial.st_mtime_ns) == (final.st_dev, final.st_ino, final.st_size, final.st_mtime_ns)
        lines = newlines + (1 if count and tail != b"\n" else 0)
        return stable and count == metadata.byte_length and lines == metadata.line_count and digest.hexdigest() == metadata.sha256
    finally:
        if source is not None:
            os.close(source)
        os.close(directory)


def json_bytes(data):
    return (json.dumps(dump_object(data), ensure_ascii=False, allow_nan=False, indent=2) + "\n").encode("utf-8")


def safe_text(value):
    text = str(value).replace("\r\n", "\n")
    text = "".join("\ufffd" if (ord(ch) < 32 and ch not in "\n\t") or 0x7f <= ord(ch) <= 0x9f or ord(ch) in (*range(0x202a, 0x202f), *range(0x2066, 0x206a)) else ch for ch in text)
    if len(text.encode()) > 4096:
        text = text.encode()[:4096].decode("utf-8", errors="ignore") + " [truncated; see JSON]"
    return re.sub(r'([`*_\[\]()#|!\\])', r'\\\1', html.escape(text).replace("\n", " "))


@dataclass
class ArtifactBundle(ReadonlyRecord):
    files: dict[str, bytes]
    run: dict
    effective_status: str
    exit_code: int


@dataclass
class RunArtifacts(ReadonlyRecord):
    run_dir: Path
    files: dict
    diff_path: Path | None
    effective_status: str
    exit_code: int
    incomplete: bool = False
    errors: list = field(default_factory=list)


@readonly
def build_artifacts(run_result, evidence_catalog, repair_plan=None, *, trace=(), incident_analysis=None, inspection_result=None, clock=None):
    data = dump_object(run_result)
    parse_object("RunResult", data)
    from .evidence import EvidenceStore, EvidenceError
    try:
        EvidenceStore._validate_run_id(data["run_id"])
    except EvidenceError:
        raise ContractError("invalid_run_id", "RunResult", [FieldError("run_id", "invalid_value", "invalid run identity")]) from None
    if evidence_catalog.run_id != data["run_id"]:
        raise ContractError("run_id_mismatch", "RunResult", [FieldError("run_id", "invalid_value", "evidence catalog belongs to another run")])
    if incident_analysis is not None and (incident_analysis.project_id != data["project_id"] or not any(item["input_id"] == incident_analysis.input_id and item["sha256"] == incident_analysis.package_sha256 for item in data["inputs"])):
        raise ContractError("incident_input_mismatch", "IncidentAnalysis", [])
    catalog_root = Path(evidence_catalog.root)
    if not catalog_root.is_absolute() or catalog_root.is_symlink() or catalog_root.resolve() != catalog_root:
        raise ContractError("invalid_evidence_catalog", "EvidenceCatalog", [FieldError("root", "invalid_value", "expected a canonical run directory")])
    if repair_plan is not None and (repair_plan.run_id != data["run_id"] or instant(repair_plan.as_of) != instant(data["as_of"]) or dump_object(repair_plan.proposals) != data["proposals"]):
        raise ContractError("repair_plan_mismatch", "RepairPlan", [])
    ready_count = sum(p["status"] == "preview_ready" for p in data["proposals"])
    if ready_count and (ready_count != 1 or repair_plan is None or not repair_plan.diff):
        raise ContractError("diff_missing", "RepairPlan", [])
    if data["mode"]["semantic"] == "off" and (trace or any(c["check_id"] == "f06.semantic" and c["status"] == "passed" for c in data["coverage"])):
        raise ContractError("semantic_mode_conflict", "RunResult", [])
    now = (clock() if clock else datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
    findings = sorted(data["findings"], key=lambda f: ({"supported": 0, "hypothesis": 1, "unknown": 2}[f["assessment"]], f["finding_id"]))
    coverage = sorted(data["coverage"], key=lambda c: c["check_id"])
    errors = list(data["errors"])
    integrity_errors = []
    checked_locations, unresolved_keys = {}, set()
    def location(ref, fid=None):
        item = evidence_catalog.location(ref["evidence_id"])
        ok = bool(item and item.sha256 == ref["sha256"] and item.encoding in ("utf-8", "utf-8-bom") and type(item.line_count) is int and type(ref["line_start"]) is int and type(ref["line_end"]) is int and 1 <= ref["line_start"] <= ref["line_end"] <= item.line_count)
        if ok:
            try:
                path = normalize_relative_path(item.artifact_path)
                key = (path, item.sha256, item.byte_length, item.line_count, item.encoding)
                if key not in checked_locations:
                    checked_locations[key] = _snapshot_matches(catalog_root, path, item)
                ok = checked_locations[key]
            except (OSError, ValueError, UnicodeError):
                ok = False
        if not ok:
            key = (fid, ref["evidence_id"], ref["sha256"], ref["line_start"], ref["line_end"])
            if key not in unresolved_keys:
                unresolved_keys.add(key)
                integrity_errors.append({"finding_id": fid, "evidence_id": ref["evidence_id"], "path": None, "reason": "evidence_ref_unresolved"})
            return None
        return item
    for finding in findings:
        for ref in finding["support_refs"] + finding["counter_refs"]:
            location(ref, finding["finding_id"])
    if repair_plan:
        for proposal in repair_plan.proposals:
            if proposal["status"] == "preview_ready":
                for base in proposal["base_files"]:
                    item = evidence_catalog.location(base.get("evidence_id")) if base.get("evidence_id") else next((l for l in evidence_catalog.all_locations().values() if l.logical_name == base["relative_path"] and l.sha256 == base["sha256"]), None)
                    if not item or item.sha256 != base["sha256"] or type(item.line_count) is not int or not location({"evidence_id": item.evidence_id, "sha256": item.sha256, "line_start": 1, "line_end": max(1, item.line_count)}):
                        integrity_errors.append({"finding_id": None, "evidence_id": base.get("evidence_id"), "path": base["relative_path"], "reason": "base_file_unverifiable"})
    requested = data["status"]
    incomplete = not coverage or any(c["status"] != "passed" and not (data["mode"]["semantic"] == "off" and c["check_id"] == "f06.semantic" and c["status"] == "not_run" and c["reason"] == "disabled_by_user") for c in coverage)
    projected_trace = []
    for index, item in enumerate(trace):
        if index >= 1000:
            projected_trace.append({"event": "trace_truncated", "sequence": index, "elapsed_ms": 0, "code": "trace_limit_exceeded"})
            break
        item = dump_object(item)
        allowed = {"run_start", "model_call", "tool_call", "tool_result", "candidate_filtered", "candidate_rejected", "protocol_error", "run_end"}
        projected = {key: item[key] for key in ("event", "sequence", "elapsed_ms", "action", "success", "status", "stop_reason", "code", "field", "normalized", "candidate_id", "kind", "suggested_action", "reason", "support_refs", "read_id", "evidence_id", "line_start", "line_end", "text_bytes", "span_sha256", "latency_ms", "response_bytes", "content_bytes", "content_sha256") if key in item}
        if projected.get("event") not in allowed:
            projected = {"event": "error", "code": "unsupported_trace_event"}
        if item.get("event") == "run_start" and data["parameters"]["model"]:
            projected["model"] = data["parameters"]["model"]
        for ref in projected.get("support_refs", []):
            location(ref)
        projected_trace.append(projected)
    semantic_passed = any(c["check_id"] == "f06.semantic" and c["status"] == "passed" for c in coverage)
    trace_missing = data["mode"]["semantic"] == "local" and (not projected_trace or projected_trace[0].get("event") != "run_start" or not projected_trace[0].get("model") or projected_trace[-1].get("event") != "run_end" or any(type(item.get("sequence")) is not int or item["sequence"] != index or type(item.get("elapsed_ms")) is not int or item["elapsed_ms"] < 0 or item.get("event") == "error" for index, item in enumerate(projected_trace)) or semantic_passed and (projected_trace[-1].get("status") != "complete" or projected_trace[-1].get("stop_reason") != "submitted"))
    status = "failed" if requested == "failed" else "partial" if requested == "partial" or incomplete or integrity_errors or trace_missing else "complete"
    for reason in (["coverage_incomplete"] if incomplete else []) + (["semantic_trace_missing"] if trace_missing else []) + sorted({r["reason"] for r in integrity_errors}):
        errors.append({"code": reason, "path": None, "message": reason, "severity": "error"})
    if requested == "partial" and not incomplete and not integrity_errors and not trace_missing and not errors:
        errors.append({"code": "status_unexplained", "path": None, "message": "partial status retained from the caller", "severity": "warning"})
    exit_code = {"complete": 0, "partial": 3, "failed": 1}[status]
    roles = {"run": "run.json", "report": "report.md", "findings": "findings.json", "checks": "checks.json", "proposals": "proposals.json", "diff": None, "evidence_index": evidence_catalog.index_artifact_path, "trace": None, "incidents": None}
    docs = {}
    if inspection_result is not None:
        if inspection_result.run_id != data["run_id"]:
            raise ContractError("run_id_mismatch", "InspectionResult", [])
        docs["inspection.json"] = json_bytes(dump_object(inspection_result))
        roles["inspection"] = "inspection.json"
    def summary(items, key, values):
        return {"total": len(items), **{value: sum(i[key] == value for i in items) for value in values}}
    findings_summary = summary(findings, "assessment", ("supported", "hypothesis", "unknown"))
    coverage_summary = summary(coverage, "status", ("passed", "failed", "not_run"))
    proposals = data["proposals"]
    no_preview_reason = None if ready_count else "pending_confirmation_or_blocked" if proposals else "no_editable_findings"
    for kind, items, stats in (("findings", findings, findings_summary), ("checks", coverage, coverage_summary)):
        docs[kind + ".json"] = json_bytes({"schema_version": 1, "kind": kind, "run_id": data["run_id"], "generated_at": now, "summary": stats, kind: items})
    docs["proposals.json"] = json_bytes({"schema_version": 1, "kind": "proposals", "run_id": data["run_id"], "generated_at": now, "proposals": proposals, "advisories": repair_plan.advisories if repair_plan else [], "rejections": repair_plan.rejections if repair_plan else [], "repair_checks": repair_plan.checks if repair_plan else [], "apply_check": repair_plan.apply_check if repair_plan else None, "hunks": repair_plan.hunks if repair_plan else [], "diff_sha256": repair_plan.diff_sha256 if repair_plan else None, "no_preview_reason": no_preview_reason})
    if repair_plan and repair_plan.diff is not None:
        selected = next((p for p in proposals if p["proposal_id"] == repair_plan.diff_proposal_id), None)
        if hashlib.sha256(repair_plan.diff).hexdigest() != repair_plan.diff_sha256 or sum(p["status"] == "preview_ready" for p in proposals) != 1 or not selected or selected["diff_sha256"] != repair_plan.diff_sha256 or selected["status"] != "preview_ready":
            raise ContractError("diff_mismatch", "RepairPlan", [])
        if not repair_plan.apply_check or repair_plan.apply_check.get("exit_code") != 0 or not repair_plan.apply_check.get("applied_in_temp_copy"):
            raise ContractError("diff_precheck_missing", "RepairPlan", [])
        docs["proposal.diff"] = repair_plan.diff
        roles["diff"] = "proposal.diff"
    if incident_analysis is not None:
        docs["incidents.json"] = json_bytes(incident_analysis.to_dict())
        roles["incidents"] = "incidents.json"
    if data["mode"]["semantic"] == "local":
        docs["trace.jsonl"] = b"".join(json.dumps(item, ensure_ascii=False).encode() + b"\n" for item in projected_trace)
        roles["trace"] = "trace.jsonl"
    provenance = {location.provenance_kind for location in evidence_catalog.all_locations().values()}
    synthetic_basis = "synthetic" if data["synthetic"] and provenance and provenance <= {"synthetic", "project_file"} and all(i["synthetic"] for i in data["inputs"]) else "user_supplied" if provenance or data["inputs"] else "unknown"
    run = {"schema_version": 1, "kind": "run", "run_id": data["run_id"], "project_id": data["project_id"], "as_of": data["as_of"], "mode": data["mode"], "created_at": now, "status": status, "requested_status": requested if requested != status else None, "status_reason": errors[-1]["code"] if requested != status and errors else None, "exit_code": exit_code, "synthetic": synthetic_basis == "synthetic", "synthetic_basis": synthetic_basis, "project_revision": data["project_revision"], "parameters": data["parameters"] | {"as_of": data["as_of"]}, "inputs": data["inputs"], "coverage_summary": coverage_summary, "findings_summary": findings_summary, "proposals_summary": summary(proposals, "status", ("preview_ready", "needs_confirmation", "blocked")) | {"advisories": len(repair_plan.advisories) if repair_plan else 0, "rejections": len(repair_plan.rejections) if repair_plan else 0}, "artifacts": roles, "artifact_manifest": [], "artifact_manifest_status": "final", "integrity": {"evidence_links": "failed" if integrity_errors else "passed", "unresolved_refs": integrity_errors, "downgraded": requested != status}, "errors": sorted(errors, key=lambda e: (e["code"], e.get("path") or ""))}
    report = ["# Context Manager report", "", f"Project: {safe_text(data['project_id'])}", f"Status: **{status}** · exit code {exit_code}", f"Synthetic basis: {synthetic_basis}", f"As of: {safe_text(data['as_of'])}", "", "## Scope and coverage", "", safe_text(json.dumps(data["parameters"]["scope"], ensure_ascii=False)), "", f"Checks: {coverage_summary['passed']} passed, {coverage_summary['failed']} failed, {coverage_summary['not_run']} not run.", "", "## Findings", ""]
    if not findings:
        report.append("No grounded findings were produced. See check coverage for unperformed or incomplete checks.")
    if inspection_result is not None:
        inventory = ["## Instruction inventory", "", "Only task ancestors, rule directories and explicit imports were inspected.", ""]
        if not inspection_result.rule_files:
            inventory.append("No instruction files are available to check (没有可检查的规则).")
        for item in inspection_result.rule_files:
            inventory.append("- " + safe_text(item["relative_path"]) + ": " + safe_text(item["scope"]["scope_match"]) + "; expected loading: " + safe_text(item["load_expectation"]) + "; actual loading is not established by discovery.")
        inventory += ["", f"References missing: {inspection_result.counts['references_missing']}; commands missing: {inspection_result.counts['commands_missing']}.", "", "[Inventory, imports, scope and command details](inspection.json)", ""]
        report[report.index("## Findings"):report.index("## Findings")] = inventory
    for finding in findings:
        report += [f"### {finding['kind']} · {finding['assessment']}", "", safe_text(finding["claim"]), "", f"Action: {finding['suggested_action']} · basis: {finding['basis']}", ""]
        for ref in finding["support_refs"] + finding["counter_refs"]:
            item = location(ref, finding["finding_id"])
            label = safe_text(item.logical_name if item else ref["evidence_id"]) + f" (lines {ref['line_start']}–{ref['line_end']})"
            report.append(f"- [{label}]({quote(item.artifact_path, safe='/')})" if item else "- Unresolved evidence: " + label)
        if finding["missing_evidence"]:
            report += ["", "Missing evidence: " + safe_text(", ".join(finding["missing_evidence"]))]
        report.append("")
    report += ["## Repair proposals", ""]
    for proposal in proposals:
        report.append(f"- {proposal['status']}: {safe_text(proposal['proposal_id'])}")
        report.extend("  " + safe_text(q["prompt"]) for q in proposal["unresolved_questions"])
    if roles["diff"]:
        report += ["", "[Review approved diff](proposal.diff). The project was not modified."]
    if repair_plan:
        report.extend("- " + safe_text(a["summary"]) for a in repair_plan.advisories)
        report.extend("- Rejected edit: " + safe_text(r["reason"]) for r in repair_plan.rejections)
    if no_preview_reason:
        report += ["", "No preview diff: " + no_preview_reason + "."]
    report += ["", "## Incomplete checks and gaps", ""]
    report.extend(f"- {c['check_id']}: {c['status']} · {safe_text(c['reason'])}" for c in coverage if c["status"] != "passed")
    report.extend("- " + safe_text(e["code"]) + ": " + safe_text(e["message"]) for e in run["errors"])
    if data["mode"]["semantic"] == "off":
        report += ["", "Semantic analysis: not requested (未请求)."]
    if incident_analysis:
        report += ["", "Incident counts: " + safe_text(json.dumps(dump_object(incident_analysis.counts), sort_keys=True)), "", "[Incident observations, receipts and gaps](incidents.json)"]
        if incident_analysis.legacy:
            report += ["", "Legacy example: claims are unverified; full snapshots and receipts are required."]
    report += ["", "## Artifacts", "", "[Findings](findings.json) · [Checks](checks.json) · [Proposals](proposals.json) · [Evidence index](evidence/index.json)", ""]
    docs["report.md"] = "\n".join(report).encode()
    return ArtifactBundle(docs, run, status, exit_code)


def _write_artifact(path: Path, data: bytes, *, replace_existing=False):
    descriptor, name = tempfile.mkstemp(prefix="." + path.name + ".tmp-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(0o644)
        if replace_existing:
            os.replace(temporary, path)
        else:
            os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@readonly
def write_run_artifacts(run_result, output_root, evidence_catalog, repair_plan=None, *, trace=(), incident_analysis=None, inspection_result=None, clock=None):
    bundle = build_artifacts(run_result, evidence_catalog, repair_plan, trace=trace, incident_analysis=incident_analysis, inspection_result=inspection_result, clock=clock)
    root = Path(output_root).resolve()
    directory = root / bundle.run["run_id"]
    if directory.is_symlink() or directory.resolve() != evidence_catalog.root.resolve():
        raise ArtifactCollisionError("invalid artifact directory")
    directory.mkdir(mode=0o755, parents=True, exist_ok=True)
    if any((directory / name).exists() or (directory / name).is_symlink() for name in (*bundle.files, "run.json")):
        raise ArtifactCollisionError("run artifacts already exist")
    manifest = bundle.run | {"artifact_manifest_status": "incomplete", "artifact_manifest": []}
    try:
        with open(directory / "run.json", "xb") as handle:
            handle.write(json_bytes(manifest))
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError:
        raise ArtifactCollisionError("run artifacts already exist") from None
    except OSError:
        raise ArtifactWriteError("run.json") from None
    completed = {}
    store_metadata = directory / "_store.json"
    if store_metadata.is_file() and not store_metadata.is_symlink():
        metadata = store_metadata.read_bytes()
        completed["_store.json"] = {"path": "_store.json", "sha256": hashlib.sha256(metadata).hexdigest(), "byte_length": len(metadata), "status": "written"}
    writing = "run.json"
    try:
        order = ["findings.json", "checks.json", "proposals.json", "inspection.json", "proposal.diff", "incidents.json", "trace.jsonl", "report.md"]
        for name in order:
            if name not in bundle.files:
                continue
            data = bundle.files[name]
            writing = name
            _write_artifact(directory / name, data)
            completed[name] = {"path": name, "sha256": hashlib.sha256(data).hexdigest(), "byte_length": len(data), "status": "written"}
            manifest["artifact_manifest"] = list(completed.values())
            writing = "run.json"
            _write_artifact(directory / "run.json", json_bytes(manifest), replace_existing=True)
        manifest["artifact_manifest_status"] = "final"
        _write_artifact(directory / "run.json", json_bytes(manifest), replace_existing=True)
    except OSError:
        raise ArtifactWriteError(writing, completed) from None
    return RunArtifacts(directory, completed, directory / "proposal.diff" if bundle.run["artifacts"]["diff"] else None, bundle.effective_status, bundle.exit_code, errors=bundle.run["errors"])
