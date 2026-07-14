from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "sync_ai_cio_skill.py"
SPEC = importlib.util.spec_from_file_location("sync_ai_cio_skill", SCRIPT)
assert SPEC and SPEC.loader
sync_ai_cio_skill = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sync_ai_cio_skill)


class SkillSyncTests(unittest.TestCase):
    def populate(self, root: Path, prefix: str) -> None:
        for relative_path in sync_ai_cio_skill.SYNCED_FILES:
            path = root / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"{prefix}:{relative_path}\n", encoding="utf-8")

    def test_from_installed_copies_only_allowlisted_skill_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            installed = root / "installed"
            repository = root / "repository"
            self.populate(installed, "installed")
            (installed / "memory.md").write_text("private", encoding="utf-8")
            (installed / ".env").write_text("TOKEN=secret", encoding="utf-8")
            cache = installed / "scripts" / "__pycache__" / "cache.pyc"
            cache.parent.mkdir(parents=True)
            cache.write_bytes(b"cache")

            result = sync_ai_cio_skill.main([
                "from-installed", "--installed", str(installed), "--repository", str(repository),
            ])

            self.assertEqual(result, 0)
            self.assertEqual(sync_ai_cio_skill.drift(installed, repository), [])
            self.assertFalse((repository / "memory.md").exists())
            self.assertFalse((repository / ".env").exists())
            self.assertFalse((repository / "scripts" / "__pycache__").exists())

    def test_check_reports_content_drift(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            installed = root / "installed"
            repository = root / "repository"
            self.populate(installed, "installed")
            self.populate(repository, "repository")

            self.assertEqual(sync_ai_cio_skill.main([
                "check", "--installed", str(installed), "--repository", str(repository),
            ]), 1)
            differences = sync_ai_cio_skill.drift(installed, repository)
            self.assertIn("content differs: SKILL.md", differences)

    def test_to_installed_restores_exact_parity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            installed = root / "installed"
            repository = root / "repository"
            self.populate(installed, "old")
            self.populate(repository, "repository")

            result = sync_ai_cio_skill.main([
                "to-installed", "--installed", str(installed), "--repository", str(repository),
            ])

            self.assertEqual(result, 0)
            self.assertEqual(sync_ai_cio_skill.drift(installed, repository), [])
            first_line = (installed / "SKILL.md").read_text(encoding="utf-8").splitlines()[0]
            self.assertEqual(first_line, "repository:SKILL.md")

    def test_sync_refuses_a_symlinked_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            installed = root / "installed"
            repository = root / "repository"
            self.populate(installed, "installed")
            repository.mkdir()
            outside = root / "outside"
            outside.write_text("do not replace", encoding="utf-8")
            (repository / "SKILL.md").symlink_to(outside)

            with self.assertRaisesRegex(ValueError, "symlinked skill file"):
                sync_ai_cio_skill.sync(installed, repository)
            self.assertEqual(outside.read_text(encoding="utf-8"), "do not replace")


if __name__ == "__main__":
    unittest.main()
