#!/usr/bin/env python3
"""Merge worker evidence batches into the canonical v1 evidence JSONL.

A worker only ever writes an invocation-owned candidate file. This module checks
the batch against the frozen model/round/rubric identity, refuses duplicates and
stale rows, merges atomically, and immediately re-runs the v1 validator, restoring
the previous bytes when the merged bundle would not validate.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any

from validate_bundle import DIRECTIONS, validate_bundle

DISPOSITIONS = {"examined", "not_examined", "missing", "unsafe_to_test"}
COVERAGES = {"complete", "partial", "missing", "unsafe_to_test"}
CONFIDENCES = {"high", "medium", "low"}
REASON_CODES = {
    "implemented_and_verified", "implemented_static_only", "behavior_failed",
    "missing_implementation", "violates_explicit_constraint", "insufficient_evidence",
    "subjective_needs_human", "unsafe_to_test", "policy_ambiguous", "model_fallback_zero",
}
EVIDENCE_REQUIRED = ("evidence_id", "type", "direction")


class EvidenceRejected(ValueError):
    """The candidate batch does not satisfy the frozen evidence contract."""

    def __init__(self, errors: list[dict[str, str]]):
        super().__init__(json.dumps(errors, ensure_ascii=False))
        self.errors = errors


def _issue(code: str, message: str, path: str) -> dict[str, str]:
    return {"code": code, "message": message, "path": path}


def derived_status_ignoring_declared(report: dict) -> str:
    """Derive the status the bundle would have if its declared status were correct.

    ``validate_bundle`` folds its own STATUS_MISMATCH error into ``derived_status``,
    which makes that field useless for converging the declared value, so recompute
    it from the counts instead.
    """

    blocking = [row for row in report.get("errors", []) if row.get("code") != "STATUS_MISMATCH"]
    if blocking:
        return "incomplete"
    counts = report.get("counts", {}) or {}
    unresolved = sum(
        int(counts.get(key, 0) or 0)
        for key in ("pending_adjudications", "unknown_scores", "human_checks", "material_gaps")
    )
    return "ready_for_local_review" if unresolved else "ready_for_form"


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[Any]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _baseline_rubrics(root: Path, manifest: dict) -> list[str]:
    rubric_ids: list[str] = []
    for entry in sorted(manifest.get("inputs", {}).get("rubrics", []), key=lambda item: item.get("round", 0)):
        rubric_ids.extend(row["id"] for row in _read_json(root / entry["path"]))
    return rubric_ids


def _existing_evidence_ids(root: Path, manifest: dict) -> set[str]:
    seen: set[str] = set()
    for model in manifest.get("models", []):
        for row in _read_jsonl(root / model["directory"] / "rubric-evidence.jsonl"):
            for item in row.get("evidence", []) or []:
                if isinstance(item, dict) and isinstance(item.get("evidence_id"), str):
                    seen.add(item["evidence_id"])
    return seen


def validate_evidence_batch(root: Path, rows: list[Any]) -> list[dict[str, str]]:
    """Return every contract violation in the candidate batch."""

    errors: list[dict[str, str]] = []
    manifest = _read_json(root / "MANIFEST.json")
    model_ids = {row["model_id"] for row in manifest.get("models", [])}
    rubric_ids = _baseline_rubrics(root, manifest)
    known_evidence = _existing_evidence_ids(root, manifest)
    seen_pairs: set[tuple[str, str]] = set()
    seen_evidence: set[str] = set()
    for index, row in enumerate(rows):
        where = f"candidate#{index}"
        if not isinstance(row, dict):
            errors.append(_issue("EVIDENCE_BATCH_INVALID", "Each evidence record must be an object", where))
            continue
        model_id, rubric_id = row.get("model_id"), row.get("rubric_id")
        if model_id not in model_ids:
            errors.append(_issue("EVIDENCE_MODEL_UNKNOWN", f"{model_id!r} is not a frozen model", where))
            continue
        if rubric_id not in rubric_ids:
            errors.append(_issue("EVIDENCE_RUBRIC_UNKNOWN", f"{rubric_id!r} is not a frozen rubric", where))
            continue
        pair = (model_id, rubric_id)
        if pair in seen_pairs:
            errors.append(_issue("EVIDENCE_DUPLICATE", f"{model_id}/{rubric_id} appears twice in this batch", where))
            continue
        seen_pairs.add(pair)
        for field, allowed, code in (("disposition", DISPOSITIONS, "EVIDENCE_ROW_ENUM_INVALID"),
                                     ("coverage", COVERAGES, "EVIDENCE_ROW_ENUM_INVALID"),
                                     ("confidence", CONFIDENCES, "EVIDENCE_ROW_ENUM_INVALID"),
                                     ("reason_code", REASON_CODES, "EVIDENCE_ROW_ENUM_INVALID")):
            if row.get(field) not in allowed:
                errors.append(_issue(code, f"{model_id}/{rubric_id} has invalid {field}={row.get(field)!r}", where))
        score = row.get("suggested_score")
        if score not in (0, 1, None) or isinstance(score, bool):
            errors.append(_issue("EVIDENCE_SCORE_INVALID", f"{model_id}/{rubric_id} suggested_score must be 0, 1, or null", where))
        if not str(row.get("fact_summary", "")).strip():
            errors.append(_issue("FACT_SUMMARY_MISSING", f"{model_id}/{rubric_id} needs a non-empty fact_summary", where))
        if not isinstance(row.get("human_check_needed"), bool) or not isinstance(row.get("adjudication_ids"), list) or not isinstance(row.get("limitations"), list):
            errors.append(_issue("EVIDENCE_ROW_TYPE_INVALID", f"{model_id}/{rubric_id} needs boolean/list state fields", where))
        evidence = row.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            errors.append(_issue("EVIDENCE_ITEMS_MISSING", f"{model_id}/{rubric_id} needs at least one evidence item", where))
            continue
        for item_index, item in enumerate(evidence):
            item_where = f"{where}/evidence/{item_index}"
            if not isinstance(item, dict):
                errors.append(_issue("EVIDENCE_ITEM_INVALID", "Evidence item must be an object", item_where))
                continue
            missing = [key for key in EVIDENCE_REQUIRED if not str(item.get(key, "")).strip()]
            if missing:
                errors.append(_issue("EVIDENCE_ITEM_INVALID", f"Evidence item is missing {missing}", item_where))
                continue
            if item.get("direction") not in DIRECTIONS:
                errors.append(_issue("EVIDENCE_DIRECTION_INVALID", f"Invalid direction {item.get('direction')!r}", item_where))
            evidence_id = item["evidence_id"]
            if evidence_id in known_evidence or evidence_id in seen_evidence:
                errors.append(_issue("EVIDENCE_DUPLICATE_ID", f"{evidence_id!r} already exists or repeats", item_where))
            seen_evidence.add(evidence_id)
    if not rows:
        errors.append(_issue("EVIDENCE_BATCH_EMPTY", "Candidate batch holds no records", "candidate"))
    return errors


def merge_evidence_batch(root: Path, manifest: dict, rows: list[dict]) -> tuple[list[Path], dict[Path, bytes | None], list[tuple[Path, Path]]]:
    """Stage the merged JSONL files for each model named in the batch."""

    grouped: dict[str, list[dict]] = {}
    for row in rows:
        grouped.setdefault(row["model_id"], []).append(dict(row))
    previous: dict[Path, bytes | None] = {}
    staged: list[tuple[Path, Path]] = []
    for model in manifest.get("models", []):
        model_id = model["model_id"]
        if model_id not in grouped:
            continue
        target = root / model["directory"] / "rubric-evidence.jsonl"
        previous[target] = target.read_bytes() if target.is_file() else None
        existing = [row for row in _read_jsonl(target)]
        updates = {row["rubric_id"]: row for row in grouped[model_id]}
        merged = [updates.pop(row.get("rubric_id"), row) for row in existing]
        merged.extend(updates.values())
        temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
        temporary.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in merged), encoding="utf-8")
        staged.append((temporary, target))
    return [target for _temporary, target in staged], previous, staged


def record_evidence(workspace: str | Path, candidate: str | Path) -> dict[str, Any]:
    """Merge one validated worker batch into the canonical evidence files."""

    root = Path(workspace).resolve()
    rows = _read_jsonl(Path(candidate))
    errors = validate_evidence_batch(root, rows)
    if errors:
        raise EvidenceRejected(errors)
    manifest = _read_json(root / "MANIFEST.json")
    written, previous, staged = merge_evidence_batch(root, manifest, rows)
    manifest_path = root / "MANIFEST.json"
    manifest_before = manifest_path.read_bytes()
    previous[manifest_path] = manifest_before
    try:
        for temporary, target in staged:
            os.replace(temporary, target)
        # The declared status is script-owned: converge it, then judge only on the
        # next report so a status mismatch never masquerades as a content failure.
        report: dict = {}
        for _attempt in range(3):
            report = validate_bundle(root, require_seal=False)
            codes = {row["code"] for row in report["errors"]}
            if report["result"] == "pass":
                break
            if codes == {"STATUS_MISMATCH"}:
                manifest["package_status"] = derived_status_ignoring_declared(report)
                manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                continue
            raise EvidenceRejected(report["errors"])
        if report.get("result") != "pass":
            raise EvidenceRejected(report.get("errors", []))
    except BaseException:
        for target, payload in previous.items():
            if payload is None:
                target.unlink(missing_ok=True)
            else:
                target.write_bytes(payload)
        for temporary, _target in staged:
            temporary.unlink(missing_ok=True)
        raise
    missing = sorted(
        (model["model_id"], rubric_id)
        for model in manifest.get("models", [])
        for rubric_id in _baseline_rubrics(root, manifest)
        if rubric_id not in {
            row.get("rubric_id")
            for row in _read_jsonl(root / model["directory"] / "rubric-evidence.jsonl")
        }
    )
    return {"workspace": str(root), "merged": len(rows), "files": [str(path) for path in written],
            "missing_pairs": [list(pair) for pair in missing], "status": report["derived_status"]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workspace", type=Path)
    parser.add_argument("--candidate", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = record_evidence(args.workspace, args.candidate)
    except EvidenceRejected as exc:
        print(json.dumps(exc.errors, ensure_ascii=False), file=sys.stderr)
        return 3
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 3
    print(json.dumps(result, ensure_ascii=False))
    return 0 if not result["missing_pairs"] else 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["EvidenceRejected", "merge_evidence_batch", "record_evidence", "validate_evidence_batch"]
