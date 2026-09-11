#!/usr/bin/env python3
"""Own the outer decision workspace: exact slots, atomic records, deterministic audit.

Every model x rubric pair the sealed base package contains gets exactly one slot,
and every pending adjudication and material gap gets exactly one closure slot.
Recording a slot validates it against the live policy immediately, so a weak
agent cannot stage a decision that only fails at the end.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any

from form_ready_context import BaseContext, load_form_ready_base
from record_observation import parse_timestamp
from validate_final_decisions import (
    ACCEPTED_ACTORS,
    DECISION_JSON,
    REMOTE_HUMAN_ACTOR,
)

AUDIT_JSON = "decisions/adjudication-audit.json"
SCHEMA_VERSION = "2.0.0"
CLOSURE_FIELDS = ("final_policy", "decided_by", "decider", "decided_at")


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def _atomic_write(path: Path, text: str) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _envelope(manifest: dict[str, Any], context: BaseContext, rubric: dict[str, Any]) -> dict[str, Any]:
    return {
        "outer_package_id": manifest["outer_package_id"],
        "base_package_id": context.package_id,
        "base_zip_sha256": manifest["base"]["sha256"],
        "source_input_digest": context.source_input_digest,
        "task_id": context.task_id,
        "model_id": None,
        "rubric_id": rubric.get("id"),
        "round": rubric.get("round"),
        "criterion_sha256": rubric.get("criterion_sha256"),
    }


def _affected_pairs(context: BaseContext, adjudication_id: str) -> list[list[str]]:
    return sorted(
        [model_id, rubric_id]
        for (model_id, rubric_id), row in context.evidence.items()
        if adjudication_id in (row or {}).get("adjudication_ids", []) or []
    )


def build_skeleton(manifest: dict[str, Any], context: BaseContext) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return the exact decision skeleton and the exact closure skeleton."""

    rubrics = {row.get("id"): row for row in context.rubrics}
    records: list[dict[str, Any]] = []
    for (model_id, rubric_id) in sorted(context.evidence):
        rubric = rubrics.get(rubric_id)
        if rubric is None:
            raise ValueError(f"Sealed base has evidence for unknown rubric {rubric_id!r}")
        records.append({
            **_envelope(manifest, context, rubric),
            "model_id": model_id,
            "score": None,
            "decided_by": None,
            "reason": None,
            "evidence_ids": [],
            "decider": None,
            "decided_at": None,
        })
    adjudications = [
        {"adjudication_id": item.get("adjudication_id"), "status": "open", "resolution": None,
         "affected_pairs": _affected_pairs(context, item.get("adjudication_id"))}
        for item in context.pending_adjudications
    ]
    gaps = [{"gap_id": gap_id, "status": "open", "resolution": None} for gap_id in context.material_gaps]
    decisions = {
        "schema_version": SCHEMA_VERSION,
        "outer_package_id": manifest["outer_package_id"],
        "base_package_id": context.package_id,
        "base_zip_sha256": manifest["base"]["sha256"],
        "source_input_digest": context.source_input_digest,
        "task_id": context.task_id,
        "records": records,
    }
    audit = {
        "schema_version": SCHEMA_VERSION,
        "outer_package_id": manifest["outer_package_id"],
        "base_package_id": context.package_id,
        "records": sorted(adjudications + gaps, key=lambda row: str(row.get("adjudication_id") or row.get("gap_id"))),
    }
    return decisions, audit


def initialize_workspace(form_root: str | Path) -> dict[str, Any]:
    """Create the exact decision and closure slots, refusing to overwrite anything."""

    root = Path(form_root)
    manifest, context, _ = load_form_ready_base(root)
    decisions_path, audit_path = root / DECISION_JSON, root / AUDIT_JSON
    for path in (decisions_path, audit_path):
        if path.exists() or path.is_symlink():
            raise FileExistsError(f"Decision workspace already exists: {path}")
    decisions, audit = build_skeleton(manifest, context)
    decisions_path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(decisions_path, _dump(decisions))
    _atomic_write(audit_path, _dump(audit))
    return {"decisions": str(decisions_path), "audit": str(audit_path),
            "pairs": len(decisions["records"]), "closures": len(audit["records"])}


