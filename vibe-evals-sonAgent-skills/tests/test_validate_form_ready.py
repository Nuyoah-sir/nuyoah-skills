import json
import hashlib
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

from form_ready_context import load_base_context
from artifact_integrity import safe_extract_zip
from validate_form_ready import (
    PACKAGE_STATUSES,
    REQUIRED_BASE_FIELDS,
    REQUIRED_MANIFEST_FIELDS,
    REQUIRED_PATHS,
    assess_form_ready,
    validate_form_ready,
)
from tests.v2_fixtures import make_form_ready_workspace, rewrite_manifest


def _inner_row_mutator(**_fields):
    """Return a bundle mutator that rewrites the single inner evidence row."""

    def mutate(bundle: Path) -> None:
        path = bundle / "models" / "model-a" / "rubric-evidence.jsonl"
        row = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
        row.update(_fields)
        path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
        manifest_path = bundle / "MANIFEST.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["package_status"] = "ready_for_local_review"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if _fields.get("adjudication_ids"):
            (bundle / "review" / "pending-adjudications.json").write_text(json.dumps({"items": [{
                "adjudication_id": "ADJ-001", "status": "pending", "rubric_ids": ["R1-01"],
                "question": "按行为还是代码判？", "ambiguity": "两种验收解释都可能成立。",
                "evidence_ids": [], "applies_to_all_models": True,
                "recommended_policy": "按真实行为。", "alternative_policy": "按静态代码。",
                "impact": "影响 R1-01。", "resolution": None,
            }]}, ensure_ascii=False), encoding="utf-8")

    return mutate


def _missing_material_mutator(gap_id: str = "rubrics说明.xlsx"):
    def mutate(bundle: Path) -> None:
        path = bundle / "MANIFEST.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest["missing_materials"] = [gap_id]
        manifest["package_status"] = "ready_for_local_review"
        path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    return mutate


def schema_rule_accepts(rule, value):
    """Evaluate the JSON Schema scalar keywords used by these ID contracts."""

    if rule.get("type") == "string" and not isinstance(value, str):
        return False
    return not isinstance(value, str) or re.fullmatch(rule.get("pattern", ".*"), value) is not None


class ValidateFormReadyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_assesses_minimal_complete_unsealed_form_ready_workspace(self):
        fixture = make_form_ready_workspace(self.root, complete=False, closed=True)
        before_sha = hashlib.sha256(fixture.sealed_inner_zip.read_bytes()).hexdigest()
        report = assess_form_ready(fixture.outer)
        self.assertEqual("pass", report["result"], report["errors"])
        self.assertEqual("ready_for_form", report["derived_status"])
        self.assertEqual(before_sha, hashlib.sha256((fixture.outer / "base/evidence-bundle.zip").read_bytes()).hexdigest())
        self.assertEqual(1, report["counts"]["models"])
        self.assertEqual(0, sum(report["unresolved"].values()))
        self.assertEqual(1, report["base_counts"]["models"])

    def test_rejects_wrong_schema_and_unsafe_internal_path(self):
        fixture = make_form_ready_workspace(self.root, complete=True)
        rewrite_manifest(fixture.outer, schema_version="1.0.0", decisions_path="../escape.json")
        codes = {row["code"] for row in validate_form_ready(fixture.outer, require_seal=False)["errors"]}
        self.assertIn("SCHEMA_UNSUPPORTED", codes)
        self.assertIn("UNSAFE_PATH", codes)

    def test_checked_in_schemas_parse_and_all_local_refs_resolve(self):
        schema_dir = ROOT / "shared" / "schema"
        expected = {
            "form-ready-bundle.schema.json", "media-index.schema.json", "observation-record.schema.json",
            "final-decisions.schema.json", "criterion-classification.schema.json", "adjudication-audit.schema.json",
            "source-verification.schema.json", "form-input.schema.json", "presentation-input.schema.json",
            "presentation-attestation.schema.json", "verification-receipt.schema.json",
        }
        for name in expected:
            with self.subTest(schema=name):
                document = json.loads((schema_dir / name).read_text(encoding="utf-8"))
                self.assertEqual("https://json-schema.org/draft/2020-12/schema", document["$schema"])
                stack = [document]
                while stack:
                    value = stack.pop()
                    if isinstance(value, dict):
                        reference = value.get("$ref")
                        if isinstance(reference, str) and reference.startswith("#/"):
                            target = document
                            for token in reference[2:].split("/"):
                                target = target[token.replace("~1", "/").replace("~0", "~")]
                            self.assertIsNotNone(target)
                        stack.extend(value.values())
                    elif isinstance(value, list):
                        stack.extend(value)

    def test_manifest_schema_required_fields_statuses_and_paths_match_runtime(self):
        schema = json.loads((ROOT / "shared" / "schema" / "form-ready-bundle.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(REQUIRED_MANIFEST_FIELDS, frozenset(schema["required"]))
        self.assertEqual(REQUIRED_BASE_FIELDS, frozenset(schema["properties"]["base"]["required"]))
        self.assertEqual(set(REQUIRED_PATHS), set(schema["properties"]["paths"]["required"]))
        self.assertEqual(REQUIRED_PATHS, {key: row["const"] for key, row in schema["properties"]["paths"]["properties"].items()})
        self.assertEqual(PACKAGE_STATUSES, frozenset(schema["properties"]["package_status"]["enum"]))

    def test_fixture_preserves_nested_archive_bytes_and_loads_base_context(self):
        fixture = make_form_ready_workspace(self.root, complete=True)
        self.assertEqual((self.root / "source-name.zip").read_bytes(), fixture.sealed_inner_zip.read_bytes())
        extracted = safe_extract_zip(fixture.sealed_inner_zip, self.root / "base-for-context")
        context = load_base_context(extracted)
        self.assertEqual(fixture.inner_package_id, context.package_id)
        self.assertEqual(fixture.source_digest, context.source_input_digest)
        self.assertEqual(["R1-01"], [row["id"] for row in context.rubrics])
        self.assertIn(("model-a", "R1-01"), context.evidence)

    def test_rejects_valid_record_transplanted_from_another_outer_workspace(self):
        first = make_form_ready_workspace(self.root / "first", complete=True)
        second = make_form_ready_workspace(self.root / "second", complete=True)
        source = json.loads((second.outer / "decisions/final-decisions.json").read_text(encoding="utf-8"))
        target_path = first.outer / "decisions/final-decisions.json"
        target_path.write_text(json.dumps(source, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        report = assess_form_ready(first.outer)

        self.assertIn("IDENTITY_MISMATCH", {row["code"] for row in report["errors"]})

    def test_assessment_ignores_declared_status_but_validation_enforces_it(self):
        fixture = make_form_ready_workspace(self.root, complete=False, closed=True)
        manifest_path = fixture.outer / "FORM-READY.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["package_status"] = "incomplete"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        assessment = assess_form_ready(fixture.outer)
        self.assertEqual("pass", assessment["result"], assessment["errors"])
        self.assertEqual("ready_for_form", assessment["derived_status"])
        report = validate_form_ready(fixture.outer, require_seal=False)
        self.assertIn("STATUS_MISMATCH", {row["code"] for row in report["errors"]})

    def test_tampered_inner_archive_returns_clean_error(self):
        fixture = make_form_ready_workspace(self.root, complete=True)
        with fixture.sealed_inner_zip.open("ab") as handle:
            handle.write(b"tamper")
        report = assess_form_ready(fixture.outer)
        self.assertEqual("fail", report["result"])
        self.assertIn("BASE_INTEGRITY_FAILED", {row["code"] for row in report["errors"]})

    def test_rejects_wrong_json_record_document_shapes(self):
        bad_documents = ([], {"records": "not-an-array"}, {"records": [None]})
        for index, document in enumerate(bad_documents):
            with self.subTest(document=document):
                fixture = make_form_ready_workspace(self.root / str(index), complete=True)
                path = fixture.outer / "decisions/final-decisions.json"
                path.write_text(json.dumps(document) + "\n", encoding="utf-8")
                report = assess_form_ready(fixture.outer)
                self.assertIn("RECORD_INVALID", {row["code"] for row in report["errors"]})

    def test_rejects_non_object_malformed_and_non_utf8_jsonl_records(self):
        payloads = (b"[]\n", b"{not json}\n", b"\xff\n")
        for index, payload in enumerate(payloads):
            with self.subTest(payload=payload):
                fixture = make_form_ready_workspace(self.root / str(index), complete=True)
                (fixture.outer / "observations/machine-vision.jsonl").write_bytes(payload)
                report = assess_form_ready(fixture.outer)
                self.assertIn("RECORD_INVALID", {row["code"] for row in report["errors"]})

    def test_rejects_unreadable_present_record_file(self):
        fixture = make_form_ready_workspace(self.root, complete=True)
        blocked = fixture.outer / "observations/machine-vision.jsonl"
        original = type(blocked).read_text

        def fail_one(path, *args, **kwargs):
            if path == blocked:
                raise OSError("synthetic read failure")
            return original(path, *args, **kwargs)

        with mock.patch.object(type(blocked), "read_text", fail_one):
            report = assess_form_ready(fixture.outer)
        self.assertIn("RECORD_INVALID", {row["code"] for row in report["errors"]})

    def test_patterned_schema_ids_reject_non_strings(self):
        cases = (
            ("observation-record.schema.json", "observation_id"),
            ("adjudication-audit.schema.json", "adjudication_id"),
        )
        for filename, field in cases:
            schema = json.loads((ROOT / "shared" / "schema" / filename).read_text(encoding="utf-8"))
            properties = schema["properties"] if field in schema.get("properties", {}) else schema["$defs"]["record"]["properties"]
            field_schema = properties[field]
            self.assertEqual("string", field_schema.get("type"), f"{filename} must constrain {field} to strings")
            for invalid in (7, True, None):
                with self.subTest(schema=filename, value=invalid):
                    self.assertFalse(schema_rule_accepts(field_schema, invalid))


class FormReadyClosureTests(unittest.TestCase):
    """Task 7: the outer layer must close every inner unresolved state."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def _criterion_sha(self, outer: Path) -> str:
        document = json.loads((outer / "decisions/criterion-classifications.json").read_text(encoding="utf-8"))
        return document["records"][0]["criterion_sha256"]

    def test_outer_decisions_can_close_immutable_inner_null_pending_and_human_check(self):
        fixture = make_form_ready_workspace(
            self.root, complete=False, closed=True,
            mutate_bundle=_inner_row_mutator(suggested_score=None, reason_code="insufficient_evidence",
                                             human_check_needed=True, adjudication_ids=["ADJ-001"]),
        )
        inner_before = (fixture.outer / "base/evidence-bundle.zip").read_bytes()
        manifest = json.loads((fixture.outer / "FORM-READY.json").read_text(encoding="utf-8"))

        report = assess_form_ready(fixture.outer)

        self.assertEqual("pass", report["result"], report["errors"])
        self.assertEqual("ready_for_form", report["derived_status"])
        self.assertEqual(0, sum(report["unresolved"].values()))
        self.assertEqual(1, report["counts"]["closed_inner_human_check_pairs"])
        self.assertEqual(1, report["counts"]["closed_inner_adjudication_pairs"])
        self.assertEqual(1, report["base_counts"]["human_checks"])
        self.assertEqual(1, report["base_counts"]["pending_adjudications"])
        self.assertEqual(inner_before, (fixture.outer / "base/evidence-bundle.zip").read_bytes())
        self.assertEqual(manifest["base"]["sha256"], hashlib.sha256(inner_before).hexdigest())

    def test_refuses_form_ready_when_any_inner_null_lacks_final_binary_decision(self):
        fixture = make_form_ready_workspace(
            self.root, complete=False, closed=True,
            mutate_bundle=_inner_row_mutator(suggested_score=None, reason_code="insufficient_evidence"),
        )
        path = fixture.outer / "decisions/final-decisions.json"
        document = json.loads(path.read_text(encoding="utf-8"))
        document["records"][0]["score"] = None
        path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        report = assess_form_ready(fixture.outer)

        self.assertEqual("fail", report["result"])
        self.assertIn("DECISION_SCORE_INVALID", {row["code"] for row in report["errors"]})
        self.assertNotEqual("ready_for_form", report["derived_status"])

    def test_refuses_form_ready_when_pending_adjudication_lacks_remote_resolution(self):
        fixture = make_form_ready_workspace(
            self.root, complete=False, closed=True,
            mutate_bundle=_inner_row_mutator(adjudication_ids=["ADJ-001"]),
        )
        path = fixture.outer / "decisions/adjudication-audit.json"
        document = json.loads(path.read_text(encoding="utf-8"))
        for record in document["records"]:
            record["decided_by"] = "mechanical"
        path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        report = assess_form_ready(fixture.outer)

        self.assertIn("DECISION_UNRESOLVED_ADJUDICATION", {row["code"] for row in report["errors"]})
        self.assertEqual(1, report["unresolved"]["adjudications"])
        self.assertEqual("incomplete", report["derived_status"])

    def test_refuses_form_ready_when_human_check_lacks_valid_visual_or_human_observation(self):
        fixture = make_form_ready_workspace(
            self.root, complete=False, closed=True,
            mutate_bundle=_inner_row_mutator(human_check_needed=True),
        )
        (fixture.outer / "observations/remote-human.jsonl").write_text("", encoding="utf-8")

        report = assess_form_ready(fixture.outer)

        codes = {row["code"] for row in report["errors"]}
        self.assertIn("DECISION_EVIDENCE_MISSING", codes)
        self.assertEqual(1, report["unresolved"]["human_checks"])

    def test_refuses_form_ready_when_material_gap_lacks_typed_resolution(self):
        fixture = make_form_ready_workspace(
            self.root, complete=False, closed=True,
            mutate_bundle=_missing_material_mutator("rubrics说明.xlsx"),
        )
        path = fixture.outer / "decisions/adjudication-audit.json"
        document = json.loads(path.read_text(encoding="utf-8"))
        document["records"] = [record for record in document["records"] if record.get("gap_id") != "rubrics说明.xlsx"]
        path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        report = assess_form_ready(fixture.outer)

        self.assertIn("DECISION_UNRESOLVED_MATERIAL_GAP", {row["code"] for row in report["errors"]})
        self.assertEqual(1, report["unresolved"]["material_gaps"])
        self.assertEqual(1, report["base_counts"]["material_gaps"])

    def test_requires_exact_model_times_rubric_decision_coverage(self):
        fixture = make_form_ready_workspace(self.root, complete=False, closed=True)
        path = fixture.outer / "decisions/final-decisions.json"
        document = json.loads(path.read_text(encoding="utf-8"))
        document["records"].append({**document["records"][0], "model_id": "model-ghost"})
        path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        report = assess_form_ready(fixture.outer)
        self.assertIn("DECISION_COVERAGE_UNEXPECTED", {row["code"] for row in report["errors"]})

        document["records"] = []
        path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        report = assess_form_ready(fixture.outer)
        self.assertIn("DECISION_COVERAGE_MISSING", {row["code"] for row in report["errors"]})


if __name__ == "__main__":
    unittest.main()
