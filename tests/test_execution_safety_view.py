"""Read-only execution-safety telemetry in the viewer."""
import tempfile
import unittest
from pathlib import Path

from scripts import agent_office


class ExecutionSafetyViewTests(unittest.TestCase):
    def test_payload_shape(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            p = agent_office.execution_safety_payload(root)
        for key in ("enforcement_enabled", "kill_switch", "gate_verdict",
                    "authorization", "advisor_state", "read_only"):
            self.assertIn(key, p)
        self.assertTrue(p["read_only"])

    def test_route_is_read_only(self):
        src = Path(agent_office.__file__).read_text(encoding="utf-8")
        self.assertIn('route == "/api/execution-safety"', src)
        self.assertNotIn("do_POST", src)

    def test_trading_view_renders_panel(self):
        html = Path("web/agent-office/trading.html").read_text(encoding="utf-8")
        self.assertIn("execution-safety", html)
        self.assertIn("/api/execution-safety", html)


if __name__ == "__main__":
    unittest.main()
