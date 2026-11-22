"""Guard the live UI brand name: Nextus, never Nexus.

Historical plan/spec files are deliberately out of scope — this covers only
files the running viewer serves or that document the shipped UI.
"""
import unittest
from pathlib import Path

LIVE_FILES = (
    "web/agent-office/index.html",
    "web/agent-office/market.html",
    "web/agent-office/shell.html",
    "web/agent-office/market.js",
    "web/agent-office/scene.js",
    "web/agent-office/README.md",
    "scripts/desk_safety_status.py",
)


class BrandNameTests(unittest.TestCase):
    def test_live_ui_uses_nextus(self):
        for rel in LIVE_FILES:
            text = Path(rel).read_text(encoding="utf-8")
            self.assertIn("Nextus", text, rel)

    def test_live_ui_has_no_nexus(self):
        for rel in LIVE_FILES:
            text = Path(rel).read_text(encoding="utf-8")
            self.assertNotIn("nexus", text.lower(), rel)

    def test_scene_wall_label_is_nextus(self):
        scene = Path("web/agent-office/scene.js").read_text(encoding="utf-8")
        self.assertIn("N E X T U S", scene)
