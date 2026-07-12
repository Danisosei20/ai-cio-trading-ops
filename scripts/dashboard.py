#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from robinhood_tools.database import CioDatabase


def render_dashboard(database: CioDatabase, output_path: str | Path) -> Path:
    data = database.dashboard()
    data["recent_approvals"] = database.list_approvals(25)
    data["trade_lifecycles"] = database.list_trade_lifecycles()
    payload = html.escape(json.dumps(data, indent=2, sort_keys=True))
    rows = "".join(
        f"<tr><td>{html.escape(str(row['symbol']))}</td><td>{html.escape(str(row['status']))}</td>"
        f"<td>{html.escape(str(row['created_at']))}</td><td>{html.escape(str(row['expires_at']))}</td></tr>"
        for row in data["recent_approvals"]
    ) or "<tr><td colspan='4'>No approvals</td></tr>"
    lifecycle_rows = "".join(
        f"<tr><td>{html.escape(str(row['task_name']))}</td><td>{html.escape(str(row['status']))}</td>"
        f"<td>{html.escape(str(row['opened_at']))}</td><td>{html.escape(str(row['realized_profit'] or ''))}</td></tr>"
        for row in data["trade_lifecycles"]
    ) or "<tr><td colspan='4'>No trade lifecycles</td></tr>"
    document = f"""<!doctype html><html><head><meta charset='utf-8'><title>AI CIO Dashboard</title>
<style>body{{font:16px system-ui;max-width:1000px;margin:40px auto;padding:0 20px}}pre,table{{background:#f4f4f4;padding:20px}}table{{width:100%;border-collapse:collapse}}td,th{{padding:8px;text-align:left;border-bottom:1px solid #ccc}}</style>
</head><body><h1>AI CIO Dashboard</h1><p>Read-only local status.</p>
<h2>Recent approvals</h2><table><thead><tr><th>Symbol</th><th>Status</th><th>Created</th><th>Expires</th></tr></thead><tbody>{rows}</tbody></table>
<h2>Trade lifecycles</h2><table><thead><tr><th>Task</th><th>Status</th><th>Opened</th><th>Realized profit</th></tr></thead><tbody>{lifecycle_rows}</tbody></table>
<h2>System data</h2><pre>{payload}</pre></body></html>"""
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(document, encoding="utf-8")
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a read-only AI CIO status dashboard.")
    parser.add_argument("--database", default="outputs/cio.db")
    parser.add_argument("--output", default="outputs/dashboard.html")
    args = parser.parse_args()
    output = render_dashboard(CioDatabase(args.database), args.output)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
