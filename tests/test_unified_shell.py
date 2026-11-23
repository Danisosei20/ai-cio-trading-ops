"""Static guards for the unified shell markup and navigation contract."""
import re
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

    def test_options_nav_button_present(self):
        html = Path("web/agent-office/shell.html").read_text(encoding="utf-8")
        self.assertIn('data-view="options"', html)
        self.assertIn(">Options</button>", html)

    def test_options_nav_is_unique(self):
        html = Path("web/agent-office/shell.html").read_text(encoding="utf-8")
        self.assertEqual(html.count('data-view="options"'), 1)
        js = Path("web/agent-office/shell.js").read_text(encoding="utf-8")
        self.assertEqual(js.count('options: "/options"'), 1)
        self.assertEqual(js.count('options: "Options"'), 1)

    def test_basket_nav_button_present(self):
        html = Path("web/agent-office/shell.html").read_text(encoding="utf-8")
        self.assertIn('data-view="basket"', html)
        self.assertIn(">Basket</button>", html)

    def test_basket_nav_is_unique(self):
        html = Path("web/agent-office/shell.html").read_text(encoding="utf-8")
        self.assertEqual(html.count('data-view="basket"'), 1)
        js = Path("web/agent-office/shell.js").read_text(encoding="utf-8")
        self.assertEqual(len(re.findall(r'basket:\s*"/basket"', js)), 1)
        self.assertEqual(len(re.findall(r'basket:\s*"Basket"', js)), 1)


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

    def test_unknown_view_falls_back_to_office(self):
        js = Path("web/agent-office/shell.js").read_text(encoding="utf-8")
        self.assertIn("office", js)
        self.assertIn("data-view", js)

    def test_options_view_maps_to_read_only_page(self):
        js = Path("web/agent-office/shell.js").read_text(encoding="utf-8")
        # VIEW_SRC sends the tab to the read-only /options page; VIEW_TITLE
        # gives it a human label. No other behavior should change.
        self.assertRegex(js, r'options:\s*"/options"')
        self.assertRegex(js, r'options:\s*"Options"')

    def test_basket_view_maps_to_read_only_page(self):
        js = Path("web/agent-office/shell.js").read_text(encoding="utf-8")
        # VIEW_SRC sends the tab to the signal-only /basket page; VIEW_TITLE
        # gives it a human label. No other behavior should change.
        self.assertRegex(js, r'basket:\s*"/basket"')
        self.assertRegex(js, r'basket:\s*"Basket"')

