# Flat-book evidence — design

## Intent

On a day with no open option positions, the OddsAnalyst and ScenarioAnalyst
stages had incomplete evidence: `options_time_machine.py --book` exited 2
("no symbols given") and `scenario_analysis.py` returned early without
writing. The gate then carried a failed stage and a stale artifact describing
positions the desk no longer held.

## Approaches

1. **Write a dated empty snapshot (chosen).** When the book is genuinely flat,
   both producers write a fresh artifact with an empty result set and a
   `reason: "no open option positions"`. A flat book is a real state, not a
   failure. An explicit `--symbol` mode with no symbols still exits 2.
2. **Skip the stages on a flat book.** Rejected: changes stage semantics and
   requires orchestrator edits, which would touch the gate registry.
3. **Reuse the previous artifact.** Rejected: describes positions no longer
   held; misleading and stale.

## Decisions

- `scenario_analysis.main`: when `fetch_book()` yields nothing, write
  `book: []`, `scenarios: []`, `generated_at`, and the reason; return 0.
- `options_time_machine.main`: when `--book` yields no symbols, write
  `results: []`, `generated_at`, `model`, and the reason; return 0. A plain
  invocation with neither `--symbol` nor `--book` still returns 2.
- No gate thresholds, verdict logic, or order path changed. Both artifacts
  remain research/diagnostic evidence only.
