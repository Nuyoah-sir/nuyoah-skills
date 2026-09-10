#!/usr/bin/env python3
"""Strictly validate a Vibe Evals evidence bundle without executing model code."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Iterable

from summarize_conversation import summarize_conversation_bytes

SCHEMA = "vibe-evals-evidence-bundle"
VERSION = "1.0.0"
PATH_KEYS = {"path", "directory", "prompt", "record_txt", "stdout_path", "stderr_path", "script_path", "source_file"}
EVIDENCE_TYPES = {"static_line", "self_test_run", "probe_run", "conversation", "human_note", "snapshot_diff", "inventory_fact"}
DIRECTIONS = {"support", "refute", "confirm_missing", "context"}
DISPOSITIONS = {"examined", "fallback_zero"}
COVERAGES = {"complete", "partial", "missing", "unsafe_to_test"}
CONFIDENCES = {"high", "medium", "low"}
REASON_CODES = {"implemented_and_verified", "implemented_static_only", "behavior_failed", "missing_implementation", "violates_explicit_constraint", "insufficient_evidence", "subjective_needs_human", "unsafe_to_test", "policy_ambiguous", "model_fallback_zero"}
ROW_REQUIRED = {"model_id", "rubric_id", "rubric_round", "criterion", "disposition", "coverage", "suggested_score", "confidence", "reason_code", "fact_summary", "evidence", "human_check_needed", "adjudication_ids", "limitations"}
SEAL_EXCLUDES = {"READY.json", "integrity/files.sha256", "integrity/validation-report.json"}


def issue(code: str, message: str, path: str | None = None) -> dict[str, str]:
    value = {"code": code, "message": message}
    if path:
        value["path"] = path
    return value


def safe_relative(value: str) -> bool:
    if not value or "\x00" in value:
        return False
    win = PureWindowsPath(value)
    posix = PurePosixPath(value.replace("\\", "/"))
    return not win.is_absolute() and not win.drive and not posix.is_absolute() and ".." not in posix.parts


def normalize_round(value: Any) -> int | None:
    """Return the canonical positive integer for supported round spellings."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 1 else None
    if isinstance(value, str):
        match = re.fullmatch(r"[Rr]?([1-9]\d*)", value.strip())
        if match:
            return int(match.group(1))
    return None


def rubric_round(rubric: dict) -> int | None:
    return rubric.get("_normalized_round", rubric.get("_bundle_round"))


def walk_paths(value: Any, key: str = "") -> Iterable[tuple[str, str]]:
    if isinstance(value, dict):
        for child_key, child in value.items():
            yield from walk_paths(child, child_key)
    elif isinstance(value, list):
        for child in value:
            yield from walk_paths(child, key)
    elif isinstance(value, str) and (key in PATH_KEYS or key.endswith("_path") or key.endswith("_dir")):
        yield key, value


def read_json(path: Path, errors: list[dict]) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        errors.append(issue("FILE_MISSING", "Required file is missing", path.as_posix()))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        errors.append(issue("JSON_INVALID", str(exc), path.as_posix()))
    return None


def read_jsonl(path: Path, errors: list[dict], required: bool = True) -> list[dict]:
    rows: list[dict] = []
    if not path.exists() and not required:
        return rows
    try:
        for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not raw.strip():
                continue
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise ValueError("row is not an object")
            rows.append(value)
    except FileNotFoundError:
        errors.append(issue("FILE_MISSING", "Required file is missing", path.as_posix()))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        errors.append(issue("JSONL_INVALID", f"line {number}: {exc}", path.as_posix()))
    return rows


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def source_inventory_digest(rows: list[dict], errors: list[dict]) -> str | None:
    normalized: list[tuple[str, str]] = []
    seen: set[str] = set()
    for item in rows:
        if not isinstance(item, dict):
            errors.append(issue("SOURCE_INVENTORY_INVALID", "Every source inventory row must be an object", "source-freeze.json"))
            continue
        path = item.get("path")
        digest = item.get("sha256")
        size = item.get("size")
        if not isinstance(path, str) or not safe_relative(path) or path in seen or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest) or not isinstance(size, int) or isinstance(size, bool) or size < 0:
            errors.append(issue("SOURCE_INVENTORY_INVALID", f"Invalid/duplicate source inventory row {path!r}", "source-freeze.json"))
            continue
        seen.add(path)
        normalized.append((path, digest))
    if len(normalized) != len(rows):
        return None
    return hashlib.sha256("\n".join(f"{digest}  {path}" for path, digest in sorted(normalized)).encode("utf-8")).hexdigest()


def source_identity_digest(freeze: dict, inventory_digest: str) -> str:
    prompt = freeze.get("prompt_source")
    record = freeze.get("record_source")
    rubrics = freeze.get("rubric_sources", [])
    identity = {
        "inventory_digest": inventory_digest,
        "prompt_source": {key: prompt.get(key) for key in ("path", "size", "sha256")} if isinstance(prompt, dict) else None,
        "record_source": {key: record.get(key) for key in ("path", "size", "sha256")} if isinstance(record, dict) else None,
        "rubric_sources": [{key: item.get(key) for key in ("round", "path", "size", "sha256")} for item in rubrics if isinstance(item, dict)],
        "model_sources": freeze.get("model_sources", {}),
    }
    return hashlib.sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def load_rubrics(root: Path, manifest: dict, errors: list[dict]) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for entry in manifest.get("inputs", {}).get("rubrics", []):
        relative = entry.get("path", "")
        bundle_round = normalize_round(entry.get("round"))
        if not safe_relative(relative):
            continue
        value = read_json(root / PurePosixPath(relative), errors)
        if not isinstance(value, list):
            errors.append(issue("RUBRICS_ARRAY_REQUIRED", "Rubric file must be an array", relative))
            continue
        if entry.get("count") != len(value):
            errors.append(issue("RUBRIC_COUNT_MISMATCH", "Manifest count differs from rubric file", relative))
        expected_digest = entry.get("sha256")
        if not isinstance(expected_digest, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_digest):
            errors.append(issue("RUBRIC_DIGEST_MISSING", "Manifest rubric entry needs a lowercase SHA-256", relative))
        elif sha256(root / PurePosixPath(relative)) != expected_digest:
            errors.append(issue("RUBRIC_DIGEST_MISMATCH", "Rubric file differs from manifest SHA-256", relative))
        for source_index, item in enumerate(value):
            if not isinstance(item, dict) or not item.get("id") or "criterion" not in item:
                errors.append(issue("RUBRIC_INVALID", "Every rubric needs id and criterion", relative))
                continue
            rubric_id = item["id"]
            if rubric_id in result:
                errors.append(issue("RUBRIC_ID_DUPLICATE", f"Duplicate rubric id {rubric_id}", relative))
                continue
            source_round = normalize_round(item.get("round")) if "round" in item else bundle_round
            if "round" in item and source_round is None:
                errors.append(issue("RUBRIC_ROUND_INVALID", f"{rubric_id} declares an unsupported round {item.get('round')!r}", relative))
            elif source_round != bundle_round:
                errors.append(issue("RUBRIC_ROUND_SOURCE_MISMATCH", f"{rubric_id} declares a round different from its rubric file", relative))
            result[rubric_id] = {
                **item,
                "_normalized_round": source_round if source_round is not None else bundle_round,
                "_bundle_round": bundle_round,
                "_source_file": relative,
                "_source_index": source_index,
            }
    return result


