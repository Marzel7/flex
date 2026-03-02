#!/bin/bash
# Wrapper script to run Helius monitor from the correct directory
cd /Users/kevinkeaveney/Dev/claude/flex
python helius_cli_monitor.py >> /tmp/helius_cron.log 2>&1
