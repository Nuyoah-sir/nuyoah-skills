#!/usr/bin/env python3
"""Generate the portable supporting outputs from validated decisions and slots.

Nothing here is hand-authored: the report, heatmap, and form input are rendered
from the validated presentation slots, the closed decisions, and the projected
scored rubrics, and every artifact is byte-reproducible from those inputs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any

from form_ready_context import load_form_ready_base
from presentation_workspace import DIMENSIONS, TASK_SLOTS, validate_presentation
from project_form_ready_scores import SCORED_DIR, verify_projected_scores
from validate_final_decisions import DECISION_JSON, validate_final_decisions
from form_ready_context import load_observation_registry

SCHEMA_VERSION = "2.0.0"
REPORT_INPUT = "presentation/report-input.json"
REPORT_MD = "presentation/\u53cd\u9988\u62a5\u544a.md"
HEATMAP = "presentation/rubrics\u6253\u5206\u70ed\u529b\u56fe.html"
FORM_INPUT = "presentation/form-input.json"


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def _digest_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _file_digest(path: Path) -> str:
    return _digest_bytes(path.read_bytes())


def _atomic_write(path: Path, text: str) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _identity(manifest: dict[str, Any], context) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "outer_package_id": manifest["outer_package_id"],
        "base_package_id": context.package_id,
        "base_zip_sha256": manifest["base"]["sha256"],
        "source_input_digest": context.source_input_digest,
        "task_id": context.task_id,
    }


def _slot_value(document: dict[str, Any], slot_id: str) -> Any:
    slot = document["slots"].get(slot_id) or {}
    return slot.get("value")


def _load_inputs(form_root: Path) -> tuple[dict[str, Any], Any, dict[str, Any], dict[tuple[str, str], dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    manifest, context, _ = load_form_ready_base(form_root)
    document = json.loads((form_root / "presentation/presentation-input.json").read_text(encoding="utf-8"))
    observations = load_observation_registry(form_root, manifest)
    outcome = validate_final_decisions(
        context, observations, observations, form_root / DECISION_JSON,
        audit_path=form_root / "decisions/adjudication-audit.json",
    )
    if outcome["result"] != "pass":
        raise ValueError("SUPPORTING_OUTPUTS_REQUIRE_CLOSED_DECISIONS: " + json.dumps(outcome["errors"], ensure_ascii=False))
    verify_projected_scores(context, outcome["registry"], form_root)
    scored: dict[str, list[dict[str, Any]]] = {}
    for model_id in sorted(context.models):
        scored[model_id] = json.loads((form_root / SCORED_DIR / f"rubrics-{model_id}.json").read_text(encoding="utf-8"))
    return manifest, context, document, outcome["registry"], scored


def _report_input(manifest, context, document, registry, scored) -> dict[str, Any]:
    models: dict[str, Any] = {}
    for model_id in sorted(context.models):
        rows = []
        for row in scored[model_id]:
            decision = registry.get((model_id, row.get("id"))) or {}
            rows.append({
                "rubric_id": row.get("id"),
                "round": row.get("round"),
                "criterion": row.get("criterion"),
                "score": row.get("score"),
                "reason": row.get("reason"),
                "decided_by": decision.get("decided_by"),
                "evidence_ids": decision.get("evidence_ids"),
            })
        models[model_id] = {
            "overall_impression": _slot_value(document, f"model.{model_id}.overall_impression"),
            "dimensions": {name: _slot_value(document, f"model.{model_id}.{name}") for name in DIMENSIONS},
            "pros": _slot_value(document, f"model.{model_id}.pros"),
            "cons": _slot_value(document, f"model.{model_id}.cons"),
            "style": _slot_value(document, f"model.{model_id}.style"),
            "labels": {
                "pros": _slot_value(document, f"model.{model_id}.labels.pros"),
                "cons": _slot_value(document, f"model.{model_id}.labels.cons"),
                "style": _slot_value(document, f"model.{model_id}.labels.style"),
            },
            "rubrics": rows,
        }
    return {
        **_identity(manifest, context),
        "task": {name: _slot_value(document, f"task.{name}") for name in TASK_SLOTS},
        "models": models,
    }


def _report_markdown(report: dict[str, Any]) -> str:
    lines = ["# \u53cd\u9988\u62a5\u544a", ""]
    lines.append("## \u9898\u76ee\u6d1e\u5bdf")
    lines.append("")
    for name in TASK_SLOTS:
        lines.append(f"- **{name}**: {report['task'][name]}")
    lines.append("")
    for model_id, model in report["models"].items():
        lines.append(f"## {model_id}")
        lines.append("")
        lines.append(f"- \u603b\u4f53\u5370\u8c61: {model['overall_impression']}")
        for name in DIMENSIONS:
            value = model["dimensions"][name] or {}
            if value.get("applicable") is False:
                lines.append(f"- {name}: N/A - {value.get('basis')}")
            else:
                lines.append(f"- {name}: {value.get('score')} - {value.get('basis')}")
        lines.append("")
        lines.append("Pros:")
        for item in model["pros"] or []:
            lines.append(f"- {item}")
        lines.append("")
        lines.append("Cons:")
        for item in model["cons"] or []:
            lines.append(f"- {item}")
        lines.append("")
        lines.append(f"Style: {model['style']}")
        lines.append("")
        lines.append(f"Labels: pros={model['labels']['pros']} cons={model['labels']['cons']} style={model['labels']['style']}")
        lines.append("")
        lines.append("| rubric | round | score | decided_by | evidence_ids | reason |")
        lines.append("| --- | --- | --- | --- | --- | --- |")
        for row in model["rubrics"]:
            evidence = ",".join(row.get("evidence_ids") or [])
            reason = str(row.get("reason", "")).replace("|", "/").replace("\n", " ")
            lines.append(f"| {row['rubric_id']} | {row['round']} | {row['score']} | {row['decided_by']} | {evidence} | {reason} |")
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def _heatmap_html(report: dict[str, Any]) -> str:
    model_ids = list(report["models"])
    rubric_ids: list[str] = []
    for model in report["models"].values():
        for row in model["rubrics"]:
            if row["rubric_id"] not in rubric_ids:
                rubric_ids.append(row["rubric_id"])
    head = "".join(f"<th>{model_id}</th>" for model_id in model_ids)
    rows = []
    for rubric_id in rubric_ids:
        cells = []
        for model_id in model_ids:
            row = next((item for item in report["models"][model_id]["rubrics"] if item["rubric_id"] == rubric_id), None)
            score = "" if row is None else row["score"]
            cells.append(f'<td class="score-{score}">{score}</td>')
        rows.append(f'<tr><th scope="row">{rubric_id}</th>{"".join(cells)}</tr>')
    return (
        "<!doctype html>\n<html lang=\"zh\">\n<head>\n<meta charset=\"utf-8\">\n"
        "<title>rubrics\u6253\u5206\u70ed\u529b\u56fe</title>\n<style>\n"
        "table{border-collapse:collapse}th,td{border:1px solid #ccc;padding:4px 8px;text-align:center}\n"
        ".score-0{background:#fdd}.score-1{background:#dfd}\n</style>\n</head>\n<body>\n"
        f"<table>\n<thead><tr><th>rubric</th>{head}</tr></thead>\n<tbody>\n" + "\n".join(rows) +
        "\n</tbody>\n</table>\n</body>\n</html>\n"
    )


def _form_input(form_root: Path, manifest, context, report: dict[str, Any]) -> dict[str, Any]:
    scored_digests = {
        model_id: _file_digest(form_root / SCORED_DIR / f"rubrics-{model_id}.json")
        for model_id in sorted(report["models"])
    }
    slots = json.loads((form_root / "presentation/presentation-input.json").read_text(encoding="utf-8"))["slots"]
    records: list[dict[str, Any]] = [{
        "model_id": None,
        "task": report["task"],
        "evidence_refs": {name: list((slots.get(f"task.{name}") or {}).get("evidence_ids") or []) for name in TASK_SLOTS},
    }]
    for model_id, model in report["models"].items():
        records.append({
            "model_id": model_id,
            "overall_impression": model["overall_impression"],
            "dimensions": model["dimensions"],
            "pros": model["pros"],
            "cons": model["cons"],
            "style": model["style"],
            "labels": model["labels"],
            "evidence_refs": _evidence_refs(form_root, model_id),
        })
    return {
        **_identity(manifest, context),
        "presentation_input_sha256": _file_digest(form_root / "presentation/presentation-input.json"),
        "final_decisions_sha256": _file_digest(form_root / DECISION_JSON),
        "scored_sha256": scored_digests,
        "records": records,
    }


def _evidence_refs(form_root: Path, model_id: str) -> dict[str, Any]:
    document = json.loads((form_root / "presentation/presentation-input.json").read_text(encoding="utf-8"))
    prefix = f"model.{model_id}."

    def refs(name: str) -> list[str]:
        return list((document["slots"].get(prefix + name) or {}).get("evidence_ids") or [])

    return {
        "overall": refs("overall_impression"),
        "dimensions": {name: refs(name) for name in DIMENSIONS},
        "pros": refs("pros"),
        "cons": refs("cons"),
        "style": refs("style"),
    }


def render_supporting_outputs(form_root: str | Path, shared_root: str | Path | None = None) -> dict[str, Any]:
    """Write the four supporting artifacts, refusing to overwrite anything."""

    root = Path(form_root)
    presentation = validate_presentation(root, shared_root)
    if not presentation["presentation_complete"]:
        raise ValueError("SUPPORTING_OUTPUTS_REQUIRE_COMPLETE_PRESENTATION: " + json.dumps(presentation["errors"], ensure_ascii=False))
    manifest, context, document, registry, scored = _load_inputs(root)
    targets = [root / REPORT_INPUT, root / REPORT_MD, root / HEATMAP, root / FORM_INPUT]
    for target in targets:
        if target.exists() or target.is_symlink():
            raise FileExistsError(f"Supporting output already exists: {target}")
    report = _report_input(manifest, context, document, registry, scored)
    form_input = _form_input(root, manifest, context, report)
    (root / "presentation").mkdir(parents=True, exist_ok=True)
    _atomic_write(root / REPORT_INPUT, _dump(report))
    _atomic_write(root / REPORT_MD, _report_markdown(report))
    _atomic_write(root / HEATMAP, _heatmap_html(report))
    _atomic_write(root / FORM_INPUT, _dump(form_input))
    return {"report_input": str(root / REPORT_INPUT), "report": str(root / REPORT_MD),
            "heatmap": str(root / HEATMAP), "form_input": str(root / FORM_INPUT)}


def verify_supporting_outputs(form_root: str | Path, shared_root: str | Path | None = None) -> dict[str, Any]:
    """Replay the supporting outputs and byte-compare them with the package."""

    root = Path(form_root)
    manifest, context, document, registry, scored = _load_inputs(root)
    report = _report_input(manifest, context, document, registry, scored)
    expected = {
        REPORT_INPUT: _dump(report),
        REPORT_MD: _report_markdown(report),
        HEATMAP: _heatmap_html(report),
        FORM_INPUT: _dump(_form_input(root, manifest, context, report)),
    }
    for relative, text in expected.items():
        target = root.joinpath(*relative.split("/"))
        if not target.is_file():
            raise FileNotFoundError(f"Supporting output is missing: {relative}")
        if target.read_text(encoding="utf-8") != text:
            raise ValueError(f"Supporting output is not byte-reproducible: {relative}")
    return {"verified": sorted(expected)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("render", "verify"))
    parser.add_argument("--form-root", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.mode == "render":
            print(json.dumps(render_supporting_outputs(args.form_root), ensure_ascii=False))
        else:
            print(json.dumps(verify_supporting_outputs(args.form_root), ensure_ascii=False))
    except (OSError, UnicodeError, ValueError, KeyError, json.JSONDecodeError, FileExistsError) as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "FORM_INPUT", "HEATMAP", "REPORT_INPUT", "REPORT_MD", "render_supporting_outputs",
    "verify_supporting_outputs",
]
