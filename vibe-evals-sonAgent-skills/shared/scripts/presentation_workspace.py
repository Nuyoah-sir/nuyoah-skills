#!/usr/bin/env python3
"""Collect every V2.1 semantic input in one validated, evidence-bound workspace.

The workspace is an auditable remote recommendation: each slot holds a machine
proposal with its basis and evidence, and the slots V2.1 treats as subjective
also require an interactive presentation attestation bound to that exact
proposal digest. Nothing here is ever labelled as a local human score.
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

from form_ready_context import load_form_ready_base, load_observation_registry
from record_observation import interactive_stdin_available, issue, parse_timestamp
from v21_labels import LABEL_SECTIONS, allowed_labels

PRESENTATION_JSON = "presentation/presentation-input.json"
SCHEMA_VERSION = "2.0.0"
TASK_SLOTS = ("ranking_reason", "maximum_difference", "capability_boundary", "difficulty_and_approach")
DIMENSIONS = ("G1", "G2", "G3", "S1", "A1", "R1")
MODEL_SLOTS = ("overall_impression", *DIMENSIONS, "pros", "cons", "style", "labels.pros", "labels.cons", "labels.style")
SUBJECTIVE_SLOTS = ("overall_impression", "G3", "style", "labels.pros", "labels.cons", "labels.style")
LABEL_SECTION_FOR_SLOT = {"labels.pros": "Pros", "labels.cons": "Cons", "labels.style": "Stylistic Fingerprints"}
SCORE_MIN, SCORE_MAX, SCORE_STEP = 0.0, 4.0, 0.5


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def _atomic_write(path: Path, text: str) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def slot_ids(models: list[str]) -> list[str]:
    identifiers = [f"task.{name}" for name in TASK_SLOTS]
    for model_id in models:
        identifiers.extend(f"model.{model_id}.{name}" for name in MODEL_SLOTS)
    return identifiers


def empty_slot(slot_id: str) -> dict[str, Any]:
    name = slot_id.split(".", 2)[2] if slot_id.startswith("model.") else slot_id.split(".", 1)[1]
    return {
        "slot": slot_id,
        "requires_attestation": name in SUBJECTIVE_SLOTS,
        "value": None,
        "basis": None,
        "evidence_ids": [],
        "recorder": None,
        "recorded_at": None,
        "proposal_digest": None,
        "attestation": None,
    }


def initialize_presentation(form_root: str | Path) -> dict[str, Any]:
    root = Path(form_root)
    manifest, context, _ = load_form_ready_base(root)
    target = root / PRESENTATION_JSON
    if target.exists() or target.is_symlink():
        raise FileExistsError(f"Presentation workspace already exists: {target}")
    document = {
        "schema_version": SCHEMA_VERSION,
        "outer_package_id": manifest["outer_package_id"],
        "base_package_id": context.package_id,
        "base_zip_sha256": manifest["base"]["sha256"],
        "source_input_digest": context.source_input_digest,
        "task_id": context.task_id,
        "decisions_digest": None,
        "scored_digests": {},
        "slots": {slot_id: empty_slot(slot_id) for slot_id in slot_ids(sorted(context.models))},
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(target, _dump(document))
    return {"slots": len(document["slots"]), "path": str(target)}


def _load(form_root: str | Path) -> tuple[Path, dict[str, Any]]:
    path = Path(form_root) / PRESENTATION_JSON
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or not isinstance(document.get("slots"), dict):
        raise ValueError("PRESENTATION_DOCUMENT_INVALID")
    return path, document


def _resolvable_evidence(root: Path, context, model_id: str | None, slot_id: str, cited: list[str]) -> list[str]:
    """Return the cited ids that do not resolve inside this package/model."""

    manifest = json.loads((root / "FORM-READY.json").read_text(encoding="utf-8"))
    observations = load_observation_registry(root, manifest)
    media = set(observations.media_roles)
    valid: set[str] = set()
    for (evidence_model, _rubric), row in context.evidence.items():
        for item in row.get("evidence", []) or []:
            if isinstance(item, dict) and isinstance(item.get("evidence_id"), str):
                valid.add(item["evidence_id"])
    valid |= {row.get("observation_id") for row in observations.vision if isinstance(row, dict)}
    valid |= {row.get("observation_id") for row in observations.human if isinstance(row, dict)}
    valid |= media
    unknown = [item for item in cited if item not in valid]
    if model_id is not None:
        scoped = {item for item in cited if item.startswith("EV-")}
        unknown.extend(sorted(
            item for item in scoped
            if not any(item in {e.get("evidence_id") for e in (row.get("evidence", []) or []) if isinstance(e, dict)}
                       for (owner, _rubric), row in context.evidence.items() if owner == model_id)
        ))
    return sorted(set(unknown))


def record_slot(form_root: str | Path, slot_id: str, candidate: dict[str, Any]) -> dict[str, Any]:
    """Record one machine proposal, validating it immediately against the package."""

    root = Path(form_root)
    path, document = _load(root)
    slot = document["slots"].get(slot_id)
    if not isinstance(slot, dict):
        raise ValueError(f"PRESENTATION_SLOT_UNKNOWN: {slot_id}")
    value, basis, evidence_ids = candidate.get("value"), candidate.get("basis"), candidate.get("evidence_ids")
    mistakes: list[str] = []
    if value is None:
        mistakes.append("value must not be null")
    if not isinstance(basis, str) or not basis.strip():
        mistakes.append("basis must be a nonempty string")
    if not isinstance(evidence_ids, list) or not evidence_ids or any(not isinstance(item, str) or not item for item in evidence_ids):
        mistakes.append("evidence_ids must be a nonempty list of ids")
    recorder = candidate.get("recorder")
    if not isinstance(recorder, str) or not recorder.strip():
        mistakes.append("recorder must name the machine that proposed the value")
    recorded_at = candidate.get("recorded_at")
    if parse_timestamp(recorded_at) is None:
        mistakes.append("recorded_at must be an RFC3339 timestamp")
    if mistakes:
        raise ValueError("PRESENTATION_RECORD_REJECTED: " + "; ".join(mistakes))

    manifest, context, _ = load_form_ready_base(root)
    model_id = slot_id.split(".")[1] if slot_id.startswith("model.") else None
    unknown = _resolvable_evidence(root, context, model_id, slot_id, evidence_ids)
    if unknown:
        raise ValueError(f"PRESENTATION_RECORD_REJECTED: evidence does not resolve in this package: {unknown}")
    body = {"slot": slot_id, "value": value, "basis": basis, "evidence_ids": sorted(set(evidence_ids)), "recorder": recorder, "recorded_at": recorded_at}
    slot.update(body)
    slot["attestation"] = None
    slot["proposal_digest"] = _digest(body)
    _atomic_write(path, _dump(document))
    return slot


def record_attestation(form_root: str | Path, slot_id: str, *, observer: str, confirmation_text: str) -> dict[str, Any]:
    """Bind one interactive human attestation to the slot's exact proposal digest."""

    root = Path(form_root)
    path, document = _load(root)
    slot = document["slots"].get(slot_id)
    if not isinstance(slot, dict):
        raise ValueError(f"PRESENTATION_SLOT_UNKNOWN: {slot_id}")
    if not slot.get("proposal_digest"):
        raise ValueError("PRESENTATION_ATTESTATION_WITHOUT_PROPOSAL")
    if not isinstance(observer, str) or not observer.strip():
        raise ValueError("PRESENTATION_ATTESTATION_REJECTED: observer must be a stable alias")
    if not isinstance(confirmation_text, str) or not confirmation_text.strip():
        raise ValueError("PRESENTATION_ATTESTATION_REJECTED: refusing to invent a confirmation")
    slot["attestation"] = {
        "attestation_id": f"PRES-HUM-{uuid.uuid4()}",
        "subject": slot_id,
        "proposal_digest": slot["proposal_digest"],
        "observer": observer,
        "capture_method": "interactive_terminal",
        "attested_at": _now(),
        "confirmation_text": confirmation_text,
    }
    _atomic_write(path, _dump(document))
    return slot["attestation"]


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _value_mistakes(slot: dict[str, Any], shared_root: Path, slot_id: str) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    name = slot_id.split(".", 2)[2] if slot_id.startswith("model.") else slot_id.split(".", 1)[1]
    value = slot.get("value")
    if name in DIMENSIONS:
        if not isinstance(value, dict) or not isinstance(value.get("applicable"), bool):
            errors.append(issue("PRESENTATION_SLOT_VALUE_INVALID", "A dimension needs {applicable, score?, basis}", slot_id))
            return errors
        if value["applicable"] is False:
            if "不适用" not in str(value.get("basis", "")):
                errors.append(issue("PRESENTATION_NOT_APPLICABLE_BASIS", "A non-applicable dimension must state 不适用【不适用：依据】", slot_id))
        else:
            score = value.get("score")
            if not isinstance(score, (int, float)) or isinstance(score, bool):
                errors.append(issue("PRESENTATION_SCORE_INVALID", "An applicable dimension needs a numeric score", slot_id))
            elif not (SCORE_MIN <= score <= SCORE_MAX) or round((score - SCORE_MIN) / SCORE_STEP) * SCORE_STEP != score:
                errors.append(issue("PRESENTATION_SCORE_INVALID", "Scores must be 0-4 in 0.5 increments", slot_id))
    elif name == "overall_impression":
        score = value
        if not isinstance(score, (int, float)) or isinstance(score, bool) or not (SCORE_MIN <= score <= SCORE_MAX) or round(score / SCORE_STEP) * SCORE_STEP != score:
            errors.append(issue("PRESENTATION_SCORE_INVALID", "Overall impression must be 0-4 in 0.5 increments", slot_id))
    elif name == "pros":
        if not isinstance(value, list) or len(value) != 2 or any(not isinstance(item, str) or not item.strip() for item in value):
            errors.append(issue("PRESENTATION_PROS_INVALID", "Exactly two nonempty pros are required", slot_id))
    elif name == "cons":
        if not isinstance(value, list) or len(value) > 5 or any(not isinstance(item, str) or not item.strip() for item in value):
            errors.append(issue("PRESENTATION_CONS_INVALID", "Cons must be zero to five nonempty strings", slot_id))
    elif name == "style":
        if not isinstance(value, str) or not value.strip():
            errors.append(issue("PRESENTATION_SLOT_VALUE_INVALID", "Style must be one nonempty sentence", slot_id))
    elif name.startswith("labels."):
        section = LABEL_SECTION_FOR_SLOT[name]
        legal = allowed_labels(shared_root)[section]
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            errors.append(issue("PRESENTATION_LABELS_INVALID", "Labels must be a list of strings", slot_id))
        else:
            illegal = sorted(set(value) - legal)
            if illegal:
                errors.append(issue("PRESENTATION_LABEL_UNSUPPORTED", f"Labels outside the V2.1 library: {illegal}", slot_id))
    elif not isinstance(value, str) or not value.strip():
        errors.append(issue("PRESENTATION_SLOT_VALUE_INVALID", "Task slots must be nonempty strings", slot_id))
    return errors


