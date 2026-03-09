#!/bin/bash

# Run Helius CLI monitor
# Suitable for cron jobs or background execution
# Uses reorganized src/ structure

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_ROOT"
python -m src.monitoring.helius_cli_monitor >> /tmp/helius_cron.log 2>&1
