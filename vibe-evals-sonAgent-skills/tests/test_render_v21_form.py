import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

from package_bundle import package_bundle, safe_extract_zip
from finalize_scores import finalize_scores
from render_v21_form import render_v21_form, verify_v21_form
from tests.test_validate_bundle import APP_BYTES, APP_DIGEST, identity_digest, inventory_digest, make_bundle, write_json


LABEL_LIBRARY = """\
# 标签库
## Pros
| tag | 定义 |
| --- | --- |
| 操作流畅 | 交互顺畅 |
## Cons
| tag | 定义 |
| --- | --- |
| 行为不正确 | 功能行为错误 |
## Stylistic Fingerprints
| tag | 定义 |
| --- | --- |
| 偏好原生 JS | 使用原生 JavaScript |
"""


def add_model(bundle: Path, model_id: str, display_name: str) -> None:
    manifest_path = bundle / "MANIFEST.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["models"].append(
        {
            "model_id": model_id,
            "display_name": display_name,
            "directory": f"models/{model_id}",
            "output_status": "present",
        }
    )
    write_json(manifest_path, manifest)

    source = bundle / "models" / "model-a"
    target = bundle / "models" / model_id
    target.mkdir(parents=True)
    for name in ("model.json", "inventory.json"):
        value = json.loads((source / name).read_text(encoding="utf-8"))
        if name == "model.json":
            value["model_id"] = model_id
            value["display_name"] = display_name
            value["source_output_root_label"] = f"source/{model_id}"
            value["round_source_labels"] = {"1": f"snapshots/{model_id}/R1"}
        else:
            value["rounds"][0]["source_root_label"] = f"snapshots/{model_id}/R1"
        write_json(target / name, value)
    row = json.loads((source / "rubric-evidence.jsonl").read_text(encoding="utf-8"))
    row["model_id"] = model_id
    row["evidence"][0]["evidence_id"] = f"EV-{model_id}-R1-01-001"
    (target / "rubric-evidence.jsonl").write_text(
        json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    freeze_path = bundle / "source-freeze.json"
    freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
    freeze["source_inventory"].extend([
        {"path": f"source/{model_id}/app.js", "size": len(APP_BYTES), "sha256": APP_DIGEST},
        {"path": f"snapshots/{model_id}/R1/app.js", "size": len(APP_BYTES), "sha256": APP_DIGEST},
    ])
    freeze["snapshot_dirs"].append(f"snapshots/{model_id}/R1")
    freeze["model_sources"][model_id] = {"display_name": display_name, "final_root": f"source/{model_id}", "round_roots": {"1": f"snapshots/{model_id}/R1"}, "conversation_source_path": None}
    freeze["source_inventory_digest"] = inventory_digest(freeze["source_inventory"])
    freeze["input_digest"] = identity_digest(freeze["source_inventory_digest"], freeze["prompt_source"], freeze["rubric_sources"], freeze["model_sources"])
    write_json(freeze_path, freeze)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["source_input_digest"] = freeze["input_digest"]
    write_json(manifest_path, manifest)


def seal(bundle: Path, root: Path) -> Path:
    archive = root / "bundle.zip"
    package_bundle(bundle, archive)
    return safe_extract_zip(archive, root / "sealed")


def model_input(overall: float, score: float = 3.0, *, s1=True) -> dict:
    dimensions = {
        "G1": {"score": score, "basis": "显性约束已落实（R1-01）。"},
        "G2": {"score": score, "basis": "核心按钮可运行（R1-01）。"},
        "G3": {"score": score, "basis": "首轮完成并自检。"},
        "S1": (
            {"applicable": True, "score": score, "basis": "前端界面可见且布局完整。"}
            if s1
            else {"applicable": False, "basis": "纯后端任务，无可视界面。"}
        ),
        "A1": {"applicable": False, "basis": "当前 V2.1 政策暂不启用 A1。"},
        "R1": {"applicable": False, "basis": "首轮未提问。"},
    }
    evidence_id = "EV-model-a-R1-01-001"
    return {
        "overall_impression": overall,
        "dimensions": dimensions,
        "pros": ["按钮链路完整（R1-01）。", "交付文件可直接检查（R1-01）。"],
        "cons": [],
        "style": "偏好原生实现（app.js）。",
        "evidence_refs": {"overall": [evidence_id], "dimensions": {key: [evidence_id] if key in {"G1", "G2", "G3"} or dimensions[key].get("applicable", True) else [] for key, _ in (("G1", ""), ("G2", ""), ("G3", ""), ("S1", ""), ("A1", ""), ("R1", ""))}, "pros": [[evidence_id], [evidence_id]], "cons": [], "style": [evidence_id]},
        "labels": {"pros": ["操作流畅"], "cons": [], "style": ["偏好原生 JS"]},
    }


def form_input(package_id: str, models: dict) -> dict:
    return {
        "schema_version": "1.0.0",
        "base_package_id": package_id,
        "task": {
            "ranking_reason": "总体印象优先；同分时以同一组 Rubric 得分决胜。",
            "maximum_difference": "核心链路完整度不同。",
            "capability_boundary": "静态实现不能替代真实交互验证。",
            "difficulty_and_approach": "难点是同时满足功能与交互；逐项映射 Rubric 验收。",
            "evidence_refs": {
                "ranking_reason": ["EV-model-a-R1-01-001"],
                "maximum_difference": ["EV-model-a-R1-01-001"],
                "capability_boundary": ["EV-model-a-R1-01-001"],
                "difficulty_and_approach": ["EV-model-a-R1-01-001"],
            },
        },
        "models": models,
    }


def write_scored(bundle: Path, root: Path, model_scores: dict[str, int]) -> tuple[Path, Path]:
    decisions = root / "human-decisions.json"
    rubric_scores = {}
    for model_id, score in model_scores.items():
        rubric_scores[model_id] = {"R1-01": {"score": score, "reason": "人工已裁定。", "evidence_ids": [f"EV-{model_id}-R1-01-001"], "decided_by": "human"}}
    write_json(decisions, {"schema_version": "1.0.0", "base_package_id": "pkg-001", "rubric_scores": rubric_scores})
    scored_dir = root / "scored"
    finalize_scores(bundle, decisions, scored_dir)
    return scored_dir, decisions


class RenderV21FormTests(unittest.TestCase):
    def test_sorts_by_overall_then_rubric_and_keeps_double_tie(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = make_bundle(root)
            add_model(bundle, "model-b", "模型 B")
            add_model(bundle, "model-c", "模型 C")
            add_model(bundle, "model-d", "模型 D")
            sealed = seal(bundle, root)
            scored, decisions = write_scored(sealed, root, {"model-a": 0, "model-b": 1, "model-c": 1, "model-d": 0})
            labels = root / "labels.md"
            labels.write_text(LABEL_LIBRARY, encoding="utf-8")
            data = form_input(
                "pkg-001",
                {
                    "model-a": model_input(3.5),
                    "model-b": model_input(3.5),
                    "model-c": model_input(3.5),
                    "model-d": model_input(3.0),
                },
            )
            for model_id in data["models"]:
                replacement = f"EV-{model_id}-R1-01-001"
                refs = data["models"][model_id]["evidence_refs"]
                refs["overall"] = [replacement]
                refs["dimensions"] = {key: ([replacement] if values else []) for key, values in refs["dimensions"].items()}
                refs["pros"] = [[replacement] for _ in refs["pros"]]
                refs["cons"] = [[replacement] for _ in refs["cons"]]
                refs["style"] = [replacement]
            input_path = root / "form-input.json"
            write_json(input_path, data)
            output = root / "form.md"

            result = render_v21_form(sealed, decisions, scored, input_path, output, labels)
            text = output.read_text(encoding="utf-8")

            self.assertEqual(["model-b", "model-c", "model-a", "model-d"], result["order"])
            self.assertEqual({"model-b": 1, "model-c": 1, "model-a": 2, "model-d": 3}, result["ranks"])
            self.assertIn("模型 B = 模型 C > 模型 A > 模型 D", text)
            self.assertLess(text.index("| 1 | **模型 B**"), text.index("| 2 | **模型 A**"))

    def test_single_model_n_a_has_no_numeric_controls(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sealed = seal(make_bundle(root), root)
            scored, decisions = write_scored(sealed, root, {"model-a": 1})
            labels = root / "labels.md"
            labels.write_text(LABEL_LIBRARY, encoding="utf-8")
            data = form_input("pkg-001", {"model-a": model_input(3.5, s1=False)})
            input_path = root / "form-input.json"
            write_json(input_path, data)
            output = root / "form.md"

            render_v21_form(sealed, decisions, scored, input_path, output, labels)
            text = output.read_text(encoding="utf-8")

            self.assertIn("模型 A（单模型，无同场比较/无排序）", text)
            self.assertIn("N/A【不适用：纯后端任务，无可视界面。】", text)
            s1 = text.split("#### S1 · 视觉审美", 1)[1].split("#### A1", 1)[0]
            self.assertNotIn("【建议", s1)
            self.assertNotIn("[ ] 0", s1)
            self.assertIn("【建议 3.5，待人工确认】", text)

    def test_rejects_invalid_scores_labels_and_model_coverage(self):
        mutations = []
        bad_step = form_input("pkg-001", {"model-a": model_input(3.3)})
        mutations.append(bad_step)
        bad_label = form_input("pkg-001", {"model-a": model_input(3.5)})
        bad_label["models"]["model-a"]["labels"]["pros"] = ["不存在的标签"]
        mutations.append(bad_label)
        missing_model = form_input("pkg-001", {})
        mutations.append(missing_model)
        bad_na = form_input("pkg-001", {"model-a": model_input(3.5, s1=False)})
        bad_na["models"]["model-a"]["dimensions"]["S1"]["score"] = 0
        mutations.append(bad_na)

        for index, data in enumerate(mutations):
            with self.subTest(index=index), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                sealed = seal(make_bundle(root), root)
                scored, decisions = write_scored(sealed, root, {"model-a": 1})
                labels = root / "labels.md"
                labels.write_text(LABEL_LIBRARY, encoding="utf-8")
                input_path = root / "form-input.json"
                write_json(input_path, data)
                with self.assertRaises(ValueError):
                    render_v21_form(sealed, decisions, scored, input_path, root / "form.md", labels)

    def test_refuses_overwrite_and_verify_is_byte_exact(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sealed = seal(make_bundle(root), root)
            scored, decisions = write_scored(sealed, root, {"model-a": 1})
            labels = root / "labels.md"
            labels.write_text(LABEL_LIBRARY, encoding="utf-8")
            input_path = root / "form-input.json"
            write_json(input_path, form_input("pkg-001", {"model-a": model_input(3.5)}))
            output = root / "form.md"
            render_v21_form(sealed, decisions, scored, input_path, output, labels)

            self.assertEqual("pass", verify_v21_form(sealed, decisions, scored, input_path, output, labels)["result"])
            with self.assertRaises(FileExistsError):
                render_v21_form(sealed, decisions, scored, input_path, output, labels)
            output.write_text(output.read_text(encoding="utf-8") + "篡改\n", encoding="utf-8")
            report = verify_v21_form(sealed, decisions, scored, input_path, output, labels)
            self.assertEqual("fail", report["result"])
            self.assertEqual("BYTE_MISMATCH", report["errors"][0]["code"])


if __name__ == "__main__":
    unittest.main()
