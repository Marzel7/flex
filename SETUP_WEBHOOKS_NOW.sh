#!/bin/bash

# Helius Webhook Setup - Ready to Run
# This script sets up real Helius webhooks with your ngrok URL

HELIUS_API_KEY="f084fae8-d111-4337-9960-2d9c5e02a726"
WEBHOOK_URL="https://uncatholical-rylie-phrenetically.ngrok-free.dev/helius/webhook"
CREATOR_LIMIT=1000
CREATE_MISSING=1

echo "╔════════════════════════════════════════════════════════════════╗"
echo "║     HELIUS WEBHOOK SETUP - READY TO RUN                      ║"
echo "╚════════════════════════════════════════════════════════════════╝"
echo ""

echo "Configuration:"
echo "  API Key: ****...726 (from .env)"
echo "  Webhook URL: $WEBHOOK_URL"
echo "  Creator Limit: $CREATOR_LIMIT"
echo "  Auto-create: Enabled"
echo ""

echo "Starting webhook sync..."
echo ""

export HELIUS_API_KEY="$HELIUS_API_KEY"
export WEBHOOK_URL="$WEBHOOK_URL"
export CREATOR_LIMIT=$CREATOR_LIMIT
export CREATE_MISSING=1

python helius_webhook_sync_m5.py --once

if [ $? -eq 0 ]; then
    echo ""
    echo "✅ Webhooks configured successfully!"
    echo ""
    echo "Next steps:"
    echo "  1. Verify Flask app is running: python3 main.py"
    echo "  2. Open browser: http://localhost:5002/webhook-monitor"
    echo "  3. Watch for webhooks arriving in real-time!"
    echo ""
else
    echo ""
    echo "❌ Webhook setup failed. Check errors above."
    exit 1
fi
