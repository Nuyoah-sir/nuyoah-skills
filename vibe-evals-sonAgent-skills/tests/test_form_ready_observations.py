import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "shared" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from record_observation import sha256_text
from validate_form_ready import validate_form_ready
from tests.v2_fixtures import make_form_ready_workspace, write_json

TARGET_BLOB = "a" * 64
RENDER_BLOB = "b" * 64


class FormReadyObservationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def _prepare(self, *, complete=True, closed=True):
        fixture = make_form_ready_workspace(self.root / "fixture", complete=complete, closed=closed)
        outer = fixture.outer
        manifest = json.loads((outer / "FORM-READY.json").read_text(encoding="utf-8"))
        binding = {
            "outer_package_id": manifest["outer_package_id"],
            "base_package_id": fixture.inner_package_id,
            "base_zip_sha256": manifest["base"]["sha256"],
            "source_input_digest": fixture.source_digest,
            "task_id": manifest["base"]["package_id"] and "示例题",
        }
        index = json.loads((outer / "observations/media-index.json").read_text(encoding="utf-8"))
        for media_id in ("MEDIA-target", "RENDER-candidate"):
            self.assertIn(media_id, index["uses"], "the closed fixture must register both review media ids")
        return fixture, manifest, binding

    def _envelope(self, fixture, manifest):
        rubric_index = json.loads(
            (fixture.sealed_inner_zip.parents[2] / "inspected-inner" / "inputs" / "rubric-index.json").read_text(encoding="utf-8")
        )
        rubric = next(row for row in rubric_index["rubrics"] if row["id"] == "R1-01")
        return {
            "outer_package_id": manifest["outer_package_id"],
            "base_package_id": fixture.inner_package_id,
            "base_zip_sha256": manifest["base"]["sha256"],
            "source_input_digest": fixture.source_digest,
            "task_id": "示例题",
            "model_id": "model-a",
            "rubric_id": "R1-01",
            "round": 1,
            "criterion_sha256": rubric["criterion_sha256"],
        }

    def _vision(self, fixture, manifest, read_index, **overrides):
        question = overrides.pop("question", f"Read {read_index}: does the frozen target show the button? ")
        raw_response = overrides.pop("raw_response", f"Observation {read_index}: the button is present in the render.")
        fact = overrides.pop("fact", f"the button is present in the render (read {read_index})")
        record = {
            **self._envelope(fixture, manifest),
            "observation_id": f"VIS-model-a-R1-01-0{read_index}",
            "type": "machine_vision",
            "read_index": read_index,
            "invocation_id": f"provider-request-{read_index}",
            "session_id": f"isolated-read-{read_index}",
            "independent_context": True,
            "input_media_ids": ["MEDIA-target", "RENDER-candidate"],
            "tool": "vision-bridge",
            "model": "vision-model-id",
            "tool_version": "1.0.0",
            "question": question,
            "prompt_sha256": sha256_text(question),
            "raw_response": raw_response,
            "raw_response_sha256": sha256_text(raw_response),
            "fact": fact,
            "verdict": 1,
            "confidence": "high",
            "conflict": False,
            "observed_at": "2026-09-10T10:00:00+08:00",
        }
        record.update(overrides)
        record["prompt_sha256"] = sha256_text(record["question"]) if "prompt_sha256" not in overrides else record["prompt_sha256"]
        record["raw_response_sha256"] = sha256_text(record["raw_response"]) if "raw_response_sha256" not in overrides else record["raw_response_sha256"]
        return record

    def _human(self, fixture, manifest, **overrides):
        record = {
            **self._envelope(fixture, manifest),
            "observation_id": "HUM-model-a-R1-01-01",
            "type": "remote_human",
            "observer": "operator-01",
            "recorded_by": "remote-agent-01",
            "capture_method": "interactive_terminal",
            "observed_at": "2026-09-10T10:00:00+08:00",
            "steps": ["open the verified full render", "compare against the frozen target"],
            "expected_result": "the button is present",
            "observed_result": "the button is visible in the centre of the render",
            "observed_version": f"{fixture.inner_package_id} / RENDER-candidate",
            "confirmation_text": "是的，我看到了按钮，就在图中央。",
            "media_ids": ["MEDIA-target", "RENDER-candidate"],
            "verdict": 1,
        }
        record.update(overrides)
        return record

    def _write_vision(self, fixture, rows):
        path = fixture.outer / "observations/machine-vision.jsonl"
        path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")

    def _write_human(self, fixture, rows):
        path = fixture.outer / "observations/remote-human.jsonl"
        path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")

    def _codes(self, outer, *, require_seal=False):
        report = validate_form_ready(outer, require_seal=require_seal)
        return {row["code"] for row in report["errors"]}, report

    def test_machine_vision_accepts_two_consistent_independent_reads_with_same_media(self):
        fixture, manifest, _ = self._prepare()
        self._write_vision(fixture, [self._vision(fixture, manifest, 1), self._vision(fixture, manifest, 2)])
        codes, report = self._codes(fixture.outer)
        self.assertEqual(set(), codes)
        self.assertEqual("pass", report["result"])

    def test_machine_vision_rejects_single_read(self):
        fixture, manifest, _ = self._prepare()
        self._write_vision(fixture, [self._vision(fixture, manifest, 1)])
        codes, _ = self._codes(fixture.outer)
        self.assertIn("VISION_READ_COUNT", codes)

    def test_machine_vision_rejects_identical_prompts_as_non_independent(self):
        fixture, manifest, _ = self._prepare()
        shared = "Does the frozen target show the button? "
        self._write_vision(fixture, [
            self._vision(fixture, manifest, 1, question=shared),
            self._vision(fixture, manifest, 2, question=shared),
        ])
        codes, _ = self._codes(fixture.outer)
        self.assertIn("VISION_READ_NOT_INDEPENDENT", codes)

    def test_machine_vision_rejects_same_invocation_or_session_id(self):
        fixture, manifest, _ = self._prepare()
        for field, value in (("invocation_id", "provider-request-1"), ("session_id", "isolated-read-1")):
            with self.subTest(field=field):
                self._write_vision(fixture, [
                    self._vision(fixture, manifest, 1),
                    self._vision(fixture, manifest, 2, **{field: value}),
                ])
                codes, _ = self._codes(fixture.outer)
                self.assertIn("VISION_READ_NOT_INDEPENDENT", codes)

    def test_machine_vision_conflict_or_low_confidence_requires_remote_human(self):
        fixture, manifest, _ = self._prepare()
        self._write_vision(fixture, [self._vision(fixture, manifest, 1), self._vision(fixture, manifest, 2, verdict=0)])
        codes, _ = self._codes(fixture.outer)
        self.assertIn("VISION_CONFLICT", codes)
        self._write_vision(fixture, [self._vision(fixture, manifest, 1), self._vision(fixture, manifest, 2, confidence="low")])
        codes, _ = self._codes(fixture.outer)
        self.assertIn("VISION_LOW_CONFIDENCE", codes)

    def test_machine_vision_requires_raw_answer_specific_fact_and_target_candidate_media(self):
        fixture, manifest, _ = self._prepare()
        self._write_vision(fixture, [self._vision(fixture, manifest, 1, raw_response="", raw_response_sha256=sha256_text("")), self._vision(fixture, manifest, 2)])
        codes, _ = self._codes(fixture.outer)
        self.assertIn("VISION_RECORD_INCOMPLETE", codes)
        self._write_vision(fixture, [self._vision(fixture, manifest, 1, input_media_ids=["MEDIA-target"]), self._vision(fixture, manifest, 2, input_media_ids=["MEDIA-target"])])
        codes, _ = self._codes(fixture.outer)
        self.assertIn("VISION_RECORD_INCOMPLETE", codes)
        self._write_vision(fixture, [self._vision(fixture, manifest, 1, input_media_ids=["MEDIA-unknown", "RENDER-candidate"]), self._vision(fixture, manifest, 2)])
        codes, _ = self._codes(fixture.outer)
        self.assertIn("VISION_RECORD_INCOMPLETE", codes)

    def test_machine_vision_cannot_close_subjective_or_policy_ambiguous_rubric(self):
        fixture, manifest, _ = self._prepare()
        self._write_vision(fixture, [self._vision(fixture, manifest, 1), self._vision(fixture, manifest, 2)])
        path = fixture.outer / "decisions/criterion-classifications.json"
        document = json.loads(path.read_text(encoding="utf-8"))
        document["records"][0]["classification"] = "subjective_or_policy"
        document["records"][0]["requires_remote_human"] = True
        write_json(path, document)
        codes, _ = self._codes(fixture.outer)
        self.assertIn("VISION_POLICY_REQUIRES_HUMAN", codes)

    def test_machine_vision_static_evidence_conflict_requires_remote_human(self):
        fixture, manifest, _ = self._prepare()
        self._write_vision(fixture, [self._vision(fixture, manifest, 1, verdict=0), self._vision(fixture, manifest, 2, verdict=0)])
        codes, _ = self._codes(fixture.outer)
        self.assertIn("VISION_STATIC_CONFLICT", codes)

    def test_human_attestation_requires_every_provenance_field(self):
        fixture, manifest, _ = self._prepare()
        self._write_human(fixture, [self._human(fixture, manifest)])
        codes, report = self._codes(fixture.outer)
        self.assertEqual(set(), codes, report["errors"])
        for field in ("observer", "recorded_by", "capture_method", "steps", "expected_result", "observed_result", "observed_version", "confirmation_text", "media_ids"):
            with self.subTest(field=field):
                record = self._human(fixture, manifest)
                record.pop(field)
                self._write_human(fixture, [record])
                codes, _ = self._codes(fixture.outer)
                self.assertIn("HUMAN_RECORD_INCOMPLETE", codes)

    def test_human_attestation_rejects_defaulted_confirmation_text(self):
        fixture, manifest, _ = self._prepare()
        for value in ("", "y", "确认"):
            with self.subTest(value=value):
                self._write_human(fixture, [self._human(fixture, manifest, confirmation_text=value)])
                codes, _ = self._codes(fixture.outer)
                self.assertIn("HUMAN_RECORD_INCOMPLETE", codes)

    def test_human_attestation_must_name_the_observed_version_and_registered_media(self):
        fixture, manifest, _ = self._prepare()
        self._write_human(fixture, [self._human(fixture, manifest, observed_version=fixture.inner_package_id)])
        codes, _ = self._codes(fixture.outer)
        self.assertIn("HUMAN_RECORD_INCOMPLETE", codes)
        self._write_human(fixture, [self._human(fixture, manifest, media_ids=["MEDIA-target", "RENDER-missing"])])
        codes, _ = self._codes(fixture.outer)
        self.assertIn("HUMAN_RECORD_INCOMPLETE", codes)

    def test_non_human_record_cannot_claim_human_confirmation(self):
        fixture, manifest, _ = self._prepare()
        self._write_vision(fixture, [
            self._vision(fixture, manifest, 1, fact="人工确认按钮存在"),
            self._vision(fixture, manifest, 2),
        ])
        codes, _ = self._codes(fixture.outer)
        self.assertIn("HUMAN_CLAIM_FOR_NON_HUMAN", codes)

    def test_criterion_classification_policy(self):
        fixture, manifest, _ = self._prepare()
        path = fixture.outer / "decisions/criterion-classifications.json"
        original = json.loads(path.read_text(encoding="utf-8"))
        cases = {
            "unknown category defaults to human": ({"classification": "guessed"}, "CRITERION_CLASS_REQUIRES_HUMAN"),
            "objective class must quote the criterion verbatim": ({"measurable_phrase": "按钮漂亮"}, "CRITERION_CLASS_INVALID"),
            "objective class cannot require a human": ({"requires_remote_human": True}, "CRITERION_CLASS_INVALID"),
        }
        for label, (patch, expected) in cases.items():
            with self.subTest(case=label):
                document = json.loads(json.dumps(original))
                document["records"][0].update(patch)
                write_json(path, document)
                codes, _ = self._codes(fixture.outer)
                self.assertIn(expected, codes)
        document = json.loads(json.dumps(original))
        document["records"][0]["classification"] = "subjective_or_policy"
        document["records"][0]["requires_remote_human"] = False
        write_json(path, document)
        codes, _ = self._codes(fixture.outer)
        self.assertIn("CRITERION_CLASS_REQUIRES_HUMAN", codes)

    def test_record_observation_cli_refuses_piped_human_confirmation(self):
        fixture, manifest, _ = self._prepare()
        payload = self._human(fixture, manifest)
        payload.pop("confirmation_text")
        record_path = self.root / "human.json"
        write_json(record_path, payload)
        result = subprocess.run(
            [sys.executable, str(SCRIPTS / "record_observation.py"), "human", str(fixture.outer), "--record", str(record_path)],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, check=False,
        )
        self.assertNotEqual(0, result.returncode)
        self.assertIn("HUMAN_CONFIRMATION_NOT_INTERACTIVE", result.stderr)
        self.assertFalse((fixture.outer / "observations/remote-human.jsonl").read_text(encoding="utf-8").strip())

    def test_vision_cli_appends_only_after_validation_payload_is_provided(self):
        fixture, manifest, _ = self._prepare()
        (fixture.outer / "observations/machine-vision.jsonl").write_text("", encoding="utf-8")
        record_path = self.root / "vision.json"
        write_json(record_path, self._vision(fixture, manifest, 1))
        result = subprocess.run(
            [sys.executable, str(SCRIPTS / "record_observation.py"), "vision", str(fixture.outer), "--record", str(record_path)],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, check=False,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        rows = (fixture.outer / "observations/machine-vision.jsonl").read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(1, len(rows))
        self.assertEqual("VIS-model-a-R1-01-01", json.loads(rows[0])["observation_id"])


if __name__ == "__main__":
    unittest.main()
