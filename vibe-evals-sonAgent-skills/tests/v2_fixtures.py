"""Complete fictional fixtures for the form-ready bundle v2 tests."""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "shared" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from artifact_integrity import safe_extract_zip, sha256_file
from package_bundle import package_bundle
from decision_workspace import finalize_audit, initialize_workspace, record_closure, record_pair
from form_ready_context import load_base_context
from presentation_workspace import initialize_presentation, record_attestation, record_slot
from project_form_ready_scores import project_scores
from register_media import register_source_media
from render_supporting_outputs import render_supporting_outputs
from validate_final_decisions import validate_final_decisions
from form_ready_context import load_observation_registry
from tests.test_validate_bundle import identity_digest, inventory_digest, make_bundle

FIXTURE_PNG = (b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" +
               (2).to_bytes(4, "big") + (3).to_bytes(4, "big") +
               b"\x08\x06\x00\x00\x00" + b"\x00\x00\x00\x00")
FIXTURE_RENDER_PNG = (b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" +
                      (4).to_bytes(4, "big") + (6).to_bytes(4, "big") +
                      b"\x08\x06\x00\x00\x00" + b"\x00\x00\x00\x00")


FORM_READY_PATHS = {
    "media_index": "observations/media-index.json",
    "machine_vision": "observations/machine-vision.jsonl",
    "remote_human": "observations/remote-human.jsonl",
    "final_decisions": "decisions/final-decisions.json",
    "criterion_classifications": "decisions/criterion-classifications.json",
    "adjudication_audit": "decisions/adjudication-audit.json",
    "source_verification": "integrity/source-verification.json",
    "run_state": "provenance/run-state.json",
    "scored_dir": "scored",
    "presentation_input": "presentation/presentation-input.json",
    "presentation_attestations": "presentation/presentation-attestations.jsonl",
    "report_input": "presentation/report-input.json",
    "report": "presentation/反馈报告.md",
    "heatmap": "presentation/rubrics打分热力图.html",
    "form_input": "presentation/form-input.json",
}