def validate_presentation(form_root: str | Path, shared_root: str | Path | None = None) -> dict[str, Any]:
    """Return per-slot errors and the single ``presentation_complete`` verdict."""

    root = Path(form_root)
    shared = Path(shared_root) if shared_root is not None else Path(__file__).resolve().parents[1]
    errors: list[dict[str, str]] = []
    try:
        _, document = _load(root)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        return {"presentation_complete": False, "errors": [issue("PRESENTATION_DOCUMENT_INVALID", str(exc), PRESENTATION_JSON)]}
    expected = set(slot_ids(sorted(load_form_ready_base(root)[1].models)))
    actual = set(document["slots"])
    if expected != actual:
        errors.append(issue("PRESENTATION_SLOTS_MISMATCH", f"Slot set differs: missing={sorted(expected - actual)} extra={sorted(actual - expected)}", PRESENTATION_JSON))
    for slot_id, slot in sorted(document["slots"].items()):
        if not isinstance(slot, dict):
            errors.append(issue("PRESENTATION_SLOT_INVALID", "Slot must be an object", slot_id))
            continue
        if slot.get("value") is None or slot.get("proposal_digest") is None:
            errors.append(issue("PRESENTATION_SLOT_EMPTY", "Slot has no machine proposal yet", slot_id))
            continue
        if not isinstance(slot.get("basis"), str) or not slot["basis"].strip():
            errors.append(issue("PRESENTATION_BASIS_MISSING", "Every proposal needs a written basis", slot_id))
        if not slot.get("evidence_ids"):
            errors.append(issue("PRESENTATION_EVIDENCE_MISSING", "Every proposal needs at least one evidence id", slot_id))
        errors.extend(_value_mistakes(slot, shared, slot_id))
        body = {key: slot.get(key) for key in ("slot", "value", "basis", "evidence_ids", "recorder", "recorded_at")}
        body["evidence_ids"] = sorted(set(slot.get("evidence_ids") or []))
        if slot.get("proposal_digest") != _digest(body):
            errors.append(issue("PRESENTATION_PROPOSAL_DRIFT", "Proposal digest does not match the recorded proposal", slot_id))
        if slot.get("requires_attestation"):
            attestation = slot.get("attestation")
            if not isinstance(attestation, dict) or attestation.get("proposal_digest") != slot.get("proposal_digest"):
                errors.append(issue("PRESENTATION_ATTESTATION_REQUIRED", "This subjective slot needs a matching presentation attestation", slot_id))
            elif parse_timestamp(attestation.get("attested_at")) is None or not str(attestation.get("observer", "")).strip():
                errors.append(issue("PRESENTATION_ATTESTATION_INVALID", "Attestation needs an observer alias and timestamp", slot_id))
    return {"presentation_complete": not errors, "errors": errors}


