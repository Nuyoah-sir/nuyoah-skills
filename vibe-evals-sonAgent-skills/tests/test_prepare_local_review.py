import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

from prepare_local_review import prepare_local_review
from package_bundle import package_bundle, safe_extract_zip
from tests.test_validate_bundle import make_bundle, write_json


class PrepareLocalReviewTests(unittest.TestCase):
    def sealed(self, root, bundle):
        archive = root / "sealed.zip"
        package_bundle(bundle, archive)
        return safe_extract_zip(archive, root / "sealed")

    def test_ready_bundle_produces_finalize_ready_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = self.sealed(root, make_bundle(root))
            result = prepare_local_review(bundle, root / "review-out")
            self.assertTrue(result["can_finalize"])
            self.assertEqual([], result["evidence_requests"])

    def test_unknown_score_becomes_request_not_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = make_bundle(root, "ready_for_local_review")
            path = bundle / "models" / "model-a" / "rubric-evidence.jsonl"
            row = json.loads(path.read_text(encoding="utf-8"))
            row["suggested_score"] = None
            row["reason_code"] = "insufficient_evidence"
            row["coverage"] = "partial"
            row["evidence"] = []
            path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
            bundle = self.sealed(root, bundle)
            result = prepare_local_review(bundle, root / "review-out")
            self.assertFalse(result["can_finalize"])
            self.assertEqual("model-a", result["evidence_requests"][0]["model_id"])
            self.assertNotIn("score", result["evidence_requests"][0])

    def test_incomplete_bundle_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = self.sealed(root, make_bundle(root))
            (bundle / "models" / "model-a" / "rubric-evidence.jsonl").write_text("", encoding="utf-8")
            with self.assertRaises(ValueError):
                prepare_local_review(bundle, root / "review-out")

    def test_material_gap_is_actionable_in_review_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw = make_bundle(root, "ready_for_local_review")
            manifest_path = raw / "MANIFEST.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["missing_materials"] = ["record_txt_missing"]
            write_json(manifest_path, manifest)
            bundle = self.sealed(root, raw)
            result = prepare_local_review(bundle, root / "review-out")
            self.assertEqual(["record_txt_missing"], result["material_gaps"])
            self.assertFalse(result["can_finalize"])
            self.assertIn("record_txt_missing", (root / "review-out/人工裁定清单.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
