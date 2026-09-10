import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

from package_bundle import package_bundle, safe_extract_zip
from finalize_scores import finalize_scores
from tests.test_validate_bundle import make_bundle, write_json
from verify_supporting_outputs import verify_supporting_outputs


class VerifySupportingOutputsTests(unittest.TestCase):
    def test_checks_report_total_heatmap_rows_cells_and_placeholders(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "bundle.zip"
            package_bundle(make_bundle(root), archive)
            bundle = safe_extract_zip(archive, root / "sealed")
            decisions = root / "decisions.json"
            write_json(decisions, {"schema_version": "1.0.0", "base_package_id": "pkg-001", "rubric_scores": {"model-a": {"R1-01": {"score": 1, "reason": "绑定成立", "evidence_ids": ["EV-model-a-R1-01-001"], "decided_by": "accepted_machine"}}}})
            scored = root / "scored"
            finalize_scores(bundle, decisions, scored)
            report = root / "反馈报告.md"
            report.write_text("# 报告\n模型 A：1/1\n", encoding="utf-8")
            heatmap = root / "heatmap.html"
            heatmap.write_text('<tr data-rubric-id="R1-01"><td data-model-id="model-a" data-score="1">1</td></tr>', encoding="utf-8")
            self.assertEqual("pass", verify_supporting_outputs(bundle, decisions, scored, report, heatmap)["result"])
            heatmap.write_text('<tr data-rubric-id="{R1-01}"></tr>', encoding="utf-8")
            result = verify_supporting_outputs(bundle, decisions, scored, report, heatmap)
            self.assertEqual("fail", result["result"])
            self.assertIn("HEATMAP_PLACEHOLDER", {item["code"] for item in result["errors"]})

    def test_css_is_not_placeholder_and_orphan_or_wrong_row_cells_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "bundle.zip"
            package_bundle(make_bundle(root), archive)
            bundle = safe_extract_zip(archive, root / "sealed")
            decisions = root / "decisions.json"
            write_json(decisions, {"schema_version": "1.0.0", "base_package_id": "pkg-001", "rubric_scores": {"model-a": {"R1-01": {"score": 1, "reason": "绑定成立", "evidence_ids": ["EV-model-a-R1-01-001"], "decided_by": "accepted_machine"}}}})
            scored = root / "scored"
            finalize_scores(bundle, decisions, scored)
            report = root / "report.md"
            report.write_text("| 模型 A | 1/1 |\n", encoding="utf-8")
            heatmap = root / "heatmap.html"
            heatmap.write_text('<style>body { color: red; }</style><tr data-rubric-id="R1-01"><td data-model-id="model-a" data-score="1">1</td></tr>', encoding="utf-8")
            self.assertEqual("pass", verify_supporting_outputs(bundle, decisions, scored, report, heatmap)["result"])
            heatmap.write_text('<tr data-rubric-id="R1-01"></tr><td data-model-id="model-a" data-score="1">1</td>', encoding="utf-8")
            codes = {item["code"] for item in verify_supporting_outputs(bundle, decisions, scored, report, heatmap)["errors"]}
            self.assertIn("HEATMAP_ORPHAN_CELL", codes)
            self.assertIn("HEATMAP_CELL_MISSING", codes)


if __name__ == "__main__":
    unittest.main()
