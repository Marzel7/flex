#!/bin/bash

# Helius Webhook Quick Start Script
# This script guides you through setting up real Helius webhooks

set -e

echo "╔════════════════════════════════════════════════════════════════╗"
echo "║     HELIUS WEBHOOK SETUP - QUICK START                        ║"
echo "╚════════════════════════════════════════════════════════════════╝"
echo ""

# Check dependencies
echo "📋 Checking dependencies..."

if ! command -v ngrok &> /dev/null; then
    echo "❌ ngrok not found. Install with: brew install ngrok"
    exit 1
fi
echo "✅ ngrok found"

if ! command -v sqlite3 &> /dev/null; then
    echo "❌ sqlite3 not found"
    exit 1
fi
echo "✅ sqlite3 found"

# Check API key
echo ""
echo "🔑 Checking Helius API key..."
if [ -f ".env" ]; then
    API_KEY=$(grep "HELIUS_API_KEY=" .env | cut -d= -f2)
    if [ -n "$API_KEY" ]; then
        echo "✅ Found API key in .env"
        export HELIUS_API_KEY="$API_KEY"
    fi
fi

if [ -z "$HELIUS_API_KEY" ]; then
    echo "❌ HELIUS_API_KEY not set. Add it to .env file."
    echo "   HELIUS_API_KEY=your_key_here"
    exit 1
fi

# Check database
echo ""
echo "📁 Checking database..."
if [ ! -f "flex_complete_database.db" ]; then
    echo "❌ Database not found: flex_complete_database.db"
    exit 1
fi
echo "✅ Database found"

# Count creators
CREATOR_COUNT=$(sqlite3 flex_complete_database.db "SELECT COUNT(DISTINCT creator) FROM creator_scan_priority" 2>/dev/null || echo "0")
echo "   Found $CREATOR_COUNT creators in database"

# Check sync script
echo ""
echo "🔄 Checking sync script..."
if [ ! -f "helius_webhook_sync_m5.py" ]; then
    echo "❌ Sync script not found: helius_webhook_sync_m5.py"
    exit 1
fi
echo "✅ Sync script found"

echo ""
echo "╔════════════════════════════════════════════════════════════════╗"
echo "║               SETUP READY - NEXT STEPS                        ║"
echo "╚════════════════════════════════════════════════════════════════╝"
echo ""

echo "Run these commands in separate terminals:"
echo ""

echo "📡 TERMINAL 1: Start ngrok tunnel"
echo "  $ ngrok http 5002"
echo "  (copy the HTTPS URL from output, e.g., https://abc123.ngrok.io)"
echo ""

echo "⚙️  TERMINAL 2: Configure Helius webhooks"
echo "  $ export HELIUS_API_KEY=\"$HELIUS_API_KEY\""
echo "  $ export WEBHOOK_URL=\"https://YOUR_NGROK_URL/helius/webhook\""
echo "  $ export CREATOR_LIMIT=1000"
echo "  $ export CREATE_MISSING=1"
echo "  $ python helius_webhook_sync_m5.py --once"
echo ""

echo "🚀 TERMINAL 3: Start Flask app"
echo "  $ python3 main.py"
echo ""

echo "📊 TERMINAL 4: Watch webhook metrics"
echo "  $ watch -n 5 'curl -s http://localhost:5002/api/webhook-status | jq'"
echo ""

echo "🌐 Then open browser:"
echo "  http://localhost:5002/webhook-monitor"
echo ""

echo "For more details, see: HELIUS_WEBHOOK_SETUP_GUIDE.md"
echo ""
