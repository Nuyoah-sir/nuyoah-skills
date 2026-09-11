"""The end-to-end injection matrix.

Every row below is a defect the plan requires to be rejected with a stable code.
Rows marked with a test name are already asserted by that test; the executable
cases in this file cover the rows that had no dedicated proof, and the mapping
check fails if a named test ever disappears.
"""

import hashlib
import json
import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

from artifact_integrity import safe_extract_zip
from package_form_ready import package_form_ready
from register_media import inspect_image, register_render
from tests.v2_fixtures import make_form_ready_workspace, write_json

# case -> (test that already asserts the stable code, code)
REQUIRED_CASES = {
    "missing media": ("test_source_registration_rejects_unindexed_oversize_duplicate_and_keeps_source_immutable", "SOURCE_MEDIA_MISMATCH"),
    "source mutation after freeze": ("test_source_mutation_after_freeze_invalidates_the_workspace_terminally", "SOURCE_CHANGED"),
    "one vision read": ("test_machine_vision_rejects_single_read", "VISION_READ_COUNT"),
    "same invocation or session": ("test_machine_vision_rejects_same_invocation_or_session_id", "VISION_READ_NOT_INDEPENDENT"),
    "vision versus static conflict": ("test_machine_vision_static_evidence_conflict_requires_remote_human", "VISION_STATIC_CONFLICT"),
    "machine text claiming a human": ("test_non_human_record_cannot_claim_human_confirmation", "HUMAN_CLAIM_FOR_NON_HUMAN"),
    "final score left null": ("test_null_or_missing_score_is_never_closed", "DECISION_SCORE_INVALID"),
    "pending adjudication open": ("test_stale_pending_adjudication_must_be_closed_exactly_once", "DECISION_UNRESOLVED_ADJUDICATION"),
    "required material gap": ("test_gap_policy_is_executable_and_never_inferred_from_prose", "GAP_POLICY_REQUIRED"),
    "altered inner bytes": ("test_inner_byte_tampering_and_bundle_replacement_are_refused", "BASE_INTEGRITY_FAILED"),
    "another valid inner package": ("test_inner_byte_tampering_and_bundle_replacement_are_refused", "IDENTITY_MISMATCH"),
    "cross-workspace decision": ("test_cross_workspace_record_replacement_is_refused", "IDENTITY_MISMATCH"),
    "same-total score swap": ("test_two_exchanged_scores_keep_totals_but_change_the_rendered_artifacts", "SCORED_REPLAY_DRIFT"),
    "wrong form input": ("test_rejects_form_input_binding_and_unknown_model_records", "FORM_INPUT_BINDING_MISMATCH"),
    "dual or unknown root manifest": ("test_ambiguous_or_unknown_root_manifests_stop_before_extraction", "artifact kind"),
    "outer traversal member": ("test_malicious_outer_archives_are_rejected_before_extraction", "traversal"),
    "duplicate normalized member": ("test_safe_extract_rejects_normalized_collisions", "collision"),
    "symlink, device, or encrypted member": ("test_safe_extract_rejects_encrypted_symlink_and_device_entries", "unsupported entry"),
    "suspicious compression": ("test_safe_extract_applies_all_limits_during_preflight", "limit"),
    "packaging sidecar write failure": ("test_sidecar_write_failure_rolls_back_the_formal_zip", "rollback"),
    "nested (inner) traversal member": (None, "traversal"),
    "renderer receipt forgery": (None, "RENDER_RECEIPT_"),
}


def _suite_test_names() -> set[str]:
    loader = unittest.TestLoader()
    suite = loader.discover(str(ROOT / "tests"), pattern="test_*.py", top_level_dir=str(ROOT))

    names: set[str] = set()

    def walk(item) -> None:
        if isinstance(item, unittest.TestSuite):
            for child in item:
                walk(child)
        else:
            names.add(item.id().rsplit(".", 1)[-1])

    walk(suite)
    return names


class InjectionMatrixTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_every_required_case_maps_to_a_live_test(self):
        names = _suite_test_names()
        for case, (test_name, _code) in REQUIRED_CASES.items():
            if test_name is None:
                # Covered by an executable case in this module instead.
                self.assertIn(case, {"nested (inner) traversal member", "renderer receipt forgery"})
                continue
            with self.subTest(case=case):
                self.assertIn(test_name, names, f"{case} claims coverage from a test that no longer exists")

    def test_nested_traversal_inside_the_inner_archive_is_rejected(self):
        attack = self.root / "inner.zip"
        with zipfile.ZipFile(attack, "w") as archive:
            archive.writestr("MANIFEST.json", json.dumps({"schema": "vibe-evals-evidence-bundle", "schema_version": "1.0.0"}))
            archive.writestr("../escape.txt", b"boom")
        with self.assertRaises(ValueError) as caught:
            safe_extract_zip(attack, self.root / "inner-out")
        self.assertIn("unsafe artifact path", str(caught.exception).lower())
        self.assertFalse((self.root / "inner-out").exists())
        self.assertFalse((self.root / "escape.txt").exists())

        # The same archive must also be refused when it is used as a base package.
        fixture = make_form_ready_workspace(self.root / "fixture", complete=False, closed=True)
        shutil.copyfile(attack, fixture.outer / "base/evidence-bundle.zip")
        (fixture.outer / "base/evidence-bundle.zip.sha256").write_text(
            f"{hashlib.sha256(attack.read_bytes()).hexdigest()}  evidence-bundle.zip\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            package_form_ready(fixture.outer, self.root / "out" / "nested.zip", task_root=fixture.task_root)
        self.assertFalse((self.root / "out").exists())

    def test_a_hand_written_renderer_receipt_is_refused(self):
        fixture = make_form_ready_workspace(self.root / "fixture", complete=False, closed=True)
        form = fixture.outer
        render = next(form.joinpath("observations/renders").iterdir())
        info = inspect_image(render)
        index = json.loads((form / "observations/media-index.json").read_text(encoding="utf-8"))
        full = next(use for use in index["uses"].values() if use["role"] == "candidate_full")
        renderer = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")

        forged = self.root / "forged-receipt.json"
        write_json(forged, {
            "receipt_source": "render_media", "adapter_class": "passive_static_only",
            "media_id": "RENDER-forged", "role": "candidate_full",
            "bindings": [{"model_id": "model-a", "round": 1, "rubric_ids": ["R1-01"]}],
            "renderer_executable": str(renderer), "renderer_sha256": hashlib.sha256(renderer.read_bytes()).hexdigest(),
            "renderer_version": "forged", "argv": ["forged"], "started_at": "2026-09-10T10:00:00Z",
            "ended_at": "2026-09-10T10:00:05Z", "exit_code": 0,
            "stdout_sha256": "0" * 64, "stderr_sha256": "0" * 64,
            "input_sha256": "0" * 64, "output_sha256": info["sha256"],
            "output_width": info["width"], "output_height": info["height"],
            "safety_findings": {"well_formed": True},
        })
        forged.with_suffix(".json.sha256").write_text(
            f"{hashlib.sha256(forged.read_bytes()).hexdigest()}  {forged.name}\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "RENDER_RECEIPT_"):
            register_render(form, render, forged)

        # A caller-authored safety claim is refused outright.
        claimed = json.loads(forged.read_text(encoding="utf-8"))
        claimed["isolated"] = True
        write_json(forged, claimed)
        forged.with_suffix(".json.sha256").write_text(
            f"{hashlib.sha256(forged.read_bytes()).hexdigest()}  {forged.name}\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "CALLER_SAFETY_CLAIMS_FORBIDDEN"):
            register_render(form, render, forged)


if __name__ == "__main__":
    unittest.main()
