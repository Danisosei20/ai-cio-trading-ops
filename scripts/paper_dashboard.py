#!/usr/bin/env python3
"""Paper trading dashboard: Alpaca paper + TradingAgents + approvals in one page.

Generates outputs/paper/dashboard.html (paper DB path from config).
No credentials are written into the page; account numbers stay masked.

Generate:
  python3 scripts/paper_dashboard.py
Serve locally:
  python3 scripts/paper_dashboard.py --serve --port 8080
"""
from __future__ import annotations

import argparse
import html
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ET = ZoneInfo("America/New_York")


def load_tradingagents(root: Path) -> list[dict]:
    rows: list[dict] = []
    if not root.exists():
        return rows
    for ticker_dir in sorted(root.iterdir()):
        if not ticker_dir.is_dir():
            continue
        for date_dir in sorted(ticker_dir.iterdir()):
            if not date_dir.is_dir():
                continue
            for name in ("auto.json", "decision.json"):
                f = date_dir / name
                if not f.exists():
                    continue
                try:
                    d = json.loads(f.read_text())
                except Exception:
                    continue
                d.setdefault("ticker", ticker_dir.name)
                d.setdefault("date", date_dir.name)
                d["_file"] = name
                rows.append(d)
    rows.sort(key=lambda r: (r.get("date", ""), r.get("ticker", "")), reverse=True)
    return rows[:50]


def load_desk(db) -> list[dict]:
    try:
        candidates = db.list_trade_candidates(8)
    except Exception:
        return []
    timelines = []
    for c in candidates:
        try:
            timelines.append(db.candidate_timeline(c["candidate_id"]))
        except Exception:
            continue
    return timelines


def render_opinion_bar(op: dict) -> str:
    conf = int(op.get("confidence", 0) or 0)
    agent = html.escape(str(op.get("agent", "")))
    verdict = html.escape(str(op.get("verdict", "")))
    return (f"<div style='margin:4px 0'><span style='display:inline-block;width:70px'>{agent}</span>"
            f"<span style='display:inline-block;width:220px;background:#eee;border-radius:4px'>"
            f"<span style='display:inline-block;width:{conf}%;background:#0969da;color:#fff;"
            f"font-size:12px;border-radius:4px'>&nbsp;{conf}%</span></span> "
            f"<small>{verdict}</small></div>")


def load_backtest() -> dict:
    f = Path("outputs/backtests/latest.json")
    if not f.exists():
        return {}
    try:
        return json.loads(f.read_text())
    except Exception:
        return {}


def load_status() -> list[dict]:
    f = Path("outputs/paper/tradingagents/status.json")
    if not f.exists():
        return []
    try:
        d = json.loads(f.read_text())
    except Exception:
        return []
    return [{"ticker": k, **v} for k, v in sorted(d.items())]


def load_live_log(lines: int = 60) -> str:
    f = Path("outputs/paper/tradingagents/live.log")
    if not f.exists():
        return "No live log yet. Run scripts/tradingagents_paper_auto.py to start."
    try:
        content = f.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return "Log unreadable."
    return "\n".join(content[-lines:]) or "(log empty)"


def badge(text: str) -> str:
    t = (text or "").lower()
    color = "#666"
    if any(k in t for k in ("buy", "overweight", "buy_placed", "filled")):
        color = "#1a7f37"
    elif any(k in t for k in ("sell", "underweight", "sell_placed")):
        color = "#cf222e"
    elif any(k in t for k in ("hold", "skip", "dry_run", "no_action")):
        color = "#8250df"
    return f"<span style='background:{color};color:#fff;padding:2px 10px;border-radius:12px;font-size:13px'>{html.escape(text or '—')}</span>"


