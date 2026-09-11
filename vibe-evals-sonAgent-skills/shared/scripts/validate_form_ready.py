#!/usr/bin/env python3
"""Validate the immutable-base shell of a form-ready bundle v2 workspace."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from artifact_integrity import _walk_safe_files, safe_extract_zip, verify_sidecar
from form_ready_context import BaseContext, load_base_context, load_observation_registry
from presentation_workspace import validate_presentation
from project_form_ready_scores import verify_projected_scores
from render_supporting_outputs import verify_supporting_outputs
from validate_final_decisions import GAP_POLICY_JSON, load_gap_policy, validate_final_decisions, validate_observation_provenance
from validate_bundle import validate_bundle

SCHEMA = "vibe-evals-form-ready-bundle"
SCHEMA_VERSION = "2.0.0"
PACKAGE_STATUSES = frozenset({"incomplete", "ready_for_form"})
REQUIRED_MANIFEST_FIELDS = frozenset({"schema", "schema_version", "outer_package_id", "base", "paths", "package_status"})
REQUIRED_BASE_FIELDS = frozenset({"archive_path", "sha256_path", "sha256", "original_name", "package_id", "source_input_digest"})
REQUIRED_PATHS = {
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
ALLOWED_MANIFEST_FIELDS = REQUIRED_MANIFEST_FIELDS
ALLOWED_BASE_FIELDS = REQUIRED_BASE_FIELDS
_SHA256 = re.compile(r"[0-9a-f]{64}")
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}")
_DRIVE = re.compile(r"^[A-Za-z]:")
_SHARED_ROOT = Path(__file__).resolve().parents[1]


def _gap_policy() -> dict:
    """Load the executable gap policy, falling back to the deny-by-default default."""

    try:
        return load_gap_policy(_SHARED_ROOT / GAP_POLICY_JSON)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return load_gap_policy(None)


def issue(code: str, message: str, path: str = "FORM-READY.json") -> dict[str, str]:
    return {"code": code, "message": message, "path": path}


def _safe_relative(value: Any) -> bool:
    if not isinstance(value, str) or not value or "\x00" in value:
        return False
    normalized = value.replace("\\", "/")
    if normalized.startswith(("/", "//")) or _DRIVE.match(normalized):
        return False
    parts = normalized.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        return False
    return PurePosixPath(*parts).as_posix() == normalized


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _add_identity_error(errors: list[dict[str, str]], path: str, detail: str) -> None:
    errors.append(issue("IDENTITY_MISMATCH", detail, path))


def _check_envelope(
    value: Any,
    *,
    errors: list[dict[str, str]],
    path: str,
    manifest: dict[str, Any],
    context: BaseContext,
    rubric_by_id: dict[str, dict[str, Any]],
    require_model_rubric: bool,
) -> None:
    if not isinstance(value, dict):
        return
    expected = {
        "outer_package_id": manifest.get("outer_package_id"),
        "base_package_id": context.package_id,
        "base_zip_sha256": manifest.get("base", {}).get("sha256"),
        "source_input_digest": context.source_input_digest,
        "task_id": context.task_id,
    }
    required = set(expected)
    if require_model_rubric:
        required.update({"model_id", "rubric_id", "round", "criterion_sha256"})
    missing = sorted(key for key in required if key not in value)
    if missing:
        _add_identity_error(errors, path, f"Identity envelope is missing fields: {missing}")
        return
    mismatched = sorted(key for key, expected_value in expected.items() if value.get(key) != expected_value)
    model_id = value.get("model_id")
    rubric_id = value.get("rubric_id")
    if model_id is not None and model_id not in context.models:
        mismatched.append("model_id")
    rubric = rubric_by_id.get(rubric_id) if rubric_id is not None else None
    if rubric_id is not None and rubric is None:
        mismatched.append("rubric_id")
    elif rubric is not None:
        if value.get("round") != rubric.get("round"):
            mismatched.append("round")
        if value.get("criterion_sha256") != rubric.get("criterion_sha256"):
            mismatched.append("criterion_sha256")
    if mismatched:
        _add_identity_error(errors, path, f"Identity fields do not match the immutable base: {sorted(set(mismatched))}")


def _check_present_record_identities(root: Path, manifest: dict[str, Any], context: BaseContext, errors: list[dict[str, str]]) -> None:
    """Check identity only; later validator stages own record semantics."""

    rubric_by_id = {row.get("id"): row for row in context.rubrics}
    paths = manifest.get("paths", {})
    media_relative = paths.get("media_index")
    if _safe_relative(media_relative):
        media_path = root.joinpath(*PurePosixPath(media_relative).parts)
        if media_path.is_file():
            try:
                media = _read_json(media_path)
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                errors.append(issue("RECORD_INVALID", f"Cannot parse media index: {exc}", media_relative))
                media = None
            if media is not None and not isinstance(media, dict):
                errors.append(issue("RECORD_INVALID", "Media index root must be an object", media_relative))
            elif isinstance(media, dict):
                if media.get("outer_package_id") != manifest.get("outer_package_id"):
                    _add_identity_error(errors, media_relative, "Media index outer_package_id does not match")
                uses = media.get("uses")
                if not isinstance(uses, dict):
                    errors.append(issue("RECORD_INVALID", "Media index uses must be an object", f"{media_relative}#/uses"))
                else:
                    for media_id, use in uses.items():
                        if not isinstance(use, dict):
                            errors.append(issue("RECORD_INVALID", "Media use must be an object", f"{media_relative}#/uses/{media_id}"))
                            continue
                        bindings = use.get("bindings")
                        if not isinstance(bindings, list) or not bindings:
                            errors.append(issue("RECORD_INVALID", "Media use requires a nonempty bindings array", f"{media_relative}#/uses/{media_id}"))
                            continue
                        for index, binding in enumerate(bindings):
                            if not isinstance(binding, dict):
                                errors.append(issue("RECORD_INVALID", "Media binding must be an object", f"{media_relative}#/uses/{media_id}/bindings/{index}"))
                                continue
                            _check_envelope(binding, errors=errors, path=f"{media_relative}#/uses/{media_id}/bindings/{index}", manifest=manifest, context=context, rubric_by_id=rubric_by_id, require_model_rubric=False)
    json_record_keys = ("final_decisions", "criterion_classifications", "adjudication_audit", "form_input")
    for key in json_record_keys:
        relative = paths.get(key)
        if not _safe_relative(relative):
            continue
        file_path = root.joinpath(*PurePosixPath(relative).parts)
        if not file_path.is_file():
            continue
        try:
            document = _read_json(file_path)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            errors.append(issue("RECORD_INVALID", f"Cannot parse record document: {exc}", relative))
            continue
        if not isinstance(document, dict):
            errors.append(issue("RECORD_INVALID", "Record document root must be an object", relative))
            continue
        if key == "form_input":
            _check_envelope(document, errors=errors, path=relative, manifest=manifest, context=context, rubric_by_id=rubric_by_id, require_model_rubric=False)
        records = document.get("records")
        if not isinstance(records, list):
            errors.append(issue("RECORD_INVALID", "Record document records must be an array", f"{relative}#/records"))
            continue
        if key in {"form_input", "adjudication_audit"}:
            # These documents carry the package envelope once at the document level.
            # Their records hold per-model payloads and multi-pair closures, which the
            # decision and presentation stages cross-check against the sealed base.
            continue
        for index, record in enumerate(records):
            if not isinstance(record, dict):
                errors.append(issue("RECORD_INVALID", "Each record must be an object", f"{relative}#/records/{index}"))
                continue
            _check_envelope(
                record,
                errors=errors,
                path=f"{relative}#/records/{index}",
                manifest=manifest,
                context=context,
                rubric_by_id=rubric_by_id,
                require_model_rubric=key == "final_decisions",
            )
    for key in ("machine_vision", "remote_human"):
        relative = paths.get(key)
        if not _safe_relative(relative):
            continue
        file_path = root.joinpath(*PurePosixPath(relative).parts)
        if not file_path.is_file():
            continue
        try:
            lines = file_path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError) as exc:
            errors.append(issue("RECORD_INVALID", f"Cannot read JSONL record file: {exc}", relative))
            continue
        for line_number, line in enumerate(lines, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                errors.append(issue("RECORD_INVALID", f"Cannot parse JSONL record: {exc}", f"{relative}:{line_number}"))
                continue
            if not isinstance(record, dict):
                errors.append(issue("RECORD_INVALID", "Each JSONL record must be an object", f"{relative}:{line_number}"))
                continue
            _check_envelope(record, errors=errors, path=f"{relative}:{line_number}", manifest=manifest, context=context, rubric_by_id=rubric_by_id, require_model_rubric=True)


def _stage_media_coverage(root_path: Path, manifest: dict[str, Any], errors: list[dict[str, str]], counts: dict[str, int]) -> None:
    """Stage 5: exact media registry coverage and byte agreement."""

    from register_media import inspect_image

    relative = manifest.get("paths", {}).get("media_index")
    if not _safe_relative(relative):
        return
    path = root_path.joinpath(*PurePosixPath(relative).parts)
    if not path.is_file():
        errors.append(issue("MEDIA_INDEX_MISSING", "Media index is missing", relative))
        return
    try:
        index = _read_json(path)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        errors.append(issue("RECORD_INVALID", f"Cannot parse media index: {exc}", relative))
        return
    blobs, uses = index.get("blobs"), index.get("uses")
    if not isinstance(blobs, dict) or not isinstance(uses, dict):
        errors.append(issue("RECORD_INVALID", "Media index needs blobs and uses objects", relative))
        return
    counts["unique_blobs"], counts["media_uses"] = len(blobs), len(uses)
    indexed: set[str] = set()
    for digest, blob in sorted(blobs.items()):
        if _SHA256.fullmatch(str(digest)) is None or not isinstance(blob, dict):
            errors.append(issue("MEDIA_BLOB_INVALID", f"Invalid blob entry {digest!r}", f"{relative}#/blobs/{digest}"))
            continue
        blob_relative = blob.get("path")
        if not _safe_relative(blob_relative):
            errors.append(issue("UNSAFE_PATH", "Blob path must be a safe package-relative path", f"{relative}#/blobs/{digest}"))
            continue
        target = root_path.joinpath(*PurePosixPath(blob_relative).parts)
        if not target.is_file():
            errors.append(issue("MEDIA_BLOB_MISSING", "Indexed blob file is missing", blob_relative))
            continue
        try:
            info = inspect_image(target)
        except (OSError, ValueError) as exc:
            errors.append(issue("MEDIA_BLOB_MISMATCH", f"Blob bytes are not a supported image: {exc}", blob_relative))
            continue
        if (info["sha256"] != digest or info["size"] != blob.get("size") or info["mime"] != blob.get("mime")
                or (info["width"], info["height"]) != (blob.get("width"), blob.get("height"))):
            errors.append(issue("MEDIA_BLOB_MISMATCH", "Indexed blob metadata disagrees with the frozen bytes", blob_relative))
        indexed.add(blob_relative)
    actual = {
        file.relative_to(root_path).as_posix()
        for base in (root_path / "observations/source-media", root_path / "observations/renders")
        if base.exists() for file in _walk_safe_files(base)
    }
    if actual != indexed:
        errors.append(issue("MEDIA_COVERAGE_MISMATCH",
                            f"Index/blob coverage differs: unindexed={sorted(actual - indexed)} missing={sorted(indexed - actual)}",
                            relative))
    for media_id, use in sorted(uses.items()):
        if not isinstance(use, dict):
            errors.append(issue("MEDIA_USE_INVALID", "Media use must be an object", f"{relative}#/uses/{media_id}"))
            continue
        if use.get("status", "registered") != "registered":
            counts["unresolved_media"] += 1
            errors.append(issue("MEDIA_UNRESOLVED_USE",
                                "A planned review medium was never registered",
                                f"{relative}#/uses/{media_id}"))
            continue
        if use.get("blob_sha256") not in blobs:
            errors.append(issue("MEDIA_USE_INVALID", "A registered media use must reference an indexed blob", f"{relative}#/uses/{media_id}"))


def _stage_score_replay(root_path: Path, context: BaseContext | None, registry: dict, errors: list[dict[str, str]], counts: dict[str, int]) -> None:
    """Stage 8: portable scored artifacts must replay byte-identically."""

    if context is None or not registry:
        return
    if not (root_path / "scored").is_dir():
        errors.append(issue("SCORED_OUTPUT_MISSING", "Portable scored output is missing", "scored"))
        return
    try:
        summary = verify_projected_scores(context, registry, root_path)
    except (OSError, UnicodeError, ValueError, KeyError, FileNotFoundError, FileExistsError) as exc:
        errors.append(issue("SCORED_REPLAY_DRIFT", f"Scored artifacts are not reproducible from the closed decisions: {exc}", "scored"))
        return
    counts["scored_files"] = len(summary.get("files", {}))


def _stage_presentation_crosscheck(
    root_path: Path,
    manifest: dict[str, Any],
    context: BaseContext | None,
    errors: list[dict[str, str]],
    counts: dict[str, int],
) -> None:
    """Stage 9: presentation slots, supporting outputs, and form-input binding."""

    presentation = validate_presentation(root_path, _SHARED_ROOT)
    slot_document_path = root_path / "presentation/presentation-input.json"
    if slot_document_path.is_file():
        try:
            counts["presentation_slots"] = len(_read_json(slot_document_path).get("slots", {}))
        except (OSError, UnicodeError, json.JSONDecodeError):
            pass
    if not presentation["presentation_complete"]:
        for row in presentation["errors"][:5]:
            errors.append(issue("PRESENTATION_INCOMPLETE", row["message"], row["path"]))
        return
    try:
        verify_supporting_outputs(root_path, _SHARED_ROOT)
    except (OSError, UnicodeError, ValueError, KeyError, FileNotFoundError, FileExistsError) as exc:
        errors.append(issue("SUPPORTING_OUTPUT_DRIFT", f"Supporting outputs are not reproducible: {exc}", "presentation"))
    relative = manifest.get("paths", {}).get("form_input")
    if not _safe_relative(relative) or context is None:
        return
    form_path = root_path.joinpath(*PurePosixPath(relative).parts)
    if not form_path.is_file():
        errors.append(issue("FORM_INPUT_MISSING", "Form input is missing", relative))
        return
    try:
        form_input = _read_json(form_path)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        errors.append(issue("RECORD_INVALID", f"Cannot parse form input: {exc}", relative))
        return
    digests = {
        "presentation_input_sha256": root_path / "presentation/presentation-input.json",
        "final_decisions_sha256": root_path / "decisions/final-decisions.json",
    }
    for key, target in digests.items():
        if not target.is_file() or form_input.get(key) != hashlib.sha256(target.read_bytes()).hexdigest():
            errors.append(issue("FORM_INPUT_BINDING_MISMATCH", f"{key} does not match the packaged file", relative))
    scored_digests = form_input.get("scored_sha256")
    if not isinstance(scored_digests, dict):
        errors.append(issue("FORM_INPUT_BINDING_MISMATCH", "scored_sha256 must map every model to a digest", relative))
    else:
        expected_models = sorted(context.models)
        if sorted(scored_digests) != expected_models:
            errors.append(issue("FORM_INPUT_BINDING_MISMATCH", "scored_sha256 must cover exactly the package models", relative))
        for model_id in expected_models:
            target = root_path / "scored" / f"rubrics-{model_id}.json"
            if not target.is_file() or scored_digests.get(model_id) != hashlib.sha256(target.read_bytes()).hexdigest():
                errors.append(issue("FORM_INPUT_BINDING_MISMATCH", f"scored_sha256 does not match scored/rubrics-{model_id}.json", relative))
    counts["form_records"] = len(form_input.get("records", []) or [])
    seen_models: set[Any] = set()
    for index, record in enumerate(form_input.get("records", []) or []):
        if not isinstance(record, dict):
            errors.append(issue("FORM_INPUT_RECORD_INVALID", "Each form input record must be an object", f"{relative}#/records/{index}"))
            continue
        model_id = record.get("model_id")
        if model_id is None:
            continue
        if model_id not in context.models or model_id in seen_models:
            errors.append(issue("FORM_INPUT_BINDING_MISMATCH", f"Form input record {model_id!r} does not name exactly one package model", f"{relative}#/records/{index}"))
        seen_models.add(model_id)
    if seen_models != set(context.models):
        errors.append(issue("FORM_INPUT_BINDING_MISMATCH", "Form input must carry exactly one record per package model", relative))


def assess_form_ready(root: str | Path, scratch_root: str | Path | None = None) -> dict[str, Any]:
    """Assess the v2 base chain without requiring an outer seal or declared status."""

    root_path = Path(root)
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    unresolved_counts = {"scores": 0, "adjudications": 0, "human_checks": 0, "material_gaps": 0}
    counts: dict[str, int] = {"models": 0, "rubrics": 0, "media_uses": 0, "unique_blobs": 0, "unresolved_media": 0,
                              "vision_records": 0, "human_records": 0, "final_decisions": 0,
                              "closed_inner_adjudication_pairs": 0, "closed_inner_human_check_pairs": 0,
                              "optional_gap_closures": 0, "semantic_gap_closures": 0, "scored_files": 0,
                              "presentation_slots": 0, "form_records": 0}
    base_counts: dict[str, Any] = {}
    context: BaseContext | None = None
    decision_registry: dict = {}
    manifest: dict[str, Any] = {}
    try:
        _walk_safe_files(root_path)
    except (OSError, ValueError) as exc:
        errors.append(issue("ARTIFACT_TREE_UNSAFE", str(exc), "."))
        return {"validator_version": SCHEMA_VERSION, "result": "fail", "derived_status": "incomplete", "errors": errors, "warnings": warnings}
    try:
        value = _read_json(root_path / "FORM-READY.json")
        if not isinstance(value, dict):
            raise ValueError("root value is not an object")
        manifest = value
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        errors.append(issue("MANIFEST_INVALID", f"Cannot read FORM-READY.json: {exc}"))
    if manifest:
        missing = sorted(REQUIRED_MANIFEST_FIELDS - set(manifest))
        if missing:
            errors.append(issue("MANIFEST_FIELDS_MISSING", f"Missing manifest fields: {missing}"))
        extra = sorted(set(manifest) - ALLOWED_MANIFEST_FIELDS)
        if extra:
            errors.append(issue("MANIFEST_SHAPE_INVALID", f"Unexpected manifest fields: {extra}"))
        if manifest.get("schema") != SCHEMA or manifest.get("schema_version") != SCHEMA_VERSION:
            errors.append(issue("SCHEMA_UNSUPPORTED", f"Expected {SCHEMA}/{SCHEMA_VERSION}"))
        if _UUID.fullmatch(str(manifest.get("outer_package_id", ""))) is None:
            errors.append(issue("OUTER_PACKAGE_ID_INVALID", "outer_package_id must be a canonical lowercase UUID"))
        if manifest.get("package_status") not in PACKAGE_STATUSES:
            errors.append(issue("PACKAGE_STATUS_INVALID", f"package_status must be one of {sorted(PACKAGE_STATUSES)}"))
        base = manifest.get("base")
        if not isinstance(base, dict):
            errors.append(issue("BASE_INVALID", "base must be an object"))
            base = {}
        missing_base = sorted(REQUIRED_BASE_FIELDS - set(base))
        if missing_base:
            errors.append(issue("BASE_FIELDS_MISSING", f"Missing base fields: {missing_base}"))
        extra_base = sorted(set(base) - ALLOWED_BASE_FIELDS)
        if extra_base:
            errors.append(issue("MANIFEST_SHAPE_INVALID", f"Unexpected base fields: {extra_base}", "FORM-READY.json#/base"))
        paths = manifest.get("paths")
        if not isinstance(paths, dict):
            errors.append(issue("PATHS_INVALID", "paths must be an object"))
            paths = {}
        extra_paths = sorted(set(paths) - set(REQUIRED_PATHS))
        if extra_paths:
            errors.append(issue("MANIFEST_SHAPE_INVALID", f"Unexpected path fields: {extra_paths}", "FORM-READY.json#/paths"))
        for key, expected in REQUIRED_PATHS.items():
            actual = paths.get(key)
            if not _safe_relative(actual):
                errors.append(issue("UNSAFE_PATH", f"paths.{key} is not a safe POSIX package-relative path", f"FORM-READY.json#/paths/{key}"))
            elif actual != expected:
                errors.append(issue("PATH_MISMATCH", f"paths.{key} must be {expected!r}", f"FORM-READY.json#/paths/{key}"))
        for key, expected in (("archive_path", "base/evidence-bundle.zip"), ("sha256_path", "base/evidence-bundle.zip.sha256")):
            actual = base.get(key)
            if not _safe_relative(actual):
                errors.append(issue("UNSAFE_PATH", f"base.{key} is not a safe POSIX package-relative path", f"FORM-READY.json#/base/{key}"))
            elif actual != expected:
                errors.append(issue("BASE_PATH_MISMATCH", f"base.{key} must be {expected!r}", f"FORM-READY.json#/base/{key}"))
        if _SHA256.fullmatch(str(base.get("sha256", ""))) is None:
            errors.append(issue("BASE_SHA256_INVALID", "base.sha256 must be 64 lowercase hexadecimal characters", "FORM-READY.json#/base/sha256"))
        if _SHA256.fullmatch(str(base.get("source_input_digest", ""))) is None:
            errors.append(issue("SOURCE_DIGEST_INVALID", "base.source_input_digest must be 64 lowercase hexadecimal characters", "FORM-READY.json#/base/source_input_digest"))
        if not isinstance(base.get("package_id"), str) or not base.get("package_id"):
            errors.append(issue("BASE_PACKAGE_ID_INVALID", "base.package_id must be a nonempty string", "FORM-READY.json#/base/package_id"))
        original_name = base.get("original_name")
        if not _safe_relative(original_name) or "/" in str(original_name).replace("\\", "/"):
            errors.append(issue("UNSAFE_PATH", "base.original_name must be a single safe filename", "FORM-READY.json#/base/original_name"))

        archive_relative = base.get("archive_path")
        sidecar_relative = base.get("sha256_path")
        can_open_base = (
            manifest.get("schema") == SCHEMA
            and manifest.get("schema_version") == SCHEMA_VERSION
            and archive_relative == "base/evidence-bundle.zip"
            and sidecar_relative == "base/evidence-bundle.zip.sha256"
            and _SHA256.fullmatch(str(base.get("sha256", ""))) is not None
        )
        if can_open_base:
            archive = root_path.joinpath(*PurePosixPath(archive_relative).parts)
            sidecar = root_path.joinpath(*PurePosixPath(sidecar_relative).parts)
            try:
                with tempfile.TemporaryDirectory(prefix="vibe-form-ready-base-") as temporary:
                    staged_archive = Path(temporary) / "evidence-bundle.zip"
                    with archive.open("rb") as source, staged_archive.open("xb") as target:
                        shutil.copyfileobj(source, target, length=1024 * 1024)
                    verified_digest = verify_sidecar(staged_archive, sidecar, base.get("sha256"))
                    extracted = safe_extract_zip(staged_archive, Path(temporary) / "bundle")
                    inner_report = validate_bundle(extracted, require_seal=True)
                    if inner_report.get("result") != "pass":
                        errors.append(issue("BASE_VALIDATION_FAILED", "Inner evidence bundle failed strict sealed validation", archive_relative))
                    else:
                        context = load_base_context(extracted)
                        base_counts = dict(inner_report.get("counts", {}) or {})
                        mismatches = []
                        if base.get("package_id") != context.package_id:
                            mismatches.append("package_id")
                        if base.get("source_input_digest") != context.source_input_digest:
                            mismatches.append("source_input_digest")
                        if base.get("sha256") != verified_digest:
                            mismatches.append("sha256")
                        if mismatches:
                            _add_identity_error(errors, "FORM-READY.json#/base", f"Base identity mismatch: {mismatches}")
                        _check_present_record_identities(root_path, manifest, context, errors)
                        try:
                            observations = validate_observation_provenance(root_path, manifest, context, errors)
                            decisions_path = root_path.joinpath(*PurePosixPath(paths.get("final_decisions")).parts) if _safe_relative(paths.get("final_decisions")) else None
                            if decisions_path is not None:
                                audit_path = root_path.joinpath(*PurePosixPath(paths.get("adjudication_audit")).parts) if _safe_relative(paths.get("adjudication_audit")) else None
                                outcome = validate_final_decisions(
                                    context,
                                    observations,
                                    observations,
                                    decisions_path,
                                    audit_path=audit_path,
                                    gap_policy=_gap_policy(),
                                )
                                errors.extend(outcome["errors"])
                                unresolved_counts.update(outcome["unresolved"])
                                decision_registry = outcome["registry"]
                        except (OSError, UnicodeError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
                            errors.append(issue("RECORD_VALIDATION_FAILED", str(exc), "observations"))
                        try:
                            counts["models"] = len(context.models)
                            counts["rubrics"] = len(context.rubrics)
                            counts["vision_records"] = len(load_observation_registry(root_path, manifest).vision)
                            counts["human_records"] = len(load_observation_registry(root_path, manifest).human)
                            counts["final_decisions"] = len(decision_registry)
                            for (model_id, rubric_id), row in context.evidence.items():
                                decision = decision_registry.get((model_id, rubric_id)) or {}
                                if (row or {}).get("human_check_needed") is True and decision.get("decided_by") == "remote_human":
                                    counts["closed_inner_human_check_pairs"] += 1
                                if (row or {}).get("adjudication_ids") and decision:
                                    counts["closed_inner_adjudication_pairs"] += 1
                            _stage_media_coverage(root_path, manifest, errors, counts)
                            _stage_score_replay(root_path, context, decision_registry, errors, counts)
                            _stage_presentation_crosscheck(root_path, manifest, context, errors, counts)
                        except (OSError, UnicodeError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
                            errors.append(issue("RECORD_VALIDATION_FAILED", str(exc), "stages"))
            except (OSError, UnicodeError, ValueError, KeyError, TypeError, json.JSONDecodeError, zipfile.BadZipFile) as exc:
                errors.append(issue("BASE_INTEGRITY_FAILED", str(exc), str(archive_relative)))
    counts["unresolved_scores"] = unresolved_counts["scores"]
    counts["unresolved_adjudications"] = unresolved_counts["adjudications"]
    counts["unresolved_human_checks"] = unresolved_counts["human_checks"]
    counts["unresolved_material_gaps"] = unresolved_counts["material_gaps"]
    sealed = not errors and not any(unresolved_counts.values())
    derived = "ready_for_form" if sealed else "incomplete"
    return {
        "validator_version": SCHEMA_VERSION,
        "result": "pass" if not errors else "fail",
        "derived_status": derived,
        "counts": dict(counts),
        "base_counts": dict(base_counts),
        "unresolved": dict(unresolved_counts),
        "errors": errors,
        "warnings": warnings,
    }


def validate_form_ready(root: str | Path, require_seal: bool = False, scratch_root: str | Path | None = None) -> dict[str, Any]:
    """Validate the deep assessment and the declared status; outer sealing arrives in Task 8."""

    report = assess_form_ready(root, scratch_root=scratch_root)
    try:
        manifest = _read_json(Path(root) / "FORM-READY.json")
    except (OSError, UnicodeError, json.JSONDecodeError):
        manifest = {}
    if manifest.get("package_status") != report["derived_status"]:
        report["errors"].append(issue("STATUS_MISMATCH", f"Manifest={manifest.get('package_status')!r}, derived={report['derived_status']!r}"))
    if require_seal:
        report["errors"].append(issue("SEAL_VALIDATION_UNAVAILABLE", "Outer seal validation is introduced by the packaging stage"))
    report["result"] = "pass" if not report["errors"] else "fail"
    if report["errors"]:
        report["derived_status"] = "incomplete"
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--require-seal", action="store_true")
    args = parser.parse_args(argv)
    report = validate_form_ready(args.root, require_seal=args.require_seal)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["result"] == "pass" else 3


if __name__ == "__main__":
    sys.exit(main())


__all__ = [
    "PACKAGE_STATUSES",
    "REQUIRED_BASE_FIELDS",
    "REQUIRED_MANIFEST_FIELDS",
    "REQUIRED_PATHS",
    "SCHEMA",
    "SCHEMA_VERSION",
    "assess_form_ready",
    "validate_form_ready",
]
