#!/usr/bin/env python3
"""Create a deterministic evidence-bundle workspace from discovery.json."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import uuid
from pathlib import Path

from discover_task_package import discover_task_package
from validate_bundle import normalize_round


def initialize_bundle(task_root: str | Path, discovery_path: str | Path, bundle_dir: str | Path) -> Path:
    task = Path(task_root).resolve()
    supplied = json.loads(Path(discovery_path).read_text(encoding="utf-8"))
    bundle = Path(bundle_dir).resolve()
    if not supplied.get("ok"):
        raise ValueError("Discovery is not successful")
    current = discover_task_package(task)
    if not current.get("ok") or current.get("input_digest") != supplied.get("input_digest"):
        raise ValueError("Task source or discovery changed; run discovery again with a new run_id")
    discovery = current
    if bundle.exists():
        raise FileExistsError(f"Bundle directory already exists: {bundle}")
    try:
        bundle.relative_to(task)
    except ValueError:
        pass
    else:
        raise ValueError("Bundle output must be outside the read-only task root")
    bundle.mkdir(parents=True)
    prompt_source = task / discovery["prompt"]["path"]
    prompt_target = bundle / "inputs/prompt.md"
    prompt_target.parent.mkdir(parents=True)
    shutil.copyfile(prompt_source, prompt_target)
    rubric_entries = []
    rubric_index = []
    for discovered in discovery["rubrics"]:
        source = task / discovered["path"]
        target = bundle / "inputs/rubrics" / f"rubrics{discovered['round']}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        values = json.loads(target.read_text(encoding="utf-8"))
        rubric_entries.append({"round": discovered["round"], "path": target.relative_to(bundle).as_posix(), "count": len(values), "sha256": discovered["sha256"]})
        for index, item in enumerate(values):
            raw_round = item.get("round", discovered["round"])
            normalized_round = normalize_round(raw_round)
            if normalized_round is None:
                raise ValueError(f"Rubric {item.get('id')!r} has an unsupported round {raw_round!r}")
            if normalized_round != discovered["round"]:
                raise ValueError(f"Rubric {item.get('id')!r} declares round {raw_round!r}, expected R{discovered['round']}")
            criterion = item["criterion"]
            rubric_index.append({"id": item["id"], "round": normalized_round, "criterion": criterion, "criterion_sha256": hashlib.sha256(criterion.encode("utf-8")).hexdigest(), "source_file": target.relative_to(bundle).as_posix(), "source_index": index, "source_basis": [], "review": {"basis_status": "unreviewed"}})
    (bundle / "inputs/rubric-index.json").write_text(json.dumps({"rubrics": rubric_index}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    prompt_rows = []
    for number, line in enumerate(prompt_source.read_text(encoding="utf-8").splitlines(), 1):
        if line.strip():
            prompt_rows.append({"requirement_id": f"P-CAND-{number:04d}", "round": None, "kind": "candidate", "text": line.strip(), "source": {"path": "inputs/prompt.md", "line_start": number, "line_end": number, "quote": line}, "mapped_rubric_ids": [], "coverage": "needs_classification"})
    (bundle / "inputs/prompt-requirements.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in prompt_rows), encoding="utf-8")
    record_target = None
    if discovery.get("record_txt"):
        record_target = bundle / "inputs/record.txt"
        shutil.copyfile(task / discovery["record_txt"]["path"], record_target)
    models = []
    source_inventory = discovery.get("source_inventory", [])
    source_bindings = discovery.get("model_source_bindings", {})
    for model in discovery["models"]:
        model_id = model["model_id"]
        model_root = bundle / "models" / model_id
        model_root.mkdir(parents=True)
        models.append({"model_id": model_id, "display_name": model["display_name"], "directory": model_root.relative_to(bundle).as_posix(), "output_status": "present"})
        binding = source_bindings.get(model_id, {})
        (model_root / "model.json").write_text(json.dumps({"model_id": model_id, "display_name": model["display_name"], "source_output_root_label": model["path"], "round_source_labels": binding.get("round_roots", {}), "conversation_source_path": binding.get("conversation_source_path"), "output_status": "present", "fallback_zero": False, "fallback_reason": None}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        prefix = model["path"].rstrip("/") + "/"
        final_files = [{"path": item["path"][len(prefix):], "size": item["size"], "sha256": item["sha256"]} for item in source_inventory if item["path"].startswith(prefix)]
        (model_root / "inventory.json").write_text(json.dumps({"final": {"files": final_files}, "rounds": []}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        rows = []
        for rubric in rubric_index:
            rows.append({"model_id": model_id, "rubric_id": rubric["id"], "rubric_round": rubric["round"], "criterion": rubric["criterion"], "disposition": "examined", "coverage": "missing", "suggested_score": None, "confidence": "low", "reason_code": "insufficient_evidence", "fact_summary": "待该模型证据工作者取证。", "evidence": [], "human_check_needed": False, "adjudication_ids": [], "limitations": ["not_examined_yet"]})
        (model_root / "rubric-evidence.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
        (model_root / "human-observations.jsonl").write_text("", encoding="utf-8")
    (bundle / "review").mkdir(parents=True)
    (bundle / "review/pending-adjudications.json").write_text('{"items": []}\n', encoding="utf-8")
    (bundle / "review/rubric-review.json").write_text(json.dumps({"status": "unreviewed", "items": []}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    package_id = str(uuid.uuid4())
    missing_materials = []
    if len(rubric_entries) > 1:
        for model in discovery["models"]:
            binding = source_bindings.get(model["model_id"], {})
            if not binding.get("conversation_source_path"):
                missing_materials.append(f"model:{model['model_id']}:conversation_record_missing")
            for round_number in range(1, len(rubric_entries)):
                if str(round_number) not in binding.get("round_roots", {}):
                    missing_materials.append(f"model:{model['model_id']}:round_{round_number}_snapshot_missing")
    manifest = {"schema": "vibe-evals-evidence-bundle", "schema_version": "1.0.0", "package_id": package_id, "task": {"name": task.name, "batch_id": task.name.rsplit("-", 1)[-1], "round_count": len(rubric_entries), "task_type": "unknown"}, "generator": {"agent": "codex", "skill_version": "1.0.0"}, "source_input_digest": discovery["input_digest"], "inputs": {"prompt": "inputs/prompt.md", "rubrics": rubric_entries, "record_txt": "inputs/record.txt" if record_target else None}, "models": models, "package_status": "ready_for_local_review", "missing_materials": missing_materials, "warnings": discovery.get("warnings", [])}
    (bundle / "MANIFEST.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (bundle / "source-freeze.json").write_text(json.dumps({"input_digest": discovery["input_digest"], "source_inventory_digest": discovery["source_inventory_digest"], "source_inventory": source_inventory, "prompt_source": discovery["prompt"], "record_source": discovery.get("record_txt"), "rubric_sources": discovery["rubrics"], "model_sources": source_bindings, "snapshot_dirs": discovery.get("snapshot_dirs", []), "conversation_files": [item["path"] for item in discovery.get("conversations", [])]}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (bundle / "run-state.json").write_text(json.dumps({"schema_version": "1.0.0", "package_id": package_id, "phase": "INITIALIZED", "models": {m["model_id"]: {"status": "PENDING", "attempts": 0} for m in models}}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return bundle


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task_root", type=Path)
    parser.add_argument("discovery", type=Path)
    parser.add_argument("bundle_dir", type=Path)
    args = parser.parse_args(argv)
    try:
        print(initialize_bundle(args.task_root, args.discovery, args.bundle_dir))
    except (ValueError, FileExistsError, KeyError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
