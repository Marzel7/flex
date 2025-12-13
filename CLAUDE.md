# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the Application

```bash
python main.py
```

This starts:
- A background thread monitoring Raydium DEX pools every 60 seconds
- A Flask web server on port 5001

Web UI available at: http://localhost:5001

## Dependencies

- requests
- flask
- sqlite3 (stdlib)

## Architecture

Single-file application (`main.py`) with three main components:

1. **RaydiumDatabase** - SQLite persistence layer for pool data
   - Tables: `pools` (current state), `pool_history` (snapshots over time)
   - Thread-safe via `check_same_thread=False` connections

2. **RaydiumMonitor** - Fetches and processes pool data from Raydium API (`https://api.raydium.io/v2`)
   - Runs in background daemon thread
   - Filters pools by minimum liquidity threshold (default $5000)

3. **Flask Web App** - Serves HTML template with embedded CSS/JS
   - Single route `/` serves the full SPA
   - API endpoint `/api/pools/<tab_type>` returns JSON (recent, volume, liquidity)
   - Auto-refreshes every 30 seconds client-side

## Data Storage

SQLite database `raydium_pools.db` stores pool data locally.
