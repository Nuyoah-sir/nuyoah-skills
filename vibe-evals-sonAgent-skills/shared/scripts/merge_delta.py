#!/usr/bin/env python3
"""Apply a sealed evidence delta into a new unsealed canonical bundle."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from validate_delta import validate_delta
from validate_bundle import validate_bundle


def _merge_run_namespaces(output_model: Path, delta_model: Path) -> None:
    """Merge validated run indexes and their referenced files without overwriting history."""
    for namespace in ("tests", "probes"):
        delta_namespace = delta_model / namespace
        delta_index = delta_namespace / "index.json"
        if not delta_index.is_file():
            continue
        delta_value = json.loads(delta_index.read_text(encoding="utf-8"))
        output_namespace = output_model / namespace
        output_index = output_namespace / "index.json"
        base_value = json.loads(output_index.read_text(encoding="utf-8")) if output_index.is_file() else {"runs": []}
        base_ids = {item.get("run_id") for item in base_value.get("runs", [])}
        delta_ids = [item.get("run_id") for item in delta_value.get("runs", [])]
        collisions = sorted(base_ids & set(delta_ids))
        if collisions:
            raise ValueError(f"Run IDs already exist in base {namespace}: {collisions}")
        for source in sorted((item for item in delta_namespace.rglob("*") if item.is_file() and item != delta_index), key=lambda item: item.relative_to(delta_namespace).as_posix()):
            relative = source.relative_to(delta_namespace)
            target = output_namespace / relative
            if target.exists():
                raise ValueError(f"Delta would overwrite run artifact: {target}")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        output_namespace.mkdir(parents=True, exist_ok=True)
        output_index.write_text(json.dumps({**base_value, "runs": base_value.get("runs", []) + delta_value.get("runs", [])}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def merge_delta(base_dir: str | Path, delta_dir: str | Path, requests_path: str | Path, output_dir: str | Path) -> Path:
    base = Path(base_dir).resolve()
    delta = Path(delta_dir).resolve()
    output = Path(output_dir).resolve()
    report = validate_delta(delta, base, requests_path, require_seal=True)
    if report["result"] != "pass":
        raise ValueError(json.dumps(report, ensure_ascii=False))
    if output.exists():
        raise FileExistsError(f"Output directory already exists: {output}")
    shutil.copytree(base, output)
    for relative in ("READY.json", "integrity/files.sha256", "integrity/validation-report.json"):
        (output / relative).unlink(missing_ok=True)
    meta = json.loads((delta / "DELTA.json").read_text(encoding="utf-8"))
    responses = [json.loads(line) for line in (delta / "responses.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    manifest_path = output / "MANIFEST.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    model_dirs = {item["model_id"]: item["directory"] for item in manifest["models"]}
    for model_id, model_dir in model_dirs.items():
        delta_model = delta / "models" / model_id
        if delta_model.exists():
            _merge_run_namespaces(output / model_dir, delta_model)
    referenced_blobs = {
        evidence.get("source_blob_path")
        for response in responses
        for evidence in response.get("new_evidence", [])
        if evidence.get("type") == "static_line" and isinstance(evidence.get("source_blob_path"), str)
        and evidence.get("source_blob_path", "").startswith("evidence/source-blobs/")
    }
    for relative in sorted(referenced_blobs):
        source = delta / relative
        target = output / relative
        if not source.is_file():
            if target.is_file():
                continue
            raise ValueError(f"Referenced source blob is absent from both base and delta: {relative}")
        if target.exists() and target.read_bytes() != source.read_bytes():
            raise ValueError(f"Content-addressed source blob collision: {target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            shutil.copy2(source, target)
    for response in responses:
        model_id = response["model_id"]
        rubric_id = response["rubric_id"]
        evidence_path = output / model_dirs[model_id] / "rubric-evidence.jsonl"
        rows = [json.loads(line) for line in evidence_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        for row in rows:
            if row["rubric_id"] == rubric_id:
                row["evidence"] = row.get("evidence", []) + response.get("new_evidence", [])
                update = response.get("proposed_update", {}) if response.get("status") == "fulfilled" else {}
                allowed = {"suggested_score", "reason_code", "coverage", "confidence", "fact_summary", "limitations"}
                row.update({key: value for key, value in update.items() if key in allowed})
                break
        evidence_path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
        supplement = output / model_dirs[model_id] / "supplements" / f"{response['request_id']}.json"
        supplement.parent.mkdir(parents=True, exist_ok=True)
        supplement.write_text(json.dumps(response, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manifest["parent_package_id"] = manifest["package_id"]
    manifest["package_id"] = f"{manifest['package_id']}+{meta['delta_id']}"
    manifest["applied_deltas"] = manifest.get("applied_deltas", []) + [meta["delta_id"]]
    pending_doc = json.loads((output / "review/pending-adjudications.json").read_text(encoding="utf-8"))
    any_pending = any(item.get("status") == "pending" for item in pending_doc.get("items", []))
    any_unknown = any(json.loads(line).get("suggested_score") is None or json.loads(line).get("human_check_needed") for model in manifest["models"] for line in (output / model["directory"] / "rubric-evidence.jsonl").read_text(encoding="utf-8").splitlines() if line.strip())
    manifest["package_status"] = "ready_for_local_review" if any_pending or any_unknown else "ready_for_form"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    merged_report = validate_bundle(output, require_seal=False)
    if merged_report["result"] != "pass":
        raise ValueError("Merged bundle failed validation: " + json.dumps(merged_report, ensure_ascii=False))
    return output


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base_dir", type=Path)
    parser.add_argument("delta_dir", type=Path)
    parser.add_argument("requests", type=Path)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args(argv)
    try:
        result = merge_delta(args.base_dir, args.delta_dir, args.requests, args.output_dir)
    except (ValueError, FileExistsError) as exc:
        print(str(exc), file=sys.stderr)
        return 3
    print(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
