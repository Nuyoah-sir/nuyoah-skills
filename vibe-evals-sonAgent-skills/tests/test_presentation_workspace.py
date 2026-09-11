import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

from presentation_workspace import (
    DIMENSIONS, MODEL_SLOTS, TASK_SLOTS, initialize_presentation, record_attestation,
    record_slot, slot_ids, validate_presentation,
)
from v21_labels import LABEL_SECTIONS, LABEL_SOURCE, allowed_labels, build_label_document, load_label_document
from tests.v2_fixtures import make_form_ready_workspace

EV_ID = "EV-model-a-R1-01-001"
NOT_APPLICABLE = "\u4e0d\u9002\u7528"


class V21LabelLibraryTests(unittest.TestCase):
    def test_checked_in_label_library_is_exactly_reproducible_from_the_markdown(self):
        rebuilt = build_label_document(ROOT / LABEL_SOURCE)
        self.assertEqual(rebuilt, load_label_document(ROOT / "shared"))

    def test_label_library_is_non_trivial_and_has_no_duplicate_tags(self):
        allowed = allowed_labels(ROOT / "shared")
        self.assertEqual(set(LABEL_SECTIONS), set(allowed))
        for section, tags in allowed.items():
            with self.subTest(section=section):
                self.assertGreaterEqual(len(tags), 10)
        flattened = [tag for tags in allowed.values() for tag in tags]
        self.assertEqual(len(flattened), len(set(flattened)), "a tag must not appear in two sections")

    def test_every_legal_tag_appears_verbatim_in_the_source_markdown(self):
        markdown = (ROOT / LABEL_SOURCE).read_text(encoding="utf-8")
        allowed = allowed_labels(ROOT / "shared")
        missing = sorted(tag for tags in allowed.values() for tag in tags if tag not in markdown)
        self.assertEqual([], missing)


class PresentationWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.fixture = make_form_ready_workspace(self.root / "fixture", complete=True)
        self.form = self.fixture.outer

    def _legal_tags(self, section):
        return sorted(allowed_labels(ROOT / "shared")[section])

    def _proposal(self, value, evidence=None, recorder="remote-agent-01"):
        return {"value": value, "basis": "recorded basis for this slot",
                "evidence_ids": evidence or [EV_ID], "recorder": recorder,
                "recorded_at": "2026-09-10T10:00:00+08:00"}

    def _dimension(self, score=3.0):
        return {"applicable": True, "score": score, "basis": "observable rubric outcome"}

    def _fill(self, *, attest=True):
        for name in TASK_SLOTS:
            record_slot(self.form, f"task.{name}", self._proposal(f"observation for {name}"))
        for name in DIMENSIONS:
            record_slot(self.form, f"model.model-a.{name}", self._proposal(self._dimension()))
        record_slot(self.form, "model.model-a.overall_impression", self._proposal(3.0))
        record_slot(self.form, "model.model-a.pros", self._proposal(["pro one", "pro two"]))
        record_slot(self.form, "model.model-a.cons", self._proposal(["con one"]))
        record_slot(self.form, "model.model-a.style", self._proposal("distinctive behaviour"))
        record_slot(self.form, "model.model-a.labels.pros", self._proposal(self._legal_tags("Pros")[:2]))
        record_slot(self.form, "model.model-a.labels.cons", self._proposal(self._legal_tags("Cons")[:1]))
        record_slot(self.form, "model.model-a.labels.style", self._proposal(self._legal_tags("Stylistic Fingerprints")[:1]))
        if attest:
            for name in ("overall_impression", "G3", "style", "labels.pros", "labels.cons", "labels.style"):
                record_attestation(self.form, f"model.model-a.{name}", observer="operator-01",
                                   confirmation_text="yes, I confirm this reads correctly")

    def test_initialize_creates_every_slot_exactly_once_and_refuses_a_second_run(self):
        result = initialize_presentation(self.form)
        self.assertEqual(4 + len(MODEL_SLOTS), result["slots"])
        document = json.loads((self.form / "presentation/presentation-input.json").read_text(encoding="utf-8"))
        self.assertEqual(set(slot_ids(["model-a"])), set(document["slots"]))
        self.assertTrue(all(slot["value"] is None for slot in document["slots"].values()))
        with self.assertRaises(FileExistsError):
            initialize_presentation(self.form)

    def test_record_requires_proposal_basis_evidence_recorder_and_a_known_slot(self):
        initialize_presentation(self.form)
        with self.assertRaisesRegex(ValueError, "PRESENTATION_SLOT_UNKNOWN"):
            record_slot(self.form, "model.model-a.not_a_slot", self._proposal("x"))
        for field in ("basis", "evidence_ids", "recorder"):
            with self.subTest(field=field):
                proposal = self._proposal("x")
                proposal.pop(field)
                with self.assertRaisesRegex(ValueError, "PRESENTATION_RECORD_REJECTED"):
                    record_slot(self.form, "task.ranking_reason", proposal)
        with self.assertRaisesRegex(ValueError, "does not resolve"):
            record_slot(self.form, "task.ranking_reason", self._proposal("x", evidence=["EV-nope"]))
        with self.assertRaisesRegex(ValueError, "does not resolve"):
            record_slot(self.form, "model.model-a.overall_impression", self._proposal(3.0, evidence=["VIS-someone-else"]))

    def test_complete_workspace_is_only_reached_with_matching_attestations(self):
        initialize_presentation(self.form)
        self._fill(attest=False)
        report = validate_presentation(self.form, ROOT / "shared")
        self.assertFalse(report["presentation_complete"])
        codes = {row["code"] for row in report["errors"]}
        self.assertIn("PRESENTATION_ATTESTATION_REQUIRED", codes)
        self.assertNotIn("PRESENTATION_SLOT_VALUE_INVALID", codes)

        for name in ("overall_impression", "G3", "style", "labels.pros", "labels.cons", "labels.style"):
            record_attestation(self.form, f"model.model-a.{name}", observer="operator-01",
                               confirmation_text="yes, I confirm this reads correctly")
        report = validate_presentation(self.form, ROOT / "shared")
        self.assertEqual([], report["errors"])
        self.assertTrue(report["presentation_complete"])

    def test_a_new_proposal_invalidates_a_previous_attestation(self):
        initialize_presentation(self.form)
        self._fill()
        self.assertTrue(validate_presentation(self.form, ROOT / "shared")["presentation_complete"])
        record_slot(self.form, "model.model-a.style", self._proposal("a different stylistic claim"))
        report = validate_presentation(self.form, ROOT / "shared")
        self.assertFalse(report["presentation_complete"])
        self.assertIn("PRESENTATION_ATTESTATION_REQUIRED", {row["code"] for row in report["errors"]})

    def test_value_rules_reject_unsupported_labels_and_off_grid_scores(self):
        initialize_presentation(self.form)
        record_slot(self.form, "model.model-a.labels.pros", self._proposal(["totally invented tag"]))
        record_slot(self.form, "model.model-a.G1", self._proposal(self._dimension(score=2.3)))
        record_slot(self.form, "model.model-a.G2", self._proposal({"applicable": False, "basis": "looks fine"}))
        codes = {row["code"] for row in validate_presentation(self.form, ROOT / "shared")["errors"]}
        self.assertIn("PRESENTATION_LABEL_UNSUPPORTED", codes)
        self.assertIn("PRESENTATION_SCORE_INVALID", codes)
        self.assertIn("PRESENTATION_NOT_APPLICABLE_BASIS", codes)
        record_slot(self.form, "model.model-a.G2", self._proposal({"applicable": False, "basis": f"{NOT_APPLICABLE}: not a visual task"}))
        codes = {row["code"] for row in validate_presentation(self.form, ROOT / "shared")["errors"]}
        self.assertNotIn("PRESENTATION_NOT_APPLICABLE_BASIS", codes)

    def test_tampering_with_a_value_without_the_digest_is_detected(self):
        initialize_presentation(self.form)
        self._fill()
        path = self.form / "presentation/presentation-input.json"
        document = json.loads(path.read_text(encoding="utf-8"))
        document["slots"]["model.model-a.pros"]["value"] = ["swapped in later"]
        path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self.assertIn("PRESENTATION_PROPOSAL_DRIFT", {row["code"] for row in validate_presentation(self.form, ROOT / "shared")["errors"]})


if __name__ == "__main__":
    unittest.main()