def _print(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    initialize = subparsers.add_parser("initialize")
    initialize.add_argument("--form-root", type=Path, required=True)
    record = subparsers.add_parser("record")
    record.add_argument("--form-root", type=Path, required=True)
    record.add_argument("--slot", required=True)
    record.add_argument("--value-json", required=True)
    attest = subparsers.add_parser("attest")
    attest.add_argument("--form-root", type=Path, required=True)
    attest.add_argument("--slot", required=True)
    attest.add_argument("--observer", required=True)
    check = subparsers.add_parser("validate")
    check.add_argument("--form-root", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "initialize":
            _print(initialize_presentation(args.form_root))
        elif args.command == "record":
            _print(record_slot(args.form_root, args.slot, json.loads(Path(args.value_json).read_text(encoding="utf-8"))))
        elif args.command == "attest":
            if not interactive_stdin_available():
                print("PRESENTATION_ATTESTATION_NOT_INTERACTIVE: run this in an interactive terminal", file=sys.stderr)
                return 3
            print("Type the human's original confirmation words, then press Enter:")
            _print(record_attestation(args.form_root, args.slot, observer=args.observer, confirmation_text=input()))
        else:
            report = validate_presentation(args.form_root)
            _print(report)
            return 0 if report["presentation_complete"] else 2
    except (OSError, UnicodeError, ValueError, KeyError, json.JSONDecodeError, FileExistsError) as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DIMENSIONS", "MODEL_SLOTS", "PRESENTATION_JSON", "TASK_SLOTS", "empty_slot",
    "initialize_presentation", "record_attestation", "record_slot", "slot_ids",
    "validate_presentation",
]
