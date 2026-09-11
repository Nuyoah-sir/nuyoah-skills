import json
import contextlib
import hashlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

from tests.v2_fixtures import FIXTURE_PNG

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


PASSIVE_SVG = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<svg xmlns="http://www.w3.org/2000/svg" width="40" height="20" viewBox="0 0 40 20">\n'
    '  <rect id="button" x="2" y="2" width="36" height="16" fill="#2563EB"/>\n'
    "</svg>\n"
)


class V2CompleteEvalEndToEndTests(unittest.TestCase):
    """Drive the real runner from a raw task package instead of a prebuilt fixture."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.task = self._task_package()
        self.out = self.root / "runs"
        self.run = self.out / "E2E"

    def _task_package(self) -> Path:
        root = self.root / "task"
        root.mkdir(parents=True)
        (root / "prompt.md").write_text("\u7b2c\u4e00\u8f6e\uff1a\u5b9e\u73b0\u6309\u94ae\uff0c\u5e76\u8ba9\u6309\u94ae\u4e3a\u84dd\u8272\u3002\n", encoding="utf-8")
        (root / "rubrics1.json").write_text(json.dumps([
            {"id": "R1-01", "round": 1, "criterion": "\u6309\u94ae\u53ef\u70b9\u51fb"},
            {"id": "R1-02", "round": 1, "criterion": "\u6309\u94ae\u4e3a\u84dd\u8272"},
        ], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        model = root / "\u9898-\u6a21\u578b\u8f93\u51fa" / "GLM5.2"
        model.mkdir(parents=True)
        # The frozen target lives with the model output so discovery inventories it;
        # its role is decided by name, not by location.
        (model / "target.png").write_bytes(FIXTURE_PNG)
        (model / "app.js").write_text("button.onclick = run;\n", encoding="utf-8")
        (model / "fixed.svg").write_text(PASSIVE_SVG, encoding="utf-8")
        return root

    def _cli(self, *argv) -> tuple[int, str]:
        from complete_eval import main

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
            code = main(list(argv))
        return code, buffer.getvalue()

    def _state(self) -> dict:
        return json.loads((self.run / "run-state.json").read_text(encoding="utf-8"))

    def _write(self, name: str, value) -> Path:
        path = self.root / "candidates" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2) + "\n"
        path.write_text(text, encoding="utf-8")
        return path

    def test_remote_chain_from_raw_package_reaches_closed_decisions(self):
        code, output = self._cli("start", "--task-root", str(self.task), "--output-root", str(self.out), "--run-id", "E2E")
        self.assertEqual(2, code, output)
        self.assertEqual("evidence_initialized", self._state()["phase"])
        model_id = json.loads((self.run / "discovery.json").read_text(encoding="utf-8"))["models"][0]["model_id"]

        requirements = self._write("requirements.jsonl", json.dumps({
            "requirement_id": "P-R1-001", "round": 1, "kind": "explicit",
            "text": "\u5b9e\u73b0\u6309\u94ae\uff0c\u5e76\u8ba9\u6309\u94ae\u4e3a\u84dd\u8272",
            "source": {"path": "inputs/prompt.md", "line_start": 1, "line_end": 1,
                       "quote": "\u7b2c\u4e00\u8f6e\uff1a\u5b9e\u73b0\u6309\u94ae\uff0c\u5e76\u8ba9\u6309\u94ae\u4e3a\u84dd\u8272\u3002"},
            "mapped_rubric_ids": ["R1-01", "R1-02"], "coverage": "mapped",
        }, ensure_ascii=False) + "\n")
        items = self._write("review-items.json", {"items": [
            {"rubric_id": "R1-01", "basis_status": "supported",
             "basis": [{"type": "prompt", "requirement_id": "P-R1-001"}],
             "reason": "\u9898\u9762\u76f4\u63a5\u8981\u6c42\u5b9e\u73b0\u6309\u94ae\u3002", "reviewer": "w1",
             "reviewed_at": "2026-09-10T10:00:00+08:00"},
            {"rubric_id": "R1-02", "basis_status": "supported",
             "basis": [{"type": "prompt", "requirement_id": "P-R1-001"}],
             "reason": "\u9898\u9762\u76f4\u63a5\u8981\u6c42\u84dd\u8272\u3002", "reviewer": "w1",
             "reviewed_at": "2026-09-10T10:00:00+08:00"},
        ]})
        code, output = self._cli("accept-review", "--run", str(self.run), "--requirements", str(requirements), "--review-items", str(items))
        self.assertEqual(0, code, output)
        self.assertEqual("review_complete", self._state()["phase"])

        index = json.loads((self.run / "v1/inputs/rubric-index.json").read_text(encoding="utf-8"))["rubrics"]
        criterion = {row["id"]: row["criterion_sha256"] for row in index}
        app_digest = hashlib.sha256((self.task / "\u9898-\u6a21\u578b\u8f93\u51fa/GLM5.2/app.js").read_bytes()).hexdigest()
        svg_digest = hashlib.sha256((self.task / "\u9898-\u6a21\u578b\u8f93\u51fa/GLM5.2/fixed.svg").read_bytes()).hexdigest()
        svg_line = PASSIVE_SVG.splitlines()[2]
        blob_root = "evidence/source-blobs"
        (self.run / "v1" / blob_root).mkdir(parents=True, exist_ok=True)
        (self.run / "v1" / blob_root / f"{app_digest}.txt").write_bytes((self.task / "\u9898-\u6a21\u578b\u8f93\u51fa/GLM5.2/app.js").read_bytes())
        (self.run / "v1" / blob_root / f"{svg_digest}.txt").write_bytes((self.task / "\u9898-\u6a21\u578b\u8f93\u51fa/GLM5.2/fixed.svg").read_bytes())
        batch = self._write("evidence.jsonl", "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in [
            {"model_id": model_id, "rubric_id": "R1-01", "rubric_round": 1, "criterion": "\u6309\u94ae\u53ef\u70b9\u51fb",
             "disposition": "examined", "coverage": "complete", "suggested_score": 1, "confidence": "high",
             "reason_code": "implemented_static_only", "fact_summary": "\u70b9\u51fb\u5904\u7406\u5668\u5df2\u7ed1\u5b9a\u3002",
             "evidence": [{"evidence_id": f"EV-{model_id}-R1-01-001", "type": "static_line", "direction": "support",
                           "source_state": "final", "round": 1, "path": "app.js", "line_start": 1, "line_end": 1,
                           "file_sha256": app_digest, "source_blob_path": f"{blob_root}/{app_digest}.txt",
                           "excerpt": "button.onclick = run;", "fact": "\u7ed1\u5b9a\u70b9\u51fb\u5904\u7406\u5668\u3002"}],
             "human_check_needed": False, "adjudication_ids": [], "limitations": []},
            {"model_id": model_id, "rubric_id": "R1-02", "rubric_round": 1, "criterion": "\u6309\u94ae\u4e3a\u84dd\u8272",
             "disposition": "examined", "coverage": "complete", "suggested_score": 1, "confidence": "high",
             "reason_code": "implemented_static_only", "fact_summary": "\u77e9\u5f62\u586b\u5145\u8272\u4e3a #2563EB\u3002",
             "evidence": [{"evidence_id": f"EV-{model_id}-R1-02-001", "type": "static_line", "direction": "support",
                           "source_state": "final", "round": 1, "path": "fixed.svg", "line_start": 3, "line_end": 3,
                           "file_sha256": svg_digest, "source_blob_path": f"{blob_root}/{svg_digest}.txt",
                           "excerpt": svg_line, "fact": "\u77e9\u5f62\u586b\u5145\u8272\u4e3a\u84dd\u8272\u3002"}],
             "human_check_needed": False, "adjudication_ids": [], "limitations": []},
        ]))
        code, output = self._cli("accept-evidence", "--run", str(self.run), "--candidate", str(batch))
        self.assertEqual(0, code, output)
        self.assertEqual("evidence_collected", self._state()["phase"])

        code, output = self._cli("seal-base", "--run", str(self.run))
        self.assertEqual(0, code, output)
        self.assertEqual("base_verified", self._state()["phase"])
        inner = self.run / "base" / "evidence-bundle.zip"
        inner_digest = hashlib.sha256(inner.read_bytes()).hexdigest()

        code, output = self._cli("freeze-media", "--run", str(self.run))
        self.assertEqual(0, code, output)
        self.assertEqual("media_frozen", self._state()["phase"])
        index = json.loads((self.run / "form-ready/observations/media-index.json").read_text(encoding="utf-8"))
        target_media = next(media_id for media_id, use in index["uses"].items() if use["role"] == "target")

        code, output = self._cli("render", "--run", str(self.run))
        self.assertEqual(0, code, output)
        index = json.loads((self.run / "form-ready/observations/media-index.json").read_text(encoding="utf-8"))
        candidates = [media_id for media_id, use in index["uses"].items() if use["role"] == "candidate_full"]
        self.assertEqual(1, len(candidates), index["uses"])
        render_media_id = candidates[0]

        manifest = json.loads((self.run / "form-ready/FORM-READY.json").read_text(encoding="utf-8"))
        envelope = {
            "outer_package_id": manifest["outer_package_id"], "base_package_id": manifest["base"]["package_id"],
            "base_zip_sha256": manifest["base"]["sha256"], "source_input_digest": manifest["base"]["source_input_digest"],
            "task_id": json.loads((self.run / "v1/MANIFEST.json").read_text(encoding="utf-8"))["task"]["name"],
            "model_id": model_id, "rubric_id": "R1-02", "round": 1, "criterion_sha256": criterion["R1-02"],
        }
        for read_index in (1, 2):
            question = f"Read {read_index}: what colour is the button rectangle, and where is it? "
            raw = f"Read {read_index} answer: the rectangle sits at the top-left and its fill is #2563EB."
            record = {**envelope, "observation_id": f"VIS-{model_id}-R1-02-0{read_index}", "type": "machine_vision",
                      "read_index": read_index, "invocation_id": f"invocation-{read_index}", "session_id": f"session-{read_index}",
                      "independent_context": True, "input_media_ids": [target_media, render_media_id],
                      "tool": "vision-bridge", "model": "vision-model", "tool_version": "1.0.0",
                      "question": question, "prompt_sha256": hashlib.sha256(question.encode("utf-8")).hexdigest(),
                      "raw_response": raw, "raw_response_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
                      "fact": f"the rectangle fill is #2563EB (read {read_index})", "verdict": 1,
                      "confidence": "high", "conflict": False, "observed_at": "2026-09-10T10:00:00+08:00"}
            code, output = self._cli("record-vision", "--run", str(self.run), "--record", str(self._write(f"vision-{read_index}.json", record)))
            self.assertEqual(2, code, output)

        code, output = self._cli("decide", "--run", str(self.run), "--pair", f"{model_id}/R1-01", "--value-json",
                                 str(self._write("d1.json", {"score": 1, "decided_by": "mechanical",
                                                             "reason": "\u9759\u6001\u8bc1\u636e\u652f\u6301\u3002",
                                                             "evidence_ids": [f"EV-{model_id}-R1-01-001"],
                                                             "decider": "worker-1",
                                                             "decided_at": "2026-09-10T10:00:00+08:00"})))
        self.assertEqual(2, code, output)
        code, output = self._cli("decide", "--run", str(self.run), "--pair", f"{model_id}/R1-02", "--value-json",
                                 str(self._write("d2.json", {"score": 1, "decided_by": "machine_vision",
                                                             "reason": "\u4e24\u6b21\u72ec\u7acb\u8bfb\u4e00\u81f4\u3002",
                                                             "evidence_ids": [f"VIS-{model_id}-R1-02-01", f"VIS-{model_id}-R1-02-02"],
                                                             "decider": "worker-1",
                                                             "decided_at": "2026-09-10T10:00:00+08:00"})))
        # Recording the last open pair closes the decision phase immediately.
        self.assertEqual(0, code, output)
        self.assertEqual("decisions_complete", self._state()["phase"])

        decisions = json.loads((self.run / "form-ready/decisions/final-decisions.json").read_text(encoding="utf-8"))
        scores = sorted(row["score"] for row in decisions["records"])
        self.assertEqual([1, 1], scores)
        self.assertTrue(all(row["decided_by"] in {"mechanical", "machine_vision"} for row in decisions["records"]))
        authors = {row["rubric_id"]: row["decided_by"] for row in decisions["records"]}
        self.assertEqual({"R1-01": "mechanical", "R1-02": "machine_vision"}, authors)

        # The frozen inner package must be untouched by every later phase.
        self.assertEqual(inner_digest, hashlib.sha256(inner.read_bytes()).hexdigest())
        self.assertEqual(manifest["base"]["sha256"], inner_digest)

        # Presentation input can be filled, but the subjective slots need a real
        # human, so the run stops there instead of fabricating an attestation.
        from presentation_workspace import initialize_presentation
        initialize_presentation(self.run / "form-ready")
        for slot, value in (
            ("task.ranking_reason", "fixture insight"), ("task.maximum_difference", "fixture insight"),
            ("task.capability_boundary", "fixture insight"), ("task.difficulty_and_approach", "fixture insight"),
            (f"model.{model_id}.overall_impression", 3.0), (f"model.{model_id}.G1", {"applicable": True, "score": 3.0, "basis": "ok"}),
            (f"model.{model_id}.G2", {"applicable": True, "score": 3.0, "basis": "ok"}),
            (f"model.{model_id}.G3", {"applicable": True, "score": 3.0, "basis": "ok"}),
            (f"model.{model_id}.S1", {"applicable": False, "basis": "\u4e0d\u9002\u7528\uff1a\u975e\u89c6\u89c9\u9898"}),
            (f"model.{model_id}.A1", {"applicable": False, "basis": "\u4e0d\u9002\u7528\uff1a\u653f\u7b56"}),
            (f"model.{model_id}.R1", {"applicable": False, "basis": "\u4e0d\u9002\u7528\uff1a\u5355\u8f6e"}),
            (f"model.{model_id}.pros", ["pro one", "pro two"]), (f"model.{model_id}.cons", ["con one"]),
            (f"model.{model_id}.style", "fixture style"), (f"model.{model_id}.labels.pros", ["\u6a21\u5757\u62c6\u5206\u6e05\u6670"]),
            (f"model.{model_id}.labels.cons", ["\u529f\u80fd\u7f3a\u9677"]),
            (f"model.{model_id}.labels.style", ["\u6280\u672f\u6808\u504f\u597d"]),
        ):
            code, output = self._cli("record-presentation", "--run", str(self.run), "--slot", slot, "--value-json",
                                     str(self._write("slot.json", {"value": value, "basis": "fixture basis",
                                                                   "evidence_ids": [f"EV-{model_id}-R1-01-001"],
                                                                   "recorder": "worker-1",
                                                                   "recorded_at": "2026-09-10T10:00:00+08:00"})))
            self.assertEqual(2, code, f"{slot}: {output}")
        code, output = self._cli("attest-presentation", "--run", str(self.run), "--slot", f"model.{model_id}.overall_impression", "--observer", "operator-1")
        self.assertEqual(5, code, "a human attestation must require a real console")
        self.assertEqual("decisions_complete", self._state()["phase"])
        self.assertEqual([], sorted(path.name for path in self.root.iterdir() if path.name.endswith(".zip")))

        forbidden = {"\u4eba\u5de5\u88c1\u5b9a\u6e05\u5355.md", "evidence_requests.json", "human-decisions.json", "local-human-evidence.jsonl"}
        present = {path.name for path in (self.run / "form-ready").rglob("*") if path.name in forbidden}
        self.assertEqual(set(), present)


if __name__ == "__main__":
    unittest.main()
