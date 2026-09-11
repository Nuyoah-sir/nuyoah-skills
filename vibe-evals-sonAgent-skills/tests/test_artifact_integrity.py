import hashlib
import json
import shutil
import stat
import sys
import tempfile
import unittest
import unicodedata
import zipfile
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

from artifact_integrity import (
    DEFAULT_LIMITS,
    detect_artifact_kind,
    safe_extract_zip,
    sha256_file,
    verify_checksum_manifest,
    verify_sidecar,
    write_checksum_manifest,
)


class ArtifactIntegrityTests(unittest.TestCase):
    def _zip(self, root: Path, members: list[tuple[str | zipfile.ZipInfo, bytes | str]]) -> Path:
        archive = root / "artifact.zip"
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as handle:
            for name, content in members:
                handle.writestr(name, content)
        return archive

    def test_default_limits_are_the_contract_values(self):
        self.assertEqual(
            {
                "members": 10_000,
                "member_bytes": 128 * 1024 * 1024,
                "total_bytes": 512 * 1024 * 1024,
                "compression_ratio": 200,
            },
            DEFAULT_LIMITS,
        )

    def test_sha256_file_and_filename_bound_sidecar(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "artifact.zip"
            archive.write_bytes(b"archive")
            digest = hashlib.sha256(b"archive").hexdigest()
            sidecar = root / "artifact.zip.sha256"
            sidecar.write_text(f"{digest.upper()}  {archive.name}\n", encoding="utf-8")

            self.assertEqual(digest, sha256_file(archive))
            self.assertEqual(digest, verify_sidecar(archive, sidecar, digest.upper()))

            sidecar.write_text(f"{digest}  renamed.zip\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "filename"):
                verify_sidecar(archive, sidecar)

    def test_sidecar_rejects_bad_format_actual_and_expected_digest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "artifact.zip"
            archive.write_bytes(b"archive")
            digest = sha256_file(archive)
            sidecar = root / "artifact.zip.sha256"
            for text in (
                f"{digest} artifact.zip\n",
                f"sha256:{digest}  artifact.zip\n",
                f"{digest}  artifact.zip\n{digest}  artifact.zip\n",
            ):
                with self.subTest(text=text):
                    sidecar.write_text(text, encoding="utf-8")
                    with self.assertRaises(ValueError):
                        verify_sidecar(archive, sidecar)

            sidecar.write_text(f"{'0' * 64}  artifact.zip\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "mismatch"):
                verify_sidecar(archive, sidecar)
            sidecar.write_text(f"{digest}  artifact.zip\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "expected"):
                verify_sidecar(archive, sidecar, "0" * 64)

    def test_safe_extract_rejects_unsafe_names_before_writing_anything(self):
        bad_names = (
            "../escape.txt",
            "/absolute.txt",
            "C:/drive.txt",
            "folder/name. ",
            "folder/name.",
            "CON",
            "folder/aux.txt",
            "folder/COM1.log",
        )
        for bad_name in bad_names:
            with self.subTest(name=bad_name), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                archive = self._zip(root, [("safe.txt", "safe"), (bad_name, "bad")])
                destination = root / "out"
                with self.assertRaises(ValueError):
                    safe_extract_zip(archive, destination)
                self.assertFalse(destination.exists())
                self.assertFalse((root / "safe.txt").exists())

    def test_safe_extract_rejects_normalized_collisions(self):
        composed = "caf\u00e9.txt"
        decomposed = unicodedata.normalize("NFD", composed)
        for names in (("A.txt", "a.TXT"), (composed, decomposed)):
            with self.subTest(names=names), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                archive = self._zip(root, [(name, "x") for name in names])
                with self.assertRaisesRegex(ValueError, "Duplicate"):
                    safe_extract_zip(archive, root / "out")
                self.assertFalse((root / "out").exists())

    def test_safe_extract_preflights_file_directory_conflicts(self):
        composed = "caf\u00e9"
        decomposed = unicodedata.normalize("NFD", composed)
        cases = (
            (("parent", "file"), ("parent/child.txt", "child")),
            (("A", "file"), ("a/child.txt", "child")),
            (("a/child.txt", "child"), ("A", "file")),
            ((composed, "file"), (f"{decomposed}/child.txt", "child")),
            ((f"{decomposed}/child.txt", "child"), (composed, "file")),
        )
        for members in cases:
            with self.subTest(members=members), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                archive = self._zip(root, list(members))
                destination = root / "out"
                with mock.patch("zipfile.ZipFile.open", side_effect=AssertionError("extraction started")):
                    with self.assertRaisesRegex(ValueError, "conflict"):
                        safe_extract_zip(archive, destination)
                self.assertFalse(destination.exists())

    def test_safe_extract_rejects_all_windows_device_name_forms_before_extraction(self):
        names = (
            "CONIN$",
            "conin$.txt",
            "CONOUT$",
            "conout$.log",
            "COM\u00b9",
            "com\u00b2.txt",
            "COM\u00b3.log",
            "LPT\u00b9",
            "lpt\u00b2.txt",
            "LPT\u00b3.log",
        )
        for name in names:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                archive = self._zip(root, [(name, "device")])
                destination = root / "out"
                with mock.patch("zipfile.ZipFile.open", side_effect=AssertionError("extraction started")):
                    with self.assertRaisesRegex(ValueError, "reserved"):
                        safe_extract_zip(archive, destination)
                self.assertFalse(destination.exists())

    def test_safe_extract_rejects_encrypted_symlink_and_device_entries(self):
        encrypted = zipfile.ZipInfo("encrypted.txt")
        encrypted.flag_bits |= 0x1
        symlink = zipfile.ZipInfo("link")
        symlink.create_system = 3
        symlink.external_attr = (stat.S_IFLNK | 0o777) << 16
        device = zipfile.ZipInfo("device")
        device.create_system = 3
        device.external_attr = (stat.S_IFCHR | 0o600) << 16
        for info in (encrypted, symlink, device):
            with self.subTest(name=info.filename), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                archive = self._zip(root, [(info, b"x")])
                if info is encrypted:
                    # zipfile clears the encryption bit while writing; set it in
                    # both local and central headers to create a flagged fixture.
                    data = bytearray(archive.read_bytes())
                    for signature, offset in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
                        start = data.index(signature)
                        flags = int.from_bytes(data[start + offset : start + offset + 2], "little") | 1
                        data[start + offset : start + offset + 2] = flags.to_bytes(2, "little")
                    archive.write_bytes(data)
                with self.assertRaises(ValueError):
                    safe_extract_zip(archive, root / "out")
                self.assertFalse((root / "out").exists())

    def test_safe_extract_applies_all_limits_during_preflight(self):
        cases = (
            ([('a', b'x'), ('b', b'x')], {"members": 1}, "members"),
            ([('a', b'xx')], {"member_bytes": 1}, "member"),
            ([('a', b'x'), ('b', b'x')], {"total_bytes": 1}, "total"),
            ([('a', b'x' * 4096)], {"compression_ratio": 2}, "ratio"),
        )
        for members, limit, label in cases:
            with self.subTest(limit=label), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                archive = self._zip(root, members)
                with self.assertRaises(ValueError):
                    safe_extract_zip(archive, root / "out", limits={**DEFAULT_LIMITS, **limit})
                self.assertFalse((root / "out").exists())

    def test_safe_extract_refuses_existing_destination_and_publishes_atomically(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = self._zip(root, [("nested/file.txt", "ok")])
            destination = root / "out"
            destination.mkdir()
            with self.assertRaises(FileExistsError):
                safe_extract_zip(archive, destination)
            destination.rmdir()

            result = safe_extract_zip(archive, destination)
            self.assertEqual(destination, result)
            self.assertEqual("ok", (destination / "nested/file.txt").read_text(encoding="utf-8"))
            self.assertEqual([], list(root.glob(".out.*.tmp")))

    def test_safe_extract_cleans_staging_after_mid_extraction_io_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = self._zip(root, [("one.txt", "one"), ("two.txt", "two")])
            destination = root / "out"
            real_copy = shutil.copyfileobj
            calls = 0

            def failing_copy(source, target, *args, **kwargs):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("injected write failure")
                return real_copy(source, target, *args, **kwargs)

            with mock.patch("artifact_integrity.shutil.copyfileobj", side_effect=failing_copy):
                with self.assertRaisesRegex(OSError, "injected"):
                    safe_extract_zip(archive, destination)
            self.assertFalse(destination.exists())
            self.assertEqual([], list(root.glob(".out.*.tmp")))

    def test_detect_artifact_kind_recognizes_only_supported_root_manifests(self):
        fixtures = (
            ("FORM-READY.json", "vibe-evals-form-ready-bundle", "2.0.0", "form-ready"),
            ("MANIFEST.json", "vibe-evals-evidence-bundle", "1.0.0", "bundle"),
            ("DELTA.json", "vibe-evals-evidence-delta", "1.0.0", "delta"),
        )
        for filename, schema, version, kind in fixtures:
            with self.subTest(filename=filename), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                archive = self._zip(root, [(filename, json.dumps({"schema": schema, "schema_version": version}))])
                self.assertEqual((kind, version), detect_artifact_kind(archive))

    def test_detect_artifact_kind_rejects_ambiguity_unknown_schema_and_oversize_manifest(self):
        cases = (
            [("MANIFEST.json", '{"schema":"vibe-evals-evidence-bundle","schema_version":"1.0.0"}'), ("DELTA.json", '{}')],
            [("MANIFEST.json", '{"schema":"unknown","schema_version":"1.0.0"}')],
            [("nested/MANIFEST.json", '{}')],
        )
        for members in cases:
            with self.subTest(members=members), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                archive = self._zip(root, members)
                with self.assertRaises(ValueError):
                    detect_artifact_kind(archive)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = self._zip(root, [("MANIFEST.json", " " * 1025)])
            with self.assertRaisesRegex(ValueError, "manifest"):
                detect_artifact_kind(archive, manifest_bytes=1024)

    def test_detect_artifact_kind_runs_full_preflight_before_manifest_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            good = json.dumps({"schema": "vibe-evals-evidence-bundle", "schema_version": "1.0.0"})
            archive = self._zip(root, [("MANIFEST.json", good), ("../escape", "bad")])
            with mock.patch("zipfile.ZipFile.open", side_effect=AssertionError("manifest read happened")):
                with self.assertRaisesRegex(ValueError, "Unsafe"):
                    detect_artifact_kind(archive)

    def test_checksum_manifest_is_deterministic_and_requires_exact_coverage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "z.txt").write_text("z", encoding="utf-8")
            (root / "a").mkdir()
            (root / "a/x.txt").write_text("x", encoding="utf-8")
            (root / "ignored.txt").write_text("ignored", encoding="utf-8")
            manifest = root / "integrity/files.sha256"

            written = write_checksum_manifest(root, excludes={"ignored.txt", "integrity/files.sha256"})
            self.assertEqual(manifest, written)
            rows = verify_checksum_manifest(root, manifest, excludes={"ignored.txt", "integrity/files.sha256"})
            self.assertEqual(["a/x.txt", "z.txt"], [item["path"] for item in rows])
            self.assertEqual(
                sorted(manifest.read_text(encoding="utf-8").splitlines()),
                manifest.read_text(encoding="utf-8").splitlines(),
            )

            (root / "extra.txt").write_text("extra", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "coverage"):
                verify_checksum_manifest(root, manifest, excludes={"ignored.txt", "integrity/files.sha256"})

    def test_checksum_manifest_rejects_unsafe_paths_duplicates_and_mismatch_stably(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "file.txt").write_text("data", encoding="utf-8")
            manifest = root / "files.sha256"
            digest = sha256_file(root / "file.txt")
            for bad_line in (
                f"{digest}  ../file.txt\n",
                f"{digest}  /file.txt\n",
                f"{digest}  C:/file.txt\n",
                f"{digest}  file.txt\n{digest}  FILE.txt\n",
            ):
                with self.subTest(line=bad_line):
                    manifest.write_text(bad_line, encoding="utf-8")
                    with self.assertRaises(ValueError):
                        verify_checksum_manifest(root, manifest, excludes={"files.sha256"})

            manifest.write_text(f"{'0' * 64}  file.txt\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, r"file\.txt") as caught:
                verify_checksum_manifest(root, manifest, excludes={"files.sha256"})
            self.assertNotIn(str(root), str(caught.exception))

if __name__ == "__main__":
    unittest.main()
