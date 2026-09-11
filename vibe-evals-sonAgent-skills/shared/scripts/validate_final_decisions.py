#!/usr/bin/env python3
"""Enforce observation provenance and closure rules for a v2 form-ready bundle.

Task scope: observation provenance (machine vision and remote human). Decision
coverage, score projection, and adjudication closure are enforced by the same
module once those records exist.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from form_ready_context import BaseContext, ObservationRegistry, load_observation_registry
from record_observation import (
    issue,
    parse_timestamp,
    validate_classification_record,
    validate_human_record,
    validate_vision_pair,
    validate_vision_record,
)

DECISION_JSON = "decisions/final-decisions.json"
AUDIT_JSON = "decisions/adjudication-audit.json"
GAP_POLICY_JSON = "references/gap-policy.json"

MECHANICAL_ACTOR = "mechanical"
ACCEPTED_MACHINE_ACTOR = "accepted_machine"
MACHINE_VISION_ACTOR = "machine_vision"
REMOTE_HUMAN_ACTOR = "remote_human"
ACCEPTED_ACTORS = (ACCEPTED_MACHINE_ACTOR, MECHANICAL_ACTOR, MACHINE_VISION_ACTOR, REMOTE_HUMAN_ACTOR)

DECISION_REQUIRED = ("score", "decided_by", "reason", "evidence_ids", "decider", "decided_at")
NON_DETERMINISTIC_EVIDENCE_TYPES = frozenset({
    "human_note", "human_observation", "machine_vision", "visual_observation",
})
DEFAULT_GAP_POLICY = {
    "schema_version": "2.0.0",
    "known_optional": {},
    "required": [],
    "semantic": [],
    "unknown_policy": "remote_human_with_limitation",
}

_ACTOR_RANK = {ACTOR: index for index, ACTOR in enumerate(ACCEPTED_ACTORS)}


def accepted_actor_values() -> set[str]:
    return set(ACCEPTED_ACTORS)


def load_gap_policy(path: str | Path | None = None) -> dict[str, Any]:
    """Load the executable gap policy. Free text never decides optionality."""

    if path is None:
        return dict(DEFAULT_GAP_POLICY)
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError("GAP_POLICY_INVALID")
    policy = dict(DEFAULT_GAP_POLICY)
    policy.update({key: document[key] for key in ("known_optional", "required", "semantic", "unknown_policy") if key in document})
    for key in ("known_optional", "required", "semantic"):
        policy[key] = policy[key] or ({} if key == "known_optional" else [])
    return policy


def _rubric_by_id(context: BaseContext) -> dict[str, dict[str, Any]]:
    return {row.get("id"): row for row in context.rubrics}


def _pair_evidence(context: BaseContext, model_id: str, rubric_id: str) -> dict[str, dict[str, Any]]:
    row = context.evidence.get((model_id, rubric_id))
    if not isinstance(row, dict):
        return {}
    return {
        item.get("evidence_id"): item
        for item in row.get("evidence", []) or []
        if isinstance(item, dict) and isinstance(item.get("evidence_id"), str)
    }


def _pair_observations(observations: ObservationRegistry, model_id: str, rubric_id: str) -> tuple[list[dict], list[dict]]:
    vision = [row for row in observations.vision if isinstance(row, dict) and row.get("model_id") == model_id and row.get("rubric_id") == rubric_id]
    human = [row for row in observations.human if isinstance(row, dict) and row.get("model_id") == model_id and row.get("rubric_id") == rubric_id]
    return vision, human


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


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _audit_closure(decisions_path: Path, audit_path: Path | None) -> tuple[dict[str, dict], dict[str, dict]]:
    """Return adjudication and material-gap closures declared in the audit file."""

    target = audit_path or decisions_path.parent / "adjudication-audit.json"
    if not target.is_file():
        return {}, {}
    document = _read_json(target)
    adjudications: dict[str, dict] = {}
    gaps: dict[str, dict] = {}
    for record in (document.get("records", []) if isinstance(document, dict) else []):
        if not isinstance(record, dict):
            continue
        if record.get("adjudication_id"):
            adjudications[record["adjudication_id"]] = record
        if record.get("gap_id"):
            gaps[record["gap_id"]] = record
    return adjudications, gaps


def _check_cited_evidence(
    decision: dict[str, Any],
    *,
    evidence: dict[str, dict[str, Any]],
    actor: str,
    path: str,
) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    cited = [item for item in decision.get("evidence_ids", []) if isinstance(item, str)]
    unknown = [item for item in cited if item.startswith("EV-") and item not in evidence]
    if unknown:
        errors.append(issue("DECISION_EVIDENCE_UNKNOWN", f"{actor} cites unknown inner evidence: {sorted(set(unknown))}", path))
        return errors
    score = decision.get("score")
    directions = {evidence[item].get("direction") for item in cited if item in evidence}
    types = {evidence[item].get("type") for item in cited if item in evidence}
    if actor == MECHANICAL_ACTOR and (types & NON_DETERMINISTIC_EVIDENCE_TYPES):
        errors.append(issue("DECISION_ACTOR_UNSUPPORTED", f"{actor} cannot rely on non-deterministic evidence types", path))
    if not directions:
        errors.append(issue("DECISION_EVIDENCE_MISSING", f"{actor} requires at least one supporting inner evidence record", path))
    elif score == 1 and "support" not in directions:
        errors.append(issue("DECISION_EVIDENCE_DIRECTION", "A score of 1 requires support-direction evidence", path))
    elif score == 0 and not ({"refute", "confirm_missing"} & directions):
        errors.append(issue("DECISION_EVIDENCE_DIRECTION", "A score of 0 requires refute or confirm_missing evidence", path))
    return errors


def _validate_decision_record(
    decision: Any,
    *,
    model_id: str,
    rubric_id: str,
    context: BaseContext,
    observations: ObservationRegistry,
    path: str,
) -> tuple[list[dict[str, str]], bool]:
    """Validate one decision row. Returns (errors, counts_as_resolved)."""

    if not isinstance(decision, dict):
        return [issue("DECISION_RECORD_INVALID", "Each decision must be an object", path)], False
    errors: list[dict[str, str]] = []
    missing = sorted(key for key in DECISION_REQUIRED if key not in decision)
    if missing:
        return [issue("DECISION_RECORD_INCOMPLETE", f"Decision is missing fields: {missing}", path)], False
    actor = decision.get("decided_by")
    if actor not in ACCEPTED_ACTORS:
        return [issue("DECISION_ACTOR_INVALID", f"decided_by must be one of {sorted(ACCEPTED_ACTORS)}", path)], False
    if decision.get("score") not in (0, 1) or isinstance(decision.get("score"), bool):
        errors.append(issue("DECISION_SCORE_INVALID", "score must be exactly 0 or 1; null is never a score", path))
    if not isinstance(decision.get("reason"), str) or not decision["reason"].strip():
        errors.append(issue("DECISION_RECORD_INCOMPLETE", "reason must be a nonempty string", path))
    if not isinstance(decision.get("decider"), str) or not decision["decider"].strip():
        errors.append(issue("DECISION_RECORD_INCOMPLETE", "decider must be a nonempty string", path))
    if parse_timestamp(decision.get("decided_at")) is None:
        errors.append(issue("DECISION_RECORD_INCOMPLETE", "decided_at must be an RFC3339 timestamp", path))
    cited = decision.get("evidence_ids")
    if not isinstance(cited, list) or not cited or any(not isinstance(item, str) or not item for item in cited):
        errors.append(issue("DECISION_RECORD_INCOMPLETE", "evidence_ids must be a nonempty list of ids", path))
        cited = []
    if decision.get("model_id") != model_id or decision.get("rubric_id") != rubric_id:
        errors.append(issue("DECISION_PAIR_MISMATCH", "Decision model_id/rubric_id must match its coverage slot", path))
    inner = context.evidence.get((model_id, rubric_id)) or {}
    rubric = _rubric_by_id(context).get(rubric_id) or {}
    if decision.get("round") != rubric.get("round"):
        errors.append(issue("DECISION_PAIR_MISMATCH", "Decision round must match the rubric round", path))
    evidence = _pair_evidence(context, model_id, rubric_id)
    vision, human = _pair_observations(observations, model_id, rubric_id)
    classification = observations.classifications.get(rubric_id) or {}
    adjudication_ids = [item for item in inner.get("adjudication_ids", []) or [] if isinstance(item, str)]
    human_check = inner.get("human_check_needed") is True

    if actor == MECHANICAL_ACTOR:
        if human_check:
            errors.append(issue("DECISION_ACTOR_UNSUPPORTED", "mechanical cannot close a rubric that needs a human check", path))
        if adjudication_ids:
            errors.append(issue("DECISION_ACTOR_UNSUPPORTED", "mechanical cannot close a pending adjudication", path))
        if classification.get("classification") in (None, "subjective_or_policy"):
            errors.append(issue("DECISION_ACTOR_UNSUPPORTED", "mechanical requires an objective criterion classification", path))
        errors.extend(_check_cited_evidence(decision, evidence=evidence, actor=actor, path=path))
    elif actor == ACCEPTED_MACHINE_ACTOR:
        suggested = inner.get("suggested_score")
        if suggested not in (0, 1) or isinstance(suggested, bool):
            errors.append(issue("DECISION_ACTOR_UNSUPPORTED", "accepted_machine requires an inner suggested score of 0 or 1", path))
        elif decision.get("score") != suggested:
            errors.append(issue("DECISION_ACTOR_UNSUPPORTED", "accepted_machine must equal the inner suggested score", path))
        if human_check or adjudication_ids:
            errors.append(issue("DECISION_ACTOR_UNSUPPORTED", "accepted_machine cannot close a human check or pending adjudication", path))
        errors.extend(_check_cited_evidence(decision, evidence=evidence, actor=actor, path=path))
    elif actor == MACHINE_VISION_ACTOR:
        if classification.get("classification") in (None, "subjective_or_policy"):
            errors.append(issue("DECISION_ACTOR_UNSUPPORTED", "machine_vision requires an objective criterion classification", path))
        if not any(item.startswith("VIS-") and item in {row.get("observation_id") for row in vision} for item in cited):
            errors.append(issue("DECISION_EVIDENCE_MISSING", "machine_vision must cite the audited VIS observations", path))
        elif len(vision) != 2 or any(row.get("verdict") != decision.get("score") for row in vision):
            errors.append(issue("DECISION_ACTOR_UNSUPPORTED", "the qualified VIS pair must agree with the decision score", path))
    else:
        matching = [row for row in human if row.get("verdict") == decision.get("score")]
        if not matching:
            errors.append(issue("DECISION_EVIDENCE_MISSING", "remote_human requires a matching HUM attestation for this pair", path))
        if not any(item.startswith("HUM-") and item in {row.get("observation_id") for row in human} for item in cited):
            errors.append(issue("DECISION_EVIDENCE_MISSING", "evidence_ids must cite the HUM attestation by id", path))
    return errors, not errors


def validate_final_decisions(
    base_ctx: BaseContext,
    media_registry: ObservationRegistry,
    observations: ObservationRegistry,
    decisions_path: str | Path,
    *,
    audit_path: str | Path | None = None,
    gap_policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Enforce exact coverage, the four-actor decision matrix, and closure."""

    path = Path(decisions_path)
    errors: list[dict[str, str]] = []
    policy = gap_policy or DEFAULT_GAP_POLICY
    registry: dict[tuple[str, str], dict[str, Any]] = {}
    try:
        document = _read_json(path)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return {
            "result": "fail",
            "errors": [issue("DECISIONS_UNREADABLE", f"Cannot read final decisions: {exc}", str(path))],
            "registry": {},
            "unresolved": {"scores": len(base_ctx.evidence), "adjudications": 0, "human_checks": 0, "material_gaps": len(base_ctx.material_gaps)},
        }
    records = document.get("records", []) if isinstance(document, dict) else []
    for index, record in enumerate(records if isinstance(records, list) else []):
        if isinstance(record, dict) and isinstance(record.get("model_id"), str) and isinstance(record.get("rubric_id"), str):
            key = (record["model_id"], record["rubric_id"])
            if key in registry:
                errors.append(issue("DECISION_DUPLICATE", f"Duplicate decision for {key}", f"{DECISION_JSON}#/records/{index}"))
                continue
            registry[key] = record
            row_errors, _ = _validate_decision_record(
                record, model_id=key[0], rubric_id=key[1],
                context=base_ctx, observations=observations, path=f"{DECISION_JSON}#/records/{index}",
            )
            errors.extend(row_errors)
        else:
            errors.append(issue("DECISION_RECORD_INVALID", "Each decision must name a model_id and rubric_id", f"{DECISION_JSON}#/records/{index}"))
    expected = set(base_ctx.evidence)
    missing = sorted(expected - set(registry))
    unexpected = sorted(set(registry) - expected)
    if missing:
        errors.append(issue("DECISION_COVERAGE_MISSING", f"Missing decisions for {len(missing)} model/rubric pairs", DECISION_JSON))
    if unexpected:
        errors.append(issue("DECISION_COVERAGE_UNEXPECTED", f"Decisions reference unknown pairs: {unexpected}", DECISION_JSON))

    adjudication_closures, gap_closures = _audit_closure(path, Path(audit_path) if audit_path else None)
    unresolved_adjudications = 0
    for item in base_ctx.pending_adjudications:
        adj_id = item.get("adjudication_id")
        affected = [
            (model_id, rubric_id) for (model_id, rubric_id) in expected
            if adj_id in (base_ctx.evidence.get((model_id, rubric_id)) or {}).get("adjudication_ids", [])
        ]
        closure = adjudication_closures.get(adj_id)
        resolved_pairs = {tuple(pair) for pair in (closure or {}).get("affected_pairs", [])} if isinstance((closure or {}).get("affected_pairs"), list) else set()
        if (closure or {}).get("resolution") not in (0, 1) or resolved_pairs != set(affected):
            unresolved_adjudications += 1

    unresolved_gaps = 0
    for gap_id in base_ctx.material_gaps:
        closure = gap_closures.get(gap_id)
        if gap_id in {str(value) for value in policy.get("required", [])}:
            unresolved_gaps += 1
            continue
        if gap_id in (policy.get("known_optional") or {}):
            if not isinstance(closure, dict) or closure.get("precondition_satisfied") is not True:
                unresolved_gaps += 1
            continue
        limitation = str((closure or {}).get("limitation", "")).strip()
        if not isinstance(closure, dict) or closure.get("decided_by") != REMOTE_HUMAN_ACTOR or not limitation:
            unresolved_gaps += 1
    required_gaps = {str(value) for value in policy.get("required", [])}
    known_optional = policy.get("known_optional") or {}
    for gap_id, closure in sorted(gap_closures.items()):
        if gap_id in required_gaps:
            errors.append(issue("GAP_POLICY_REQUIRED", f"{gap_id} is a required gap and can never be closed", AUDIT_JSON))
        elif gap_id not in known_optional and closure.get("decided_by") != REMOTE_HUMAN_ACTOR:
            errors.append(issue("GAP_POLICY_SEMANTIC", f"{gap_id} is not a known optional gap, so it requires a remote human plus a limitation", AUDIT_JSON))
        if gap_id not in set(base_ctx.material_gaps):
            errors.append(issue("GAP_UNKNOWN", f"{gap_id} is not a material gap of the sealed base package", AUDIT_JSON))

    unresolved_human = sum(
        1 for (model_id, rubric_id) in expected
        if (base_ctx.evidence.get((model_id, rubric_id)) or {}).get("human_check_needed") is True
        and (registry.get((model_id, rubric_id)) or {}).get("decided_by") != REMOTE_HUMAN_ACTOR
    )
    unresolved_scores = len(missing)
    unresolved = {
        "scores": unresolved_scores,
        "adjudications": unresolved_adjudications,
        "human_checks": unresolved_human,
        "material_gaps": unresolved_gaps,
    }
    if unresolved_scores:
        errors.append(issue("DECISION_UNRESOLVED_SCORES", f"{unresolved_scores} model/rubric pairs have no closing decision", DECISION_JSON))
    if unresolved_human:
        errors.append(issue("DECISION_UNRESOLVED_HUMAN_CHECK", f"{unresolved_human} human-check pairs lack a remote human decision", DECISION_JSON))
    if unresolved_adjudications:
        errors.append(issue("DECISION_UNRESOLVED_ADJUDICATION", f"{unresolved_adjudications} pending adjudications are not closed", AUDIT_JSON))
    if unresolved_gaps:
        errors.append(issue("DECISION_UNRESOLVED_MATERIAL_GAP", f"{unresolved_gaps} material gaps are not closed", AUDIT_JSON))
    return {
        "result": "pass" if not errors else "fail",
        "errors": errors,
        "registry": registry,
        "unresolved": unresolved,
    }


__all__ = [
    "ACCEPTED_ACTORS", "accepted_actor_values", "adjudication_affected", "evidence_direction",
    "load_gap_policy", "validate_final_decisions", "validate_observation_provenance",
]
