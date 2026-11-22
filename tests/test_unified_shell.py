"""Static guards for the unified shell markup and navigation contract."""
import unittest
from pathlib import Path


class ShellMarkupTests(unittest.TestCase):
    def test_required_ids_and_nav_views(self):
        html = Path("web/agent-office/shell.html").read_text(encoding="utf-8")
        for ident in ("shell-nav", "shell-view", "shell-safety", "shell-safety-badge", "shell-safety-note", "shell-title"):
            self.assertIn(f'id="{ident}"', html, ident)
        for view in ("office", "market", "pipeline", "overview"):
            self.assertIn(f'data-view="{view}"', html, view)
        self.assertIn("<iframe", html)
