#!/usr/bin/env python3
"""Continuous intraday day-trade loop: scan -> judge -> enter, every N min.

Owner directive: autotrade throughout market open, not just at 10:30.
Each cycle:
  1. Scout OR+VWAP+zone setups (daytrade_scout logic)
  2. Filter out already-traded setups (dedup by ticker+date+setup)
  3. If new setups exist, run paper_auto --day-trade on them
  4. Log everything to outputs/intraday.log

Runs from market open (09:45 ET) to flatten time (15:45 ET). At 15:45,
triggers flatten_daytrades.py as a safety net.

Usage:
  python scripts/intraday_loop.py                      # run until 15:45 (default 15-min cycles)
  python scripts/intraday_loop.py --interval 10       # 10-min cycles
  python scripts/intraday_loop.py --dry-run           # scan only, no orders
  python scripts/intraday_loop.py --once              # single cycle then exit
  python scripts/intraday_loop.py --start 10:00 --end 15:30  # custom window
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from robinhood_tools.trade_state import TradeStateMachine, TradeState
from robinhood_tools.workflow_manifest import (FAILED, VERIFIED,
                                               write_manifest)

ET = ZoneInfo("America/New_York")
LOG_FILE = ROOT / "outputs" / "intraday.log"
TRADED_FILE = ROOT / "outputs" / "daytrade" / "traded_setups.json"
SCOUT_SCRIPT = ROOT / "scripts" / "daytrade_scout.py"
AUTO_SCRIPT = ROOT / "scripts" / "tradingagents_paper_auto.py"
FLATTEN_SCRIPT = ROOT / "scripts" / "flatten_daytrades.py"
LATEST_DECISION = ROOT / "outputs" / "orchestrator" / "latest.json"
TA_PY = Path(os.environ.get("TA_PY", "")) or (ROOT.parent / "TradingAgents" / ".venv" / "Scripts" / "python.exe")


def _log(msg: str) -> None:
    ts = datetime.now(ET).isoformat()
    line = f"{ts} {msg}"
    print(line, flush=True)
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def _load_traded() -> list[dict]:
    if TRADED_FILE.exists():
        try:
            return json.loads(TRADED_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []
    return []


def _save_traded(traded: list[dict]) -> None:
    TRADED_FILE.parent.mkdir(parents=True, exist_ok=True)
    TRADED_FILE.write_text(json.dumps(traded, indent=2), encoding="utf-8")


def _market_open() -> bool:
    """Check if the market is open via Alpaca clock (best effort)."""
    try:
        import urllib.request
        import json as _j
        from robinhood_tools.settings import load_env
        env = load_env(".env")
        base = env.get("ALPACA_PAPER_BASE_URL", "https://paper-api.alpaca.markets")
        key = env.get("ALPACA_API_KEY") or env.get("ALPACA_KEY_ID")
        secret = env.get("ALPACA_API_SECRET") or env.get("ALPACA_SECRET_KEY")
        req = urllib.request.Request(base + "/v2/clock",
                                    headers={"APCA-API-KEY-ID": key,
                                             "APCA-API-SECRET-KEY": secret})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return bool(_j.loads(resp.read()).get("is_open", False))
    except Exception:
        return True  # fail open: assume open if we can't check


def desk_verdict(root: Path = ROOT, *, now: datetime | None = None) -> tuple[bool, str]:
    """Return (may_trade, reason) from today's recorded orchestrator verdict.

    Fail closed: a missing, unreadable, malformed or stale decision, or any
    verdict other than ``proceed``, blocks orders. A verdict from an earlier
    ET date never authorises a later session. ``--force`` does not bypass this.
    """
    now = now or datetime.now(ET)
    try:
        doc = json.loads((root / "outputs/orchestrator/latest.json").read_text(
            encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        return False, "no readable orchestrator decision"
    if not isinstance(doc, dict):
        return False, "malformed orchestrator decision"
    stamp = str(doc.get("generated_at") or "")
    if not stamp:
        return False, "decision has no timestamp"
    try:
        decided = datetime.fromisoformat(stamp).astimezone(ET).date()
    except (ValueError, TypeError):
        return False, "unreadable decision timestamp"
    if decided != now.date():
        return False, f"decision is from {decided}, not today"
    verdict = str(doc.get("verdict") or "")
    if verdict != "proceed":
        return False, f"verdict is {verdict or 'missing'}"
    return True, "proceed"


def _parse_time(s: str) -> datetime:
    """Parse 'HH:MM' into today's ET datetime."""
    h, m = s.split(":")
    now = datetime.now(ET)
    return now.replace(hour=int(h), minute=int(m), second=0, microsecond=0)


def run_scout(tickers: str | None = None) -> dict | None:
    """Run the daytrade scout in-process (avoids subprocess PermissionError)."""
    try:
        import scripts.daytrade_scout as scout_mod
        if tickers:
            scout_mod.WATCHLIST = tuple(t.strip().upper() for t in tickers.split(",") if t.strip())
        scout_mod.main([])
        today = datetime.now(ET).date().isoformat()
        scout_file = ROOT / "outputs" / "daytrade" / f"{today}.json"
        if scout_file.exists():
            return json.loads(scout_file.read_text(encoding="utf-8"))
        return None
    except Exception as exc:
        import traceback
        _log(f"scout error: {type(exc).__name__}: {exc}")
        _log(f"scout traceback: {traceback.format_exc()[:300]}")
        return None


