import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

from finalize_scores import finalize_scores
from package_bundle import package_bundle, safe_extract_zip
from prepare_local_review import prepare_local_review
from validate_bundle import validate_bundle
from tests.test_validate_bundle import make_bundle, write_json


class EndToEndTests(unittest.TestCase):
    def test_package_extract_review_and_finalize(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = make_bundle(root)
            archive = root / "dist" / "evidence.zip"
            packaged = package_bundle(bundle, archive)
            self.assertEqual("ready_for_form", packaged["status"])
            extracted = safe_extract_zip(archive, root / "extracted")
            self.assertEqual("pass", validate_bundle(extracted)["result"])
            review = prepare_local_review(extracted, root / "review")
            self.assertTrue(review["can_finalize"])
            decisions = root / "decisions.json"
            write_json(decisions, {"schema_version": "1.0.0", "base_package_id": "pkg-001", "rubric_scores": {"model-a": {"R1-01": {"score": 1, "reason": "点击处理器已绑定（app.js:1）", "evidence_ids": ["EV-model-a-R1-01-001"], "decided_by": "accepted_machine"}}}})
            final = finalize_scores(extracted, decisions, root / "final")
            self.assertEqual(1, final["model_count"])

    def test_ready_for_local_review_v1_emits_legacy_review_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reviewable_bundle = make_bundle(root / "legacy-local-review", "ready_for_local_review")
            reviewable_manifest_path = reviewable_bundle / "MANIFEST.json"
            reviewable_manifest = json.loads(reviewable_manifest_path.read_text(encoding="utf-8"))
            reviewable_manifest["missing_materials"] = ["record_txt_missing"]
            write_json(reviewable_manifest_path, reviewable_manifest)

            evidence_path = reviewable_bundle / "models" / "model-a" / "rubric-evidence.jsonl"
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            evidence.update({
                "coverage": "partial",
                "suggested_score": None,
                "reason_code": "insufficient_evidence",
                "fact_summary": "record_txt_missing",
                "evidence": [],
            })
            evidence_path.write_text(json.dumps(evidence, ensure_ascii=False) + "\n", encoding="utf-8")

            reviewable_archive = root / "dist" / "legacy-local-review.zip"
            package_bundle(reviewable_bundle, reviewable_archive)
            reviewable_extracted = safe_extract_zip(reviewable_archive, root / "legacy-local-review-extracted")
            review_root = root / "legacy-local-review-output"
            local_review = prepare_local_review(reviewable_extracted, review_root)
            self.assertFalse(local_review["can_finalize"])
            self.assertTrue((review_root / "人工裁定清单.md").is_file())
            self.assertTrue((review_root / "evidence_requests.json").is_file())
            self.assertIn("record_txt_missing", (review_root / "人工裁定清单.md").read_text(encoding="utf-8"))
            self.assertIn("record_txt_missing", (review_root / "evidence_requests.json").read_text(encoding="utf-8"))

    def test_extracted_tampering_is_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = make_bundle(root)
            archive = root / "evidence.zip"
            package_bundle(bundle, archive)
            extracted = safe_extract_zip(archive, root / "extracted")
            (extracted / "inputs" / "prompt.md").write_text("tampered", encoding="utf-8")
            report = validate_bundle(extracted)
            self.assertIn("HASH_MISMATCH", {item["code"] for item in report["errors"]})


if __name__ == "__main__":
    unittest.main()
