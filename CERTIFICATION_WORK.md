# Certification & hardening work plan — COMPLETE

Baseline: main `01f9a6f`, 940 tests. Worktree: certification-hardening.
ADR 0001 preserved: AI research only; deterministic gate authoritative.

## Audit results (Phase 1) — COMPLETE
Five parallel audits: gate architecture, execution/risk, AI stations, ops/viewer/datafeed, tests/certification.
09:45 replay (simulated tradeable session) COMPLETE: verdict proceed; OddsAnalyst ok, ScenarioAnalyst ok.

## Contained HIGH/CRITICAL fixes — ALL COMPLETE (TDD, reviewed)
- [x] A. Stale evidence rejection in Screen + Liquid, fail-closed.
- [x] B. No-OpenAI provider enforcement in both LLM bridges.
- [x] C. Strategy ownership isolation at 15:45 flatten + day-trade accounting.
- [x] D. Overnight entry window enforcement + exit-consumable state persistence.
- [x] E. RealtimeStream reconnect crash + websockets 17 exception import + socket lifecycle.
- [x] F. Windows preflight schedules check + viewer port correction.
- [x] G. Research/News artifact path collision.
- [x] H. paper_certification --no-broker hermetic.

Reviewer findings (real producer shape, overnight exit key, state clobber, reconnect socket lifecycle) all fixed.

## Verification — COMPLETE
- 974 tests OK (2 skipped), ruff clean, mypy clean (CI command), secret scan clean.
- paper_certification --no-broker: 67 PASS / 0 FAIL / 0 WARN / 2 SKIP (was 63 PASS / 4 FAIL).

## Honest limitations (reported, not fixed here)
- 09:45->10:20 authorization record: architectural gap, needs new ADR + gate redesign.
- Borrow/shortable validation: needs broker eligibility interface decision.
- RealtimeStream remains unintegrated; IEX-only feed; no consolidated feed.
- Preflight in worktree: 4 environment FAILs (no sibling TradingAgents venv/key, 2 viewers on 8766).
- Live Robinhood not testable in this environment.
