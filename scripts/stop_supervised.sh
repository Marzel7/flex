#!/bin/bash
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
SUPERVISORCTL="/Users/kevinkeaveney/anaconda3/envs/algotrader/bin/supervisorctl"
CONF="$PROJECT_ROOT/config/supervisor/supervisord.conf"

echo "🛑 Stopping WATCHTOWER..."
"$SUPERVISORCTL" -c "$CONF" shutdown 2>/dev/null || true
pkill -f "supervisord.*watchtower" 2>/dev/null || true
pkill -f "caffeinate" 2>/dev/null || true
echo "✓ All processes stopped"
