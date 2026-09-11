#!/usr/bin/env python3
"""Enforce observation provenance and closure rules for a v2 form-ready bundle.

Task scope: observation provenance (machine vision and remote human). Decision
coverage, score projection, and adjudication closure are enforced by the same
module once those records exist.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from form_ready_context import BaseContext, ObservationRegistry, load_observation_registry
from record_observation import (
    issue,
    validate_classification_record,
    validate_human_record,
    validate_vision_pair,
    validate_vision_record,
)


def adjudication_affected(context: BaseContext, model_id: str, rubric_id: str) -> bool:
    row = context.evidence.get((model_id, rubric_id))
    if not isinstance(row, dict):
        return False
    return bool(row.get("adjudication_ids"))


def evidence_direction(context: BaseContext, model_id: str, rubric_id: str) -> str | None:
    row = context.evidence.get((model_id, rubric_id))
    if not isinstance(row, dict):
        return None
    directions = {
        item.get("direction")
        for item in row.get("evidence", []) or []
        if isinstance(item, dict) and item.get("direction") != "context"
    }
    return directions.pop() if len(directions) == 1 else None


def _rubric_by_id(context: BaseContext) -> dict[str, dict[str, Any]]:
    return {row.get("id"): row for row in context.rubrics}


def validate_observation_provenance(
    root: str | Path,
    manifest: dict[str, Any],
    context: BaseContext,
    errors: list[dict[str, str]],
) -> ObservationRegistry:
    """Append one error per violated provenance rule; never repair a record."""

    registry = load_observation_registry(root, manifest)
    rubric_by_id = _rubric_by_id(context)

    for rubric_id, record in sorted(registry.classifications.items()):
        path = f"decisions/criterion-classifications.json#/{rubric_id}"
        errors.extend(validate_classification_record(
            record,
            rubric=rubric_by_id.get(rubric_id),
            adjudication_affected=any(
                adjudication_affected(context, model_id, rubric_id) for model_id in context.models
            ),
            path=path,
        ))

    vision_groups: dict[tuple[Any, Any], list[dict[str, Any]]] = {}
    for index, record in enumerate(registry.vision):
        path = f"observations/machine-vision.jsonl:{index + 1}"
        if not isinstance(record, dict):
            errors.append(issue("VISION_RECORD_INCOMPLETE", "Each vision record must be an object", path))
            continue
        errors.extend(validate_vision_record(
            record,
            media_roles=registry.media_roles,
            media_status=registry.media_status,
            path=path,
        ))
        vision_groups.setdefault((record.get("model_id"), record.get("rubric_id")), []).append(record)
    for (model_id, rubric_id), rows in sorted(vision_groups.items(), key=lambda item: (str(item[0][0]), str(item[0][1]))):
        errors.extend(validate_vision_pair(
            rows,
            classification=registry.classifications.get(rubric_id),
            adjudication_affected=adjudication_affected(context, model_id, rubric_id),
            evidence_direction=evidence_direction(context, model_id, rubric_id),
            path=f"observations/machine-vision.jsonl#{model_id}/{rubric_id}",
        ))

    for index, record in enumerate(registry.human):
        path = f"observations/remote-human.jsonl:{index + 1}"
        errors.extend(validate_human_record(
            record,
            media_roles=registry.media_roles,
            media_status=registry.media_status,
            path=path,
        ))
    return registry


__all__ = [
    "adjudication_affected", "evidence_direction", "validate_observation_provenance",
]