@dataclass(frozen=True)
class FormReadyFixture:
    sealed_inner_zip: Path
    sealed_inner_sidecar: Path
    outer: Path
    inner_package_id: str
    source_digest: str
    task_root: Path
    authoritative_source_freeze: dict


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _criterion_digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def make_form_ready_workspace(root: Path, *, complete: bool, mutate_bundle=None, closed: bool = False) -> FormReadyFixture:
    complete = complete or closed
    """Build a real sealed v1 package nested byte-for-byte in an unsealed v2 tree."""

    root = Path(root)
    inner_source = make_bundle(root / "inner-work")
    task_root = root / "task-source"
    task_root.mkdir(parents=True)
    freeze_path = inner_source / "source-freeze.json"
    freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
    media_row = {"path": "target.png", "size": len(FIXTURE_PNG), "sha256": hashlib.sha256(FIXTURE_PNG).hexdigest()}
    freeze["source_inventory"].append(media_row)
    freeze["source_inventory_digest"] = inventory_digest(freeze["source_inventory"])
    freeze["input_digest"] = identity_digest(freeze["source_inventory_digest"], freeze["prompt_source"], freeze["rubric_sources"], freeze["model_sources"], freeze.get("record_source"))
    write_json(freeze_path, freeze)
    inner_manifest_path = inner_source / "MANIFEST.json"
    inner_manifest = json.loads(inner_manifest_path.read_text(encoding="utf-8"))
    inner_manifest["source_input_digest"] = freeze["input_digest"]
    write_json(inner_manifest_path, inner_manifest)
    build_task_root(task_root, inner_source, freeze)
    if mutate_bundle is not None:
        mutate_bundle(inner_source)
    source_archive = root / "source-name.zip"
    package_bundle(inner_source, source_archive)
    inspected = safe_extract_zip(source_archive, root / "inspected-inner")
    inner_manifest = json.loads((inspected / "MANIFEST.json").read_text(encoding="utf-8"))

    outer = root / "form-ready"
    base_dir = outer / "base"
    base_dir.mkdir(parents=True)
    nested_archive = base_dir / "evidence-bundle.zip"
    shutil.copyfile(source_archive, nested_archive)
    nested_digest = sha256_file(nested_archive)
    (base_dir / "evidence-bundle.zip.sha256").write_text(
        f"{nested_digest}  {nested_archive.name}\n", encoding="utf-8"
    )
    outer_id = str(uuid.uuid4())
    write_json(
        outer / "FORM-READY.json",
        {
            "schema": "vibe-evals-form-ready-bundle",
            "schema_version": "2.0.0",
            "outer_package_id": outer_id,
            "base": {
                "archive_path": "base/evidence-bundle.zip",
                "sha256_path": "base/evidence-bundle.zip.sha256",
                "sha256": nested_digest,
                "original_name": source_archive.name,
                "package_id": inner_manifest["package_id"],
                "source_input_digest": inner_manifest["source_input_digest"],
            },
            "paths": dict(FORM_READY_PATHS),
            "package_status": "ready_for_form" if complete else "incomplete",
        },
    )
    criterion = "按钮可点击"
    envelope = {
        "outer_package_id": outer_id,
        "base_package_id": inner_manifest["package_id"],
        "base_zip_sha256": nested_digest,
        "source_input_digest": inner_manifest["source_input_digest"],
        "task_id": inner_manifest["task"]["name"],
        "model_id": "model-a",
        "rubric_id": "R1-01",
        "round": 1,
        "criterion_sha256": _criterion_digest(criterion),
    }
    write_json(outer / FORM_READY_PATHS["media_index"], {"schema_version": "2.0.0", "outer_package_id": outer_id, "blobs": {}, "uses": {}})
    (outer / FORM_READY_PATHS["machine_vision"]).parent.mkdir(parents=True, exist_ok=True)
    (outer / FORM_READY_PATHS["machine_vision"]).write_text("", encoding="utf-8")
    (outer / FORM_READY_PATHS["remote_human"]).write_text("", encoding="utf-8")
    write_json(outer / FORM_READY_PATHS["final_decisions"], {"schema_version": "2.0.0", "records": [{**envelope, "score": 1, "decided_by": "mechanical", "reason": "Fictional fixture.", "evidence_ids": ["EV-model-a-R1-01-001"], "decider": "fixture", "decided_at": "2026-09-10T10:00:00+08:00"}]})
    write_json(outer / FORM_READY_PATHS["criterion_classifications"], {"schema_version": "2.0.0", "records": [{key: envelope[key] for key in ("outer_package_id", "base_package_id", "base_zip_sha256", "source_input_digest", "task_id", "rubric_id", "round", "criterion_sha256") } | {"classification": "presence", "measurable_phrase": "按钮可点击", "reason": "Fictional fixture.", "requires_remote_human": False}]})
    write_json(outer / FORM_READY_PATHS["adjudication_audit"], {"schema_version": "2.0.0", "records": []})
    write_json(outer / FORM_READY_PATHS["source_verification"], {"schema_version": "2.0.0", "outer_package_id": outer_id, "base_package_id": inner_manifest["package_id"], "base_zip_sha256": nested_digest, "source_input_digest": inner_manifest["source_input_digest"], "task_id": inner_manifest["task"]["name"], "checked_at": "2026-09-10T10:00:00+08:00", "result": "unchanged"})
    (outer / "provenance").mkdir(exist_ok=True)
    write_json(outer / FORM_READY_PATHS["run_state"], {"schema_version": "2.0.0", "run_id": str(uuid.uuid4()), "outer_package_id": outer_id, "base_package_id": inner_manifest["package_id"], "base_zip_sha256": nested_digest, "source_input_digest": inner_manifest["source_input_digest"], "phase": "base_verified", "completed_phase_receipts": [], "created_at": "2026-09-10T10:00:00+08:00", "updated_at": "2026-09-10T10:00:00+08:00", "terminal_failure_code": None})
    if closed:
        close_form_ready_package(outer, task_root, freeze, inspected)
    return FormReadyFixture(nested_archive, nested_archive.with_suffix(".zip.sha256"), outer, inner_manifest["package_id"], inner_manifest["source_input_digest"], task_root, freeze)


