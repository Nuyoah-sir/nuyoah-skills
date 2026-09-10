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


class FinalizeScoresTests(unittest.TestCase):
    def sealed(self, root):
        bundle = make_bundle(root)
        archive = root / "sealed.zip"
        package_bundle(bundle, archive)
        return safe_extract_zip(archive, root / "sealed")

    def test_preserves_baseline_fields_and_adds_only_score_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = self.sealed(root)
            decisions = root / "human-decisions.json"
            write_json(decisions, {"schema_version": "1.0.0", "base_package_id": "pkg-001", "rubric_scores": {"model-a": {"R1-01": {"score": 1, "reason": "点击处理器已绑定（app.js:1）", "evidence_ids": ["EV-model-a-R1-01-001"], "decided_by": "human"}}}})
            result = finalize_scores(bundle, decisions, root / "out")
            scored = json.loads(Path(result["files"]["model-a"]).read_text(encoding="utf-8"))
            self.assertEqual({"id", "round", "criterion", "score", "reason"}, set(scored[0]))
            self.assertEqual(1, scored[0]["score"])

    def test_rejects_missing_model_rubric_decision(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = self.sealed(root)
            decisions = root / "human-decisions.json"
            write_json(decisions, {"schema_version": "1.0.0", "base_package_id": "pkg-001", "rubric_scores": {"model-a": {}}})
            with self.assertRaises(ValueError):
                finalize_scores(bundle, decisions, root / "out")

    def test_rejects_unknown_evidence_reference_and_wrong_package(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = self.sealed(root)
            decisions = root / "human-decisions.json"
            write_json(decisions, {"schema_version": "1.0.0", "base_package_id": "wrong", "rubric_scores": {"model-a": {"R1-01": {"score": 0, "reason": "x", "evidence_ids": ["missing"], "decided_by": "human"}}}})
            with self.assertRaises(ValueError):
                finalize_scores(bundle, decisions, root / "out")

    def test_accepts_local_human_observation_for_same_rubric(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw = make_bundle(root, "ready_for_local_review")
            row_path = raw / "models/model-a/rubric-evidence.jsonl"
            row = json.loads(row_path.read_text(encoding="utf-8"))
            row["human_check_needed"] = True
            row_path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
            archive = root / "sealed.zip"
            package_bundle(raw, archive)
            bundle = safe_extract_zip(archive, root / "sealed")
            decisions = root / "human-decisions.json"
            write_json(decisions, {
                "schema_version": "1.0.0",
                "base_package_id": "pkg-001",
                "local_evidence": [{"evidence_id": "LOCAL-model-a-R1-01-001", "model_id": "model-a", "rubric_id": "R1-01", "type": "human_note", "observer": "评测人", "observed_at": "2026-09-09T12:00:00+08:00", "steps": ["点击按钮"], "expected_result": "按钮触发运行", "observed_result": "按钮触发运行", "observed_version": "pkg-001"}],
                "rubric_scores": {"model-a": {"R1-01": {"score": 1, "reason": "人工点击可触发运行（本机实测）", "evidence_ids": ["LOCAL-model-a-R1-01-001"], "decided_by": "human"}}},
            })
            result = finalize_scores(bundle, decisions, root / "out")
            self.assertTrue((Path(result["audit"]).parent / "local-human-evidence.jsonl").is_file())

    def test_rejects_accepted_machine_when_human_check_is_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw = make_bundle(root, "ready_for_local_review")
            row_path = raw / "models/model-a/rubric-evidence.jsonl"
            row = json.loads(row_path.read_text(encoding="utf-8"))
            row["human_check_needed"] = True
            row_path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
            archive = root / "sealed.zip"
            package_bundle(raw, archive)
            bundle = safe_extract_zip(archive, root / "sealed")
            decisions = root / "human-decisions.json"
            write_json(decisions, {"schema_version": "1.0.0", "base_package_id": "pkg-001", "rubric_scores": {"model-a": {"R1-01": {"score": 1, "reason": "直接接受机器建议", "evidence_ids": ["EV-model-a-R1-01-001"], "decided_by": "accepted_machine"}}}})
            with self.assertRaises(ValueError):
                finalize_scores(bundle, decisions, root / "out")
            self.assertFalse((root / "out").exists())

    def test_adjudication_resolution_score_must_match_final_decision(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw = make_bundle(root, "ready_for_local_review")
            evidence_path = raw / "models/model-a/rubric-evidence.jsonl"
            row = json.loads(evidence_path.read_text(encoding="utf-8"))
            row["adjudication_ids"] = ["ADJ-001"]
            evidence_path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
            write_json(raw / "review/pending-adjudications.json", {"items": [{"adjudication_id": "ADJ-001", "status": "pending", "rubric_ids": ["R1-01"], "question": "按静态还是行为？", "ambiguity": "rubric 未明确静态实现是否足够。", "evidence_ids": ["EV-model-a-R1-01-001"], "applies_to_all_models": True, "recommended_policy": "按静态实现判。", "alternative_policy": "必须实测后再判。", "impact": "影响 R1-01 的最终分。", "resolution": None}]})
            archive = root / "sealed.zip"
            package_bundle(raw, archive)
            bundle = safe_extract_zip(archive, root / "sealed")
            decisions = root / "decisions.json"
            payload = {"schema_version": "1.0.0", "base_package_id": "pkg-001", "adjudication_resolutions": [{"adjudication_id": "ADJ-001", "decided_by": "human", "decider": "评测人", "decided_at": "2026-09-10T10:00:00+08:00", "decision_source": "用户确认采用静态充分口径", "final_policy": "采用静态充分口径", "per_model_final_scores": {"model-a": {"R1-01": 0}}}], "rubric_scores": {"model-a": {"R1-01": {"score": 1, "reason": "静态绑定成立", "evidence_ids": ["EV-model-a-R1-01-001"], "decided_by": "human"}}}}
            write_json(decisions, payload)
            with self.assertRaises(ValueError):
                finalize_scores(bundle, decisions, root / "bad-out")
            payload["adjudication_resolutions"][0]["per_model_final_scores"]["model-a"]["R1-01"] = 1
            write_json(decisions, payload)
            result = finalize_scores(bundle, decisions, root / "good-out")
            self.assertEqual(1, result["model_count"])

    def test_material_gap_requires_explicit_human_resolution(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw = make_bundle(root, "ready_for_local_review")
            manifest_path = raw / "MANIFEST.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["missing_materials"] = ["record_txt_missing"]
            write_json(manifest_path, manifest)
            archive = root / "sealed.zip"
            package_bundle(raw, archive)
            bundle = safe_extract_zip(archive, root / "sealed")
            decisions = root / "decisions.json"
            payload = {"schema_version": "1.0.0", "base_package_id": "pkg-001", "rubric_scores": {"model-a": {"R1-01": {"score": 1, "reason": "静态证据成立", "evidence_ids": ["EV-model-a-R1-01-001"], "decided_by": "accepted_machine"}}}}
            write_json(decisions, payload)
            with self.assertRaises(ValueError):
                finalize_scores(bundle, decisions, root / "bad")
            payload["material_gap_resolutions"] = [{"gap_id": "record_txt_missing", "decision": "proceed_with_limitation", "reason": "该记录不影响按钮静态条目，表单中保留限制。", "decided_by": "human", "decider": "评测人", "decided_at": "2026-09-10T10:00:00+08:00"}]
            write_json(decisions, payload)
            self.assertEqual(1, finalize_scores(bundle, decisions, root / "good")["model_count"])


if __name__ == "__main__":
    unittest.main()
