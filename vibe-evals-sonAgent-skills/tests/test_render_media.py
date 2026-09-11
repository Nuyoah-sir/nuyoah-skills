import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

from render_media import inspect_passive_svg, render_media
from register_media import register_render
from tests.test_form_ready_media import PNG
from tests.v2_fixtures import make_form_ready_workspace

SAFE = b'<svg xmlns="http://www.w3.org/2000/svg" width="2" height="3" viewBox="0 0 2 3"><rect x="0" y="0" width="2" height="3" fill="#fff"/></svg>'


class RenderMediaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def _render_receipt(self, *, media_id="REN-full", role="candidate_full", parent=None, crop=None):
        source = self.root / f"{media_id}.svg"; source.write_bytes(SAFE)
        output = self.root / f"{media_id}.png"
        executable = self.root / "msedge.exe"; executable.write_bytes(b"fictional edge")

        def fake_run(argv, **kwargs):
            if "--version" in argv:
                return subprocess.CompletedProcess(argv, 0, b"Edge 1\n", b"")
            screenshot = next(item.split("=", 1)[1] for item in argv if item.startswith("--screenshot="))
            Path(screenshot).write_bytes(PNG)
            return subprocess.CompletedProcess(argv, 0, b"", b"")

        with mock.patch("render_media.subprocess.run", side_effect=fake_run):
            receipt = render_media(
                source, output, renderer_executable=executable, media_id=media_id, role=role,
                bindings=[{"model_id": "model-a", "round": 1, "rubric_ids": ["R1-01"]}],
                parent_media_id=parent, crop=crop,
            )
        return output, receipt

    def test_passive_svg_gate_accepts_small_static_svg_and_rejects_active_forms(self):
        self.assertEqual("passive_static_only", inspect_passive_svg(SAFE)["adapter_class"])
        attacks = {
            "script": b'<svg xmlns="http://www.w3.org/2000/svg"><script/></svg>',
            "foreign": b'<svg xmlns="http://www.w3.org/2000/svg"><foreignObject/></svg>',
            "smil": b'<svg xmlns="http://www.w3.org/2000/svg"><animate/></svg>',
            "event": b'<svg xmlns="http://www.w3.org/2000/svg" onload="x"/>',
            "doctype": b'<!DOCTYPE svg [<!ENTITY x SYSTEM "file:///x">]><svg xmlns="http://www.w3.org/2000/svg"/>',
            "import": b'<svg xmlns="http://www.w3.org/2000/svg"><style>@import "x";</style></svg>',
            "css-url": b'<svg xmlns="http://www.w3.org/2000/svg"><style>rect{fill:url(x)}</style></svg>',
            "network": b'<svg xmlns="http://www.w3.org/2000/svg"><image href="https://example.com/x"/></svg>',
            "local": b'<svg xmlns="http://www.w3.org/2000/svg"><image href="file:///c:/x"/></svg>',
            "data": b'<svg xmlns="http://www.w3.org/2000/svg"><image href="data:text/html,x"/></svg>',
            "namespace": b'<svg xmlns="http://www.w3.org/2000/svg"><x:bad xmlns:x="urn:x"/></svg>',
            "unknown": b'<svg xmlns="http://www.w3.org/2000/svg"><iframe/></svg>',
            "external-ref": b'<svg xmlns="http://www.w3.org/2000/svg"><use href="other.svg#x"/></svg>',
            "processing-instruction": b'<?xml-stylesheet href="x.css"?><svg xmlns="http://www.w3.org/2000/svg"/>',
            "non-svg-root": b'<rect xmlns="http://www.w3.org/2000/svg" width="2" height="3"/>',
            "unknown-css": b'<svg xmlns="http://www.w3.org/2000/svg"><style>rect{behavior:evil}</style></svg>',
        }
        for name, data in attacks.items():
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "UNSAFE_SVG"):
                inspect_passive_svg(data)

    def test_render_media_rejects_active_html_without_invoking_process(self):
        html = self.root / "app.html"; html.write_text("<html></html>", encoding="utf-8")
        with mock.patch("render_media.subprocess.run") as run, self.assertRaisesRegex(ValueError, "NO_QUALIFIED_RENDERER"):
            render_media(html, self.root / "out.png", renderer_executable=self.root / "msedge.exe", media_id="R", role="candidate_full", bindings=[])
        run.assert_not_called()

    def test_renderer_owned_receipt_records_fixed_provenance_and_registers(self):
        fixture = make_form_ready_workspace(self.root / "fixture", complete=False)
        output, receipt_path = self._render_receipt()
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        self.assertEqual("passive_static_only", receipt["adapter_class"])
        self.assertIn("not an active-code sandbox", receipt["adapter_security_scope"])
        self.assertEqual([str((self.root / "msedge.exe").resolve()), "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check", "--user-data-dir=<TEMP_PROFILE>", "--screenshot=<OUTPUT_FILE>", "--window-size=2,3", "<INPUT_FILE>"], receipt["argv"])
        self.assertFalse({"model_code_treated_as_untrusted", "isolated", "network_disabled", "credentials_absent", "disposable_copy", "minimal_permissions"} & set(receipt))
        result = register_render(fixture.outer, output, receipt_path)
        self.assertEqual("REN-full", result["media_id"])

    def test_register_render_rejects_caller_dict_tamper_and_out_of_bounds_crop(self):
        fixture = make_form_ready_workspace(self.root / "fixture", complete=False)
        output, receipt_path = self._render_receipt()
        with self.assertRaisesRegex(ValueError, "RENDERER_OWNED_RECEIPT_REQUIRED"):
            register_render(fixture.outer, output, json.loads(receipt_path.read_text(encoding="utf-8")))
        register_render(fixture.outer, output, receipt_path)
        crop_output, crop_receipt = self._render_receipt(media_id="REN-crop", role="candidate_crop", parent="REN-full", crop={"x": 1, "y": 1, "width": 2, "height": 3})
        with self.assertRaisesRegex(ValueError, "CROP_OUT_OF_BOUNDS"):
            register_render(fixture.outer, crop_output, crop_receipt)
        receipt_path.write_text(receipt_path.read_text(encoding="utf-8").replace("Edge 1", "fake"), encoding="utf-8")
        with self.assertRaises(ValueError):
            register_render(fixture.outer, output, receipt_path)

    def test_registration_rejects_renderer_executable_changed_after_receipt(self):
        fixture = make_form_ready_workspace(self.root / "fixture", complete=False)
        output, receipt_path = self._render_receipt()
        (self.root / "msedge.exe").write_bytes(b"changed executable")
        with self.assertRaisesRegex(ValueError, "RENDERER_DIGEST_MISMATCH"):
            register_render(fixture.outer, output, receipt_path)


if __name__ == "__main__":
    unittest.main()
