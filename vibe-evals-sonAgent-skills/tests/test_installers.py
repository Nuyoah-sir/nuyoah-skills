"""Installer behaviour: exact skill sets, preflight atomicity, and rollback."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
THIRD_PARTY = ROOT / "install-third-party.ps1"
LOCAL = ROOT / "install-local.ps1"
THIRD_PARTY_SKILLS = (
    "vibe-evals-son-evidence-export",
    "vibe-evals-son-evidence-supplement",
    "vibe-evals-son-complete-eval",
)
LOCAL_SKILLS = ("vibe-evals-bundle-finalize",)
POWERSHELL = shutil.which("pwsh") or shutil.which("powershell")


@unittest.skipIf(POWERSHELL is None, "PowerShell is not available")
class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.skills_root = self.root / "skills"

    def _install(self, script: Path, *extra: str) -> subprocess.CompletedProcess:
        command = [POWERSHELL, "-NoProfile", "-NonInteractive", "-File", str(script), "-SkillsRoot", str(self.skills_root), *extra]
        # PowerShell output on this host mixes codepages, so decode defensively.
        return subprocess.run(command, capture_output=True, text=True, check=False, cwd=str(ROOT),
                              encoding="utf-8", errors="replace",
                              env={**os.environ, "PYTHONUTF8": "1"})

    def _installed(self) -> list[str]:
        if not self.skills_root.is_dir():
            return []
        return sorted(path.name for path in self.skills_root.iterdir() if path.is_dir())

    def _output(self, result: subprocess.CompletedProcess) -> str:
        return (result.stdout or "") + (result.stderr or "")

    def test_third_party_installer_installs_exactly_the_three_remote_skills(self):
        result = self._install(THIRD_PARTY)
        self.assertEqual(0, result.returncode, self._output(result))
        self.assertEqual(sorted(THIRD_PARTY_SKILLS), self._installed())
        for name in THIRD_PARTY_SKILLS:
            with self.subTest(skill=name):
                self.assertTrue((self.skills_root / name / "SKILL.md").is_file())
                self.assertTrue((self.skills_root / name / "runtime-manifest.sha256").is_file())
        self.assertFalse((self.skills_root / "vibe-evals-bundle-finalize").exists())

    def test_local_installer_installs_exactly_the_finalizer(self):
        result = self._install(LOCAL)
        self.assertEqual(0, result.returncode, self._output(result))
        self.assertEqual(sorted(LOCAL_SKILLS), self._installed())
        self.assertTrue((self.skills_root / "vibe-evals-bundle-finalize" / "scripts" / "verify_and_render_form_ready.py").is_file())

    def test_a_missing_third_skill_fails_preflight_without_installing_anything(self):
        hidden = ROOT / "vibe-evals-son-complete-eval.hidden"
        source = ROOT / "vibe-evals-son-complete-eval"
        source.rename(hidden)
        try:
            result = self._install(THIRD_PARTY)
        finally:
            hidden.rename(source)

        self.assertNotEqual(0, result.returncode)
        self.assertIn("Distribution is incomplete", self._output(result))
        self.assertEqual([], self._installed(), "preflight failure must not install any skill")

    def test_existing_destination_requires_force_and_force_upgrades_in_place(self):
        (self.skills_root / "vibe-evals-son-evidence-export").mkdir(parents=True)
        stale = self.skills_root / "vibe-evals-son-evidence-export" / "stale.txt"
        stale.write_text("stale", encoding="utf-8")

        refused = self._install(THIRD_PARTY)
        self.assertNotEqual(0, refused.returncode)
        self.assertTrue(stale.is_file(), "a refused run must not touch the installed skill")
        self.assertEqual(["vibe-evals-son-evidence-export"], self._installed())

        forced = self._install(THIRD_PARTY, "-Force")
        self.assertEqual(0, forced.returncode, self._output(forced))
        self.assertEqual(sorted(THIRD_PARTY_SKILLS), self._installed())
        self.assertFalse(stale.exists(), "the upgraded skill must replace the stale directory")
        backups = self.root / "codex-skill-backups"
        self.assertTrue(backups.is_dir(), "an upgrade must keep a backup")
        self.assertTrue(any("vibe-evals-son-evidence-export" in path.name for path in backups.iterdir()))

    def test_a_missing_runtime_resource_is_rejected_before_install(self):
        victim = ROOT / "vibe-evals-son-complete-eval" / "scripts" / "record_observation.py"
        stash = ROOT / "vibe-evals-son-complete-eval" / "scripts" / "record_observation.py.stash"
        victim.rename(stash)
        try:
            result = self._install(THIRD_PARTY)
        finally:
            stash.rename(victim)

        self.assertNotEqual(0, result.returncode)
        output = self._output(result)
        self.assertTrue("MANIFEST_FILE_MISSING" in output or "Runtime manifest check failed" in output, output)
        self.assertEqual([], self._installed())

    def test_failure_while_swapping_the_second_skill_rolls_the_first_one_back(self):
        result = self._install(THIRD_PARTY, "-FailAfterSkill", THIRD_PARTY_SKILLS[1])
        self.assertNotEqual(0, result.returncode)
        self.assertIn("Diagnostic failure requested", self._output(result))
        self.assertEqual([], self._installed(), "a mid-swap failure must leave no installed skill behind")
        staging = sorted(path.name for path in self.root.iterdir() if path.name.startswith(".codex-skill-staging-"))
        self.assertEqual([], staging, "invocation-owned staging must be removed")

    def test_failure_while_upgrading_restores_the_previous_install(self):
        first = self._install(LOCAL)
        self.assertEqual(0, first.returncode, self._output(first))
        installed_skill = self.skills_root / "vibe-evals-bundle-finalize"
        marker = installed_skill / "SKILL.md"
        before = marker.read_bytes()

        result = self._install(LOCAL, "-Force", "-FailAfterSkill", "vibe-evals-bundle-finalize")
        self.assertNotEqual(0, result.returncode)
        self.assertTrue(marker.is_file(), "the previous install must be restored")
        self.assertEqual(before, marker.read_bytes())

    def test_third_party_installer_never_touches_the_finalizer_directory(self):
        finalizer = self.skills_root / "vibe-evals-bundle-finalize"
        finalizer.mkdir(parents=True)
        sentinel = finalizer / "keep.txt"
        sentinel.write_text("keep", encoding="utf-8")

        result = self._install(THIRD_PARTY, "-Force")
        self.assertEqual(0, result.returncode, self._output(result))
        self.assertTrue(sentinel.is_file())
        self.assertEqual("keep", sentinel.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
