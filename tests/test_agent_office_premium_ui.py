"""Static guards: the premium redesign must keep every JS/API contract."""
import unittest
from pathlib import Path

BASE = Path("web/agent-office")
IDS = (
    "clock", "session", "safety-badge", "mode", "demo", "office", "scene-note",
    "motion", "zoom-out", "zoom-in", "fit", "fullscreen", "team", "safety-entries",
    "safety-mode", "safety-account", "safety-cert", "safety-recon",
    "safety-protective", "safety-alerts", "safety-note", "connection-dot",
    "portrait", "detail-role", "detail-title", "detail-copy", "detail-state",
    "journal-tag", "journal", "run-label", "completion", "progress", "run-id",
    "connection", "subtitle",
)


class PremiumUIGuards(unittest.TestCase):
    def setUp(self):
        self.html = (BASE / "index.html").read_text(encoding="utf-8")
        self.css = (BASE / "office.css").read_text(encoding="utf-8")
        self.js = (BASE / "office.js").read_text(encoding="utf-8")

    def test_every_dom_id_the_js_uses_exists(self):
        for ident in IDS:
            self.assertIn(f'id="{ident}"', self.html, ident)

    def test_single_accent_and_no_green_badge(self):
        self.assertIn("--accent: #6fd6c8", self.css)
        self.assertNotIn("safety-badge.ok", self.css)
        self.assertNotIn("safety-badge.green", self.css)

    def test_fresh_motion_is_gated_and_reduces(self):
        self.assertIn("@media (prefers-reduced-motion: no-preference)", self.css)
        self.assertIn("@media (prefers-reduced-motion: reduce)", self.css)

    def test_safety_panel_state_hook_wired(self):
        self.assertIn("dataset.entries", self.js)
        self.assertIn('[data-entries="active"]', self.css)

    def test_demo_and_disconnect_reset_posture_to_unknown(self):
        # The panel state must never contradict an UNKNOWN badge.
        head = self.js.split("if(demo){")[0]
        self.assertIn("dataset.entries='unknown'", head)
        self.assertIn('panel.dataset.entries=e===\'PAUSED\'', self.js)

    def test_scene_stage_and_viewport_kept(self):
        self.assertIn('class="stage"', self.html)
        self.assertIn('id="office"', self.html)
        self.assertIn(".viewport", self.css)
