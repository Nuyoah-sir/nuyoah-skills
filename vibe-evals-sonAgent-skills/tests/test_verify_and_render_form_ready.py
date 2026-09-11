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

from package_form_ready import package_form_ready
from verify_and_render_form_ready import FORBIDDEN_LOCAL_FILES, RECEIPT_NAME, verify_and_render
from tests.v2_fixtures import make_form_ready_workspace

FORM_NAME = "V2.1\u8bc4\u5206\u8868\u5355.md"
REPORT_NAME = "\u53cd\u9988\u62a5\u544a.md"
HEATMAP_NAME = "rubrics\u6253\u5206\u70ed\u529b\u56fe.html"


class VerifyAndRenderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.fixture = make_form_ready_workspace(self.root / "fixture", complete=False, closed=True)
        self.archive = self.root / "handoff" / "pkg.zip"
        self.packaged = package_form_ready(self.fixture.outer, self.archive, task_root=self.fixture.task_root)
        self.sidecar = self.archive.with_suffix(".zip.sha256")

    def _relocated(self):
        """A copy on an unrelated drive-like directory, with no repo context nearby."""

        target_dir = self.root / "relocated-D" / "incoming" / "batch-42"
        target_dir.mkdir(parents=True, exist_ok=True)
        archive = target_dir / "PS-0819copy-form-ready.zip"
        sidecar = archive.with_suffix(".zip.sha256")
        shutil.copyfile(self.archive, archive)
        sidecar.write_text(f"{sha256(archive)}  {archive.name}\n", encoding="utf-8")
        return archive, sidecar

    def test_relocated_archive_renders_from_archive_sidecar_and_output_only(self):
        archive, sidecar = self._relocated()
        output = self.root / "rendered"
        result = verify_and_render(archive, sidecar, output, expected_sha256=sha256(archive))

        self.assertEqual(sha256(archive), result["verified_sha256"])
        published = sorted(path.name for path in output.iterdir())
        self.assertIn(FORM_NAME, published)
        self.assertIn(REPORT_NAME, published)
        self.assertIn(HEATMAP_NAME, published)
        self.assertIn(RECEIPT_NAME, published)
        self.assertEqual(set(), FORBIDDEN_LOCAL_FILES & set(published))
        form = (output / FORM_NAME).read_text(encoding="utf-8")
        self.assertIn("# \u793a\u4f8b\u9898-\u8bc4\u5206\u8868\u5355", form)
        self.assertIn("#### G1 \u00b7 \u6307\u4ee4\u4e0e\u7ea6\u675f\u9075\u5faa", form)
        self.assertIn("\u5f85\u4eba\u5de5\u786e\u8ba4", form)

    def test_receipt_binds_the_package_digests_counts_and_outputs(self):
        archive, sidecar = self._relocated()
        output = self.root / "rendered"
        verify_and_render(archive, sidecar, output, expected_sha256=sha256(archive))
        receipt = json.loads((output / RECEIPT_NAME).read_text(encoding="utf-8"))

        self.assertEqual(sha256(archive), receipt["archive_sha256"])
        self.assertEqual(self.fixture.inner_package_id, receipt["base_package_id"])
        self.assertEqual(1, receipt["counts"]["models"])
        self.assertEqual(1, receipt["counts"]["rubrics"])
        self.assertEqual(0, sum(receipt["unresolved"].values()))
        self.assertEqual("pass", receipt["outer_result"])
        self.assertEqual(sha256(output / FORM_NAME), receipt["form_sha256"])
        self.assertEqual(sha256(output / REPORT_NAME), receipt["report_sha256"])
        self.assertEqual(sha256(output / HEATMAP_NAME), receipt["heatmap_sha256"])
        self.assertIn("T", receipt["rendered_at"])
        serialized = json.dumps(receipt, ensure_ascii=False)
        self.assertNotIn(str(self.root), serialized)
        self.assertNotIn(":\\", serialized)

    def test_rendered_markdown_is_byte_exact_on_replay(self):
        archive, sidecar = self._relocated()
        first = self.root / "first"
        second = self.root / "second"
        verify_and_render(archive, sidecar, first, expected_sha256=sha256(archive))
        verify_and_render(archive, sidecar, second, expected_sha256=sha256(archive))
        self.assertEqual((first / FORM_NAME).read_bytes(), (second / FORM_NAME).read_bytes())

    def test_wrong_expected_digest_and_unresolved_package_are_rejected_without_output(self):
        archive, sidecar = self._relocated()
        output = self.root / "rejected-digest"
        with self.assertRaises(ValueError):
            verify_and_render(archive, sidecar, output, expected_sha256="0" * 64)
        self.assertFalse(output.exists())

        # Drop a required supporting output: the outer seal no longer verifies.
        broken = self.root / "broken.zip"
        with zipfile.ZipFile(archive) as source, zipfile.ZipFile(broken, "w", compression=zipfile.ZIP_DEFLATED) as target:
            for info in source.infolist():
                if info.filename == f"presentation/{HEATMAP_NAME}":
                    continue
                target.writestr(info, source.read(info))
        broken_sidecar = broken.with_suffix(".zip.sha256")
        broken_sidecar.write_text(f"{sha256(broken)}  {broken.name}\n", encoding="utf-8")
        output = self.root / "rejected-broken"
        with self.assertRaises(ValueError):
            verify_and_render(broken, broken_sidecar, output)
        self.assertFalse(output.exists())

    def test_altered_counts_and_digests_inside_the_package_are_rejected(self):
        archive, sidecar = self._relocated()
        cases = {
            "heatmap": f"presentation/{HEATMAP_NAME}",
            "scored": "scored/rubrics-model-a.json",
            "form_input": "presentation/form-input.json",
        }
        for label, member in cases.items():
            with self.subTest(case=label):
                tampered = self.root / f"tampered-{label}.zip"
                with zipfile.ZipFile(archive) as source, zipfile.ZipFile(tampered, "w", compression=zipfile.ZIP_DEFLATED) as target:
                    for info in source.infolist():
                        payload = source.read(info)
                        if info.filename == member:
                            payload = payload + (b"\n" if member.endswith(".json") else b"\n<!--x-->")
                        target.writestr(info, payload)
                tampered_sidecar = tampered.with_suffix(".zip.sha256")
                tampered_sidecar.write_text(f"{sha256(tampered)}  {tampered.name}\n", encoding="utf-8")
                output = self.root / f"rejected-{label}"
                with self.assertRaises(ValueError):
                    verify_and_render(tampered, tampered_sidecar, output)
                self.assertFalse(output.exists(), label)

    def test_existing_output_directory_is_refused(self):
        archive, sidecar = self._relocated()
        output = self.root / "exists"
        output.mkdir()
        with self.assertRaises(FileExistsError):
            verify_and_render(archive, sidecar, output, expected_sha256=sha256(archive))
        self.assertEqual([], sorted(path.name for path in output.iterdir()))


def sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


if __name__ == "__main__":
    unittest.main()
