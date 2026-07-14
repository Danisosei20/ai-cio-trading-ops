from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .database import CioDatabase
from .privacy import create_safe_support_bundle
from .runtime import build_settings
from .settings import load_env
from .slack_replies import parse_safe_reply, reply_acknowledgement, transition_for_reply


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="cio", description="Read-only and maintenance commands for AI CIO.")
    parser.add_argument("--config", default="config/approval_routes.json")
    parser.add_argument("--env-file", default=".env")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("health")
    sub.add_parser("paper-broker-health")
    sub.add_parser("operations-status")
    sub.add_parser("approvals")
    sub.add_parser("dashboard")
    sub.add_parser("lifecycles")
    experiments = sub.add_parser("research-experiments")
    experiments.add_argument("--limit", type=int, default=25)
    migrate = sub.add_parser("migrate")
    migrate.add_argument("--backup")
    migrate.add_argument("--database")
    daily = sub.add_parser("daily-review")
    daily.add_argument("--account-label", required=True)
    watchdog = sub.add_parser("watchdog")
    watchdog.add_argument("--account-label", required=True)
    watchdog.add_argument("--date")
    watchdog.add_argument("--scheduled-time")
    watchdog.add_argument("--database")
    watchdog.add_argument("--notify", action="store_true")
    sub.add_parser("recovery-plan")
    shadow = sub.add_parser("shadow-recommendations")
    shadow.add_argument("--limit", type=int, default=25)
    backup = sub.add_parser("backup")
    backup.add_argument("destination")
    bundle = sub.add_parser("support-bundle")
    bundle.add_argument("destination")
    reply = sub.add_parser("process-slack-reply")
    reply.add_argument("--channel-id", required=True)
    reply.add_argument("--message-ts", required=True)
    reply.add_argument("--text", required=True)
    reject = sub.add_parser("reject-approval")
    reject.add_argument("approval_id")
    acknowledged = sub.add_parser("mark-slack-reply-acknowledged")
    acknowledged.add_argument("--channel-id", required=True)
    acknowledged.add_argument("--message-ts", required=True)
    sub.add_parser("expire-reply-windows")
    sub.add_parser("clean-reply-windows")
    sub.add_parser("emergency-stop")
    sub.add_parser("emergency-resume")
    audit = sub.add_parser("export-audit")
    audit.add_argument("destination")

    args = parser.parse_args(argv)
    settings = build_settings(args.config, args.env_file)
    database = CioDatabase(settings.database_path)
    if args.command == "health":
        print(json.dumps({
            "database": database.integrity_check(), "mode": settings.mode,
            "live_trading_enabled": settings.trading_enabled,
        }, indent=2))
    elif args.command == "paper-broker-health":
        from .alpaca_paper import AlpacaPaperBackend, AlpacaPaperHttpTransport
        from .errors import RobinhoodToolError
        try:
            settings.require_paper_trading()
            values = {**load_env(args.env_file), **os.environ}
            accounts = AlpacaPaperBackend(AlpacaPaperHttpTransport.from_values(values)).list_accounts()
        except RobinhoodToolError as exc:
            print(json.dumps({
                "broker": "alpaca", "environment": "paper", "connected": False,
                "error": str(exc),
            }, indent=2))
            return 2
        print(json.dumps({
            "broker": "alpaca", "environment": "paper", "connected": True,
            "accounts": [
                {"label": account.label, "masked_account_number": account.masked_account_number}
                for account in accounts
            ],
        }, indent=2))
    elif args.command == "operations-status":
        from .observability import evaluate_operational_status
        status = evaluate_operational_status(database)
        print(json.dumps(status.as_dict(), indent=2))
        return {"healthy": 0, "safe_stopped": 1, "degraded": 1, "critical": 2}[status.state]
    elif args.command == "approvals":
        print(json.dumps(database.list_approvals(), indent=2))
    elif args.command == "dashboard":
        from scripts.dashboard import render_dashboard
        render_dashboard(database, settings.dashboard_path)
        print(settings.dashboard_path)
    elif args.command == "lifecycles":
        print(json.dumps(database.list_trade_lifecycles(), indent=2))
    elif args.command == "research-experiments":
        print(json.dumps(database.list_research_experiments(args.limit), indent=2))
    elif args.command == "migrate":
        from .migrations import migrate_database
        target_database = Path(args.database) if args.database else settings.database_path
        print(json.dumps(migrate_database(target_database, backup_path=args.backup), indent=2))
    elif args.command == "daily-review":
        from .orchestrator import DailyOrchestrator
        result = DailyOrchestrator(database, settings).run_daily(args.account_label, lambda: [])
        print(json.dumps(result.__dict__, indent=2))
    elif args.command == "watchdog":
        from .daily_controls import check_daily_run_watchdog
        from .health import SlackHealthWebApiNotifier

        watchdog_database = CioDatabase(args.database) if args.database else database
        tz = ZoneInfo(settings.timezone)
        run_day = date.fromisoformat(args.date) if args.date else datetime.now(tz).date()
        hour, minute = map(int, (args.scheduled_time or settings.schedule_time_local).split(":"))
        scheduled_for = datetime(run_day.year, run_day.month, run_day.day, hour, minute, tzinfo=tz)
        watchdog_result = check_daily_run_watchdog(
            watchdog_database, account_label=args.account_label, scheduled_for=scheduled_for,
            grace=timedelta(minutes=settings.watchdog_grace_minutes),
        )
        payload = asdict(watchdog_result)
        if watchdog_result.alert_required and args.notify:
            alert_key = f"missed-daily-run:{watchdog_result.run_key}"
            if watchdog_database.claim_health_alert(alert_key):
                try:
                    values = load_env(args.env_file)
                    values.setdefault("HEALTH_SLACK_CHANNEL_ID", settings.health_channel_id)
                    SlackHealthWebApiNotifier.from_values(values).send_health_alert(
                        message=(
                            f"AI CIO health alert: {watchdog_result.detail} "
                            f"Run key: {watchdog_result.run_key}."
                        )
                    )
                    watchdog_database.complete_health_alert(alert_key, sent=True)
                    payload["health_alert"] = "sent"
                except Exception as exc:
                    watchdog_database.complete_health_alert(alert_key, sent=False, error=type(exc).__name__)
                    payload["health_alert"] = "failed"
                    print(json.dumps(payload, indent=2))
                    return 3
            else:
                payload["health_alert"] = "already_sent"
        print(json.dumps(payload, indent=2))
        if watchdog_result.alert_required:
            return 2
    elif args.command == "recovery-plan":
        from .operations import build_recovery_plan
        print(json.dumps(asdict(build_recovery_plan(database)), indent=2))
    elif args.command == "shadow-recommendations":
        print(json.dumps(database.shadow_recommendations(args.limit), indent=2))
    elif args.command == "backup":
        print(database.backup(args.destination))
    elif args.command == "support-bundle":
        print(create_safe_support_bundle(Path.cwd(), args.destination))
    elif args.command == "process-slack-reply":
        parsed = parse_safe_reply(args.text)
        transition = transition_for_reply(parsed)
        claimed = database.claim_slack_message(args.channel_id, args.message_ts, parsed.kind)
        print(json.dumps({
            "claimed": claimed, "kind": parsed.kind,
            "value": str(parsed.value) if parsed.value is not None else None,
            "state": transition.state,
            "needs_fresh_review": transition.needs_fresh_review,
            "acknowledgement": reply_acknowledgement(parsed),
        }))
    elif args.command == "reject-approval":
        record = database.reject(args.approval_id)
        print(json.dumps({"approval_id": record.approval_id, "status": record.status}))
    elif args.command == "mark-slack-reply-acknowledged":
        database.mark_slack_message_acknowledged(args.channel_id, args.message_ts)
        print(json.dumps({"acknowledged": True}))
    elif args.command == "expire-reply-windows":
        print(json.dumps({"rejected_approval_ids": database.reject_expired_reply_windows()}))
    elif args.command == "clean-reply-windows":
        print(json.dumps({"deleted_windows": database.cleanup_terminal_reply_windows()}))
    elif args.command == "emergency-stop":
        database.set_emergency_kill(True)
        print(json.dumps({"emergency_kill": "on"}))
    elif args.command == "emergency-resume":
        database.set_emergency_kill(False)
        print(json.dumps({"emergency_kill": "off"}))
    elif args.command == "export-audit":
        from .reporting import export_audit_bundle
        output, digest = export_audit_bundle(database.audit_export(), args.destination)
        print(json.dumps({"output": str(output), "sha256": digest}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
