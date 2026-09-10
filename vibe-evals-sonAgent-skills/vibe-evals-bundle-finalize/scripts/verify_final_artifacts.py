#!/usr/bin/env python3
"""Deterministically verify scored rubric artifacts against a sealed evidence bundle."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path, PurePosixPath

from validate_bundle import validate_bundle
from finalize_scores import finalize_scores


def _json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _add(errors: list[dict], code: str, message: str, **context) -> None:
    item = {"code": code, "message": message}
    if context:
        item["context"] = context
    errors.append(item)


def _inside(path: Path, directory: Path) -> bool:
    try:
        path.resolve().relative_to(directory.resolve())
        return True
    except ValueError:
        return False


def verify_final_artifacts(
    bundle_dir: str | Path,
    decisions_path: str | Path,
    output_dir: str | Path,
) -> dict:
    """Cross-check all final score files, decisions, audit entries, and totals.

    The function never edits its inputs. Semantic mismatches are collected in the
    returned report instead of stopping at the first problem.
    """

    bundle = Path(bundle_dir).resolve()
    decisions_file = Path(decisions_path).resolve()
    output = Path(output_dir).resolve()
    errors: list[dict] = []
    totals: dict[str, dict[str, int]] = {}

    bundle_report = validate_bundle(bundle, require_seal=True)
    if bundle_report.get("result") != "pass":
        _add(errors, "BUNDLE_INVALID", "Evidence bundle is not a valid sealed bundle.", bundle_errors=bundle_report.get("errors", []))
        return {"result": "fail", "errors": errors, "totals": totals}

    try:
        manifest = _json(bundle / "MANIFEST.json")
        decisions = _json(decisions_file)
    except (OSError, json.JSONDecodeError) as exc:
        _add(errors, "INPUT_INVALID", f"Cannot read manifest or human decisions: {exc}")
        return {"result": "fail", "errors": errors, "totals": totals}

    package_id = manifest.get("package_id")
    if decisions.get("schema_version") != "1.0.0" or decisions.get("base_package_id") != package_id:
        _add(errors, "DECISION_IDENTITY_MISMATCH", "Decision schema_version or base_package_id does not match the bundle.")

    try:
        with tempfile.TemporaryDirectory() as tmp:
            expected_root = Path(tmp) / "expected"
            finalize_scores(bundle, decisions_file, expected_root)
            for name in [*(f"rubrics-{item.get('model_id')}.json" for item in manifest.get("models", [])), "decision-audit.json", "local-human-evidence.jsonl"]:
                expected_path = expected_root / name
                actual_path = output / name
                if expected_path.exists() != actual_path.exists() or (expected_path.exists() and expected_path.read_bytes() != actual_path.read_bytes()):
                    _add(errors, "FINALIZE_REPLAY_MISMATCH", f"{name} differs from a fresh finalize_scores replay.")
    except (ValueError, FileExistsError, OSError, json.JSONDecodeError) as exc:
        _add(errors, "DECISION_POLICY_INVALID", f"Human decisions fail finalize policy validation: {exc}")

    baseline: list[dict] = []
    for entry in sorted(manifest.get("inputs", {}).get("rubrics", []), key=lambda item: item.get("round", 0)):
        baseline.extend(_json(bundle / PurePosixPath(entry["path"])))
    baseline_by_id = {item.get("id"): item for item in baseline}
    rubric_ids = set(baseline_by_id)

    models = manifest.get("models", [])
    model_ids = {item.get("model_id") for item in models}
    decisions_by_model = decisions.get("rubric_scores", {})
    if not isinstance(decisions_by_model, dict) or set(decisions_by_model) != model_ids:
        _add(errors, "DECISION_MODEL_COVERAGE", "Human decisions must contain exactly every manifest model.")

    local_by_pair: dict[tuple[str, str], set[str]] = {}
    local_evidence = decisions.get("local_evidence", [])
    if not isinstance(local_evidence, list):
        _add(errors, "LOCAL_EVIDENCE_INVALID", "local_evidence must be a list when present.")
        local_evidence = []
    for item in local_evidence:
        if not isinstance(item, dict):
            _add(errors, "LOCAL_EVIDENCE_INVALID", "Each local_evidence item must be an object.")
            continue
        pair = (item.get("model_id"), item.get("rubric_id"))
        evidence_id = item.get("evidence_id")
        if pair[0] not in model_ids or pair[1] not in rubric_ids or not evidence_id:
            _add(errors, "LOCAL_EVIDENCE_INVALID", "Local evidence has an unknown model/rubric pair or no evidence_id.", evidence_id=evidence_id)
            continue
        local_by_pair.setdefault(pair, set()).add(evidence_id)

    summary_path = output / "scoring-summary.json"
    summary = None
    if not summary_path.is_file():
        _add(errors, "SUMMARY_MISSING", "scoring-summary.json is missing.")
    else:
        try:
            summary = _json(summary_path)
        except (OSError, json.JSONDecodeError) as exc:
            _add(errors, "SUMMARY_INVALID", f"Cannot parse scoring-summary.json: {exc}")

    audit_path = output / "decision-audit.json"
    audit = None
    if not audit_path.is_file():
        _add(errors, "AUDIT_MISSING", "decision-audit.json is missing.")
    else:
        try:
            audit = _json(audit_path)
        except (OSError, json.JSONDecodeError) as exc:
            _add(errors, "AUDIT_INVALID", f"Cannot parse decision-audit.json: {exc}")

    if summary is not None:
        if summary.get("base_package_id") != package_id:
            _add(errors, "SUMMARY_IDENTITY_MISMATCH", "Summary base_package_id does not match the bundle.")
        if summary.get("model_count") != len(models) or summary.get("rubric_count") != len(baseline):
            _add(errors, "SUMMARY_COUNT_MISMATCH", "Summary model_count or rubric_count is incorrect.")
        summary_files = summary.get("files", {})
        if not isinstance(summary_files, dict) or set(summary_files) != model_ids:
            _add(errors, "SUMMARY_MODEL_COVERAGE", "Summary files must contain exactly every manifest model.")
            summary_files = {}
        for model_id, raw_path in summary_files.items():
            try:
                declared = Path(raw_path).resolve()
            except TypeError:
                _add(errors, "UNSAFE_SUMMARY_PATH", "Summary output path is not a string.", model_id=model_id)
                continue
            expected = (output / f"rubrics-{model_id}.json").resolve()
            if not _inside(declared, output) or declared != expected:
                _add(errors, "UNSAFE_SUMMARY_PATH", "Summary score path must name the canonical file inside output_dir.", model_id=model_id, path=str(raw_path))
        raw_audit = summary.get("audit")
        if not isinstance(raw_audit, str) or not _inside(Path(raw_audit), output) or Path(raw_audit).resolve() != audit_path:
            _add(errors, "UNSAFE_SUMMARY_PATH", "Summary audit path must name decision-audit.json inside output_dir.")

    audit_models: dict = {}
    if audit is not None:
        if audit.get("schema_version") != "1.0.0" or audit.get("base_package_id") != package_id:
            _add(errors, "AUDIT_IDENTITY_MISMATCH", "Audit schema_version or base_package_id does not match the bundle.")
        audit_models = audit.get("models", {})
        if not isinstance(audit_models, dict) or set(audit_models) != model_ids:
            _add(errors, "AUDIT_MODEL_COVERAGE", "Audit must contain exactly every manifest model.")
            audit_models = {}
        if audit.get("adjudication_resolutions", []) != decisions.get("adjudication_resolutions", []):
            _add(errors, "AUDIT_ADJUDICATION_MISMATCH", "Audit adjudication resolutions differ from human decisions.")
        if audit.get("material_gap_resolutions", []) != decisions.get("material_gap_resolutions", []):
            _add(errors, "AUDIT_MATERIAL_GAP_MISMATCH", "Audit material-gap resolutions differ from human decisions.")

    for model in models:
        model_id = model.get("model_id")
        pair_evidence: dict[str, set[str]] = {}
        evidence_path = bundle / PurePosixPath(model["directory"]) / "rubric-evidence.jsonl"
        for row in _jsonl(evidence_path):
            pair_evidence[row["rubric_id"]] = {
                item.get("evidence_id")
                for item in row.get("evidence", [])
                if isinstance(item, dict) and item.get("evidence_id")
            }

        model_decisions = decisions_by_model.get(model_id, {}) if isinstance(decisions_by_model, dict) else {}
        if not isinstance(model_decisions, dict) or set(model_decisions) != rubric_ids:
            _add(errors, "DECISION_RUBRIC_COVERAGE", "Model decisions must contain exactly every baseline rubric.", model_id=model_id)
            model_decisions = model_decisions if isinstance(model_decisions, dict) else {}
        model_audit = audit_models.get(model_id, {}) if isinstance(audit_models, dict) else {}
        if not isinstance(model_audit, dict) or set(model_audit) != rubric_ids:
            _add(errors, "AUDIT_RUBRIC_COVERAGE", "Model audit must contain exactly every baseline rubric.", model_id=model_id)
            model_audit = model_audit if isinstance(model_audit, dict) else {}

        scored_path = output / f"rubrics-{model_id}.json"
        try:
            scored = _json(scored_path)
        except FileNotFoundError:
            _add(errors, "SCORED_FILE_MISSING", "Canonical scored rubric file is missing.", model_id=model_id)
            continue
        except (OSError, json.JSONDecodeError) as exc:
            _add(errors, "SCORED_FILE_INVALID", f"Cannot parse scored rubric file: {exc}", model_id=model_id)
            continue
        if not isinstance(scored, list) or len(scored) != len(baseline):
            _add(errors, "SCORED_RUBRIC_COVERAGE", "Scored rubric list length differs from the baseline.", model_id=model_id)
            scored = scored if isinstance(scored, list) else []

        scored_by_id = {item.get("id"): item for item in scored if isinstance(item, dict)}
        if set(scored_by_id) != rubric_ids:
            _add(errors, "SCORED_RUBRIC_COVERAGE", "Scored rubric IDs differ from the baseline.", model_id=model_id)

        scored_total = 0
        decision_total = 0
        audit_total = 0
        for rubric_id, original in baseline_by_id.items():
            decision = model_decisions.get(rubric_id)
            final_item = scored_by_id.get(rubric_id)
            audit_item = model_audit.get(rubric_id)
            if not isinstance(decision, dict):
                continue

            score = decision.get("score")
            reason = decision.get("reason")
            refs = decision.get("evidence_ids")
            decided_by = decision.get("decided_by")
            if score not in (0, 1) or isinstance(score, bool) or not isinstance(reason, str) or not reason.strip() or not isinstance(refs, list) or not refs:
                _add(errors, "DECISION_INVALID", "Final decision needs score 0/1, non-empty reason, and evidence_ids.", model_id=model_id, rubric_id=rubric_id)
                continue
            decision_total += score
            allowed = pair_evidence.get(rubric_id, set()) | local_by_pair.get((model_id, rubric_id), set())
            unknown = sorted({item for item in refs if isinstance(item, str)} - allowed)
            if len(refs) != len([item for item in refs if isinstance(item, str)]) or unknown:
                _add(errors, "UNKNOWN_EVIDENCE_REFERENCE", "Decision references evidence outside its model/rubric pair.", model_id=model_id, rubric_id=rubric_id, evidence_ids=unknown)

            expected_audit = {
                "score": score,
                "reason": reason,
                "evidence_ids": refs,
                "decided_by": decided_by,
            }
            if audit_item != expected_audit:
                _add(errors, "AUDIT_DECISION_MISMATCH", "Audit entry differs from the human decision.", model_id=model_id, rubric_id=rubric_id)
            if isinstance(audit_item, dict):
                audit_score = audit_item.get("score")
                if audit_score in (0, 1) and not isinstance(audit_score, bool):
                    audit_total += audit_score
                audit_refs = audit_item.get("evidence_ids", [])
                if isinstance(audit_refs, list):
                    unknown_audit = sorted({item for item in audit_refs if isinstance(item, str)} - allowed)
                    if unknown_audit:
                        _add(errors, "UNKNOWN_EVIDENCE_REFERENCE", "Audit references evidence outside its model/rubric pair.", model_id=model_id, rubric_id=rubric_id, evidence_ids=unknown_audit)

            if not isinstance(final_item, dict):
                continue
            expected_keys = set(original) | {"score", "reason"}
            baseline_changed = any(final_item.get(key) != value for key, value in original.items())
            if set(final_item) != expected_keys or baseline_changed:
                _add(errors, "SCORED_BASELINE_MUTATION", "Scored rubric must preserve all baseline fields and add only score/reason.", model_id=model_id, rubric_id=rubric_id)
            if final_item.get("score") != score or final_item.get("reason") != reason:
                _add(errors, "SCORED_DECISION_MISMATCH", "Scored rubric score/reason differs from the human decision.", model_id=model_id, rubric_id=rubric_id)
            final_score = final_item.get("score")
            if final_score in (0, 1) and not isinstance(final_score, bool):
                scored_total += final_score

        totals[model_id] = {"score": decision_total, "maximum": len(baseline)}
        if scored_total != decision_total or (audit is not None and audit_total != decision_total):
            _add(errors, "TOTAL_MISMATCH", "Scored-file, decision, and audit totals do not agree.", model_id=model_id, scored=scored_total, decisions=decision_total, audit=audit_total)

    expected_score_files = {f"rubrics-{model_id}.json" for model_id in model_ids}
    actual_score_files = {item.name for item in output.glob("rubrics-*.json") if item.is_file()}
    if actual_score_files != expected_score_files:
        _add(errors, "EXTRA_OR_MISSING_SCORE_FILE", "Canonical score-file set differs from manifest models.", expected=sorted(expected_score_files), actual=sorted(actual_score_files))

    local_path = output / "local-human-evidence.jsonl"
    if local_evidence:
        if not local_path.is_file():
            _add(errors, "LOCAL_EVIDENCE_AUDIT_MISSING", "local-human-evidence.jsonl is required when decisions contain local evidence.")
        else:
            try:
                if _jsonl(local_path) != local_evidence:
                    _add(errors, "LOCAL_EVIDENCE_AUDIT_MISMATCH", "Persisted local evidence differs from human decisions.")
            except (OSError, json.JSONDecodeError) as exc:
                _add(errors, "LOCAL_EVIDENCE_AUDIT_MISMATCH", f"Cannot parse local-human-evidence.jsonl: {exc}")

    return {
        "result": "pass" if not errors else "fail",
        "base_package_id": package_id,
        "model_count": len(models),
        "rubric_count": len(baseline),
        "totals": totals,
        "errors": errors,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle_dir", type=Path)
    parser.add_argument("human_decisions", type=Path)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args(argv)
    try:
        report = verify_final_artifacts(args.bundle_dir, args.human_decisions, args.output_dir)
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        print(json.dumps({"result": "fail", "errors": [{"code": "UNEXPECTED_INPUT", "message": str(exc)}]}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 3
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["result"] == "pass" else 3


if __name__ == "__main__":
    sys.exit(main())
