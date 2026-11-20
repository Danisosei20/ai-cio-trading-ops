#!/usr/bin/env python3
"""News scout: fresh, source-cited news dossiers per ticker (Firecrawl API).

Feeds the desk's research department with the current independent sources its
policy requires (news, filings, investor-relations). Writes one JSON that the
research bridge / fleet record can point at.

Usage:
  python3 scripts/news_scout.py --tickers NVDA,SPY
  python3 scripts/news_scout.py --tickers NVDA --out outputs/news/nvda.json

Credentials (either works, first hit wins):
  FIRECRAWL_API_KEY in the process environment, or
  FIRECRAWL_API_KEY in ../TradingAgents/.env (desk convention for LLM-side keys)

Get a free key at https://firecrawl.dev. Without a key this exits cleanly
with a "skipped" message -- it never crashes a scheduled run.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

SEARCH_URL = "https://api.firecrawl.dev/v2/search"
SCRAPE_URL = "https://api.firecrawl.dev/v2/scrape"


def _firecrawl_key() -> str:
    key = os.environ.get("FIRECRAWL_API_KEY", "")
    if key:
        return key
    dotenv = Path(__file__).resolve().parents[1].parent / "TradingAgents" / ".env"
    if not dotenv.exists():
        return ""
    try:
        for line in dotenv.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("FIRECRAWL_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


def _post(url: str, key: str, payload: dict, timeout: int = 60) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {key}",
                 "Content-Type": "application/json"},
        method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read() or b"{}")


def _normalize_hits(payload: dict) -> list:
    """Firecrawl search shapes vary: data may be a list, or {'web': [...]},
    or a category dict ({'web': [...], 'news': [...]}). Flatten to a list."""
    hits = payload.get("data") or payload.get("web") or []
    if isinstance(hits, dict):
        hits = hits.get("web") or hits.get("news") or []
    if isinstance(hits, dict):
        flat: list = []
        for value in hits.values():
            if isinstance(value, list):
                flat.extend(value)
        hits = flat
    if not isinstance(hits, list):
        return []
    return [h for h in hits if isinstance(h, dict)]


def scout_ticker(ticker: str, key: str, max_sources: int, max_chars: int) -> dict:
    search = _post(SEARCH_URL, key,
                   {"query": f"{ticker} stock news", "limit": max_sources})
    sources = []
    for hit in _normalize_hits(search)[:max_sources]:
        url = str(hit.get("url", ""))
        if not url:
            continue
        entry = {"url": url, "title": str(hit.get("title", ""))[:300],
                 "description": str(hit.get("description", ""))[:500],
                 "markdown": ""}
        try:
            scraped = _post(SCRAPE_URL, key,
                             {"url": url, "formats": ["markdown"]})
            sdata = scraped.get("data") or {}
            md = ""
            if isinstance(sdata, dict):
                md = sdata.get("markdown") or scraped.get("markdown") or ""
            entry["markdown"] = str(md)[:max_chars]
        except Exception as exc:  # noqa: BLE001 - one dead source never kills the run
            entry["markdown"] = f"[scrape failed: {type(exc).__name__}]"
        sources.append(entry)
    return {"ticker": ticker, "sources": sources,
            "fetched_at": datetime.now(timezone.utc).isoformat()}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Firecrawl news scout (desk research).")
    p.add_argument("--tickers", required=True, help="comma list, e.g. NVDA,SPY")
    p.add_argument("--out", default="outputs/news/latest.json")
    p.add_argument("--max-sources", type=int, default=3)
    p.add_argument("--max-chars", type=int, default=2000)
    args = p.parse_args(argv)

    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    key = _firecrawl_key()
    if not key:
        print("news_scout: skipped (no FIRECRAWL_API_KEY). "
              "Free key: https://firecrawl.dev")
        return 0

    record = {"generated_at": datetime.now(timezone.utc).isoformat(),
              "tickers": []}
    for ticker in tickers:
        try:
            record["tickers"].append(
                scout_ticker(ticker, key, args.max_sources, args.max_chars))
            print(f"news_scout: {ticker} -> "
                  f"{len(record['tickers'][-1]['sources'])} sources")
        except Exception as exc:  # noqa: BLE001 - log, never crash the chain
            print(f"news_scout: {ticker} failed ({type(exc).__name__}: {exc})")
            record["tickers"].append(
                {"ticker": ticker, "sources": [],
                 "error": f"{type(exc).__name__}: {exc}",
                 "fetched_at": datetime.now(timezone.utc).isoformat()})

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(record, indent=2), encoding="utf-8")
    print(f"news_scout: wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
