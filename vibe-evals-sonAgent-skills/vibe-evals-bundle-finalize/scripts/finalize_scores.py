#!/usr/bin/env python3
"""Create per-model scored rubric JSONs from a verified bundle and explicit decisions."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path, PurePosixPath

from validate_bundle import validate_bundle


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def finalize_scores(bundle_dir: str | Path, decisions_path: str | Path, output_dir: str | Path) -> dict:
    bundle = Path(bundle_dir).resolve()
    output = Path(output_dir).resolve()
    report = validate_bundle(bundle)
    if report["result"] != "pass" or report["derived_status"] == "incomplete":
        raise ValueError("Evidence bundle is incomplete: " + json.dumps(report["errors"], ensure_ascii=False))
    manifest = json.loads((bundle / "MANIFEST.json").read_text(encoding="utf-8"))
    decisions = json.loads(Path(decisions_path).read_text(encoding="utf-8"))
    if decisions.get("schema_version") != "1.0.0" or decisions.get("base_package_id") != manifest.get("package_id"):
        raise ValueError("Decision schema or base_package_id mismatch")

    expected_gaps = set(report.get("material_gaps", []))
    gap_rows = decisions.get("material_gap_resolutions", [])
    if not isinstance(gap_rows, list):
        raise ValueError("material_gap_resolutions must be a list")
    gap_ids = [item.get("gap_id") for item in gap_rows if isinstance(item, dict)]
    if len(gap_ids) != len(set(gap_ids)) or set(gap_ids) != expected_gaps:
        raise ValueError(f"Material gap resolutions must cover exactly {sorted(expected_gaps)}")
    for item in gap_rows:
        required = ("gap_id", "decision", "reason", "decider", "decided_at")
        if item.get("decided_by") != "human" or item.get("decision") != "proceed_with_limitation" or any(not item.get(key) for key in required):
            raise ValueError(f"Invalid material gap resolution {item.get('gap_id')!r}")

    pending = json.loads((bundle / "review" / "pending-adjudications.json").read_text(encoding="utf-8")).get("items", [])
    pending_by_id = {item.get("adjudication_id"): item for item in pending if item.get("status") == "pending"}
    resolution_rows = decisions.get("adjudication_resolutions", [])
    if not isinstance(resolution_rows, list):
        raise ValueError("adjudication_resolutions must be a list")
    resolution_ids = [item.get("adjudication_id") for item in resolution_rows if isinstance(item, dict)]
    if len(resolution_ids) != len(set(resolution_ids)) or set(resolution_ids) != set(pending_by_id):
        raise ValueError("Adjudication resolutions must cover pending adjudications exactly once")
    resolutions = {item.get("adjudication_id"): item for item in resolution_rows}
    missing_resolutions = [item.get("adjudication_id") for item in pending if item.get("status") == "pending" and item.get("adjudication_id") not in resolutions]
    if missing_resolutions:
        raise ValueError(f"Pending adjudications lack human resolutions: {missing_resolutions}")
    for item_id in pending_by_id:
        resolution = resolutions[item_id]
        if (resolution.get("decided_by") != "human" or not resolution.get("final_policy") or not resolution.get("per_model_final_scores")
                or not resolution.get("decider") or not resolution.get("decided_at") or not resolution.get("decision_source")):
            raise ValueError(f"Adjudication resolution {item_id} lacks human decision details")

    baseline: list[dict] = []
    for entry in sorted(manifest["inputs"]["rubrics"], key=lambda item: item["round"]):
        baseline.extend(json.loads((bundle / PurePosixPath(entry["path"])).read_text(encoding="utf-8")))
    rubric_ids = [item["id"] for item in baseline]
    model_id_set = {item["model_id"] for item in manifest["models"]}
    local_evidence = decisions.get("local_evidence", [])
    local_ids: set[str] = set()
    local_by_pair: dict[tuple[str, str], set[str]] = {}
    for item in local_evidence:
        evidence_id = item.get("evidence_id")
        pair = (item.get("model_id"), item.get("rubric_id"))
        required = ("observer", "observed_at", "steps", "expected_result", "observed_result", "observed_version")
        if (not isinstance(evidence_id, str) or not evidence_id.startswith("LOCAL-") or evidence_id in local_ids or pair[0] not in model_id_set or pair[1] not in rubric_ids
                or item.get("type") != "human_note" or not isinstance(item.get("steps"), list)
                or not item.get("steps") or any(not item.get(key) for key in required)):
            raise ValueError(f"Invalid local human evidence {evidence_id!r}")
        local_ids.add(evidence_id)
        local_by_pair.setdefault(pair, set()).add(evidence_id)
    result_files: dict[str, str] = {}
    prepared_files: dict[str, list[dict]] = {}
    audit: dict[str, dict] = {}

    for model in manifest["models"]:
        model_id = model["model_id"]
        rows = _jsonl(bundle / PurePosixPath(model["directory"]) / "rubric-evidence.jsonl")
        by_rubric = {row["rubric_id"]: row for row in rows}
        evidence_by_rubric = {row["rubric_id"]: {item["evidence_id"] for item in row.get("evidence", []) if item.get("evidence_id")} for row in rows}
        model_decisions = decisions.get("rubric_scores", {}).get(model_id, {})
        if set(model_decisions) != set(rubric_ids):
            missing = sorted(set(rubric_ids) - set(model_decisions))
            extra = sorted(set(model_decisions) - set(rubric_ids))
            raise ValueError(f"Decision coverage mismatch for {model_id}: missing={missing}, extra={extra}")
        scored: list[dict] = []
        model_audit: dict[str, dict] = {}
        for original in baseline:
            rubric_id = original["id"]
            decision = model_decisions[rubric_id]
            score = decision.get("score")
            reason = str(decision.get("reason", "")).strip()
            refs = decision.get("evidence_ids", [])
            decided_by = decision.get("decided_by")
            if score not in (0, 1) or isinstance(score, bool):
                raise ValueError(f"Final score must be 0 or 1 for {model_id}/{rubric_id}")
            if not reason or not refs:
                raise ValueError(f"Final score needs reason and evidence_ids for {model_id}/{rubric_id}")
            allowed_refs = evidence_by_rubric.get(rubric_id, set()) | local_by_pair.get((model_id, rubric_id), set())
            unknown_refs = sorted(set(refs) - allowed_refs)
            if unknown_refs:
                raise ValueError(f"Unknown evidence references for {model_id}/{rubric_id}: {unknown_refs}")
            if decided_by not in {"human", "accepted_machine"}:
                raise ValueError(f"decided_by must be human or accepted_machine for {model_id}/{rubric_id}")
            evidence_row = by_rubric[rubric_id]
            if evidence_row.get("human_check_needed") and not (set(refs) & local_by_pair.get((model_id, rubric_id), set())):
                raise ValueError(f"Human-check rubric needs LOCAL evidence for {model_id}/{rubric_id}")
            if evidence_row.get("adjudication_ids") and decided_by != "human":
                raise ValueError(f"Adjudicated rubric must be decided_by=human for {model_id}/{rubric_id}")
            for adjudication_id in evidence_row.get("adjudication_ids", []):
                resolution = resolutions.get(adjudication_id, {})
                resolved_score = resolution.get("per_model_final_scores", {}).get(model_id, {}).get(rubric_id)
                if resolved_score != score:
                    raise ValueError(f"Adjudication {adjudication_id} score disagrees with {model_id}/{rubric_id}")
            if decided_by == "accepted_machine" and (evidence_row.get("suggested_score") != score or evidence_row.get("human_check_needed") or evidence_row.get("adjudication_ids")):
                raise ValueError(f"accepted_machine is not allowed for unresolved or differing evidence at {model_id}/{rubric_id}")
            scored.append({**original, "score": score, "reason": reason})
            model_audit[rubric_id] = {"score": score, "reason": reason, "evidence_ids": refs, "decided_by": decided_by}
        prepared_files[model_id] = scored
        audit[model_id] = model_audit

    output.mkdir(parents=True, exist_ok=False)
    for model_id, scored in prepared_files.items():
        target = output / f"rubrics-{model_id}.json"
        target.write_text(json.dumps(scored, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        result_files[model_id] = str(target)
    audit_path = output / "decision-audit.json"
    audit_path.write_text(json.dumps({"schema_version": "1.0.0", "base_package_id": manifest["package_id"], "models": audit, "adjudication_resolutions": decisions.get("adjudication_resolutions", []), "material_gap_resolutions": gap_rows}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if local_evidence:
        (output / "local-human-evidence.jsonl").write_text("".join(json.dumps(item, ensure_ascii=False) + "\n" for item in local_evidence), encoding="utf-8")
    summary = {"base_package_id": manifest["package_id"], "files": result_files, "audit": str(audit_path), "model_count": len(result_files), "rubric_count": len(rubric_ids)}
    (output / "scoring-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle_dir", type=Path)
    parser.add_argument("human_decisions", type=Path)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args(argv)
    try:
        result = finalize_scores(args.bundle_dir, args.human_decisions, args.output_dir)
    except (ValueError, FileExistsError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 3
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