def validate_index(root: Path, rubrics: dict[str, dict], errors: list[dict]) -> None:
    index = read_json(root / "inputs" / "rubric-index.json", errors)
    if not isinstance(index, dict):
        return
    rows = index.get("rubrics")
    if not isinstance(rows, list):
        errors.append(issue("RUBRIC_INDEX_INVALID", "rubric-index.json needs a rubrics array", "inputs/rubric-index.json"))
        return
    by_id = {row.get("id"): row for row in rows if isinstance(row, dict)}
    if set(by_id) != set(rubrics):
        errors.append(issue("RUBRIC_INDEX_COVERAGE", "Rubric index IDs differ from baseline", "inputs/rubric-index.json"))
    for rubric_id, rubric in rubrics.items():
        row = by_id.get(rubric_id, {})
        expected_round = rubric_round(rubric)
        if row.get("criterion") != rubric.get("criterion") or row.get("round") != expected_round:
            errors.append(issue("RUBRIC_INDEX_DRIFT", f"Index drift for {rubric_id}", "inputs/rubric-index.json"))
        expected_criterion_digest = hashlib.sha256(str(rubric.get("criterion", "")).encode("utf-8")).hexdigest()
        if row.get("criterion_sha256") != expected_criterion_digest or row.get("source_file") != rubric.get("_source_file") or row.get("source_index") != rubric.get("_source_index"):
            errors.append(issue("RUBRIC_INDEX_SOURCE_DRIFT", f"Source coordinates/hash drift for {rubric_id}", "inputs/rubric-index.json"))
        basis_rows = row.get("source_basis")
        if not isinstance(basis_rows, list) or not basis_rows:
            errors.append(issue("RUBRIC_BASIS_MISSING", f"{rubric_id} has no prompt/effect/author basis", "inputs/rubric-index.json"))
        else:
            for basis in basis_rows:
                basis_type = basis.get("type") if isinstance(basis, dict) else None
                if basis_type not in {"prompt", "model_effect", "author_thinking"}:
                    errors.append(issue("RUBRIC_BASIS_INVALID", f"{rubric_id} has invalid basis type {basis_type!r}", "inputs/rubric-index.json"))
                elif basis_type == "prompt" and not basis.get("requirement_id"):
                    errors.append(issue("RUBRIC_BASIS_INVALID", f"{rubric_id} prompt basis lacks requirement_id", "inputs/rubric-index.json"))
                elif basis_type in {"model_effect", "author_thinking"} and not (basis.get("fact") or basis.get("source")):
                    errors.append(issue("RUBRIC_BASIS_INVALID", f"{rubric_id} {basis_type} basis lacks fact/source", "inputs/rubric-index.json"))
        review = row.get("review") if isinstance(row.get("review"), dict) else {}
        if review.get("basis_status") not in {"supported", "partially_supported", "unsupported", "ambiguous"}:
            errors.append(issue("RUBRIC_REVIEW_INCOMPLETE", f"{rubric_id} has not been reviewed", "inputs/rubric-index.json"))


def validate_semantic_inputs(root: Path, manifest: dict, rubrics: dict[str, dict], errors: list[dict]) -> None:
    """Reject initializer scaffolds until prompt mapping and rubric review are complete."""
    requirements = read_jsonl(root / "inputs" / "prompt-requirements.jsonl", errors)
    prompt_relative = manifest.get("inputs", {}).get("prompt", "")
    prompt_lines = []
    if safe_relative(prompt_relative) and (root / PurePosixPath(prompt_relative)).is_file():
        prompt_lines = (root / PurePosixPath(prompt_relative)).read_text(encoding="utf-8").splitlines()
    requirement_ids: set[str] = set()
    for row in requirements:
        requirement_id = row.get("requirement_id")
        if not requirement_id or requirement_id in requirement_ids:
            errors.append(issue("PROMPT_REQUIREMENT_ID", f"Missing or duplicate requirement_id {requirement_id!r}", "inputs/prompt-requirements.jsonl"))
        requirement_ids.add(requirement_id)
        kind = row.get("kind")
        if kind not in {"explicit", "necessary_implicit", "context"}:
            errors.append(issue("PROMPT_REQUIREMENT_UNCLASSIFIED", f"{requirement_id} still has kind={row.get('kind')!r}", "inputs/prompt-requirements.jsonl"))
        coverage = row.get("coverage")
        mapped = row.get("mapped_rubric_ids")
        if coverage not in {"mapped", "gap", "not_applicable"} or not isinstance(mapped, list):
            errors.append(issue("PROMPT_COVERAGE_INCOMPLETE", f"{requirement_id} needs mapped/gap coverage", "inputs/prompt-requirements.jsonl"))
        elif kind == "context" and (coverage != "not_applicable" or mapped):
            errors.append(issue("PROMPT_CONTEXT_MAPPING_INVALID", f"{requirement_id} context must be not_applicable and unmapped", "inputs/prompt-requirements.jsonl"))
        elif kind != "context" and coverage == "not_applicable":
            errors.append(issue("PROMPT_COVERAGE_INCOMPLETE", f"{requirement_id} requirement cannot be not_applicable", "inputs/prompt-requirements.jsonl"))
        elif coverage == "mapped" and not mapped:
            errors.append(issue("PROMPT_MAPPING_EMPTY", f"{requirement_id} is mapped but has no rubric", "inputs/prompt-requirements.jsonl"))
        elif any(rubric_id not in rubrics for rubric_id in mapped):
            errors.append(issue("PROMPT_MAPPING_UNKNOWN", f"{requirement_id} references an unknown rubric", "inputs/prompt-requirements.jsonl"))
        source = row.get("source") if isinstance(row.get("source"), dict) else {}
        start = source.get("line_start")
        end = source.get("line_end")
        if source.get("path") != prompt_relative or not isinstance(start, int) or isinstance(start, bool) or not isinstance(end, int) or isinstance(end, bool):
            errors.append(issue("PROMPT_SOURCE_INVALID", f"{requirement_id} needs prompt line coordinates", "inputs/prompt-requirements.jsonl"))
        elif start < 1 or end < start or end > len(prompt_lines):
            errors.append(issue("PROMPT_SOURCE_RANGE_INVALID", f"{requirement_id} line range is outside prompt.md", "inputs/prompt-requirements.jsonl"))
        else:
            source_text = "\n".join(prompt_lines[start - 1:end])
            if source.get("quote") != source_text:
                errors.append(issue("PROMPT_QUOTE_MISMATCH", f"{requirement_id} source.quote differs from prompt lines", "inputs/prompt-requirements.jsonl"))
            normalized_claim = "".join(str(row.get("text", "")).split())
            normalized_source = "".join(source_text.split())
            if not normalized_claim or (kind in {"explicit", "context"} and normalized_claim not in normalized_source):
                errors.append(issue("PROMPT_TEXT_MISMATCH", f"{requirement_id} text is not present in its prompt line range", "inputs/prompt-requirements.jsonl"))
            if kind == "necessary_implicit" and not str(row.get("rationale", "")).strip():
                errors.append(issue("PROMPT_IMPLICIT_RATIONALE_MISSING", f"{requirement_id} implicit requirement needs a rationale", "inputs/prompt-requirements.jsonl"))
    if not requirements:
        errors.append(issue("PROMPT_REQUIREMENTS_EMPTY", "At least one classified prompt requirement is required", "inputs/prompt-requirements.jsonl"))
    if prompt_lines:
        nonempty_lines = {number for number, line in enumerate(prompt_lines, 1) if line.strip()}
        covered_lines = set()
        for row in requirements:
            source = row.get("source") if isinstance(row.get("source"), dict) else {}
            start, end = source.get("line_start"), source.get("line_end")
            if isinstance(start, int) and not isinstance(start, bool) and isinstance(end, int) and not isinstance(end, bool) and 1 <= start <= end <= len(prompt_lines):
                covered_lines.update(range(start, end + 1))
        if not nonempty_lines.issubset(covered_lines):
            errors.append(issue("PROMPT_REQUIREMENT_COVERAGE", f"Unclassified non-empty prompt lines: {sorted(nonempty_lines-covered_lines)}", "inputs/prompt-requirements.jsonl"))

    index = read_json(root / "inputs" / "rubric-index.json", errors)
    index_rows = {item.get("id"): item for item in (index or {}).get("rubrics", []) if isinstance(item, dict)}
    requirement_map = {item.get("requirement_id"): item for item in requirements if item.get("requirement_id")}
    for rubric_id, index_row in index_rows.items():
        for basis in index_row.get("source_basis", []):
            if isinstance(basis, dict) and basis.get("type") == "prompt":
                requirement = requirement_map.get(basis.get("requirement_id"))
                if not requirement or rubric_id not in requirement.get("mapped_rubric_ids", []):
                    errors.append(issue("RUBRIC_BASIS_REFERENCE_INVALID", f"{rubric_id} prompt basis is not reciprocally mapped", "inputs/rubric-index.json"))

    review = read_json(root / "review" / "rubric-review.json", errors)
    if not isinstance(review, dict) or review.get("status") != "reviewed" or not isinstance(review.get("items"), list):
        errors.append(issue("RUBRIC_REVIEW_INCOMPLETE", "rubric-review.json must have status=reviewed and an items array", "review/rubric-review.json"))
    else:
        review_items = review.get("items", [])
        by_rubric = {item.get("rubric_id"): item for item in review_items if isinstance(item, dict) and item.get("rubric_id")}
        if len(by_rubric) != len(review_items) or set(by_rubric) != set(rubrics):
            errors.append(issue("RUBRIC_REVIEW_COVERAGE", "rubric-review items must cover every rubric exactly once", "review/rubric-review.json"))
        for rubric_id, item in by_rubric.items():
            expected = (index_rows.get(rubric_id, {}).get("review") or {}).get("basis_status")
            if item.get("finding") != expected or not str(item.get("reason", "")).strip():
                errors.append(issue("RUBRIC_REVIEW_MISMATCH", f"Review summary for {rubric_id} must match rubric-index and explain the finding", "review/rubric-review.json"))

    freeze = read_json(root / "source-freeze.json", errors)
    if not isinstance(freeze, dict) or freeze.get("input_digest") != manifest.get("source_input_digest") or not isinstance(freeze.get("source_inventory"), list):
        errors.append(issue("SOURCE_FREEZE_INVALID", "source-freeze.json must match source_input_digest and contain source_inventory", "source-freeze.json"))


