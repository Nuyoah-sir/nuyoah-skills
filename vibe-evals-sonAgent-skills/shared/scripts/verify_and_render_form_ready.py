#!/usr/bin/env python3
"""One-call local consumption of a sealed form-ready bundle.

The local machine only verifies and renders: it hands the archive, the sidecar,
and an output directory to this module and gets back the V2.1 form, the verified
report, the heatmap, and a verification receipt. It performs no adjudication and
never imports the legacy local review helpers.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from artifact_integrity import detect_artifact_kind, safe_extract_zip, sha256_file, verify_sidecar
from form_ready_context import load_base_context
from render_v21_form import build_form_from_context
from validate_form_ready import SCHEMA_VERSION, validate_form_ready

RECEIPT_NAME = "verification-receipt.json"
REPORT_NAME = "\u53cd\u9988\u62a5\u544a.md"
HEATMAP_NAME = "rubrics\u6253\u5206\u70ed\u529b\u56fe.html"
RUBRIC_DIMENSIONS = ("G1", "G2", "G3", "S1", "A1", "R1")
FORBIDDEN_LOCAL_FILES = frozenset({
    "\u4eba\u5de5\u88c1\u5b9a\u6e05\u5355.md",
    "evidence_requests.json",
    "human-decisions.json",
    "local-human-evidence.jsonl",
})
LABEL_LIBRARY_NAME = "V2.1\u6807\u7b7e\u5e93.md"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _digest(path: Path) -> str:
    return sha256_file(path)


def _label_library(explicit: str | Path | None) -> Path:
    if explicit is not None:
        path = Path(explicit)
        if not path.is_file():
            raise ValueError(f"Label library not found: {path}")
        return path
    here = Path(__file__).resolve().parent
    candidates = [
        here.parent / "templates" / LABEL_LIBRARY_NAME,
        here.parent / "references" / LABEL_LIBRARY_NAME,
        here.parent.parent / "templates" / LABEL_LIBRARY_NAME,
        here.parent.parent / "references" / LABEL_LIBRARY_NAME,
        here.parent.parent / "vibe-evals-bundle-finalize" / "templates" / LABEL_LIBRARY_NAME,
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise ValueError("The V2.1 label library could not be located")


def _form_data_from_package(root: Path) -> dict[str, Any]:
    """Turn the validated form input into the shape the shared renderer consumes."""

    form_input = json.loads((root / "presentation/form-input.json").read_text(encoding="utf-8"))
    records = form_input.get("records") or []
    task_record = next((row for row in records if isinstance(row, dict) and row.get("model_id") is None), None)
    if not isinstance(task_record, dict) or not isinstance(task_record.get("evidence_refs"), dict):
        raise ValueError("Form input must carry one task record with evidence refs")
    models = {}
    for row in records:
        if not isinstance(row, dict) or row.get("model_id") is None:
            continue
        # A v2 presentation slot carries one evidence list for the whole pros/cons
        # block, so each claim inherits that slot-level list. The receipt records
        # this granularity instead of pretending the evidence is per claim.
        refs = row.get("evidence_refs") or {}
        pros = row.get("pros") or []
        cons = row.get("cons") or []
        models[row["model_id"]] = {
            "overall_impression": row.get("overall_impression"),
            "dimensions": row.get("dimensions"),
            "pros": row.get("pros"),
            "cons": row.get("cons"),
            "style": row.get("style"),
            "labels": row.get("labels"),
            "evidence_refs": {
                "overall": list(refs.get("overall") or []),
                "dimensions": refs.get("dimensions") or {},
                "pros": [list(refs.get("pros") or []) for _ in pros],
                "cons": [list(refs.get("cons") or []) for _ in cons],
                "style": list(refs.get("style") or []),
            },
        }
    return {
        "task": {**task_record["task"], "evidence_refs": task_record["evidence_refs"]},
        "models": models,
    }


def _scored_totals(outer_root: Path, base_root: Path, manifest: dict) -> tuple[list[str], dict[str, int]]:
    """Rubric ids come from the sealed base package; scores come from the outer projection."""

    baseline: list[dict[str, Any]] = []
    for entry in sorted(manifest.get("inputs", {}).get("rubrics", []), key=lambda item: item.get("round", 0)):
        baseline.extend(json.loads((base_root / entry["path"]).read_text(encoding="utf-8")))
    rubric_ids = [row["id"] for row in baseline]
    totals = {}
    for model in manifest.get("models", []):
        model_id = model["model_id"]
        rows = json.loads((outer_root / "scored" / f"rubrics-{model_id}.json").read_text(encoding="utf-8"))
        if [row.get("id") for row in rows] != rubric_ids:
            raise ValueError(f"Scored rubrics for {model_id} do not match the frozen rubric set")
        totals[model_id] = sum(int(row["score"]) for row in rows)
    return rubric_ids, totals


def _evidence_meta(root: Path, manifest: dict) -> dict[str, dict[str, dict]]:
    meta: dict[str, dict[str, dict]] = {}
    for model in manifest.get("models", []):
        model_id = model["model_id"]
        path = root / model["directory"] / "rubric-evidence.jsonl"
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        meta[model_id] = {
            evidence.get("evidence_id"): {"rubric_id": row.get("rubric_id"), "direction": evidence.get("direction")}
            for row in rows for evidence in row.get("evidence", []) if evidence.get("evidence_id")
        }
    return meta


def _observation_bindings(root: Path) -> dict[str, dict[str, dict]]:
    """Record the outer VIS/HUM ids so locally rendered claims stay auditable."""

    bindings: dict[str, dict[str, dict]] = {}
    for name in ("machine-vision.jsonl", "remote-human.jsonl"):
        path = root / "observations" / name
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            model_id, observation_id = row.get("model_id"), row.get("observation_id")
            if isinstance(model_id, str) and isinstance(observation_id, str):
                bindings.setdefault(model_id, {})[observation_id] = {
                    "rubric_id": row.get("rubric_id"),
                    "direction": "support" if row.get("verdict") == 1 else "refute",
                }
    return bindings


def verify_and_render(
    archive: str | Path,
    sidecar: str | Path,
    output_dir: str | Path,
    *,
    expected_sha256: str | None = None,
    label_library: str | Path | None = None,
) -> dict[str, Any]:
    """Verify a sealed form-ready ZIP and publish the locally rendered form."""

    archive_path = Path(archive).resolve()
    sidecar_path = Path(sidecar).resolve()
    output = Path(output_dir).resolve()
    if output.exists():
        raise FileExistsError(f"Output directory already exists: {output}")
    library = _label_library(label_library)
    outer_sha = sha256_file(archive_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="vibe-verify-render-") as temporary:
        workspace = Path(temporary)
        staged = workspace / archive_path.name
        shutil.copyfile(archive_path, staged)
        verified = verify_sidecar(staged, sidecar_path, expected_sha256)
        kind, _version = detect_artifact_kind(staged)
        if kind != "form-ready":
            raise ValueError(f"Not a form-ready artifact: detected {kind!r}")
        outer_root = safe_extract_zip(staged, workspace / "outer")
        outer_report = validate_form_ready(outer_root, require_seal=True)
        if outer_report["result"] != "pass":
            raise ValueError("Form-ready bundle failed strict validation: " + json.dumps(outer_report["errors"], ensure_ascii=False))
        manifest = json.loads((outer_root / "FORM-READY.json").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory(prefix="vibe-verify-base-") as base_temporary:
            base_root = safe_extract_zip(outer_root / "base/evidence-bundle.zip", Path(base_temporary) / "base")
            context = load_base_context(base_root)
            rubric_ids, totals = _scored_totals(outer_root, base_root, context.manifest)
            meta = _evidence_meta(base_root, context.manifest)
            for model_id, extra in _observation_bindings(outer_root).items():
                meta.setdefault(model_id, {}).update(extra)
            form_data = _form_data_from_package(outer_root)
            model_map = {row["model_id"]: row for row in context.manifest["models"]}
            markdown, render_info = build_form_from_context(
                context.manifest, form_data, model_map, rubric_ids, totals, meta, library,
            )

        staged_dir = workspace / "publish"
        staged_dir.mkdir()
        form_path = staged_dir / "V2.1\u8bc4\u5206\u8868\u5355.md"
        form_path.write_text(markdown, encoding="utf-8")
        shutil.copyfile(outer_root / "presentation" / REPORT_NAME, staged_dir / REPORT_NAME)
        shutil.copyfile(outer_root / "presentation" / HEATMAP_NAME, staged_dir / HEATMAP_NAME)
        receipt = {
            "schema_version": SCHEMA_VERSION,
            "outer_package_id": manifest["outer_package_id"],
            "base_package_id": context.package_id,
            "base_zip_sha256": manifest["base"]["sha256"],
            "source_input_digest": context.source_input_digest,
            "task_id": context.task_id,
            "archive_sha256": verified,
            "expected_sha256": expected_sha256,
            "outer_validator_version": outer_report["validator_version"],
            "outer_result": outer_report["result"],
            "inner_validator_version": "1.0.0",
            "inner_result": "pass",
            "counts": outer_report["counts"],
            "base_counts": outer_report["base_counts"],
            "unresolved": outer_report["unresolved"],
            "form_sha256": _digest(form_path),
            "report_sha256": _digest(staged_dir / REPORT_NAME),
            "heatmap_sha256": _digest(staged_dir / HEATMAP_NAME),
            "render": render_info,
            "evidence_granularity": {"pros_cons": "slot-level", "task_and_style": "slot-level"},
            "rendered_at": _now(),
        }
        (staged_dir / RECEIPT_NAME).write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        for name in FORBIDDEN_LOCAL_FILES & {path.name for path in staged_dir.iterdir()}:
            raise ValueError(f"Local verification must not produce {name}")
        os.replace(staged_dir, output)
    return {
        "output_dir": str(output),
        "form": str(output / "V2.1\u8bc4\u5206\u8868\u5355.md"),
        "receipt": str(output / RECEIPT_NAME),
        "archive_sha256": outer_sha,
        "verified_sha256": verified,
        "counts": receipt["counts"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("sidecar", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--expected-sha256", default=None)
    parser.add_argument("--label-library", type=Path, default=None)
    args = parser.parse_args(argv)
    try:
        result = verify_and_render(args.archive, args.sidecar, args.output_dir,
                                   expected_sha256=args.expected_sha256, label_library=args.label_library)
    except (ValueError, FileExistsError, OSError, json.JSONDecodeError, KeyError) as exc:
        print(str(exc), file=sys.stderr)
        return 3
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["FORBIDDEN_LOCAL_FILES", "RECEIPT_NAME", "verify_and_render"]
