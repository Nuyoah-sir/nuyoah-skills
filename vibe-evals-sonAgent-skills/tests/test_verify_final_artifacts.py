import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

from finalize_scores import finalize_scores
from package_bundle import package_bundle, safe_extract_zip
from tests.test_validate_bundle import make_bundle, write_json
from verify_final_artifacts import verify_final_artifacts


def write_decisions(path: Path, package_id: str = "pkg-001") -> None:
    write_json(
        path,
        {
            "schema_version": "1.0.0",
            "base_package_id": package_id,
            "rubric_scores": {
                "model-a": {
                    "R1-01": {
                        "score": 1,
                        "reason": "点击处理器已绑定（app.js:1）",
                        "evidence_ids": ["EV-model-a-R1-01-001"],
                        "decided_by": "accepted_machine",
                    }
                }
            },
        },
    )


class VerifyFinalArtifactsTests(unittest.TestCase):
    def completed_fixture(self, root: Path):
        bundle = make_bundle(root)
        archive = root / "sealed.zip"
        package_bundle(bundle, archive)
        sealed = safe_extract_zip(archive, root / "sealed")
        decisions = root / "human-decisions.json"
        write_decisions(decisions)
        output = root / "final"
        finalize_scores(sealed, decisions, output)
        return sealed, decisions, output

    def test_accepts_complete_untampered_output_and_reports_totals(self):
        with tempfile.TemporaryDirectory() as tmp:
            sealed, decisions, output = self.completed_fixture(Path(tmp))

            report = verify_final_artifacts(sealed, decisions, output)

            self.assertEqual("pass", report["result"])
            self.assertEqual([], report["errors"])
            self.assertEqual({"model-a": {"score": 1, "maximum": 1}}, report["totals"])

    def test_rejects_scored_rubric_tampering_and_total_disagreement(self):
        with tempfile.TemporaryDirectory() as tmp:
            sealed, decisions, output = self.completed_fixture(Path(tmp))
            scored_path = output / "rubrics-model-a.json"
            scored = json.loads(scored_path.read_text(encoding="utf-8"))
            scored[0]["score"] = 0
            write_json(scored_path, scored)

            report = verify_final_artifacts(sealed, decisions, output)

            self.assertEqual("fail", report["result"])
            codes = {item["code"] for item in report["errors"]}
            self.assertIn("SCORED_DECISION_MISMATCH", codes)
            self.assertIn("TOTAL_MISMATCH", codes)

    def test_rejects_missing_audit_and_wrong_evidence_mapping(self):
        with tempfile.TemporaryDirectory() as tmp:
            sealed, decisions, output = self.completed_fixture(Path(tmp))
            (output / "decision-audit.json").unlink()
            report = verify_final_artifacts(sealed, decisions, output)
            self.assertIn("AUDIT_MISSING", {item["code"] for item in report["errors"]})

        with tempfile.TemporaryDirectory() as tmp:
            sealed, decisions, output = self.completed_fixture(Path(tmp))
            audit_path = output / "decision-audit.json"
            audit = json.loads(audit_path.read_text(encoding="utf-8"))
            audit["models"]["model-a"]["R1-01"]["evidence_ids"] = ["EV-not-in-row"]
            write_json(audit_path, audit)

            report = verify_final_artifacts(sealed, decisions, output)

            codes = {item["code"] for item in report["errors"]}
            self.assertIn("AUDIT_DECISION_MISMATCH", codes)
            self.assertIn("UNKNOWN_EVIDENCE_REFERENCE", codes)

    def test_rejects_decision_reference_from_another_rubric(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sealed, decisions, output = self.completed_fixture(root)
            payload = json.loads(decisions.read_text(encoding="utf-8"))
            payload["rubric_scores"]["model-a"]["R1-01"]["evidence_ids"] = ["EV-other-rubric"]
            write_json(decisions, payload)

            report = verify_final_artifacts(sealed, decisions, output)

            self.assertIn("UNKNOWN_EVIDENCE_REFERENCE", {item["code"] for item in report["errors"]})

    def test_rejects_summary_path_outside_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            sealed, decisions, output = self.completed_fixture(Path(tmp))
            summary_path = output / "scoring-summary.json"
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            summary["files"]["model-a"] = str(output.parent / "elsewhere.json")
            write_json(summary_path, summary)

            report = verify_final_artifacts(sealed, decisions, output)

            self.assertIn("UNSAFE_SUMMARY_PATH", {item["code"] for item in report["errors"]})

    def test_rejects_jointly_forged_decided_by(self):
        with tempfile.TemporaryDirectory() as tmp:
            sealed, decisions, output = self.completed_fixture(Path(tmp))
            payload = json.loads(decisions.read_text(encoding="utf-8"))
            payload["rubric_scores"]["model-a"]["R1-01"]["decided_by"] = "forged"
            write_json(decisions, payload)
            audit_path = output / "decision-audit.json"
            audit = json.loads(audit_path.read_text(encoding="utf-8"))
            audit["models"]["model-a"]["R1-01"]["decided_by"] = "forged"
            write_json(audit_path, audit)
            codes = {item["code"] for item in verify_final_artifacts(sealed, decisions, output)["errors"]}
            self.assertIn("DECISION_POLICY_INVALID", codes)


if __name__ == "__main__":
    unittest.main()
