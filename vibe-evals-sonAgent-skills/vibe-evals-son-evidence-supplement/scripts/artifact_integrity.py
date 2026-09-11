#!/usr/bin/env python3
"""Dependency-free integrity helpers for sealed Vibe Evals artifacts."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import unicodedata
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from typing import Iterable, Mapping


DEFAULT_LIMITS = {
    "members": 10_000,
    "member_bytes": 128 * 1024 * 1024,
    "total_bytes": 512 * 1024 * 1024,
    "compression_ratio": 200,
}

_HEX64 = re.compile(r"[0-9a-fA-F]{64}")
_SIDECAR = re.compile(r"([0-9a-fA-F]{64})  ([^\r\n]+)\r?\n?")
_CHECKSUM_LINE = re.compile(r"([0-9a-fA-F]{64})  ([^\r\n]+)")
_WINDOWS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
_MANIFEST_SCHEMAS = {
    "FORM-READY.json": ("form-ready", "vibe-evals-form-ready-bundle", "2.0.0"),
    "MANIFEST.json": ("bundle", "vibe-evals-evidence-bundle", "1.0.0"),
    "DELTA.json": ("delta", "vibe-evals-evidence-delta", "1.0.0"),
}


def sha256_file(path: str | Path) -> str:
    """Return the lowercase SHA-256 digest of *path*."""

    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _normalized_digest(value: str, label: str) -> str:
    if not isinstance(value, str) or _HEX64.fullmatch(value) is None:
        raise ValueError(f"{label} must be exactly 64 hexadecimal characters")
    return value.lower()


def verify_sidecar(
    archive: str | Path,
    sidecar: str | Path,
    expected_sha256: str | None = None,
) -> str:
    """Verify a filename-bound SHA-256 sidecar and optional independent digest."""

    archive_path = Path(archive)
    text = Path(sidecar).read_text(encoding="utf-8")
    match = _SIDECAR.fullmatch(text)
    if match is None:
        raise ValueError("SHA-256 sidecar must contain exactly: 64 hex, two spaces, archive filename")
    sidecar_digest = match.group(1).lower()
    if match.group(2) != archive_path.name:
        raise ValueError("SHA-256 sidecar filename does not match the archive filename")
    actual_digest = sha256_file(archive_path)
    if sidecar_digest != actual_digest:
        raise ValueError(f"Archive SHA-256 mismatch: expected {sidecar_digest}, actual {actual_digest}")
    if expected_sha256 is not None:
        expected_digest = _normalized_digest(expected_sha256, "Expected SHA-256")
        if expected_digest != actual_digest:
            raise ValueError(f"Archive SHA-256 does not match user expected digest: {expected_digest}")
    return actual_digest


def _collision_key(path: str) -> str:
    return unicodedata.normalize("NFC", path).casefold()


def _safe_posix_path(raw_name: str, *, directory: bool = False) -> str:
    if not isinstance(raw_name, str) or not raw_name or "\x00" in raw_name:
        raise ValueError(f"Unsafe artifact path: {raw_name!r}")
    name = raw_name.replace("\\", "/")
    if name.startswith("/") or name.startswith("//") or re.match(r"^[A-Za-z]:", name):
        raise ValueError(f"Unsafe absolute or drive path: {raw_name}")
    if directory and name.endswith("/"):
        name = name[:-1]
    parts = name.split("/")
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise ValueError(f"Unsafe artifact path: {raw_name}")
    for part in parts:
        if ":" in part or part.rstrip(" .") != part:
            raise ValueError(f"Unsafe Windows artifact path: {raw_name}")
        if part.split(".", 1)[0].upper() in _WINDOWS_RESERVED:
            raise ValueError(f"Unsafe Windows reserved name: {raw_name}")
    normalized = PurePosixPath(*parts).as_posix()
    if normalized in {"", "."}:
        raise ValueError(f"Unsafe artifact path: {raw_name}")
    return normalized


def _effective_limits(limits: Mapping[str, int | float] | None) -> dict[str, int | float]:
    values = dict(DEFAULT_LIMITS)
    if limits is not None:
        unknown = set(limits) - set(DEFAULT_LIMITS)
        if unknown:
            raise ValueError(f"Unknown ZIP limit(s): {', '.join(sorted(unknown))}")
        values.update(limits)
    for name, value in values.items():
        if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
            raise ValueError(f"ZIP limit {name} must be positive")
    return values


def _entry_kind(info: zipfile.ZipInfo) -> str:
    unix_mode = (info.external_attr >> 16) & 0xFFFF
    file_type = stat.S_IFMT(unix_mode)
    if info.is_dir():
        if file_type not in (0, stat.S_IFDIR):
            raise ValueError(f"Unsafe ZIP special entry: {info.filename}")
        return "directory"
    if file_type not in (0, stat.S_IFREG):
        raise ValueError(f"Unsafe ZIP symlink or device entry: {info.filename}")
    return "file"


def _preflight_zip(
    handle: zipfile.ZipFile,
    limits: Mapping[str, int | float] | None = None,
) -> list[tuple[zipfile.ZipInfo, str, str]]:
    effective = _effective_limits(limits)
    infos = handle.infolist()
    if len(infos) > effective["members"]:
        raise ValueError(f"ZIP member count exceeds limit {effective['members']}")
    checked: list[tuple[zipfile.ZipInfo, str, str]] = []
    seen: dict[str, str] = {}
    file_paths: set[str] = set()
    required_directories: set[str] = set()
    total_size = 0
    for info in infos:
        if info.flag_bits & 0x1:
            raise ValueError(f"Encrypted ZIP member is not allowed: {info.filename}")
        kind = _entry_kind(info)
        normalized = _safe_posix_path(info.filename, directory=kind == "directory")
        collision_key = _collision_key(normalized)
        if collision_key in seen:
            raise ValueError(f"Duplicate normalized ZIP member: {info.filename}")
        seen[collision_key] = info.filename
        parts = normalized.split("/")
        ancestors = {"/".join(parts[:index]) for index in range(1, len(parts))}
        if ancestors & file_paths or (kind == "file" and normalized in required_directories):
            raise ValueError(f"ZIP file/directory conflict: {info.filename}")
        required_directories.update(ancestors)
        if kind == "file":
            file_paths.add(normalized)
        if info.file_size > effective["member_bytes"]:
            raise ValueError(f"ZIP member exceeds byte limit: {info.filename}")
        total_size += info.file_size
        if total_size > effective["total_bytes"]:
            raise ValueError("ZIP total expanded bytes exceed limit")
        if info.file_size:
            if info.compress_size <= 0 or info.file_size / info.compress_size > effective["compression_ratio"]:
                raise ValueError(f"ZIP member compression ratio exceeds limit: {info.filename}")
        checked.append((info, normalized, kind))
    return checked


def safe_extract_zip(
    archive: str | Path,
    destination: str | Path,
    *,
    limits: Mapping[str, int | float] | None = None,
) -> Path:
    """Preflight an entire ZIP, stage extraction, and atomically publish it."""

    archive_path = Path(archive)
    destination_path = Path(destination)
    if destination_path.exists() or destination_path.is_symlink():
        raise FileExistsError(f"Destination already exists: {destination_path}")
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    staging = destination_path.parent / f".{destination_path.name}.{uuid.uuid4().hex}.tmp"
    try:
        with zipfile.ZipFile(archive_path) as handle:
            checked = _preflight_zip(handle, limits)
            staging.mkdir()
            for info, relative, kind in checked:
                target = staging.joinpath(*PurePosixPath(relative).parts)
                if kind == "directory":
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with handle.open(info, "r") as source, target.open("xb") as output:
                    shutil.copyfileobj(source, output, length=1024 * 1024)
        if destination_path.exists() or destination_path.is_symlink():
            raise FileExistsError(f"Destination already exists: {destination_path}")
        staging.rename(destination_path)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return destination_path


def detect_artifact_kind(
    archive: str | Path,
    *,
    limits: Mapping[str, int | float] | None = None,
    manifest_bytes: int = 1024 * 1024,
) -> tuple[str, str]:
    """Return ``(kind, version)`` after safe ZIP preflight and bounded parsing."""

    if not isinstance(manifest_bytes, int) or isinstance(manifest_bytes, bool) or manifest_bytes <= 0:
        raise ValueError("manifest_bytes must be a positive integer")
    with zipfile.ZipFile(Path(archive)) as handle:
        checked = _preflight_zip(handle, limits)
        root_manifests = [item for item in checked if item[1] in _MANIFEST_SCHEMAS and "/" not in item[1]]
        if len(root_manifests) != 1:
            raise ValueError("Artifact must contain exactly one supported root manifest")
        info, filename, kind = root_manifests[0]
        if kind != "file":
            raise ValueError(f"Root manifest must be a file: {filename}")
        if info.file_size > manifest_bytes:
            raise ValueError(f"Root manifest exceeds {manifest_bytes} byte limit: {filename}")
        with handle.open(info, "r") as source:
            raw = source.read(manifest_bytes + 1)
        if len(raw) > manifest_bytes:
            raise ValueError(f"Root manifest exceeds {manifest_bytes} byte limit: {filename}")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid root manifest JSON: {filename}") from exc
    expected_kind, expected_schema, expected_version = _MANIFEST_SCHEMAS[filename]
    if not isinstance(value, dict) or value.get("schema") != expected_schema:
        raise ValueError(f"Unknown artifact schema in {filename}")
    version = value.get("schema_version")
    if version != expected_version:
        raise ValueError(f"Unsupported artifact schema version in {filename}: {version!r}")
    return expected_kind, version


def _normalize_relative_set(paths: Iterable[str], label: str) -> set[str]:
    normalized: set[str] = set()
    collision_keys: set[str] = set()
    for path in paths:
        clean = _safe_posix_path(path)
        key = _collision_key(clean)
        if key in collision_keys:
            raise ValueError(f"Duplicate normalized {label} path: {path}")
        collision_keys.add(key)
        normalized.add(clean)
    return normalized


def _inventory_files(root: Path, excludes: set[str]) -> dict[str, Path]:
    if not root.is_dir():
        raise ValueError(f"Checksum root is not a directory: {root}")
    result: dict[str, Path] = {}
    collision_keys: set[str] = set()
    paths = sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix())
    for path in paths:
        if path.is_symlink():
            raise ValueError(f"Checksum tree contains symbolic link: {path.relative_to(root).as_posix()}")
        if not path.is_file():
            continue
        relative = _safe_posix_path(path.relative_to(root).as_posix())
        key = _collision_key(relative)
        if key in collision_keys:
            raise ValueError(f"Duplicate normalized checksum path: {relative}")
        collision_keys.add(key)
        if relative not in excludes:
            result[relative] = path
    return result


def _rows_for_inventory(files: Mapping[str, Path]) -> list[dict[str, str]]:
    return [{"path": relative, "sha256": sha256_file(files[relative])} for relative in sorted(files)]


def write_checksum_manifest(
    root: str | Path,
    manifest: str | Path | None = None,
    *,
    excludes: set[str] | frozenset[str],
) -> list[dict[str, str]]:
    """Write a deterministic SHA-256 manifest and return its sorted rows."""

    root_path = Path(root)
    excludes_normalized = _normalize_relative_set(excludes, "excluded")
    manifest_path = Path(manifest) if manifest is not None else root_path / "integrity" / "files.sha256"
    try:
        manifest_relative = manifest_path.resolve(strict=False).relative_to(root_path.resolve(strict=True)).as_posix()
    except ValueError as exc:
        raise ValueError("Checksum manifest must be inside its root") from exc
    if manifest_relative not in excludes_normalized:
        raise ValueError("Checksum manifest path must be caller-excluded")
    rows = _rows_for_inventory(_inventory_files(root_path, excludes_normalized))
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = manifest_path.with_name(f".{manifest_path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text("".join(f"{row['sha256']}  {row['path']}\n" for row in rows), encoding="utf-8")
        os.replace(temporary, manifest_path)
    finally:
        temporary.unlink(missing_ok=True)
    return rows


def verify_checksum_manifest(
    root: str | Path,
    manifest: str | Path | None = None,
    *,
    excludes: set[str] | frozenset[str],
) -> list[dict[str, str]]:
    """Verify manifest syntax, digests, and exact caller-defined file coverage."""

    root_path = Path(root)
    excludes_normalized = _normalize_relative_set(excludes, "excluded")
    manifest_path = Path(manifest) if manifest is not None else root_path / "integrity" / "files.sha256"
    text = manifest_path.read_text(encoding="utf-8")
    rows: list[dict[str, str]] = []
    listed: dict[str, str] = {}
    collision_keys: set[str] = set()
    for line_number, line in enumerate(text.splitlines(), 1):
        match = _CHECKSUM_LINE.fullmatch(line)
        if match is None:
            raise ValueError(f"Invalid checksum manifest line {line_number}")
        digest = match.group(1).lower()
        relative = _safe_posix_path(match.group(2))
        key = _collision_key(relative)
        if key in collision_keys:
            raise ValueError(f"Duplicate normalized checksum path: {relative}")
        collision_keys.add(key)
        listed[relative] = digest
        rows.append({"path": relative, "sha256": digest})
    if [row["path"] for row in rows] != sorted(listed):
        raise ValueError("Checksum manifest paths are not in deterministic order")
    files = _inventory_files(root_path, excludes_normalized)
    listed_paths = set(listed)
    actual_paths = set(files)
    if listed_paths != actual_paths:
        missing = sorted(actual_paths - listed_paths)
        extra = sorted(listed_paths - actual_paths)
        raise ValueError(f"Checksum manifest coverage mismatch: missing={missing}, extra={extra}")
    mismatches = [relative for relative in sorted(files) if sha256_file(files[relative]) != listed[relative]]
    if mismatches:
        raise ValueError(f"Checksum mismatch: {', '.join(mismatches)}")
    return rows


__all__ = [
    "DEFAULT_LIMITS",
    "detect_artifact_kind",
    "safe_extract_zip",
    "sha256_file",
    "verify_checksum_manifest",
    "verify_sidecar",
    "write_checksum_manifest",
]
