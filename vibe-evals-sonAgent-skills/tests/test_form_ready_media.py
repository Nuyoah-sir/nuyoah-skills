import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

from artifact_integrity import sha256_file
from initialize_form_ready import initialize_form_ready
from register_media import register_source_media
from tests.test_validate_bundle import inventory_digest
from tests.v2_fixtures import make_form_ready_workspace


PNG = (b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" +
       (2).to_bytes(4, "big") + (3).to_bytes(4, "big") +
       b"\x08\x06\x00\x00\x00" + b"\x00\x00\x00\x00")


class FormReadyMediaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def _binding(self, outer, fixture, digest):
        return {"outer_package_id": json.loads((outer / "FORM-READY.json").read_text(encoding="utf-8"))["outer_package_id"], "base_package_id": fixture.inner_package_id, "base_zip_sha256": digest, "source_input_digest": fixture.source_digest, "task_id": "示例题"}

    def test_initializer_verifies_then_preserves_inner_archive_and_rebinds_sidecar(self):
        fixture = make_form_ready_workspace(self.root / "fixture", complete=False)
        source_zip = fixture.sealed_inner_zip
        source_sidecar = source_zip.with_suffix(".zip.sha256")
        digest = sha256_file(source_zip)
        source_sidecar.write_text(f"{digest}  {source_zip.name}\n", encoding="utf-8")

        outer = initialize_form_ready(source_zip, source_sidecar, self.root / "new", expected_sha256=digest)

        copied = outer / "base/evidence-bundle.zip"
        self.assertEqual(source_zip.read_bytes(), copied.read_bytes())
        self.assertEqual(f"{digest}  evidence-bundle.zip\n", (outer / "base/evidence-bundle.zip.sha256").read_text(encoding="utf-8"))
        manifest = json.loads((outer / "FORM-READY.json").read_text(encoding="utf-8"))
        self.assertEqual("incomplete", manifest["package_status"])
        self.assertFalse((outer / "READY.json").exists())
        state = json.loads((outer / "provenance/run-state.json").read_text(encoding="utf-8"))
        self.assertEqual("base_verified", state["phase"])

    def test_initializer_refuses_bad_hash_existing_output_and_invalid_runner_state_without_publication(self):
        fixture = make_form_ready_workspace(self.root / "fixture", complete=False)
        source_zip = fixture.sealed_inner_zip
        sidecar = source_zip.with_suffix(".zip.sha256")
        digest = sha256_file(source_zip)
        sidecar.write_text(f"{digest}  {source_zip.name}\n", encoding="utf-8")
        cases = [
            ({"expected_sha256": "0" * 64}, self.root / "bad-hash"),
            ({"run_state": {"phase": "base_sealed", "completed_phase_receipts": []}}, self.root / "bad-state"),
        ]
        for kwargs, output in cases:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                initialize_form_ready(source_zip, sidecar, output, **kwargs)
            self.assertFalse(output.exists())
        existing = self.root / "existing"
        existing.mkdir()
        with self.assertRaises(FileExistsError):
            initialize_form_ready(source_zip, sidecar, existing)

    def test_initializer_advances_matching_runner_state_without_changing_run_id(self):
        fixture = make_form_ready_workspace(self.root / "fixture", complete=False)
        digest = sha256_file(fixture.sealed_inner_zip)
        run_id = "run-fixed"
        state = {
            "phase": "base_sealed", "run_id": run_id, "base_package_id": fixture.inner_package_id,
            "base_zip_sha256": digest, "source_input_digest": fixture.source_digest,
            "created_at": "2026-09-10T00:00:00Z",
            "completed_phase_receipts": [{"phase": "base_sealed", "base_zip_sha256": digest}],
        }
        outer = initialize_form_ready(fixture.sealed_inner_zip, fixture.sealed_inner_sidecar, self.root / "outer", run_state=state)
        result = json.loads((outer / "provenance/run-state.json").read_text(encoding="utf-8"))
        self.assertEqual(run_id, result["run_id"])
        self.assertEqual("base_verified", result["phase"])

    def test_register_source_media_requires_frozen_bytes_and_deduplicates_blob_uses(self):
        fixture = make_form_ready_workspace(self.root / "fixture", complete=False)
        source_zip = fixture.sealed_inner_zip
        sidecar = source_zip.with_suffix(".zip.sha256")
        digest = sha256_file(source_zip)
        sidecar.write_text(f"{digest}  {source_zip.name}\n", encoding="utf-8")
        outer = initialize_form_ready(source_zip, sidecar, self.root / "outer")
        task = self.root / "task"
        task.mkdir()
        image = task / "target.png"
        image.write_bytes(PNG)
        row = {"path": "target.png", "size": len(PNG), "sha256": hashlib.sha256(PNG).hexdigest()}
        freeze = {"input_digest": fixture.source_digest, "source_inventory": [row], "source_inventory_digest": inventory_digest([row])}
        binding = self._binding(outer, fixture, digest)
        first = register_source_media(outer, task, freeze, {"media_id": "SRC-target", "role": "target", "source_relative_path": "target.png", "bindings": [binding]})
        second = register_source_media(outer, task, freeze, {"media_id": "SRC-target-copy", "role": "target", "source_relative_path": "target.png", "bindings": [binding]})
        index = json.loads((outer / "observations/media-index.json").read_text(encoding="utf-8"))
        self.assertEqual(first["blob_sha256"], second["blob_sha256"])
        self.assertEqual(1, len(index["blobs"]))
        self.assertEqual(2, len(index["uses"]))
        self.assertEqual({"width": 2, "height": 3}, {k: index["blobs"][first["blob_sha256"]][k] for k in ("width", "height")})

    def test_register_source_media_rejects_duplicate_id_mutation_mime_and_limits(self):
        fixture = make_form_ready_workspace(self.root / "fixture", complete=False)
        source_zip = fixture.sealed_inner_zip
        sidecar = source_zip.with_suffix(".zip.sha256")
        digest = sha256_file(source_zip)
        sidecar.write_text(f"{digest}  {source_zip.name}\n", encoding="utf-8")
        outer = initialize_form_ready(source_zip, sidecar, self.root / "outer")
        task = self.root / "task"; task.mkdir()
        image = task / "wrong.jpg"; image.write_bytes(PNG)
        row = {"path": "wrong.jpg", "size": len(PNG), "sha256": hashlib.sha256(PNG).hexdigest()}
        freeze = {"input_digest": fixture.source_digest, "source_inventory": [row], "source_inventory_digest": inventory_digest([row])}
        with self.assertRaisesRegex(ValueError, "MIME_EXTENSION_MISMATCH"):
            register_source_media(outer, task, freeze, {"media_id": "SRC-1", "role": "target", "source_relative_path": "wrong.jpg", "bindings": [self._binding(outer, fixture, digest)]})
        image.unlink(); image = task / "target.png"; image.write_bytes(PNG)
        row["path"] = "target.png"; row["size"] += 1
        freeze["source_inventory_digest"] = inventory_digest([row])
        with self.assertRaisesRegex(ValueError, "SOURCE_MEDIA_MISMATCH"):
            register_source_media(outer, task, freeze, {"media_id": "SRC-1", "role": "target", "source_relative_path": "target.png", "bindings": [self._binding(outer, fixture, digest)]})

    def test_source_registration_rejects_unindexed_oversize_duplicate_and_keeps_source_immutable(self):
        fixture = make_form_ready_workspace(self.root / "fixture", complete=False)
        digest = sha256_file(fixture.sealed_inner_zip)
        outer = initialize_form_ready(fixture.sealed_inner_zip, fixture.sealed_inner_sidecar, self.root / "outer")
        task = self.root / "task"; task.mkdir()
        image = task / "target.png"; image.write_bytes(PNG)
        before = image.read_bytes()
        binding = self._binding(outer, fixture, digest)
        empty = {"input_digest": fixture.source_digest, "source_inventory": [], "source_inventory_digest": inventory_digest([])}
        with self.assertRaisesRegex(ValueError, "SOURCE_MEDIA_UNINDEXED"):
            register_source_media(outer, task, empty, {"media_id": "SRC-1", "role": "target", "source_relative_path": "target.png", "bindings": [binding]})
        huge = bytearray(PNG); huge[16:20] = (20_000).to_bytes(4, "big")
        image.write_bytes(huge)
        row = {"path": "target.png", "size": len(huge), "sha256": hashlib.sha256(huge).hexdigest()}
        freeze = {"input_digest": fixture.source_digest, "source_inventory": [row], "source_inventory_digest": inventory_digest([row])}
        with self.assertRaisesRegex(ValueError, "IMAGE_LIMIT_EXCEEDED"):
            register_source_media(outer, task, freeze, {"media_id": "SRC-1", "role": "target", "source_relative_path": "target.png", "bindings": [binding]})
        image.write_bytes(before)
        row = {"path": "target.png", "size": len(before), "sha256": hashlib.sha256(before).hexdigest()}
        freeze = {"input_digest": fixture.source_digest, "source_inventory": [row], "source_inventory_digest": inventory_digest([row])}
        plan = {"media_id": "SRC-1", "role": "target", "source_relative_path": "target.png", "bindings": [binding]}
        register_source_media(outer, task, freeze, plan)
        self.assertEqual(before, image.read_bytes())
        with self.assertRaisesRegex(ValueError, "DUPLICATE_MEDIA_ID"):
            register_source_media(outer, task, freeze, plan)
        image.write_bytes(before + b"changed")
        with self.assertRaisesRegex(ValueError, "SOURCE_MEDIA_MISMATCH"):
            register_source_media(outer, task, freeze, plan | {"media_id": "SRC-2"})

    def test_source_registration_rejects_symlinked_task_root(self):
        fixture = make_form_ready_workspace(self.root / "fixture", complete=False)
        outer = initialize_form_ready(fixture.sealed_inner_zip, fixture.sealed_inner_sidecar, self.root / "outer")
        real = self.root / "real"; real.mkdir(); (real / "target.png").write_bytes(PNG)
        linked = self.root / "linked"
        try:
            os.symlink(real, linked, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"cannot create directory symlink: {exc}")
        with self.assertRaisesRegex(ValueError, "symbolic link|reparse point"):
            register_source_media(outer, linked, {"input_digest": fixture.source_digest, "source_inventory": [], "source_inventory_digest": inventory_digest([])}, {"media_id": "SRC-1", "role": "target", "source_relative_path": "target.png", "bindings": []})


if __name__ == "__main__":
    unittest.main()