def render(output: Path, database_path: str) -> Path:
    from robinhood_tools.alpaca_paper import AlpacaPaperBackend, AlpacaPaperHttpTransport
    from robinhood_tools.database import CioDatabase
    from robinhood_tools.runtime import build_settings
    from robinhood_tools.settings import load_env
    import os

    settings = build_settings()
    db = CioDatabase(database_path)
    try:
        approvals = db.list_approvals(25)
    except Exception:
        approvals = []
    try:
        lifecycles = db.list_trade_lifecycles()
    except Exception:
        lifecycles = []

    account_line, pos_rows, order_rows = "unavailable", "", ""
    try:
        env = {**load_env(".env"), **os.environ}
        backend = AlpacaPaperBackend(AlpacaPaperHttpTransport.from_values(env))
        accounts = backend.list_accounts()
        acct = backend.transport.request("GET", "/v2/account")
        account_line = (f"{accounts[0].label} {accounts[0].masked_account_number} · "
                        f"cash ${acct.get('cash')} · buying power ${acct.get('buying_power')} · "
                        f"portfolio ${acct.get('portfolio_value')}")
        for p in backend.list_positions():
            pos_rows += (f"<tr><td><b>{html.escape(str(p.get('symbol')))}</b></td>"
                         f"<td>{html.escape(str(p.get('qty')))}</td>"
                         f"<td>${html.escape(str(p.get('current_price')))}</td>"
                         f"<td>${html.escape(str(p.get('unrealized_pl')))}</td></tr>")
        for o in backend.list_open_orders()[:25]:
            order_rows += (f"<tr><td>{html.escape(str(o.get('symbol')))}</td>"
                           f"<td>{html.escape(str(o.get('side')))}</td>"
                           f"<td>{html.escape(str(o.get('qty')))}</td>"
                           f"<td>{html.escape(str(o.get('limit_price') or o.get('type')))}</td>"
                           f"<td>{html.escape(str(o.get('status')))}</td></tr>")
        if not pos_rows:
            pos_rows = "<tr><td colspan='4'>No positions</td></tr>"
        if not order_rows:
            order_rows = "<tr><td colspan='5'>No open orders</td></tr>"
    except Exception as exc:  # noqa: BLE001
        account_line = f"paper broker unreachable: {html.escape(type(exc).__name__)}"
        pos_rows = "<tr><td colspan='4'>—</td></tr>"
        order_rows = "<tr><td colspan='5'>—</td></tr>"

    ta_rows = ""
    for r in load_tradingagents(Path("outputs/paper/tradingagents")):
        action = r.get("action") or r.get("decision", "")
        note = str(r.get("reason", r.get("status", r.get("order_id", ""))))[:160]
        qty_px = str(r.get("qty", r.get("price", "")))
        ta_rows += (f"<tr><td><b>{html.escape(str(r.get('ticker')))}</b></td>"
                     f"<td>{html.escape(str(r.get('date')))}</td>"
                     f"<td>{badge(str(action))}</td>"
                     f"<td>{html.escape(str(r.get('decision', '')))}</td>"
                     f"<td>{html.escape(qty_px)}</td>"
                     f"<td style='max-width:380px'>{html.escape(note)}</td></tr>")
    if not ta_rows:
        ta_rows = "<tr><td colspan='6'>No TradingAgents runs yet</td></tr>"
    live_rows = "".join(
        f"<tr><td><b>{html.escape(str(s.get('ticker', '')))}</b></td><td>{badge(str(s.get('stage', '')))}</td>"
        f"<td>{html.escape(str(s.get('detail', ''))[:120])}</td><td>{html.escape(str(s.get('updated_at', ''))[:19])}</td></tr>"
        for s in load_status()
    ) or "<tr><td colspan='4'>No live run yet</td></tr>"
    live_log = html.escape(load_live_log())
    bt = load_backtest()
    bt_rows = "".join(
        f"<tr><td><b>{html.escape(str(r.get('ticker', '')))}</b></td>"
        f"<td>{html.escape(str(r.get('variant', '')))}</td>"
        f"<td>{r.get('is_return', 0):.1%}</td><td>{r.get('oos_return', 0):.1%}</td>"
        f"<td>{'PASS' if r.get('incubation_pass') else '—'}</td></tr>"
        for r in (bt.get("top") or [])[:8]
    ) or "<tr><td colspan='5'>No backtest yet — run scripts/strategy_backtest.py</td></tr>"
    bt_id = html.escape(str(bt.get("run_id", "")))
    desk_cards = ""
    for t in load_desk(db):
        c = t["candidate"]
        judge = [o for o in t["opinions"] if o.get("agent") == "judge"]
        verdict = judge[0]["verdict"] if judge else "—"
        jconf = judge[0]["confidence"] if judge else ""
        risk = t["risk_decisions"][-1] if t["risk_decisions"] else None
        risk_badge = ("PASS" if risk and risk["approved"] else
                      "FAIL" if risk else "NO RISK RUN")
        bars = "".join(render_opinion_bar(o) for o in t["opinions"] if o.get("agent") != "judge")
        desk_cards += (
            f"<div style='border:1px solid #ddd;border-radius:8px;padding:12px;margin:10px 0'>"
            f"<b>{html.escape(str(c.get('symbol')))}</b> "
            f"{badge(str(c.get('status', '')))} "
            f"<span>Judge: <b>{html.escape(str(verdict))}</b> {html.escape(str(jconf))}%</span> "
            f"<span>Risk: <b>{risk_badge}</b></span><br>{bars}</div>")
    if not desk_cards:
        desk_cards = "<p>No desk analyses yet — the next pipeline run will appear here.</p>"
    appr_rows = "".join(
        f"<tr><td>{html.escape(str(a.get('symbol', '')))}</td><td>{badge(str(a.get('status', '')))}</td>"
        f"<td>{html.escape(str(a.get('created_at', '')))}</td><td>{html.escape(str(a.get('expires_at', '')))}</td></tr>"
        for a in approvals
    ) or "<tr><td colspan='4'>No approvals</td></tr>"
    life_rows = "".join(
        f"<tr><td><b>{html.escape(str(lc.get('task_name', '')))}</b></td><td>{html.escape(str(lc.get('status', '')))}</td>"
        f"<td>{html.escape(str(lc.get('opened_at', '')))}</td><td>{html.escape(str(lc.get('realized_profit') or ''))}</td></tr>"
        for lc in lifecycles
    ) or "<tr><td colspan='4'>No lifecycles</td></tr>"

    now_et = datetime.now(ET).strftime("%Y-%m-%d %H:%M ET")
    live = "OFF" if not settings.trading_enabled else "ON — STOP"
    doc = f"""<!doctype html><html><head><meta charset='utf-8'>
<meta http-equiv='refresh' content='15'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Paper Trading — AI CIO</title>
<style>
body{{font:15px -apple-system,system-ui,sans-serif;max-width:1100px;margin:24px auto;padding:0 16px;color:#111}}
.hdr{{display:flex;gap:12px;flex-wrap:wrap;align-items:center}}
.card{{border:1px solid #ddd;border-radius:12px;padding:16px;margin:16px 0}}
table{{width:100%;border-collapse:collapse}}td,th{{padding:8px;text-align:left;border-bottom:1px solid #eee;font-size:14px}}
th{{color:#555;font-weight:600}}.pill{{padding:2px 10px;border-radius:12px;font-size:13px}}.live-off{{background:#1a7f37;color:#fff}}.live-on{{background:#cf222e;color:#fff}}
small{{color:#555}}</style></head><body>
<div class='hdr'><h1>Paper Trading</h1>
<span class='pill {"live-off" if live=="OFF" else "live-on"}'>LIVE {live}</span>
<span class='pill' style='background:#0969da;color:#fff'>PAPER AUTO {"ON" if settings.paper_autonomy.enabled else "OFF"}</span>
<small>{now_et} · Alpaca paper only · refreshes every 60s</small></div>
<div class='card'><h2>Account</h2><p>{account_line}</p>
<h3>Positions</h3><table><thead><tr><th>Symbol</th><th>Qty</th><th>Price</th><th>Unrealized</th></tr></thead><tbody>{pos_rows}</tbody></table>
<h3>Open orders</h3><table><thead><tr><th>Symbol</th><th>Side</th><th>Qty</th><th>Limit</th><th>Status</th></tr></thead><tbody>{order_rows}</tbody></table></div>
<div class='card'><h2>Live trader activity</h2>
<table><thead><tr><th>Ticker</th><th>Stage</th><th>Detail</th><th>Updated</th></tr></thead><tbody>{live_rows}</tbody></table>
<h3>Run log (last 60 lines)</h3><pre style='max-height:300px;overflow:auto;white-space:pre-wrap'>{live_log}</pre>
<small>Stages: researching → gating → reviewing → placing → buy_placed / skip. Full log: outputs/paper/tradingagents/live.log</small></div>
<div class='card'><h2>TradingAgents research → auto-trade</h2>
<table><thead><tr><th>Ticker</th><th>Date</th><th>Action</th><th>Decision</th><th>Qty/Price</th><th>Reason / Order</th></tr></thead><tbody>{ta_rows}</tbody></table>
<small>Buy/Overweight → buy (≤$500, DAY limit). Sell/Underweight → sell held position. All gates + review fingerprint enforced. Full logs: outputs/paper/tradingagents/</small></div>
<div class='card'><h2>AI desk debates</h2>{desk_cards}
<small>Bull/bear/red arguments with confidence; judge verdict + risk gate per candidate. Full evidence in the ledger.</small></div>
<div class='card'><h2>Strategy backtests + incubation {bt_id}</h2>
<table><thead><tr><th>Ticker</th><th>Variant</th><th>In-sample</th><th>Out-of-sample</th><th>Pass</th></tr></thead><tbody>{bt_rows}</tbody></table>
<small>Ranked by in-sample Sharpe, min-trades gate both segments. PASS = OOS &gt; 0. Only PASS variants are tradable.</small></div>
<div class='card'><h2>Approvals (paper ledger)</h2>
<table><thead><tr><th>Symbol</th><th>Status</th><th>Created</th><th>Expires</th></tr></thead><tbody>{appr_rows}</tbody></table>
<h2>Lifecycles</h2><table><thead><tr><th>Task</th><th>Status</th><th>Opened</th><th>Realized</th></tr></thead><tbody>{life_rows}</tbody></table></div>
</body></html>"""
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(doc, encoding="utf-8")
    return output


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--database", default="outputs/paper/cio.db")
    ap.add_argument("--output", default="outputs/paper/dashboard.html")
    ap.add_argument("--serve", action="store_true")
    ap.add_argument("--port", type=int, default=8080)
    a = ap.parse_args()
    out = render(Path(a.output), a.database)
    print(out)
    if a.serve:
        import functools
        import http.server
        handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(out.parent))
        with http.server.ThreadingHTTPServer(("127.0.0.1", a.port), handler) as httpd:
            print(f"Serving {out.parent} at http://127.0.0.1:{a.port}/{out.name}")
            httpd.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
