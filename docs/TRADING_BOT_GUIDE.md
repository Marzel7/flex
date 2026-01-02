# Trading Bot Guide - Auto Buy & Sell with P&L Tracking

Automated trading bot that detects new PumpSwap token launches and executes trades with profit tracking.

## Overview

The trading bot integrates with the WebSocket listener to:
- **Detect** new token launches in real-time
- **Buy** tokens automatically when launched
- **Monitor** price movements and P&L
- **Sell** when 20% profit target is reached
- **Track** P&L on real-time display

## Features

### 1. Database-Tracked Trades
Database schema extended with trading columns:
- `trade_status` - Token state: `waiting` | `bought` | `sold`
- `buy_price_usd` - Price paid per token
- `buy_time` - When token was bought
- `buy_signature` - Transaction signature for buy
- `sell_price_usd` - Price received per token
- `sell_time` - When token was sold
- `sell_signature` - Transaction signature for sell
- `quantity_bought` - Number of tokens purchased
- `profit_loss_usd` - $ profit/loss from trade
- `profit_loss_percent` - % return from trade

### 2. Trading Bot Class
New `TradingBot` class in `test_pumpswap_listener.py`:

```python
bot = TradingBot(use_trading=False)  # Safety: trading disabled by default
bot = TradingBot(use_trading=True)   # Enable actual trading
```

#### Methods

**execute_buy(token_mint, symbol, sol_amount)**
- Executes buy transaction on new token detection
- Default: 0.001 SOL per token
- Returns: `{'status', 'signature', 'output_amount', 'error'}`

**execute_sell(token_mint, symbol, quantity)**
- Executes sell when 20% profit reached
- Quantity: number of tokens to sell
- Returns: `{'status', 'signature', 'output_sol', 'error'}`

**update_trade_in_db(token_mint, buy_price_usd, quantity, buy_signature)**
- Records buy transaction details
- Updates `trade_status` to `bought`

**update_sell_in_db(token_mint, sell_price_usd, sell_signature)**
- Records sell transaction details
- Calculates and updates P&L
- Updates `trade_status` to `sold`

### 3. Real-Time P&L Display

The live table now includes a **P&L** column showing trade status:

```
Name             Current Price     SOL Balance  % Change   P&L
------------------------------------------------------------------
TokenA           $0.00000123       50.00 SOL    +5.0%      —
TokenB           $0.00000456       20.00 SOL    -10.0%     💰 Holding
TokenC           $0.00000789       30.00 SOL    +2.0%      ✓ +25.5% (+$12.50)
TokenD           $0.00000234       15.00 SOL    -20.0%     ✗ -5.2% (-$2.15)
```

**Status Indicators:**
- `—` = Not traded yet
- `💰 Holding` = Bought, waiting for 20% profit
- `✓ +X.X% (+$Y)` = Sold with profit
- `✗ -X.X% ($Y)` = Sold with loss

## Setup

### 1. Environment Variable

To enable actual trading, set the environment variable before running:

```bash
# Enable auto-trading
export ENABLE_TRADING=true

# Run listener with trading enabled
python tests/test_pumpswap_listener.py

# Run with trading disabled (default, safe mode)
# unset ENABLE_TRADING
# python tests/test_pumpswap_listener.py
```

### 2. Configuration

The trading bot automatically uses your `.env` credentials:
- `HELIUS_API_KEY` - RPC endpoint (required)
- `JUPITER_API_KEY` - DEX aggregation (optional, falls back to Raydium)
- `TRADING_KEYPAIR` - Trading wallet (JSON array format, required for trading)

### 3. Safety Features

**Trading is disabled by default** - Multiple layers of safety:

1. `ENABLE_TRADING=false` by default
2. If not set, listener runs in monitoring-only mode
3. Bot checks `use_trading` flag before executing trades
4. Returns `{'status': 'skipped'}` if disabled
5. All trades logged with signatures on Solscan

## Usage Examples

### Example 1: Run Listener with Trading Disabled (Monitoring Only)

```python
listener = StandalonePumpSwapListener(use_trading=False)
# Only monitors prices and displays P&L tracking
# Does NOT execute actual trades
```

### Example 2: Run with Auto-Trading Enabled

```python
listener = StandalonePumpSwapListener(use_trading=True)
# Auto-buys new tokens
# Auto-sells when 20% profit reached
# Displays P&L in real-time table
```

### Example 3: Manual Trade Integration

```python
# Create bot instance
bot = TradingBot(use_trading=True)

# Execute buy
buy_result = await bot.execute_buy(
    token_mint="DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
    symbol="BONK",
    sol_amount=0.001
)

if buy_result['status'] == 'confirmed':
    # Record buy in database
    bot.update_trade_in_db(
        token_mint="DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
        buy_price_usd=buy_result['price'],
        quantity_bought=buy_result['output_amount'],
        buy_signature=buy_result['signature']
    )
```

