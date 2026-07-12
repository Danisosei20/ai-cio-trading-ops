from __future__ import annotations

import argparse
import json
from pathlib import Path

from .database import CioDatabase
from .privacy import create_safe_support_bundle
from .runtime import build_settings
from .slack_replies import parse_safe_reply, reply_acknowledgement, transition_for_reply


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="cio", description="Read-only and maintenance commands for AI CIO.")
    parser.add_argument("--config", default="config/approval_routes.json")
    parser.add_argument("--env-file", default=".env")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("health")
    sub.add_parser("approvals")
    sub.add_parser("dashboard")
    sub.add_parser("lifecycles")
    migrate = sub.add_parser("migrate")
    migrate.add_argument("--backup")
    daily = sub.add_parser("daily-review")
    daily.add_argument("--account-label", required=True)
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

    args = parser.parse_args(argv)
    settings = build_settings(args.config, args.env_file)
    database = CioDatabase(settings.database_path)
    if args.command == "health":
        print(json.dumps({
            "database": database.integrity_check(), "mode": settings.mode,
            "live_trading_enabled": settings.trading_enabled,
        }, indent=2))
    elif args.command == "approvals":
        print(json.dumps(database.list_approvals(), indent=2))
    elif args.command == "dashboard":
        from scripts.dashboard import render_dashboard
        render_dashboard(database, settings.dashboard_path)
        print(settings.dashboard_path)
    elif args.command == "lifecycles":
        print(json.dumps(database.list_trade_lifecycles(), indent=2))
    elif args.command == "migrate":
        from .migrations import migrate_database
        print(json.dumps(migrate_database(settings.database_path, backup_path=args.backup), indent=2))
    elif args.command == "daily-review":
        from .orchestrator import DailyOrchestrator
        result = DailyOrchestrator(database, settings).run_daily(args.account_label, lambda: [])
        print(json.dumps(result.__dict__, indent=2))
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
