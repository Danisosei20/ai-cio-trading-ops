"""Static guards for the safety panel markup/styles (no JS test runner)."""
import unittest
from pathlib import Path


class SafetyMarkupTest(unittest.TestCase):
    def setUp(self):
        self.html = Path("web/agent-office/index.html").read_text(encoding="utf-8")
        self.css = Path("web/agent-office/office.css").read_text(encoding="utf-8")

    def test_all_safety_ids_present(self):
        for ident in ("safety-badge", "safety-entries", "safety-mode",
                      "safety-account", "safety-cert", "safety-recon",
                      "safety-protective", "safety-alerts", "safety-note"):
            self.assertIn(f'id="{ident}"', self.html, ident)

    def test_safety_panel_precedes_inspector(self):
        self.assertLess(self.html.index('id="safety-entries"'),
                        self.html.index('id="detail-role"'))

    def test_css_has_badge_variants_and_narrow_reorder(self):
        for cls in (".safety-badge.paused", ".safety-badge.active",
                    ".safety-badge.unknown", ".safety"):
            self.assertIn(cls, self.css)
        self.assertIn("order: -1", self.css)


class SafetyRenderTest(unittest.TestCase):
    def setUp(self):
        self.js = Path("web/agent-office/office.js").read_text(encoding="utf-8")

    def test_render_safety_called_from_render_panel(self):
        self.assertIn("renderSafety(", self.js)

    def test_demo_guard_present(self):
        self.assertIn("DEMO — status not evaluated", self.js)

    def test_badge_classes_used(self):
        for cls in ("safety-badge paused", "safety-badge active",
                    "safety-badge unknown"):
            self.assertIn(cls, self.js)

    def test_disconnect_does_not_show_stale_posture(self):
        # A failed poll must clear the cached safety payload, and the renderer
        # must fail closed while the feed is down.
        self.assertIn("safety:undefined", self.js)
        body = self.js.split("function renderSafety")[1].split("function renderPanel")[0]
        self.assertIn("!connected", body)
        self.assertIn("Feed disconnected", body)
