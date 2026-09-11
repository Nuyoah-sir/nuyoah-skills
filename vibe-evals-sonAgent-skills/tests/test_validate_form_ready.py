import json
import sys
import tempfile
import unittest
from pathlib import Path

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


class ValidateFormReadyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_assesses_minimal_complete_unsealed_form_ready_workspace(self):
        fixture = make_form_ready_workspace(self.root, complete=True)
        report = assess_form_ready(fixture.outer)
        self.assertEqual("pass", report["result"], report["errors"])
        self.assertEqual("ready_for_form", report["derived_status"])

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
        fixture = make_form_ready_workspace(self.root, complete=False)
        self.assertEqual("pass", assess_form_ready(fixture.outer)["result"])
        report = validate_form_ready(fixture.outer, require_seal=False)
        self.assertIn("STATUS_MISMATCH", {row["code"] for row in report["errors"]})

    def test_tampered_inner_archive_returns_clean_error(self):
        fixture = make_form_ready_workspace(self.root, complete=True)
        with fixture.sealed_inner_zip.open("ab") as handle:
            handle.write(b"tamper")
        report = assess_form_ready(fixture.outer)
        self.assertEqual("fail", report["result"])
        self.assertIn("BASE_INTEGRITY_FAILED", {row["code"] for row in report["errors"]})


if __name__ == "__main__":
    unittest.main()
