# Downside Signal Paths Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A down tape produces a put candidate and a day-trade short instead of nothing, with a $5k long / $5k short notional split, all while dry-run stays on.

**Architecture:** Three seams. First, extend the existing `market_prices.market_regime()` to return `RISK_OFF`. Second, write the full screened universe to a new screener output so weak names are available offline at 15:10. Third, give `PortfolioRiskController` a notion of direction so `record_order`/`check_exposure` account long and short books separately, which also fixes a pre-existing bug where short entries were invisible to exposure accounting. The 15:10 watch and the day-trade engine then branch on regime and budget respectively.

**Tech Stack:** Python 3, `unittest` (desk venv has no pytest), git on branch `agent/premarket-disclosure-signals`, Alpaca paper only.

**Spec:** `docs/superpowers/specs/2026-11-05-short-biased-signal-paths-design.md`

## Global Constraints

- Paper trading only; never enable live brokerage execution.
- No OpenAI integration — NVIDIA NIM models only.
- Missing evidence is not permission to trade: `UNKNOWN` regime fails closed.
- Every execution path passes the central risk controller; exposure checks stay atomic.
- Exits are never zone-blocked and never blocked by this change.
- Dry-run stays on for `daytrade_engine.py` and the options watch until the Friday 16:45 review.
- Windows console is cp1252: new console output is ASCII only.
- Long screen's ranking, scoring, and `latest.json` output are byte-identical to today.
- `outputs/` is a junction to `%LOCALAPPDATA%\AI-cio-outputs`; read the real files there, not in the repo tree.

## Review Focus

- **A long `sell` that is really an exit** (existing position, bearish signal) — must release the long book, not open a short entry. Pinned in Task 3, Step 1.
- **A persisted ledger from before this change** (no `direction` field anywhere) — must load and behave exactly as before. Pinned in Task 3, Step 1 (legacy-load test) and Task 5, Step 1 (cert).
- **Empty or single-row screener data** (fewer than 2 rows makes `pstdev` 0) — must not produce a size-0 z-score crash or a nonsense ranking. Pinned in Task 2, Step 1.
- **A short candidate that exceeds the remaining short budget** — must clamp to the budget and trade smaller, not be dropped and not sized beyond the budget. Pinned in Task 4, Step 1.
- **SPY fetch failure inside the 15:10 watch** — must fail closed (no put entries) rather than defaulting to `RISK_ON`. Pinned in Task 3's regime helper test and Task 5's cert check.

---

### Task 1: Extend `market_regime()` with `RISK_OFF`

**Files:**
- Modify: `robinhood_tools/market_prices.py:32-40`
- Test: `tests/test_market_prices.py`

**Interfaces:**
- Produces: `market_regime(spy_closes: list[float]) -> str` now returning one of
  `"RISK_ON" | "CHOP" | "RISK_OFF" | "UNKNOWN"`. Existing `RISK_ON`/`CHOP`/
  `UNKNOWN` values are unchanged, so `tradingagents_paper_auto.py`'s
  `!= "RISK_ON"` fail-closed branch keeps working. Later tasks call this to
  branch candidate direction.
  Also produces `_spy_closes() -> list[float]` in the same module — the single
  SPY-bar fetch (`period="1y"`, `[]` on any failure) that Task 5's `_regime()`
  consumes. `market_regime` itself must stay pure: no network, no yfinance
  import.

- [ ] **Step 1: Write the failing tests**
Add to `tests/test_market_prices.py` alongside the existing `market_regime`
tests (which assert `CHOP` and `UNKNOWN`):

```python
def test_market_regime_risk_off_when_spy_below_50d_sma(self):
    closes = [100.0] * 50 + [50.0]        # last close far below the 50d mean
    self.assertEqual(market_regime(closes), "RISK_OFF")

def test_market_regime_risk_on_unchanged(self):
    closes = list(range(50, 101))          # rising
    self.assertEqual(market_regime(closes), "RISK_ON")

def test_spy_closes_returns_empty_list_when_yfinance_missing(self):
    with unittest.mock.patch.dict("sys.modules", {"yfinance": None}):
        self.assertEqual(market_prices._spy_closes(), [])
```

Also add a loader test so the fetch stays stub-free and the function stays
pure:

```python
def test_spy_closes_for_regime_does_not_fetch_network(self):
    # pinned so a later edit cannot silently add a network call here
    self.assertEqual(market_prices.market_regime([]), "UNKNOWN")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m unittest tests.test_market_prices -v`