def inventory_maps(value: dict) -> tuple[dict[str, str], dict[tuple[int, str], str]]:
    final = {item.get("path"): item.get("sha256") for item in value.get("final", {}).get("files", [])}
    rounds: dict[tuple[int, str], str] = {}
    for group in value.get("rounds", []):
        for item in group.get("files", []):
            rounds[(group.get("round"), item.get("path"))] = item.get("sha256")
    return final, rounds


def load_run_ids(model_root: Path, errors: list[dict]) -> tuple[dict[str, dict], dict[str, dict]]:
    test_ids: dict[str, dict] = {}
    probe_ids: dict[str, dict] = {}
    for child, target in (("tests/index.json", test_ids), ("probes/index.json", probe_ids)):
        path = model_root / PurePosixPath(child)
        if not path.exists():
            continue
        value = read_json(path, errors)
        if not isinstance(value, dict):
            continue
        seen_run_ids: set[str] = set()
        for run in value.get("runs", []):
            if not isinstance(run, dict):
                errors.append(issue("RUN_INVALID", "Every run entry must be an object", path.as_posix()))
                continue
            run_id = run.get("run_id")
            if not run_id or run_id in seen_run_ids:
                errors.append(issue("RUN_ID_DUPLICATE", f"Missing or duplicate run_id {run_id!r}", path.as_posix()))
            seen_run_ids.add(run_id)
            executed = run.get("status") in {"passed", "failed", "error", "timeout"}
            if executed and run_id:
                target[run_id] = run
            if executed:
                safety = run.get("safety", {})
                keys = {"model_code_treated_as_untrusted", "isolated", "network_disabled", "credentials_absent", "disposable_copy", "minimal_permissions"}
                if not all(safety.get(key) is True for key in keys):
                    errors.append(issue("UNSAFE_TEST_EXECUTION", f"Executed run {run_id!r} lacks full isolation", path.as_posix()))
                argv = run.get("argv")
                if not isinstance(argv, list) or not argv or any(not isinstance(arg, str) or not arg for arg in argv):
                    errors.append(issue("RUN_COMMAND_INVALID", f"Executed run {run_id!r} needs a non-empty argv array", path.as_posix()))
                script_relative = run.get("script_path")
                script_digest = run.get("script_sha256")
                script_file = model_root / PurePosixPath(script_relative) if safe_relative(str(script_relative or "")) else None
                if (not script_relative or not isinstance(script_digest, str) or not re.fullmatch(r"[0-9a-f]{64}", script_digest)
                        or script_file is None or not script_file.is_file() or sha256(script_file) != script_digest):
                    errors.append(issue("RUN_SCRIPT_UNBOUND", f"Executed run {run_id!r} needs a retained script_path and matching script_sha256", path.as_posix()))
                if not isinstance(run.get("duration_ms"), (int, float)) or isinstance(run.get("duration_ms"), bool) or run.get("duration_ms", -1) < 0:
                    errors.append(issue("RUN_DURATION_INVALID", f"Executed run {run_id!r} needs duration_ms", path.as_posix()))
                status = run.get("status")
                exit_code = run.get("exit_code")
                if status == "passed" and exit_code != 0:
                    errors.append(issue("RUN_EXIT_INVALID", f"Passed run {run_id!r} must have exit_code 0", path.as_posix()))
                elif status in {"failed", "error"} and (not isinstance(exit_code, int) or isinstance(exit_code, bool)):
                    errors.append(issue("RUN_EXIT_INVALID", f"{status} run {run_id!r} needs an integer exit_code", path.as_posix()))
                elif status == "timeout" and (exit_code is not None or not run.get("termination_reason")):
                    errors.append(issue("RUN_EXIT_INVALID", f"Timeout run {run_id!r} needs exit_code=null and termination_reason", path.as_posix()))
                assertions = run.get("assertions")
                if status in {"passed", "failed"}:
                    if not isinstance(assertions, list) or not assertions:
                        errors.append(issue("RUN_ASSERTIONS_INVALID", f"{status} run {run_id!r} needs non-empty structured assertions", path.as_posix()))
                    else:
                        for assertion in assertions:
                            if (not isinstance(assertion, dict) or not assertion.get("rubric_id") or "expected" not in assertion
                                    or "actual" not in assertion or not isinstance(assertion.get("passed"), bool)):
                                errors.append(issue("RUN_ASSERTIONS_INVALID", f"Run {run_id!r} has an unbound assertion", path.as_posix()))
                elif assertions is None and not run.get("assertions_unavailable_reason"):
                    errors.append(issue("RUN_ASSERTIONS_UNKNOWN", f"Executed run {run_id!r} needs assertions or an unavailable reason", path.as_posix()))
            elif run.get("status") in {"skipped", "skipped_no_isolation"} and not run.get("skip_reason"):
                errors.append(issue("SKIPPED_RUN_REASON_MISSING", f"Skipped run {run_id!r} needs skip_reason", path.as_posix()))
            for log_key in ("stdout_path", "stderr_path"):
                relative = run.get(log_key)
                if executed and not relative:
                    errors.append(issue("RUN_LOG_MISSING", f"Executed run {run_id!r} needs {log_key}", path.as_posix()))
                elif relative and (not safe_relative(relative) or not (model_root / PurePosixPath(relative)).is_file()):
                    errors.append(issue("RUN_LOG_MISSING", f"{run_id!r} references missing {log_key}", str(relative)))
    return test_ids, probe_ids


