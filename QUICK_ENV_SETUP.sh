#!/bin/bash
# Quick Environment Setup for Trading

# This script helps you set up TRADING_KEYPAIR and HELIUS_API_KEY
# Usage: bash QUICK_ENV_SETUP.sh

echo "🔑 Quick Trading Environment Setup"
echo "=================================="
echo ""

# Get keypair
read -p "Enter path to keypair JSON file: " KEYPAIR_FILE

if [ ! -f "$KEYPAIR_FILE" ]; then
    echo "❌ File not found: $KEYPAIR_FILE"
    exit 1
fi

# Read keypair
TRADING_KEYPAIR=$(python3 -c "import json; f=open('$KEYPAIR_FILE'); print(json.dumps(json.load(f)))")

if [ $? -ne 0 ]; then
    echo "❌ Failed to read keypair file"
    exit 1
fi

echo "✅ Keypair loaded"
echo ""

# Get Helius key
read -p "Enter your Helius API key: " HELIUS_API_KEY

if [ -z "$HELIUS_API_KEY" ]; then
    echo "❌ API key required"
    exit 1
fi

echo "✅ API key accepted"
echo ""

# Show export commands
echo "📋 Copy and paste these commands:"
echo ""
echo "export TRADING_KEYPAIR='$TRADING_KEYPAIR'"
echo "export HELIUS_API_KEY=\"$HELIUS_API_KEY\""
echo ""

# Ask to save
read -p "Save to shell profile? (y/n): " SAVE

if [ "$SAVE" = "y" ]; then
    SHELL_PROFILE="$HOME/.zshrc"
    if [ ! -f "$SHELL_PROFILE" ]; then
        SHELL_PROFILE="$HOME/.bash_profile"
    fi

    echo "" >> "$SHELL_PROFILE"
    echo "# Trading environment variables" >> "$SHELL_PROFILE"
    echo "export TRADING_KEYPAIR='$TRADING_KEYPAIR'" >> "$SHELL_PROFILE"
    echo "export HELIUS_API_KEY=\"$HELIUS_API_KEY\"" >> "$SHELL_PROFILE"

    echo "✅ Saved to $SHELL_PROFILE"
    echo ""
    echo "Run: source $SHELL_PROFILE"
    echo "Then: python3 test_buy_only.py"
else
    echo "📌 Run the export commands above, then:"
    echo "   python3 test_buy_only.py"
fi

echo ""
echo "✅ Setup complete!"