def filter_new_setups(scout: dict, traded: list[dict]) -> list[str]:
    """Return tickers with setups not yet traded today."""
    traded_keys = {(t["ticker"], t["date"], t["setup"]) for t in traded}
    today = datetime.now(ET).date().isoformat()
    new_tickers = []
    for s in scout.get("setups", []):
        if s.get("setup") in ("long_reclaim", "short_breakdown"):
            key = (s["ticker"], today, s["setup"])
            if key not in traded_keys:
                new_tickers.append(s["ticker"])
    return new_tickers


def _research_manifest(*, status: str, rc: int | None, reason: str,
                       tickers: list[str]) -> None:
    """Persist terminal research evidence so a timeout is never silent."""
    now = datetime.now(ET)
    try:
        write_manifest(
            ROOT, task="research", date=now.date().isoformat(), status=status,
            run_id=f"research-{now.strftime('%H%M%S')}",
            started_at=now.isoformat(), finished_at=now.isoformat(),
            exit_code=rc, mode="paper_research",
            evidence={"tickers": tickers, "judge_timeout_seconds": 3600,
                      "orders_submitted": 0},
            error=reason)
    except Exception as exc:  # noqa: BLE001 - manifest failure must not hide rc
        _log(f"research manifest FAILED: {type(exc).__name__}: {exc}")


def run_judge(tickers: list[str], dry_run: bool = False,
              authorization_id: str | None = None) -> None:
    """Run paper_auto --day-trade on the given tickers."""
    cmd = [str(TA_PY), str(AUTO_SCRIPT),
           "--day-trade",
           "--tickers", ",".join(tickers),
           "--analysts", "market,news",
           "--max-tool-rounds", "4",
           # Measured 2026-11-06: one nemotron call ~35s (1-token call 0.5s), a
           # full research run ~544s with one analyst. The previous 600s cap
           # was under the real cost, so every ticker died at the budget and
           # the desk recorded no decisions. 1200s is sized from measurement,
           # not guesswork; the whole invocation is still bounded by the 1800s
           # subprocess timeout below.
           "--ticker-timeout", "1200",
           "--quick-model", "nvidia/nemotron-3.5-lightning-30b-a3b",
           "--deep-model", "nvidia/nemotron-3.5-lightning-30b-a3b",
           "--max-notional", "2000"]
    if authorization_id:
        cmd += ["--authorization-id", authorization_id]
    if dry_run:
        cmd.append("--dry-run")
    try:
        _log(f"judge: running on {tickers}")
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace",
                           # Only NEW setups reach the judge, so this is
                           # normally 1-3 tickers. Bounded above the 1200s
                           # per-ticker budget so a slow-but-completing research
                           # run is not killed mid-flight: the old 900s cap sat
                           # under the per-ticker budget, so a run that was
                           # working still died at the wall clock.
                           timeout=3600,
                           cwd=str(ROOT))
        if r.returncode != 0:
            reason = f"judge failed rc={r.returncode}: {r.stderr[:300]}"
            _log(reason)
            _research_manifest(status=FAILED, rc=r.returncode or 1,
                               reason=reason, tickers=tickers)
            return False
        _log(f"judge completed for {tickers}")
        _research_manifest(status=VERIFIED, rc=0, reason="", tickers=tickers)
        return True
    except subprocess.TimeoutExpired as exc:
        reason = f"judge timeout after 3600s: {exc}"
        _log(reason)
        _research_manifest(status=FAILED, rc=124, reason=reason[:400],
                           tickers=tickers)
        return False
    except Exception as exc:
        reason = f"judge error: {type(exc).__name__}: {exc}"
        _log(reason)
        _research_manifest(status=FAILED, rc=127, reason=reason[:400],
                           tickers=tickers)
        return False