Expected: FAIL on `test_market_regime_risk_off_when_spy_below_50d_sma` — it
returns `"CHOP"` because the current function returns `CHOP` for anything
that is not `RISK_ON`.

- [ ] **Step 3: Implement in `robinhood_tools/market_prices.py`**

Change `market_regime` to the signature below. Keep the `UNKNOWN` short-history
guard at the head. Extend the docstring to name all four values and to state
that a fetch failure must be handled by the caller returning `UNKNOWN`.

```python
def market_regime(spy_closes: list[float]) -> str:
    """RISK_ON above the 50-day mean, RISK_OFF below it, CHOP when mixed.

    Returns UNKNOWN on short history — callers fail closed on UNKNOWN.
    """
```

The body keeps the existing `window`/mean computation; it returns `RISK_ON`
when the last close is above the mean, `RISK_OFF` when below. Do not return
`CHOP` in either of those two cases.

Add `_spy_closes()` in the same module, keeping it free of the `from __future__
import annotations`-safe import at module top (yfinance is only installed in the
TradingAgents venv):

```python
def _spy_closes() -> list[float]:
    """SPY daily closes for the regime gate. [] on any failure (fails closed)."""
    try:
        import yfinance as yf  # noqa: PLC0415
        frame = yf.Ticker("SPY").history(period="1y", interval="1d")
        return [float(c) for c in frame["Close"].dropna()]
    except Exception:  # noqa: BLE001 - ImportError, network, empty frame
        return []
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m unittest tests.test_market_prices -v`
Expected: PASS, including the pre-existing `CHOP`/`UNKNOWN` tests unchanged and
the two new `_spy_closes` guards.

- [ ] **Step 5: Commit**

```bash
git add robinhood_tools/market_prices.py tests/test_market_prices.py
git commit -m "feat(risk): market_regime reports RISK_OFF below the 50d mean"
```

---

### Task 2: Persist the full screened universe

**Files:**
- Modify: `scripts/universe_screen.py:97-134`
- Test: `tests/test_universe_screen.py`

**Interfaces:**
- Consumes: `rank(rows, top) -> list[dict]` (existing, unchanged — sorts
  descending by `score`, returns `[:top]`).
- Produces: `rank_weak(rows: list[dict], top: int) -> list[dict]` — same scorer,
  ascending `mom20` sort, returns `[:top]`. Also
  `screener_payload(rows, ranked, universe) -> dict` building the
  `{"date", "universe", "screened", "ranked", "weak"}` shape both output files
  share. A new file `outputs/screener/weak.json` is written with the same
  payload; `latest.json` is written with `ranked` only and must stay
  byte-identical to today's shape.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_universe_screen.py`:

```python
def test_rank_weak_orders_by_lowest_mom20(self):
    rows = [{"symbol": "A", "mom20": 0.30, "relvol": 1.0, "dist52w": -0.1},
            {"symbol": "B", "mom20": -0.20, "relvol": 1.0, "dist52w": -0.1},
            {"symbol": "C", "mom20": 0.05, "relvol": 1.0, "dist52w": -0.1}]
    weak = rank_weak(rows, 2)
    self.assertEqual([r["symbol"] for r in weak], ["B", "C"])
    self.assertIn("score", weak[0])          # scorer still applied

def test_rank_weak_single_row_does_not_crash(self):
    rows = [{"symbol": "A", "mom20": 0.1, "relvol": 1.0, "dist52w": 0.0}]
    self.assertEqual(len(rank_weak(rows, 5)), 1)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m unittest tests.test_universe_screen -v`
Expected: FAIL — `ImportError: cannot import name 'rank_weak'`.

- [ ] **Step 3: Implement `rank_weak` and the shared payload builder**

In `scripts/universe_screen.py`, add `rank_weak(rows, top)` beside `rank`. It
computes `score` exactly as `rank` does (same weights, same z function), then
sorts ascending by `mom20` and truncates to `top`. Extract the payload dict
construction into `screener_payload(rows, ranked, universe, weak=None)`.

For zero or one input rows the z-score denominator uses the already-present
`or 1e-9` guard; do not add a new crash path. A `momentum` of `None` is not
possible — `features()` always sets a float — so no `None` guard is needed.

