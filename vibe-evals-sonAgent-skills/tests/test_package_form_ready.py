import hashlib
import json
import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

from extract_artifact import extract_artifact
from package_form_ready import SourceChangedError, package_form_ready
from validate_form_ready import validate_form_ready
from tests.v2_fixtures import make_form_ready_workspace


def _craft_zip(path: Path, members: dict[str, bytes], *, mode: int = 0o600) -> Path:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, payload in members.items():
            info = zipfile.ZipInfo(name)
            info.external_attr = mode << 16
            archive.writestr(info, payload)
    return path


def _different_but_valid_bundle(bundle: Path) -> None:
    """Change one valid inner detail so the bundle bytes differ from the default fixture."""

    path = bundle / "models" / "model-a" / "rubric-evidence.jsonl"
    row = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    row["human_check_needed"] = True
    path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
    manifest_path = bundle / "MANIFEST.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["package_status"] = "ready_for_local_review"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class PackageFormReadyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def _closed(self, name="fixture"):
        return make_form_ready_workspace(self.root / name, complete=False, closed=True)

    def _outputs(self, name="pkg.zip"):
        return self.root / "out" / name

    def _published(self):
        directory = self.root / "out"
        return sorted(path.name for path in directory.iterdir()) if directory.is_dir() else []

    def test_only_a_semantically_complete_workspace_may_package(self):
        incomplete = make_form_ready_workspace(self.root / "incomplete", complete=True)
        with self.assertRaises(ValueError):
            package_form_ready(incomplete.outer, self._outputs(), task_root=incomplete.task_root)
        self.assertEqual([], self._published())

        fixture = self._closed()
        result = package_form_ready(fixture.outer, self._outputs(), task_root=fixture.task_root)
        self.assertEqual("ready_for_form", result["status"])
        self.assertEqual(["pkg.zip", "pkg.zip.sha256"], self._published())
        self.assertEqual(result["sha256"], (self._outputs().with_suffix(".zip.sha256")).read_text(encoding="utf-8").split()[0])

    def test_round_trip_extraction_validates_strictly_and_binds_the_kind(self):
        fixture = self._closed()
        zip_path = self._outputs()
        packaged = package_form_ready(fixture.outer, zip_path, task_root=fixture.task_root)
        result = extract_artifact(None, zip_path, zip_path.with_suffix(".zip.sha256"), self.root / "extracted",
                                  expected_sha256=packaged["sha256"])
        self.assertEqual("form-ready", result["kind"])
        self.assertEqual("ready_for_form", result["validation"]["derived_status"])
        self.assertTrue((self.root / "extracted" / "READY.json").is_file())
        with self.assertRaisesRegex(ValueError, "does not match the detected artifact kind"):
            extract_artifact("bundle", zip_path, zip_path.with_suffix(".zip.sha256"), self.root / "extracted-bundle")
        self.assertFalse((self.root / "extracted-bundle").exists())

    def test_expected_digest_remains_the_trusted_anchor_when_the_outer_seal_is_rewritten(self):
        fixture = self._closed()
        zip_path = self._outputs()
        packaged = package_form_ready(fixture.outer, zip_path, task_root=fixture.task_root)
        forged = self.root / "forged.zip"
        with zipfile.ZipFile(zip_path) as source, zipfile.ZipFile(forged, "w", compression=zipfile.ZIP_DEFLATED) as target:
            for info in source.infolist():
                payload = source.read(info)
                if info.filename == "presentation/report-input.json":
                    value = json.loads(payload.decode("utf-8"))
                    value["models"]["model-a"]["cons"] = ["rewritten without any evidence"]
                    payload = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
                target.writestr(info, payload)
        row = f"{hashlib.sha256(forged.read_bytes()).hexdigest()}  forged.zip\n"
        forged.with_suffix(".zip.sha256").write_text(row, encoding="utf-8")

        # Self-contained checks cannot detect an attacker who rewrites the inner seal too;
        # the external sidecar/expected digest is what still catches it.
        with self.assertRaisesRegex(ValueError, "does not match"):
            extract_artifact("form-ready", forged, forged.with_suffix(".zip.sha256"), self.root / "forged-out",
                             expected_sha256=packaged["sha256"])
        self.assertFalse((self.root / "forged-out").exists())
        self.assertNotEqual(packaged["sha256"], hashlib.sha256(forged.read_bytes()).hexdigest())

    def test_source_mutation_after_freeze_invalidates_the_workspace_terminally(self):
        fixture = self._closed()
        target = fixture.task_root / "source" / "model-a" / "app.js"
        target.write_bytes(target.read_bytes() + b"// drift\n")
        zip_path = self._outputs()
        with self.assertRaises(SourceChangedError) as caught:
            package_form_ready(fixture.outer, zip_path, task_root=fixture.task_root)

        self.assertEqual(4, SourceChangedError.exit_code)
        self.assertIn("SOURCE_CHANGED", str(caught.exception))
        self.assertEqual([], self._published())
        manifest = json.loads((fixture.outer / "FORM-READY.json").read_text(encoding="utf-8"))
        self.assertEqual("incomplete", manifest["package_status"])
        state = json.loads((fixture.outer / "provenance/run-state.json").read_text(encoding="utf-8"))
        self.assertEqual("invalid_source_changed", state["terminal_failure_code"])
        for generated in ("READY.json", "integrity/files.sha256", "integrity/validation-report.json"):
            self.assertFalse((fixture.outer / generated).exists(), generated)

        # Restoring the bytes must not resurrect the workspace.
        target.write_bytes(target.read_bytes()[: -len(b"// drift\n")])
        with self.assertRaises(ValueError):
            package_form_ready(fixture.outer, zip_path, task_root=fixture.task_root)

    def test_sidecar_write_failure_rolls_back_the_formal_zip(self):
        fixture = self._closed()
        zip_path = self._outputs()
        real_replace = __import__("os").replace

        def flaky(source, destination):
            if str(destination).endswith(".zip.sha256"):
                raise OSError("simulated sidecar failure")
            return real_replace(source, destination)

        with mock.patch("os.replace", side_effect=flaky):
            with self.assertRaises(OSError):
                package_form_ready(fixture.outer, zip_path, task_root=fixture.task_root)

        self.assertEqual([], self._published())
        for generated in ("READY.json", "integrity/files.sha256", "integrity/validation-report.json"):
            self.assertFalse((fixture.outer / generated).exists(), generated)

    def test_inner_byte_tampering_and_bundle_replacement_are_refused(self):
        fixture = self._closed()
        inner = fixture.outer / "base" / "evidence-bundle.zip"
        original = inner.read_bytes()
        inner.write_bytes(original + b"\x00")
        with self.assertRaises(ValueError):
            package_form_ready(fixture.outer, self._outputs("tampered.zip"), task_root=fixture.task_root)
        self.assertEqual([], self._published())

        inner.write_bytes(original)
        other = make_form_ready_workspace(self.root / "other", complete=False,
                                          mutate_bundle=_different_but_valid_bundle)
        self.assertNotEqual(original, (other.outer / "base/evidence-bundle.zip").read_bytes(),
                            "the replacement bundle must be a different valid bundle")
        shutil.copyfile(other.outer / "base/evidence-bundle.zip", inner)
        shutil.copyfile(other.outer / "base/evidence-bundle.zip.sha256", fixture.outer / "base/evidence-bundle.zip.sha256")
        with self.assertRaises(ValueError):
            package_form_ready(fixture.outer, self._outputs("swapped.zip"), task_root=fixture.task_root)

    def test_cross_workspace_record_replacement_is_refused(self):
        fixture = self._closed("first")
        other = self._closed("second")
        shutil.copyfile(other.outer / "decisions/final-decisions.json", fixture.outer / "decisions/final-decisions.json")
        with self.assertRaises(ValueError):
            package_form_ready(fixture.outer, self._outputs(), task_root=fixture.task_root)
        report = validate_form_ready(fixture.outer, require_seal=False)
        self.assertIn("IDENTITY_MISMATCH", {row["code"] for row in report["errors"]})

    def test_malicious_outer_archives_are_rejected_before_extraction(self):
        fixture = self._closed()
        zip_path = self._outputs()
        packaged = package_form_ready(fixture.outer, zip_path, task_root=fixture.task_root)
        cases = {
            "traversal": {"FORM-READY.json": b"{}", "../escape.txt": b"boom"},
            "collision": {"FORM-READY.json": b"{}", "form-ready.json": b"{}"},
            "absolute": {"FORM-READY.json": b"{}", "/etc/passwd": b"boom"},
        }
        for label, members in cases.items():
            with self.subTest(case=label):
                attack = self.root / f"{label}.zip"
                _craft_zip(attack, members)
                attack.with_suffix(".zip.sha256").write_text(
                    f"{hashlib.sha256(attack.read_bytes()).hexdigest()}  {attack.name}\n", encoding="utf-8")
                destination = self.root / f"out-{label}"
                with self.assertRaises(ValueError):
                    extract_artifact(None, attack, attack.with_suffix(".zip.sha256"), destination,
                                     expected_sha256=packaged["sha256"] if label == "digest" else None)
                self.assertFalse(destination.exists(), label)

    def test_symlink_and_encrypted_members_are_rejected(self):
        fixture = self._closed()
        zip_path = self._outputs()
        package_form_ready(fixture.outer, zip_path, task_root=fixture.task_root)
        symlink_zip = self.root / "symlink.zip"
        _craft_zip(symlink_zip, {"FORM-READY.json": b"{}", "link": b"../../etc/passwd"}, mode=0o120777)
        symlink_zip.with_suffix(".zip.sha256").write_text(
            f"{hashlib.sha256(symlink_zip.read_bytes()).hexdigest()}  {symlink_zip.name}\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            extract_artifact(None, symlink_zip, symlink_zip.with_suffix(".zip.sha256"), self.root / "symlink-out")
        self.assertFalse((self.root / "symlink-out").exists())


if __name__ == "__main__":
    unittest.main()
