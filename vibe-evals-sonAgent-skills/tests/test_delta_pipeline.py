import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

from merge_delta import merge_delta
from initialize_delta import initialize_delta
from package_bundle import package_bundle, safe_extract_zip
from package_delta import package_delta
from prepare_local_review import prepare_local_review
from validate_delta import validate_delta
from tests.test_validate_bundle import APP_BLOB, APP_DIGEST, make_bundle, set_source_rubric_round, write_json


class DeltaPipelineTests(unittest.TestCase):
    def test_initializer_freezes_request_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = make_bundle(root)
            base_zip = root / "base.zip"
            package_bundle(bundle, base_zip)
            base = safe_extract_zip(base_zip, root / "base")
            requests = root / "requests.json"
            write_json(requests, {"schema_version": "1.0.0", "base_package_id": "pkg-001", "requests": [{"request_id": "REQ-001", "model_id": "model-a", "rubric_id": "R1-01", "need": "补充点击行为证据", "allowed_methods": ["static_line"]}]})
            delta = initialize_delta(base, requests, root / "delta-work")
            meta = json.loads((delta / "DELTA.json").read_text(encoding="utf-8"))
            self.assertEqual(["REQ-001"], meta["request_ids"])
            self.assertEqual(requests.read_bytes(), (delta / "request-copy.json").read_bytes())
            with self.assertRaises(FileExistsError):
                initialize_delta(base, requests, delta)

    def test_delta_evidence_enters_canonical_registry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = make_bundle(root, "ready_for_local_review")
            set_source_rubric_round(bundle, "R1")
            manifest = json.loads((bundle / "MANIFEST.json").read_text(encoding="utf-8"))
            evidence_path = bundle / "models/model-a/rubric-evidence.jsonl"
            row = json.loads(evidence_path.read_text(encoding="utf-8"))
            row.update({"suggested_score": None, "reason_code": "insufficient_evidence", "coverage": "partial", "confidence": "low", "evidence": []})
            evidence_path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
            base_zip = root / "base.zip"
            package_bundle(bundle, base_zip)
            base = safe_extract_zip(base_zip, root / "base")
            base_digest = hashlib.sha256((base / "integrity/files.sha256").read_bytes()).hexdigest()

            delta = root / "delta-src"
            request_value = {"schema_version": "1.0.0", "base_package_id": "pkg-001", "requests": [{"request_id": "REQ-001", "model_id": "model-a", "rubric_id": "R1-01", "need": "补充点击处理器静态证据", "allowed_methods": ["static_line"]}]}
            write_json(delta / "request-copy.json", request_value)
            request_digest = hashlib.sha256((delta / "request-copy.json").read_bytes()).hexdigest()
            write_json(delta / "DELTA.json", {"schema": "vibe-evals-evidence-delta", "schema_version": "1.0.0", "delta_id": "delta-001", "base_package_id": "pkg-001", "base_digest": base_digest, "request_sha256": request_digest, "source_input_digest": manifest["source_input_digest"], "request_ids": ["REQ-001"], "response_count": 1})
            new_evidence = {"evidence_id": "EV-model-a-R1-01-NEW", "type": "static_line", "direction": "support", "source_state": "round_end", "round": 1, "path": "app.js", "line_start": 1, "line_end": 1, "file_sha256": APP_DIGEST, "source_blob_path": APP_BLOB, "excerpt": "button.onclick = run;", "fact": "补证确认点击处理器。"}
            response = {"request_id": "REQ-001", "model_id": "model-a", "rubric_id": "R1-01", "status": "fulfilled", "reason_code": "evidence_collected", "new_evidence": [new_evidence], "proposed_update": {"suggested_score": 1, "reason_code": "implemented_static_only", "coverage": "complete", "confidence": "medium", "fact_summary": "补证确认点击处理器。", "limitations": []}}
            delta.mkdir(parents=True, exist_ok=True)
            (delta / "responses.jsonl").write_text(json.dumps(response, ensure_ascii=False) + "\n", encoding="utf-8")
            bad = json.loads(json.dumps(response))
            bad["new_evidence"][0] = {"evidence_id": "EV-NOT-ALLOWED", "type": "inventory_fact", "direction": "support", "metric": "files", "value": 1, "basis": "inventory", "fact": "one file"}
            (delta / "responses.jsonl").write_text(json.dumps(bad, ensure_ascii=False) + "\n", encoding="utf-8")
            bad_report = validate_delta(delta, base, delta / "request-copy.json", require_seal=False)
            self.assertIn("METHOD_NOT_ALLOWED", {item["code"] for item in bad_report["errors"]})
            (delta / "responses.jsonl").write_text(json.dumps(response, ensure_ascii=False) + "\n", encoding="utf-8")
            extra_blob = delta / "evidence/source-blobs/unreferenced.txt"
            extra_blob.parent.mkdir(parents=True)
            extra_blob.write_text("not referenced", encoding="utf-8")
            blob_report = validate_delta(delta, base, delta / "request-copy.json", require_seal=False)
            self.assertIn("SOURCE_BLOB_COVERAGE", {item["code"] for item in blob_report["errors"]})
            extra_blob.unlink()
            delta_zip = root / "delta.zip"
            package_delta(delta, base, delta / "request-copy.json", delta_zip)
            sealed_delta = safe_extract_zip(delta_zip, root / "delta")
            merged = merge_delta(base, sealed_delta, delta / "request-copy.json", root / "merged")
            merged_row = json.loads((merged / "models/model-a/rubric-evidence.jsonl").read_text(encoding="utf-8"))
            self.assertEqual("EV-model-a-R1-01-NEW", merged_row["evidence"][0]["evidence_id"])
            self.assertEqual(1, merged_row["suggested_score"])

    def test_probe_delta_merges_run_index_logs_and_revalidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = make_bundle(root, "ready_for_local_review")
            evidence_path = bundle / "models/model-a/rubric-evidence.jsonl"
            row = json.loads(evidence_path.read_text(encoding="utf-8"))
            row.update({"suggested_score": None, "reason_code": "unsafe_to_test", "coverage": "unsafe_to_test", "confidence": "low", "evidence": []})
            evidence_path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
            base_zip = root / "base.zip"
            package_bundle(bundle, base_zip)
            base = safe_extract_zip(base_zip, root / "base")
            base_digest = hashlib.sha256((base / "integrity/files.sha256").read_bytes()).hexdigest()
            manifest = json.loads((base / "MANIFEST.json").read_text(encoding="utf-8"))

            delta = root / "delta-src"
            request_value = {"schema_version": "1.0.0", "base_package_id": "pkg-001", "requests": [{"request_id": "REQ-002", "model_id": "model-a", "rubric_id": "R1-01", "need": "隔离运行点击探针", "allowed_methods": ["probe_run"]}]}
            write_json(delta / "request-copy.json", request_value)
            request_digest = hashlib.sha256((delta / "request-copy.json").read_bytes()).hexdigest()
            write_json(delta / "DELTA.json", {"schema": "vibe-evals-evidence-delta", "schema_version": "1.0.0", "delta_id": "delta-002", "base_package_id": "pkg-001", "base_digest": base_digest, "request_sha256": request_digest, "source_input_digest": manifest["source_input_digest"], "request_ids": ["REQ-002"], "response_count": 1})
            probe_root = delta / "models/model-a/probes"
            probe_root.mkdir(parents=True)
            (probe_root / "stdout.log").write_text("PASS\n", encoding="utf-8")
            (probe_root / "stderr.log").write_text("", encoding="utf-8")
            (probe_root / "probe.js").write_text("process.exit(0);\n", encoding="utf-8")
            script_digest = hashlib.sha256((probe_root / "probe.js").read_bytes()).hexdigest()
            source_digest = hashlib.sha256((APP_DIGEST + "  app.js").encode("utf-8")).hexdigest()
            safety = {key: True for key in ("model_code_treated_as_untrusted", "isolated", "network_disabled", "credentials_absent", "disposable_copy", "minimal_permissions")}
            write_json(probe_root / "index.json", {"runs": [{"run_id": "PR-002", "status": "passed", "argv": ["node", "probe.js"], "script_path": "probes/probe.js", "script_sha256": script_digest, "exit_code": 0, "duration_ms": 12, "assertions": [{"rubric_id": "R1-01", "expected": "click succeeds", "actual": "click succeeds", "passed": True}], "stdout_path": "probes/stdout.log", "stderr_path": "probes/stderr.log", "safety": safety, "source_state": "round_end", "round": 1, "source_inventory_digest": source_digest}]})
            response = {"request_id": "REQ-002", "model_id": "model-a", "rubric_id": "R1-01", "status": "fulfilled", "reason_code": "evidence_collected", "new_evidence": [{"evidence_id": "EV-PROBE-002", "type": "probe_run", "direction": "support", "run_id": "PR-002", "fact": "隔离探针通过点击场景。"}], "proposed_update": {"suggested_score": 1, "reason_code": "implemented_and_verified", "coverage": "complete", "confidence": "high", "fact_summary": "隔离探针通过。", "limitations": []}}
            (delta / "responses.jsonl").write_text(json.dumps(response, ensure_ascii=False) + "\n", encoding="utf-8")
            delta_zip = root / "delta.zip"
            package_delta(delta, base, delta / "request-copy.json", delta_zip)
            sealed_delta = safe_extract_zip(delta_zip, root / "delta")
            merged = merge_delta(base, sealed_delta, delta / "request-copy.json", root / "merged")
            self.assertTrue((merged / "models/model-a/probes/stdout.log").is_file())
            merged_index = json.loads((merged / "models/model-a/probes/index.json").read_text(encoding="utf-8"))
            self.assertEqual("PR-002", merged_index["runs"][0]["run_id"])

    def test_rejects_rewritten_request_and_nonfulfilled_gate_update(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = make_bundle(root, "ready_for_local_review")
            row_path = bundle / "models/model-a/rubric-evidence.jsonl"
            row = json.loads(row_path.read_text(encoding="utf-8"))
            row["human_check_needed"] = True
            row_path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
            archive = root / "base.zip"
            package_bundle(bundle, archive)
            base = safe_extract_zip(archive, root / "base")
            original = root / "original-requests.json"
            write_json(original, {"schema_version": "1.0.0", "base_package_id": "pkg-001", "requests": [{"request_id": "REQ-009", "model_id": "model-a", "rubric_id": "R1-01", "need": "补证", "allowed_methods": ["static_line"]}]})
            delta = initialize_delta(base, original, root / "delta")
            rewritten = json.loads((delta / "request-copy.json").read_text(encoding="utf-8"))
            rewritten["requests"][0]["allowed_methods"] = ["probe_run"]
            write_json(delta / "request-copy.json", rewritten)
            meta = json.loads((delta / "DELTA.json").read_text(encoding="utf-8"))
            meta["request_sha256"] = hashlib.sha256((delta / "request-copy.json").read_bytes()).hexdigest()
            write_json(delta / "DELTA.json", meta)
            response = {"request_id": "REQ-009", "model_id": "model-a", "rubric_id": "R1-01", "status": "unresolved", "reason_code": "no_isolation", "new_evidence": [], "proposed_update": {"human_check_needed": False}}
            (delta / "responses.jsonl").write_text(json.dumps(response, ensure_ascii=False) + "\n", encoding="utf-8")
            report = validate_delta(delta, base, original, require_seal=False)
            codes = {item["code"] for item in report["errors"]}
            self.assertIn("REQUEST_ORIGINAL_MISMATCH", codes)
            self.assertIn("NONFULFILLED_UPDATE_FORBIDDEN", codes)
            self.assertIn("REMOTE_GATE_UPDATE_FORBIDDEN", codes)


if __name__ == "__main__":
    unittest.main()