In `main()`, after `ranked = rank(rows, a.top)`, compute
`weak = rank_weak(rows, 20)` and write both files:
`latest.json` gets `screener_payload(rows, ranked, len(symbols))` with no
`weak` key; `weak.json` gets `screener_payload(rows, ranked, len(symbols),
weak=weak)`. Also write the dated `weak-{date}.json` next to the existing dated
file. Keep the existing print loop and its `--out` handling.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m unittest tests.test_universe_screen -v`
Expected: PASS.

- [ ] **Step 5: Verify `latest.json` shape is unchanged**

Run: `.venv\Scripts\python.exe -c "import json;d=json.loads(open('C:/Users/Danis Akoto/AppData/Local/AI-cio-outputs/screener/latest.json').read());print(sorted(d));print(len(d['ranked']))"`
Expected: `['date', 'ranked', 'screened', 'universe']` and `10` — no `weak` key.

- [ ] **Step 6: Commit**

```bash
git add scripts/universe_screen.py tests/test_universe_screen.py
git commit -m "feat(screener): persist the weak-momentum tail for the bear book"
```

---

### Task 3: Give the risk controller a notion of direction

**Files:**
- Modify: `robinhood_tools/portfolio_risk.py` (`RiskState`, `__init__`,
  `check_exposure`, `check`, `record_order`, `status`, CLI `main`)
- Test: `tests/test_portfolio_risk_direction.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `RiskState` gains `book: dict = field(default_factory=dict)` mapping
    `"long"`/`"short"` → `{symbol: {"strategy", "notional", "entered_at"}}`.
    Open-position source of truth remains `RiskState.open_positions`; `book`
    is the same keys grouped by direction, so `check_exposure` sums per book.
    Keep `open_positions` written as well so existing tooling keeps working.
  - `record_order(symbol: str, side: str, strategy: str, notional: float,
    direction: str | None = None, *, short: bool | None = None)` — when
    `direction`/`short` are omitted the call behaves exactly as today:
    `buy` adds, `sell` releases. When `short=True`, a `sell` opens the short
    book and a `buy` releases it.
  - `check(symbol, side, notional, strategy, data_age_sec=0, current_equity=0,
    direction=None, short=None) -> RiskVerdict` — the exposure check now
    compares against the cap for the book this order would touch.
  - `check_exposure(symbol, notional, short=False) -> tuple[bool, str]`.
  - `status()["exposure"]` gains `long_notional`, `short_notional`,
    `max_per_book`, `total_notional`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_portfolio_risk_direction.py`. Use the same isolation
pattern the certification harness uses: monkeypatch the module-level
`STATE_FILE`, `LOCK_FILE`, and `KILL_SWITCH_FILE` to a temp directory via
`unittest.mock.patch.object`.

```python
SHORT_CAP = Decimal("5000")

class DirectionAccountingTests(unittest.TestCase):
    def _ctrl(self):
        return pr.PortfolioRiskController()

    def test_long_sell_on_held_symbol_releases_it(self):
        c = self._ctrl()
        c.record_order("AAPL", "buy", "intraday", 4000)
        c.record_order("AAPL", "sell", "intraday", 4000)
        self.assertNotIn("AAPL", c.state.book["long"])

    def test_short_entry_opens_short_book(self):
        c = self._ctrl()
        c.record_order("QQQ", "sell", "intraday", 3000, short=True)
        self.assertIn("QQQ", c.state.book["short"])

    def test_short_entry_does_not_open_a_long(self):
        c = self._ctrl()
        c.record_order("QQQ", "sell", "intraday", 3000, short=True)
        self.assertNotIn("QQQ", c.state.book["long"])

    def test_short_exit_releases_short_book(self):
        c = self._ctrl()
        c.record_order("QQQ", "sell", "intraday", 3000, short=True)
        c.record_order("QQQ", "buy", "intraday", 3000, short=True)
        self.assertNotIn("QQQ", c.state.book["short"])

    def test_short_cap_enforced_independently_of_long(self):
        c = self._ctrl()
        for i in range(6):
            c.record_order(f"L{i}", "buy", "intraday", 2000)
        v = c.check("QQQ", "sell", 4000, "intraday", short=True)
        self.assertTrue(v.approved)
