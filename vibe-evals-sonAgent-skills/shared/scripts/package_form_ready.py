#!/usr/bin/env python3
"""Seal a form-ready workspace into a verified outer ZIP plus sidecar.

The packager owns the outer seal: it writes the script-derived source
verification, the validation report, READY.json, and the checksum manifest, then
round-trips the staged ZIP through strict validation before publishing. If the
live source no longer matches the frozen inventory at any point, the workspace is
invalidated terminally and no ZIP is published.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from artifact_integrity import _walk_safe_files, safe_extract_zip, sha256_file, write_checksum_manifest
from form_ready_context import load_form_ready_base
from validate_form_ready import FORM_READY_EXCLUDES, SCHEMA_VERSION, assess_form_ready, validate_form_ready
from verify_source import verify_frozen_source

SOURCE_VERIFICATION = "integrity/source-verification.json"
RUN_STATE = "provenance/run-state.json"
TERMINAL_FAILURE = "invalid_source_changed"


class SourceChangedError(ValueError):
    """The live source no longer matches the frozen inventory."""

    exit_code = 4


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def _atomic_write(path: Path, text: str) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _write_source_verification(root: Path, task_root: Path, freeze: dict, manifest: dict, context) -> dict:
    """Write the script-derived source receipt; an agent-authored one is overwritten."""

    outcome = verify_frozen_source(task_root, freeze)
    record = {
        "schema_version": SCHEMA_VERSION,
        "outer_package_id": manifest["outer_package_id"],
        "base_package_id": context.package_id,
        "base_zip_sha256": manifest["base"]["sha256"],
        "source_input_digest": context.source_input_digest,
        "task_id": context.task_id,
        "checked_at": _now(),
        "result": "unchanged" if outcome.get("status") == "unchanged" else "changed",
        "generator": "package_form_ready",
        "detail": outcome,
    }
    _atomic_write(root.joinpath(*SOURCE_VERIFICATION.split("/")), _dump(record))
    return outcome


def _set_terminal_failure(root: Path, run_state: dict | None = None) -> None:
    path = root.joinpath(*RUN_STATE.split("/"))
    state = run_state if isinstance(run_state, dict) else {}
    if path.is_file():
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            state = {}
    state["phase"] = "terminated"
    state["terminal_failure_code"] = TERMINAL_FAILURE
    state["updated_at"] = _now()
    _atomic_write(path, _dump(state))


def _run_state(root: Path) -> dict:
    path = root.joinpath(*RUN_STATE.split("/"))
    if not path.is_file():
        return {}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return state if isinstance(state, dict) else {}


def _remove_seal_files(root: Path) -> None:
    for relative in sorted(FORM_READY_EXCLUDES):
        root.joinpath(*relative.split("/")).unlink(missing_ok=True)


def _invalidate(root: Path, manifest_path: Path, manifest: dict, verification: dict) -> None:
    """Terminal invalidation: no reuse even if the source bytes are restored later."""

    _remove_seal_files(root)
    manifest["package_status"] = "incomplete"
    _atomic_write(manifest_path, _dump(manifest))
    _set_terminal_failure(root)
    _atomic_write(root.joinpath(*SOURCE_VERIFICATION.split("/")), _dump({
        "schema_version": SCHEMA_VERSION,
        "outer_package_id": manifest.get("outer_package_id"),
        "checked_at": _now(),
        "result": "changed",
        "generator": "package_form_ready",
        "detail": verification,
    }))


def package_form_ready(form_root: str | Path, output_zip: str | Path, *, task_root: str | Path) -> dict[str, Any]:
    """Seal a complete, source-consistent workspace into an outer ZIP."""

    root = Path(form_root).resolve()
    output = Path(output_zip).resolve()
    task = Path(task_root).resolve()
    try:
        output.relative_to(root)
    except ValueError:
        pass
    else:
        raise ValueError("Output ZIP must be outside the form-ready package directory")
    sidecar = output.with_suffix(output.suffix + ".sha256")
    partial = output.with_suffix(output.suffix + ".partial")
    staged_sidecar = partial.with_suffix(partial.suffix + ".sha256")
    for candidate in (output, sidecar, partial, staged_sidecar):
        if candidate.exists() or candidate.is_symlink():
            raise FileExistsError(f"Output already exists: {candidate}")
    _walk_safe_files(root)
    terminal = _run_state(root).get("terminal_failure_code")
    if terminal:
        raise ValueError(f"WORKSPACE_TERMINALLY_FAILED: {terminal} cannot be reused, even if the source bytes were restored")
    manifest_path = root / "FORM-READY.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    original_status = manifest.get("package_status")
    _, context, freeze = load_form_ready_base(root)

    verification = _write_source_verification(root, task, freeze, manifest, context)
    if verification.get("status") != "unchanged":
        _invalidate(root, manifest_path, manifest, verification)
        raise SourceChangedError("SOURCE_CHANGED: " + json.dumps(verification, ensure_ascii=False))

    digest = ""
    try:
        report = assess_form_ready(root)
        if report["derived_status"] != "ready_for_form":
            raise ValueError("Form-ready package is not complete: " + json.dumps(report["errors"], ensure_ascii=False))
        manifest["package_status"] = "ready_for_form"
        _atomic_write(manifest_path, _dump(manifest))
        root.joinpath("integrity").mkdir(parents=True, exist_ok=True)
        _atomic_write(root / "integrity/validation-report.json", _dump(report))
        _atomic_write(root / "READY.json", _dump({
            "schema_version": SCHEMA_VERSION,
            "outer_package_id": manifest["outer_package_id"],
            "base_package_id": context.package_id,
            "status": "ready_for_form",
            "counts": report["counts"],
            "validated_at": _now(),
        }))
        write_checksum_manifest(root, excludes=set(FORM_READY_EXCLUDES))

        output.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(partial, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in _walk_safe_files(root):
                archive.write(path, path.relative_to(root).as_posix())
        digest = sha256_file(partial)
        _atomic_write(staged_sidecar, f"{digest}  {output.name}\n")
        with tempfile.TemporaryDirectory(prefix="vibe-form-ready-seal-") as temporary:
            extracted = safe_extract_zip(partial, Path(temporary) / "package")
            strict = validate_form_ready(extracted, require_seal=True)
            if strict["result"] != "pass":
                raise ValueError("Staged form-ready ZIP failed strict validation: " + json.dumps(strict["errors"], ensure_ascii=False))
        second = _write_source_verification(root, task, freeze, manifest, context)
        if second.get("status") != "unchanged":
            raise SourceChangedError("SOURCE_CHANGED: " + json.dumps(second, ensure_ascii=False))
        os.replace(partial, output)
        try:
            os.replace(staged_sidecar, sidecar)
        except BaseException:
            output.unlink(missing_ok=True)
            raise
    except SourceChangedError:
        _invalidate(root, manifest_path, manifest, {})
        for candidate in (partial, staged_sidecar, output, sidecar):
            candidate.unlink(missing_ok=True)
        raise
    except BaseException:
        partial.unlink(missing_ok=True)
        staged_sidecar.unlink(missing_ok=True)
        _remove_seal_files(root)
        manifest["package_status"] = original_status
        _atomic_write(manifest_path, _dump(manifest))
        raise
    return {"zip": str(output), "sha256": digest, "status": "ready_for_form", "counts": report["counts"]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("form_root", type=Path)
    parser.add_argument("output_zip", type=Path)
    parser.add_argument("--task-root", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = package_form_ready(args.form_root, args.output_zip, task_root=args.task_root)
    except SourceChangedError as exc:
        print(str(exc), file=sys.stderr)
        return SourceChangedError.exit_code
    except (ValueError, FileExistsError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 3
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["FORM_READY_EXCLUDES", "SourceChangedError", "package_form_ready"]
