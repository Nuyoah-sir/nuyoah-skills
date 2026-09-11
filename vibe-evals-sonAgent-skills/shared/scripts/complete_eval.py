#!/usr/bin/env python3
"""Own the durable remote phase state machine for a complete evaluation.

Every forward transition happens only after the relevant validator subreport
passes, and only these commands may write canonical artifacts. A weak agent calls
the commands in order and reports their exit codes; it never hand-edits JSON.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any

from artifact_integrity import sha256_file
from collect_model_inventory import collect_model_inventory
from decision_workspace import finalize_audit, initialize_workspace, record_closure, record_pair
from discover_task_package import discover_task_package
from form_ready_context import load_form_ready_base, load_observation_registry
from initialize_bundle import initialize_bundle
from initialize_form_ready import initialize_form_ready
from package_bundle import package_bundle
from package_form_ready import SourceChangedError, package_form_ready
from presentation_workspace import initialize_presentation, record_attestation, record_slot, validate_presentation
from project_form_ready_scores import project_scores, verify_projected_scores
from record_evidence import EvidenceRejected, derived_status_ignoring_declared, record_evidence
from record_observation import append_jsonl, interactive_stdin_available
from record_observation import SUBJECTIVE_CLASS, required_classification
from record_review import ReviewRejected, record_review
from register_media import plan_required_media_uses, register_source_media
from register_media import register_render
from render_media import render_media
from render_supporting_outputs import render_supporting_outputs, verify_supporting_outputs
from summarize_conversation import summarize_conversation
from validate_bundle import validate_bundle
from validate_final_decisions import validate_final_decisions
from validate_form_ready import assess_form_ready, validate_form_ready
from verify_source import verify_frozen_source

PHASES = (
    "discovered", "evidence_initialized", "review_complete", "evidence_collected", "base_sealed",
    "base_verified", "media_frozen", "observations_complete", "decisions_complete",
    "presentation_complete", "outputs_complete", "source_verified", "sealed",
)
TERMINAL_PHASE = "invalid_source_changed"
STATE_NAME = "run-state.json"
EXIT_OK, EXIT_UNRESOLVED, EXIT_INVALID, EXIT_SOURCE_CHANGED, EXIT_HUMAN_REQUIRED = 0, 2, 3, 4, 5


class PhaseError(ValueError):
    """Aphases were skipped, reordered, or attempted from the wrong state."""

    exit_code = EXIT_INVALID


class HumanRequired(ValueError):
    """This action cannot be completed without a real human in the loop."""

    exit_code = EXIT_HUMAN_REQUIRED


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def _atomic(path: Path, text: str) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _state_path(run: Path) -> Path:
    return run / STATE_NAME


def _load_state(run: str | Path) -> dict[str, Any]:
    path = _state_path(Path(run))
    if not path.is_file():
        raise PhaseError(f"RUN_NOT_FOUND: {run} has no {STATE_NAME}")
    state = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(state, dict):
        raise PhaseError("RUN_STATE_INVALID: run state must be an object")
    if state.get("phase") == TERMINAL_PHASE:
        raise SourceChangedError("RUN_TERMINATED: invalid_source_changed cannot be resumed")
    return state


def _advance(run: Path, state: dict[str, Any], phase: str, **extra: Any) -> dict[str, Any]:
    if phase not in PHASES:
        raise PhaseError(f"PHASE_UNKNOWN: {phase!r}")
    state.update(extra)
    state["phase"] = phase
    state["updated_at"] = _now()
    state.setdefault("completed_phase_receipts", [])
    _atomic(_state_path(run), _dump(state))
    return state


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _require(state: dict[str, Any], *allowed: str, action: str = "this action") -> None:
    if state.get("phase") not in allowed:
        raise PhaseError(
            f"PHASE_ORDER_VIOLATION: {action} requires phase in {sorted(allowed)}, current phase is {state.get('phase')!r}"
        )


def _run_paths(run: Path) -> dict[str, Path]:
    return {"v1": run / "v1", "base": run / "base" / "evidence-bundle.zip",
            "form": run / "form-ready", "task": Path(json.loads(_state_path(run).read_text(encoding="utf-8"))["task_root"])}


def _sync_base_status(v1_root: Path) -> str:
    """Keep the inner manifest's declared status equal to the script-derived status."""

    report = validate_bundle(v1_root, require_seal=False)
    derived = derived_status_ignoring_declared(report)
    path = v1_root / "MANIFEST.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("package_status") != derived:
        manifest["package_status"] = derived
        _atomic(path, _dump(manifest))
    return derived


