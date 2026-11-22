"""Behavioral tests for the fail-closed shell safety classifier.

The real classifier (web/agent-office/shell_safety.js) is driven through Node
so that any edit inverting a fail-closed condition fails CI, instead of relying
on substring assertions and a one-time manual browser check.
"""
from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLASSIFIER = ROOT / "web" / "agent-office" / "shell_safety.js"

HARNESS = r"""
const { classifySafety } = require(process.argv[1]);
const fs = require('fs');
const lines = fs.readFileSync(0, 'utf8').split('\n').filter(Boolean);
for (const line of lines) console.log(JSON.stringify(classifySafety(JSON.parse(line))));
"""

UNKNOWN = {
    "text": "SAFETY UNKNOWN",
    "cls": "safety-badge unknown",
    "note": "status not verified",
}
ACTIVE = {
    "text": "ENTRIES ACTIVE",
    "cls": "safety-badge active",
    "note": "mode unknown · account unknown",
}
PAUSED = {
    "text": "ENTRIES PAUSED",
    "cls": "safety-badge paused",
    "note": "mode unknown · account unknown",
}
ACTIVE_CONFIGURED = {
    "text": "ENTRIES ACTIVE",
    "cls": "safety-badge active",
    "note": "mode PAPER ONLY · account FLAT",
}

# (label, payload, exact expected {text, cls, note})
CASES = [
    ("null", None, UNKNOWN),
    ("empty object", {}, UNKNOWN),
    ("unknown entries", {"entries": "UNKNOWN"}, UNKNOWN),
    ("null entries", {"entries": None}, UNKNOWN),
    ("array entries not coerced", {"entries": ["ACTIVE"]}, UNKNOWN),
    ("lowercase active", {"entries": "active"}, ACTIVE),
    ("array status not coerced", {"status": ["ok"], "entries": "ACTIVE"}, UNKNOWN),
    ("halt status", {"status": "halt", "entries": "ACTIVE"}, UNKNOWN),
    ("ok status active", {"status": "ok", "entries": "ACTIVE"}, ACTIVE),
    ("missing status active", {"entries": "ACTIVE"}, ACTIVE),
    ("paused", {"entries": "PAUSED"}, PAUSED),
    (
        "configured active",
        {"mode": "PAPER ONLY", "account": "FLAT", "entries": "ACTIVE"},
        ACTIVE_CONFIGURED,
    ),
]


class ShellSafetyBehaviorTest(unittest.TestCase):
    def _classify(self, payloads):
        payload_text = "\n".join(json.dumps(p) for p in payloads) + "\n"
        result = subprocess.run(
            ["node", "-e", HARNESS, str(CLASSIFIER)],
            input=payload_text,
            capture_output=True,
            encoding="utf-8",
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return [json.loads(line) for line in result.stdout.splitlines() if line.strip()]

    def test_safety_classifier_behavior(self):
        actual = self._classify([payload for _, payload, _ in CASES])
        self.assertEqual(len(actual), len(CASES))
        for (label, payload, expected), got in zip(CASES, actual):
            with self.subTest(case=label, payload=payload):
                self.assertEqual(got, expected)


class ShellSafetyStaticGuards(unittest.TestCase):
    def test_safety_script_served(self):
        src = (ROOT / "scripts" / "agent_office.py").read_text(encoding="utf-8")
        self.assertIn('"/shell_safety.js"', src)

    def test_no_write_endpoint(self):
        src = (ROOT / "scripts" / "agent_office.py").read_text(encoding="utf-8")
        self.assertNotIn("do_POST", src)

    def test_no_hardcoded_hex_in_shell_safety_css(self):
        css = (ROOT / "web" / "agent-office" / "office.css").read_text(encoding="utf-8")
        start = css.index("body.shell .shell-safety {")
        end = css.index("}", start)
        self.assertNotRegex(css[start:end], r"#[0-9A-Fa-f]{3,8}\b")


if __name__ == "__main__":
    unittest.main()