```

Also a legacy-load test, which pins the "old ledger still loads" requirement:

```python
    def test_legacy_state_without_book_loads(self):
        legacy = {"open_positions": {"AAPL": {"strategy": "intraday",
                                               "notional": 1000,
                                               "entered_at": "x"}}}
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "s.json"
            p.write_text(json.dumps(legacy), encoding="utf-8")
            with patch.object(pr, "STATE_FILE", p), \
                 patch.object(pr, "LOCK_FILE", Path(d) / "l.lock"), \
                 patch.object(pr, "KILL_SWITCH_FILE", Path(d) / "K"):
                c = pr.PortfolioRiskController()
        self.assertIn("AAPL", c.state.book["long"])
        v = c.check("QQQ", "sell", 4000, "intraday", short=True)
        self.assertTrue(v.approved)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m unittest tests.test_portfolio_risk_direction -v`
Expected: FAIL — `AttributeError` / `TypeError` on the unexpected keyword, and
`book` not populated.

- [ ] **Step 3: Implement the direction split**

In `robinhood_tools/portfolio_risk.py`:

1. Add `book: dict = field(default_factory=lambda: {"long": {}, "short": {}})`
   to `RiskState`. Because `RiskState(**{...})` filters unknown keys, a legacy
   file without `book` gets the default.
2. Add `self._max_book_notional = Decimal("5000")` in `__init__` next to
   `_max_total_notional`, and keep `_max_total_notional` unchanged for
   reporting.
3. Give `RiskState` a small private helper the controller calls after every
   mutation, e.g. `def _regroup(self)` that rebuilds `book` from each
   `open_positions` entry's stored `direction` (`"long"` default when absent,
   which is what legacy rows lack). Call it at the end of `record_order` and
   after `_load_state` so state loaded from disk is always regrouped.
4. `check_exposure(symbol, notional, short=False)`: sum only the relevant
   book, compare `total + notional > self._max_book_notional`, and return
   `f"short exposure {total}+{notional} > {cap}"` /
   `f"total exposure {total}+{notional} > {cap}"` — the existing wording for
   the long case is kept so current log greps and no other caller's string
   match break. Position-count check still uses `open_positions`.
5. `check(...)` and `record_order(...)` accept `direction: str | None = None`
   and `short: bool | None = None`. Resolve `is_short = short if short is not
   None else (direction == "short")`. Write `direction` into the
   `open_positions` entry. All existing call sites that pass no direction keep
   today's behaviour (`buy` opens long, `sell` releases).
6. `status()`: build `exposure` from the two books.

Legacy behaviour to preserve exactly: `buy` on a symbol already in
`open_positions` must not double-count; `sell` on a symbol not present is a
no-op.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m unittest tests.test_portfolio_risk_direction -v`
Expected: PASS.

- [ ] **Step 5: Run the existing risk-controller suite**

Run: `.venv\Scripts\python.exe -m unittest discover -s tests -q`
Expected: all previously-passing tests still pass. A failure in the
max-open-positions or exposure tests means the regroup broke legacy
behaviour — fix before continuing.

- [ ] **Step 6: Commit**

```bash
git add robinhood_tools/portfolio_risk.py tests/test_portfolio_risk_direction.py
git commit -m "feat(risk): account long and short books separately"
```

---

### Task 4: Recalibrate day-trade short sizing to the short budget

**Files:**
- Modify: `scripts/daytrade_engine.py` (`_calc_position_size` and the
  `_validate_setup` short branch around lines 209-246; the risk-gate call site
  around line 547)
- Test: `tests/test_auto_trader_guards.py` (new guard class)

**Interfaces:**
- Consumes: Task 3's `check(..., short=True)` and
  `record_order(..., short=True)`.
- Produces: `_calc_position_size(equity, entry, stop, direction,
  remaining_notional: Decimal | None = None) -> Decimal` — when
  `remaining_notional` is given and the risk-sized notional exceeds it, returns
  the largest `qty` that fits, floored at 1 share, or `Decimal("0")` when fewer
  than one share fits. `_validate_setup` passes the remaining short budget for
  short plans.

- [ ] **Step 1: Write the failing test**

Add a class `ShortBudgetSizingTests` to
`tests/test_auto_trader_guards.py`, importing `daytrade_engine as de`:

```python
def test_short_clamps_to_remaining_budget_not_dropped(self):
    # 0.25% of 100k = 250 risk; stop 1% away => ~2.5% notional risked
    # so raw size is ~25k notional. Budget 5k must clamp it down.
    qty = de._calc_position_size(Decimal("100000"), Decimal("100"),
                                 Decimal("101"), "short",
                                 remaining_notional=Decimal("5000"))
    self.assertGreater(qty, 0)
    self.assertLessEqual(qty * Decimal("100"), Decimal("5000"))

def test_short_returns_zero_when_budget_cannot_hold_one_share(self):
    qty = de._calc_position_size(Decimal("100000"), Decimal("100"),
                                 Decimal("101"), "short",
                                 remaining_notional=Decimal("50"))
    self.assertEqual(qty, 0)

def test_long_size_unchanged_when_no_remaining_given(self):
    qty = de._calc_position_size(Decimal("100000"), Decimal("100"),
                                 Decimal("99"), "long")
    self.assertGreater(qty, 0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m unittest tests.test_auto_trader_guards -v`
Expected: FAIL — `TypeError: _calc_position_size() got an unexpected keyword
argument 'remaining_notional'`.

- [ ] **Step 3: Implement the clamp**

In `scripts/daytrade_engine.py`, extend `_calc_position_size` with the
`remaining_notional` parameter and clamp after the existing risk division:
`qty = min(qty, floor(remaining_notional / entry))`. Apply the clamp only when
`remaining_notional is not None` — the long path keeps today's value because
existing calls do not pass it.

In `_validate_setup`'s short branch, compute the remaining short budget with a
fresh `_call_risk_controller` read (`ctrl.status()["exposure"]["short_notional"]`
vs `max_per_book`) before sizing, pass it to `_calc_position_size`, and return
`False, "short notional budget exhausted", {}` when the clamped qty is 0.

At the risk-gate call site (~line 547), pass `short=True` when
`plan["direction"] == "short"` so the controller checks the short book.

Leave `dry_run=True` and the rest of the pipeline untouched.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m unittest tests.test_auto_trader_guards -v`
Expected: PASS, including all 9 pre-existing guards.

- [ ] **Step 5: Commit**

```bash
git add scripts/daytrade_engine.py tests/test_auto_trader_guards.py
git commit -m "feat(daytrade): size shorts to the short budget, not to death"
```

---

### Task 5: Make the 15:10 options watch put-aware under `RISK_OFF`

**Files:**
- Modify: `scripts/options_watch_trade.py` (`gated_candidates` around lines
  100-115; the entry loop around 209-243)
- Test: `tests/test_options_watch_direction.py`

**Interfaces:**
- Consumes: Task 1's `market_regime`, Task 3's controller (read-only, for a
  budget note in the log), Task 2's `outputs/screener/weak.json`.
- Produces: `_regime() -> str` returning `RISK_ON`/`CHOP`/`RISK_OFF`/`UNKNOWN`
  and failing closed (returns `UNKNOWN`) on any fetch error; and
  `weak_candidates(path='outputs/screener/weak.json') -> list[tuple[str, str]]`
  reading `(symbol, "put")` pairs.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_options_watch_direction.py`. Stub `gated_candidates`'s
screener read by pointing the module at a temp file, and stub SPY bars via
`unittest.mock.patch` on the yfinance-importing helper so no network runs.

```python
def test_risk_off_returns_put_candidates_from_weak_screen(self):
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "weak.json"
        p.write_text(json.dumps({"weak": [{"symbol": "WEAK1", "mom20": -0.3}]}), encoding="utf-8")
        self.assertEqual(weak_candidates(str(p)), [("WEAK1", "put")])

def test_risk_on_uses_long_candidates_unchanged(self):
    # the existing per-row mom20 rule still governs in RISK_ON
    self.assertEqual(_direction_for_row({"mom20": 0.2}, "RISK_ON"), "call")
    self.assertEqual(_direction_for_row({"mom20": -0.2}, "RISK_ON"), "put")

def test_unknown_regime_fails_closed(self):
    self.assertEqual(_direction_for_row({"mom20": -0.9}, "UNKNOWN"), "call")
```