def command_start(args: argparse.Namespace) -> int:
    task_root, output_root = Path(args.task_root), Path(args.output_root)
    if not task_root.is_absolute() or not output_root.is_absolute():
        raise PhaseError("START_REQUIRES_ABSOLUTE_PATHS: pass absolute --task-root and --output-root")
    run_id = args.run_id or f"{_now().replace(':', '').replace('-', '')[:15]}-{uuid.uuid4().hex[:6]}"
    run = output_root / run_id
    if run.exists():
        raise PhaseError(f"RUN_EXISTS: {run} already exists")
    discovery = discover_task_package(task_root)
    if not discovery.get("ok"):
        raise PhaseError("DISCOVERY_FAILED: " + json.dumps(discovery, ensure_ascii=False))
    run.mkdir(parents=True)
    _atomic(run / "discovery.json", _dump(discovery))
    state = {"run_id": run_id, "task_root": str(task_root), "phase": "discovered",
             "created_at": _now(), "updated_at": _now(), "completed_phase_receipts": [],
             "terminal_failure_code": None}
    _atomic(_state_path(run), _dump(state))
    initialize_bundle(task_root, run / "discovery.json", run / "v1")
    freeze = json.loads((run / "v1" / "source-freeze.json").read_text(encoding="utf-8"))
    for model_id, model in (freeze.get("model_sources") or {}).items():
        final_root = task_root / str(model.get("final_root"))
        rounds = [(int(number), task_root / str(path)) for number, path in (model.get("round_roots") or {}).items()]
        collect_model_inventory(final_root, rounds, run / "v1" / "models" / model_id / "inventory.json", task_root)
        conversation = model.get("conversation_source_path")
        if isinstance(conversation, str) and (task_root / conversation).is_file():
            summarize_conversation(task_root / conversation, run / "v1" / "models" / model_id / "conversation-summary.json",
                                   task_root, None, None, run / "v1")
    report = validate_bundle(run / "v1", require_seal=False)
    _advance(run, state, "evidence_initialized", base_result=report["result"], base_derived=report["derived_status"])
    print(json.dumps({"run": str(run), "run_id": run_id, "phase": "evidence_initialized",
                      "base_derived": report["derived_status"]}, ensure_ascii=False))
    return EXIT_UNRESOLVED if report["derived_status"] != "ready_for_form" else EXIT_OK


def _run_dir(args: argparse.Namespace) -> Path:
    return Path(args.run)


def command_status(args: argparse.Namespace) -> int:
    run = _run_dir(args)
    try:
        state = _load_state(run)
    except SourceChangedError:
        print(json.dumps({"run": str(run), "phase": TERMINAL_PHASE}, ensure_ascii=False))
        return EXIT_SOURCE_CHANGED
    print(json.dumps(state, ensure_ascii=False))
    return EXIT_OK if state["phase"] == "sealed" else EXIT_UNRESOLVED


def command_accept_review(args: argparse.Namespace) -> int:
    run, state = _run_dir(args), None
    state = _load_state(run)
    _require(state, "evidence_initialized", action="accept-review")
    result = record_review(run / "v1", requirements=Path(args.requirements), review_items=Path(args.review_items))
    _advance(run, state, "review_complete", review_result=result["status"])
    print(json.dumps(result, ensure_ascii=False))
    return EXIT_OK


