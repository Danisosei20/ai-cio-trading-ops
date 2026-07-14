from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from robinhood_tools.errors import PolicyViolation
from robinhood_tools.settings import load_config, validate_config_shape


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

    def test_full_config_rejects_unknown_or_missing_fields(self):
        root = Path(__file__).resolve().parents[1]
        raw = json.loads(root.joinpath("config/approval_routes.example.json").read_text())
        raw["risk_limits"]["max_order_values_usd"] = 25
        with self.assertRaisesRegex(PolicyViolation, "unknown fields: max_order_values_usd"):
            validate_config_shape(raw)
        del raw["risk_limits"]["max_order_value_usd"]
        with self.assertRaisesRegex(PolicyViolation, "missing required fields: max_order_value_usd"):
            validate_config_shape(raw)


if __name__ == "__main__":
    unittest.main()
