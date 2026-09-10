import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

from discover_task_package import discover_task_package
from initialize_bundle import initialize_bundle, main as initialize_main
from verify_source import main as verify_main


def make_task(parent: Path) -> Path:
    task = parent / "示例题-0908"
    rounds = task / "示例题轮次记录"
    model = task / "示例题-模型输出" / "模型 A"
    rounds.mkdir(parents=True)
    model.mkdir(parents=True)
    (rounds / "prompt.md").write_text("实现一个按钮。\n", encoding="utf-8")
    (rounds / "rubrics1.json").write_text(
        json.dumps(
            [
                {
                    "id": "R1-01",
                    "round": 1,
                    "criterion": "按钮点击后更新状态。",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (model / "app.py").write_text("clicked = False\n", encoding="utf-8")
    return task


def write_discovery(task: Path, path: Path) -> dict:
    discovery = discover_task_package(task)
    path.write_text(
        json.dumps(discovery, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return discovery


class InitializeAndVerifyTests(unittest.TestCase):
    def test_initialize_creates_expected_bundle_structure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task = make_task(root)
            discovery_path = root / "discovery.json"
            discovery = write_discovery(task, discovery_path)
            bundle = initialize_bundle(task, discovery_path, root / "bundle")

            self.assertTrue(discovery["ok"])
            expected_files = {
                "MANIFEST.json",
                "source-freeze.json",
                "run-state.json",
                "inputs/prompt.md",
                "inputs/rubrics/rubrics1.json",
                "inputs/rubric-index.json",
                "inputs/prompt-requirements.jsonl",
                "models/a/model.json",
                "models/a/inventory.json",
                "models/a/rubric-evidence.jsonl",
                "models/a/human-observations.jsonl",
                "review/pending-adjudications.json",
                "review/rubric-review.json",
            }
            actual_files = {
                path.relative_to(bundle).as_posix()
                for path in bundle.rglob("*")
                if path.is_file()
            }
            self.assertTrue(expected_files.issubset(actual_files))

            manifest = json.loads((bundle / "MANIFEST.json").read_text(encoding="utf-8"))
            self.assertEqual(discovery["input_digest"], manifest["source_input_digest"])
            self.assertEqual(["a"], [item["model_id"] for item in manifest["models"]])

            inventory = json.loads(
                (bundle / "models/a/inventory.json").read_text(encoding="utf-8")
            )
            self.assertEqual(["app.py"], [item["path"] for item in inventory["final"]["files"]])

            evidence_rows = [
                json.loads(line)
                for line in (bundle / "models/a/rubric-evidence.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
                if line.strip()
            ]
            self.assertEqual(1, len(evidence_rows))
            self.assertEqual("R1-01", evidence_rows[0]["rubric_id"])
            self.assertIsNone(evidence_rows[0]["suggested_score"])

    def test_initialize_rejects_failed_discovery_and_existing_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task = make_task(root)

            failed_discovery = root / "failed-discovery.json"
            failed_discovery.write_text('{"ok": false}\n', encoding="utf-8")
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                result = initialize_main([str(task), str(failed_discovery), str(root / "bundle")])
            self.assertEqual(3, result)
            self.assertIn("Discovery is not successful", stderr.getvalue())

            valid_discovery = root / "discovery.json"
            write_discovery(task, valid_discovery)
            existing = root / "already-exists"
            existing.mkdir()
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                result = initialize_main([str(task), str(valid_discovery), str(existing)])
            self.assertEqual(3, result)
            self.assertIn("already exists", stderr.getvalue())

    def test_verify_source_returns_zero_when_source_is_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task = make_task(root)
            discovery_path = root / "discovery.json"
            write_discovery(task, discovery_path)
            bundle = initialize_bundle(task, discovery_path, root / "bundle")

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                result = verify_main([str(task), str(bundle / "source-freeze.json")])

            self.assertEqual(0, result)
            payload = json.loads(stdout.getvalue())
            self.assertEqual("unchanged", payload["status"])

    def test_verify_source_returns_four_after_model_output_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task = make_task(root)
            discovery_path = root / "discovery.json"
            write_discovery(task, discovery_path)
            bundle = initialize_bundle(task, discovery_path, root / "bundle")
            (task / "示例题-模型输出/模型 A/app.py").write_text(
                "clicked = True\n", encoding="utf-8"
            )

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                result = verify_main([str(task), str(bundle / "source-freeze.json")])

            self.assertEqual(4, result)
            payload = json.loads(stdout.getvalue())
            self.assertEqual("source_changed", payload["status"])
            self.assertEqual(["示例题-模型输出/模型 A/app.py"], payload["changed"])
            self.assertEqual([], payload["added"])
            self.assertEqual([], payload["removed"])


if __name__ == "__main__":
    unittest.main()
