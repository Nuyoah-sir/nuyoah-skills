#!/usr/bin/env python3
"""Validate a request-scoped Vibe Evals evidence delta against a sealed base bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path, PurePosixPath

from validate_bundle import EVIDENCE_TYPES, DIRECTIONS, load_run_ids, safe_relative, sha256, validate_bundle

STATUSES = {"fulfilled", "partial", "unresolved"}
REASONS = {"evidence_collected", "partial_evidence", "no_isolation", "requires_human_ruling", "method_not_allowed", "not_found"}
EXCLUDES = {"DELTA-READY.json", "integrity/files.sha256", "integrity/validation-report.json"}


def delta_digest(base: Path) -> str:
    return hashlib.sha256((base / "integrity/files.sha256").read_bytes()).hexdigest()


def validate_delta(delta_dir: str | Path, base_dir: str | Path, requests_path: str | Path, require_seal: bool = True) -> dict:
    delta = Path(delta_dir).resolve()
    base = Path(base_dir).resolve()
    original_requests = Path(requests_path).resolve()
    errors: list[dict] = []
    base_report = validate_bundle(base, require_seal=True)
    if base_report["result"] != "pass":
        errors.append({"code": "BASE_INVALID", "message": "Base bundle failed sealed validation"})
        return {"result": "fail", "errors": errors, "counts": {}}
    try:
        manifest = json.loads((base / "MANIFEST.json").read_text(encoding="utf-8"))
        meta = json.loads((delta / "DELTA.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        return {"result": "fail", "errors": [{"code": "DELTA_INVALID", "message": str(exc)}], "counts": {}}
    if meta.get("schema") != "vibe-evals-evidence-delta" or meta.get("schema_version") != "1.0.0":
        errors.append({"code": "DELTA_SCHEMA", "message": "Unsupported delta schema"})
    if meta.get("base_package_id") != manifest.get("package_id") or meta.get("base_digest") != delta_digest(base):
        errors.append({"code": "DELTA_BASE_MISMATCH", "message": "Delta base identity/digest mismatch"})
    if meta.get("source_input_digest") != manifest.get("source_input_digest"):
        errors.append({"code": "SOURCE_CHANGED", "message": "Delta source digest differs from base"})
    model_map = {item["model_id"]: item for item in manifest["models"]}
    rubric_ids = set()
    rubric_rounds = {}
    inventory = {}
    old_ids = set()
    for entry in manifest["inputs"]["rubrics"]:
        for item in json.loads((base / entry["path"]).read_text(encoding="utf-8")):
            rubric_ids.add(item["id"])
            rubric_rounds[item["id"]] = item.get("round", entry["round"])
    for model_id, model in model_map.items():
        model_root = base / model["directory"]
        inv = json.loads((model_root / "inventory.json").read_text(encoding="utf-8"))
        inventory[model_id] = {
            "rounds": {(group["round"], item["path"]): item["sha256"] for group in inv.get("rounds", []) for item in group.get("files", [])},
            "final": {item["path"]: item["sha256"] for item in inv.get("final", {}).get("files", [])},
        }
        for line in (model_root / "rubric-evidence.jsonl").read_text(encoding="utf-8").splitlines():
            if line.strip():
                old_ids.update(item.get("evidence_id") for item in json.loads(line).get("evidence", []))
    response_path = delta / "responses.jsonl"
    responses = []
    try:
        responses = [json.loads(line) for line in response_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        errors.append({"code": "RESPONSES_INVALID", "message": str(exc)})
    if meta.get("response_count") != len(responses):
        errors.append({"code": "RESPONSE_COUNT", "message": "response_count mismatch"})
    request_map = {}
    request_copy = delta / "request-copy.json"
    try:
        if request_copy.read_bytes() != original_requests.read_bytes():
            errors.append({"code": "REQUEST_ORIGINAL_MISMATCH", "message": "request-copy.json is not byte-identical to the retained local evidence_requests.json"})
        request_value = json.loads(request_copy.read_text(encoding="utf-8"))
        if hashlib.sha256(request_copy.read_bytes()).hexdigest() != meta.get("request_sha256"):
            errors.append({"code": "REQUEST_DIGEST_MISMATCH", "message": "request-copy.json differs from request_sha256"})
        if request_value.get("base_package_id") != manifest.get("package_id"):
            errors.append({"code": "REQUEST_BASE_MISMATCH", "message": "request-copy.json targets another package"})
        request_map = {item.get("request_id"): item for item in request_value.get("requests", []) if isinstance(item, dict)}
    except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
        errors.append({"code": "REQUEST_COPY_INVALID", "message": str(exc)})
    request_ids = set()
    new_ids = set()
    referenced_delta_blobs: set[str] = set()
    run_ids = {}
    for model_id in model_map:
        delta_model_root = delta / "models" / model_id
        run_ids[model_id] = load_run_ids(delta_model_root, errors) if delta_model_root.exists() else (set(), set())
    for response in responses:
        request_id = response.get("request_id")
        model_id = response.get("model_id")
        rubric_id = response.get("rubric_id")
        if not request_id or request_id in request_ids:
            errors.append({"code": "REQUEST_ID_DUPLICATE", "message": f"Missing/duplicate {request_id!r}"})
        request_ids.add(request_id)
        if model_id not in model_map or rubric_id not in rubric_ids:
            errors.append({"code": "TARGET_INVALID", "message": f"Unknown {model_id}/{rubric_id}"})
        request = request_map.get(request_id)
        if not request or request.get("model_id") != model_id or request.get("rubric_id") != rubric_id:
            errors.append({"code": "RESPONSE_SCOPE_MISMATCH", "message": f"Response {request_id} does not match request-copy.json"})
            request = {}
        if response.get("status") not in STATUSES or response.get("reason_code") not in REASONS:
            errors.append({"code": "RESPONSE_ENUM", "message": f"Invalid status/reason for {request_id}"})
        if response.get("status") == "unresolved" and response.get("new_evidence"):
            errors.append({"code": "UNRESOLVED_HAS_EVIDENCE", "message": f"{request_id} cannot add evidence"})
        if response.get("status") == "fulfilled" and not response.get("new_evidence"):
            errors.append({"code": "FULFILLED_WITHOUT_EVIDENCE", "message": f"{request_id} is fulfilled but adds no evidence"})
        if response.get("status") == "partial" and not response.get("new_evidence"):
            errors.append({"code": "PARTIAL_WITHOUT_EVIDENCE", "message": f"{request_id} is partial but adds no evidence"})
        if response.get("status") != "fulfilled" and "proposed_update" in response:
            errors.append({"code": "NONFULFILLED_UPDATE_FORBIDDEN", "message": f"{request_id} cannot update scoring state unless fulfilled"})
        directions = set()
        for item in response.get("new_evidence", []):
            evidence_id = item.get("evidence_id")
            if not evidence_id or evidence_id in old_ids or evidence_id in new_ids:
                errors.append({"code": "EVIDENCE_ID_DUPLICATE", "message": f"Duplicate evidence {evidence_id!r}"})
            new_ids.add(evidence_id)
            if item.get("type") not in EVIDENCE_TYPES or item.get("direction") not in DIRECTIONS:
                errors.append({"code": "EVIDENCE_INVALID", "message": f"Invalid evidence type/direction for {evidence_id}"})
            if item.get("type") not in request.get("allowed_methods", []):
                errors.append({"code": "METHOD_NOT_ALLOWED", "message": f"{request_id} did not allow {item.get('type')!r}"})
            directions.add(item.get("direction"))
            if item.get("type") == "static_line":
                relative = item.get("path", "")
                expected = None
                if item.get("source_state") == "round_end":
                    expected = inventory.get(model_id, {}).get("rounds", {}).get((item.get("round"), relative))
                    if item.get("round") != rubric_rounds.get(rubric_id):
                        errors.append({"code": "ROUND_LEAKAGE", "message": f"{request_id} uses the wrong round"})
                elif item.get("source_state") == "final":
                    expected = inventory.get(model_id, {}).get("final", {}).get(relative)
                else:
                    errors.append({"code": "STATIC_SOURCE_STATE_INVALID", "message": f"Invalid source_state for {evidence_id}"})
                blob_relative = item.get("source_blob_path", "")
                delta_blob = delta / PurePosixPath(blob_relative) if safe_relative(str(blob_relative)) else delta / "__invalid__"
                if delta_blob.is_file():
                    referenced_delta_blobs.add(str(blob_relative))
                    blob = delta_blob
                else:
                    blob = (base / PurePosixPath(blob_relative)) if safe_relative(str(blob_relative)) else delta / "__invalid__"
                blob_valid = False
                try:
                    raw = blob.read_bytes()
                    lines = raw.decode("utf-8").splitlines()
                    start, end = item.get("line_start"), item.get("line_end")
                    blob_valid = (isinstance(start, int) and not isinstance(start, bool) and isinstance(end, int) and not isinstance(end, bool)
                                  and 1 <= start <= end <= len(lines) and hashlib.sha256(raw).hexdigest() == item.get("file_sha256")
                                  and "\n".join(lines[start - 1:end]) == item.get("excerpt"))
                except (OSError, UnicodeDecodeError):
                    blob_valid = False
                if not safe_relative(relative) or expected != item.get("file_sha256") or not item.get("excerpt") or not blob_valid:
                    errors.append({"code": "STATIC_EVIDENCE_INVALID", "message": f"Unbacked static evidence {evidence_id}"})
            elif item.get("type") in {"self_test_run", "probe_run"}:
                test_ids, probe_ids = run_ids.get(model_id, (set(), set()))
                valid = test_ids if item.get("type") == "self_test_run" else probe_ids
                if item.get("run_id") not in valid:
                    errors.append({"code": "RUN_REFERENCE_MISSING", "message": f"Unknown delta run {item.get('run_id')!r}"})
                else:
                    run = valid[item.get("run_id")]
                    run_state = run.get("source_state")
                    run_round = run.get("round")
                    if run_state == "round_end":
                        expected_inventory = {path: digest for (number, path), digest in inventory.get(model_id, {}).get("rounds", {}).items() if number == run_round}
                    elif run_state == "final":
                        expected_inventory = inventory.get(model_id, {}).get("final", {})
                    else:
                        expected_inventory = {}
                    expected_digest = hashlib.sha256("\n".join(f"{digest}  {path}" for path, digest in sorted(expected_inventory.items())).encode("utf-8")).hexdigest()
                    if not expected_inventory or run.get("source_inventory_digest") != expected_digest:
                        errors.append({"code": "RUN_SOURCE_UNBOUND", "message": f"Run {item.get('run_id')!r} is not bound to base inventory"})
                    rubric_round = rubric_rounds.get(rubric_id)
                    if item.get("direction") != "context" and ((run_state == "round_end" and run_round != rubric_round) or (run_state == "final" and rubric_round != manifest.get("task", {}).get("round_count"))):
                        errors.append({"code": "ROUND_LEAKAGE", "message": f"Run {item.get('run_id')!r} uses a later source state"})
            elif item.get("type") == "human_note":
                errors.append({"code": "REMOTE_HUMAN_NOTE_FORBIDDEN", "message": "Remote supplementation cannot impersonate a local human observation"})
            elif item.get("type") == "conversation":
                summary_path = base / model_map.get(model_id, {}).get("directory", "") / "conversation-summary.json"
                try:
                    summary = json.loads(summary_path.read_text(encoding="utf-8"))
                    event = next((row for row in summary.get("key_events", []) if row.get("msg_ref") == item.get("msg_ref")), None)
                except (OSError, json.JSONDecodeError, UnicodeDecodeError):
                    event = None
                if not event or any(item.get(key) != event.get(key) for key in ("flat_msg_index", "role", "excerpt", "event_type")):
                    errors.append({"code": "MESSAGE_CONTENT_MISMATCH", "message": f"Conversation evidence {evidence_id} is not bound to the base summary"})
                elif item.get("direction") != "context" and event.get("round") > rubric_rounds.get(rubric_id):
                    errors.append({"code": "ROUND_LEAKAGE", "message": f"Conversation evidence {evidence_id} uses a later round"})
            elif item.get("type") == "snapshot_diff":
                from_round, to_round, relative = item.get("from_round"), item.get("to_round"), item.get("path", "")
                if (not isinstance(from_round, int) or not isinstance(to_round, int) or not (1 <= from_round < to_round <= rubric_rounds.get(rubric_id, 0))
                        or inventory.get(model_id, {}).get("rounds", {}).get((from_round, relative)) != item.get("before_sha256")
                        or inventory.get(model_id, {}).get("rounds", {}).get((to_round, relative)) != item.get("after_sha256")):
                    errors.append({"code": "SNAPSHOT_DIFF_INVALID", "message": f"Snapshot diff {evidence_id} is not bound to ordered round inventories"})
            elif item.get("type") == "inventory_fact":
                if not item.get("metric") or "value" not in item or not item.get("basis") or not item.get("fact"):
                    errors.append({"code": "INVENTORY_FACT_INCOMPLETE", "message": f"Incomplete inventory fact {evidence_id}"})
                if item.get("direction") == "confirm_missing":
                    relative = str(item.get("path", ""))
                    round_number = rubric_rounds.get(rubric_id)
                    if item.get("metric") != "path_exists" or item.get("value") is not False or item.get("source_state") != "round_end" or item.get("round") != round_number or not safe_relative(relative) or (round_number, relative) in inventory.get(model_id, {}).get("rounds", {}):
                        errors.append({"code": "CONFIRM_MISSING_UNBACKED", "message": f"Unbacked missing-path evidence {evidence_id}"})
                elif item.get("direction") != "context":
                    errors.append({"code": "INVENTORY_FACT_NOT_SCORING", "message": f"Inventory fact {evidence_id} cannot support/refute a score unless it mechanically confirms a missing path"})
        proposed = response.get("proposed_update", {})
        if not isinstance(proposed, dict):
            errors.append({"code": "PROPOSED_UPDATE_INVALID", "message": f"{request_id} proposed_update must be an object"})
        elif "suggested_score" in proposed:
            score = proposed.get("suggested_score")
            if score not in (0, 1, None) or isinstance(score, bool):
                errors.append({"code": "PROPOSED_SCORE_INVALID", "message": f"{request_id} proposes an invalid score"})
            elif score == 1 and "support" not in directions:
                errors.append({"code": "PROPOSED_SCORE_DIRECTION", "message": f"{request_id} proposes 1 without support"})
            elif score == 0 and not ({"refute", "confirm_missing"} & directions):
                errors.append({"code": "PROPOSED_SCORE_DIRECTION", "message": f"{request_id} proposes 0 without refute/confirm_missing"})
        if isinstance(proposed, dict) and ({"human_check_needed", "adjudication_ids"} & set(proposed)):
            errors.append({"code": "REMOTE_GATE_UPDATE_FORBIDDEN", "message": f"{request_id} cannot clear or alter local human/adjudication gates"})
    blob_root = delta / "evidence" / "source-blobs"
    actual_delta_blobs = {
        item.relative_to(delta).as_posix()
        for item in blob_root.rglob("*")
        if item.is_file()
    } if blob_root.is_dir() else set()
    if actual_delta_blobs != referenced_delta_blobs:
        errors.append({
            "code": "SOURCE_BLOB_COVERAGE",
            "message": f"Delta source blobs must be exactly the referenced set; missing={sorted(referenced_delta_blobs-actual_delta_blobs)} extra={sorted(actual_delta_blobs-referenced_delta_blobs)}",
        })
    expected_request_ids = meta.get("request_ids")
    if not isinstance(expected_request_ids, list) or not expected_request_ids or len(expected_request_ids) != len(set(expected_request_ids)):
        errors.append({"code": "REQUEST_IDS_INVALID", "message": "DELTA.json needs a unique non-empty request_ids list"})
    elif set(expected_request_ids) != request_ids:
        errors.append({"code": "REQUEST_COVERAGE", "message": "responses.jsonl must contain exactly one response for every request_id"})
    if request_map and set(request_map) != set(expected_request_ids or []):
        errors.append({"code": "REQUEST_METADATA_MISMATCH", "message": "DELTA request_ids differ from request-copy.json"})
    if require_seal:
        ready_path = delta / "DELTA-READY.json"
        checks = delta / "integrity/files.sha256"
        if not ready_path.is_file() or not checks.is_file():
            errors.append({"code": "DELTA_SEAL_MISSING", "message": "Sealed delta needs DELTA-READY and checksums"})
        else:
            try:
                ready = json.loads(ready_path.read_text(encoding="utf-8"))
                if ready.get("schema_version") != "1.0.0" or ready.get("delta_id") != meta.get("delta_id") or ready.get("base_package_id") != manifest.get("package_id"):
                    errors.append({"code": "DELTA_READY_MISMATCH", "message": "DELTA-READY does not match DELTA/base metadata"})
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                errors.append({"code": "DELTA_READY_INVALID", "message": str(exc)})
            listed = {}
            for line in checks.read_text(encoding="utf-8").splitlines():
                match = re.fullmatch(r"([0-9a-f]{64})  (.+)", line)
                if not match:
                    errors.append({"code": "CHECKSUM_FORMAT", "message": line})
                    continue
                relative = match.group(2)
                if not safe_relative(relative):
                    errors.append({"code": "UNSAFE_PATH", "message": relative})
                    continue
                if relative in listed:
                    errors.append({"code": "CHECKSUM_DUPLICATE", "message": relative})
                    continue
                listed[relative] = match.group(1)
            expected = {p.relative_to(delta).as_posix() for p in delta.rglob("*") if p.is_file() and p.relative_to(delta).as_posix() not in EXCLUDES}
            if set(listed) != expected:
                errors.append({"code": "CHECKSUM_COVERAGE", "message": "Delta checksum coverage mismatch"})
            for relative, digest in listed.items():
                target = delta / PurePosixPath(relative)
                if target.is_file() and sha256(target) != digest:
                    errors.append({"code": "HASH_MISMATCH", "message": relative})
    return {"result": "pass" if not errors else "fail", "errors": errors, "counts": {"responses": len(responses), "new_evidence": len(new_ids)}}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("delta_dir", type=Path)
    parser.add_argument("base_dir", type=Path)
    parser.add_argument("requests", type=Path, help="Original evidence_requests.json retained by the local reviewer")
    parser.add_argument("--allow-unsealed", action="store_true")
    args = parser.parse_args(argv)
    report = validate_delta(args.delta_dir, args.base_dir, args.requests, not args.allow_unsealed)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["result"] == "pass" else 3


if __name__ == "__main__":
    sys.exit(main())
