import json
import contextlib
import io
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

from record_evidence import EvidenceRejected, record_evidence
from record_review import REVIEW_ARTIFACTS, ReviewRejected, record_review
from validate_bundle import validate_bundle
from tests.test_validate_bundle import APP_BLOB, APP_DIGEST, make_bundle
from tests.v2_fixtures import make_form_ready_workspace


def _write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def scaffold_workspace(root: Path) -> Path:
    """Downgrade a fixture bundle into the initializer's review scaffold."""

    bundle = make_bundle(root / "work")
    requirements = [
        json.loads(line)
        for line in (bundle / "inputs/prompt-requirements.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    for row in requirements:
        row["kind"] = "candidate"
        row["coverage"] = "needs_classification"
        row["mapped_rubric_ids"] = []
        row.pop("rationale", None)
    (bundle / "inputs/prompt-requirements.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in requirements), encoding="utf-8")
    index = json.loads((bundle / "inputs/rubric-index.json").read_text(encoding="utf-8"))
    for row in index["rubrics"]:
        row["source_basis"] = []
        row["review"] = {"basis_status": "unreviewed"}
    _write_json(bundle / "inputs/rubric-index.json", index)
    _write_json(bundle / "review/rubric-review.json", {"status": "unreviewed", "items": []})
    return bundle


def review_candidates(root: Path) -> tuple[Path, Path]:
    requirements = [{
        "requirement_id": "P-R1-001",
        "round": 1,
        "kind": "explicit",
        "text": "\u5b9e\u73b0\u6309\u94ae",
        "source": {"path": "inputs/prompt.md", "line_start": 1, "line_end": 1, "quote": "\u7b2c\u4e00\u8f6e\uff1a\u5b9e\u73b0\u6309\u94ae\u3002"},
        "mapped_rubric_ids": ["R1-01"],
        "coverage": "mapped",
    }]
    items = {"items": [{
        "rubric_id": "R1-01",
        "basis_status": "supported",
        "basis": [{"type": "prompt", "requirement_id": "P-R1-001"}],
        "reason": "\u9898\u9762\u660e\u786e\u8981\u6c42\u5b9e\u73b0\u6309\u94ae\uff0c\u6761\u76ee\u53ef\u8ffd\u6eaf\u3002",
        "reviewer": "review-worker-1",
        "reviewed_at": "2026-09-10T10:00:00+08:00",
    }]}
    requirement_path = root / "candidates" / "requirements.jsonl"
    requirement_path.parent.mkdir(parents=True, exist_ok=True)
    requirement_path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in requirements), encoding="utf-8")
    item_path = root / "candidates" / "review-items.json"
    _write_json(item_path, items)
    return requirement_path, item_path


def evidence_row(**overrides) -> dict:
    row = {
        "model_id": "model-a",
        "rubric_id": "R1-01",
        "rubric_round": 1,
        "criterion": "\u6309\u94ae\u53ef\u70b9\u51fb",
        "disposition": "examined",
        "coverage": "complete",
        "suggested_score": 1,
        "confidence": "high",
        "reason_code": "implemented_static_only",
        "fact_summary": "\u70b9\u51fb\u5904\u7406\u5668\u5df2\u7ed1\u5b9a\u3002",
        "evidence": [{
            "evidence_id": "EV-model-a-R1-01-777", "type": "static_line", "direction": "support",
            "source_state": "round_end", "round": 1, "path": "app.js", "line_start": 1, "line_end": 1,
            "file_sha256": APP_DIGEST, "source_blob_path": APP_BLOB,
            "excerpt": "button.onclick = run;", "fact": "\u7ed1\u5b9a\u70b9\u51fb\u5904\u7406\u5668\u3002",
        }],
        "human_check_needed": False,
        "adjudication_ids": [],
        "limitations": [],
    }
    row.update(overrides)
    return row


class RecordReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bundle = scaffold_workspace(self.root)
        self.requirements, self.items = review_candidates(self.root)

    def _artifact_bytes(self) -> dict[str, bytes]:
        return {relative: (self.bundle / relative).read_bytes() for relative in REVIEW_ARTIFACTS}

    def test_completes_the_scaffold_and_leaves_the_workspace_valid(self):
        result = record_review(self.bundle, requirements=self.requirements, review_items=self.items)

        self.assertEqual(1, result["rubrics"])
        report = validate_bundle(self.bundle, require_seal=False)
        self.assertEqual("pass", report["result"], report["errors"])
        index = json.loads((self.bundle / "inputs/rubric-index.json").read_text(encoding="utf-8"))
        self.assertEqual("supported", index["rubrics"][0]["review"]["basis_status"])
        self.assertEqual([{"type": "prompt", "requirement_id": "P-R1-001"}], index["rubrics"][0]["source_basis"])
        review = json.loads((self.bundle / "review/rubric-review.json").read_text(encoding="utf-8"))
        self.assertEqual("reviewed", review["status"])
        self.assertEqual(["R1-01"], [row["rubric_id"] for row in review["items"]])
        self.assertEqual("supported", review["items"][0]["finding"])
        self.assertTrue(review["items"][0]["reason"].strip())
        serialized = (self.bundle / "inputs/prompt-requirements.jsonl").read_text(encoding="utf-8")
        self.assertNotIn("needs_classification", serialized)
        self.assertNotIn('"kind": "candidate"', serialized)

    def test_rejects_unclassified_requirements_without_touching_the_workspace(self):
        before = self._artifact_bytes()
        self.requirements.write_text(
            self.requirements.read_text(encoding="utf-8").replace('"kind": "explicit"', '"kind": "candidate"'),
            encoding="utf-8")
        with self.assertRaises(ReviewRejected) as caught:
            record_review(self.bundle, requirements=self.requirements, review_items=self.items)
        self.assertIn("PROMPT_REQUIREMENT_UNCLASSIFIED", {row["code"] for row in caught.exception.errors})
        self.assertEqual(before, self._artifact_bytes())

    def test_rejects_a_rubric_without_basis_or_review_status(self):
        document = json.loads(self.items.read_text(encoding="utf-8"))
        document["items"][0]["basis"] = []
        document["items"][0]["basis_status"] = "unreviewed"
        _write_json(self.items, document)
        before = self._artifact_bytes()
        with self.assertRaises(ReviewRejected) as caught:
            record_review(self.bundle, requirements=self.requirements, review_items=self.items)
        codes = {row["code"] for row in caught.exception.errors}
        self.assertIn("RUBRIC_BASIS_MISSING", codes)
        self.assertIn("RUBRIC_REVIEW_INCOMPLETE", codes)
        self.assertEqual(before, self._artifact_bytes())

    def test_rejects_a_quote_that_does_not_match_the_frozen_prompt(self):
        document = json.loads(self.requirements.read_text(encoding="utf-8").splitlines()[0])
        document["source"]["quote"] = "\u51ed\u7a7a\u7f16\u9020\u7684\u9898\u9762"
        self.requirements.write_text(json.dumps(document, ensure_ascii=False) + "\n", encoding="utf-8")
        with self.assertRaises(ReviewRejected) as caught:
            record_review(self.bundle, requirements=self.requirements, review_items=self.items)
        self.assertIn("PROMPT_QUOTE_MISMATCH", {row["code"] for row in caught.exception.errors})

    def test_rejects_a_missing_rubric_review_item(self):
        _write_json(self.items, {"items": []})
        with self.assertRaises(ReviewRejected) as caught:
            record_review(self.bundle, requirements=self.requirements, review_items=self.items)
        self.assertIn("RUBRIC_REVIEW_COVERAGE", {row["code"] for row in caught.exception.errors})


class RecordEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bundle = make_bundle(self.root / "work")
        self.candidate = self.root / "candidates" / "evidence.jsonl"
        self.candidate.parent.mkdir(parents=True, exist_ok=True)

    def _write_candidate(self, rows) -> None:
        self.candidate.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")

    def test_merges_a_valid_batch_and_reports_the_remaining_pairs(self):
        self._write_candidate([evidence_row()])
        before = (self.bundle / "models/model-a/rubric-evidence.jsonl").read_bytes()
        result = record_evidence(self.bundle, self.candidate)

        self.assertEqual(1, result["merged"])
        self.assertEqual([], result["missing_pairs"])
        rows = [json.loads(line) for line in (self.bundle / "models/model-a/rubric-evidence.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
        self.assertEqual(1, len(rows))
        self.assertEqual("EV-model-a-R1-01-777", rows[0]["evidence"][0]["evidence_id"])
        self.assertNotEqual(before, (self.bundle / "models/model-a/rubric-evidence.jsonl").read_bytes())
        self.assertEqual("pass", validate_bundle(self.bundle, require_seal=False)["result"])

    def test_rejects_unknown_duplicate_and_stale_rows_without_merging(self):
        before = (self.bundle / "models/model-a/rubric-evidence.jsonl").read_bytes()
        cases = {
            "unknown model": ([evidence_row(model_id="model-ghost")], "EVIDENCE_MODEL_UNKNOWN"),
            "unknown rubric": ([evidence_row(rubric_id="R1-99")], "EVIDENCE_RUBRIC_UNKNOWN"),
            "duplicate pair": ([evidence_row(), evidence_row()], "EVIDENCE_DUPLICATE"),
            "unknown reason code": ([evidence_row(reason_code="made_up")], "EVIDENCE_ROW_ENUM_INVALID"),
            "reused evidence id": ([evidence_row(evidence=[{**evidence_row()["evidence"][0], "evidence_id": "EV-model-a-R1-01-001"}])], "EVIDENCE_DUPLICATE_ID"),
            "null score with a positive reason code": ([evidence_row(suggested_score=None)], "EVIDENCE_STATE_CONFLICT"),
        }
        for label, (rows, code) in cases.items():
            with self.subTest(case=label):
                self._write_candidate(rows)
                with self.assertRaises(EvidenceRejected) as caught:
                    record_evidence(self.bundle, self.candidate)
                self.assertIn(code, {row["code"] for row in caught.exception.errors})
                self.assertEqual(before, (self.bundle / "models/model-a/rubric-evidence.jsonl").read_bytes())

    def test_rejects_a_batch_that_would_break_v1_validation_and_rolls_back(self):
        broken = evidence_row()
        broken["evidence"][0]["source_blob_path"] = "evidence/source-blobs/" + "f" * 64 + ".txt"
        self._write_candidate([broken])
        before = (self.bundle / "models/model-a/rubric-evidence.jsonl").read_bytes()
        with self.assertRaises(EvidenceRejected) as caught:
            record_evidence(self.bundle, self.candidate)
        # The refusal has to come from the real v1 validator, not from the batch pre-checks.
        self.assertIn("SOURCE_BLOB_INVALID", {row["code"] for row in caught.exception.errors})
        self.assertEqual(before, (self.bundle / "models/model-a/rubric-evidence.jsonl").read_bytes())

    def test_empty_batch_is_rejected(self):
        self._write_candidate([])
        with self.assertRaises(EvidenceRejected) as caught:
            record_evidence(self.bundle, self.candidate)
        self.assertIn("EVIDENCE_BATCH_EMPTY", {row["code"] for row in caught.exception.errors})


MODEL_OUTPUT_DIR = "\u9898-\u6a21\u578b\u8f93\u51fa"
PROMPT_LINE = "\u7b2c\u4e00\u8f6e\uff1a\u5b9e\u73b0\u6309\u94ae\u3002"


def synthetic_task_root(root: Path) -> Path:
    """A minimal package that the real discovery accepts."""

    root.mkdir(parents=True, exist_ok=True)
    (root / "prompt.md").write_text(PROMPT_LINE + "\n", encoding="utf-8")
    (root / "rubrics1.json").write_text(json.dumps(
        [{"id": "R1-01", "round": 1, "criterion": "\u6309\u94ae\u53ef\u70b9\u51fb"}], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    model_dir = root / MODEL_OUTPUT_DIR / "GLM5.2"
    model_dir.mkdir(parents=True, exist_ok=True)
    (model_dir / "app.js").write_text("button.onclick = run;\n", encoding="utf-8")
    return root


class CompleteEvalRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.task = synthetic_task_root(self.root / "task")
        self.out = self.root / "runs"
        self.run = self.out / "R1"

    def _run_cli(self, *argv):
        from complete_eval import main

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
            code = main(list(argv))
        self.last_output = buffer.getvalue()
        return code

    def _state(self) -> dict:
        return json.loads((self.run / "run-state.json").read_text(encoding="utf-8"))

    def _start(self) -> int:
        return self._run_cli("start", "--task-root", str(self.task), "--output-root", str(self.out), "--run-id", "R1")

    def test_start_requires_absolute_paths_and_initializes_the_phase_chain(self):
        code = self._run_cli("start", "--task-root", "task", "--output-root", str(self.out), "--run-id", "R0")
        self.assertEqual(3, code)
        self.assertFalse((self.out / "R0").exists())

        code = self._start()
        self.assertEqual(2, code, "a freshly initialized workspace is valid but unresolved")
        state = self._state()
        self.assertEqual("evidence_initialized", state["phase"])
        self.assertEqual(str(self.task), state["task_root"])
        self.assertTrue((self.run / "v1" / "MANIFEST.json").is_file())
        self.assertTrue((self.run / "v1" / "models").is_dir())
        self.assertEqual(2, self._run_cli("status", "--run", str(self.run)), "status reports unresolved until the run is sealed")

    def test_forward_commands_refuse_skipped_or_reordered_phases(self):
        self._start()
        self.assertEqual(3, self._run_cli("seal-base", "--run", str(self.run)))
        self.assertEqual(3, self._run_cli("package", "--run", str(self.run), "--output-zip", str(self.root / "pkg.zip")))
        self.assertEqual(3, self._run_cli("check-source", "--run", str(self.run)))
        self.assertFalse((self.root / "pkg.zip").exists())
        self.assertEqual("evidence_initialized", self._state()["phase"])

    def test_review_then_evidence_batches_advance_only_after_validation(self):
        self._start()
        requirements = self.root / "req.jsonl"
        requirements.write_text(json.dumps({
            "requirement_id": "P-R1-001", "round": 1, "kind": "explicit", "text": "\u5b9e\u73b0\u6309\u94ae",
            "source": {"path": "inputs/prompt.md", "line_start": 1, "line_end": 1, "quote": PROMPT_LINE},
            "mapped_rubric_ids": ["R1-01"], "coverage": "mapped",
        }, ensure_ascii=False) + "\n", encoding="utf-8")
        items = self.root / "items.json"
        _write_json(items, {"items": [{
            "rubric_id": "R1-01", "basis_status": "supported",
            "basis": [{"type": "prompt", "requirement_id": "P-R1-001"}],
            "reason": "\u9898\u9762\u76f4\u63a5\u8981\u6c42\u3002", "reviewer": "w1",
            "reviewed_at": "2026-09-10T10:00:00+08:00",
        }]})
        self.assertEqual(0, self._run_cli("accept-review", "--run", str(self.run), "--requirements", str(requirements), "--review-items", str(items)))
        self.assertEqual("review_complete", self._state()["phase"])

        # Re-running the same phase is refused instead of silently re-writing artifacts.
        before = (self.run / "v1/inputs/rubric-index.json").read_bytes()
        self.assertEqual(3, self._run_cli("accept-review", "--run", str(self.run), "--requirements", str(requirements), "--review-items", str(items)))
        self.assertEqual(before, (self.run / "v1/inputs/rubric-index.json").read_bytes())

        model_id = json.loads((self.run / "discovery.json").read_text(encoding="utf-8"))["models"][0]["model_id"]
        batch = self.root / "batch.jsonl"
        batch.write_text(json.dumps(evidence_row(model_id=model_id), ensure_ascii=False) + "\n", encoding="utf-8")
        self.assertEqual(3, self._run_cli("accept-evidence", "--run", str(self.run), "--candidate", str(batch)))
        self.assertEqual("review_complete", self._state()["phase"])

    def test_source_change_terminates_the_run_and_blocks_every_later_command(self):
        self._start()
        # Give the run a real sealed workspace whose frozen inventory cannot match
        # this synthetic task root, then pretend the earlier phases have run.
        donor = make_form_ready_workspace(self.root / "donor", complete=False, closed=True)
        shutil.copytree(donor.outer, self.run / "form-ready")
        state = self._state()
        state["phase"] = "outputs_complete"
        (self.run / "run-state.json").write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        self.assertEqual(4, self._run_cli("check-source", "--run", str(self.run)))
        self.assertEqual("invalid_source_changed", self._state()["phase"])
        self.assertEqual(4, self._run_cli("status", "--run", str(self.run)))
        self.assertEqual(4, self._run_cli("validate", "--run", str(self.run)))
        self.assertEqual(4, self._run_cli("package", "--run", str(self.run), "--output-zip", str(self.root / "pkg.zip")))
        self.assertFalse((self.root / "pkg.zip").exists())

    def test_human_actions_require_a_real_console(self):
        self._start()
        record = self.root / "human.json"
        _write_json(record, {"observation_id": "HUM-1", "model_id": "glm5-2", "rubric_id": "R1-01"})
        # From an illegal phase the order guard fires first.
        code = self._run_cli("record-human", "--run", str(self.run), "--record", str(record))
        self.assertEqual(3, code)
        state = self._state()
        state["phase"] = "media_frozen"
        (self.run / "run-state.json").write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        code = self._run_cli("record-human", "--run", str(self.run), "--record", str(record))
        self.assertEqual(5, code)
        self.assertEqual("media_frozen", self._state()["phase"])


if __name__ == "__main__":
    unittest.main()
