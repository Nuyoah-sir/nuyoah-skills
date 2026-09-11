#!/usr/bin/env python3
"""Register immutable source and renderer-owned review media."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import struct
import tempfile
import uuid
from pathlib import Path
from typing import Any

from artifact_integrity import _safe_posix_path, _walk_safe_files, sha256_file, verify_sidecar
from artifact_integrity import safe_extract_zip
from form_ready_context import load_base_context
from validate_bundle import validate_bundle

MAX_IMAGE_BYTES = 64 * 1024 * 1024
MAX_DIMENSION = 16_384
MAX_PIXELS = 64_000_000
_SAFETY_CLAIMS = {
    "model_code_treated_as_untrusted", "isolated", "network_disabled",
    "credentials_absent", "disposable_copy", "minimal_permissions",
}


def _atomic_json(path: Path, value: Any) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _png_dimensions(data: bytes) -> tuple[int, int]:
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        raise ValueError("MEDIA_FORMAT_INVALID: invalid PNG signature or IHDR")
    return struct.unpack(">II", data[16:24])


def _jpeg_dimensions(data: bytes) -> tuple[int, int]:
    if not data.startswith(b"\xff\xd8"):
        raise ValueError("MEDIA_FORMAT_INVALID: invalid JPEG signature")
    index = 2
    while index + 9 < len(data):
        if data[index] != 0xFF:
            index += 1; continue
        marker = data[index + 1]; index += 2
        if marker in {0xD8, 0xD9}:
            continue
        if index + 2 > len(data): break
        length = int.from_bytes(data[index:index + 2], "big")
        if marker in set(range(0xC0, 0xC4)) | set(range(0xC5, 0xC8)) | set(range(0xC9, 0xCC)) | set(range(0xCD, 0xD0)):
            return int.from_bytes(data[index + 5:index + 7], "big"), int.from_bytes(data[index + 3:index + 5], "big")
        index += length
    raise ValueError("MEDIA_FORMAT_INVALID: JPEG dimensions missing")


def inspect_image(path: str | Path) -> dict[str, Any]:
    file_path = Path(path)
    size = file_path.stat().st_size
    if size <= 0 or size > MAX_IMAGE_BYTES:
        raise ValueError("IMAGE_LIMIT_EXCEEDED: media byte size is outside bounds")
    data = file_path.read_bytes()
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        mime, suffix, dimensions = "image/png", ".png", _png_dimensions(data)
    elif data.startswith(b"\xff\xd8"):
        mime, suffix, dimensions = "image/jpeg", ".jpg", _jpeg_dimensions(data)
    elif data.lstrip().startswith(b"<svg") or data.lstrip().startswith(b"<?xml"):
        from render_media import inspect_passive_svg
        svg = inspect_passive_svg(data)
        mime, suffix, dimensions = "image/svg+xml", ".svg", (svg["width"], svg["height"])
    else:
        raise ValueError("MEDIA_FORMAT_INVALID: unsupported image bytes")
    width, height = dimensions
    if width <= 0 or height <= 0 or width > MAX_DIMENSION or height > MAX_DIMENSION or width * height > MAX_PIXELS:
        raise ValueError("IMAGE_LIMIT_EXCEEDED: image dimensions or pixel count exceed bounds")
    return {"mime": mime, "canonical_suffix": suffix, "size": size, "width": width, "height": height, "sha256": hashlib.sha256(data).hexdigest()}


def _load_index(root: Path) -> tuple[Path, dict[str, Any]]:
    path = root / "observations" / "media-index.json"
    index = json.loads(path.read_text(encoding="utf-8"))
    blob_paths = {row["path"] for row in index.get("blobs", {}).values()}
    actual = {
        file.relative_to(root).as_posix() for base in (root / "observations/source-media", root / "observations/renders")
        if base.exists() for file in _walk_safe_files(base)
    }
    if blob_paths != actual:
        raise ValueError(f"MEDIA_INDEX_COVERAGE_MISMATCH: indexed={sorted(blob_paths)} actual={sorted(actual)}")
    return path, index


def _base_identity(root: Path) -> tuple[dict[str, Any], Any]:
    manifest = json.loads((root / "FORM-READY.json").read_text(encoding="utf-8"))
    archive = root / "base/evidence-bundle.zip"
    sidecar = root / "base/evidence-bundle.zip.sha256"
    verify_sidecar(archive, sidecar, manifest.get("base", {}).get("sha256"))
    with tempfile.TemporaryDirectory(prefix="vibe-media-base-") as temporary:
        extracted = safe_extract_zip(archive, Path(temporary) / "bundle")
        report = validate_bundle(extracted, require_seal=True)
        if report.get("result") != "pass":
            raise ValueError("BASE_VALIDATION_FAILED")
        context = load_base_context(extracted)
    return manifest, context


def _normalize_bindings(root: Path, bindings: Any, *, render_scopes: bool) -> list[dict[str, Any]]:
    manifest, context = _base_identity(root)
    if not isinstance(bindings, list) or not bindings:
        raise ValueError("MEDIA_BINDINGS_INVALID")
    rubric_by_id = {row["id"]: row for row in context.rubrics}
    base = {
        "outer_package_id": manifest["outer_package_id"], "base_package_id": context.package_id,
        "base_zip_sha256": manifest["base"]["sha256"], "source_input_digest": context.source_input_digest,
        "task_id": context.task_id,
    }
    normalized: list[dict[str, Any]] = []
    if render_scopes:
        for scope in bindings:
            if not isinstance(scope, dict) or scope.get("model_id") not in context.models:
                raise ValueError("MEDIA_BINDINGS_INVALID")
            round_value, rubric_ids = scope.get("round"), scope.get("rubric_ids")
            if not isinstance(round_value, int) or isinstance(round_value, bool) or not isinstance(rubric_ids, list) or not rubric_ids:
                raise ValueError("MEDIA_BINDINGS_INVALID")
            for rubric_id in rubric_ids:
                rubric = rubric_by_id.get(rubric_id)
                if rubric is None or rubric.get("round") != round_value:
                    raise ValueError("MEDIA_BINDINGS_INVALID")
                normalized.append(base | {"model_id": scope["model_id"], "rubric_id": rubric_id, "round": round_value, "criterion_sha256": rubric["criterion_sha256"]})
    else:
        for binding in bindings:
            if not isinstance(binding, dict) or any(binding.get(key) != value for key, value in base.items()):
                raise ValueError("MEDIA_BINDINGS_INVALID")
            normalized.append(dict(binding))
    return normalized


def _register(root: Path, source: Path, media_id: str, role: str, acquisition: str, bindings: list, *, receipt: dict | None = None) -> dict:
    if not isinstance(media_id, str) or not media_id:
        raise ValueError("MEDIA_ID_INVALID")
    try:
        if "/" in _safe_posix_path(media_id):
            raise ValueError
    except (TypeError, ValueError) as exc:
        raise ValueError("MEDIA_ID_INVALID") from exc
    if role not in {"target", "feedback", "candidate_full", "candidate_crop"}:
        raise ValueError("MEDIA_ROLE_INVALID")
    path, index = _load_index(root)
    if media_id in index["uses"]:
        raise ValueError(f"DUPLICATE_MEDIA_ID: {media_id}")
    info = inspect_image(source)
    expected_suffix = ".png" if acquisition == "render" else info["canonical_suffix"]
    if source.suffix.lower() not in ({".jpg", ".jpeg"} if info["mime"] == "image/jpeg" else {expected_suffix}):
        raise ValueError("MIME_EXTENSION_MISMATCH")
    directory = "renders" if acquisition == "render" else "source-media"
    relative = f"observations/{directory}/{info['sha256']}{expected_suffix}"
    destination = root.joinpath(*relative.split("/"))
    destination.parent.mkdir(parents=True, exist_ok=True)
    created = False
    if destination.exists():
        if sha256_file(destination) != info["sha256"]:
            raise ValueError("MEDIA_BLOB_COLLISION")
    else:
        temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
        try:
            with source.open("rb") as input_handle, temporary.open("xb") as output_handle:
                shutil.copyfileobj(input_handle, output_handle, length=1024 * 1024)
            if sha256_file(source) != info["sha256"] or sha256_file(temporary) != info["sha256"]:
                raise ValueError("MEDIA_SOURCE_CHANGED: media changed while being frozen")
            os.replace(temporary, destination)
            created = True
        finally:
            temporary.unlink(missing_ok=True)
    index["blobs"].setdefault(info["sha256"], {key: info[key] for key in ("mime", "size", "width", "height")} | {"path": relative})
    use = {"blob_sha256": info["sha256"], "role": role, "acquisition_method": acquisition, "bindings": bindings}
    if receipt:
        if receipt.get("parent_media_id") is not None: use["parent_media_id"] = receipt["parent_media_id"]
        if receipt.get("crop") is not None: use["crop"] = receipt["crop"]
    index["uses"][media_id] = use
    try:
        _atomic_json(path, index)
    except BaseException:
        if created:
            destination.unlink(missing_ok=True)
        raise
    return {"media_id": media_id, **use}


def register_source_media(form_root: str | Path, task_root: str | Path, source_freeze: dict, plan_item: dict) -> dict:
    root, task = Path(form_root), Path(task_root)
    _walk_safe_files(task)
    manifest = json.loads((root / "FORM-READY.json").read_text(encoding="utf-8"))
    if source_freeze.get("input_digest") != manifest.get("base", {}).get("source_input_digest"):
        raise ValueError("SOURCE_FREEZE_IDENTITY_MISMATCH")
    inventory = source_freeze.get("source_inventory")
    if not isinstance(inventory, list):
        raise ValueError("SOURCE_FREEZE_INVALID")
    try:
        canonical = sorted(inventory, key=lambda item: item["path"])
        calculated = hashlib.sha256("\n".join(f"{item['sha256']}  {item['path']}" for item in canonical).encode("utf-8")).hexdigest()
    except (KeyError, TypeError) as exc:
        raise ValueError("SOURCE_FREEZE_INVALID") from exc
    if calculated != source_freeze.get("source_inventory_digest"):
        raise ValueError("SOURCE_FREEZE_INVENTORY_MISMATCH")
    relative = _safe_posix_path(plan_item.get("source_relative_path"))
    if plan_item.get("role") not in {"target", "feedback"}:
        raise ValueError("SOURCE_MEDIA_ROLE_INVALID")
    rows = [row for row in inventory if row.get("path") == relative]
    if len(rows) != 1:
        raise ValueError("SOURCE_MEDIA_UNINDEXED: source path is not uniquely frozen")
    source = task.joinpath(*relative.split("/"))
    try:
        source.resolve(strict=True).relative_to(task.resolve(strict=True))
    except (OSError, ValueError) as exc:
        raise ValueError("SOURCE_ROOT_ESCAPE") from exc
    row = rows[0]
    if not source.is_file() or source.stat().st_size != row.get("size") or sha256_file(source) != row.get("sha256"):
        raise ValueError("SOURCE_MEDIA_MISMATCH: source bytes differ from freeze")
    bindings = _normalize_bindings(root, plan_item.get("bindings"), render_scopes=False)
    return _register(root, source, plan_item.get("media_id"), plan_item.get("role"), "source_freeze", bindings)


def register_render(form_root: str | Path, render_path: str | Path, receipt: str | Path) -> dict:
    if not isinstance(receipt, (str, Path)):
        raise ValueError("RENDERER_OWNED_RECEIPT_REQUIRED")
    receipt_path = Path(receipt)
    receipt_sidecar = receipt_path.with_suffix(receipt_path.suffix + ".sha256")
    verify_sidecar(receipt_path, receipt_sidecar)
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if receipt.get("receipt_source") != "render_media":
        raise ValueError("RENDERER_OWNED_RECEIPT_REQUIRED")
    if _SAFETY_CLAIMS & set(receipt):
        raise ValueError("CALLER_SAFETY_CLAIMS_FORBIDDEN")
    required = {
        "media_id", "role", "output_sha256", "output_width", "output_height", "input_sha256",
        "renderer_executable", "renderer_sha256", "renderer_version", "argv", "started_at", "ended_at",
        "exit_code", "stdout_sha256", "stderr_sha256", "adapter_class", "safety_findings", "bindings",
    }
    if required - set(receipt):
        raise ValueError(f"RENDER_RECEIPT_INCOMPLETE: missing={sorted(required - set(receipt))}")
    digests = ("output_sha256", "input_sha256", "renderer_sha256", "stdout_sha256", "stderr_sha256")
    if any(not isinstance(receipt.get(key), str) or len(receipt[key]) != 64 or any(ch not in "0123456789abcdef" for ch in receipt[key]) for key in digests):
        raise ValueError("RENDER_RECEIPT_INCOMPLETE: invalid SHA-256")
    bindings = receipt.get("bindings")
    if not isinstance(bindings, list) or not bindings or any(
        not isinstance(binding, dict) or not isinstance(binding.get("model_id"), str)
        or not isinstance(binding.get("round"), int) or isinstance(binding.get("round"), bool)
        or not isinstance(binding.get("rubric_ids"), list) or not binding.get("rubric_ids")
        or any(not isinstance(value, str) or not value for value in binding.get("rubric_ids", []))
        for binding in bindings
    ):
        raise ValueError("RENDER_RECEIPT_INCOMPLETE: model/round/rubrics bindings are required")
    path = Path(render_path)
    info = inspect_image(path)
    if info["mime"] != "image/png" or info["sha256"] != receipt.get("output_sha256"):
        raise ValueError("RENDER_RECEIPT_MISMATCH")
    if (info["width"], info["height"]) != (receipt.get("output_width"), receipt.get("output_height")):
        raise ValueError("RENDER_RECEIPT_MISMATCH")
    if receipt.get("adapter_class") != "passive_static_only":
        raise ValueError("NO_QUALIFIED_RENDERER")
    executable = Path(receipt.get("renderer_executable", ""))
    if not executable.is_absolute() or not executable.is_file() or sha256_file(executable) != receipt.get("renderer_sha256"):
        raise ValueError("RENDERER_DIGEST_MISMATCH")
    expected_argv = [
        receipt["renderer_executable"], "--headless=new", "--disable-gpu", "--no-first-run",
        "--no-default-browser-check", "--user-data-dir=<TEMP_PROFILE>", "--screenshot=<OUTPUT_FILE>",
        f"--window-size={receipt['output_width']},{receipt['output_height']}", "<INPUT_FILE>",
    ]
    if receipt.get("argv") != expected_argv or receipt.get("exit_code") != 0 or not receipt.get("renderer_version"):
        raise ValueError("RENDER_RECEIPT_INCOMPLETE: renderer argv/version/exit is invalid")
    if receipt.get("role") == "candidate_crop":
        crop = receipt.get("crop")
        if not receipt.get("parent_media_id") or not isinstance(crop, dict):
            raise ValueError("CROP_PARENT_REQUIRED")
        values = [crop.get(key) for key in ("x", "y", "width", "height")]
        if any(not isinstance(value, int) or isinstance(value, bool) for value in values):
            raise ValueError("CROP_INVALID")
        index = json.loads((Path(form_root) / "observations/media-index.json").read_text(encoding="utf-8"))
        parent = index.get("uses", {}).get(receipt.get("parent_media_id"))
        if not isinstance(parent, dict) or parent.get("role") != "candidate_full":
            raise ValueError("CROP_PARENT_INVALID: parent must name a registered full render")
        parent_blob = index.get("blobs", {}).get(parent.get("blob_sha256"), {})
        x, y, width, height = values
        if (width, height) != (info["width"], info["height"]) or x < 0 or y < 0 or width <= 0 or height <= 0 or x + width > parent_blob.get("width", 0) or y + height > parent_blob.get("height", 0):
            raise ValueError("CROP_OUT_OF_BOUNDS")
    root = Path(form_root)
    receipt_relative = f"observations/render-receipts/{receipt['media_id']}.json"
    stored_receipt = root.joinpath(*receipt_relative.split("/"))
    if stored_receipt.exists() or stored_receipt.is_symlink():
        raise FileExistsError(f"Render receipt already registered: {receipt['media_id']}")
    stored_receipt.parent.mkdir(parents=True, exist_ok=True)
    staged = stored_receipt.with_name(f".{stored_receipt.name}.{uuid.uuid4().hex}.tmp")
    shutil.copyfile(receipt_path, staged)
    if sha256_file(staged) != sha256_file(receipt_path):
        staged.unlink(missing_ok=True)
        raise ValueError("RENDER_RECEIPT_CHANGED")
    os.replace(staged, stored_receipt)
    try:
        bindings = _normalize_bindings(root, receipt.get("bindings"), render_scopes=True)
        return _register(root, path, receipt.get("media_id"), receipt.get("role"), f"render_media:{receipt_relative}", bindings, receipt=receipt)
    except BaseException:
        stored_receipt.unlink(missing_ok=True)
        raise


__all__ = ["inspect_image", "register_render", "register_source_media"]
