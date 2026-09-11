#!/usr/bin/env python3
"""Deterministically render and byte-verify the Vibe Evals V2.1 scoring form."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from validate_bundle import validate_bundle
from verify_final_artifacts import verify_final_artifacts

DIMENSIONS = (("G1", "指令与约束遵循"), ("G2", "功能交付完整性"), ("G3", "任务收敛效率"), ("S1", "视觉审美"), ("A1", "架构合理性"), ("R1", "需求理解与澄清"))


def _score(value, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0 or value > 4 or value * 2 != int(value * 2):
        raise ValueError(f"{field} must be 0..4 in 0.5 increments")
    return float(value)


def _fmt(value: float) -> str:
    return str(int(value)) if value.is_integer() else str(value)


def _labels(path: Path) -> set[str]:
    result = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        first = cells[0] if cells else ""
        if first and first.lower() not in {"tag", "标签", "类别"} and not set(first) <= {"-", ":"}:
            result.add(first)
    return result


def _load(bundle: Path, decisions_path: Path, scored_dir: Path, input_path: Path, label_library: Path):
    report = validate_bundle(bundle, require_seal=True)
    if report["result"] != "pass":
        raise ValueError("Bundle is not a valid sealed bundle")
    final_report = verify_final_artifacts(bundle, decisions_path, scored_dir)
    if final_report["result"] != "pass":
        raise ValueError("Scored artifacts are not bound to valid human decisions: " + json.dumps(final_report["errors"], ensure_ascii=False))
    manifest = json.loads((bundle / "MANIFEST.json").read_text(encoding="utf-8"))
    data = json.loads(input_path.read_text(encoding="utf-8"))
    if data.get("schema_version") != "1.0.0" or data.get("base_package_id") != manifest.get("package_id"):
        raise ValueError("Form input identity does not match bundle")
    model_map = {item["model_id"]: item for item in manifest["models"]}
    if set(data.get("models", {})) != set(model_map):
        raise ValueError("Form input must cover exactly every model")
    rubric_ids = []
    for entry in sorted(manifest["inputs"]["rubrics"], key=lambda item: item["round"]):
        rubric_ids.extend(item["id"] for item in json.loads((bundle / entry["path"]).read_text(encoding="utf-8")))
    totals = {}
    evidence_by_model = {}
    evidence_meta_by_model = {}
    for model_id in model_map:
        scored = json.loads((scored_dir / f"rubrics-{model_id}.json").read_text(encoding="utf-8"))
        if [item.get("id") for item in scored] != rubric_ids or any(item.get("score") not in (0, 1) or isinstance(item.get("score"), bool) for item in scored):
            raise ValueError(f"Invalid scored rubrics for {model_id}")
        totals[model_id] = sum(item["score"] for item in scored)
        model_dir = bundle / model_map[model_id]["directory"]
        rows = [json.loads(line) for line in (model_dir / "rubric-evidence.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
        evidence_meta_by_model[model_id] = {
            evidence.get("evidence_id"): {"rubric_id": row.get("rubric_id"), "direction": evidence.get("direction")}
            for row in rows for evidence in row.get("evidence", []) if evidence.get("evidence_id")
        }
        evidence_by_model[model_id] = set(evidence_meta_by_model[model_id])
    decisions = json.loads(decisions_path.read_text(encoding="utf-8"))
    for item in decisions.get("local_evidence", []):
        if item.get("model_id") in evidence_by_model and item.get("evidence_id"):
            evidence_by_model[item["model_id"]].add(item["evidence_id"])
            final_score = decisions.get("rubric_scores", {}).get(item["model_id"], {}).get(item.get("rubric_id"), {}).get("score")
            evidence_meta_by_model[item["model_id"]][item["evidence_id"]] = {
                "rubric_id": item.get("rubric_id"),
                "direction": "support" if final_score == 1 else "refute" if final_score == 0 else "context",
            }
    all_form_evidence = set().union(*evidence_by_model.values()) if evidence_by_model else set()
    task = data.get("task")
    required_task = ("ranking_reason", "maximum_difference", "capability_boundary", "difficulty_and_approach")
    if not isinstance(task, dict) or any(not str(task.get(key, "")).strip() for key in required_task):
        raise ValueError("Form input task section is incomplete")
    task_refs = task.get("evidence_refs")
    if (not isinstance(task_refs, dict) or set(task_refs) != set(required_task)
            or any(not isinstance(task_refs[key], list) or not task_refs[key] for key in required_task)
            or any(ref not in all_form_evidence for key in required_task for ref in task_refs[key])):
        raise ValueError("Task-level claims need non-empty evidence_refs from the verified evidence registry")
    allowed_labels = _labels(label_library)
    for model_id, item in data["models"].items():
        _score(item.get("overall_impression"), f"{model_id}.overall_impression")
        dimensions = item.get("dimensions")
        if not isinstance(dimensions, dict) or set(dimensions) != {key for key, _ in DIMENSIONS}:
            raise ValueError(f"{model_id}.dimensions must contain exactly G1/G2/G3/S1/A1/R1")
        for key, _ in DIMENSIONS:
            dim = dimensions[key]
            applicable = dim.get("applicable", True)
            if not isinstance(applicable, bool) or not str(dim.get("basis", "")).strip():
                raise ValueError(f"Invalid {model_id}.{key} basis/applicability")
            if key in {"G1", "G2", "G3"} and not applicable:
                raise ValueError(f"{key} is mandatory")
            if applicable:
                _score(dim.get("score"), f"{model_id}.{key}.score")
            elif "score" in dim:
                raise ValueError(f"N/A {model_id}.{key} must not contain score")
        if not isinstance(item.get("pros"), list) or len(item["pros"]) != 2 or any(not str(value).strip() for value in item["pros"]):
            raise ValueError(f"{model_id} needs exactly two pros")
        if not isinstance(item.get("cons"), list) or len(item["cons"]) > 5 or any(not str(value).strip() for value in item["cons"]):
            raise ValueError(f"{model_id} cons must contain at most five non-empty items")
        if not str(item.get("style", "")).strip():
            raise ValueError(f"{model_id} needs style text")
        refs = item.get("evidence_refs")
        if not isinstance(refs, dict) or set(refs) != {"overall", "dimensions", "pros", "cons", "style"}:
            raise ValueError(f"{model_id}.evidence_refs shape is invalid")
        if (not isinstance(refs["overall"], list) or not refs["overall"]
                or not isinstance(refs["dimensions"], dict) or set(refs["dimensions"]) != {key for key, _ in DIMENSIONS}
                or not isinstance(refs["pros"], list) or len(refs["pros"]) != len(item["pros"])
                or not isinstance(refs["cons"], list) or len(refs["cons"]) != len(item["cons"])
                or not isinstance(refs["style"], list) or not refs["style"]):
            raise ValueError(f"{model_id}.evidence_refs does not align with the form claims")
        claim_ref_lists = [refs["overall"], refs["style"], *refs["pros"], *refs["cons"]]
        for key, _ in DIMENSIONS:
            values = refs["dimensions"].get(key)
            if not isinstance(values, list) or ((key in {"G1", "G2", "G3"} or item["dimensions"][key].get("applicable", True)) and not values):
                raise ValueError(f"{model_id}.{key} needs evidence references when applicable")
            claim_ref_lists.append(values)
        if any(not isinstance(values, list) or any(ref not in evidence_by_model[model_id] for ref in values) for values in claim_ref_lists):
            raise ValueError(f"{model_id}.evidence_refs contains an unknown or cross-model evidence ID")
        for index, values in enumerate(refs["pros"]):
            if not any(evidence_meta_by_model[model_id][ref]["direction"] == "support" for ref in values):
                raise ValueError(f"{model_id}.pros[{index}] needs at least one supporting evidence reference")
        for index, values in enumerate(refs["cons"]):
            if not any(evidence_meta_by_model[model_id][ref]["direction"] in {"refute", "confirm_missing"} for ref in values):
                raise ValueError(f"{model_id}.cons[{index}] needs at least one refuting or confirm-missing evidence reference")
        labels = item.get("labels", {})
        if set(labels) != {"pros", "cons", "style"} or any(not isinstance(values, list) for values in labels.values()):
            raise ValueError(f"{model_id} labels shape is invalid")
        unknown = {value for values in labels.values() for value in values} - allowed_labels
        if unknown:
            raise ValueError(f"Unknown V2.1 labels for {model_id}: {sorted(unknown)}")
    return manifest, data, model_map, rubric_ids, totals, evidence_meta_by_model


def build_form_from_context(
    manifest: dict,
    form_data: dict,
    model_map: dict,
    rubric_ids: list[str],
    totals: dict[str, int],
    evidence_meta_by_model: dict[str, dict[str, dict]],
    label_library: Path,
) -> tuple[str, dict]:
    """Assemble the V2.1 form from already validated inputs.

    This is the only ordering and Markdown implementation: the v1 adapter and the
    v2 form-ready adapter both call it, so the two paths can never drift apart.
    """

    data = form_data
    for model_id, item in data["models"].items():
        if model_id not in model_map or model_id not in totals:
            raise ValueError(f"{model_id} is not a scored model of this package")
        known = evidence_meta_by_model.get(model_id, {})
        refs = item["evidence_refs"]
        referenced = {ref for values in [refs["overall"], refs["style"], *refs["pros"], *refs["cons"], *refs["dimensions"].values()] for ref in values}
        unknown = sorted(ref for ref in referenced if ref not in known)
        if unknown:
            raise ValueError(f"{model_id} cites evidence that is not in the verified registry: {unknown}")
    overall = {model_id: float(data["models"][model_id]["overall_impression"]) for model_id in model_map}
    insertion = {model_id: index for index, model_id in enumerate(model_map)}
    order = sorted(model_map, key=lambda model_id: (-overall[model_id], -totals[model_id], insertion[model_id]))
    ranks = {}
    rank = 0
    previous = None
    for model_id in order:
        key = (overall[model_id], totals[model_id])
        if key != previous:
            rank += 1
            previous = key
        ranks[model_id] = rank
    if len(order) == 1:
        expression = f"{model_map[order[0]]['display_name']}（单模型，无同场比较/无排序）"
    else:
        groups = []
        for current_rank in sorted(set(ranks.values())):
            groups.append(" = ".join(model_map[model_id]["display_name"] for model_id in order if ranks[model_id] == current_rank))
        expression = " > ".join(groups)
    task = data["task"]
    task_refs = task["evidence_refs"]
    label_digest = hashlib.sha256(label_library.read_bytes()).hexdigest()
    lines = [f"# {manifest['task']['name']}-评分表单", "", f"> 依据：Coding Agent 人评标准 V2.1（本地规则摘要 v1.0.0；标签库 SHA-256 `{label_digest}`）；所有机器预填数值均为待人工确认。", "", "## 〇、题目级共享节", "", "### 模型排序", "", "| 档位 | 模型 | 总体印象分（第一排序键） | Rubric 得分（同分决胜） |", "|---:|---|---:|---:|"]
    for model_id in order:
        lines.append(f"| {ranks[model_id]} | **{model_map[model_id]['display_name']}** | 【建议 {_fmt(overall[model_id])}，待人工确认】 | {totals[model_id]}/{len(rubric_ids)} |")
    lines.extend(["", f"**排序表达式**：{expression}", "", f"**排序理由**：{task['ranking_reason']}（证据：{', '.join(task_refs['ranking_reason'])}）", "", "### 洞察", "", f"- **最大模型差异**：{task['maximum_difference']}（证据：{', '.join(task_refs['maximum_difference'])}）", f"- **能力边界 Insight**：{task['capability_boundary']}（证据：{', '.join(task_refs['capability_boundary'])}）", "", "### 难点和思路", "", f"{task['difficulty_and_approach']}（证据：{', '.join(task_refs['difficulty_and_approach'])}）"])
    for sequence, model_id in enumerate(order, 1):
        item = data["models"][model_id]
        refs = item["evidence_refs"]
        lines.extend(["", "---", "", f"## {sequence}、模型 {model_map[model_id]['display_name']}", "", "### 总体印象", "", f"【建议 {_fmt(overall[model_id])}，待人工确认】　[ ] 0　[ ] 0.5　[ ] 1　[ ] 1.5　[ ] 2　[ ] 2.5　[ ] 3　[ ] 3.5　[ ] 4", f"证据：{', '.join(refs['overall'])}"])
        for key, title in DIMENSIONS:
            dim = item["dimensions"][key]
            lines.extend(["", f"#### {key} · {title}", "", f"**机器证据**：{dim['basis']}（证据：{', '.join(refs['dimensions'][key]) or 'N/A 依据'}）", ""])
            if dim.get("applicable", True):
                score = _score(dim["score"], f"{model_id}.{key}.score")
                lines.append(f"【建议 {_fmt(score)}，待人工确认】　[ ] 0　[ ] 0.5　[ ] 1　[ ] 1.5　[ ] 2　[ ] 2.5　[ ] 3　[ ] 3.5　[ ] 4")
            else:
                lines.append(f"N/A【不适用：{dim['basis']}】")
        lines.extend(["", "### 文字反馈", "", "#### 👍 亮点 Pros", ""])
        lines.extend(f"- {value}（证据：{', '.join(refs['pros'][index])}）" for index, value in enumerate(item["pros"]))
        lines.extend(["", "#### 👎 缺陷 Cons", ""])
        lines.extend([*(f"- {value}（证据：{', '.join(refs['cons'][index])}）" for index, value in enumerate(item["cons"]))] or ["- 无突出缺陷"])
        lines.extend(["", "#### 🎨 风格 / 其他 Style", "", f"{item['style']}（证据：{', '.join(refs['style'])}）", "", "### Vibe 标签", ""])
        labels = item["labels"]
        lines.append(f"- 正面亮点：{'、'.join(labels['pros']) or '无'}")
        lines.append(f"- 负面缺陷：{'、'.join(labels['cons']) or '无'}")
        lines.append(f"- 风格指纹：{'、'.join(labels['style']) or '无'}")
    lines.append("")
    return "\n".join(lines), {"order": order, "ranks": ranks, "totals": totals, "label_library_sha256": label_digest}


def _build(bundle: Path, decisions_path: Path, scored_dir: Path, input_path: Path, label_library: Path) -> tuple[str, dict]:
    manifest, data, model_map, rubric_ids, totals, evidence_meta_by_model = _load(bundle, decisions_path, scored_dir, input_path, label_library)
    return build_form_from_context(manifest, data, model_map, rubric_ids, totals, evidence_meta_by_model, label_library)


def render_v21_form(bundle_dir, decisions_path, scored_dir, input_path, output_path, label_library=None) -> dict:
    output = Path(output_path).resolve()
    if output.exists():
        raise FileExistsError(f"Output already exists: {output}")
    library = Path(label_library).resolve() if label_library else Path(__file__).resolve().parents[1] / "templates" / "V2.1标签库.md"
    content, result = _build(Path(bundle_dir).resolve(), Path(decisions_path).resolve(), Path(scored_dir).resolve(), Path(input_path).resolve(), library)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(content, encoding="utf-8")
    return {**result, "output": str(output)}


def verify_v21_form(bundle_dir, decisions_path, scored_dir, input_path, output_path, label_library=None) -> dict:
    output = Path(output_path).resolve()
    library = Path(label_library).resolve() if label_library else Path(__file__).resolve().parents[1] / "templates" / "V2.1标签库.md"
    try:
        expected, result = _build(Path(bundle_dir).resolve(), Path(decisions_path).resolve(), Path(scored_dir).resolve(), Path(input_path).resolve(), library)
        actual = output.read_text(encoding="utf-8")
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        return {"result": "fail", "errors": [{"code": "INPUT_INVALID", "message": str(exc)}]}
    if actual != expected:
        return {"result": "fail", "errors": [{"code": "BYTE_MISMATCH", "message": "Form differs from deterministic render"}]}
    return {"result": "pass", "errors": [], **result, "output": str(output)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("render", "verify"))
    parser.add_argument("bundle", type=Path)
    parser.add_argument("human_decisions", type=Path)
    parser.add_argument("scored_dir", type=Path)
    parser.add_argument("form_input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--label-library", type=Path)
    args = parser.parse_args(argv)
    try:
        result = render_v21_form(args.bundle, args.human_decisions, args.scored_dir, args.form_input, args.output, args.label_library) if args.mode == "render" else verify_v21_form(args.bundle, args.human_decisions, args.scored_dir, args.form_input, args.output, args.label_library)
    except (ValueError, FileExistsError, OSError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 3
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("result", "pass") == "pass" else 3


if __name__ == "__main__":
    sys.exit(main())