def validate_seal(root: Path, errors: list[dict], expected_status: str, expected_counts: dict) -> None:
    ready = read_json(root / "READY.json", errors)
    checksum_file = root / "integrity" / "files.sha256"
    if not isinstance(ready, dict):
        return
    manifest = json.loads((root / "MANIFEST.json").read_text(encoding="utf-8"))
    if ready.get("schema_version") != VERSION or ready.get("package_id") != manifest.get("package_id") or ready.get("status") != expected_status or ready.get("counts") != expected_counts:
        errors.append(issue("READY_MISMATCH", "READY does not match manifest/derived status", "READY.json"))
    validation_report = read_json(root / "integrity" / "validation-report.json", errors)
    if not isinstance(validation_report, dict) or validation_report.get("result") != "pass" or validation_report.get("derived_status") != expected_status or validation_report.get("counts") != expected_counts or validation_report.get("errors"):
        errors.append(issue("SEALED_REPORT_MISMATCH", "Stored validation report differs from the current validation result", "integrity/validation-report.json"))
    if not checksum_file.is_file():
        errors.append(issue("CHECKSUM_MISSING", "Sealed bundle requires integrity/files.sha256", "integrity/files.sha256"))
        return
    listed: dict[str, str] = {}
    for number, line in enumerate(checksum_file.read_text(encoding="utf-8").splitlines(), 1):
        match = re.fullmatch(r"([0-9a-f]{64})  (.+)", line)
        if not match:
            errors.append(issue("CHECKSUM_FORMAT", f"Invalid checksum line {number}", "integrity/files.sha256"))
            continue
        digest, relative = match.groups()
        if not safe_relative(relative):
            errors.append(issue("UNSAFE_PATH", "Unsafe checksum path", relative))
            continue
        if relative in listed:
            errors.append(issue("CHECKSUM_DUPLICATE", "Duplicate checksum path", relative))
            continue
        listed[relative] = digest
    expected_files = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file() and p.relative_to(root).as_posix() not in SEAL_EXCLUDES}
    if set(listed) != expected_files:
        errors.append(issue("CHECKSUM_COVERAGE", f"missing={sorted(expected_files-set(listed))} extra={sorted(set(listed)-expected_files)}", "integrity/files.sha256"))
    for relative, digest in listed.items():
        path = root / PurePosixPath(relative)
        if path.is_file() and sha256(path) != digest:
            errors.append(issue("HASH_MISMATCH", "SHA-256 mismatch", relative))