def _identity_binding(outer: Path, task_id: str) -> dict:
    manifest = json.loads((outer / "FORM-READY.json").read_text(encoding="utf-8"))
    return {
        "outer_package_id": manifest["outer_package_id"],
        "base_package_id": manifest["base"]["package_id"],
        "base_zip_sha256": manifest["base"]["sha256"],
        "source_input_digest": manifest["base"]["source_input_digest"],
        "task_id": task_id,
    }


def build_task_root(task_root: Path, bundle: Path, freeze: dict) -> None:
    """Recreate a task root that matches the frozen inventory byte for byte."""

    for row in freeze["source_inventory"]:
        relative = row["path"]
        if row["sha256"] == hashlib.sha256(FIXTURE_PNG).hexdigest():
            data = FIXTURE_PNG
        elif relative.endswith("prompt.md"):
            data = (bundle / "inputs" / "prompt.md").read_bytes()
        elif relative.endswith(".json") and (bundle / "inputs" / "rubrics" / Path(relative).name).is_file():
            data = (bundle / "inputs" / "rubrics" / Path(relative).name).read_bytes()
        else:
            blob = bundle / "evidence" / "source-blobs" / f"{row['sha256']}.txt"
            if not blob.is_file():
                raise FileNotFoundError(f"Fixture cannot reconstruct frozen source {relative!r}")
            data = blob.read_bytes()
        if hashlib.sha256(data).hexdigest() != row["sha256"]:
            raise ValueError(f"Fixture reconstruction for {relative!r} does not match the freeze")
        target = task_root.joinpath(*relative.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


def _register_candidate_render(outer: Path, binding: dict) -> str:
    """Register the frozen candidate render as a real, hash-verified blob."""

    path = outer / "observations/media-index.json"
    index = json.loads(path.read_text(encoding="utf-8"))
    digest = hashlib.sha256(FIXTURE_RENDER_PNG).hexdigest()
    relative = f"observations/renders/{digest}.png"
    target = outer.joinpath(*relative.split("/"))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(FIXTURE_RENDER_PNG)
    index["blobs"][digest] = {"mime": "image/png", "size": len(FIXTURE_RENDER_PNG), "width": 4, "height": 6, "path": relative}
    index["uses"]["RENDER-candidate"] = {
        "blob_sha256": digest, "role": "candidate_full", "acquisition_method": "render_media",
        "bindings": [binding], "status": "registered",
    }
    write_json(path, index)
    return digest


def close_form_ready_package(outer: Path, task_root: Path, freeze: dict, inspected: Path) -> None:
    """Close a base-verified workspace into a complete v2 package using the real modules."""

    manifest = json.loads((outer / "FORM-READY.json").read_text(encoding="utf-8"))
    task_id = json.loads((inspected / "MANIFEST.json").read_text(encoding="utf-8"))["task"]["name"]
    binding = _identity_binding(outer, task_id)
    for name in ("decisions/final-decisions.json", "decisions/adjudication-audit.json"):
        (outer / name).unlink(missing_ok=True)
    register_source_media(outer, task_root, freeze, {
        "media_id": "MEDIA-target", "role": "target", "source_relative_path": "target.png",
        "bindings": [binding],
    })
    render_digest = _register_candidate_render(outer, binding)
    criterion_path = outer / "decisions/criterion-classifications.json"
    classification = json.loads(criterion_path.read_text(encoding="utf-8"))
    adjudicated_rubrics = {
        rubric_id for (_model_id, rubric_id), row in load_base_context(inspected).evidence.items()
        if (row or {}).get("adjudication_ids")
    }
    for record in classification["records"]:
        if record.get("rubric_id") in adjudicated_rubrics:
            record.update({"classification": "subjective_or_policy", "reason": "Fixture policy ambiguity.",
                           "requires_remote_human": True})
            record.pop("measurable_phrase", None)
        else:
            record.update({"classification": "presence", "measurable_phrase": "按钮可点击",
                           "reason": "Fixture classification.", "requires_remote_human": False})
    write_json(criterion_path, classification)

    indexed = json.loads(criterion_path.read_text(encoding="utf-8"))["records"][0]
    vision = []
    for read_index in ((1, 2) if not adjudicated_rubrics else ()):
        question = f"Read {read_index}: is the button present in the render? "
        raw = f"Answer {read_index}: the button is present."
        vision.append({
            "observation_id": f"VIS-model-a-R1-01-0{read_index}", "type": "machine_vision",
            "outer_package_id": manifest["outer_package_id"], "base_package_id": manifest["base"]["package_id"],
            "base_zip_sha256": manifest["base"]["sha256"], "source_input_digest": manifest["base"]["source_input_digest"],
            "task_id": task_id, "model_id": "model-a", "rubric_id": "R1-01", "round": 1,
            "criterion_sha256": indexed["criterion_sha256"], "read_index": read_index,
            "invocation_id": f"provider-request-{read_index}", "session_id": f"isolated-read-{read_index}",
            "independent_context": True, "input_media_ids": ["MEDIA-target", "RENDER-candidate"],
            "tool": "vision-bridge", "model": "fixture-vision", "tool_version": "1.0.0",
            "question": question, "prompt_sha256": hashlib.sha256(question.encode("utf-8")).hexdigest(),
            "raw_response": raw, "raw_response_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
            "fact": f"button present in the render (read {read_index})", "verdict": 1,
            "confidence": "high", "conflict": False, "observed_at": "2026-09-10T10:00:00+08:00",
        })
    (outer / "observations/machine-vision.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in vision), encoding="utf-8")

    base_ctx = load_base_context(inspected)
    human_pairs = [
        (model_id, rubric_id) for (model_id, rubric_id), row in sorted(base_ctx.evidence.items())
        if (row or {}).get("human_check_needed") is True or (row or {}).get("adjudication_ids")
    ]
    human_rows = []
    for model_id, rubric_id in human_pairs:
        human_rows.append({
            "observation_id": f"HUM-{model_id}-{rubric_id}", "type": "remote_human",
            "outer_package_id": manifest["outer_package_id"], "base_package_id": manifest["base"]["package_id"],
            "base_zip_sha256": manifest["base"]["sha256"], "source_input_digest": manifest["base"]["source_input_digest"],
            "task_id": task_id, "model_id": model_id, "rubric_id": rubric_id, "round": 1,
            "criterion_sha256": indexed["criterion_sha256"], "observer": "fixture-operator",
            "recorded_by": "fixture-agent", "capture_method": "interactive_terminal",
            "observed_at": "2026-09-10T10:00:00+08:00",
            "steps": ["open the verified full render", "compare it with the frozen target"],
            "expected_result": "the criterion holds", "observed_result": "the criterion holds in the render",
            "observed_version": f"{manifest['base']['package_id']} / RENDER-candidate",
            "confirmation_text": "fixture human confirmation of the render",
            "media_ids": ["MEDIA-target", "RENDER-candidate"], "verdict": 1,
        })
    if human_rows:
        (outer / "observations/remote-human.jsonl").write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in human_rows), encoding="utf-8")

    initialize_workspace(outer)
    decisions: dict[tuple[str, str], int] = {}
    for (model_id, rubric_id), row in sorted(base_ctx.evidence.items()):
        suggested = (row or {}).get("suggested_score")
        score = suggested if suggested in (0, 1) and not isinstance(suggested, bool) else 1
        if (model_id, rubric_id) in human_pairs:
            record_pair(outer, model_id, rubric_id, {
                "score": score, "decided_by": "remote_human", "reason": "远程人工按渲染与目标比对后判定。",
                "evidence_ids": [f"HUM-{model_id}-{rubric_id}"], "decider": "fixture-operator",
                "decided_at": "2026-09-10T10:00:00+08:00",
            })
        else:
            record_pair(outer, model_id, rubric_id, {
                "score": score, "decided_by": "mechanical", "reason": "静态证据支持。",
                "evidence_ids": [f"EV-{model_id}-{rubric_id}-001"], "decider": "fixture-agent",
                "decided_at": "2026-09-10T10:00:00+08:00",
            })
        decisions[(model_id, rubric_id)] = score
    for adjudication in base_ctx.pending_adjudications:
        affected = [[m, r] for (m, r) in sorted(decisions) if adjudication.get("adjudication_id") in ((base_ctx.evidence.get((m, r)) or {}).get("adjudication_ids") or [])]
        record_closure(outer, adjudication_id=adjudication.get("adjudication_id"), value={
            "final_policy": "按真实渲染行为判定。", "decided_by": "remote_human", "decider": "fixture-operator",
            "decided_at": "2026-09-10T10:00:00+08:00", "affected_pairs": affected,
            "per_model_final_scores": {model_id: {rubric_id: decisions[(model_id, rubric_id)]}
                                       for model_id, rubric_id in (tuple(pair) for pair in affected)},
        })
    for gap_id in base_ctx.material_gaps:
        record_closure(outer, gap_id=gap_id, value={
            "decided_by": "remote_human", "decider": "fixture-operator",
            "decided_at": "2026-09-10T10:00:00+08:00",
            "reason": "Fixture gap closure.", "limitation": "Fixture limitation statement.",
        })
    finalize_audit(outer)
    observations = load_observation_registry(outer, manifest)
    outcome = validate_final_decisions(
        base_ctx, observations, observations, outer / "decisions/final-decisions.json",
        audit_path=outer / "decisions/adjudication-audit.json",
    )
    if outcome["result"] != "pass":
        raise ValueError("Fixture closure is not valid: " + json.dumps(outcome["errors"], ensure_ascii=False))
    project_scores(base_ctx, outcome["registry"], outer)

    initialize_presentation(outer)
    evidence = ["EV-model-a-R1-01-001"]
    for name in ("ranking_reason", "maximum_difference", "capability_boundary", "difficulty_and_approach"):
        record_slot(outer, f"task.{name}", {"value": f"fixture insight for {name}", "basis": "Fixture basis.",
            "evidence_ids": evidence, "recorder": "fixture-agent", "recorded_at": "2026-09-10T10:00:00+08:00"})
    for name in ("G1", "G2", "G3", "S1", "A1", "R1"):
        value = {"applicable": False, "basis": "不适用【不适用：fixture】"} if name in {"S1", "A1", "R1"} else {"applicable": True, "score": 3.0, "basis": "Fixture basis."}
        record_slot(outer, f"model.model-a.{name}", {"value": value, "basis": "Fixture basis.",
            "evidence_ids": evidence, "recorder": "fixture-agent", "recorded_at": "2026-09-10T10:00:00+08:00"})
    for name, value in (("overall_impression", 3.0), ("pros", ["fixture pro one", "fixture pro two"]),
                        ("cons", ["fixture con one"]), ("style", "fixture style sentence")):
        record_slot(outer, f"model.model-a.{name}", {"value": value, "basis": "Fixture basis.",
            "evidence_ids": evidence, "recorder": "fixture-agent", "recorded_at": "2026-09-10T10:00:00+08:00"})
    legal = json.loads((SCRIPTS.parent / "references/v21-labels.json").read_text(encoding="utf-8"))["labels"]
    for slot, section in (("labels.pros", "Pros"), ("labels.cons", "Cons"), ("labels.style", "Stylistic Fingerprints")):
        tags = sorted(tag for group in legal[section].values() for tag in group)[:1]
        record_slot(outer, f"model.model-a.{slot}", {"value": tags, "basis": "Fixture basis.",
            "evidence_ids": evidence, "recorder": "fixture-agent", "recorded_at": "2026-09-10T10:00:00+08:00"})
    for name in ("overall_impression", "G3", "style", "labels.pros", "labels.cons", "labels.style"):
        record_attestation(outer, f"model.model-a.{name}", observer="fixture-operator",
                           confirmation_text="fixture human confirmation")
    render_supporting_outputs(outer)


def rewrite_manifest(outer: Path, *, schema_version: str, decisions_path: str) -> None:
    path = Path(outer) / "FORM-READY.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["schema_version"] = schema_version
    manifest["paths"]["final_decisions"] = decisions_path
    write_json(path, manifest)
