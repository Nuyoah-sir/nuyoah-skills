#!/usr/bin/env python3
"""Read-only discovery for common Vibe Evals task-package layouts."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import unicodedata
from pathlib import Path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _file(path: Path, root: Path) -> dict:
    stat = path.stat()
    return {"path": path.relative_to(root).as_posix(), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns, "sha256": _sha256(path)}


def _model_id(name: str) -> str:
    value = unicodedata.normalize("NFKC", name).lower().strip()
    value = re.sub(r"^模型[\s_-]*", "", value)
    value = re.sub(r"[\s_]+", "-", value)
    value = re.sub(r"[^a-z0-9-]+", "-", value).strip("-")
    if value:
        return value
    return "model-" + hashlib.sha256(name.encode("utf-8")).hexdigest()[:10]


def _rubric_rank(name: str) -> int:
    if name.startswith("合并rubrics"):
        return 0
    if name.startswith("rubrics"):
        return 1
    if name.startswith("原rubrics"):
        return 2
    return 99


def _source_bindings(root: Path, models: list[dict], snapshots: list[Path], conversations: list[Path]) -> tuple[dict, list[dict]]:
    """Conservatively bind source material to models from directory identity.

    A snapshot is left unassigned when its owner is ambiguous. With one model,
    all Rn directories and a sole conversation file are unambiguous.
    """
    result = {
        item["model_id"]: {
            "display_name": item["display_name"],
            "final_root": item["path"],
            "round_roots": {},
            "conversation_source_path": None,
        }
        for item in models
    }
    binding_errors: list[dict] = []
    snapshot_candidates: dict[tuple[str, str], list[str]] = {}
    for snapshot in snapshots:
        match = re.fullmatch(r"R(\d+)", snapshot.name, re.IGNORECASE)
        if not match:
            continue
        relative = snapshot.relative_to(root).as_posix()
        candidates = []
        for model in models:
            parts = {part.casefold() for part in Path(relative).parts}
            if relative.startswith(model["path"].rstrip("/") + "/") or model["display_name"].casefold() in parts or model["model_id"].casefold() in parts:
                candidates.append(model["model_id"])
        if len(models) == 1:
            candidates = [models[0]["model_id"]]
        if len(candidates) == 1:
            snapshot_candidates.setdefault((candidates[0], match.group(1)), []).append(relative)
        elif len(candidates) > 1:
            binding_errors.append({"code": "SNAPSHOT_OWNER_AMBIGUOUS", "message": f"Snapshot {relative} matches multiple models", "candidates": candidates})
    for (model_id, round_number), paths in snapshot_candidates.items():
        if len(paths) != 1:
            binding_errors.append({"code": "SNAPSHOT_ROUND_AMBIGUOUS", "message": f"Model {model_id} round {round_number} has multiple snapshots", "candidates": paths})
        else:
            result[model_id]["round_roots"][round_number] = paths[0]
    conversation_candidates: dict[str, list[str]] = {item["model_id"]: [] for item in models}
    for conversation in conversations:
        relative = conversation.relative_to(root).as_posix()
        candidates = []
        folded = relative.casefold()
        for model in models:
            if model["display_name"].casefold() in folded or model["model_id"].casefold() in folded:
                candidates.append(model["model_id"])
        if len(models) == 1:
            candidates = [models[0]["model_id"]]
        if len(candidates) > 1:
            binding_errors.append({"code": "CONVERSATION_OWNER_AMBIGUOUS", "message": f"Conversation {relative} matches multiple models", "candidates": candidates})
        elif len(candidates) == 1:
            conversation_candidates[candidates[0]].append(relative)
    for model_id, paths in conversation_candidates.items():
        if len(paths) > 1:
            binding_errors.append({"code": "CONVERSATION_MODEL_AMBIGUOUS", "message": f"Model {model_id} has multiple conversation files", "candidates": paths})
        elif len(paths) == 1:
            result[model_id]["conversation_source_path"] = paths[0]
    return result, binding_errors


def discover_task_package(task_root: str | Path) -> dict:
    root = Path(task_root).resolve()
    errors: list[dict] = []
    warnings: list[dict] = []
    if not root.is_dir():
        return {"ok": False, "task_root": str(root), "errors": [{"code": "TASK_ROOT_MISSING", "message": "Task root is not a directory"}], "warnings": []}

    prompts = sorted(root.glob("**/prompt.md"))
    prompt = prompts[0] if len(prompts) == 1 else None
    if not prompt:
        code = "PROMPT_MISSING" if not prompts else "PROMPT_AMBIGUOUS"
        errors.append({"code": code, "message": "Exactly one prompt.md is required", "candidates": [p.relative_to(root).as_posix() for p in prompts]})

    rubric_candidates = [p for p in root.glob("**/*rubrics*.json") if p.is_file()]
    by_round: dict[int, list[Path]] = {}
    for path in rubric_candidates:
        match = re.search(r"rubrics(\d+)", path.name, re.IGNORECASE)
        if match:
            by_round.setdefault(int(match.group(1)), []).append(path)
    selected: dict[int, Path] = {}
    for round_number, paths in by_round.items():
        best_rank = min(_rubric_rank(path.name) for path in paths)
        best = sorted(path for path in paths if _rubric_rank(path.name) == best_rank)
        if len(best) > 1:
            errors.append({"code": "RUBRICS_AMBIGUOUS", "message": f"Round {round_number} has multiple same-priority rubric files", "candidates": [p.relative_to(root).as_posix() for p in best]})
        else:
            selected[round_number] = best[0]
    if not selected:
        errors.append({"code": "RUBRICS_MISSING", "message": "No 合并rubrics*.json, rubrics*.json, or 原rubrics*.json found"})

    model_roots = [p for p in root.iterdir() if p.is_dir() and p.name.endswith("模型输出")]
    if not model_roots:
        model_roots = [p for p in root.glob("**/*-模型输出") if p.is_dir()]
    model_root = model_roots[0] if len(model_roots) == 1 else None
    models: list[dict] = []
    seen_ids: set[str] = set()
    if not model_root:
        code = "MODEL_OUTPUT_MISSING" if not model_roots else "MODEL_OUTPUT_AMBIGUOUS"
        errors.append({"code": code, "message": "Exactly one *-模型输出 directory is required", "candidates": [p.relative_to(root).as_posix() for p in model_roots]})
    else:
        for child in sorted((p for p in model_root.iterdir() if p.is_dir()), key=lambda p: p.name):
            model_id = _model_id(child.name)
            if model_id in seen_ids:
                errors.append({"code": "MODEL_ID_COLLISION", "message": f"Multiple model directories normalize to {model_id}", "paths": [m["path"] for m in models if m["model_id"] == model_id] + [child.relative_to(root).as_posix()]})
            seen_ids.add(model_id)
            models.append({"model_id": model_id, "display_name": child.name, "path": child.relative_to(root).as_posix()})
        if not models:
            errors.append({"code": "MODEL_OUTPUT_EMPTY", "message": "Model output directory has no model subdirectories"})

    conversation_files = sorted(root.glob("**/*模型对话记录.json"))
    snapshots = sorted(p for p in root.glob("**/R[0-9]*") if p.is_dir())
    records = sorted(root.glob("**/记录.txt"))
    if not conversation_files:
        warnings.append({"code": "CONVERSATION_MISSING", "message": "No model conversation JSON found"})
    if not snapshots:
        warnings.append({"code": "SNAPSHOTS_MISSING", "message": "No R1/R2 round snapshot directories found"})
    if not records:
        warnings.append({"code": "RECORD_TXT_MISSING", "message": "record.txt is optional but absent"})
    elif len(records) > 1:
        errors.append({"code": "RECORD_TXT_AMBIGUOUS", "message": "Multiple 记录.txt files found; select or remove ambiguity before export", "candidates": [path.relative_to(root).as_posix() for path in records]})

    files = []
    for path in ([prompt] if prompt else []) + list(selected.values()) + conversation_files + records:
        files.append(_file(path, root))
    source_files = list(files)
    source_paths = []
    if model_root:
        source_paths.extend(p for p in model_root.rglob("*") if p.is_file() and not p.is_symlink())
    for snapshot in snapshots:
        source_paths.extend(p for p in snapshot.rglob("*") if p.is_file() and not p.is_symlink())
    known = {item["path"] for item in source_files}
    for path in sorted(set(source_paths)):
        item = _file(path, root)
        if item["path"] not in known:
            source_files.append(item)
            known.add(item["path"])
    inventory_digest = hashlib.sha256("\n".join(f"{f['sha256']}  {f['path']}" for f in sorted(source_files, key=lambda item: item["path"])).encode("utf-8")).hexdigest()
    bindings, binding_errors = _source_bindings(root, models, snapshots, conversation_files)
    errors.extend(binding_errors)
    prompt_entry = _file(prompt, root) if prompt else None
    rubric_entries = [{"round": round_number, **_file(path, root)} for round_number, path in sorted(selected.items())]
    record_entry = _file(records[0], root) if len(records) == 1 else None
    identity = {
        "inventory_digest": inventory_digest,
        "prompt_source": {key: prompt_entry.get(key) for key in ("path", "size", "sha256")} if prompt_entry else None,
        "record_source": {key: record_entry.get(key) for key in ("path", "size", "sha256")} if record_entry else None,
        "rubric_sources": [{key: item.get(key) for key in ("round", "path", "size", "sha256")} for item in rubric_entries],
        "model_sources": bindings,
    }
    digest = hashlib.sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    return {
        "ok": not errors,
        "task_root": str(root),
        "prompt": prompt_entry,
        "rubrics": rubric_entries,
        "model_output_root": model_root.relative_to(root).as_posix() if model_root else None,
        "models": models,
        "conversations": [_file(path, root) for path in conversation_files],
        "snapshot_dirs": [p.relative_to(root).as_posix() for p in snapshots],
        "record_txt": record_entry,
        "input_digest": digest,
        "source_inventory_digest": inventory_digest,
        "source_file_count": len(source_files),
        "source_inventory": source_files,
        "model_source_bindings": bindings,
        "errors": errors,
        "warnings": warnings,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task_root", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    result = discover_task_package(args.task_root)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if result["ok"] else 3


if __name__ == "__main__":
    sys.exit(main())
