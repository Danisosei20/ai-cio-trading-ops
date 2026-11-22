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


class ShellRouteTests(unittest.TestCase):
    def test_read_only_server(self):
        src = Path("scripts/agent_office.py").read_text(encoding="utf-8")
        self.assertNotIn("do_POST", src)

    def test_standalone_routes_preserved(self):
        src = Path("scripts/agent_office.py").read_text(encoding="utf-8")
        for route in ('"/"', '"/trading"', '"/pipeline"', '"/market"', '"/shell"', '"/shell.js"'):
            self.assertIn(route, src)


class ShellBehaviorTests(unittest.TestCase):
    def test_nav_switches_iframe_only(self):
        js = Path("web/agent-office/shell.js").read_text(encoding="utf-8")
        self.assertIn("shell-view", js)
        self.assertIn("iframe", js)
        self.assertNotIn("location.reload", js)
        self.assertNotIn("location.href =", js)

    def test_safety_ribbon_fails_closed(self):
        js = Path("web/agent-office/shell.js").read_text(encoding="utf-8")
        for token in ("api/safety", "SAFETY UNKNOWN", "unknown", "entries"):
            self.assertIn(token, js)

    def test_unknown_view_falls_back_to_office(self):
        js = Path("web/agent-office/shell.js").read_text(encoding="utf-8")
        self.assertIn("office", js)
        self.assertIn("data-view", js)

