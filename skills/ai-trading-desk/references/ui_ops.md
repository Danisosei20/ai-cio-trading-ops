# UI & Ops

## Desk UI (`scripts/desk_server.py`)

```bash
python3 scripts/desk_server.py --port 8100   # http://127.0.0.1:8100/
```

Single process, stdlib only, 5s polling. Cards: Today, Autonomy + kill
switch, AI debates (confidence bars, judge, risk), Positions/orders,
Backtests, Charts (price vs strategy equity + TradingView), Research runs,
Position guard, Run log. API mirrors each card under `/api/*`.

- Read-only except `/api/kill` and `/api/resume`, which need
  `{"confirm": true}` and only flip the emergency flag (never liquidate).
- Binds loopback with no auth: do NOT expose to a network without adding
  authentication.
- Degrades cleanly: broker-down shows placeholders, never 500s or secrets
  (only masked account numbers; error names only, no values).

## Webhook (`scripts/alert_webhook.py`)

Loopback-first receiver. `POST /alert` needs `symbol/side/qty/limit/secret`
plus `earnings_date` (YYYY-MM-DD) for buys. `GET /health`, `GET /recent`.
Same gates as the trader; no yfinance dependency (stdlib only).

## Kubernetes (`deploy/k8s/`, cluster `trading-k3s`)

Namespace `trading`, research-only ConfigMap, single-replica Deployment with
probes/limits, ClusterIP Service, secret template (External Secrets shape).
Fresh installs cannot place. Promote to paper shadow only via operator
ConfigMap edit + real secrets, after shadow evidence + owner sign-off.

```bash
k3d image import trading-paper:local -c trading-k3s
kubectl --context k3d-trading-k3s apply -f deploy/k8s/namespace.yaml \
  -f deploy/k8s/configmap.yaml -f deploy/k8s/webhook.yaml
kubectl --context k3d-trading-k3s -n trading rollout status deploy/paper-webhook
```

## Files that must never be shared

`.env`, `outputs/` (databases, logs, dashboards), `tradingagents_data`,
anything under `~/.tradingagents` with keys. Run
`python3 scripts/secret_scan.py` before publishing or sending the project.
