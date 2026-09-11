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
from project_form_ready_scores import project_scores
from render_supporting_outputs import (
    FORM_INPUT, HEATMAP, REPORT_INPUT, REPORT_MD, render_supporting_outputs,
    verify_supporting_outputs,
)
from form_ready_context import load_base_context, load_observation_registry
from validate_final_decisions import validate_final_decisions
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


class SupportingOutputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.fixture = make_form_ready_workspace(self.root / "fixture", complete=True)
        self.form = self.fixture.outer
        self.base = load_base_context(self.fixture.sealed_inner_zip.parents[2] / "inspected-inner")

    def _closed(self):
        manifest = json.loads((self.form / "FORM-READY.json").read_text(encoding="utf-8"))
        observations = load_observation_registry(self.form, manifest)
        outcome = validate_final_decisions(
            self.base, observations, observations, self.form / "decisions/final-decisions.json",
            audit_path=self.form / "decisions/adjudication-audit.json",
        )
        return outcome["registry"]

    def _present(self):
        if not (self.form / "presentation/presentation-input.json").exists():
            initialize_presentation(self.form)
        legal = sorted(allowed_labels(ROOT / "shared")["Pros"])
        for name in TASK_SLOTS:
            record_slot(self.form, f"task.{name}", {"value": f"observation for {name}", "basis": "basis",
                "evidence_ids": [EV_ID], "recorder": "remote-agent-01", "recorded_at": "2026-09-10T10:00:00+08:00"})
        for name in DIMENSIONS:
            value = {"applicable": False, "basis": f"{NOT_APPLICABLE}: not applicable here"} if name in {"S1", "A1", "R1"} else {"applicable": True, "score": 3.0, "basis": "observable"}
            record_slot(self.form, f"model.model-a.{name}", {"value": value, "basis": "basis",
                "evidence_ids": [EV_ID], "recorder": "remote-agent-01", "recorded_at": "2026-09-10T10:00:00+08:00"})
        body = lambda value: {"value": value, "basis": "basis", "evidence_ids": [EV_ID],
                              "recorder": "remote-agent-01", "recorded_at": "2026-09-10T10:00:00+08:00"}
        record_slot(self.form, "model.model-a.overall_impression", body(3.0))
        record_slot(self.form, "model.model-a.pros", body(["pro one", "pro two"]))
        record_slot(self.form, "model.model-a.cons", body(["con one"]))
        record_slot(self.form, "model.model-a.style", body("distinctive behaviour"))
        record_slot(self.form, "model.model-a.labels.pros", body(legal[:2]))
        record_slot(self.form, "model.model-a.labels.cons", body(sorted(allowed_labels(ROOT / "shared")["Cons"])[:1]))
        record_slot(self.form, "model.model-a.labels.style", body(sorted(allowed_labels(ROOT / "shared")["Stylistic Fingerprints"])[:1]))
        for name in ("overall_impression", "G3", "style", "labels.pros", "labels.cons", "labels.style"):
            record_attestation(self.form, f"model.model-a.{name}", observer="operator-01", confirmation_text="confirmed")

    def test_render_requires_a_complete_presentation_and_closed_decisions(self):
        initialize_presentation(self.form)
        with self.assertRaisesRegex(ValueError, "SUPPORTING_OUTPUTS_REQUIRE_COMPLETE_PRESENTATION"):
            render_supporting_outputs(self.form, ROOT / "shared")
        self._present()
        project_scores(self.base, self._closed(), self.form)
        result = render_supporting_outputs(self.form, ROOT / "shared")
        for key, relative in (("report_input", REPORT_INPUT), ("report", REPORT_MD), ("heatmap", HEATMAP), ("form_input", FORM_INPUT)):
            self.assertTrue(Path(result[key]).is_file(), key)
            self.assertEqual(relative, Path(result[key]).relative_to(self.form).as_posix())
        with self.assertRaises(FileExistsError):
            render_supporting_outputs(self.form, ROOT / "shared")

    def test_form_input_binds_the_package_digests(self):
        self._present()
        project_scores(self.base, self._closed(), self.form)
        render_supporting_outputs(self.form, ROOT / "shared")
        form_input = json.loads((self.form / FORM_INPUT).read_text(encoding="utf-8"))
        digest = lambda relative: __import__("hashlib").sha256((self.form / relative).read_bytes()).hexdigest()
        self.assertEqual(digest("presentation/presentation-input.json"), form_input["presentation_input_sha256"])
        self.assertEqual(digest("decisions/final-decisions.json"), form_input["final_decisions_sha256"])
        self.assertEqual({"model-a": digest("scored/rubrics-model-a.json")}, form_input["scored_sha256"])
        self.assertEqual(self.fixture.inner_package_id, form_input["base_package_id"])

    def test_verify_replays_byte_identically_and_detects_a_score_change(self):
        self._present()
        project_scores(self.base, self._closed(), self.form)
        render_supporting_outputs(self.form, ROOT / "shared")
        self.assertEqual({"verified": sorted([REPORT_INPUT, REPORT_MD, HEATMAP, FORM_INPUT])},
                         verify_supporting_outputs(self.form, ROOT / "shared"))

        path = self.form / "scored/rubrics-model-a.json"
        rows = json.loads(path.read_text(encoding="utf-8"))
        rows[0]["score"] = 0 if rows[0]["score"] == 1 else 1
        path.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            verify_supporting_outputs(self.form, ROOT / "shared")

    def test_two_exchanged_scores_keep_totals_but_change_the_rendered_artifacts(self):
        from render_supporting_outputs import _heatmap_html

        def report(first: float, second: float) -> dict:
            return {
                "schema_version": "2.0.0", "outer_package_id": "o", "base_package_id": "b",
                "base_zip_sha256": "a" * 64, "source_input_digest": "c" * 64, "task_id": "t",
                "task": {name: "text" for name in TASK_SLOTS},
                "models": {
                    "model-a": {"overall_impression": 3.0, "dimensions": {}, "pros": ["p1", "p2"],
                                "cons": [], "style": "s", "labels": {}, "rubrics": [
                                    {"rubric_id": "R1-01", "round": 1, "criterion": "c1", "score": first, "reason": "r1",
                                     "decided_by": "mechanical", "evidence_ids": ["EV-1"]},
                                    {"rubric_id": "R1-02", "round": 1, "criterion": "c2", "score": second, "reason": "r2",
                                     "decided_by": "mechanical", "evidence_ids": ["EV-2"]}]},
                    "model-b": {"overall_impression": 3.0, "dimensions": {}, "pros": ["p1", "p2"],
                                "cons": [], "style": "s", "labels": {}, "rubrics": [
                                    {"rubric_id": "R1-01", "round": 1, "criterion": "c1", "score": second, "reason": "r1",
                                     "decided_by": "mechanical", "evidence_ids": ["EV-1"]},
                                    {"rubric_id": "R1-02", "round": 1, "criterion": "c2", "score": first, "reason": "r2",
                                     "decided_by": "mechanical", "evidence_ids": ["EV-2"]}]},
                },
            }

        original = report(1, 0)
        exchanged = report(0, 1)
        totals = lambda value: {model_id: sum(row["score"] for row in value["models"][model_id]["rubrics"]) for model_id in value["models"]}
        self.assertEqual(totals(original), totals(exchanged), "the swap must preserve every total")
        self.assertNotEqual(_heatmap_html(original), _heatmap_html(exchanged))
        self.assertIn('<td class="score-1">1</td>', _heatmap_html(original))
        self.assertIn('<td class="score-0">0</td>', _heatmap_html(exchanged))


if __name__ == "__main__":
    unittest.main()
