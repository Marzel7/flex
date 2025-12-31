# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the Application

```bash
python main.py
```

This starts:
- A WebSocket listener for real-time pool creation events
- A Flask web server on port 5002
- Client-side polling every 1 second for near real-time updates

Web UI available at: http://localhost:5002

## Dependencies

**Required:**
- requests
- flask
- sqlite3 (stdlib)
- solders (for on-chain Metaplex metadata fetching)

**Optional (for production):**
- gunicorn (recommended for proper SSE streaming)

## Architecture

Single-file application (`main.py`) with four main components:

1. **RaydiumDatabase** - SQLite persistence layer for pool data
   - Tables: `pools` (current state), `pool_history` (snapshots over time)
   - Thread-safe via `check_same_thread=False` connections

2. **TokenMonitor** - WebSocket-based pool detection
   - Subscribes to Solana program logs via Helius WebSocket RPC
   - Detects pool creation events from Raydium V4 and CPMM programs
   - Detects PumpSwap migrations (PumpFun → Raydium V4) in real-time
   - Parses transaction logs to extract pool details (mint, liquidity, symbol)
   - Detects pool creation events in real-time (~3-8 seconds after on-chain)
   - Broadcasts new pools to broadcast queue for client polling

3. **Metadata Fetcher** - On-chain metadata lookups
   - `fetch_metaplex_metadata()` - Direct PDA lookup of Metaplex Token Metadata Program
   - Derives metadata account PDAs using Solders library
   - Falls back to external APIs (Jupiter API) if on-chain lookup fails
   - Parses binary Metaplex v1 structure to extract name, symbol, URI

4. **Flask Web App** - Serves HTML template with embedded CSS/JS
   - Single route `/` serves the full SPA
   - API endpoint `/api/pools` returns recent pools from database
   - API endpoint `/api/pools/new` returns new pools from broadcast queue (polled every 1 second)
   - Client-side polling every 1 second for near real-time updates (~1-2 second latency)
   - 30-second backup refresh ensures no pools are missed

## Data Flow

1. WebSocket listener detects pool creation (~3-8 seconds after on-chain)
2. Metadata fetcher retrieves name, symbol, image (Metaplex first, external APIs fallback)
3. New pool added to broadcast queue
4. Client polls `/api/pools/new` every 1 second
5. Client receives new pools → adds to UI with image, name, symbol, address
6. Total latency: ~8-12 seconds (pool creation + confirmation + metadata + 1s poll interval)
7. 30-second full refresh ensures no pools are missed

## Data Storage

SQLite database `raydium_pools.db` stores pool data locally.