def command_accept_evidence(args: argparse.Namespace) -> int:
    run = _run_dir(args)
    state = _load_state(run)
    _require(state, "review_complete", "evidence_collected", action="accept-evidence")
    result = record_evidence(run / "v1", Path(args.candidate))
    if result["missing_pairs"]:
        state["phase"] = state.get("phase", "review_complete")
        _atomic(_state_path(run), _dump(state))
        print(json.dumps(result, ensure_ascii=False))
        return EXIT_UNRESOLVED
    result["status"] = _sync_base_status(run / "v1")
    _advance(run, state, "evidence_collected", evidence_result=result["status"])
    print(json.dumps(result, ensure_ascii=False))
    return EXIT_OK


def command_seal_base(args: argparse.Namespace) -> int:
    run = _run_dir(args)
    state = _load_state(run)
    _require(state, "evidence_collected", action="seal-base")
    archive = run / "base" / "evidence-bundle.zip"
    result = package_bundle(run / "v1", archive)
    manifest = json.loads((run / "v1" / "MANIFEST.json").read_text(encoding="utf-8"))
    inner_binding = {
        "phase": "base_sealed", "run_id": state["run_id"], "base_package_id": manifest["package_id"],
        "base_zip_sha256": result["sha256"], "source_input_digest": manifest["source_input_digest"],
        "created_at": state["created_at"],
        "completed_phase_receipts": [{"phase": "base_sealed", "base_zip_sha256": result["sha256"]}],
    }
    initialize_form_ready(archive, archive.with_suffix(".zip.sha256"), run / "form-ready", run_state=inner_binding)
    _advance(run, state, "base_verified", base_zip_sha256=result["sha256"],
             completed_phase_receipts=[{"phase": "base_sealed", "base_zip_sha256": result["sha256"]}])
    print(json.dumps({**result, "phase": "base_verified"}, ensure_ascii=False))
    return EXIT_OK


def command_freeze_media(args: argparse.Namespace) -> int:
    run = _run_dir(args)
    state = _load_state(run)
    _require(state, "base_verified", action="freeze-media")
    form = run / "form-ready"
    manifest, context, freeze = load_form_ready_base(form)
    classifications = _write_classifications(form, context, manifest)
    plan = plan_required_media_uses(form)
    registered = []
    required = [item for item in plan if item["role"] in {"target", "feedback"}]
    for item in plan:
        if item["role"] in {"target", "feedback"}:
            registered.append(register_source_media(form, Path(state["task_root"]), freeze, item))
    if len(registered) < len(required):
        print(json.dumps({"planned": len(plan), "required_source_media": len(required), "registered": len(registered)}, ensure_ascii=False))
        return EXIT_UNRESOLVED
    _advance(run, state, "media_frozen", media_registered=len(registered))
    print(json.dumps({"planned": len(plan), "required_source_media": len(required), "registered": len(registered),
                      "classifications": classifications}, ensure_ascii=False))
    return EXIT_OK


def _class_objective(criterion: str) -> str:
    """Pick the least ambitious objective class a criterion can support."""

    if "#" in criterion or any(word in criterion for word in ("\u84dd\u8272", "\u7ea2\u8272", "\u7eff\u8272", "\u9ed1\u8272", "\u767d\u8272")):
        return "exact_color"
    if any(word in criterion for word in ("\u6570\u91cf", "\u81f3\u5c11", "\u4e0d\u5c11\u4e8e", "\u4e2a\u6570")):
        return "count"
    if any(word in criterion for word in ("\u6587\u6848", "\u6587\u5b57", "label", "\u6807\u7b7e\u540d")):
        return "literal_text"
    return "presence"


