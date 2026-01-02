# Environment Variable Setup Guide

Quick reference for using environment variables with the trading system.

## TL;DR - Quick Setup

```bash
# 1. Run the setup helper (one-time)
python3 setup_trading_env.py

# 2. Follow the prompts (saves to ~/.zshrc or ~/.bash_profile)

# 3. In new terminal, just run:
python3 test_buy_only.py
```

That's it! The script will automatically load your keypair from `TRADING_KEYPAIR` env var.

---

## Manual Setup (If You Prefer)

### Step 1: Get Your Keypair as JSON

```bash
# If you have a keypair.json file:
python3 << 'EOF'
import json
with open("test_keypair.json") as f:
    data = json.load(f)
print(json.dumps(data))
EOF
```

This will output something like: `[1, 2, 3, ..., 64 numbers]`

### Step 2: Export to Environment (Temporary)

```bash
# Just for this terminal session:
export TRADING_KEYPAIR='[1, 2, 3, ..., 64 numbers]'
export HELIUS_API_KEY="your_key_here"

# Then run:
python3 test_buy_only.py
```

**⚠️ Important**: This only works in the current terminal. Close the terminal and it's gone.

### Step 3: Make Persistent (Optional)

To use in all future terminals, add to your shell profile:

```bash
# Open your profile
nano ~/.zshrc  # or ~/.bash_profile for bash

# Add these lines at the end:
export TRADING_KEYPAIR='[1, 2, 3, ..., 64 numbers]'
export HELIUS_API_KEY="your_key_here"

# Save (Ctrl+X, Y, Enter in nano)

# Reload in current terminal:
source ~/.zshrc
```

---

## How It Works

The `test_buy_only.py` script checks for `TRADING_KEYPAIR` in this order:

1. **Look for env var**: `TRADING_KEYPAIR` environment variable
   - If found and valid → Use it automatically ✅
   - If found but invalid → Print error and fall back

2. **Fall back to file input**: Ask for keypair file path
   - If you provide a path → Load from file ✅
   - If you skip (press Enter) → Exit

This means:
- ✅ You can use EITHER env var OR file
- ✅ Env var takes priority if both are available
- ✅ Always falls back gracefully
- ✅ No errors if env var not set

---

## Supported Formats

The script accepts `TRADING_KEYPAIR` in these formats:

### Format 1: JSON Array (Recommended)
```bash
export TRADING_KEYPAIR='[1, 2, 3, ..., 64]'
```

### Format 2: Hex String (Optional)
```bash
export TRADING_KEYPAIR='0a1b2c3d...'
```

---

## Security Considerations

### ✅ Safe To Do:
- Store in `~/.zshrc` on your personal computer
- Use for testing with small amounts (0.01 SOL)
- Share the setup script with others (not the key!)
- Rotate keys periodically

### ❌ Never Do:
- Commit to Git (add to `.gitignore`)
- Share the `TRADING_KEYPAIR` value
- Use your main wallet keypair
- Leave sensitive systems running unattended
- Log or print the keypair value
- Store in shared directories

### 🔒 Better Security Options:

For production, consider:
1. **Hardware wallets** - Keys never on computer
2. **Encrypted keyfiles** - Encrypted at rest
3. **Vault systems** - Secrets management
4. **Key rotation** - Periodic key changes
5. **Access auditing** - Log all key usage

---

## Troubleshooting

### Problem: "TRADING_KEYPAIR not found"
```bash
# Solution: Export in current terminal
export TRADING_KEYPAIR='[your_keypair]'
python3 test_buy_only.py

# Or add to ~/.zshrc and reload
source ~/.zshrc
```

### Problem: "Invalid keypair format"
```bash
# Solution: Verify JSON is valid
python3 << 'EOF'
import json
keypair = '[1, 2, 3]'  # Your value
data = json.loads(keypair)
print(f"Valid! Length: {len(data)}")
EOF
```

### Problem: "Could not parse keypair"
```bash
# Solution: Make sure it's exactly 64 numbers
python3 << 'EOF'
import json
keypair = '[...]'  # Your value
data = json.loads(keypair)
if len(data) == 64:
    print("✓ Correct format (64 numbers)")
else:
    print(f"✗ Wrong format ({len(data)} numbers, need 64)")
EOF
```

### Problem: Environment variable not persisting
```bash
# Solution: Check which shell you use
echo $SHELL
# Returns: /bin/zsh or /bin/bash

# Edit the correct profile:
nano ~/.zshrc     # If zsh
# or
nano ~/.bash_profile  # If bash

# Then reload:
source ~/.zshrc
# or
source ~/.bash_profile
```

