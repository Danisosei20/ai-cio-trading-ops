from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from robinhood_tools.errors import PolicyViolation
from robinhood_tools.settings import load_config


class PersonalSettingsTests(unittest.TestCase):
    def test_personal_env_file_is_listed_in_gitignore(self):
        root = Path(__file__).resolve().parents[1]
        ignored = root.joinpath(".gitignore").read_text().splitlines()
        self.assertIn(".env", ignored)
        self.assertTrue(root.joinpath(".env.example").exists())

    def test_resolves_personal_variables_and_preserves_numeric_types(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config.json"
            env = root / ".env"
            config.write_text(json.dumps({"channel": "${CHANNEL}", "minutes": "${MINUTES}"}))
            env.write_text("CHANNEL=C123\nMINUTES=120\n")
            resolved = load_config(config, env)
            self.assertEqual(resolved["channel"], "C123")
            self.assertEqual(resolved["minutes"], 120)

    def test_missing_required_personal_variable_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config.json"
            config.write_text(json.dumps({"channel": "${CHANNEL}"}))
            with self.assertRaisesRegex(PolicyViolation, "CHANNEL"):
                load_config(config, root / ".env")


if __name__ == "__main__":
    unittest.main()
