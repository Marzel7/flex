# Security Fix Guide - Step by Step

## Your Credentials Were Exposed - Here's How to Fix It

### Timeline
- **Problem Found:** Your public GitHub repo contains exposed credentials in git history
- **Exposed in:** Commit `4f544b7` (deleted `test` file)
- **Affected Account:** `HhijUXN96yy1Pdrk3tmTDiV1HWw1LrQjhdX3DDusPnn9`
- **Status:** COMPROMISED - DO NOT USE THIS ACCOUNT

---

## Step 1: Generate New Trading Keypair (5 minutes)

```bash
python3 utils/generate_new_keypair.py
```

Output will look like:
```
PUBLIC KEY (Your Wallet Address):
  HhijUXN96yy1Pdrk3tmTDiV1HWw1LrQjhdX3DDusPnn9

Add this to your .env file:
TRADING_KEYPAIR=[123, 45, 67, 89, 101, 112, ...]
```

---

## Step 2: Update Your .env File

Copy the new TRADING_KEYPAIR from Step 1 into `.env`:

```bash
# Open .env
nano .env
```

Update these lines:
```
HELIUS_API_KEY=your_new_helius_key_here
JUPITER_API_KEY=your_new_jupiter_key_here
TRADING_KEYPAIR=[your, new, keypair, array]
```

**IMPORTANT:** Do NOT commit `.env` to git!
```bash
# Verify .env is ignored
git check-ignore .env
# Should output: .env
```

---

## Step 3: Rotate Your API Keys

### Helius API Key
1. Go to https://dashboard.helius.dev
2. Navigate to API Keys
3. Generate a new key
4. Copy the new key into `.env` as `HELIUS_API_KEY`

### Jupiter API Key
1. Go to https://api.jup.ag
2. Look for your API keys
3. Revoke the old key
4. Generate a new one
5. Copy into `.env` as `JUPITER_API_KEY`

---

## Step 4: Move SOL to New Wallet

### Old Compromised Wallet:
```
HhijUXN96yy1Pdrk3tmTDiV1HWw1LrQjhdX3DDusPnn9
```

### New Safe Wallet:
Get from `python3 utils/generate_new_keypair.py` output

**Action:**
1. Open Phantom Wallet
2. Send all SOL from old wallet → new wallet
3. Keep the old wallet empty

---

## Step 5: Test Everything Works

```bash
# Test that new credentials work
python3 << 'EOF'
import sys
sys.path.insert(0, '/Users/kevinkeaveney/Dev/claude/flex')

from utils.load_env import load_env
load_env()

import os
from tests.test_pumpswap_listener import StandalonePumpSwapListener

# Test with new credentials
listener = StandalonePumpSwapListener(use_trading=False)
print("✓ Listener initialized successfully with new credentials!")
print(f"✓ Wallet: {listener.trading_bot.keypair.pubkey() if listener.trading_bot.keypair else 'N/A'}")
EOF
```

---

## Step 6: Clean Git History (Optional but Recommended)

### Option A: Using GitHub Web UI
1. Go to your GitHub repo
2. Settings → Secret Scanning
3. GitHub will detect and inform you about exposed secrets
4. GitHub may have already invalidated the old keys

### Option B: Using BFG Repo-Cleaner (Advanced)
```bash
# Download BFG
brew install bfg  # macOS
# or
apt-get install bfg  # Linux

# Create secrets file
cat > /tmp/secrets.txt << 'EOF'
0ae07551-32df-4d9d-af2a-1925fb7f561f
5bf35d71-d363-401e-a357-4001b014c77c
EOF

# Clone a fresh copy (important!)
cd /tmp
git clone --bare https://github.com/YOUR_USERNAME/flex.git flex.git
cd flex.git

# Clean secrets
bfg --replace-text /tmp/secrets.txt --force

# Push
git reflog expire --expire=now --all && git gc --prune=now --aggressive
git push --force

# Go back to your original repo and pull
cd ~/Dev/claude/flex
git fetch --all
git reset --hard origin/main
```

---

## Verification Checklist

- [ ] Generated new keypair with `generate_new_keypair.py`
- [ ] Updated `.env` with all three new values (Helius key, Jupiter key, Trading keypair)
- [ ] `.env` is in `.gitignore` (run `git check-ignore .env`)
- [ ] Old compromised account emptied of SOL
- [ ] Tested listener with new credentials
- [ ] Rotated Helius and Jupiter API keys
- [ ] Cleaned git history (optional)

---

## What's Safe Now?

✓ `.env` file - NEVER committed, only on your machine
✓ New keypair - Secure, not in git
✓ New API keys - Rotated and secure
✓ Repository - Public, but no longer contains secrets

---

## Important Reminders

1. **Never commit .env** - It's in `.gitignore` for a reason
2. **Never hardcode secrets** - Always use environment variables
3. **Rotate credentials regularly** - Even if not exposed
4. **Use `.env.example`** - It has templates, not real secrets
5. **Check `.gitignore`** - Before committing anything sensitive

---

## Need Help?

If anything goes wrong:
1. Run the verification test in Step 5
2. Check that `.env` exists and has the right format
3. Verify all three credentials are set
4. Make sure no other files contain secrets

Questions? Let me know!
