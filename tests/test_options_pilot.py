import pytest

import scripts.options_pilot as options_pilot


def test_dry_run_reaches_setup_after_initializing_timestamp(monkeypatch):
    def fail_setup(*args, **kwargs):
        raise RuntimeError("setup sentinel")

    monkeypatch.setattr(options_pilot, "build_settings", fail_setup)
    monkeypatch.setattr(options_pilot, "record_preentry_block", lambda *args, **kwargs: None)

    with pytest.raises(RuntimeError, match="setup sentinel"):
        options_pilot.main(["--dry-run"])