def _write_classifications(form: Path, context, manifest: dict) -> int:
    """Generate the criterion classifications the observation gate depends on."""

    path = form / "decisions/criterion-classifications.json"
    if path.is_file():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            existing = {}
        if existing.get("records"):
            return len(existing["records"])
    records = []
    for rubric in context.rubrics:
        criterion = str(rubric.get("criterion", ""))
        adjudication_affected = any(
            rubric.get("id") in ((row or {}).get("adjudication_ids") or []) for row in context.evidence.values()
        )
        forced = required_classification(rubric, adjudication_affected)
        record = {
            "outer_package_id": manifest["outer_package_id"],
            "base_package_id": context.package_id,
            "base_zip_sha256": manifest["base"]["sha256"],
            "source_input_digest": context.source_input_digest,
            "task_id": context.task_id,
            "rubric_id": rubric.get("id"),
            "round": rubric.get("round"),
            "criterion_sha256": rubric.get("criterion_sha256"),
        }
        if forced == SUBJECTIVE_CLASS:
            record.update({"classification": SUBJECTIVE_CLASS, "reason": "Runner default: subjective or policy-ambiguous.",
                           "requires_remote_human": True})
        else:
            record.update({"classification": _class_objective(criterion), "measurable_phrase": criterion,
                           "reason": "Runner default: objective class derived from the criterion wording.",
                           "requires_remote_human": False})
        records.append(record)
    _atomic(path, _dump({"schema_version": "2.0.0", "outer_package_id": manifest["outer_package_id"],
                         "base_package_id": context.package_id, "records": records}))
    return len(records)


def command_record_vision(args: argparse.Namespace) -> int:
    run = _run_dir(args)
    state = _load_state(run)
    _require(state, "media_frozen", "observations_complete", action="record-vision")
    record = json.loads(Path(args.record).read_text(encoding="utf-8"))
    if not isinstance(record, dict) or not record.get("invocation_id"):
        raise PhaseError("VISION_RECORD_INCOMPLETE: provider invocation metadata is required")
    append_jsonl(run / "form-ready" / "observations/machine-vision.jsonl", record)
    print(json.dumps({"appended": record.get("observation_id")}, ensure_ascii=False))
    return EXIT_UNRESOLVED


def command_render(args: argparse.Namespace) -> int:
    """Render the planned passive-SVG candidates and register them as review media."""

    run = _run_dir(args)
    state = _load_state(run)
    _require(state, "media_frozen", "observations_complete", action="render")
    form = run / "form-ready"
    manifest, context, freeze = load_form_ready_base(form)
    plan = [item for item in plan_required_media_uses(form) if item["role"].startswith("candidate")]
    if args.media_id:
        plan = [item for item in plan if item["media_id"] == args.media_id]
    rendered, skipped = [], []
    for item in plan:
        source = Path(state["task_root"]).joinpath(*item["source_relative_path"].split("/"))
        if source.suffix.lower() != ".svg":
            skipped.append({"media_id": item["media_id"], "reason": "not-a-passive-svg-candidate"})
            continue
        output = run / "renders" / f"{item['media_id']}.png"
        output.parent.mkdir(parents=True, exist_ok=True)
        posix = Path(item["source_relative_path"]).as_posix()
        owner, round_number = None, None
        for model_id, model in (freeze.get("model_sources") or {}).items():
            for number, root in (model.get("round_roots") or {}).items():
                prefix = str(root).rstrip("/")
                if prefix and (posix == prefix or posix.startswith(prefix + "/")):
                    owner, round_number = model_id, int(number)
            final_root = str(model.get("final_root") or "").rstrip("/")
            if owner is None and final_root and (posix == final_root or posix.startswith(final_root + "/")):
                owner = model_id
                round_number = context.manifest.get("task", {}).get("round_count", 1)
            if owner is not None:
                break
        if owner is None or round_number is None:
            skipped.append({"media_id": item["media_id"], "reason": "candidate media is not bound to a frozen model root"})
            continue
        rubric_ids = [row["id"] for row in context.rubrics if row.get("round") == round_number]
        if not rubric_ids:
            skipped.append({"media_id": item["media_id"], "reason": f"no frozen rubric belongs to round {round_number}"})
            continue
        try:
            receipt = render_media(source, output, media_id=item["media_id"], role="candidate_full",
                                   bindings=[{"model_id": owner, "round": round_number, "rubric_ids": rubric_ids}])
            registered = register_render(form, output, receipt)
            rendered.append({"media_id": item["media_id"], "sha256": registered["blob_sha256"]})
        except (ValueError, OSError, FileExistsError) as exc:
            skipped.append({"media_id": item["media_id"], "reason": str(exc)})
    print(json.dumps({"rendered": rendered, "skipped": skipped}, ensure_ascii=False))
    if not rendered and not skipped:
        return EXIT_OK
    return EXIT_OK if rendered else EXIT_UNRESOLVED


