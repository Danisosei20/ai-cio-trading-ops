# Phase 8.9 — Controlled Paper-Pilot Readiness Package (NOT AUTHORIZED)

This is a preparation artifact only. No pilot is authorized, no enforcement flag
is changed, no demonstration order is placed and no strategy permission is added.
All limits below already exist in the repository; none is loosened.

## 1. Proposed scope (for review)

- Account: Alpaca **paper only**; `trading_enabled` kill switch must remain closed.
- Eligible strategies: existing SPEC v1.0.0 `long_reclaim` and `short_breakdown`
  only, after independent out-of-sample validation.
- Instruments: S&P 500 members and index ETFs with ≥24 h membership; no new
  symbol universe (`config/approval_routes.json:12-25`).
- Session: regular session only, 09:45–15:45 ET; no new entries after 15:15 ET;
  flat by 15:45 (`daytrade_engine.py:51-52`).
- Exposure: 0.25% risk per trade, max 2 day trades, $10,000 total notional,
  R:R ≥ 1.5 (`daytrade_engine.py:48-53`).
- Broker policy: max order $25 and max symbol exposure $25 in `risk_limits`,
  paper override $2,000, min cash reserve $50, max open positions 1, max daily
  loss $5, max weekly loss $10 (`config/approval_routes.json:27-57`).
- Shorts: require fresh broker `shortable=True`, `borrow_status=easy_to_borrow`,
  `shorting_enabled=True`, ACTIVE/unrestricted account, max evidence age 120 s
  (`robinhood_tools/short_eligibility.py:16,65-89`). No borrow availability is
  invented.
- Kill switch: `outputs/paper/KILL_SWITCH`, daily drawdown 3%, weekly 7%;
  risk-reducing exits remain exempt (`robinhood_tools/entry_risk.py:14-25,45-62`).
- Authorization: parent TTL 8 h, child TTL 300 s, ≤20 children, atomic single-use
  gateway, position-effect binding (`execution_handoff.py:105`,
  `authorization_propagation.py:18,68`, `execution_authorization.py:520-544`).
- Oversight: human approval path and audit retention 2555 days
  (`config/approval_routes.json:93-98,168-174`).

## 2. Prerequisites not yet met

1. Deploy and verify the 24 hardened operational files (owner authorization
   pending; see `phase-8-9-deployment-verification.md`).
2. Obtain real scheduler evidence for protective tasks; Operational logging
   remains disabled.
3. Enable and rehearse authorization enforcement; currently OFF. Close-order
   verification at the broker boundary is conditional on the same flag.
4. Produce signal-expiration evidence; current 32 observations yield 0
   `ACTIONABLE_AT_DECISION`, 1 cutoff exceedance and 31 insufficient-evidence
   cases.
5. Produce an immutable multi-session point-in-time dataset; none exists.
6. Complete walk-forward and out-of-sample evaluation; current verdict
   NOT_VERIFIED for both long and short rules.
7. Confirm zero uncertain submission intents and verified flatten scope.

## 3. Automatic stop conditions (existing policy, no new allowances)

- Kill switch file present; daily/weekly drawdown limits exceeded; RISK_OFF or
  unsupported regime; missing/stale market data; 15:15 cutoff; 15:45 flatten;
  audit store unhealthy; authorization key store unhealthy; any uncertain broker
  response requiring reconciliation.

## 4. Rollback

Deployment rollback is tested (`deploy_hardened_launchers.py --rollback
<backup-manifest> --owner-authorized`). For an authorized pilot, the operator
would restore the pre-deployment files, keep enforcement OFF, remove any pilot
enablement and re-verify task results. No rollback has been applied.

## 5. Verdict

**NOT AUTHORIZED.** This package defines what must be evidenced before a
separate pilot approval decision; it is not that decision.