Extract the per-row rule into
`_direction_for_row(row: dict, regime: str) -> str` so this is directly
testable; it is the only behaviour change surface.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m unittest tests.test_options_watch_direction -v`
Expected: FAIL — `ImportError` on the two new names.

- [ ] **Step 3: Implement the regime branch**

In `scripts/options_watch_trade.py`:

1. Add `_direction_for_row(row, regime)`: in `RISK_OFF`, return `"put"`. In
   `UNKNOWN` and `CHOP`, fall back to today's rule (`mom20 >= 0 -> call else
   put`) — `UNKNOWN` deliberately keeps the existing fail-closed direction of
   long-only.
2. Add `_regime()` that reads SPY closes and calls
   `market_prices.market_regime(_spy_closes())`, returning `"UNKNOWN"` on any
   exception. `_spy_closes()` lives in `robinhood_tools/market_prices.py` and
   calls `yfinance.Ticker("SPY").history(period="1y", interval="1d")`, returning
   `[]` on ImportError or any exception — never fetch inside `market_regime()`
   itself, which must stay pure and network-free for its existing tests.
3. In the entry loop, when `_regime() == "RISK_OFF"`, read
   `weak_candidates()` and iterate those `(symbol, "put")` pairs instead of
   `gated_candidates(...)`; otherwise keep the existing call verbatim. Both
   feeds hit the identical downstream gate chain (liquid filter, zone
   confluence, premium cap, contracts, positions, earnings blackout, session
   window) — do not duplicate or reorder it.
4. Log the chosen regime once per run so the daily log shows why a put was or
   was not considered.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m unittest tests.test_options_watch_direction -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/options_watch_trade.py tests/test_options_watch_direction.py
git commit -m "feat(options): 15:10 watch considers puts when SPY is below its 50d mean"
```

---

### Task 6: Certify it all in the offline harness

**Files:**
- Modify: `scripts/paper_certification.py` (`group_risk`, `group_gates`)
- Test: runs offline, no new test file

**Interfaces:**
- Consumes: Tasks 1-5's public behaviour.
- Produces: four new checks in `group_risk()` / `group_gates()`, each inside
  its own `isolated_risk()` block so no earlier check's ledger state can mask
  a later one.

- [ ] **Step 1: Add the risk-controller checks**

In `group_risk()`, add after the existing exposure check:

```
- clean $5,000 long is approved
- $5,001 long is blocked (per-book cap reached)
- short entry is recorded in the short book, and a $5,000 short is approved
  while $5,000 long is already open (books are independent)
- $5,001 short is blocked by the short cap while the long book is full
- legacy state file without a "book" key loads and still tracks the long
```

- [ ] **Step 2: Add the regime-direction checks**

In `group_gates()` add, driving `options_watch_trade` with a stubbed regime and
a temp `weak.json`:

```
- RISK_OFF yields put candidates from the weak screen
- UNKNOWN regime fails closed (no put candidate)
- RISK_ON leaves the long candidate rule untouched
- _spy_closes() returns [] with no yfinance installed, so a missing data
  provider never silently becomes RISK_ON
```

- [ ] **Step 3: Run the offline certification**

Run: `.venv\Scripts\python.exe scripts/paper_certification.py --no-broker`
Expected: `PASS: 43 PASS, 0 FAIL, 0 WARN` (36 existing offline + 7 new, less
the 2 offline SKIPs that remain SKIP).

- [ ] **Step 4: Run the full certification with broker truth**

Run: `.venv\Scripts\python.exe scripts/paper_certification.py`
Expected: `PASS: 46 PASS, 0 FAIL, 0 WARN, 0 SKIP` — broker paper endpoint and
P/L reconciliation unchanged.

- [ ] **Step 5: Run the whole suite and preflight**

Run: `.venv\Scripts\python.exe -m unittest discover -s tests -q`
Expected: `OK` with no new failures.

Run: `.venv\Scripts\python.exe scripts/preflight.py --full`
Expected: 11 checks, `0 FAIL`.

- [ ] **Step 6: Commit**

```bash
git add scripts/paper_certification.py
git commit -m "test(cert): prove the short book and the bear-regime direction"
```

---

## Notes for the executor

- `outputs/` is a junction. When a step references a screener file, the real
  path is `%LOCALAPPDATA%\AI-cio-outputs\screener\`.
- The desk venv (`ai-cio-trading-ops\.venv`) has no pytest; use
  `.venv\Scripts\python.exe -m unittest`.
- `scripts/daytrade_engine.py` and the options watch stay in dry-run. Do not
  flip `dry_run` or add `--execute` anywhere in this plan.
- The `overnight_pilot.py` `uptrend_ok` `NameError` is deliberately **not**
  fixed here; it is recorded in the spec as a separate change.
