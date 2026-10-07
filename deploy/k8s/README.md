# Kubernetes rollout for the paper stack.
#
# Phase order (no big bang):
#   1. Local: docker build + compose health check (below).
#   2. Kind/minikube: kubectl apply -k deploy/k8s (add kustomization later),
#      verify /health, verify a bad-secret alert is rejected.
#   3. Staging namespace running shadow mode against paper brokers.
#   4. Production only after shadow evidence + owner sign-off.
#
# Safe defaults: fresh installs run TRADING_MODE=research_only with placement
# disabled. Promoting to paper shadow is an operator ConfigMap edit, never
# model output. Secrets come from External Secrets Operator, never git.

# --- local build + smoke test -------------------------------------------
# docker build -f deploy/docker/Dockerfile -t trading-paper:local .
# docker run --rm -p 8090:8090 -e ALERT_WEBHOOK_SECRET=test-secret trading-paper:local &
# curl -s localhost:8090/health
# curl -s -X POST localhost:8090/alert -H 'Content-Type: application/json' \
#   -d '{"symbol":"NVDA","side":"buy","qty":1,"limit":230.00,"secret":"wrong"}'
# docker compose -f deploy/docker/docker-compose.yml up -d  # + postgres/redis

# --- cluster --------------------------------------------------------------
# kubectl apply -f deploy/k8s/namespace.yaml \
#               -f deploy/k8s/configmap.yaml \
#               -f deploy/k8s/webhook.yaml
# # secrets via External Secrets Operator (same name/keys as secret.example.yaml)
# kubectl -n trading rollout status deploy/paper-webhook
# kubectl -n trading port-forward svc/paper-webhook 8090:8090
# curl -s localhost:8090/health   # {"ok":true,...}
