#!/usr/bin/env python3
"""Turn a verified evidence bundle into a local human-review workspace."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path, PurePosixPath

from validate_bundle import validate_bundle


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _methods(row: dict) -> list[str]:
    reason = row.get("reason_code")
    coverage = row.get("coverage")
    if reason == "unsafe_to_test" or coverage == "unsafe_to_test":
        return ["probe_run"]
    if any("conversation" in str(item).lower() for item in row.get("limitations", [])):
        return ["conversation"]
    if row.get("human_check_needed"):
        return []
    return ["static_line", "inventory_fact", "probe_run"]


def prepare_local_review(bundle_dir: str | Path, output_dir: str | Path) -> dict:
    bundle = Path(bundle_dir).resolve()
    output = Path(output_dir).resolve()
    report = validate_bundle(bundle)
    if report["result"] != "pass" or report["derived_status"] == "incomplete":
        raise ValueError(json.dumps(report, ensure_ascii=False))
    manifest = json.loads((bundle / "MANIFEST.json").read_text(encoding="utf-8"))
    adjudications = json.loads((bundle / "review" / "pending-adjudications.json").read_text(encoding="utf-8")).get("items", [])
    pending = [item for item in adjudications if item.get("status") == "pending"]
    material_gaps = report.get("material_gaps", [])
    requests: list[dict] = []
    human_checks: list[dict] = []
    request_number = 1
    for model in manifest.get("models", []):
        path = bundle / PurePosixPath(model["directory"]) / "rubric-evidence.jsonl"
        for row in _jsonl(path):
            if row.get("human_check_needed"):
                human_checks.append({"model_id": model["model_id"], "rubric_id": row["rubric_id"], "fact_summary": row.get("fact_summary", ""), "evidence_ids": [item.get("evidence_id") for item in row.get("evidence", []) if item.get("evidence_id")]})
            methods = _methods(row)
            if row.get("suggested_score") is None and not row.get("adjudication_ids") and methods:
                requests.append({
                    "request_id": f"REQ-{request_number:03d}",
                    "model_id": model["model_id"],
                    "rubric_id": row["rubric_id"],
                    "round": row.get("rubric_round"),
                    "need": f"补足能够判断该条目的可验证证据；当前原因：{row.get('reason_code', 'unknown')}",
                    "why_needed": row.get("fact_summary", "证据不足，不能转成便利 0/1。"),
                    "current_evidence_ids": [item.get("evidence_id") for item in row.get("evidence", []) if item.get("evidence_id")],
                    "allowed_methods": methods,
                    "acceptance": "返回带稳定来源、哈希、行号/原始日志的事实；无法取得时明确 unresolved。",
                })
                request_number += 1
    can_finalize = not pending and not requests and not human_checks and not material_gaps and report["derived_status"] == "ready_for_form"
    result = {
        "schema_version": "1.0.0",
        "base_package_id": manifest["package_id"],
        "bundle_status": report["derived_status"],
        "can_finalize": can_finalize,
        "pending_adjudications": pending,
        "human_checks": human_checks,
        "material_gaps": material_gaps,
        "evidence_requests": requests,
    }
    output.mkdir(parents=True, exist_ok=False)
    (output / "local-review.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output / "evidence_requests.json").write_text(json.dumps({"schema_version": "1.0.0", "base_package_id": manifest["package_id"], "requests": requests}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = ["# 人工裁定与补证清单", "", f"> 包：`{manifest['package_id']}`；状态：`{report['derived_status']}`", "", "## 待裁定项", ""]
    if pending:
        for item in pending:
            lines.append(f"- **{item.get('adjudication_id')}**：{item.get('question', '')}（rubric：{', '.join(item.get('rubric_ids', []))}）")
            lines.append(f"  - 歧义：{item.get('ambiguity', '未提供；退回远端补完整')} ")
            lines.append(f"  - 建议口径：{item.get('suggested_policy', '待人工提出')}；备选口径：{item.get('alternative_policy', '待人工提出')}")
            lines.append(f"  - 分数影响：{json.dumps(item.get('score_impact', {}), ensure_ascii=False)}；证据：{', '.join(item.get('evidence_ids', [])) or '无'}")
    else:
        lines.append("- 无")
    lines.extend(["", "## 缺失材料（必须显式闭环）", ""])
    if material_gaps:
        for gap in material_gaps:
            lines.append(f"- **{gap}**：优先回第三方机器重新完整 export；若客观无法取得，必须由具名人工在 `material_gap_resolutions` 中记录决定、时间和继续使用的限制，不得静默跳过。")
    else:
        lines.append("- 无")
    lines.extend(["", "## 待人工实测/主观确认", ""])
    if human_checks:
        for item in human_checks:
            lines.append(f"- {item['model_id']} / {item['rubric_id']}：{item['fact_summary']}")
    else:
        lines.append("- 无")
    lines.extend(["", "## 需远端补证", ""])
    if requests:
        for item in requests:
            lines.append(f"- {item['request_id']} · {item['model_id']} / {item['rubric_id']}：{item['need']}")
    else:
        lines.append("- 无")
    (output / "人工裁定清单.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle_dir", type=Path)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args(argv)
    try:
        result = prepare_local_review(args.bundle_dir, args.output_dir)
    except (ValueError, FileExistsError) as exc:
        print(str(exc), file=sys.stderr)
        return 3
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["can_finalize"] else 2


if __name__ == "__main__":
    sys.exit(main())
