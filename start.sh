#!/bin/bash
set -e

echo "Starting Demand Engine..."

# Copy ACP config if provided via env var
if [ -n "$ACP_CONFIG_JSON" ]; then
    echo "$ACP_CONFIG_JSON" > /app/openclaw-acp/config.json
    echo "ACP config written"
fi

# Start ACP seller runtime in background
cd /app/openclaw-acp
npx tsx src/seller/runtime/seller.ts &
SELLER_PID=$!
echo "ACP seller started (PID: $SELLER_PID)"

# Start Telegram bot (foreground)
cd /app
python -m src.main
