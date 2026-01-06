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

1. **PumpSwapDatabase** - SQLite persistence layer for pool data
   - Tables: `pools` (current state), `pool_history` (snapshots over time)
   - Thread-safe via `check_same_thread=False` connections

2. **TokenMonitor** - WebSocket-based PumpSwap pool detection
   - Subscribes to Solana PumpSwap program (pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA) via Helius WebSocket RPC
   - **ONLY monitors PumpSwap program** (not Raydium V4, CPMM, or other DEX types)
   - Detects Pump.fun → PumpSwap token migrations in real-time
   - Extracts prices from transaction post-balance metadata (Token Balance / SOL Balance)
   - Parses transaction logs to extract pool details (mint, liquidity, symbol, creator)
   - Detects pool creation events in real-time (~3-8 seconds after on-chain)
   - Continuously updates prices for existing tokens on sliding scale (30s-5min intervals)
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

SQLite database `pumpswap_tokens.db` stores pool data locally.

## Price Freshness & Updates

**Automatic Price Updates:**
- Background thread updates prices continuously on a sliding scale
- Update frequency based on pool age:
  - 0-5 minutes old: Every 30 seconds ⚡
  - 5-30 minutes old: Every 2 minutes
  - 30+ minutes old: Every 5 minutes
- Implementation: `main.py` lines 2357-2452
  - `update_pool_prices()` - Background daemon thread
  - `get_pools_needing_update()` - Determines which pools to refresh
  - Updates: `dexscreener_price_usd`, `dexscreener_price_native`, `market_cap`
  - Records timestamp in `last_price_update` for every refresh

**Checking Price Freshness:**
```bash
python get_price_from_pools.py <TOKEN_MINT>
```
Output shows: `Price Status: Updated 30s ago ✓ (fresh)`

Status indicators:
- `✓ (fresh)` = Less than 5 minutes old
- `~ (ok)` = 5-30 minutes old
- `⚠ (stale)` = More than 1 hour old

**Database Timestamps:**
- `first_seen` - Pool detection time (never changes)
- `last_price_update` - Last time price was refreshed (updates constantly)

**Scripts:**
- `get_price_from_pools.py` - Query prices with freshness status
- `get_price_live_with_balances.py` - Advanced lookup with vault details

## Development Guidelines

**Before making UI changes:**
1. Run all tests to ensure core functionality works:
   ```bash
   python test_pumpswap_detection.py      # Phase 1 (21 tests)
   python test_pumpswap_phase2.py         # Phase 2 (14 tests)
   ```
   All tests should pass (35/35) before UI modifications.

2. Verify the listener works:
   ```bash
   python test_pumpswap_listener.py       # Real-time detection and price updates
   ```

3. Test the main application:
   ```bash
   python main.py                         # Flask server + WebSocket + price updater
   ```

**Key focus areas when coding:**
- PumpSwap price extraction from transaction post-balances
- Real-time token detection via WebSocket (PumpSwap program only)
- Price updates on sliding scale for existing tokens
- Pump.fun → PumpSwap migration tracking
- Database persistence and querying

**Do NOT:**
- Add Raydium V4, CPMM, Meteora, or other DEX monitoring
- Modify price extraction logic without tests
- Change WebSocket subscription (should ONLY be PumpSwap program)
- Update UI without verifying backend tests pass first

## Security Guidelines

**CRITICAL: NEVER commit private keys, API keys, or sensitive credentials to this repository.**

### What MUST NEVER Be Committed

- ✗ Private keys (TRADING_KEYPAIR)
- ✗ API keys (HELIUS_API_KEY, JUPITER_API_KEY)
- ✗ Wallet credentials
- ✗ Sensitive credentials of any kind
- ✗ Real example values in documentation

### Credential Management

**Use `.env` file for all credentials:**
- ✓ Create a `.env` file in project root (never commit)
- ✓ `.env` is in `.gitignore` - ALWAYS VERIFY THIS
- ✓ Add credentials only to `.env`, never to code or docs
- ✓ Load credentials with `from utils.load_env import load_env`

**Example `.env` structure:**
```
HELIUS_API_KEY=your_actual_api_key_here
TRADING_KEYPAIR=[your, actual, keypair, array]
JUPITER_API_KEY=your_actual_api_key_here
```

**Documentation uses ONLY placeholders:**
```
# In docs (like ENV_SETUP.md):
HELIUS_API_KEY=your_helius_api_key_here
TRADING_KEYPAIR=[188, 77, 162, ...]  # Use ellipsis, never full key
JUPITER_API_KEY=your_jupiter_api_key_here
```

### Before Every Commit

1. **Check for exposed credentials:**
   ```bash
   git diff --staged | grep -i "api_key\|trading_keypair\|secret"
   ```

2. **Verify .env is ignored:**
   ```bash
   git check-ignore .env
   # Should output: .env
   ```

3. **Scan for hardcoded keys:**
   ```bash
   git diff --staged | grep -E "\[.*[0-9]{2,}.*[0-9]{2,}.*\]"
   ```

### If Credentials Are Ever Exposed

1. **Immediately rotate all exposed credentials:**
   - Generate new API keys
   - Create new trading keypair
   - Update `.env` file

2. **Never reuse exposed credentials:**
   - Even if the exposure is "cleaned up"
   - Git history is permanent
   - Always create new credentials

3. **Follow SECURITY_FIX_GUIDE.md** if exposure occurs

### Remember

- **Public repositories = Assume all committed content is visible to everyone**
- **Credentials in git = Consider them permanently compromised**
- **Use `.env` = Credentials stay safe and local**

---

## Working with Claude Code

### Communication Preference

- **Don't create final summary documents after every change**
- Instead: Provide concise status updates only when requesting confirmation or next steps
- Summary documents are helpful for **major milestones** (feature complete, cleanup done) but not for routine changes
- Keep responses focused on the work done, not elaborate wrap-ups

### How to Request Work

- Be clear about what you want done
- Don't expect comprehensive summaries after every action
- Ask specifically if you want a summary or status report
- Otherwise, just move to the next task
