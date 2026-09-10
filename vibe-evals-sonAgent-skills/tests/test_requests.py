import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

from validate_evidence_requests import validate_requests


class EvidenceRequestTests(unittest.TestCase):
    def test_accepts_request_scoped_to_known_model_and_rubric(self):
        value = {"schema_version": "1.0.0", "base_package_id": "pkg-1", "requests": [{"request_id": "REQ-001", "model_id": "a", "rubric_id": "R1-01", "need": "补充连续源码摘录", "allowed_methods": ["static_line"]}]}
        report = validate_requests(value, {"a"}, {"R1-01"})
        self.assertEqual([], report["errors"])

    def test_rejects_unknown_targets_duplicate_ids_and_empty_need(self):
        value = {"schema_version": "1.0.0", "base_package_id": "pkg-1", "requests": [
            {"request_id": "REQ-001", "model_id": "missing", "rubric_id": "R9-99", "need": "", "allowed_methods": []},
            {"request_id": "REQ-001", "model_id": "a", "rubric_id": "R1-01", "need": "x", "allowed_methods": ["guess"]},
        ]}
        report = validate_requests(value, {"a"}, {"R1-01"})
        codes = {item["code"] for item in report["errors"]}
        self.assertTrue({"REQUEST_ID_DUPLICATE", "MODEL_UNKNOWN", "RUBRIC_UNKNOWN", "NEED_EMPTY", "METHOD_INVALID"}.issubset(codes))

    def test_rejects_wrong_base_package(self):
        value = {"schema_version": "1.0.0", "base_package_id": "other", "requests": []}
        report = validate_requests(value, set(), set(), expected_package_id="pkg-1")
        self.assertIn("BASE_PACKAGE_MISMATCH", {item["code"] for item in report["errors"]})


if __name__ == "__main__":
    unittest.main()
