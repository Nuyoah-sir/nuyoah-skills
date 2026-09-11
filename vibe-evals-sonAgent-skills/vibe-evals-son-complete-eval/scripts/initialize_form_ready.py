#!/usr/bin/env python3
"""Initialize a v2 form-ready workspace around an immutable sealed v1 ZIP."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from artifact_integrity import safe_extract_zip, sha256_file, verify_sidecar
from form_ready_context import load_base_context
from validate_bundle import validate_bundle
from validate_form_ready import REQUIRED_PATHS


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _validate_runner_state(state: dict[str, Any], digest: str, context: Any) -> str:
    if not isinstance(state, dict) or state.get("phase") != "base_sealed":
        raise ValueError("RUN_STATE_INVALID: normal runner must be at base_sealed")
    run_id = state.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        raise ValueError("RUN_STATE_INVALID: run_id is required")
    expected = {
        "base_package_id": context.package_id,
        "base_zip_sha256": digest,
        "source_input_digest": context.source_input_digest,
    }
    for key, value in expected.items():
        if state.get(key) != value:
            raise ValueError(f"RUN_STATE_INVALID: {key} does not match sealed base")
    receipts = state.get("completed_phase_receipts")
    if not isinstance(receipts, list) or not any(
        isinstance(row, dict)
        and row.get("phase") == "base_sealed"
        and row.get("base_zip_sha256") == digest
        for row in receipts
    ):
        raise ValueError("RUN_STATE_INVALID: valid base_sealed receipt is required")
    return run_id


def initialize_form_ready(
    base_zip: str | Path,
    base_sidecar: str | Path,
    output_dir: str | Path,
    *,
    expected_sha256: str | None = None,
    run_state: dict | None = None,
) -> Path:
    """Verify a sealed v1 bundle and atomically publish an incomplete v2 workspace."""

    archive = Path(base_zip)
    sidecar = Path(base_sidecar)
    output = Path(output_dir)
    if output.exists() or output.is_symlink():
        raise FileExistsError(f"Output already exists: {output}")

    digest = verify_sidecar(archive, sidecar, expected_sha256)
    with tempfile.TemporaryDirectory(prefix="vibe-form-ready-inspect-") as temporary:
        extracted = safe_extract_zip(archive, Path(temporary) / "bundle")
        report = validate_bundle(extracted, require_seal=True)
        if report.get("result") != "pass":
            raise ValueError("BASE_VALIDATION_FAILED: inner evidence bundle is not strictly sealed")
        context = load_base_context(extracted)

    run_id = _validate_runner_state(run_state, digest, context) if run_state is not None else str(uuid.uuid4())
    outer_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = output.parent / f".{output.name}.{uuid.uuid4().hex}.tmp"
    try:
        staging.mkdir()
        nested = staging / "base" / "evidence-bundle.zip"
        nested.parent.mkdir()
        with archive.open("rb") as source, nested.open("xb") as target:
            shutil.copyfileobj(source, target, length=1024 * 1024)
        if sha256_file(nested) != digest or sha256_file(archive) != digest:
            raise ValueError("BASE_SOURCE_CHANGED: archive changed during initialization")
        (nested.with_suffix(".zip.sha256")).write_text(
            f"{digest}  evidence-bundle.zip\n", encoding="utf-8"
        )
        manifest = {
            "schema": "vibe-evals-form-ready-bundle",
            "schema_version": "2.0.0",
            "outer_package_id": outer_id,
            "base": {
                "archive_path": "base/evidence-bundle.zip",
                "sha256_path": "base/evidence-bundle.zip.sha256",
                "sha256": digest,
                "original_name": archive.name,
                "package_id": context.package_id,
                "source_input_digest": context.source_input_digest,
            },
            "paths": dict(REQUIRED_PATHS),
            "package_status": "incomplete",
        }
        _write_json(staging / "FORM-READY.json", manifest)
        _write_json(staging / REQUIRED_PATHS["media_index"], {
            "schema_version": "2.0.0", "outer_package_id": outer_id, "blobs": {}, "uses": {}
        })
        for key in ("machine_vision", "remote_human", "presentation_attestations"):
            path = staging / REQUIRED_PATHS[key]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("", encoding="utf-8")
        for key in ("final_decisions", "criterion_classifications", "adjudication_audit"):
            _write_json(staging / REQUIRED_PATHS[key], {"schema_version": "2.0.0", "records": []})
        _write_json(staging / REQUIRED_PATHS["run_state"], {
            "schema_version": "2.0.0", "run_id": run_id, "outer_package_id": outer_id,
            "base_package_id": context.package_id, "base_zip_sha256": digest,
            "source_input_digest": context.source_input_digest, "phase": "base_verified",
            "completed_phase_receipts": list(run_state.get("completed_phase_receipts", [])) if run_state else [],
            "created_at": run_state.get("created_at", now) if run_state else now,
            "updated_at": now, "terminal_failure_code": None,
        })
        if output.exists() or output.is_symlink():
            raise FileExistsError(f"Output already exists: {output}")
        os.replace(staging, output)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return output


__all__ = ["initialize_form_ready"]
