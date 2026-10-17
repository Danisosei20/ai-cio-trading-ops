# Nexus — the trading intelligence office

Run from the repository root:

```sh
python3 scripts/agent_office.py
```

Open http://localhost:8766. Use `--port` to choose another port.

The five workers represent the task-mode fleet stations: Screen, Research,
News, Verify and Report. The viewer polls every two seconds. New task-mode
runs publish `outputs/office-state.json` atomically with a five-second heartbeat.
An expired heartbeat or dead process stops live claims. Older
`outputs/fleet-summary.json` results are explicitly historical. Legacy
per-ticker runs are not animated as live task-mode work.

**Watch demo** runs a browser-only sample cycle, including a blocked task.
**Return to real fleet** restores actual status. Workers wander when idle;
that ambient motion is not evidence of task execution. Select a worker in
the right panel for their role. Motion can be paused and respects the
operating system's reduced-motion preference.

The futuristic view includes robotic agents with distinct face displays,
colour-coded holographic terminals, a signal journal, furniture-aware walking
paths, data-cube deliveries, server racks and a suspended intelligence core.
The core and network artwork are ambient illustrations, not measured telemetry.
Use the view controls to zoom, fit the room or enter full screen. Select agents
directly in the scene or with the keyboard-accessible team buttons. All office
actions are visual representations of task state.

This server binds to loopback, serves only its three fixed UI assets and
sanitized status, and has no order or task-launch endpoint. Opening it does
not run research, call an LLM, send Slack messages or unpark the desk.
