#!/bin/bash

# Setup WebSocket pool subscription environment
# Prompts for Helius API key and configures environment

echo "🌊 WebSocket Pool Subscription Setup"
echo "===================================="
echo ""

# Check if API key is already set
if [ -n "$HELIUS_API_KEY" ]; then
    echo "✓ HELIUS_API_KEY already set: ${HELIUS_API_KEY:0:20}..."
    CONFIRM=""
    read -p "Use existing key? (y/n) [y]: " CONFIRM
    CONFIRM=${CONFIRM:-y}
    if [ "$CONFIRM" = "y" ]; then
        API_KEY="$HELIUS_API_KEY"
    else
        unset HELIUS_API_KEY
    fi
fi

# Prompt for API key if not set
if [ -z "$API_KEY" ]; then
    echo "Enter your Helius API key (get one at https://www.helius.dev/)"
    echo "Or press Enter to use defaults (no key):"
    read -s API_KEY
    echo ""
fi

# Build URLs
if [ -n "$API_KEY" ]; then
    export HELIUS_RPC_URL="https://mainnet.helius-rpc.com/?api-key=$API_KEY"
    export HELIUS_WS_URL="wss://mainnet.helius-rpc.com/?api-key=$API_KEY"
    echo "✓ Configured with API key: ${API_KEY:0:20}..."
else
    export HELIUS_RPC_URL="https://mainnet.helius-rpc.com/?api-key="
    export HELIUS_WS_URL="wss://mainnet.helius-rpc.com/?api-key="
    echo "✓ Using default endpoints (rate limited)"
fi

echo ""
echo "Environment variables set:"
echo "  HELIUS_RPC_URL=$HELIUS_RPC_URL"
echo "  HELIUS_WS_URL=$HELIUS_WS_URL"
echo ""

# Offer to restart services
echo "Ready to start services. Run:"
echo "  ./scripts/restart.sh"
echo ""

# Optional: Save to .env file
read -p "Save to .env file? (y/n) [n]: " SAVE_ENV
SAVE_ENV=${SAVE_ENV:-n}

if [ "$SAVE_ENV" = "y" ]; then
    cat > .env << EOF
# WebSocket Pool Subscription Configuration
export HELIUS_RPC_URL="$HELIUS_RPC_URL"
export HELIUS_WS_URL="$HELIUS_WS_URL"
EOF
    echo "✓ Saved to .env"
    echo ""
    echo "Next time, load environment with:"
    echo "  source .env"
fi
