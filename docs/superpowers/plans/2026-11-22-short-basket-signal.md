# Short-Basket Signal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a read-only $500 five-name short-basket proposal, ranked from the desk's own bars, with a new viewer tab.

**Architecture:** A pure `short_basket(root, *, capital, count)` in `scripts/short_basket.py` ranks symbols by weakness from the latest bar artifacts and returns a sourced, fail-closed payload. `agent_office.py` serves GET `/api/basket` and a new `/basket` page. The shell gains a sixth "Basket" tab.

**Tech Stack:** Python 3.11 stdlib, `unittest`, vanilla HTML/CSS/JS, existing read-only viewer.

**Spec:** `docs/superpowers/specs/2026-11-22-short-basket-signal-design.md`

## Global Constraints

- Read-only loopback viewer: GET only, fixed assets, no write/order endpoint. No gate, risk-controller, scheduler or stage-agent change.
- Every displayed value sourced from a real local artifact; source shown verbatim.
- Fail closed: missing/malformed/insufficient bars → unavailable, never a plausible empty basket.
- Point-in-time only: bars from completed sessions preceding the proposal; no look-ahead.
- Reuse `office.css` tokens; no new accent color; no animation loops.
- Verification: full `unittest` suite, ruff, `node --check` on changed JS, live browser smoke.
- Windows host; file tools for bookkeeping.
- Do not modify the owner's uncommitted files in the main checkout.

## Review Focus

- **Unsourced name** → a basket row without a real artifact. Test: `test_every_row_has_source` (Task 1).
- **Look-ahead** → using future bars or filename order to pick a winner. Test: `test_latest_run_by_mtime` and `test_rank_by_weakness` (Task 1).
- **Insufficient data** → a symbol with one session must be skipped, not proposed. Test: `test_skips_symbols_with_too_few_sessions` (Task 1).
- **Order path** → no write endpoint or order call added. Test: `test_read_only_basket_routes` (Task 1).
- **XSS / NaN** → symbol rendered safely, no `NaN` text. Test: `test_basket_renderer_contract` (Task 2).

---

### Task 1: Signal assembler + API route

**Files:** `scripts/short_basket.py` (new), `scripts/agent_office.py`, `tests/test_short_basket.py` (new)

**Interfaces:**
- Produces `short_basket(root: Path, *, capital: float = 500.0, count: int = 5) -> dict`
- Returns `{"status":"unavailable","reason":str}` or:
```
{"status":"ok","generated_at":str,"capital":float,"count":int,
 "basket":[{"symbol":str,"reference_price":float,"change_pct":float,
            "allocation":float,"shares":float,"notional":float,"source":str}],
 "sources":[str]}
```
- Consumed by Task 2 via GET `/api/basket`.

- [ ] **Step 1 (RED):** Write failing tests in `tests/test_short_basket.py` using `tempfile`:
  - `test_unavailable_on_no_artifacts`: empty root → unavailable.
  - `test_unavailable_on_malformed_bars`: a `bars_X.json` with `bars` not a list → unavailable when no valid symbol remains.
  - `test_rank_by_weakness`: seed `bars_A.json` closing 100→80 (−20%) and `bars_B.json` 100→95 (−5%); assert A ranks before B and `change_pct` equals −20.0 and −5.0.
  - `test_skips_symbols_with_too_few_sessions`: a symbol with one session is excluded.
  - `test_latest_run_by_mtime`: two run directories with different mtimes; the fresher one supplies the prices.
  - `test_every_row_has_source`: every basket row has a non-empty source containing the artifact path.
  - `test_finite_numbers`: no NaN/inf in any numeric field.
  - `test_read_only_basket_routes`: `"/api/basket"` and `"/basket"` in agent_office.py, `do_POST` absent.

- [ ] **Step 2:** Run focused tests; watch them fail for the right reason.

- [ ] **Step 3 (GREEN):** Implement `short_basket`:
  - Choose latest `outputs/orchestrator/*/bars_*.json` run dir by mtime (deterministic lexicographic tie-break).
  - For each valid bar doc: need ≥2 distinct session dates; compute first session's first close and last session's last close; `change_pct = (last/first - 1) * 100`; reference price = last close. Skip non-finite or ≤0 prices.
  - Sort ascending by `change_pct`, take first `count`; allocation = `capital/count`, shares = allocation/reference_price, notional = shares*reference_price.
  - `source` = relative artifact path + `@` + doc's `generated_at`. If fewer than `count` valid symbols → return whatever is available; if none → unavailable.
  - In `agent_office.py::do_GET`, add `elif route == "/api/basket":` returning `json.dumps(short_basket(ROOT))`, `application/json`. No write route.

- [ ] **Step 4:** Focused test PASS, full suite, ruff clean.
- [ ] **Step 5:** Commit: `feat: build sourced short-basket signal`.

### Task 2: Basket page and renderer

**Files:** `web/agent-office/basket.html` (new), `web/agent-office/basket.js` (new), `scripts/agent_office.py`, `tests/test_basket_ui.py` (new)

- [ ] **Step 1 (RED):** Static tests: `basket.js` contains `/api/basket`, `data unavailable`, `textContent`, `source`, `signal only`; `basket.html` has `id="basket-status"`, `id="basket-rows"`, `id="basket-note"` and loads `/basket.js`; `agent_office.py` serves `/basket` and `/basket.js`.
- [ ] **Step 2:** Run focused tests, watch fail.
- [ ] **Step 3 (GREEN):** Create `basket.html` mirroring `market.html` header/brand with a back link, a banner "Signal only — no orders placed", and a table of proposal rows. Create `basket.js`: on load fetch `/api/basket` with timeout; on failure/non-ok show "data unavailable" and empty state; otherwise render one row per basket entry via `createElement`/`textContent`, format numbers with `toFixed`, show every source verbatim, and display capital and total notional. No animation loops. Add real `/basket` route and `/basket.js` static asset in `agent_office.py`.
- [ ] **Step 4:** Focused test, full suite, ruff, `node --check web/agent-office/basket.js`.
- [ ] **Step 5:** Commit: `feat: add read-only short-basket signal page`.

### Task 3: Shell tab

**Files:** `web/agent-office/shell.html`, `web/agent-office/shell.js`, `tests/test_unified_shell.py`

- [ ] **Step 1 (RED):** Tests: `shell.html` has `data-view="basket"` exactly once; `shell.js` has `basket:"/basket"` in VIEW_SRC and `basket:"Basket"` in VIEW_TITLE exactly once.
- [ ] **Step 2:** Run focused tests, watch fail.
- [ ] **Step 3 (GREEN):** Add the sixth nav button labelled "Basket" and the two map entries. No other behavior change.
- [ ] **Step 4:** Focused test, full suite, ruff, `node --check web/agent-office/shell.js`.
- [ ] **Step 5:** Commit: `feat: add Basket tab to the desk shell`.

### Task 4: Out-of-sample validation (no code changes)

- [ ] Fetch ~60 days of daily prices for a fixed universe with yfinance in a one-off script and walk-forward test: each window ranks the weakest five by prior-window return, shorts them equal-weight for the next window, with costs. Report results across non-overlapping windows. State plainly whether the rule survives out-of-sample. Do not wire results into the UI.
- [ ] Final full suite, ruff, node --check, live browser smoke on a spare port.
- [ ] Confirm `git status --short` touches only the plan's files and the owner's uncommitted files are untouched.
