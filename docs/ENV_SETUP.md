# Environment Setup - .env Configuration

## Overview

The application now uses a `.env` file for secure environment variable management across all modules. This eliminates the need for the `bash test` wrapper and allows credentials to be used consistently throughout the entire application.

## Setup

### 1. Create `.env` File

Copy `.env.example` to `.env` and fill in your credentials:

```bash
cp .env.example .env
```

### 2. Configure API Keys

Edit `.env` and add your credentials:

```bash
# Solana RPC Configuration
HELIUS_API_KEY=your_helius_api_key_here

# Trading Keypair (wallet private key as JSON array)
TRADING_KEYPAIR=[188, 77, 162, 197, 252, ...]

# Jupiter API Configuration
JUPITER_API_KEY=your_jupiter_api_key_here
```

### 3. Verify Security

The `.env` file is added to `.gitignore` and will NOT be committed to version control:

```bash
git check-ignore .env
# Output: .env
```

## Usage

### Direct Python Execution (No Wrapper Needed)

Now you can run scripts directly without the `bash test` wrapper:

**Buy BONK:**
```bash
python3 buy_token.py DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263
```

**Sell BONK:**
```bash
python3 sell_token.py DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263 500000000
```

**Buy Any Token:**
```bash
python3 buy_token.py 8y45AJzCUBSZL1UDFQRzCKovQBLQFudBrpPeg5yNpump
```

### How It Works

1. **Automatic Loading:** When any script imports the `load_env` module, it automatically loads variables from `.env`
2. **Environment Priority:** Existing environment variables are NOT overridden (allowing for local overrides)
3. **JSON Support:** Special handling for JSON arrays (like `TRADING_KEYPAIR`)

### Using in Other Python Modules

To use `.env` in any Python script, add this at the top:

```python
from load_env import load_env
load_env()

import os
api_key = os.environ.get("HELIUS_API_KEY")
```

## Configuration Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `HELIUS_API_KEY` | Solana RPC endpoint authentication | `0ae07551-32df-...` |
| `TRADING_KEYPAIR` | Wallet private key (JSON array format) | `[188, 77, 162, ...]` |
| `JUPITER_API_KEY` | Jupiter swap API authentication | `27c95184-289e-...` |

## Security Best Practices

1. **Never commit `.env`** - It's in `.gitignore`
2. **Never share your keypair** - It controls your funds
3. **Use strong API keys** - Rotate them regularly
4. **Keep backups** - Store `.env` securely outside version control
5. **Environment variable precedence** - Override locally with shell variables if needed:
   ```bash
   export HELIUS_API_KEY="override_key" && python3 buy_token.py ...
   ```

## File Structure

```
flex/
├── .env                    # ← Your credentials (NOT in git)
├── .env.example           # ← Template for setup
├── .gitignore             # ← Includes .env
├── load_env.py            # ← Loader utility
├── buy_token.py           # ← Uses .env automatically
├── sell_token.py          # ← Uses .env automatically
└── trading_executor.py    # ← Can access loaded variables
```

## Troubleshooting

### "HELIUS_API_KEY not set" Error

1. Verify `.env` file exists in project root:
   ```bash
   ls -la .env
   ```

2. Check file has correct content:
   ```bash
   cat .env
   ```

3. Ensure you're running from the project root:
   ```bash
   pwd
   # Should output: .../flex
   ```

### Environment Variables Not Loading

Make sure the script imports `load_env`:

```python
from load_env import load_env
load_env()
```

This MUST come before any imports that use environment variables.

## Migration from bash test Wrapper

### Old Way:
```bash
bash test buy_token.py DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263
```

### New Way:
```bash
python3 buy_token.py DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263
```

The `bash test` wrapper still works but is no longer necessary.

## Advanced Usage

### Using in main.py

To use `.env` in your main application:

```python
from load_env import load_env
load_env()

import os
helius_key = os.environ.get("HELIUS_API_KEY")
```

### Creating Custom Scripts

When creating new scripts that need API credentials:

```python
#!/usr/bin/env python3

from load_env import load_env
load_env()

import os
from trading_executor import TokenTrader

# Now you can use environment variables
rpc_endpoint = f"https://mainnet.helius-rpc.com/?api-key={os.environ.get('HELIUS_API_KEY')}"
trader = TokenTrader(rpc_endpoint=rpc_endpoint, ...)
```

## Summary

✅ **Benefits:**
- No need for bash wrapper scripts
- Credentials are app-wide (not token-specific)
- Secure - `.env` is not committed to git
- Standard practice (`.env` files are industry standard)
- Easy to extend to all modules
- Clear separation of secrets from code

✅ **All Scripts Working:**
- buy_token.py ✓
- sell_token.py ✓
- Can be extended to main.py and all other modules ✓
