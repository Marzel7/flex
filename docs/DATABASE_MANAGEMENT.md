# Database Management Guide

Tools and utilities for managing the PumpSwap token database.

## Overview

The PumpSwap monitoring system uses SQLite (`pumpswap_tokens.db`) to store token data, prices, and trading history. This guide covers how to manage and clean up that data.

## Database Location

```
/Users/kevinkeaveney/Dev/claude/flex/pumpswap_tokens.db
```

## Utilities

### Remove Token

Remove specific tokens or all tokens from the database.

#### Remove Single Token

```bash
python3 utils/remove_token.py <TOKEN_MINT>
```

**Example:**
```bash
python3 utils/remove_token.py DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263
```

**Output:**
```
✓ Removed token: BONK
  Mint: DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263
```

#### Remove All Tokens

```bash
python3 utils/remove_token.py --all
```

**Output:**
```
⚠️  This will delete 42 tokens from the database. Continue? (y/N): y
✓ Removed all 42 tokens from database
```

### Help

```bash
python3 utils/remove_token.py --help
```

## Database Schema

### Pools Table

Contains all detected PumpSwap tokens and their data.

**Core Columns:**
- `base_mint` - Token mint address (unique)
- `symbol` - Token symbol
- `name` - Token name
- `signature` - Pool creation transaction signature
- `first_seen` - Timestamp when detected
- `last_updated` - Last update timestamp

**Price Columns:**
- `dexscreener_price_usd` - Last cached price from DexScreener
- `dexscreener_price_native` - Price in native token
- `initial_price_usd` - Initial migration price
- `last_price_update` - When price was last updated

**Trading Columns (for automated bot):**
- `trade_status` - `waiting` | `bought` | `sold`
- `buy_price_usd` - Price paid per token
- `buy_time` - Buy timestamp
- `buy_signature` - Buy transaction signature
- `sell_price_usd` - Price received per token
- `sell_time` - Sell timestamp
- `sell_signature` - Sell transaction signature
- `quantity_bought` - Number of tokens purchased
- `profit_loss_usd` - Realized profit/loss in USD
- `profit_loss_percent` - Realized profit/loss percentage

## Common Tasks

### View All Tokens

```bash
sqlite3 pumpswap_tokens.db "SELECT symbol, name, base_mint FROM pools LIMIT 20;"
```

### View Tokens by Status

```bash
# All bought tokens
sqlite3 pumpswap_tokens.db "SELECT symbol, trade_status, buy_price_usd FROM pools WHERE trade_status = 'bought';"

# All sold tokens with P&L
sqlite3 pumpswap_tokens.db "SELECT symbol, profit_loss_percent, profit_loss_usd FROM pools WHERE trade_status = 'sold';"
```

### View Trading Activity

```bash
# Recent trades
sqlite3 pumpswap_tokens.db "SELECT symbol, trade_status, buy_time, sell_time FROM pools WHERE trade_status IN ('bought', 'sold') ORDER BY buy_time DESC LIMIT 10;"
```

### Check Database Size

```bash
ls -lh pumpswap_tokens.db
```

### Clear All Data (Full Reset)

```bash
rm pumpswap_tokens.db
```

This will delete the entire database. It will be recreated automatically when the listener detects new tokens.

## Backup and Restore

### Create Backup

```bash
cp pumpswap_tokens.db pumpswap_tokens.db.backup
```

### Restore from Backup

```bash
cp pumpswap_tokens.db.backup pumpswap_tokens.db
```

## Troubleshooting

### Database is Locked

If you see "database is locked" errors:
- Make sure the listener is not running
- Stop any other Python processes using the database
- Try again

### Corrupted Database

If the database becomes corrupted:
1. Stop the listener
2. Delete the database: `rm pumpswap_tokens.db`
3. Restart the listener - it will recreate the database

## Related Files

- [TRADING_BOT_GUIDE.md](TRADING_BOT_GUIDE.md) - Trading bot and database schema
- [COMPREHENSIVE_TRADING_GUIDE.md](COMPREHENSIVE_TRADING_GUIDE.md) - Trading commands

## Tips

✅ **Backup before major changes** - Always backup before removing large amounts of data
✅ **Use --all carefully** - Removing all tokens is permanent (unless backed up)
✅ **Check status first** - Use SQL queries to verify what you're removing
✅ **Schedule cleanups** - Remove old tokens periodically to keep database lean