## P&L Calculation

P&L is calculated when token is sold:

```python
buy_price = 0.00001234 USD per token
sell_price = 0.00001481 USD per token
quantity = 1,000,000 tokens

total_cost = 0.00001234 * 1,000,000 = $12.34
total_revenue = 0.00001481 * 1,000,000 = $14.81

profit_usd = $14.81 - $12.34 = $2.47
profit_percent = ($2.47 / $12.34) * 100 = 20.0%
```

## Profit Target Logic

### 20% Profit Threshold

The bot monitors each bought token and executes sell when:

```python
# Current price vs buy price
profit_percent = ((current_price - buy_price) / buy_price) * 100

if profit_percent >= 20.0:
    # Execute sell
    await bot.execute_sell(token_mint, symbol, quantity)
```

To implement this, add a background monitor task:

```python
async def monitor_profit_targets(self):
    """Monitor bought tokens and sell at 20% profit"""
    while self.is_running:
        db_path = Path(__file__).parent.parent / 'pumpswap_tokens.db'
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        cursor = conn.cursor()

        # Get all bought tokens
        cursor.execute('''
            SELECT base_mint, symbol, buy_price_usd, quantity_bought
            FROM pools WHERE trade_status = 'bought'
        ''')

        for token_mint, symbol, buy_price, quantity in cursor.fetchall():
            # Fetch current price
            price_result = self.price_fetcher.fetch_live_price_for_token(token_mint)
            current_price = price_result['price_usd']

            # Check profit
            profit_pct = ((current_price - buy_price) / buy_price) * 100

            if profit_pct >= 20.0:
                # Execute sell
                sell_result = await self.trading_bot.execute_sell(
                    token_mint, symbol, quantity
                )

                if sell_result['status'] == 'confirmed':
                    # Update DB with sell
                    self.trading_bot.update_sell_in_db(
                        token_mint,
                        current_price,
                        sell_result['signature']
                    )

        conn.close()
        await asyncio.sleep(10)  # Check every 10 seconds
```

## Database Schema

New columns added to `pools` table:

```sql
CREATE TABLE pools (
    -- ... existing columns ...

    -- Trading columns (new)
    trade_status TEXT DEFAULT 'waiting',           -- waiting | bought | sold
    buy_price_usd REAL,                           -- Price paid per token
    buy_time TIMESTAMP,                           -- When bought
    buy_signature TEXT,                           -- Buy tx signature
    sell_price_usd REAL,                          -- Price received per token
    sell_time TIMESTAMP,                          -- When sold
    sell_signature TEXT,                          -- Sell tx signature
    quantity_bought REAL,                         -- Tokens purchased
    profit_loss_usd REAL,                         -- $ profit/loss
    profit_loss_percent REAL                      -- % return
)
```

## Troubleshooting

### Trading Not Executing
1. Check `use_trading` flag is `True`
2. Verify `.env` credentials are valid
3. Ensure `TRADING_KEYPAIR` is valid JSON array
4. Check logs for specific error messages

### P&L Not Showing
1. Confirm token was bought (check `trade_status` in DB)
2. Verify sell was executed and confirmed
3. Check `profit_loss_usd` and `profit_loss_percent` columns populated

### Incorrect P&L
1. Verify `buy_price_usd` was recorded correctly
2. Verify `sell_price_usd` was recorded correctly
3. Check `quantity_bought` is accurate

## Safety Considerations

1. **Test First** - Run with `use_trading=False` to monitor only
2. **Small Amounts** - Start with 0.001 SOL trades
3. **Monitor Closely** - Watch for execution failures
4. **Verify Signatures** - Check Solscan links for all trades
5. **Rate Limits** - Jupiter API has rate limits; monitor for 429 errors

## Integration with Main App

To integrate trading bot with main monitoring application:

```python
# In main.py
from tests.test_pumpswap_listener import TradingBot, StandalonePumpSwapListener

listener = StandalonePumpSwapListener(use_trading=True)
listener.trading_bot = TradingBot(use_trading=True)

# Start listener
asyncio.run(listener.listen_websocket())
```

## Implementation Status

1. ✅ Database schema updated for trade tracking
2. ✅ TradingBot class created with buy/sell methods
3. ✅ P&L column added to live table display
4. ✅ Auto-buy integrated in WebSocket listener event handler
5. ✅ 20% profit monitoring loop implemented (background thread)
6. ✅ Transaction logging with Solscan verification
7. ✅ Enable/disable via environment variable
8. ✅ Comprehensive error handling and logging

See [COMPREHENSIVE_TRADING_GUIDE.md](COMPREHENSIVE_TRADING_GUIDE.md) for basic trading commands without automation.
