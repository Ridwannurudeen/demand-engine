#!/bin/bash
set -e

echo "Starting Demand Engine..."
echo "ANTHROPIC_API_KEY set: $([ -n "$ANTHROPIC_API_KEY" ] && echo 'yes' || echo 'NO')"
echo "TELEGRAM_BOT_TOKEN set: $([ -n "$TELEGRAM_BOT_TOKEN" ] && echo 'yes' || echo 'NO')"
echo "ACP_AGENT_WALLET set: $([ -n "$ACP_AGENT_WALLET" ] && echo 'yes' || echo 'NO')"
echo "ACP_CONFIG_JSON set: $([ -n "$ACP_CONFIG_JSON" ] && echo 'yes' || echo 'NO')"

# Copy ACP config if provided via env var
if [ -n "$ACP_CONFIG_JSON" ]; then
    echo "$ACP_CONFIG_JSON" > /app/openclaw-acp/config.json
    echo "ACP config written"
else
    echo "WARNING: ACP_CONFIG_JSON not set — seller will fail to authenticate"
fi

# Start ACP seller runtime in background
cd /app/openclaw-acp
npx tsx src/seller/runtime/seller.ts &
SELLER_PID=$!
echo "ACP seller started (PID: $SELLER_PID)"

# Start Telegram bot (foreground)
cd /app
echo "Starting Telegram bot..."
exec python -m src.main
