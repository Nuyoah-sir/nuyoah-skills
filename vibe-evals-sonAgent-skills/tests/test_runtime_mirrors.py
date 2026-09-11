"""Every shipped skill must carry byte-identical runtime mirrors of shared code."""

import hashlib
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHARED_SCRIPTS = ROOT / "shared" / "scripts"
SHARED_SCHEMA = ROOT / "shared" / "schema"
SHARED_REFERENCES = ROOT / "shared" / "references"

# skill -> how each mirrored directory maps back to the canonical source
SCRIPT_MIRRORS = {
    "vibe-evals-son-complete-eval": SHARED_SCRIPTS,
    "vibe-evals-son-evidence-export": SHARED_SCRIPTS,
    "vibe-evals-son-evidence-supplement": SHARED_SCRIPTS,
    "vibe-evals-bundle-finalize": SHARED_SCRIPTS,
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _runtime_files(directory: Path) -> dict[str, Path]:
    return {
        path.relative_to(directory).as_posix(): path
        for path in directory.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    }


class RuntimeMirrorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.temp_dir = Path(self.temp.name)
    def test_every_mirrored_script_matches_its_shared_source(self):
        for skill, source in SCRIPT_MIRRORS.items():
            with self.subTest(skill=skill):
                scripts = ROOT / skill / "scripts"
                if not scripts.is_dir():
                    self.skipTest(f"{skill} ships no scripts directory")
                for relative, path in sorted(_runtime_files(scripts).items()):
                    canonical = source / relative
                    self.assertTrue(canonical.is_file(), f"{skill}/scripts/{relative} has no shared source")
                    self.assertEqual(_sha256(canonical), _sha256(path), f"{skill}/scripts/{relative} drifted from shared")

    def test_complete_eval_ships_every_script_it_needs_to_import(self):
        skill = ROOT / "vibe-evals-son-complete-eval"
        scripts = skill / "scripts"
        self.assertTrue(scripts.is_dir())
        for name in ("artifact_integrity.py", "validate_bundle.py", "initialize_bundle.py", "package_bundle.py",
                     "form_ready_context.py", "validate_form_ready.py", "package_form_ready.py", "complete_eval.py",
                     "record_review.py", "record_evidence.py", "v21_labels.py"):
            with self.subTest(script=name):
                self.assertTrue((scripts / name).is_file(), f"{name} must be mirrored into the skill")

    def test_schemas_and_references_match_their_shared_sources(self):
        skill = ROOT / "vibe-evals-son-complete-eval"
        for relative, path in sorted(_runtime_files(skill / "schemas").items()):
            canonical = SHARED_SCHEMA / relative
            if not canonical.is_file():
                continue  # v2.1-only schema shipped with the local form renderer
            with self.subTest(schema=relative):
                self.assertEqual(_sha256(canonical), _sha256(path), f"schemas/{relative} drifted from shared")
        for name in ("gap-policy.json", "v21-labels.json"):
            with self.subTest(reference=name):
                self.assertEqual(_sha256(SHARED_REFERENCES / name), _sha256(skill / "references" / name))

    def test_import_closure_resolves_from_an_installed_layout(self):
        """An installed skill must import cleanly with only its own scripts on the path."""

        skill = ROOT / "vibe-evals-son-complete-eval"
        scripts = (skill / "scripts").resolve()
        probe = (
            "import importlib, io, contextlib, sys\n"
            f"sys.path.insert(0, {str(scripts)!r})\n"
            f"modules = sorted(p.stem for p in __import__('pathlib').Path({str(scripts)!r}).glob('*.py'))\n"
            "bad = []\n"
            "for name in modules:\n"
            "    try:\n"
            "        with contextlib.redirect_stdout(io.StringIO()):\n"
            "            module = importlib.import_module(name)\n"
            "        origin = getattr(module, '__file__', '') or ''\n"
            "        if not origin.startswith(" + repr(str(scripts)) + "):\n"
            "            bad.append((name, origin))\n"
            "    except BaseException as exc:\n"
            "        bad.append((name, f'{type(exc).__name__}: {exc}'))\n"
            "print(len(modules), bad)\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", probe],
            capture_output=True, text=True, check=False, cwd=str(self.temp_dir),
            env={key: value for key, value in os.environ.items() if key not in {"PYTHONPATH", "PYTHONHOME"}},
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("[]", result.stdout, f"skill imports leaked outside its own tree: {result.stdout}{result.stderr}")

    def test_skill_entrypoint_declares_the_remote_complete_eval_contract(self):
        text = (ROOT / "vibe-evals-son-complete-eval" / "SKILL.md").read_text(encoding="utf-8")
        self.assertTrue(text.startswith("---\n"))
        description = next(line for line in text.splitlines() if line.startswith("description: "))
        self.assertTrue(description.split("description: ", 1)[1].startswith("Use when"))
        for required in ("FORM_READY_SEALED", "ready_for_form", "complete_eval.py start"):
            with self.subTest(token=required):
                self.assertIn(required, text)


if __name__ == "__main__":
    unittest.main()
