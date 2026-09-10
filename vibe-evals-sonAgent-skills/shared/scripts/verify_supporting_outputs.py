#!/usr/bin/env python3
"""Verify feedback-report totals and heatmap cells against sealed scored rubrics."""

from __future__ import annotations

import argparse
import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

from validate_bundle import validate_bundle
from verify_final_artifacts import verify_final_artifacts


class HeatmapParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.current_rubric = None
        self.rows: dict[str, list[tuple[str | None, str | None]]] = {}
        self.orphan_cells = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag.lower() == "tr":
            self.current_rubric = values.get("data-rubric-id")
            if self.current_rubric is not None:
                self.rows.setdefault(self.current_rubric, [])
        elif tag.lower() == "td" and ("data-model-id" in values or "data-score" in values):
            cell = (values.get("data-model-id"), values.get("data-score"))
            if self.current_rubric is None:
                self.orphan_cells.append(cell)
            else:
                self.rows.setdefault(self.current_rubric, []).append(cell)

    def handle_endtag(self, tag):
        if tag.lower() == "tr":
            self.current_rubric = None


def verify_supporting_outputs(bundle_dir, decisions_path, scored_dir, report_path, heatmap_path) -> dict:
    bundle = Path(bundle_dir).resolve()
    scored_root = Path(scored_dir).resolve()
    errors = []
    bundle_report = validate_bundle(bundle, require_seal=True)
    if bundle_report["result"] != "pass":
        return {"result": "fail", "errors": [{"code": "BUNDLE_INVALID", "message": "Bundle failed sealed validation"}]}
    final_report = verify_final_artifacts(bundle, decisions_path, scored_root)
    if final_report["result"] != "pass":
        return {"result": "fail", "errors": [{"code": "FINAL_ARTIFACTS_INVALID", "message": "Scored artifacts are not bound to valid decisions", "details": final_report["errors"]}]}
    manifest = json.loads((bundle / "MANIFEST.json").read_text(encoding="utf-8"))
    rubrics = []
    for entry in sorted(manifest["inputs"]["rubrics"], key=lambda item: item["round"]):
        rubrics.extend(json.loads((bundle / entry["path"]).read_text(encoding="utf-8")))
    try:
        report = Path(report_path).read_text(encoding="utf-8")
        heatmap = Path(heatmap_path).read_text(encoding="utf-8")
    except OSError as exc:
        return {"result": "fail", "errors": [{"code": "OUTPUT_MISSING", "message": str(exc)}]}
    placeholder = re.compile(r"\{[^{}\r\n]+\}")
    if placeholder.search(report):
        errors.append({"code": "REPORT_PLACEHOLDER", "message": "Feedback report still contains template placeholders"})
    heatmap_without_code = re.sub(r"<(style|script)\b[^>]*>.*?</\1>", "", heatmap, flags=re.IGNORECASE | re.DOTALL)
    if placeholder.search(heatmap_without_code):
        errors.append({"code": "HEATMAP_PLACEHOLDER", "message": "Heatmap still contains template placeholders"})
    parser = HeatmapParser()
    parser.feed(heatmap)
    if parser.orphan_cells:
        errors.append({"code": "HEATMAP_ORPHAN_CELL", "message": "Score cells must be inside a rubric row"})
    totals = {}
    for model in manifest["models"]:
        model_id = model["model_id"]
        scored = json.loads((scored_root / f"rubrics-{model_id}.json").read_text(encoding="utf-8"))
        score = sum(item["score"] for item in scored)
        totals[model_id] = score
        if not re.search(rf"{re.escape(model['display_name'])}[^\r\n]{{0,240}}(?<!\d){score}\s*/\s*{len(rubrics)}(?!\d)", report):
            errors.append({"code": "REPORT_TOTAL_MISSING", "message": f"Report lacks model/total for {model_id}"})
        for item in scored:
            matches = [cell for cell in parser.rows.get(item["id"], []) if cell[0] == model_id]
            if matches != [(model_id, str(item["score"]))]:
                errors.append({"code": "HEATMAP_CELL_MISSING", "message": f"Missing score cell for {model_id}/{item['id']}"})
    expected_rubrics = {item["id"] for item in rubrics}
    if set(parser.rows) - expected_rubrics:
        errors.append({"code": "HEATMAP_EXTRA_ROW", "message": f"Unknown rubric rows: {sorted(set(parser.rows)-expected_rubrics)}"})
    for rubric in rubrics:
        if rubric["id"] not in parser.rows:
            errors.append({"code": "HEATMAP_ROW_MISMATCH", "message": f"Expected exactly one row for {rubric['id']}"})
    return {"result": "pass" if not errors else "fail", "errors": errors, "totals": totals, "rubric_count": len(rubrics)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("human_decisions", type=Path)
    parser.add_argument("scored_dir", type=Path)
    parser.add_argument("report", type=Path)
    parser.add_argument("heatmap", type=Path)
    args = parser.parse_args(argv)
    try:
        result = verify_supporting_outputs(args.bundle, args.human_decisions, args.scored_dir, args.report, args.heatmap)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        result = {"result": "fail", "errors": [{"code": "INPUT_INVALID", "message": str(exc)}]}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["result"] == "pass" else 3


if __name__ == "__main__":
    sys.exit(main())
