"""The formal audit/diagnose pipeline, preflight and status aggregation (F10)."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from pathlib import Path

from .adapters import EvidenceCatalog, inspection_candidates, rule_sources
from .contracts import ContractError, FieldError, Provenance, ProvenanceKind, dump_object, parse_object
from .decisions import DecisionResolver, Registry, bind_registry, compare_constraints, decode_json, parse_registry
from .diagnosis import evaluate_findings
from .evidence import EvidenceError, EvidenceStore, SelectedInput, prepare_selected_input
from .incidents import prepare_incidents, parse_incident_input
from .inspector import InspectionOptions, inspect_project, validate_profile
from .intents import build_edit_intents
from .repair import plan_repairs
from .reporting import ArtifactCollisionError, ArtifactWriteError, write_run_artifacts
from .rules import extract_rule_spans
from .views import ReadonlyRecord, readonly


@dataclass
class PipelineResult(ReadonlyRecord):
    run_result: object
    evidence_catalog: EvidenceCatalog
    repair_plan: object
    analyst_result: object
    incident_analysis: object
    run_id: str
    artifacts: object
    inspection: object
    comparison: object
    diagnosis: object


def _profile(data):
    return validate_profile(data)


def merge_coverage(items):
    combined = {}
    rank = {"passed": 0, "not_run": 1, "failed": 2}
    for raw in items:
        item = dump_object(raw)
        key = item["check_id"]
        if key not in combined:
            combined[key] = item
            continue
        old = combined[key]
        old["status"] = max((old["status"], item["status"]), key=rank.get)
        old["inputs"] = sorted(set(old["inputs"] + item["inputs"]))
        old["artifact_refs"] = sorted(set(old["artifact_refs"] + item["artifact_refs"]))
        reasons = sorted({r for r in (old["reason"], item["reason"]) if r})
        old["reason"] = ";".join(reasons) or None
    return [combined[key] for key in sorted(combined)]


def _run(request, *, case_input=None, run_id=None, model_config=None, command="audit"):
    # All user input parsing happens before creating an output directory.
    request = parse_object("AuditRequest", dump_object(request))
    prepared = {}
    registry_input, case_data, profile, config = None, None, None, None
    try:
        if request.decision_registry:
            prepared["decisions"] = prepare_selected_input(request.decision_registry)
            registry_input = parse_registry(prepared["decisions"].data, project_id=request.project_id)
        if request.harness_profile:
            prepared["harness_profile"] = prepare_selected_input(request.harness_profile)
            profile = _profile(decode_json(prepared["harness_profile"].data))
        if command == "diagnose":
            prepared["incidents"] = case_input if isinstance(case_input, SelectedInput) else prepare_selected_input(case_input)
            case_data = parse_incident_input(prepared["incidents"].data, project_id=request.project_id)
        if request.semantic_mode == "local":
            from .model_transport import ModelConfig, load_model_config
            if model_config is None:
                raise ContractError("invalid_model_config", "ModelConfig", [FieldError("model_config", "missing_field", "local mode requires a model config")])
            config = model_config if isinstance(model_config, ModelConfig) else load_model_config(model_config)
    except EvidenceError as error:
        raise ContractError(error.code, "Input", [FieldError("input", error.code, "selected input is unavailable or unsafe")]) from None
    if request.out is None:
        raise ContractError("invalid_argument", "AuditRequest", [FieldError("out", "missing_field", "output root required")])
    # Validate both paths before mkdir, including programmatic callers.
    output = Path(request.out).expanduser().resolve()
    project = Path(request.repo).expanduser().resolve()
    if not project.is_dir():
        raise ContractError("input_not_found", "AuditRequest", [FieldError("repo", "input_not_found", "project directory does not exist")])
    try:
        EvidenceStore._validate_output_root(output, project)
    except EvidenceError:
        raise ContractError("out_inside_repo", "AuditRequest", [FieldError("out", "out_inside_repo", "output and project directories must not contain each other")]) from None
    if output.exists() and not output.is_dir():
        raise ContractError("invalid_argument", "AuditRequest", [FieldError("out", "invalid_value", "output root must be a directory")])
    parent = output.parent
    while not parent.exists() and parent != parent.parent:
        parent = parent.parent
    if not parent.is_dir():
        raise ContractError("invalid_argument", "AuditRequest", [FieldError("out", "invalid_value", "output parent must be a directory")])
    current_run = run_id or "run_" + uuid.uuid4().hex
    EvidenceStore._validate_run_id(current_run)
    output.mkdir(parents=True, exist_ok=True)
    try:
        (output / current_run).mkdir()
    except FileExistsError:
        raise ArtifactCollisionError("run directory already exists") from None
    store = EvidenceStore(project, output, current_run)
    inspection = comparison = diagnosis = incident = analyst = repairs = None
    stage = "selected_inputs"
    try:
        inputs = []
        for kind, value in prepared.items():
            synthetic = kind == "incidents" or (kind == "decisions" and registry_input.synthetic) or (kind == "harness_profile" and profile["source"] == "synthetic")
            evidence = store.capture_selected_input(value, input_id=kind, kind={"incidents": "event_package", "decisions": "decision_registry", "harness_profile": "data"}[kind], provenance=Provenance(ProvenanceKind.SYNTHETIC if synthetic else ProvenanceKind.USER_SUPPLIED, None, None), purpose="selected_input")
            inputs.append({"input_id": kind, "kind": kind, "logical_name": evidence.relative_path, "sha256": evidence.sha256, "byte_length": evidence.byte_length, "synthetic": bool(synthetic)})
        stage = "inspection"
        inspection = inspect_project(request, store, options=InspectionOptions(profile=profile))
        stage = "rules_decisions"
        extraction = extract_rule_spans(rule_sources(inspection), store)
        registry = bind_registry(registry_input, store) if registry_input else Registry(request.project_id, provided=False)
        comparison = compare_constraints(extraction.spans, registry, store, as_of=request.as_of)
        policy = replace(comparison.policy, editable_files=[i["relative_path"] for i in inspection.registered_instruction_files]).freeze()
        comparison = replace(comparison, policy=policy).freeze()
        resolver = DecisionResolver(extraction.spans, registry, store)
        channel_profile = None
        if profile and profile["source"] == "synthetic":
            channel_profile = {"source": "synthetic", "channels": {h["id"]: h.get("context_channels", []) for h in profile["harnesses"]}}
        stage = "incidents"
        incident = prepare_incidents(prepared["incidents"], project_id=request.project_id, task_scope=request.task_scope, input_id="incidents", evidence_store=store, decisions=resolver, channel_profile=channel_profile) if case_data is not None else None
        analyst, candidates, trace = None, [], []
        if config:
            stage = "semantic"
            from .analyst import AnalystBudget, AnalystRequest, analyze_context
            from .model_transport import LocalChatTransport
            material = {"rules": extraction.spans, "registered_rules": [r for r in registry.rules if not r.get("binding_failed")], "decisions": [d for d in registry.decisions if not d.get("binding_failed")], "confirmations": [c for c in registry.confirmations if not c.get("binding_failed")], "checks": registry.behavior_checks, "inactive_decisions": comparison.policy.inactive_decisions, "supersessions": comparison.policy.supersessions, "issues": comparison.policy.issues}
            analyst_request = AnalystRequest(current_run, request, command, material, incident.to_dict() if incident else None)
            analyst = analyze_context(analyst_request, store, LocalChatTransport(config), AnalystBudget(max_wall_seconds=config.max_wall_seconds))
            candidates, trace = analyst.candidates, analyst.trace
        stage = "diagnosis"
        diagnosis = evaluate_findings(request, [*comparison.candidates, *inspection_candidates(inspection, store, request)], candidates, comparison, incident, incident.receipts if incident else (), store, inspection=inspection, model_reads=analyst.reads if analyst else ())
        intents = build_edit_intents(comparison, diagnosis, candidates)
        stage = "repair"
        repairs = plan_repairs(diagnosis.findings, intents, comparison.policy, store)
        semantic_coverage = {"check_id": "f06.semantic", "status": "passed" if analyst and analyst.status == "complete" else "failed" if analyst else "not_run", "inputs": [], "reason": None if analyst and analyst.status == "complete" else (analyst.coverage.get("reason") or analyst.stop_reason) if analyst else "disabled_by_user", "artifact_refs": ["trace.jsonl"] if analyst else []}
        coverage = merge_coverage([*store.coverage_items(), *inspection.coverage, *extraction.coverage, *comparison.coverage, *diagnosis.coverage, *(incident.coverage_items if incident else ()), semantic_coverage])
        partial = any(c["status"] != "passed" and not (c["check_id"] == "f06.semantic" and c["reason"] == "disabled_by_user") for c in coverage)
        errors = [{"code": i["code"], "path": i["relative_path"], "message": i["detail"], "severity": "warning"} for i in inspection.errors]
        errors += [{"code": "decision_" + item["reason"], "path": None, "message": "Decision " + item["decision_id"] + " is inactive: " + item["reason"], "severity": "warning"} for item in comparison.policy.inactive_decisions]
        if analyst:
            errors += [{"code": e["code"], "path": None, "message": e["message"], "severity": "warning"} for e in analyst.errors]
        if request.semantic_mode == "off" and model_config is not None:
            errors.append({"code": "model_config_ignored", "path": None, "message": "model configuration was ignored in semantic off mode", "severity": "warning"})
        artifacts = ["run.json", "report.md", "findings.json", "checks.json", "proposals.json", "inspection.json", "evidence/index.json"]
        if incident:
            artifacts.append("incidents.json")
        if analyst:
            artifacts.append("trace.jsonl")
        if repairs.diff:
            artifacts.append("proposal.diff")
        raw_result = {"run_id": current_run, "project_id": request.project_id, "mode": {"command": command, "semantic": request.semantic_mode}, "as_of": request.as_of, "status": "partial" if partial else "complete", "coverage": coverage, "findings": diagnosis.findings, "proposals": repairs.proposals, "artifacts": artifacts, "errors": errors, "parameters": {"scope": dump_object(request.task_scope), "model": analyst.model if analyst else None}, "synthetic": bool(inputs) and all(i["synthetic"] for i in inputs), "inputs": inputs, "project_revision": None}
        result = parse_object("RunResult", dump_object(raw_result))
        store.flush_index()
        catalog = EvidenceCatalog.from_store(store)
        stage = "report"
        written = write_run_artifacts(result, output, catalog, repairs, trace=trace, incident_analysis=incident, inspection_result=inspection)
        return PipelineResult(result, catalog, repairs, analyst, incident, current_run, written, inspection, comparison, diagnosis)
    except (ArtifactCollisionError, ArtifactWriteError, MemoryError):
        raise
    except Exception as error:
        # A runtime failure must never look like an empty successful audit.
        failure = parse_object("RunResult", {"run_id": current_run, "project_id": request.project_id, "mode": {"command": command, "semantic": request.semantic_mode}, "as_of": request.as_of, "status": "failed", "coverage": [{"check_id": "pipeline." + stage, "status": "failed", "inputs": [], "reason": "stage_failed", "artifact_refs": []}], "findings": [], "proposals": [], "artifacts": [], "errors": [{"code": "pipeline_stage_failed", "path": None, "message": stage + ": " + type(error).__name__, "severity": "error"}], "parameters": {"scope": dump_object(request.task_scope), "model": None}})
        store.flush_index()
        catalog = EvidenceCatalog.from_store(store)
        written = write_run_artifacts(failure, output, catalog, trace=analyst.trace if analyst else ())
        return PipelineResult(failure, catalog, None, analyst, incident, current_run, written, inspection, comparison, diagnosis)
    finally:
        store.close()


@readonly
def run_audit(request, *, run_id=None, model_config=None):
    return _run(request, run_id=run_id, model_config=model_config)


@readonly
def run_diagnose(request, case_input, *, run_id=None, model_config=None):
    return _run(request, case_input=case_input, run_id=run_id, model_config=model_config, command="diagnose")
