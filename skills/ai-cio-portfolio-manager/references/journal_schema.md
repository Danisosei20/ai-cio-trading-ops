# Journal Schema

Maintain a durable CSV journal when possible.

Preferred columns:

- date
- time_et
- account
- action
- symbol
- asset_class
- order_type
- amount_usd
- quantity
- status
- order_id
- thesis
- recommendation
- score
- probability
- reward_risk
- current_price
- intended_price
- avg_volume_20d
- avg_volume_50d
- relative_volume
- avg_daily_dollar_volume
- bid_ask_spread_pct
- order_pct_avg_volume
- return_20d
- return_60d
- relative_strength_sp500
- realized_volatility_20d
- atr_14d
- max_drawdown
- next_earnings_date
- signal_summary
- invalidation_level
- target_or_review_condition
- research_sources
- approval
- benchmark
- outcome
- lesson
- outcome_1d
- outcome_5d
- outcome_20d
- benchmark_20d
- excess_return_20d
- thesis_accuracy
- execution_slippage
- notes

## Learning Loop

At 1, 5, and 20 trading days after a recommendation or fill, record price outcome, benchmark outcome, thesis status, execution slippage, and whether volume/trend/event assumptions were correct. Attribute errors to thesis, valuation, timing, sizing, execution, missing information, or external shock. Do not change policy from one outcome; update weights or rules only after a repeated, documented pattern with enough observations to distinguish learning from noise.

Append a row after:

- Portfolio review
- Recommendation
- Order review
- User approval
- Real order placement
- Cancellation
- Fill status update
- Outcome review
- Process lesson

Maintain shadow-equity observations separately from live recommendations and fills. Record at most one qualifying
paper candidate or explicit shadow no action per daily run. Use a distinct recommendation ID and `paper` status,
never an approval ID. Schedule the same 1/5/20-trading-day learning checkpoints and compare the shadow outcome
with both the underlying equity decision and S&P 500.

Use `scripts/update_journal.py` when a local CSV journal is available.

If an existing journal uses an older schema, migrate it to these columns before appending. Do not append new-schema rows to an old header.
