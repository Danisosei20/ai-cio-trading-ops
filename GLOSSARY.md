# Glossary

## Gate stage

One of the deterministic checks whose result feeds the desk's go/no-go
decision. A gate stage produces an `ok`, `warn` or `fail` report from local
artifacts. It is replayable: the same artifacts always produce the same report.

## AI agent

A model-backed task that gathers evidence, explains a move or proposes a
basket. An AI agent produces research, never a verdict or an order. Its output
is a sourced artifact that a human can review.

## Proceed

The only verdict that permits the order stage to run. A `proceed` requires a
tradeable session, a paper-only safety snapshot and `ok` from every required
gate stage.
