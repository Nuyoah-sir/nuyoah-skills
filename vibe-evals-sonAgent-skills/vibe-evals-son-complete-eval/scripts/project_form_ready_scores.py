#!/usr/bin/env python3
"""Project closed decisions into portable scored rubrics inside the package.

The package owns its own ``scored`` directory. Callers pass only the package root,
so scored output can never be redirected outside the bundle, and every path that
reaches a portable artifact is package-relative and POSIX-style.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

from form_ready_context import BaseContext

SCORED_DIR = "scored"
SUMMARY_NAME = "scoring-summary.json"
AUDIT_NAME = "decision-audit.json"


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def _scored_payload(base_ctx: BaseContext, decision_registry: dict[tuple[str, str], dict[str, Any]]) -> tuple[dict[str, list[dict[str, Any]]], list[str]]:
    problems: list[str] = []
    payload: dict[str, list[dict[str, Any]]] = {}
    if not base_ctx.baseline_rubrics:
        problems.append("SCORED_BASELINE_MISSING")
    for model_id in sorted(base_ctx.models):
        rows: list[dict[str, Any]] = []
        for baseline in base_ctx.baseline_rubrics:
            rubric_id = baseline.get("id")
            decision = decision_registry.get((model_id, rubric_id))
            if not isinstance(decision, dict) or decision.get("score") not in (0, 1) or isinstance(decision.get("score"), bool):
                problems.append(f"SCORED_DECISION_MISSING: {model_id}/{rubric_id}")
                continue
            if not isinstance(decision.get("reason"), str) or not decision["reason"].strip():
                problems.append(f"SCORED_REASON_MISSING: {model_id}/{rubric_id}")
                continue
            rows.append({**baseline, "score": decision["score"], "reason": decision["reason"]})
        payload[model_id] = rows
    return payload, problems


def _audit_payload(base_ctx: BaseContext, decision_registry: dict[tuple[str, str], dict[str, Any]]) -> dict[str, Any]:
    models: dict[str, dict[str, Any]] = {}
    for (model_id, rubric_id), decision in sorted(decision_registry.items()):
        models.setdefault(model_id, {})[rubric_id] = {
            "score": decision.get("score"),
            "reason": decision.get("reason"),
            "evidence_ids": decision.get("evidence_ids"),
            "decided_by": decision.get("decided_by"),
            "decider": decision.get("decider"),
            "decided_at": decision.get("decided_at"),
        }
    return {
        "schema_version": "2.0.0",
        "base_package_id": base_ctx.package_id,
        "source_input_digest": base_ctx.source_input_digest,
        "models": models,
    }


def _summary_payload(base_ctx: BaseContext, payload: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    return {
        "schema_version": "2.0.0",
        "base_package_id": base_ctx.package_id,
        "source_input_digest": base_ctx.source_input_digest,
        "files": {model_id: f"{SCORED_DIR}/rubrics-{model_id}.json" for model_id in sorted(payload)},
        "audit": f"{SCORED_DIR}/{AUDIT_NAME}",
        "model_count": len(payload),
        "rubric_count": len(base_ctx.baseline_rubrics),
    }


def _write_scored(target: Path, base_ctx: BaseContext, decision_registry: dict[tuple[str, str], dict[str, Any]]) -> dict[str, Any]:
    payload, problems = _scored_payload(base_ctx, decision_registry)
    if problems:
        raise ValueError("Scored projection is incomplete: " + "; ".join(sorted(problems)))
    target.mkdir(parents=True, exist_ok=False)
    for model_id, rows in payload.items():
        (target / f"rubrics-{model_id}.json").write_text(_dump(rows), encoding="utf-8")
    (target / AUDIT_NAME).write_text(_dump(_audit_payload(base_ctx, decision_registry)), encoding="utf-8")
    summary = _summary_payload(base_ctx, payload)
    (target / SUMMARY_NAME).write_text(_dump(summary), encoding="utf-8")
    return summary


def project_scores(base_ctx: BaseContext, decision_registry: dict[tuple[str, str], dict[str, Any]], form_root: str | Path) -> dict[str, Any]:
    """Write the canonical ``scored`` directory inside the package."""

    target = Path(form_root) / SCORED_DIR
    if target.exists() or target.is_symlink():
        raise FileExistsError(f"Scored output already exists: {target}")
    return _write_scored(target, base_ctx, decision_registry)


def verify_projected_scores(base_ctx: BaseContext, decision_registry: dict[tuple[str, str], dict[str, Any]], form_root: str | Path) -> dict[str, Any]:
    """Replay the projection into a scratch directory and byte-compare every file."""

    target = Path(form_root) / SCORED_DIR
    if not target.is_dir():
        raise FileNotFoundError(f"Scored output is missing: {target}")
    with tempfile.TemporaryDirectory(prefix="vibe-scored-replay-") as temporary:
        replay = Path(temporary) / SCORED_DIR
        summary = _write_scored(replay, base_ctx, decision_registry)
        replay_files = sorted(path.name for path in replay.iterdir() if path.is_file())
        actual_files = sorted(path.name for path in target.iterdir() if path.is_file())
        if replay_files != actual_files:
            raise ValueError(f"Scored file set is not reproducible: {actual_files} != {replay_files}")
        for name in replay_files:
            if (replay / name).read_bytes() != (target / name).read_bytes():
                raise ValueError(f"Scored artifact is not byte-reproducible: {name}")
    for model_id, relative in summary["files"].items():
        if relative.startswith("/") or ":" in relative or "\\" in relative:
            raise ValueError(f"Scored path must be package-relative POSIX: {relative}")
        if not (Path(form_root) / relative).is_file():
            raise ValueError(f"Scored path does not resolve inside the package: {relative}")
    if summary["audit"] != f"{SCORED_DIR}/{AUDIT_NAME}" or not (Path(form_root) / summary["audit"]).is_file():
        raise ValueError("Scored audit path is not the canonical in-package path")
    return summary


def read_scored_summary(form_root: str | Path) -> dict[str, Any]:
    return json.loads((Path(form_root) / SCORED_DIR / SUMMARY_NAME).read_text(encoding="utf-8"))


def discard_scored(form_root: str | Path) -> None:
    """Remove a partially written scored directory. Only the canonical path is touched."""

    target = Path(form_root) / SCORED_DIR
    if target.is_dir() and not target.is_symlink():
        shutil.rmtree(target)


__all__ = [
    "AUDIT_NAME", "SCORED_DIR", "SUMMARY_NAME", "discard_scored", "project_scores",
    "read_scored_summary", "verify_projected_scores",
]