def command_record_human(args: argparse.Namespace) -> int:
    run = _run_dir(args)
    state = _load_state(run)
    _require(state, "media_frozen", "observations_complete", "decisions_complete", action="record-human")
    if not interactive_stdin_available():
        raise HumanRequired("HUMAN_REQUIRED: run record-human in an interactive terminal")
    record = json.loads(Path(args.record).read_text(encoding="utf-8"))
    print("Type the human's original confirmation words, then press Enter:")
    record["confirmation_text"] = input()
    if not record["confirmation_text"].strip():
        raise HumanRequired("HUMAN_REQUIRED: an empty confirmation cannot close a human check")
    append_jsonl(run / "form-ready" / "observations/remote-human.jsonl", record)
    print(json.dumps({"appended": record.get("observation_id")}, ensure_ascii=False))
    return EXIT_UNRESOLVED


def _observations_settled(run: Path) -> bool:
    manifest = json.loads((run / "form-ready/FORM-READY.json").read_text(encoding="utf-8"))
    context = load_form_ready_base(run / "form-ready")[1]
    registry = load_observation_registry(run / "form-ready", manifest)
    for rubric_id, record in registry.classifications.items():
        needed = record.get("classification") == "subjective_or_policy"
        pairs = [(row["model_id"], row["rubric_id"]) for row in registry.vision + registry.human]
        if needed and not any(rubric == rubric_id for _model, rubric in pairs):
            unresolved = True
            return False
    for (model_id, rubric_id), row in context.evidence.items():
        if (row or {}).get("human_check_needed") and not any(
            item.get("model_id") == model_id and item.get("rubric_id") == rubric_id for item in registry.human
        ):
            return False
    return True


