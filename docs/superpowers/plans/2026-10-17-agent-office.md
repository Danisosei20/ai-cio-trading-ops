# Agent Office Implementation Plan

**Goal:** A second-monitor pixel office showing Screen, Research, News, Verify and Report workers.

**Architecture:** A loopback Python server serves a dependency-free canvas UI and reads fleet task artifacts. Task-mode fleet writes atomic lifecycle snapshots; old artifacts remain visible as historical results. Demo runs entirely in the browser.

**Tech Stack:** Python standard library, HTML, CSS, Canvas, JavaScript.

**Spec:** User-approved chat design: angled pixel office, five named workers, task bubbles, completion board, idle coffee breaks, explicit Demo, failures shown as blocked.

## Constraints and review focus
- Read-only viewer; never starts a fleet or places trades.
- Historical artifacts are not evidence of an active task.
- Failed child research runs and failed verification must appear blocked.
- Missing or malformed data must not appear successful.
- Disconnects must visibly stop live claims; reduced motion is supported.
- Serve only fixed assets and sanitized status, never arbitrary workspace files.

## Tasks
- [x] Add fleet lifecycle snapshots and status classification; test failure/empty/stale cases.
- [x] Implement loopback viewer server and office UI with live, idle and demo states.
- [x] Run targeted tests, inspect browser, document launch command.

Verification: 13 office/fleet tests pass; JavaScript syntax check passes; browser renders five workers and switches to labelled demo without overflow at the inspected desktop size.
