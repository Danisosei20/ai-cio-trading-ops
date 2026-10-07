# Trading paper stack image (simulation-safe defaults).
# Build from the repo root so the package + config template ship together:
#   docker build -f deploy/docker/Dockerfile -t trading-paper:local .
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    # Safe defaults: research only, everything that can place is off.
    TRADING_MODE=research_only \
    TRADING_ENABLED=false \
    PAPER_TRADING_ENABLED=false \
    ALERT_WEBHOOK_PORT=8090 \
    # Non-secret personal routing required by config schema (no secrets here).
    TIMEZONE=America/New_York \
    APPROVAL_WINDOW_MINUTES=120 \
    ROBINHOOD_ACCOUNT_NICKNAME=Agentic \
    INVESTMENT_OBJECTIVE="long-term growth" \
    RISK_TOLERANCE=moderate \
    TAX_CONTEXT=taxable \
    SLACK_CHANNEL_ID=unset \
    HEALTH_SLACK_CHANNEL_ID=unset

WORKDIR /app
COPY pyproject.toml README.md ./
COPY robinhood_tools/ ./robinhood_tools/
COPY scripts/ ./scripts/
COPY config/approval_routes.example.json ./config/approval_routes.json
RUN pip install --no-cache-dir yfinance && \
    useradd --create-home --uid 10001 trader && \
    mkdir -p /app/outputs && chown -R trader:trader /app

USER trader
EXPOSE 8090
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python3 -c "import urllib.request,sys;sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8090/health',timeout=4).status==200 else 1)"
CMD ["sh", "-c", "ALERT_WEBHOOK_SECRET=${ALERT_WEBHOOK_SECRET:-unset} python3 scripts/alert_webhook.py --host 0.0.0.0 --port ${ALERT_WEBHOOK_PORT}"]