def _load_document(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or not isinstance(document.get("records"), list):
        raise ValueError(f"DECISION_DOCUMENT_INVALID: {path}")
    return document


def record_pair(form_root: str | Path, model_id: str, rubric_id: str, value: dict[str, Any]) -> dict[str, Any]:
    """Fill exactly one model x rubric slot after immediate policy validation."""

    root = Path(form_root)
    path = root / DECISION_JSON
    document = _load_document(path)
    matches = [row for row in document["records"] if row.get("model_id") == model_id and row.get("rubric_id") == rubric_id]
    if len(matches) != 1:
        raise ValueError(f"DECISION_SLOT_MISSING: {model_id}/{rubric_id} is not exactly one slot")
    slot = matches[0]
    candidate = {**slot, **value}
    candidate["model_id"], candidate["rubric_id"] = model_id, rubric_id
    mistakes = _policy_mistakes(root, candidate, model_id, rubric_id)
    if mistakes:
        raise ValueError("DECISION_POLICY_REJECTED: " + "; ".join(mistakes))
    document["records"][document["records"].index(slot)] = candidate
    _atomic_write(path, _dump(document))
    return candidate


def _policy_mistakes(root: Path, candidate: dict[str, Any], model_id: str, rubric_id: str) -> list[str]:
    from form_ready_context import load_observation_registry
    from validate_final_decisions import _validate_decision_record

    manifest, context, _ = load_form_ready_base(root)
    observations = load_observation_registry(root, manifest)
    errors, _ = _validate_decision_record(
        candidate, model_id=model_id, rubric_id=rubric_id, context=context,
        observations=observations, path=f"{DECISION_JSON}#/{model_id}/{rubric_id}",
    )
    return [f"{row['code']}: {row['message']}" for row in errors]


def record_closure(form_root: str | Path, *, adjudication_id: str | None = None, gap_id: str | None = None, value: dict[str, Any]) -> dict[str, Any]:
    """Fill exactly one adjudication or material-gap closure slot."""

    if bool(adjudication_id) == bool(gap_id):
        raise ValueError("CLOSURE_TARGET_INVALID")
    root = Path(form_root)
    path = root / AUDIT_JSON
    document = _load_document(path)
    key = "adjudication_id" if adjudication_id else "gap_id"
    wanted = adjudication_id or gap_id
    matches = [row for row in document["records"] if row.get(key) == wanted]
    if len(matches) != 1:
        raise ValueError(f"CLOSURE_SLOT_MISSING: {wanted} is not exactly one slot")
    slot = matches[0]
    candidate = {**slot, **value, key: wanted}
    if adjudication_id:
        mistakes = _adjudication_mistakes(root, candidate)
    else:
        mistakes = _gap_mistakes(candidate)
    if mistakes:
        raise ValueError("CLOSURE_POLICY_REJECTED: " + "; ".join(mistakes))
    candidate["status"] = "resolved"
    document["records"][document["records"].index(slot)] = candidate
    _atomic_write(path, _dump(document))
    return candidate


def _adjudication_mistakes(root: Path, candidate: dict[str, Any]) -> list[str]:
    manifest, context, _ = load_form_ready_base(root)
    mistakes = [f"{field} must be a nonempty string" for field in CLOSURE_FIELDS
                if not isinstance(candidate.get(field), str) or not candidate[field].strip()]
    if parse_timestamp(candidate.get("decided_at")) is None:
        mistakes.append("decided_at must be an RFC3339 timestamp")
    if candidate.get("decided_by") != REMOTE_HUMAN_ACTOR:
        mistakes.append("only a remote human resolution may close a pending adjudication")
    expected = _affected_pairs(context, candidate.get("adjudication_id"))
    if sorted(candidate.get("affected_pairs") or []) != expected:
        mistakes.append(f"affected_pairs must be exactly {expected}")
    scores = candidate.get("per_model_final_scores")
    if not isinstance(scores, dict):
        mistakes.append("per_model_final_scores must map every affected model/rubric to 0 or 1")
        return mistakes
    for model_id, rubric_id in expected:
        value = (scores.get(model_id) or {}).get(rubric_id) if isinstance(scores.get(model_id), dict) else None
        if value not in (0, 1) or isinstance(value, bool):
            mistakes.append(f"per_model_final_scores missing 0/1 for {model_id}/{rubric_id}")
    return mistakes


def _gap_mistakes(candidate: dict[str, Any]) -> list[str]:
    mistakes = [f"{field} must be a nonempty string" for field in ("reason", "decider", "decided_at")
                if not isinstance(candidate.get(field), str) or not candidate[field].strip()]
    if parse_timestamp(candidate.get("decided_at")) is None:
        mistakes.append("decided_at must be an RFC3339 timestamp")
    if candidate.get("decided_by") not in ACCEPTED_ACTORS:
        mistakes.append(f"decided_by must be one of {sorted(ACCEPTED_ACTORS)}")
    return mistakes


def finalize_audit(form_root: str | Path, *, policy_digest_source: dict[str, Any] | None = None) -> dict[str, Any]:
    """Rewrite the audit in its canonical, byte-reproducible form."""

    root = Path(form_root)
    path = root / AUDIT_JSON
    document = _load_document(path)
    records = []
    for row in document["records"]:
        normalized = {key: row[key] for key in sorted(row) if key != "policy_digest"}
        if normalized.get("adjudication_id") or normalized.get("gap_id"):
            normalized["policy_digest"] = hashlib.sha256(
                json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
        records.append(normalized)
    canonical = {
        **{key: document[key] for key in sorted(document) if key != "records"},
        "policy_digest_source": _digest_of(policy_digest_source) if policy_digest_source is not None else None,
        "records": sorted(records, key=lambda row: str(row.get("adjudication_id") or row.get("gap_id"))),
    }
    text = _dump({key: canonical[key] for key in sorted(canonical)})
    _atomic_write(path, text)
    return canonical


def _digest_of(value: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def command_initialize(args: argparse.Namespace) -> int:
    print(json.dumps(initialize_workspace(args.form_root), ensure_ascii=False))
    return 0


def command_record(args: argparse.Namespace) -> int:
    value = json.loads(Path(args.value_json).read_text(encoding="utf-8"))
    if args.adjudication or args.gap:
        result = record_closure(args.form_root, adjudication_id=args.adjudication, gap_id=args.gap, value=value)
    else:
        result = record_pair(args.form_root, args.model, args.rubric, value)
    print(json.dumps(result, ensure_ascii=False))
    return 0


def command_finalize(args: argparse.Namespace) -> int:
    print(json.dumps(finalize_audit(args.form_root), ensure_ascii=False))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    initialize = subparsers.add_parser("initialize", help="create the exact decision and closure slots")
    initialize.add_argument("--form-root", type=Path, required=True)
    initialize.set_defaults(handler=command_initialize)
    record = subparsers.add_parser("record", help="fill exactly one slot")
    record.add_argument("--form-root", type=Path, required=True)
    record.add_argument("--value-json", required=True)
    record.add_argument("--model", default=None)
    record.add_argument("--rubric", default=None)
    record.add_argument("--adjudication", default=None)
    record.add_argument("--gap", default=None)
    record.set_defaults(handler=command_record)
    finalize = subparsers.add_parser("finalize", help="write the canonical audit")
    finalize.add_argument("--form-root", type=Path, required=True)
    finalize.set_defaults(handler=command_finalize)
    args = parser.parse_args(argv)
    if args.command == "record" and not (args.adjudication or args.gap) and not (args.model and args.rubric):
        print("DECISION_SLOT_MISSING: pass --model/--rubric or --adjudication or --gap", file=sys.stderr)
        return 4
    try:
        return args.handler(args)
    except (OSError, UnicodeError, ValueError, KeyError, json.JSONDecodeError, FileExistsError) as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "AUDIT_JSON", "build_skeleton", "finalize_audit", "initialize_workspace",
    "record_closure", "record_pair",
]
