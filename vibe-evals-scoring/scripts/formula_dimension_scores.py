#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""旁路计算维度分：按《Coding Agent 人评标准 V 2.1》五、Rubric 的映射规则出数。

    维度分 = 向下取整到 0.5 的倍数( 通过条数 k / 总条数 n × 4 )

口径（2026-09-22 用户定稿）：
- 只吃 rubrics-{X}.json 里已确认的 score，0/1 二值，不给部分分；
- 条目按各自的 dimension 字段归类，多选时逐维度各计一次；
- 没标 dimension 的条目只登记不计分；没有 score 的条目列为未判定缺口，不按 0 计；
- 本结果是**旁路数据**：不参与 Rank、总体印象分与 V2.1 表单的维度分。

用法（Windows 用 py 启动器）：
    py formula_dimension_scores.py rubrics-*.json [--out 维度分-公式.md] [--json 维度分-公式.json]

每个输入文件视为一个模型，模型名取文件名去掉 `rubrics-` 前缀与扩展名。
"""

from __future__ import annotations

import argparse
import json
import sys
from math import floor
from pathlib import Path


def pick(item: dict, *names):
    """按名称取值，兼容大小写两种字段写法（score/Score、dimension/Dimension）。"""
    for name in names:
        if name in item:
            return item[name]
    lowered = {key.lower(): value for key, value in item.items()}
    for name in names:
        if name.lower() in lowered:
            return lowered[name.lower()]
    return None


def as_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, (list, tuple, set)):
        return [str(v).strip() for v in value if str(v).strip()]
    return [str(value).strip()]


def is_pass(score) -> bool:
    if score is True:
        return True
    if isinstance(score, (int, float)):
        return score == 1
    if isinstance(score, str):
        return score.strip() in ("1", "true", "True", "通过")
    return False


def floor_to_half(value: float) -> float:
    return floor(value * 2) / 2


def model_name(path: Path) -> str:
    stem = path.stem
    prefix = "rubrics-"
    return stem[len(prefix):] if stem.startswith(prefix) else stem


def read_items(path: Path) -> list:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        for key in ("rubrics", "criterions", "criteria", "items", "data"):
            if isinstance(data.get(key), list):
                return data[key]
        raise SystemExit(f"{path}: 找不到 rubric 列表")
    if not isinstance(data, list):
        raise SystemExit(f"{path}: 顶层既不是列表也不是含 rubric 列表的对象")
    return data


def analyse(path: Path) -> dict:
    items = read_items(path)
    per_dim: dict[str, dict[str, int]] = {}
    no_dimension: list = []
    undecided: list = []
    pending_per_dim: dict[str, int] = {}

    for index, item in enumerate(items, 1):
        if not isinstance(item, dict):
            continue
        label = item.get("item_id") or item.get("id") or index
        score = pick(item, "score", "Score")
        dimensions = as_list(pick(item, "dimension", "Dimension"))

        if score is None:
            # 未判定 ≠ 0 分：既不进分子也不进分母，只登记缺口
            undecided.append(label)
            for dimension in dimensions:
                pending_per_dim[dimension] = pending_per_dim.get(dimension, 0) + 1
            if not dimensions:
                no_dimension.append(label)
            continue

        if not dimensions:
            no_dimension.append(label)
            continue
        for dimension in dimensions:
            bucket = per_dim.setdefault(dimension, {"n": 0, "k": 0})
            bucket["n"] += 1
            if is_pass(score):
                bucket["k"] += 1

    rows = []
    for dimension, bucket in per_dim.items():
        n, k = bucket["n"], bucket["k"]
        raw = (k / n) * 4 if n else 0.0
        rows.append(
            {
                "dimension": dimension,
                "n": n,
                "k": k,
                "rate": (k / n) if n else 0.0,
                "raw": raw,
                "score": floor_to_half(raw),
            }
        )
    rows.sort(key=lambda row: (-row["score"], row["dimension"]))
    return {
        "model": model_name(path),
        "path": str(path),
        "total": len(items),
        "rows": rows,
        "no_dimension": no_dimension,
        "undecided": undecided,
        "pending_per_dim": pending_per_dim,
    }


def render(results: list[dict]) -> str:
    lines = [
        "# 维度分（公式）",
        "",
        "> **旁路数据，不进定稿**：由 `rubrics-{X}.json` 的 0/1 判定直接算出，"
        "不参与 Rank、总体印象分与 V2.1 表单的维度分，也不改动「3 分 = 验收线、4 分必须有特别亮点」的口径。",
        "",
        "口径：《Coding Agent 人评标准 V 2.1》五、Rubric「Rubric与维度分数映射规则」——"
        "`通过率 = k / n`、`原始分 = 通过率 × 4`、`维度分 = 向下取整到 0.5 的倍数`；"
        "条目按 `dimension` 归类，多选逐维度各计一次。",
        "",
    ]

    for result in results:
        lines.append(f"## {result['model']}")
        lines.append("")
        if not result["rows"]:
            lines.append("- 该文件没有可计分的维度（条目未标 dimension 或无判定）。")
            lines.append("")
        else:
            lines.append("| 维度 | n（总条数） | k（通过条数） | 通过率 | 原始分 | 维度分 |")
            lines.append("| --- | --- | --- | --- | --- | --- |")
            for row in result["rows"]:
                lines.append(
                    f"| {row['dimension']} | {row['n']} | {row['k']} | "
                    f"{row['k']}/{row['n']} = {row['rate']:.3f} | {row['raw']:.2f} | {row['score']:.1f} |"
                )
            lines.append("")
        if result["no_dimension"]:
            lines.append(f"- 未标 dimension、不计分的条目：{', '.join(str(x) for x in result['no_dimension'])}")
        if result["undecided"]:
            lines.append(
                f"- 缺 score、未判定的条目（未计入分子分母）：{', '.join(str(x) for x in result['undecided'])}"
            )
        if result["pending_per_dim"]:
            detail = "、".join(f"{dim} {count} 条" for dim, count in sorted(result["pending_per_dim"].items()))
            lines.append(f"- 未判定条目的维度分布：{detail}")
        lines.append("")

    if len(results) > 1:
        dimensions = sorted({row["dimension"] for result in results for row in result["rows"]})
        lines.append("## 横向对照（维度分）")
        lines.append("")
        lines.append("| 模型 | " + " | ".join(dimensions) + " |")
        lines.append("| --- | " + " | ".join("---" for _ in dimensions) + " |")
        for result in results:
            lookup = {row["dimension"]: row["score"] for row in result["rows"]}
            cells = [f"{lookup[dim]:.1f}" if dim in lookup else "—" for dim in dimensions]
            lines.append(f"| {result['model']} | " + " | ".join(cells) + " |")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="旁路计算维度分（向下取整到 0.5 的倍数）")
    parser.add_argument("files", nargs="+", help="rubrics-{X}.json（每个文件一个模型）")
    parser.add_argument("--out", help="写出 Markdown 表格")
    parser.add_argument("--json", help="写出机器可读结果")
    args = parser.parse_args()

    results = [analyse(Path(name)) for name in args.files]
    markdown = render(results)

    if args.out:
        Path(args.out).write_text(markdown, encoding="utf-8", newline="")
        print(f"wrote {args.out}")
    if args.json:
        Path(args.json).write_text(
            json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline=""
        )
        print(f"wrote {args.json}")
    if not args.out and not args.json:
        print(markdown)

    for result in results:
        summary = "、".join(f"{row['dimension']} {row['score']:.1f}" for row in result["rows"]) or "无可计分维度"
        print(f"{result['model']}: {summary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
