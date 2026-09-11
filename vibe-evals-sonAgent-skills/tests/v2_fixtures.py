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
from tests.test_validate_bundle import identity_digest, inventory_digest, make_bundle

FIXTURE_PNG = (b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" +
               (2).to_bytes(4, "big") + (3).to_bytes(4, "big") +
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


def make_form_ready_workspace(root: Path, *, complete: bool, mutate_bundle=None) -> FormReadyFixture:
    """Build a real sealed v1 package nested byte-for-byte in an unsealed v2 tree."""

    root = Path(root)
    inner_source = make_bundle(root / "inner-work")
    task_root = root / "task-source"
    task_root.mkdir(parents=True)
    (task_root / "target.png").write_bytes(FIXTURE_PNG)
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
    return FormReadyFixture(nested_archive, nested_archive.with_suffix(".zip.sha256"), outer, inner_manifest["package_id"], inner_manifest["source_input_digest"], task_root, freeze)


def rewrite_manifest(outer: Path, *, schema_version: str, decisions_path: str) -> None:
    path = Path(outer) / "FORM-READY.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["schema_version"] = schema_version
    manifest["paths"]["final_decisions"] = decisions_path
    write_json(path, manifest)
