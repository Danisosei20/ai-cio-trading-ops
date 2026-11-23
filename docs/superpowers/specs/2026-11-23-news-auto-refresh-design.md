# News view auto-refresh — design

## Intent

The News section rendered one snapshot when opened, so a newly written news
artifact was not visible until the page was reloaded. It should keep showing
the latest sourced artifact while open.

## Approaches

1. **Bounded GET polling in the view (chosen).** Poll `/api/news` every 30s
   while the tab is visible. The endpoint already selects the newest
   explanation artifact by mtime and is GET-only/read-only, so no server
   change is needed.
2. **Reload the iframe from the shell.** Rejected: only refreshes on tab
   switch, not while the section is open.
3. **Push updates (WebSocket/SSE).** Rejected: adds a stateful write-capable
   path to a viewer that is deliberately loopback-only and GET-only.

## Decisions

- `setInterval(..., 30000)`; skipped when `document.hidden`.
- An `inFlight` flag prevents overlapping fetches.
- The existing `AbortSignal.timeout(4000)` bound is retained.
- A failed or non-`ok` response fails closed: `unavailable()` clears stale
  rows and the next tick retries, so a transient error never leaves a stale
  snapshot presented as current.
- Only `textContent`/`createElement` rendering; no `innerHTML`, no animation
  loop, no order/gate path. The prior "no loop" test is updated to allow a
  bounded timer while still forbidding `requestAnimationFrame`.
