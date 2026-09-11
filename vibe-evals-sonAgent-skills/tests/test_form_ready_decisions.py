import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

from form_ready_context import BaseContext, ObservationRegistry
from record_observation import sha256_text
from validate_final_decisions import accepted_actor_values, load_gap_policy, validate_final_decisions
from validate_form_ready import validate_form_ready
from tests.v2_fixtures import make_form_ready_workspace, write_json

DECISIONS = "decisions/final-decisions.json"
AUDIT = "decisions/adjudication-audit.json"
EV_ID = "EV-model-a-R1-01-001"


class FormReadyDecisionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def _prepare(self, *, mutate_bundle=None):
        fixture = make_form_ready_workspace(self.root / "fixture", complete=True, mutate_bundle=mutate_bundle)
        return fixture, fixture.outer

    def _decisions(self, outer):
        return json.loads((outer / DECISIONS).read_text(encoding="utf-8"))

    def _codes(self, outer):
        report = validate_form_ready(outer)
        return {row["code"] for row in report["errors"]}, report

    def test_accepted_actor_values_are_exactly_the_four_decision_sources(self):
        self.assertEqual(
            {"mechanical", "accepted_machine", "machine_vision", "remote_human"},
            accepted_actor_values(),
        )

    def test_mechanical_decision_needs_deterministic_evidence_in_the_matching_direction(self):
        fixture, outer = self._prepare()
        self.assertEqual(set(), self._codes(outer)[0])

        document = self._decisions(outer)
        document["records"][0]["evidence_ids"] = ["EV-unknown"]
        write_json(outer / DECISIONS, document)
        self.assertIn("DECISION_EVIDENCE_UNKNOWN", self._codes(outer)[0])

        document["records"][0]["evidence_ids"] = [EV_ID]
        document["records"][0]["score"] = 0
        write_json(outer / DECISIONS, document)
        self.assertIn("DECISION_EVIDENCE_DIRECTION", self._codes(outer)[0])

    def test_accepted_machine_decision_must_equal_the_inner_suggested_score(self):
        fixture, outer = self._prepare()
        document = self._decisions(outer)
        document["records"][0]["decided_by"] = "accepted_machine"
        write_json(outer / DECISIONS, document)
        self.assertEqual(set(), self._codes(outer)[0])

        document["records"][0]["score"] = 0
        write_json(outer / DECISIONS, document)
        self.assertIn("DECISION_ACTOR_UNSUPPORTED", self._codes(outer)[0])

    def test_machine_vision_decision_requires_the_audited_pair(self):
        fixture, outer = self._prepare()
        document = self._decisions(outer)
        document["records"][0]["decided_by"] = "machine_vision"
        write_json(outer / DECISIONS, document)
        self.assertIn("DECISION_EVIDENCE_MISSING", self._codes(outer)[0])

        self._register_candidate_media(fixture)
        rows = [self._vision(fixture, read_index) for read_index in (1, 2)]
        document["records"][0]["evidence_ids"] = [row["observation_id"] for row in rows]
        write_json(outer / DECISIONS, document)
        (outer / "observations/machine-vision.jsonl").write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8"
        )
        self.assertEqual(set(), self._codes(outer)[0])

    def test_remote_human_decision_requires_a_matching_attestation(self):
        fixture, outer = self._prepare()
        self._register_candidate_media(fixture)
        human = self._human(fixture)
        (outer / "observations/remote-human.jsonl").write_text(json.dumps(human, ensure_ascii=False) + "\n", encoding="utf-8")
        document = self._decisions(outer)
        document["records"][0]["decided_by"] = "remote_human"
        document["records"][0]["evidence_ids"] = [human["observation_id"]]
        write_json(outer / DECISIONS, document)
        self.assertEqual(set(), self._codes(outer)[0])

        document["records"][0]["score"] = 0
        write_json(outer / DECISIONS, document)
        self.assertIn("DECISION_EVIDENCE_MISSING", self._codes(outer)[0])

    def test_null_or_missing_score_is_never_closed(self):
        fixture, outer = self._prepare()
        document = self._decisions(outer)
        document["records"][0]["score"] = None
        write_json(outer / DECISIONS, document)
        codes, report = self._codes(outer)
        self.assertIn("DECISION_SCORE_INVALID", codes)
        self.assertNotEqual("ready_for_form", report["derived_status"])

        document["records"][0].pop("score")
        write_json(outer / DECISIONS, document)
        self.assertIn("DECISION_RECORD_INCOMPLETE", self._codes(outer)[0])

    def test_missing_pair_is_unresolved(self):
        fixture, outer = self._prepare()
        document = self._decisions(outer)
        document["records"] = []
        write_json(outer / DECISIONS, document)
        codes, report = self._codes(outer)
        self.assertIn("DECISION_COVERAGE_MISSING", codes)
        self.assertEqual(1, report["unresolved"]["scores"])

    def test_human_check_pair_needs_a_remote_human_decision(self):
        def mutate(bundle: Path) -> None:
            path = bundle / "models" / "model-a" / "rubric-evidence.jsonl"
            row = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
            row["human_check_needed"] = True
            path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
            self._set_inner_status(bundle, "ready_for_local_review")

        fixture, outer = self._prepare(mutate_bundle=mutate)
        codes, report = self._codes(outer)
        self.assertIn("DECISION_UNRESOLVED_HUMAN_CHECK", codes)
        self.assertEqual(1, report["unresolved"]["human_checks"])

    def test_stale_pending_adjudication_must_be_closed_exactly_once(self):
        def mutate(bundle: Path) -> None:
            path = bundle / "models" / "model-a" / "rubric-evidence.jsonl"
            row = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
            row["adjudication_ids"] = ["ADJ-001"]
            path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
            write_json(bundle / "review" / "pending-adjudications.json", {"items": [{
                "adjudication_id": "ADJ-001", "status": "pending", "rubric_ids": ["R1-01"],
                "question": "按行为还是代码判？", "ambiguity": "两种验收解释都可能成立。",
                "evidence_ids": [], "applies_to_all_models": True,
                "recommended_policy": "按真实行为。", "alternative_policy": "按静态代码。",
                "impact": "影响 R1-01。", "resolution": None,
            }]})
            self._set_inner_status(bundle, "ready_for_local_review")

        fixture, outer = self._prepare(mutate_bundle=mutate)
        codes, report = self._codes(outer)
        self.assertIn("DECISION_UNRESOLVED_ADJUDICATION", codes)
        self.assertEqual(1, report["unresolved"]["adjudications"])

        write_json(outer / AUDIT, {"schema_version": "2.0.0", "records": [{
            "adjudication_id": "ADJ-001", "resolution": 1, "affected_pairs": [["model-a", "R1-01"]],
        }]})
        codes, report = self._codes(outer)
        self.assertNotIn("DECISION_UNRESOLVED_ADJUDICATION", codes)
        self.assertEqual(0, report["unresolved"]["adjudications"])

    def test_gap_policy_is_executable_and_never_inferred_from_prose(self):
        policy = {
            "known_optional": {"gap:notes-missing": {"precondition": "field semantics come from the actual JSON"}},
            "required": ["gap:target-missing"],
            "semantic": ["gap:tone-judgement"],
            "unknown_policy": "remote_human_with_limitation",
        }
        context, observations, decisions_path, audit_path = self._synthetic("gap:notes-missing")

        report = validate_final_decisions(context, observations, observations, decisions_path, audit_path=audit_path, gap_policy=policy)
        self.assertEqual(1, report["unresolved"]["material_gaps"])
        write_json(audit_path, {"schema_version": "2.0.0", "records": [{"gap_id": "gap:notes-missing", "precondition_satisfied": True}]})
        report = validate_final_decisions(context, observations, observations, decisions_path, audit_path=audit_path, gap_policy=policy)
        self.assertEqual(0, report["unresolved"]["material_gaps"])

        required_context, observations, decisions_path, audit_path = self._synthetic("gap:target-missing")
        write_json(audit_path, {"schema_version": "2.0.0", "records": [{"gap_id": "gap:target-missing", "precondition_satisfied": True, "decided_by": "remote_human", "limitation": "自称已补齐"}]})
        report = validate_final_decisions(required_context, observations, observations, decisions_path, audit_path=audit_path, gap_policy=policy)
        self.assertEqual(1, report["unresolved"]["material_gaps"])
        self.assertIn("GAP_POLICY_REQUIRED", {row["code"] for row in report["errors"]})

        semantic_context, observations, decisions_path, audit_path = self._synthetic("gap:tone-judgement")
        write_json(audit_path, {"schema_version": "2.0.0", "records": [{"gap_id": "gap:tone-judgement", "decided_by": "mechanical", "limitation": "看起来可忽略"}]})
        report = validate_final_decisions(semantic_context, observations, observations, decisions_path, audit_path=audit_path, gap_policy=policy)
        self.assertEqual(1, report["unresolved"]["material_gaps"])
        write_json(audit_path, {"schema_version": "2.0.0", "records": [{"gap_id": "gap:tone-judgement", "decided_by": "remote_human", "limitation": "本次不做主观语气判断。"}]})
        report = validate_final_decisions(semantic_context, observations, observations, decisions_path, audit_path=audit_path, gap_policy=policy)
        self.assertEqual(0, report["unresolved"]["material_gaps"])

    def test_shipped_gap_policy_starts_empty_and_denies_unknown_gaps(self):
        policy = load_gap_policy(ROOT / "shared" / "references" / "gap-policy.json")
        self.assertEqual({}, dict(policy["known_optional"]))
        self.assertEqual([], list(policy["required"]))
        self.assertEqual("remote_human_with_limitation", policy["unknown_policy"])

    # -- helpers -----------------------------------------------------------

    def _set_inner_status(self, bundle: Path, status: str) -> None:
        path = bundle / "MANIFEST.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest["package_status"] = status
        write_json(path, manifest)

    def _register_candidate_media(self, fixture):
        path = fixture.outer / "observations/media-index.json"
        index = json.loads(path.read_text(encoding="utf-8"))
        manifest = json.loads((fixture.outer / "FORM-READY.json").read_text(encoding="utf-8"))
        binding = {
            "outer_package_id": manifest["outer_package_id"], "base_package_id": fixture.inner_package_id,
            "base_zip_sha256": manifest["base"]["sha256"], "source_input_digest": fixture.source_digest,
            "task_id": "示例题",
        }
        for media_id, role, digest in (("MEDIA-target", "target", "a" * 64), ("RENDER-candidate", "candidate_full", "b" * 64)):
            index["blobs"][digest] = {"mime": "image/png", "size": 24, "width": 2, "height": 3, "path": f"observations/renders/{digest}.png"}
            index["uses"][media_id] = {"blob_sha256": digest, "role": role, "acquisition_method": "render_media", "bindings": [binding], "status": "registered"}
        write_json(path, index)

    def _envelope(self, fixture):
        manifest = json.loads((fixture.outer / "FORM-READY.json").read_text(encoding="utf-8"))
        classification = json.loads((fixture.outer / "decisions/criterion-classifications.json").read_text(encoding="utf-8"))
        entry = classification["records"][0]
        return {
            "outer_package_id": manifest["outer_package_id"], "base_package_id": fixture.inner_package_id,
            "base_zip_sha256": manifest["base"]["sha256"], "source_input_digest": fixture.source_digest,
            "task_id": entry["task_id"], "model_id": "model-a", "rubric_id": "R1-01", "round": 1,
            "criterion_sha256": entry["criterion_sha256"],
        }

    def _vision(self, fixture, read_index):
        question = f"Read {read_index}: is the button present? "
        raw = f"Answer {read_index}: the button is present."
        return {
            **self._envelope(fixture), "observation_id": f"VIS-model-a-R1-01-0{read_index}",
            "type": "machine_vision", "read_index": read_index,
            "invocation_id": f"request-{read_index}", "session_id": f"session-{read_index}",
            "independent_context": True, "input_media_ids": ["MEDIA-target", "RENDER-candidate"],
            "tool": "vision-bridge", "model": "vision-model-id", "tool_version": "1.0.0",
            "question": question, "prompt_sha256": sha256_text(question),
            "raw_response": raw, "raw_response_sha256": sha256_text(raw),
            "fact": f"button present in render (read {read_index})", "verdict": 1,
            "confidence": "high", "conflict": False, "observed_at": "2026-09-10T10:00:00+08:00",
        }

    def _human(self, fixture):
        return {
            **self._envelope(fixture), "observation_id": "HUM-model-a-R1-01-01",
            "type": "remote_human", "observer": "operator-01", "recorded_by": "remote-agent-01",
            "capture_method": "interactive_terminal", "observed_at": "2026-09-10T10:00:00+08:00",
            "steps": ["open the verified full render"], "expected_result": "the button is present",
            "observed_result": "the button is visible in the centre",
            "observed_version": f"{fixture.inner_package_id} / RENDER-candidate",
            "confirmation_text": "是的，我看到了按钮，就在图中央。",
            "media_ids": ["MEDIA-target", "RENDER-candidate"], "verdict": 1,
        }

    def _synthetic(self, gap_id):
        fixture = make_form_ready_workspace(self.root / f"synthetic-{gap_id.replace(':', '-')}", complete=True)
        manifest = json.loads((fixture.outer / "FORM-READY.json").read_text(encoding="utf-8"))
        rubric = {"id": "R1-01", "round": 1, "criterion": "按钮可点击", "criterion_sha256": "a" * 64}
        inner = {"rubric_id": "R1-01", "suggested_score": 1, "human_check_needed": False, "adjudication_ids": [],
                 "evidence": [{"evidence_id": EV_ID, "type": "static_line", "direction": "support"}]}
        context = BaseContext(
            root=fixture.outer, manifest=manifest, package_id=fixture.inner_package_id,
            source_input_digest=fixture.source_digest, task_id="示例题",
            models={"model-a": {"model_id": "model-a"}}, rubrics=(rubric,),
            evidence={("model-a", "R1-01"): inner}, pending_adjudications=(),
            material_gaps=(gap_id,),
        )
        observations = ObservationRegistry(
            vision=(), human=(),
            classifications={"R1-01": {"classification": "presence", "requires_remote_human": False, "measurable_phrase": "按钮可点击"}},
            media_roles={}, media_status={},
        )
        decisions_path = self.root / f"synthetic-{gap_id.replace(':', '-')}" / "decisions.json"
        write_json(decisions_path, {"schema_version": "2.0.0", "records": [{
            "model_id": "model-a", "rubric_id": "R1-01", "round": 1, "criterion_sha256": "a" * 64,
            "score": 1, "decided_by": "mechanical", "reason": "静态证据支持。",
            "evidence_ids": [EV_ID], "decider": "fixture", "decided_at": "2026-09-10T10:00:00+08:00",
        }]})
        return context, observations, decisions_path, decisions_path.parent / "adjudication-audit.json"


if __name__ == "__main__":
    unittest.main()
