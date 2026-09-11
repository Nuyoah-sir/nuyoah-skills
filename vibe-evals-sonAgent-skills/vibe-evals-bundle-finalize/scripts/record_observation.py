#!/usr/bin/env python3
"""Record and validate audited machine-vision and remote-human observations.

These records form an attributable attestation trail. They prove that a complete
attestation was captured with the required provenance fields. They cannot
cryptographically prove that a human existed or that two model reads were truly
independent; the dual-read gate is an auditable independence approximation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

OBSERVATION_SCHEMA_VERSION = "2.0.0"
VISION_JSONL = "observations/machine-vision.jsonl"
HUMAN_JSONL = "observations/remote-human.jsonl"
CLASSIFICATION_JSON = "decisions/criterion-classifications.json"
MEDIA_INDEX_JSON = "observations/media-index.json"

OBJECTIVE_CLASSES = frozenset({
    "presence", "absence", "count", "literal_text", "exact_color",
    "explicit_relative_position", "explicit_size",
})
SUBJECTIVE_CLASS = "subjective_or_policy"
CLASSIFICATIONS = OBJECTIVE_CLASSES | {SUBJECTIVE_CLASS}

SUBJECTIVE_MARKERS = (
    "美观", "好看", "自然", "清晰", "协调", "可接受", "可用", "合理", "舒服",
    "aesthetic", "beautiful", "natural", "clear", "acceptable", "usable", "reasonable", "pleasant",
)
HUMAN_CLAIM_MARKERS = ("人工确认", "真人确认", "用户已确认")
VISUAL_MEDIA_ROLES = frozenset({"target", "feedback"})
CANDIDATE_MEDIA_ROLES = frozenset({"candidate_full", "candidate_crop"})
CONFIRMATION_PLACEHOLDERS = frozenset({"y", "yes", "no", "ok", "确认", "同意", "通过", "-", "n/a", "none"})

VISION_REQUIRED = frozenset({
    "observation_id", "type", "outer_package_id", "base_package_id", "base_zip_sha256",
    "source_input_digest", "task_id", "model_id", "rubric_id", "round", "criterion_sha256",
    "read_index", "invocation_id", "session_id", "independent_context", "input_media_ids",
    "tool", "model", "tool_version", "prompt_sha256", "question", "raw_response_sha256",
    "raw_response", "fact", "verdict", "confidence", "conflict", "observed_at",
})
HUMAN_REQUIRED = frozenset({
    "observation_id", "type", "outer_package_id", "base_package_id", "base_zip_sha256",
    "source_input_digest", "task_id", "model_id", "rubric_id", "round", "criterion_sha256",
    "observer", "recorded_by", "capture_method", "observed_at", "steps", "expected_result",
    "observed_result", "observed_version", "confirmation_text", "media_ids", "verdict",
})

_SHA256 = re.compile(r"[0-9a-f]{64}")
_RFC3339 = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$")


def issue(code: str, message: str, path: str) -> dict[str, str]:
    return {"code": code, "message": message, "path": path}


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def normalize_question(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def cited_media(record: dict[str, Any]) -> tuple[str, ...]:
    values = record.get("input_media_ids") if record.get("type") == "machine_vision" else record.get("media_ids")
    if not isinstance(values, list):
        return ()
    return tuple(sorted(value for value in values if isinstance(value, str)))


def parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not _RFC3339.match(value):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _contains_human_claim(value: Any) -> bool:
    if isinstance(value, str):
        return any(marker in value for marker in HUMAN_CLAIM_MARKERS)
    if isinstance(value, dict):
        return any(_contains_human_claim(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_human_claim(item) for item in value)
    return False


def required_classification(rubric: dict[str, Any], adjudication_affected: bool) -> str:
    """Return the classification a criterion is allowed to claim."""

    criterion = str(rubric.get("criterion", ""))
    if adjudication_affected or any(marker in criterion.casefold() for marker in SUBJECTIVE_MARKERS):
        return SUBJECTIVE_CLASS
    return "objective_eligible"


def validate_classification_record(
    record: Any,
    *,
    rubric: dict[str, Any] | None,
    adjudication_affected: bool,
    path: str,
) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    if not isinstance(record, dict):
        return [issue("CRITERION_CLASS_INVALID", "Classification record must be an object", path)]
    if rubric is None:
        return [issue("CRITERION_CLASS_INVALID", "Classification names an unknown rubric", path)]
    classification = record.get("classification")
    if classification not in CLASSIFICATIONS:
        return [issue("CRITERION_CLASS_REQUIRES_HUMAN", f"Unrecognized classification {classification!r}", path)]
    criterion = str(rubric.get("criterion", ""))
    forced = required_classification(rubric, adjudication_affected)
    if forced == SUBJECTIVE_CLASS:
        if classification != SUBJECTIVE_CLASS or record.get("requires_remote_human") is not True:
            errors.append(issue(
                "CRITERION_CLASS_REQUIRES_HUMAN",
                "Subjective, policy-ambiguous, or adjudication-affected criteria must be classified subjective_or_policy and require a remote human",
                path,
            ))
        return errors
    if classification == SUBJECTIVE_CLASS:
        if record.get("requires_remote_human") is not True:
            errors.append(issue(
                "CRITERION_CLASS_REQUIRES_HUMAN",
                "Declaring a criterion subjective_or_policy must require a remote human record",
                path,
            ))
        return errors
    phrase = record.get("measurable_phrase")
    if not isinstance(phrase, str) or not phrase.strip() or phrase not in criterion:
        errors.append(issue(
            "CRITERION_CLASS_INVALID",
            "An objective classification must quote a measurable phrase that appears verbatim in the criterion",
            path,
        ))
    if record.get("requires_remote_human") is not False:
        errors.append(issue(
            "CRITERION_CLASS_INVALID",
            f"An objective classification must set requires_remote_human to false",
            path,
        ))
    return errors


def _check_common_fields(record: dict[str, Any], required: frozenset[str], *, code: str, path: str) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    missing = sorted(key for key in required if key not in record)
    if missing:
        errors.append(issue(code, f"Observation record is missing fields: {missing}", path))
    verdict = record.get("verdict")
    if verdict not in (0, 1) or isinstance(verdict, bool):
        errors.append(issue(code, "verdict must be exactly 0 or 1", path))
    observed_at = parse_timestamp(record.get("observed_at"))
    if observed_at is None:
        errors.append(issue(code, "observed_at must be an RFC3339 timestamp", path))
    elif observed_at > datetime.now(timezone.utc):
        errors.append(issue(code, "observed_at must not be in the future", path))
    return errors


def validate_vision_record(
    record: Any,
    *,
    media_roles: dict[str, str],
    media_status: dict[str, str],
    path: str,
) -> list[dict[str, str]]:
    if not isinstance(record, dict):
        return [issue("VISION_RECORD_INCOMPLETE", "Vision record must be an object", path)]
    errors = _check_common_fields(record, VISION_REQUIRED, code="VISION_RECORD_INCOMPLETE", path=path)
    if _contains_human_claim(record):
        errors.append(issue(
            "HUMAN_CLAIM_FOR_NON_HUMAN",
            "A machine-vision record cannot claim 人工确认/真人确认/用户已确认",
            path,
        ))
    for key in ("observation_id", "invocation_id", "session_id", "tool", "model", "tool_version", "fact", "question", "raw_response"):
        value = record.get(key)
        if not isinstance(value, str) or not value.strip():
            errors.append(issue("VISION_RECORD_INCOMPLETE", f"{key} must be a nonempty string", path))
    for key in ("prompt_sha256", "raw_response_sha256", "criterion_sha256"):
        if not isinstance(record.get(key), str) or _SHA256.fullmatch(record.get(key, "")) is None:
            errors.append(issue("VISION_RECORD_INCOMPLETE", f"{key} must be a lowercase SHA-256", path))
    if isinstance(record.get("question"), str) and record.get("prompt_sha256") != sha256_text(record["question"]):
        errors.append(issue("VISION_RECORD_INCOMPLETE", "prompt_sha256 does not match the recorded question", path))
    if isinstance(record.get("raw_response"), str) and record.get("raw_response_sha256") != sha256_text(record["raw_response"]):
        errors.append(issue("VISION_RECORD_INCOMPLETE", "raw_response_sha256 does not match the recorded raw response", path))
    if record.get("read_index") not in (1, 2) or isinstance(record.get("read_index"), bool):
        errors.append(issue("VISION_READ_COUNT", "read_index must be 1 or 2", path))
    if record.get("independent_context") is not True:
        errors.append(issue("VISION_READ_NOT_INDEPENDENT", "independent_context must be true", path))
    if record.get("confidence") != "high":
        errors.append(issue("VISION_LOW_CONFIDENCE", "Only high-confidence reads may support a machine verdict", path))
    if record.get("conflict") is not False:
        errors.append(issue("VISION_CONFLICT", "A supporting read must set conflict to false", path))
    media = cited_media(record)
    if not media:
        errors.append(issue("VISION_RECORD_INCOMPLETE", "At least one cited review media id is required", path))
    unknown = [media_id for media_id in media if media_id not in media_roles]
    if unknown:
        errors.append(issue("VISION_RECORD_INCOMPLETE", f"Cited media is not registered: {unknown}", path))
    elif not any(media_status.get(media_id) == "registered" for media_id in media):
        errors.append(issue("VISION_RECORD_INCOMPLETE", "Cited media must be registered, not merely planned", path))
    elif not (VISUAL_MEDIA_ROLES & {media_roles[media_id] for media_id in media}):
        errors.append(issue("VISION_RECORD_INCOMPLETE", "A visual read must cite the frozen target/feedback media", path))
    elif not (CANDIDATE_MEDIA_ROLES & {media_roles[media_id] for media_id in media}):
        errors.append(issue("VISION_RECORD_INCOMPLETE", "A visual read must cite the candidate render", path))
    return errors


def validate_vision_pair(
    reads: list[Any],
    *,
    classification: dict[str, Any] | None,
    adjudication_affected: bool,
    evidence_direction: str | None,
    path: str,
) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    ordered = [row for row in reads if isinstance(row, dict)]
    indexes = sorted(row.get("read_index") for row in ordered)
    if len(ordered) != 2 or indexes != [1, 2]:
        errors.append(issue("VISION_READ_COUNT", "Exactly two machine-vision reads (read_index 1 and 2) are required", path))
        return errors
    first, second = sorted(ordered, key=lambda row: row["read_index"])
    if cited_media(first) != cited_media(second):
        errors.append(issue("VISION_CONFLICT", "Both reads must cite the same media set", path))
    if first.get("invocation_id") == second.get("invocation_id") or first.get("session_id") == second.get("session_id"):
        errors.append(issue("VISION_READ_NOT_INDEPENDENT", "The two reads must not share an invocation or session id", path))
    if (first.get("prompt_sha256") == second.get("prompt_sha256")
            or normalize_question(str(first.get("question", ""))) == normalize_question(str(second.get("question", "")))):
        errors.append(issue("VISION_READ_NOT_INDEPENDENT", "The two reads must use different normalized questions and prompt digests", path))
    if first.get("independent_context") is not True or second.get("independent_context") is not True:
        errors.append(issue("VISION_READ_NOT_INDEPENDENT", "Both reads must declare independent_context", path))
    first_answer = str(first.get("fact", ""))
    second_raw = str(second.get("raw_response", ""))
    if len(first_answer.strip()) >= 4 and first_answer in second_raw:
        errors.append(issue("VISION_READ_NOT_INDEPENDENT", "The second read repeats the first read's answer", path))
    if first.get("verdict") != second.get("verdict"):
        errors.append(issue("VISION_CONFLICT", "The two reads must agree on the verdict", path))
    expected = SUBJECTIVE_CLASS if adjudication_affected else (classification or {}).get("classification")
    if expected is None or expected not in OBJECTIVE_CLASSES:
        errors.append(issue("VISION_POLICY_REQUIRES_HUMAN", "A subjective or policy-ambiguous criterion requires a remote human record", path))
    elif (classification or {}).get("requires_remote_human") is not False:
        errors.append(issue("VISION_POLICY_REQUIRES_HUMAN", "The criterion classification requires a remote human record", path))
    if evidence_direction in {"support", "refute", "confirm_missing"}:
        verdict = first.get("verdict")
        if (evidence_direction == "support" and verdict != 1) or (evidence_direction != "support" and verdict != 0):
            errors.append(issue(
                "VISION_STATIC_CONFLICT",
                f"The visual verdict contradicts the frozen static evidence direction {evidence_direction!r}",
                path,
            ))
    return errors


def validate_human_record(
    record: Any,
    *,
    media_roles: dict[str, str],
    media_status: dict[str, str],
    path: str,
) -> list[dict[str, str]]:
    if not isinstance(record, dict):
        return [issue("HUMAN_RECORD_INCOMPLETE", "Human record must be an object", path)]
    errors = _check_common_fields(record, HUMAN_REQUIRED, code="HUMAN_RECORD_INCOMPLETE", path=path)
    if record.get("type") != "remote_human":
        errors.append(issue("HUMAN_RECORD_INCOMPLETE", "type must be remote_human", path))
    for key in ("observation_id", "observer", "recorded_by", "capture_method", "expected_result", "observed_result", "observed_version"):
        if not isinstance(record.get(key), str) or not record[key].strip():
            errors.append(issue("HUMAN_RECORD_INCOMPLETE", f"{key} must be a nonempty string", path))
    steps = record.get("steps")
    if not isinstance(steps, list) or not steps or any(not isinstance(step, str) or not step.strip() for step in steps):
        errors.append(issue("HUMAN_RECORD_INCOMPLETE", "steps must be a nonempty list of nonempty strings", path))
    confirmation = record.get("confirmation_text")
    if not isinstance(confirmation, str) or not confirmation.strip():
        errors.append(issue("HUMAN_RECORD_INCOMPLETE", "confirmation_text must contain the human's original words", path))
    elif confirmation.strip().casefold() in CONFIRMATION_PLACEHOLDERS:
        errors.append(issue("HUMAN_RECORD_INCOMPLETE", "confirmation_text looks defaulted rather than human-authored", path))
    media = cited_media(record)
    if not media:
        errors.append(issue("HUMAN_RECORD_INCOMPLETE", "At least one cited review media id is required", path))
    unknown = [media_id for media_id in media if media_id not in media_roles]
    if unknown:
        errors.append(issue("HUMAN_RECORD_INCOMPLETE", f"Cited media is not registered: {unknown}", path))
    elif not any(media_status.get(media_id) == "registered" for media_id in media):
        errors.append(issue("HUMAN_RECORD_INCOMPLETE", "Cited media must be registered, not merely planned", path))
    version = record.get("observed_version")
    if isinstance(version, str) and version.strip():
        if record.get("base_package_id") not in version or not any(media_id in version for media_id in media):
            errors.append(issue(
                "HUMAN_RECORD_INCOMPLETE",
                "observed_version must name the observed base package id and the observed render/media id",
                path,
            ))
    return errors


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    """Append one validated record atomically enough to never rewrite earlier lines."""

    payload = json.dumps(record, ensure_ascii=False, sort_keys=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(payload + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def new_observation_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4()}"


def interactive_stdin_available() -> bool:
    """Return True only when a real interactive console is attached.

    ``sys.stdin.isatty()`` reports True for the Windows NUL device, so a weak
    agent could otherwise satisfy the interactive attestation path by piping.
    """

    if os.name != "nt":
        return sys.stdin.isatty()
    import ctypes

    handle = ctypes.windll.kernel32.GetStdHandle(-10)
    mode = ctypes.c_uint()
    return bool(ctypes.windll.kernel32.GetConsoleMode(ctypes.c_void_p(handle), ctypes.byref(mode)))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    vision = subparsers.add_parser("vision", help="append one provider-returned machine-vision read")
    vision.add_argument("root", type=Path)
    vision.add_argument("--record", type=Path, required=True, help="JSON file holding the validated record")
    human = subparsers.add_parser("human", help="interactively record one remote-human attestation")
    human.add_argument("root", type=Path)
    human.add_argument("--record", type=Path, required=True, help="JSON file holding the attestation without confirmation_text")
    args = parser.parse_args(argv)
    if args.command == "human" and not interactive_stdin_available():
        print("HUMAN_CONFIRMATION_NOT_INTERACTIVE: run this subcommand in an interactive terminal", file=sys.stderr)
        return 3
    try:
        record = json.loads(args.record.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        print(f"OBSERVATION_RECORD_UNREADABLE: {exc}", file=sys.stderr)
        return 4
    if not isinstance(record, dict):
        print("OBSERVATION_RECORD_INVALID: record must be an object", file=sys.stderr)
        return 4
    if args.command == "human":
        print("Type the human's original confirmation words, then press Enter:")
        confirmation = input()
        if not confirmation.strip():
            print("HUMAN_CONFIRMATION_EMPTY: refusing to invent an attestation", file=sys.stderr)
            return 3
        record["confirmation_text"] = confirmation
        target = Path(args.root) / HUMAN_JSONL
    else:
        target = Path(args.root) / VISION_JSONL
    append_jsonl(target, record)
    print(json.dumps({"appended": str(target), "observation_id": record.get("observation_id")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "CLASSIFICATIONS", "CLASSIFICATION_JSON", "HUMAN_JSONL", "HUMAN_REQUIRED",
    "OBJECTIVE_CLASSES", "OBSERVATION_SCHEMA_VERSION", "SUBJECTIVE_CLASS", "VISION_JSONL",
    "VISION_REQUIRED", "append_jsonl", "cited_media", "issue", "new_observation_id",
    "interactive_stdin_available", "normalize_question", "parse_timestamp",
    "required_classification", "sha256_text",
    "validate_classification_record", "validate_human_record", "validate_vision_pair",
    "validate_vision_record",
]
