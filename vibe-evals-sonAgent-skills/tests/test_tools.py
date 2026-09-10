import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

from discover_task_package import discover_task_package
from package_bundle import safe_extract_zip
from package_bundle import package_bundle
from extract_artifact import extract_artifact
from tests.test_validate_bundle import make_bundle


class ToolTests(unittest.TestCase):
    def test_discovery_prefers_merged_rubrics_and_normalizes_models(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "题-0908"
            rounds = root / "题轮次记录"
            models = root / "题-模型输出"
            rounds.mkdir(parents=True)
            models.mkdir()
            (rounds / "prompt.md").write_text("R1", encoding="utf-8")
            (rounds / "原rubrics1.json").write_text("[]", encoding="utf-8")
            (rounds / "合并rubrics1.json").write_text("[]", encoding="utf-8")
            (models / "模型 A").mkdir()
            result = discover_task_package(root)
            self.assertTrue(result["ok"])
            self.assertTrue(result["rubrics"][0]["path"].endswith("合并rubrics1.json"))
            self.assertEqual("a", result["models"][0]["model_id"])

    def test_discovery_stops_when_prompt_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "题-0908"
            (root / "题轮次记录").mkdir(parents=True)
            (root / "题-模型输出" / "A").mkdir(parents=True)
            result = discover_task_package(root)
            self.assertFalse(result["ok"])
            self.assertIn("PROMPT_MISSING", {e["code"] for e in result["errors"]})

    def test_safe_extract_rejects_zip_traversal(self):
        import zipfile

        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "bad.zip"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("../escape.txt", "bad")
            with self.assertRaises(ValueError):
                safe_extract_zip(archive, Path(tmp) / "out")

    def test_safe_extract_rejects_windows_collisions_and_reserved_names(self):
        import zipfile

        for names in (("A.txt", "a.txt"), ("CON.txt",)):
            with self.subTest(names=names), tempfile.TemporaryDirectory() as tmp:
                archive = Path(tmp) / "bad.zip"
                with zipfile.ZipFile(archive, "w") as zf:
                    for name in names:
                        zf.writestr(name, "bad")
                with self.assertRaises(ValueError):
                    safe_extract_zip(archive, Path(tmp) / "out")

    def test_extract_artifact_verifies_sidecar_and_sealed_bundle(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "bundle.zip"
            package_bundle(make_bundle(root), archive)
            result = extract_artifact("bundle", archive, archive.with_suffix(".zip.sha256"), root / "extracted")
            self.assertEqual("pass", result["validation"]["result"])

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "bundle.zip"
            package_bundle(make_bundle(root), archive)
            archive.with_suffix(".zip.sha256").write_text("0" * 64 + "  bundle.zip\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                extract_artifact("bundle", archive, archive.with_suffix(".zip.sha256"), root / "extracted")

if __name__ == "__main__":
    unittest.main()
