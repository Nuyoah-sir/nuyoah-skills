#!/usr/bin/env python3
"""Accept review-worker candidates and write the canonical v1 review artifacts.

The initializer deliberately leaves prompt requirements unclassified and every
rubric unreviewed. A weak agent must not hand-edit those files, so this module is
the only write path: it validates the candidates against the frozen prompt and
rubric baseline, replaces exactly three artifacts atomically, and immediately
runs the v1 validator, restoring the previous bytes if validation fails.

Candidate items carry ``basis_status`` plus a prose ``reason``. The v1 artifact
stores the status echo in ``finding`` and the prose in ``reason``; that legacy
field naming is reproduced here rather than invented.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any

from validate_bundle import validate_bundle

REVIEW_ARTIFACTS = ("inputs/prompt-requirements.jsonl", "inputs/rubric-index.json", "review/rubric-review.json")
REQUIREMENT_KINDS = {"explicit", "implicit", "context"}
COVERAGES = {"mapped", "gap"}
BASIS_STATUSES = {"supported", "partially_supported", "unsupported", "ambiguous"}
BASIS_TYPES = {"prompt", "effect", "author"}
SCAFFOLD_MARKERS = {"candidate", "needs_classification", "unreviewed"}


class ReviewRejected(ValueError):
    """The candidate review does not satisfy the frozen contract."""

    def __init__(self, errors: list[dict[str, str]]):
        super().__init__(json.dumps(errors, ensure_ascii=False))
        self.errors = errors


def _issue(code: str, message: str, path: str) -> dict[str, str]:
    return {"code": code, "message": message, "path": path}


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[Any]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _baseline(root: Path, manifest: dict) -> list[dict]:
    """Return the frozen rubric rows annotated with their source file and index."""

    rows: list[dict] = []
    for entry in sorted(manifest.get("inputs", {}).get("rubrics", []), key=lambda item: item.get("round", 0)):
        value = _read_json(root / entry["path"])
        if not isinstance(value, list):
            raise ValueError(f"Baseline rubric file {entry['path']!r} must be an array")
        rows.extend({**row, "_source_file": entry["path"], "_source_index": index} for index, row in enumerate(value))
    return rows


def validate_review_candidates(root: Path, requirements: list[Any], review_items: list[Any]) -> list[dict[str, str]]:
    """Return every contract violation instead of stopping at the first one."""

    errors: list[dict[str, str]] = []
    manifest = _read_json(root / "MANIFEST.json")
    prompt_path = manifest.get("inputs", {}).get("prompt")
    if not isinstance(prompt_path, str):
        return [_issue("PROMPT_INVALID", "MANIFEST.inputs.prompt must name the frozen prompt", "MANIFEST.json")]
    prompt_lines = (root / prompt_path).read_text(encoding="utf-8").splitlines()
    rubrics = _baseline(root, manifest)
    rubric_ids = [row.get("id") for row in rubrics]

    seen: set[str] = set()
    covered: set[int] = set()
    mapping: dict[str, set[str]] = {}
    for index, row in enumerate(requirements):
        where = f"inputs/prompt-requirements.jsonl#{index}"
        if not isinstance(row, dict):
            errors.append(_issue("PROMPT_REQUIREMENT_INVALID", "Each requirement must be an object", where))
            continue
        requirement_id = row.get("requirement_id")
        if not isinstance(requirement_id, str) or not requirement_id or requirement_id in seen:
            errors.append(_issue("PROMPT_REQUIREMENT_ID", f"Missing or duplicate requirement_id {requirement_id!r}", where))
            continue
        seen.add(requirement_id)
        kind = row.get("kind")
        if kind not in REQUIREMENT_KINDS:
            errors.append(_issue("PROMPT_REQUIREMENT_UNCLASSIFIED", f"{requirement_id} must be classified as one of {sorted(REQUIREMENT_KINDS)}, not {kind!r}", where))
        coverage = row.get("coverage")
        muted = kind == "context"
        if muted:
            if coverage != "gap" or row.get("mapped_rubric_ids"):
                errors.append(_issue("PROMPT_CONTEXT_MAPPING_INVALID", f"{requirement_id} context rows must be coverage=gap with no rubric", where))
        elif coverage not in COVERAGES or coverage == "needs_classification":
            errors.append(_issue("PROMPT_COVERAGE_INCOMPLETE", f"{requirement_id} needs coverage mapped or gap", where))
        mapped = row.get("mapped_rubric_ids")
        if not muted and coverage == "mapped":
            if not isinstance(mapped, list) or not mapped:
                errors.append(_issue("PROMPT_MAPPING_EMPTY", f"{requirement_id} is mapped but names no rubric", where))
            else:
                unknown = sorted(value for value in mapped if value not in rubric_ids)
                if unknown:
                    errors.append(_issue("PROMPT_MAPPING_UNKNOWN", f"{requirement_id} maps unknown rubrics {unknown}", where))
                mapping[requirement_id] = set(value for value in mapped if value in rubric_ids)
        source = row.get("source")
        span = range(1, len(prompt_lines) + 1)
        start, end = (source or {}).get("line_start"), (source or {}).get("line_end")
        if not isinstance(source, dict) or source.get("path") != prompt_path or not isinstance(start, int) or not isinstance(end, int):
            errors.append(_issue("PROMPT_SOURCE_INVALID", f"{requirement_id} needs prompt line coordinates", where))
        elif start not in span or end not in span or start > end:
            errors.append(_issue("PROMPT_SOURCE_RANGE_INVALID", f"{requirement_id} line range is outside the frozen prompt", where))
        else:
            expected_quote = "\n".join(prompt_lines[start - 1:end])
            if source.get("quote") != expected_quote:
                errors.append(_issue("PROMPT_QUOTE_MISMATCH", f"{requirement_id} source.quote differs from the frozen prompt lines", where))
            text = row.get("text")
            if not isinstance(text, str) or not text.strip() or text.strip() not in expected_quote:
                errors.append(_issue("PROMPT_TEXT_MISMATCH", f"{requirement_id} text is not present in its prompt line range", where))
            if kind == "implicit" and not str(row.get("rationale", "")).strip():
                errors.append(_issue("PROMPT_IMPLICIT_RATIONALE_MISSING", f"{requirement_id} implicit requirement needs a rationale", where))
            covered.update(range(start, end + 1))
    if not requirements:
        errors.append(_issue("PROMPT_REQUIREMENTS_EMPTY", "At least one classified prompt requirement is required", "inputs/prompt-requirements.jsonl"))
    else:
        nonempty = {number for number, line in enumerate(prompt_lines, 1) if line.strip()}
        missing = sorted(nonempty - covered)
        if missing:
            errors.append(_issue("PROMPT_REQUIREMENT_COVERAGE", f"Unclassified non-empty prompt lines: {missing}", "inputs/prompt-requirements.jsonl"))

    object_items = [row for row in review_items if isinstance(row, dict)]
    by_rubric = {row.get("rubric_id"): row for row in object_items}
    if len(by_rubric) != len(object_items):
        errors.append(_issue("RUBRIC_REVIEW_COVERAGE", "Review items must be unique per rubric", "review/rubric-review.json"))
    for rubric_id in rubric_ids:
        item = by_rubric.get(rubric_id)
        where = f"review/rubric-review.json#{rubric_id}"
        if not isinstance(item, dict):
            errors.append(_issue("RUBRIC_REVIEW_COVERAGE", f"{rubric_id} has no review item", where))
            continue
        status = item.get("basis_status")
        if status not in BASIS_STATUSES:
            errors.append(_issue("RUBRIC_REVIEW_INCOMPLETE", f"{rubric_id} basis_status must be one of {sorted(BASIS_STATUSES)}", where))
        if not str(item.get("reason", "")).strip():
            errors.append(_issue("RUBRIC_REVIEW_MISMATCH", f"{rubric_id} review must explain the finding", where))
        basis = item.get("basis")
        if not isinstance(basis, list) or not basis:
            errors.append(_issue("RUBRIC_BASIS_MISSING", f"{rubric_id} has no prompt/effect/author basis", where))
            continue
        for entry in basis:
            if not isinstance(entry, dict) or entry.get("type") not in BASIS_TYPES:
                errors.append(_issue("RUBRIC_BASIS_INVALID", f"{rubric_id} has an invalid basis entry", where))
                continue
            if entry["type"] == "prompt":
                requirement_id = entry.get("requirement_id")
                if not isinstance(requirement_id, str) or requirement_id not in mapping or rubric_id not in mapping[requirement_id]:
                    errors.append(_issue("RUBRIC_BASIS_REFERENCE_INVALID", f"{rubric_id} prompt basis is not reciprocally mapped", where))
            elif not str(entry.get("fact", "")).strip() or not str(entry.get("source", "")).strip():
                errors.append(_issue("RUBRIC_BASIS_INVALID", f"{rubric_id} {entry['type']} basis lacks fact/source", where))
    for rubric_id in by_rubric:
        if rubric_id not in rubric_ids:
            errors.append(_issue("RUBRIC_REVIEW_COVERAGE", f"Review names unknown rubric {rubric_id!r}", "review/rubric-review.json"))
    return errors


def build_review_artifacts(root: Path, requirements: list[Any], review_items: list[Any]) -> dict[str, Any]:
    manifest = _read_json(root / "MANIFEST.json")
    rubrics = _baseline(root, manifest)
    by_rubric = {row.get("rubric_id"): row for row in review_items if isinstance(row, dict)}
    index_rows = []
    for row in rubrics:
        item = by_rubric.get(row.get("id")) or {}
        index_rows.append({
            "id": row.get("id"),
            "round": row.get("round"),
            "criterion": row.get("criterion"),
            "criterion_sha256": _criterion_digest(root, row),
            "source_file": row.get("_source_file") or _source_file(root, manifest, row),
            "source_index": row.get("_source_index", row.get("source_index", 0)),
            "source_basis": item.get("basis"),
            "review": {"basis_status": item.get("basis_status"), "reason": item.get("reason"),
                       "reviewer": item.get("reviewer"), "reviewed_at": item.get("reviewed_at")},
        })
    review_document = {
        "status": "reviewed",
        "reviewer": sorted({str(row.get("reviewer")) for row in review_items if isinstance(row, dict) and row.get("reviewer")}),
        "items": [{"rubric_id": row["id"], "finding": row["review"]["basis_status"],
                   "reason": row["review"]["reason"], "basis": row["source_basis"]} for row in index_rows],
    }
    return {
        "inputs/prompt-requirements.jsonl": requirements,
        "inputs/rubric-index.json": {"rubrics": index_rows},
        "review/rubric-review.json": review_document,
    }


def _source_file(root: Path, manifest: dict, row: dict) -> str:
    return row.get("source_file") or manifest["inputs"]["rubrics"][0]["path"]


def _criterion_digest(root: Path, row: dict) -> str:
    import hashlib
    return row.get("criterion_sha256") or hashlib.sha256(str(row.get("criterion", "")).encode("utf-8")).hexdigest()


def _render(relative: str, value: Any) -> str:
    if relative.endswith(".jsonl"):
        return "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in value)
    return _dump(value)


def record_review(workspace: str | Path, *, requirements: str | Path, review_items: str | Path) -> dict[str, Any]:
    """Validate review candidates, replace the three artifacts, and re-validate the bundle."""

    root = Path(workspace).resolve()
    requirement_rows = _read_jsonl(Path(requirements))
    review_document = _read_json(Path(review_items))
    items = review_document.get("items") if isinstance(review_document, dict) else None
    if not isinstance(items, list):
        raise ReviewRejected([_issue("RUBRIC_REVIEW_INCOMPLETE", "Review candidate must hold an items array", "review/rubric-review.json")])
    errors = validate_review_candidates(root, requirement_rows, items)
    if errors:
        raise ReviewRejected(errors)
    artifacts = build_review_artifacts(root, requirement_rows, items)
    previous = {relative: (root / relative).read_bytes() if (root / relative).is_file() else None for relative in REVIEW_ARTIFACTS}
    staged: list[tuple[Path, Path]] = []
    try:
        for relative, value in artifacts.items():
            target = root.joinpath(*relative.split("/"))
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
            temporary.write_text(_render(relative, value), encoding="utf-8")
            staged.append((temporary, target))
        for temporary, target in staged:
            os.replace(temporary, target)
        report = validate_bundle(root, require_seal=False)
        if report["result"] != "pass":
            raise ReviewRejected(report["errors"])
    except BaseException:
        for relative, payload in previous.items():
            target = root.joinpath(*relative.split("/"))
            if payload is None:
                target.unlink(missing_ok=True)
            else:
                target.write_bytes(payload)
        for temporary, _target in staged:
            temporary.unlink(missing_ok=True)
        raise
    return {"workspace": str(root), "artifacts": list(REVIEW_ARTIFACTS), "rubrics": len(artifacts["inputs/rubric-index.json"]["rubrics"]),
            "requirements": len(requirement_rows), "status": report["derived_status"]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workspace", type=Path)
    parser.add_argument("--requirements", type=Path, required=True)
    parser.add_argument("--review-items", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = record_review(args.workspace, requirements=args.requirements, review_items=args.review_items)
    except ReviewRejected as exc:
        print(json.dumps(exc.errors, ensure_ascii=False), file=sys.stderr)
        return 3
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 3
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["REVIEW_ARTIFACTS", "ReviewRejected", "build_review_artifacts", "record_review", "validate_review_candidates"]
