.PHONY: test health dashboard ci lint typecheck audit skill-check skill-sync-from-installed skill-sync-to-installed

test:
	python3 -m unittest discover -s tests -v

health:
	python3 scripts/health_check.py --available-tools slack._slack_send_message --robinhood-read-ok

dashboard:
	python3 scripts/dashboard.py

ci:
	python3 -m unittest discover -s tests -v
	python3 scripts/secret_scan.py
	python3 -m json.tool config/approval_routes.example.json >/dev/null

lint:
	python3 -m ruff check robinhood_tools tests scripts

typecheck:
	python3 -m mypy robinhood_tools

audit:
	python3 -m pip_audit

skill-check:
	python3 scripts/sync_ai_cio_skill.py check

skill-sync-from-installed:
	python3 scripts/sync_ai_cio_skill.py from-installed

skill-sync-to-installed:
	python3 scripts/sync_ai_cio_skill.py to-installed
