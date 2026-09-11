#!/usr/bin/env python3
"""Load the immutable v1 evidence context used by form-ready v2 artifacts."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class BaseContext:
    root: Path
    manifest: dict[str, Any]
    package_id: str
    source_input_digest: str
    task_id: str
    models: dict[str, dict[str, Any]]
    rubrics: tuple[dict[str, Any], ...]
    evidence: dict[tuple[str, str], dict[str, Any]]
    pending_adjudications: tuple[dict[str, Any], ...]
    material_gaps: tuple[str, ...] = ()
    human_check_pairs: frozenset[tuple[str, str]] = frozenset()


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"Expected object at {path}:{line_number}")
        rows.append(value)
    return rows


def load_base_context(extracted_base: str | Path) -> BaseContext:
    """Return one normalized, read-only view of a strictly validated v1 tree."""

    root = Path(extracted_base)
    manifest = _read_json(root / "MANIFEST.json")
    rubric_index = _read_json(root / "inputs" / "rubric-index.json")
    rubrics = tuple(
        sorted(
            rubric_index.get("rubrics", []),
            key=lambda row: (row.get("round", 0), row.get("source_index", 0), row.get("id", "")),
        )
    )
    models = {row["model_id"]: row for row in manifest.get("models", [])}
    evidence: dict[tuple[str, str], dict[str, Any]] = {}
    human_checks: set[tuple[str, str]] = set()
    for model_id, model in models.items():
        for row in _read_jsonl(root / model["directory"] / "rubric-evidence.jsonl"):
            key = (model_id, row["rubric_id"])
            if key in evidence:
                raise ValueError(f"Duplicate base evidence row: {model_id}/{row['rubric_id']}")
            evidence[key] = row
            if row.get("human_check_needed") is True:
                human_checks.add(key)
    pending_doc = _read_json(root / "review" / "pending-adjudications.json")
    report_path = root / "integrity" / "validation-report.json"
    report = _read_json(report_path) if report_path.is_file() else {}
    task = manifest.get("task", {})
    task_id = task.get("name") or task.get("batch_id")
    if not isinstance(task_id, str) or not task_id:
        raise ValueError("Inner bundle task identity is missing")
    return BaseContext(
        root=root,
        manifest=manifest,
        package_id=manifest["package_id"],
        source_input_digest=manifest["source_input_digest"],
        task_id=task_id,
        models=models,
        rubrics=rubrics,
        evidence=evidence,
        pending_adjudications=tuple(pending_doc.get("items", [])),
        material_gaps=tuple(report.get("material_gaps", [])),
        human_check_pairs=frozenset(human_checks),
    )


__all__ = ["BaseContext", "load_base_context"]