def validate_bundle(bundle_path: str | Path, require_seal: bool = True) -> dict[str, Any]:
    root = Path(bundle_path).resolve()
    errors: list[dict] = []
    warnings: list[dict] = []
    manifest = read_json(root / "MANIFEST.json", errors)
    if not isinstance(manifest, dict):
        return {"validator_version": VERSION, "result": "fail", "derived_status": "incomplete", "errors": errors, "warnings": warnings, "counts": {}}
    if manifest.get("schema") != SCHEMA or manifest.get("schema_version") != VERSION:
        errors.append(issue("SCHEMA_UNSUPPORTED", f"Expected {SCHEMA} {VERSION}", "MANIFEST.json"))
    required_manifest = {"package_id", "task", "generator", "source_input_digest", "inputs", "models", "package_status", "missing_materials", "warnings"}
    missing_manifest = sorted(required_manifest - set(manifest))
    if missing_manifest:
        errors.append(issue("MANIFEST_INCOMPLETE", f"Missing fields {missing_manifest}", "MANIFEST.json"))
    source_digest = manifest.get("source_input_digest")
    if not isinstance(source_digest, str) or not re.fullmatch(r"[0-9a-f]{64}", source_digest):
        errors.append(issue("SOURCE_DIGEST_INVALID", "source_input_digest must be a lowercase SHA-256", "MANIFEST.json"))
    for _, relative in walk_paths(manifest):
        if not safe_relative(relative):
            errors.append(issue("UNSAFE_PATH", "Paths must be safe package-relative paths", relative))
    prompt = manifest.get("inputs", {}).get("prompt", "")
    if not safe_relative(prompt) or not (root / PurePosixPath(prompt)).is_file():
        errors.append(issue("PROMPT_MISSING", "Manifest prompt file is missing", str(prompt)))
    rubrics = load_rubrics(root, manifest, errors)
    rubric_entries = manifest.get("inputs", {}).get("rubrics", [])
    entry_rounds = [item.get("round") for item in rubric_entries if isinstance(item, dict)]
    expected_rounds = list(range(1, len(rubric_entries) + 1))
    entry_rounds_are_canonical = all(isinstance(value, int) and not isinstance(value, bool) and value >= 1 for value in entry_rounds)
    if not entry_rounds_are_canonical or sorted(value for value in entry_rounds if isinstance(value, int) and not isinstance(value, bool)) != expected_rounds or manifest.get("task", {}).get("round_count") != len(rubric_entries):
        errors.append(issue("ROUND_COUNT_MISMATCH", "Rubric rounds must be unique/continuous and equal task.round_count", "MANIFEST.json"))
    validate_index(root, rubrics, errors)
    validate_semantic_inputs(root, manifest, rubrics, errors)
    freeze_doc = read_json(root / "source-freeze.json", errors)
    freeze_rows = (freeze_doc or {}).get("source_inventory", []) if isinstance(freeze_doc, dict) else []
    computed_source_digest = source_inventory_digest(freeze_rows, errors) if isinstance(freeze_rows, list) else None
    if computed_source_digest is not None:
        computed_identity_digest = source_identity_digest(freeze_doc or {}, computed_source_digest)
        if (freeze_doc or {}).get("source_inventory_digest") != computed_source_digest or computed_identity_digest != source_digest or (freeze_doc or {}).get("input_digest") != computed_identity_digest:
            errors.append(issue("SOURCE_DIGEST_MISMATCH", "Canonical source inventory/binding digest differs from manifest/source-freeze", "source-freeze.json"))
    freeze_inventory = {item.get("path"): item for item in freeze_rows if isinstance(item, dict) and item.get("path")}
    prompt_source = (freeze_doc or {}).get("prompt_source", {})
    if not isinstance(prompt_source, dict) or freeze_inventory.get(prompt_source.get("path")) != prompt_source or not safe_relative(prompt) or not (root / PurePosixPath(prompt)).is_file() or sha256(root / PurePosixPath(prompt)) != prompt_source.get("sha256"):
        errors.append(issue("PROMPT_SOURCE_MISMATCH", "Copied prompt is not bound to the frozen source inventory", str(prompt)))
    frozen_rubric_sources = {(item.get("round"), item.get("sha256")): item for item in (freeze_doc or {}).get("rubric_sources", []) if isinstance(item, dict)}
    for entry in rubric_entries:
        source_entry = frozen_rubric_sources.get((entry.get("round"), entry.get("sha256")))
        if not source_entry or freeze_inventory.get(source_entry.get("path")) != {key: source_entry.get(key) for key in ("path", "size", "mtime_ns", "sha256") if key in source_entry}:
            errors.append(issue("RUBRIC_SOURCE_MISMATCH", f"Rubric round {entry.get('round')} is not bound to frozen source", entry.get("path")))
    record_relative = manifest.get("inputs", {}).get("record_txt")
    record_source = (freeze_doc or {}).get("record_source")
    if record_relative:
        if (not isinstance(record_source, dict) or freeze_inventory.get(record_source.get("path")) != record_source
                or not safe_relative(record_relative) or not (root / PurePosixPath(record_relative)).is_file()
                or sha256(root / PurePosixPath(record_relative)) != record_source.get("sha256")):
            errors.append(issue("RECORD_SOURCE_MISMATCH", "Copied record.txt is not bound to the frozen source inventory", str(record_relative)))
    elif record_source is not None:
        errors.append(issue("RECORD_SOURCE_MISMATCH", "source-freeze contains record_source but manifest has no record_txt", "source-freeze.json"))
    frozen_snapshot_dirs = set((freeze_doc or {}).get("snapshot_dirs", []))
    frozen_conversations = set((freeze_doc or {}).get("conversation_files", []))
    frozen_model_sources = (freeze_doc or {}).get("model_sources", {}) if isinstance((freeze_doc or {}).get("model_sources", {}), dict) else {}
    material_gap_keys: set[str] = set(manifest.get("missing_materials", [])) if isinstance(manifest.get("missing_materials"), list) else set()
    rubric_ids = set(rubrics)
    model_ids: set[str] = set()
    all_evidence_ids: set[str] = set()
    evidence_records = unknown = human_checks = 0

    adjudication_doc = read_json(root / "review" / "pending-adjudications.json", errors)
    adjudications: dict[str, dict] = {}
    adjudication_pairs: set[tuple[str, str, str]] = set()
    pending = 0
    if isinstance(adjudication_doc, dict):
        for item in adjudication_doc.get("items", []):
            item_id = item.get("adjudication_id")
            if not item_id or item_id in adjudications:
                errors.append(issue("ADJUDICATION_ID_DUPLICATE", f"Missing or duplicate {item_id!r}", "review/pending-adjudications.json"))
            else:
                adjudications[item_id] = item
            if item.get("status") == "pending":
                pending += 1
            else:
                errors.append(issue("REMOTE_ADJUDICATION_NOT_PENDING", f"Bundle adjudication {item_id!r} must remain pending", "review/pending-adjudications.json"))
            if item.get("resolution") is not None:
                errors.append(issue("REMOTE_ADJUDICATION_RESOLVED", f"Bundle adjudication {item_id!r} cannot contain a human resolution", "review/pending-adjudications.json"))
            required_adj_text = ("question", "ambiguity", "recommended_policy", "alternative_policy", "impact")
            rubric_list = item.get("rubric_ids")
            evidence_list = item.get("evidence_ids")
            if (any(not str(item.get(key, "")).strip() for key in required_adj_text)
                    or not isinstance(rubric_list, list) or not rubric_list or any(rubric_id not in rubric_ids for rubric_id in rubric_list)
                    or not isinstance(evidence_list, list) or not isinstance(item.get("applies_to_all_models"), bool)):
                errors.append(issue("ADJUDICATION_INCOMPLETE", f"Adjudication {item_id!r} lacks decision-ready context or valid rubric/evidence lists", "review/pending-adjudications.json"))

    for model in manifest.get("models", []):
        model_id = model.get("model_id")
        model_dir = model.get("directory", "")
        if not model_id or model_id in model_ids:
            errors.append(issue("MODEL_ID_DUPLICATE", f"Missing or duplicate model_id {model_id!r}", "MANIFEST.json"))
            continue
        model_ids.add(model_id)
        if not safe_relative(model_dir):
            errors.append(issue("UNSAFE_PATH", "Unsafe model directory", str(model_dir)))
            continue
        model_root = root / PurePosixPath(model_dir)
        model_meta = read_json(model_root / "model.json", errors)
        inventory = read_json(model_root / "inventory.json", errors)
        if not isinstance(model_meta, dict) or model_meta.get("model_id") != model_id:
            errors.append(issue("MODEL_METADATA_MISMATCH", f"model.json mismatch for {model_id}", model_dir))
        final_files, round_files = inventory_maps(inventory if isinstance(inventory, dict) else {})
        if isinstance(model_meta, dict) and isinstance(inventory, dict):
            frozen_model = frozen_model_sources.get(model_id, {})
            if (not isinstance(frozen_model, dict) or frozen_model.get("display_name") != model.get("display_name")
                    or frozen_model.get("final_root") != model_meta.get("source_output_root_label")
                    or model_meta.get("display_name") != model.get("display_name")):
                errors.append(issue("MODEL_SOURCE_BINDING_MISMATCH", f"Model {model_id} source identity differs from source-freeze/manifest", model_dir))
            final_prefix = str(model_meta.get("source_output_root_label", "")).rstrip("/")
            expected_final = {path[len(final_prefix)+1:]: item for path, item in freeze_inventory.items() if final_prefix and path.startswith(final_prefix + "/")}
            actual_final_rows = inventory.get("final", {}).get("files", [])
            actual_final = {item.get("path"): item for item in actual_final_rows if isinstance(item, dict)}
            if not final_prefix or set(actual_final) != set(expected_final) or any(actual_final[path].get("sha256") != expected_final[path].get("sha256") or actual_final[path].get("size") != expected_final[path].get("size") for path in set(actual_final) & set(expected_final)):
                errors.append(issue("FINAL_INVENTORY_SOURCE_MISMATCH", f"Final inventory for {model_id} differs from source-freeze", model_dir))
            for group in inventory.get("rounds", []):
                label = str(group.get("source_root_label", "")).rstrip("/")
                frozen_label = (frozen_model.get("round_roots", {}) if isinstance(frozen_model, dict) else {}).get(str(group.get("round")))
                metadata_label = (model_meta.get("round_source_labels", {}) if isinstance(model_meta.get("round_source_labels"), dict) else {}).get(str(group.get("round")))
                if not frozen_label or label != frozen_label or label != metadata_label:
                    errors.append(issue("ROUND_SOURCE_BINDING_MISMATCH", f"Round {group.get('round')} for {model_id} differs from its frozen binding", model_dir))
                if not label or label not in frozen_snapshot_dirs:
                    errors.append(issue("ROUND_INVENTORY_SOURCE_MISSING", f"Round {group.get('round')} for {model_id} lacks a frozen snapshot directory", model_dir))
                    continue
                expected_round_rows = {path[len(label)+1:]: item for path, item in freeze_inventory.items() if path.startswith(label + "/")}
                actual_round_rows = {item.get("path"): item for item in group.get("files", []) if isinstance(item, dict)}
                if set(actual_round_rows) != set(expected_round_rows) or any(actual_round_rows[path].get("sha256") != expected_round_rows[path].get("sha256") or actual_round_rows[path].get("size") != expected_round_rows[path].get("size") for path in set(actual_round_rows) & set(expected_round_rows)):
                    errors.append(issue("ROUND_INVENTORY_SOURCE_MISMATCH", f"Round {group.get('round')} inventory differs from source-freeze", model_dir))
        test_ids, probe_ids = load_run_ids(model_root, errors)
        conversation_path = model_root / "conversation-summary.json"
        conversation = read_json(conversation_path, errors) if conversation_path.exists() else {"key_events": []}
        if manifest.get("task", {}).get("round_count", 0) > 1 and not conversation_path.exists():
            material_gap_keys.add(f"model:{model_id}:conversation_summary_missing")
            warnings.append(issue("MODEL_CONVERSATION_MISSING", f"{model_id} lacks conversation-summary.json", model_dir))
        if conversation_path.exists() and isinstance(conversation, dict):
            source_path = model_meta.get("conversation_source_path") if isinstance(model_meta, dict) else None
            if source_path != (frozen_model_sources.get(model_id, {}) or {}).get("conversation_source_path"):
                errors.append(issue("CONVERSATION_BINDING_MISMATCH", f"{model_id} conversation path differs from frozen model binding", model_dir))
            frozen = freeze_inventory.get(source_path) if source_path in frozen_conversations else None
            if not frozen or conversation.get("source_sha256") != frozen.get("sha256"):
                errors.append(issue("CONVERSATION_SOURCE_MISMATCH", f"{model_id} conversation summary is not bound to a frozen source", model_dir))
            blob_relative = conversation.get("source_blob_path", "")
            if not safe_relative(str(blob_relative)):
                errors.append(issue("CONVERSATION_BLOB_INVALID", f"{model_id} conversation summary lacks a safe source_blob_path", model_dir))
            else:
                try:
                    blob_raw = (root / PurePosixPath(str(blob_relative))).read_bytes()
                    rebuilt = summarize_conversation_bytes(blob_raw)
                    comparable = {key: conversation.get(key) for key in ("schema_version", "source_sha256", "key_events", "structural_counts", "statistics")}
                    if hashlib.sha256(blob_raw).hexdigest() != (frozen or {}).get("sha256") or comparable != rebuilt:
                        errors.append(issue("CONVERSATION_BLOB_MISMATCH", f"{model_id} conversation summary is not reproducible from its source blob", model_dir))
                except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
                    errors.append(issue("CONVERSATION_BLOB_INVALID", f"{model_id} conversation source blob is missing or invalid", model_dir))
        msg_refs = {event.get("msg_ref"): event for event in (conversation or {}).get("key_events", []) if isinstance(event, dict) and event.get("msg_ref")}
        human_rows = read_jsonl(model_root / "human-observations.jsonl", errors, required=False)
        observation_ids: set[str] = set()
        for item in human_rows:
            observation_id = item.get("observation_id")
            if not observation_id or observation_id in observation_ids:
                errors.append(issue("OBSERVATION_ID_DUPLICATE", f"Missing or duplicate observation_id {observation_id!r}", model_dir))
            observation_ids.add(observation_id)
            needed_observation = ("observer", "observed_at", "steps", "observed_result", "observed_version")
            if any(not item.get(key) for key in needed_observation) or not isinstance(item.get("steps"), list):
                errors.append(issue("HUMAN_OBSERVATION_INCOMPLETE", f"Observation {observation_id!r} lacks actor/time/steps/result/version", model_dir))
        rows = read_jsonl(model_root / "rubric-evidence.jsonl", errors)
        required_snapshot_rounds = set(range(1, manifest.get("task", {}).get("round_count", 0)))
        present_snapshot_rounds = {number for number, _ in round_files}
        for missing_round in sorted(required_snapshot_rounds - present_snapshot_rounds):
            material_gap_keys.add(f"model:{model_id}:round_{missing_round}_snapshot_inventory_missing")
        seen: set[str] = set()
        for row in rows:
            evidence_records += 1
            missing_fields = sorted(ROW_REQUIRED - set(row))
            if missing_fields:
                errors.append(issue("EVIDENCE_ROW_INCOMPLETE", f"Missing fields {missing_fields}", model_dir))
            rubric_id = row.get("rubric_id")
            if rubric_id in seen:
                errors.append(issue("EVIDENCE_DUPLICATE", f"Duplicate row {model_id}/{rubric_id}", model_dir))
            seen.add(rubric_id)
            rubric = rubrics.get(rubric_id)
            if not rubric:
                errors.append(issue("RUBRIC_REFERENCE_MISSING", f"Unknown rubric {rubric_id}", model_dir))
                continue
            expected_round = rubric_round(rubric)
            if row.get("rubric_round") != expected_round:
                errors.append(issue("RUBRIC_ROUND_MISMATCH", f"{rubric_id} must use round {expected_round}", model_dir))
            if row.get("model_id") != model_id or row.get("criterion") != rubric.get("criterion"):
                errors.append(issue("EVIDENCE_IDENTITY_MISMATCH", f"Identity/criterion drift for {model_id}/{rubric_id}", model_dir))
            score = row.get("suggested_score")
            if score not in (0, 1, None) or isinstance(score, bool):
                errors.append(issue("INVALID_SCORE", f"Score must be 0, 1, or null for {model_id}/{rubric_id}", model_dir))
            if score is None:
                unknown += 1
            if score is not None and expected_round in required_snapshot_rounds and expected_round not in present_snapshot_rounds:
                errors.append(issue("PREFINAL_SCORE_WITHOUT_SNAPSHOT", f"{model_id}/{rubric_id} cannot be scored without its frozen round snapshot", model_dir))
            if row.get("disposition") not in DISPOSITIONS or row.get("coverage") not in COVERAGES or row.get("confidence") not in CONFIDENCES or row.get("reason_code") not in REASON_CODES:
                errors.append(issue("EVIDENCE_ROW_ENUM_INVALID", f"Invalid disposition/coverage/confidence/reason_code for {model_id}/{rubric_id}", model_dir))
            if not isinstance(row.get("human_check_needed"), bool) or not isinstance(row.get("adjudication_ids"), list) or not isinstance(row.get("limitations"), list):
                errors.append(issue("EVIDENCE_ROW_TYPE_INVALID", f"Invalid boolean/list fields for {model_id}/{rubric_id}", model_dir))
            if not str(row.get("fact_summary", "")).strip():
                errors.append(issue("FACT_SUMMARY_MISSING", f"{model_id}/{rubric_id} needs a non-empty fact_summary", model_dir))
            if row.get("coverage") in {"missing", "unsafe_to_test"} and score is not None:
                errors.append(issue("EVIDENCE_STATE_CONFLICT", f"{model_id}/{rubric_id} cannot have a numeric score with {row.get('coverage')} coverage", model_dir))
            positive_reasons = {"implemented_and_verified", "implemented_static_only"}
            negative_reasons = {"behavior_failed", "missing_implementation", "violates_explicit_constraint"}
            unresolved_reasons = {"insufficient_evidence", "subjective_needs_human", "unsafe_to_test", "policy_ambiguous", "model_fallback_zero"}
            if (score == 1 and row.get("reason_code") not in positive_reasons) or (score == 0 and row.get("reason_code") not in negative_reasons) or (score is None and row.get("reason_code") not in unresolved_reasons):
                errors.append(issue("EVIDENCE_STATE_CONFLICT", f"{model_id}/{rubric_id} score and reason_code conflict", model_dir))
            if row.get("disposition") == "fallback_zero":
                if score is not None or not row.get("human_check_needed"):
                    errors.append(issue("FALLBACK_REQUIRES_HUMAN", f"Fallback candidate {model_id}/{rubric_id} must remain null and require human confirmation", model_dir))
            evidence = row.get("evidence") if isinstance(row.get("evidence"), list) else []
            if score is not None and not evidence:
                errors.append(issue("SCORE_WITHOUT_EVIDENCE", f"Score for {model_id}/{rubric_id} has no evidence", model_dir))
            directions: set[str] = set()
            verified_run_support = False
            failed_behavior_evidence = False
            for item in evidence:
                evidence_id = item.get("evidence_id")
                kind = item.get("type")
                direction = item.get("direction")
                if not evidence_id or evidence_id in all_evidence_ids:
                    errors.append(issue("EVIDENCE_ID_DUPLICATE", f"Missing or duplicate evidence_id {evidence_id!r}", model_dir))
                else:
                    all_evidence_ids.add(evidence_id)
                if kind not in EVIDENCE_TYPES:
                    errors.append(issue("EVIDENCE_TYPE_INVALID", f"Invalid evidence type {kind!r}", model_dir))
                    continue
                if direction not in DIRECTIONS:
                    errors.append(issue("EVIDENCE_DIRECTION_INVALID", f"Invalid direction {direction!r}", model_dir))
                directions.add(direction)
                if not str(item.get("fact", "")).strip():
                    errors.append(issue("EVIDENCE_FACT_MISSING", f"Evidence {evidence_id!r} needs a factual statement", model_dir))
                if kind == "static_line":
                    needed = {"source_state", "round", "path", "line_start", "line_end", "file_sha256", "source_blob_path", "excerpt", "fact"}
                    line_start = item.get("line_start")
                    line_end = item.get("line_end")
                    if needed - set(item) or not str(item.get("excerpt", "")).strip() or not isinstance(line_start, int) or isinstance(line_start, bool) or not isinstance(line_end, int) or isinstance(line_end, bool) or line_start < 1 or line_end < line_start:
                        errors.append(issue("STATIC_EVIDENCE_INCOMPLETE", f"Incomplete static evidence {evidence_id}", model_dir))
                    relative = str(item.get("path", ""))
                    if not safe_relative(relative):
                        errors.append(issue("UNSAFE_PATH", "Unsafe static evidence path", relative))
                    source_state = item.get("source_state")
                    if source_state not in {"round_end", "final"}:
                        errors.append(issue("STATIC_SOURCE_STATE_INVALID", f"Static evidence {evidence_id} has invalid source_state", model_dir))
                    expected_hash = round_files.get((item.get("round"), relative)) if source_state == "round_end" else final_files.get(relative) if source_state == "final" else None
                    if expected_hash != item.get("file_sha256"):
                        errors.append(issue("STATIC_SOURCE_HASH_MISMATCH", f"Static evidence {evidence_id} is not backed by inventory", model_dir))
                    if item.get("source_state") == "round_end" and item.get("round") != expected_round:
                        errors.append(issue("ROUND_LEAKAGE", f"{rubric_id} uses round {item.get('round')}", model_dir))
                    if item.get("source_state") == "final" and expected_round != manifest.get("task", {}).get("round_count") and direction != "context":
                        errors.append(issue("ROUND_LEAKAGE", f"{rubric_id} uses final output for a pre-final-round decision", model_dir))
                    if item.get("excerpt_sha256") and hashlib.sha256(item.get("excerpt", "").encode("utf-8")).hexdigest() != item.get("excerpt_sha256"):
                        errors.append(issue("EXCERPT_HASH_MISMATCH", f"Excerpt hash mismatch {evidence_id}", model_dir))
                    blob_relative = item.get("source_blob_path", "")
                    if not safe_relative(str(blob_relative)):
                        errors.append(issue("SOURCE_BLOB_INVALID", f"Static evidence {evidence_id} has an unsafe source blob path", model_dir))
                    else:
                        blob = root / PurePosixPath(str(blob_relative))
                        try:
                            blob_raw = blob.read_bytes()
                            blob_lines = blob_raw.decode("utf-8").splitlines()
                            range_valid = isinstance(line_start, int) and not isinstance(line_start, bool) and isinstance(line_end, int) and not isinstance(line_end, bool) and 1 <= line_start <= line_end <= len(blob_lines)
                            exact_excerpt = "\n".join(blob_lines[line_start - 1:line_end]) if range_valid else None
                            if not range_valid or sha256(blob) != item.get("file_sha256") or exact_excerpt != item.get("excerpt"):
                                errors.append(issue("SOURCE_BLOB_MISMATCH", f"Static evidence {evidence_id} excerpt is not reproduced by its content-addressed source blob", model_dir))
                        except (OSError, UnicodeDecodeError):
                            errors.append(issue("SOURCE_BLOB_INVALID", f"Static evidence {evidence_id} source blob is missing or not UTF-8", model_dir))
                elif kind in {"self_test_run", "probe_run"}:
                    run_id = item.get("run_id")
                    valid = test_ids if kind == "self_test_run" else probe_ids
                    if not run_id or run_id not in valid:
                        errors.append(issue("RUN_REFERENCE_MISSING", f"Unknown run_id {run_id!r}", model_dir))
                    else:
                        run = valid[run_id]
                        run_state = run.get("source_state")
                        run_round = run.get("round")
                        if run_state == "round_end":
                            expected_inventory = {path: digest for (round_number, path), digest in round_files.items() if round_number == run_round}
                        elif run_state == "final":
                            expected_inventory = final_files
                        else:
                            expected_inventory = {}
                        inventory_digest = hashlib.sha256("\n".join(f"{digest}  {path}" for path, digest in sorted(expected_inventory.items())).encode("utf-8")).hexdigest()
                        if not expected_inventory or run.get("source_inventory_digest") != inventory_digest:
                            errors.append(issue("RUN_SOURCE_UNBOUND", f"Run {run_id!r} is not bound to a known source inventory", model_dir))
                        if direction != "context" and ((run_state == "round_end" and run_round != expected_round) or (run_state == "final" and expected_round != manifest.get("task", {}).get("round_count"))):
                            errors.append(issue("ROUND_LEAKAGE", f"Run {run_id!r} uses a later source state for {rubric_id}", model_dir))
                        assertions = run.get("assertions", []) if isinstance(run.get("assertions"), list) else []
                        matching = [assertion for assertion in assertions if isinstance(assertion, dict) and assertion.get("rubric_id") == rubric_id]
                        if direction != "context" and run.get("status") not in {"passed", "failed"}:
                            errors.append(issue("RUN_RESULT_DIRECTION_MISMATCH", f"Error/timeout run {run_id!r} may only be context", model_dir))
                        elif direction != "context" and not matching:
                            errors.append(issue("RUN_RUBRIC_UNBOUND", f"Run {run_id!r} has no assertion bound to {rubric_id}", model_dir))
                        elif direction == "support" and (run.get("status") != "passed" or not any(assertion.get("passed") is True for assertion in matching)):
                            errors.append(issue("RUN_RESULT_DIRECTION_MISMATCH", f"Run {run_id!r} cannot support {rubric_id}", model_dir))
                        elif direction == "refute" and not any(assertion.get("passed") is False for assertion in matching):
                            errors.append(issue("RUN_RESULT_DIRECTION_MISMATCH", f"Run {run_id!r} cannot refute {rubric_id}", model_dir))
                        elif direction == "support" and run.get("status") == "passed" and any(assertion.get("passed") is True for assertion in matching):
                            verified_run_support = True
                        elif direction == "refute" and any(assertion.get("passed") is False for assertion in matching):
                            failed_behavior_evidence = True
                elif kind == "conversation":
                    event = msg_refs.get(item.get("msg_ref"))
                    if not event:
                        errors.append(issue("MESSAGE_REFERENCE_MISSING", f"Unknown msg_ref {item.get('msg_ref')!r}", model_dir))
                    elif any(item.get(key) != event.get(key) for key in ("flat_msg_index", "role", "excerpt", "event_type")):
                        errors.append(issue("MESSAGE_CONTENT_MISMATCH", f"Conversation evidence {evidence_id} differs from its summarized source event", model_dir))
                    elif direction != "context" and (not isinstance(event.get("round"), int) or event.get("round") > expected_round):
                        errors.append(issue("ROUND_LEAKAGE", f"Conversation evidence {evidence_id} lacks an allowed round", model_dir))
                elif kind == "human_note":
                    if item.get("observation_id") not in observation_ids:
                        errors.append(issue("OBSERVATION_REFERENCE_MISSING", f"Unknown observation_id {item.get('observation_id')!r}", model_dir))
                    else:
                        observation = next((value for value in human_rows if value.get("observation_id") == item.get("observation_id")), {})
                        if item.get("quote") != observation.get("observed_result"):
                            errors.append(issue("OBSERVATION_QUOTE_MISMATCH", f"Human note {evidence_id} quote differs from the observation result", model_dir))
                    if not row.get("human_check_needed"):
                        errors.append(issue("REMOTE_HUMAN_NOTE_UNCONFIRMED", f"Remote human_note {evidence_id} must remain pending local human confirmation", model_dir))
                    if direction == "refute":
                        failed_behavior_evidence = True
                elif kind == "snapshot_diff":
                    if not isinstance(item.get("from_round"), int) or not isinstance(item.get("to_round"), int) or not safe_relative(str(item.get("path", ""))) or not re.fullmatch(r"[0-9a-f]{64}", str(item.get("before_sha256", ""))) or not re.fullmatch(r"[0-9a-f]{64}", str(item.get("after_sha256", ""))):
                        errors.append(issue("SNAPSHOT_DIFF_INCOMPLETE", f"Incomplete snapshot diff {evidence_id}", model_dir))
                    elif not (1 <= item.get("from_round") < item.get("to_round") <= expected_round):
                        errors.append(issue("SNAPSHOT_DIFF_RANGE_INVALID", f"Snapshot diff {evidence_id} must move forward within the rubric round", model_dir))
                    elif direction != "context" and item.get("to_round") > expected_round:
                        errors.append(issue("ROUND_LEAKAGE", f"Snapshot diff {evidence_id} uses a later round", model_dir))
                    elif round_files.get((item.get("from_round"), item.get("path"))) != item.get("before_sha256") or round_files.get((item.get("to_round"), item.get("path"))) != item.get("after_sha256"):
                        errors.append(issue("SNAPSHOT_DIFF_UNBACKED", f"Snapshot diff {evidence_id} hashes do not match inventories", model_dir))
                elif kind == "inventory_fact":
                    if not item.get("metric") or "value" not in item or not item.get("basis"):
                        errors.append(issue("INVENTORY_FACT_INCOMPLETE", f"Incomplete inventory fact {evidence_id}", model_dir))
                    if direction == "confirm_missing":
                        relative = str(item.get("path", ""))
                        if item.get("metric") != "path_exists" or item.get("value") is not False or item.get("source_state") != "round_end" or item.get("round") != expected_round or not safe_relative(relative) or (expected_round, relative) in round_files:
                            errors.append(issue("CONFIRM_MISSING_UNBACKED", f"Missing-path evidence {evidence_id} is not backed by the matching round inventory", model_dir))
                    elif direction != "context":
                        errors.append(issue("INVENTORY_FACT_NOT_SCORING", f"Inventory fact {evidence_id} cannot support/refute a score unless it mechanically confirms a missing path", model_dir))
            if score == 1 and "support" not in directions:
                errors.append(issue("SCORE_DIRECTION_MISMATCH", f"Score 1 lacks support for {model_id}/{rubric_id}", model_dir))
            if score == 0 and not ({"refute", "confirm_missing"} & directions):
                errors.append(issue("SCORE_DIRECTION_MISMATCH", f"Score 0 lacks refute/confirm_missing for {model_id}/{rubric_id}", model_dir))
            if row.get("reason_code") == "implemented_and_verified" and not verified_run_support:
                errors.append(issue("VERIFIED_REASON_UNBACKED", f"{model_id}/{rubric_id} claims implemented_and_verified without a passed rubric-bound run", model_dir))
            if row.get("reason_code") == "behavior_failed" and not failed_behavior_evidence:
                errors.append(issue("BEHAVIOR_FAILURE_UNBACKED", f"{model_id}/{rubric_id} claims behavior_failed without a failed rubric-bound run or refuting human observation", model_dir))
            if row.get("reason_code") in {"policy_ambiguous", "insufficient_evidence", "unsafe_to_test", "subjective_needs_human"} and score is not None:
                errors.append(issue("UNKNOWN_FORCED_TO_SCORE", f"Unresolved {model_id}/{rubric_id} must remain null", model_dir))
            if row.get("human_check_needed"):
                human_checks += 1
            for adj_id in row.get("adjudication_ids", []):
                adj = adjudications.get(adj_id)
                if not adj or rubric_id not in adj.get("rubric_ids", []):
                    errors.append(issue("ADJUDICATION_REFERENCE_INVALID", f"Invalid adjudication {adj_id} for {rubric_id}", model_dir))
                else:
                    adjudication_pairs.add((adj_id, model_id, rubric_id))
        if set(seen) != rubric_ids:
            errors.append(issue("EVIDENCE_COVERAGE", f"{model_id} missing={sorted(rubric_ids-seen)} extra={sorted(seen-rubric_ids)}", model_dir))

    if not model_ids:
        errors.append(issue("MODELS_MISSING", "Manifest needs at least one model", "MANIFEST.json"))
    for adj_id, adj in adjudications.items():
        unknown_adj_evidence = sorted(set(adj.get("evidence_ids", [])) - all_evidence_ids)
        if unknown_adj_evidence:
            errors.append(issue("ADJUDICATION_EVIDENCE_INVALID", f"{adj_id} references unknown evidence {unknown_adj_evidence}", "review/pending-adjudications.json"))
        for rubric_id in adj.get("rubric_ids", []) if isinstance(adj.get("rubric_ids"), list) else []:
            required_models = model_ids if adj.get("applies_to_all_models") is True else {
                model_id for linked_adj, model_id, linked_rubric in adjudication_pairs if linked_adj == adj_id and linked_rubric == rubric_id
            }
            if not required_models or any((adj_id, model_id, rubric_id) not in adjudication_pairs for model_id in required_models):
                errors.append(issue("ADJUDICATION_NOT_RECIPROCAL", f"{adj_id} must be referenced by every affected model/rubric evidence row", "review/pending-adjudications.json"))
    missing_materials = manifest.get("missing_materials")
    if not isinstance(missing_materials, list):
        errors.append(issue("MISSING_MATERIALS_INVALID", "missing_materials must be an array", "MANIFEST.json"))
    if manifest.get("task", {}).get("round_count", 0) > 1:
        if not (freeze_doc or {}).get("conversation_files"):
            material_gap_keys.add("source:conversation_files_missing")
            warnings.append(issue("CONVERSATION_MISSING", "Multi-turn process dimensions require conversation evidence", "source-freeze.json"))
        if not frozen_snapshot_dirs:
            material_gap_keys.add("source:snapshot_dirs_missing")
            warnings.append(issue("SNAPSHOTS_MISSING", "Pre-final round scoring requires frozen round snapshots", "source-freeze.json"))
    material_gaps = len(material_gap_keys)
    base_errors = bool(errors)
    if base_errors:
        derived = "incomplete"
    elif pending or unknown or human_checks or material_gaps:
        derived = "ready_for_local_review"
    else:
        derived = "ready_for_form"
    if manifest.get("package_status") != derived:
        errors.append(issue("STATUS_MISMATCH", f"Manifest={manifest.get('package_status')!r}, derived={derived!r}", "MANIFEST.json"))
    counts = {"models": len(model_ids), "rubrics": len(rubric_ids), "evidence_records": evidence_records, "evidence_items": len(all_evidence_ids), "pending_adjudications": pending, "unknown_scores": unknown, "human_checks": human_checks, "material_gaps": material_gaps}
    if require_seal:
        validate_seal(root, errors, derived, counts)
    if errors and derived != "ready_for_local_review":
        derived = "incomplete"
    return {
        "validator_version": VERSION,
        "result": "pass" if not errors else "fail",
        "derived_status": derived,
        "counts": counts,
        "material_gaps": sorted(material_gap_keys),
        "errors": errors,
        "warnings": warnings,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--allow-unsealed", action="store_true", help="For package construction only; local review must not use this flag")
    args = parser.parse_args(argv)
    report = validate_bundle(args.bundle, require_seal=not args.allow_unsealed)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(text + "\n", encoding="utf-8")
    print(text)
    if report["result"] == "fail":
        return 3
    return 0 if report["derived_status"] == "ready_for_form" else 2


if __name__ == "__main__":
    sys.exit(main())