def command_decide(args: argparse.Namespace) -> int:
    run = _run_dir(args)
    state = _load_state(run)
    _require(state, "media_frozen", "observations_complete", action="decide")
    if not _observations_settled(run):
        raise PhaseError("OBSERVATIONS_INCOMPLETE: every subjective or human-checked pair needs an observation record")
    form = run / "form-ready"
    manifest = json.loads((form / "FORM-READY.json").read_text(encoding="utf-8"))
    context = load_form_ready_base(form)[1]
    if not (form / "decisions/final-decisions.json").is_file():
        initialize_workspace(form)
    else:
        try:
            existing = json.loads((form / "decisions/final-decisions.json").read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            existing = {}
        if not existing.get("records"):
            # initialize_form_ready writes an empty scaffold; replace it with the real slots.
            (form / "decisions/final-decisions.json").unlink()
            (form / "decisions/adjudication-audit.json").unlink(missing_ok=True)
            initialize_workspace(form)
    if args.pair:
        model_id, rubric_id = args.pair.split("/", 1)
        value = json.loads(Path(args.value_json).read_text(encoding="utf-8"))
        record_pair(form, model_id, rubric_id, value)
    elif args.adjudication or args.gap:
        value = json.loads(Path(args.value_json).read_text(encoding="utf-8"))
        record_closure(form, adjudication_id=args.adjudication, gap_id=args.gap, value=value)
    observations = load_observation_registry(form, manifest)
    outcome = validate_final_decisions(context, observations, observations, form / "decisions/final-decisions.json",
                                      audit_path=form / "decisions/adjudication-audit.json")
    if outcome["result"] != "pass":
        print(json.dumps({"result": outcome["result"], "errors": outcome["errors"],
                          "unresolved": outcome["unresolved"]}, ensure_ascii=False))
        return EXIT_UNRESOLVED
    finalize_audit(form)
    _advance(run, state, "decisions_complete", decisions=len(outcome["registry"]))
    print(json.dumps({"decisions": len(outcome["registry"])}, ensure_ascii=False))
    return EXIT_OK


def command_record_presentation(args: argparse.Namespace) -> int:
    run = _run_dir(args)
    state = _load_state(run)
    _require(state, "decisions_complete", "presentation_complete", action="record-presentation")
    form = run / "form-ready"
    if not (form / "presentation/presentation-input.json").is_file():
        initialize_presentation(form)
    record_slot(form, args.slot, json.loads(Path(args.value_json).read_text(encoding="utf-8")))
    print(json.dumps({"slot": args.slot}, ensure_ascii=False))
    return EXIT_UNRESOLVED


def command_attest_presentation(args: argparse.Namespace) -> int:
    run = _run_dir(args)
    state = _load_state(run)
    _require(state, "decisions_complete", "presentation_complete", action="attest-presentation")
    if not interactive_stdin_available():
        raise HumanRequired("HUMAN_REQUIRED: run attest-presentation in an interactive terminal")
    form = run / "form-ready"
    print("Type the human's original confirmation words, then press Enter:")
    record_attestation(form, args.slot, observer=args.observer, confirmation_text=input())
    report = validate_presentation(form)
    if not report["presentation_complete"]:
        print(json.dumps(report, ensure_ascii=False))
        return EXIT_UNRESOLVED
    _advance(run, state, "presentation_complete")
    return EXIT_OK


def command_build_outputs(args: argparse.Namespace) -> int:
    run = _run_dir(args)
    state = _load_state(run)
    _require(state, "presentation_complete", "outputs_complete", action="build-outputs")
    form = run / "form-ready"
    manifest, context, _ = load_form_ready_base(form)
    observations = load_observation_registry(form, manifest)
    outcome = validate_final_decisions(context, observations, observations, form / "decisions/final-decisions.json",
                                      audit_path=form / "decisions/adjudication-audit.json")
    if outcome["result"] != "pass":
        raise PhaseError("DECISIONS_INCOMPLETE: " + json.dumps(outcome["errors"], ensure_ascii=False))
    if not (form / "scored").is_dir():
        project_scores(context, outcome["registry"], form)
    else:
        verify_projected_scores(context, outcome["registry"], form)
    if not (form / "presentation/form-input.json").is_file():
        render_supporting_outputs(form)
    else:
        verify_supporting_outputs(form)
    _advance(run, state, "outputs_complete")
    print(json.dumps({"scored": len(outcome["registry"])}, ensure_ascii=False))
    return EXIT_OK


def command_check_source(args: argparse.Namespace) -> int:
    run = _run_dir(args)
    state = _load_state(run)
    _require(state, "outputs_complete", "source_verified", action="check-source")
    _, _context, freeze = load_form_ready_base(run / "form-ready")
    outcome = verify_frozen_source(state["task_root"], freeze)
    if outcome.get("status") != "unchanged":
        state["phase"] = TERMINAL_PHASE
        state["terminal_failure_code"] = TERMINAL_PHASE
        _atomic(_state_path(run), _dump(state))
        print(json.dumps(outcome, ensure_ascii=False))
        return EXIT_SOURCE_CHANGED
    _advance(run, state, "source_verified")
    print(json.dumps(outcome, ensure_ascii=False))
    return EXIT_OK


def command_validate(args: argparse.Namespace) -> int:
    run = _run_dir(args)
    _load_state(run)
    report = assess_form_ready(run / "form-ready")
    print(json.dumps(report, ensure_ascii=False))
    return EXIT_OK if report["derived_status"] == "ready_for_form" else EXIT_UNRESOLVED


def command_package(args: argparse.Namespace) -> int:
    run = _run_dir(args)
    state = _load_state(run)
    _require(state, "source_verified", action="package")
    result = package_form_ready(run / "form-ready", Path(args.output_zip), task_root=state["task_root"])
    _advance(run, state, "sealed", seal_zip_sha256=result["sha256"])
    print(json.dumps(result, ensure_ascii=False))
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    start = subparsers.add_parser("start")
    start.add_argument("--task-root", type=Path, required=True)
    start.add_argument("--output-root", type=Path, required=True)
    start.add_argument("--run-id", default=None)
    start.set_defaults(handler=command_start)
    for name, handler in (("status", command_status), ("validate", command_validate), ("check-source", command_check_source),
                          ("seal-base", command_seal_base), ("freeze-media", command_freeze_media),
                          ("build-outputs", command_build_outputs)):
        sub = subparsers.add_parser(name)
        sub.add_argument("--run", type=Path, required=True)
        sub.set_defaults(handler=handler)
    review = subparsers.add_parser("accept-review")
    review.add_argument("--run", type=Path, required=True)
    review.add_argument("--requirements", type=Path, required=True)
    review.add_argument("--review-items", type=Path, required=True)
    review.set_defaults(handler=command_accept_review)
    evidence = subparsers.add_parser("accept-evidence")
    evidence.add_argument("--run", type=Path, required=True)
    evidence.add_argument("--candidate", type=Path, required=True)
    evidence.set_defaults(handler=command_accept_evidence)
    for name, handler in (("record-vision", command_record_vision), ("record-human", command_record_human)):
        sub = subparsers.add_parser(name)
        sub.add_argument("--run", type=Path, required=True)
        sub.add_argument("--record", type=Path, required=True)
        sub.set_defaults(handler=handler)
    render = subparsers.add_parser("render")
    render.add_argument("--run", type=Path, required=True)
    render.add_argument("--media-id", default=None)
    render.set_defaults(handler=command_render)
    decide = subparsers.add_parser("decide")
    decide.add_argument("--run", type=Path, required=True)
    decide.add_argument("--pair", default=None)
    decide.add_argument("--adjudication", default=None)
    decide.add_argument("--gap", default=None)
    decide.add_argument("--value-json", type=Path, default=None)
    decide.set_defaults(handler=command_decide)
    presentation = subparsers.add_parser("record-presentation")
    presentation.add_argument("--run", type=Path, required=True)
    presentation.add_argument("--slot", required=True)
    presentation.add_argument("--value-json", type=Path, required=True)
    presentation.set_defaults(handler=command_record_presentation)
    attest = subparsers.add_parser("attest-presentation")
    attest.add_argument("--run", type=Path, required=True)
    attest.add_argument("--slot", required=True)
    attest.add_argument("--observer", required=True)
    attest.set_defaults(handler=command_attest_presentation)
    package = subparsers.add_parser("package")
    package.add_argument("--run", type=Path, required=True)
    package.add_argument("--output-zip", type=Path, required=True)
    package.set_defaults(handler=command_package)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "decide":
        targets = [bool(args.pair), bool(args.adjudication), bool(args.gap)]
        if sum(targets) > 1:
            print("DECIDE_TARGET_AMBIGUOUS: pass only one of --pair, --adjudication, or --gap", file=sys.stderr)
            return EXIT_INVALID
        if any(targets) and not args.value_json:
            print("DECIDE_REQUIRES_VALUE: a target also needs --value-json", file=sys.stderr)
            return EXIT_INVALID
        if args.value_json and not any(targets):
            print("DECIDE_REQUIRES_TARGET: --value-json needs --pair, --adjudication, or --gap", file=sys.stderr)
            return EXIT_INVALID
    try:
        return args.handler(args)
    except SourceChangedError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_SOURCE_CHANGED
    except HumanRequired as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_HUMAN_REQUIRED
    except (ReviewRejected, EvidenceRejected) as exc:
        print(json.dumps(exc.errors, ensure_ascii=False), file=sys.stderr)
        return EXIT_INVALID
    except PhaseError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_INVALID
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_INVALID


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["PHASES", "TERMINAL_PHASE", "PhaseError", "HumanRequired", "build_parser", "main"]