---

## Verification

Check that everything is set up correctly:

```bash
# 1. Verify env vars are set
echo "TRADING_KEYPAIR: ${#TRADING_KEYPAIR} chars"
echo "HELIUS_API_KEY: ${#HELIUS_API_KEY} chars"

# 2. Check keypair format
python3 << 'EOF'
import os, json
kp = os.environ.get('TRADING_KEYPAIR')
if kp:
    data = json.loads(kp)
    print(f"✓ Valid keypair ({len(data)} numbers)")
else:
    print("✗ TRADING_KEYPAIR not set")
EOF

# 3. Run a quick connection test
python3 test_buy_only.py
# Should load keypair automatically!
```

---

## Cleanup & Rotation

### Rotate Keys (Security Best Practice)

```bash
# 1. Generate new keypair
python3 << 'EOF'
from solders.keypair import Keypair
import json
keypair = Keypair()
print(json.dumps(list(keypair.secret_key)))
EOF

# 2. Fund new wallet (if needed)
# Transfer SOL to new wallet address

# 3. Update env var
export TRADING_KEYPAIR='[new_keypair_array]'

# 4. Update shell profile if using persistent storage
nano ~/.zshrc
# Edit the TRADING_KEYPAIR line

# 5. Verify it works
python3 test_buy_only.py
```

### Remove Keys (When Done Testing)

```bash
# Unset env var in current terminal
unset TRADING_KEYPAIR
unset HELIUS_API_KEY

# Or remove from profile
nano ~/.zshrc
# Delete the export lines
# Save and reload: source ~/.zshrc
```

---

## Examples

### Example 1: Using setup_trading_env.py (Easiest)
```bash
$ python3 setup_trading_env.py

🔑 Trading Environment Setup Helper
======================================================================

Enter path to your keypair JSON file: test_keypair.json
✅ Keypair loaded successfully

Enter your Helius API key: 0ae07551-32df-4d9d-af2a-1925fb7f561f
✅ API key accepted

export TRADING_KEYPAIR='[1, 2, 3, ..., 64]'
export HELIUS_API_KEY="0ae07551-32df-4d9d-af2a-1925fb7f561f"

Save to ~/.zshrc or ~/.bash_profile? (y/n): y
✅ Saved to ~/.zshrc

# Output shows exactly what to do next
```

### Example 2: Manual One-Time Setup
```bash
# Export temporarily in this terminal only
$ export TRADING_KEYPAIR='[1, 2, 3, ..., 64]'
$ export HELIUS_API_KEY="your_key"

# Run trades in this terminal
$ python3 test_buy_only.py
📌 Found TRADING_KEYPAIR env var, attempting to load...
✅ Keypair loaded from TRADING_KEYPAIR env var
   Wallet: 1234567890abcdef...

# Close terminal, env vars are gone (temporary)
```

### Example 3: Persistent Setup (Recommended)
```bash
# Add to ~/.zshrc
$ nano ~/.zshrc

# At the end of file, add:
export TRADING_KEYPAIR='[1, 2, 3, ..., 64]'
export HELIUS_API_KEY="your_key"

# Save and reload
$ source ~/.zshrc

# Now in any new terminal:
$ python3 test_buy_only.py
📌 Found TRADING_KEYPAIR env var, attempting to load...
✅ Keypair loaded from TRADING_KEYPAIR env var
   Wallet: 1234567890abcdef...

# Env vars persist across terminal sessions ✓
```

---

## Quick Reference Commands

```bash
# Check if env var is set
echo $TRADING_KEYPAIR

# See length (should be ~400+ chars for keypair JSON)
echo ${#TRADING_KEYPAIR}

# View shell profile
cat ~/.zshrc

# Edit shell profile
nano ~/.zshrc

# Reload shell profile
source ~/.zshrc

# Generate new keypair
python3 << 'EOF'
from solders.keypair import Keypair
import json
print(json.dumps(list(Keypair().secret_key)))
EOF

# Test it works
python3 test_buy_only.py
```

---

## Summary

| Task | Command |
|------|---------|
| One-time setup | `python3 setup_trading_env.py` |
| Temporary (this session) | `export TRADING_KEYPAIR='[...]'` |
| Persistent (all future) | Edit `~/.zshrc` and reload |
| Check if set | `echo $TRADING_KEYPAIR` |
| Unset | `unset TRADING_KEYPAIR` |
| Rotate keys | Generate new, update env var |
| View location | `echo ~/.zshrc` |

**Recommended**: Use `setup_trading_env.py` for guided, safe setup.