def run_flatten() -> None:
    """Trigger the 15:45 flatten as a safety net."""
    try:
        _log("flatten: triggering flatten_daytrades.py")
        r = subprocess.run([str(TA_PY), str(FLATTEN_SCRIPT)],
                           capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=120,
                           cwd=str(ROOT))
        _log(f"flatten: rc={r.returncode}")
    except Exception as exc:
        _log(f"flatten error: {type(exc).__name__}: {exc}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Continuous intraday day-trade loop.")
    ap.add_argument("--interval", type=int, default=45,
                    help="minutes between scan cycles (default 45)")
    ap.add_argument("--start", default="10:00",
                    help="start time ET HH:MM (default 10:00, after OR forms)")
    ap.add_argument("--end", default="15:30",
                    help="last scan time ET HH:MM (default 15:30)")
    ap.add_argument("--tickers", default=None,
                    help="override watchlist (comma list)")
    ap.add_argument("--dry-run", action="store_true",
                    help="scan only, no orders")
    ap.add_argument("--once", action="store_true",
                    help="single cycle then exit")
    ap.add_argument("--force", action="store_true",
                    help="bypass market-open check (for testing)")
    args = ap.parse_args(argv)

    start_dt = _parse_time(args.start)
    end_dt = _parse_time(args.end)
    traded = _load_traded()
    today = datetime.now(ET).date().isoformat()
    # Reset traded list if it's a new day
    traded = [t for t in traded if t.get("date") == today]

    # State machine for trade lifecycle tracking
    sm = TradeStateMachine()

    _log(f"intraday loop starting: {args.start}-{args.end} ET, "
         f"interval={args.interval}min, dry_run={args.dry_run}")

    # If starting before the window, wait
    now = datetime.now(ET)
    if now < start_dt and not args.once:
        wait = (start_dt - now).total_seconds()
        _log(f"waiting {wait/60:.0f} min until {args.start} ET")
        time.sleep(wait)

    cycle = 0
    judge_failures: list[str] = []
    while True:
        now = datetime.now(ET)
        # --once means "run one cycle now": a rehearsal after hours must
        # not be a silent no-op (2026-11-16: --once --force at 22:39 ET
        # logged "end of scan window reached" and did nothing, so the
        # loop's wiring could not be verified outside the session).
        if now > end_dt and not args.once:
            _log("end of scan window reached")
            break
        if not _market_open() and not args.force:
            _log("market closed, stopping")
            break

        cycle += 1
        _log(f"=== cycle {cycle} at {now.strftime('%H:%M')} ET ===")

        # 1. Scout
        scout = run_scout(args.tickers)
        if scout is None:
            _log("scout returned no data, skipping cycle")
        else:
            new_tickers = filter_new_setups(scout, traded)
            if new_tickers:
                _log(f"new setups: {new_tickers}")
                # Create state machine records for each new setup
                for s in scout.get("setups", []):
                    if s["ticker"] in new_tickers:
                        direction = "long" if s["setup"] == "long_reclaim" else "short"
                        trade = sm.create_trade(
                            ticker=s["ticker"], direction=direction,
                            setup=s["setup"], strategy="intraday",
                            regime="", entry_price=s.get("price", 0),
                            stop_price=s.get("or_low", 0),
                            client_order_id=f"{s['ticker']}:{today}:{s['setup']}")
                        sm.transition(trade.id, TradeState.SIGNAL_FOUND, "scout detected setup")
                allowed, why = desk_verdict(ROOT)
                parent_authorization_id = None
                if not args.dry_run and allowed:
                    # ADR 0002 (approved): when enforcement is enabled, a fresh
                    # deterministic authorization is required and consumed here.
                    # With the flag off, legacy scheduling behavior is unchanged.
                    try:
                        from robinhood_tools.execution_authorization import enforcement_enabled
                        if enforcement_enabled():
                            from scripts.desk_orchestrator import read_latest
                            from scripts.execution_handoff import require_for_run, parent_id_for_run
                            decision_doc = read_latest(ROOT) or {}
                            auth_ok, auth_why = require_for_run(ROOT, decision_doc)
                            if auth_ok:
                                parent_authorization_id = parent_id_for_run(
                                    ROOT, str(decision_doc.get("run_id", "")))
                                if not parent_authorization_id:
                                    allowed = False
                                    why = "gate authorization unavailable for propagation"
                            if not auth_ok:
                                allowed = False
                                why = f"execution authorization: {auth_why}"
                    except Exception as exc:  # noqa: BLE001 - fail closed when enabled
                        allowed = False
                        why = f"execution authorization error: {type(exc).__name__}: {exc}"
                if not args.dry_run and not allowed:
                    _log(f"order gate blocked ({why}); no orders for {new_tickers}")
                elif not args.dry_run:
                    _log(f"order gate open ({why}); judging {new_tickers}")
                    if not run_judge(new_tickers, authorization_id=parent_authorization_id):
                        judge_failures.append(",".join(new_tickers))
                    # Record traded setups
                    today = datetime.now(ET).date().isoformat()
                    for s in scout.get("setups", []):
                        if s["ticker"] in new_tickers:
                            traded.append({
                                "ticker": s["ticker"],
                                "date": today,
                                "setup": s["setup"],
                                "entered_at": datetime.now(ET).isoformat(),
                            })
                    _save_traded(traded)
                else:
                    _log(f"dry-run: would judge {new_tickers}")
            else:
                _log("no new setups this cycle")

        if args.once:
            break

        # Wait for next cycle
        now = datetime.now(ET)
        next_cycle = now + timedelta(minutes=args.interval)
        if next_cycle > end_dt:
            break
        sleep_sec = (next_cycle - now).total_seconds()
        _log(f"sleeping {sleep_sec/60:.0f} min until next cycle")
        time.sleep(sleep_sec)

    # Flatten at the end
    if not args.dry_run and not args.once:
        _log("scan window closed, triggering flatten")
        run_flatten()

    _log("intraday loop done")
    if judge_failures:
        _log(f"research failures: {judge_failures}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
