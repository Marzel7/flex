# CEX-Linked Funding Detection System

## Overview

Detect when funding accounts are connected to centralized exchanges (CEX). This reveals whether pump groups are using exchange-sourced wallets, which indicates:
- Professional operations (CEX integration)
- Potential wash trading (exchange liquidity)
- Organized crime (exchange account pools)
- Money laundering (mixing CEX and private wallets)

---

## Known CEX Funding Wallets

### Coinbase
- **DPqsobysNf5iA9w7zrQM8HLzCKZEDMkZsWbiidsAt1xo** (Coinbase Custody/Staking - recently identified)
- **GeiExVmVuconFfuxtC8mWBbGe1zxvTa3M8fcEcNc9gS** (Coinbase Hot Wallet)
- **2FPyTwcZLUg1MDrwsyoP4WA3d7QCezF7c7JUg2NzuM3w** (Coinbase Distribution)
- [More to be discovered via on-chain analysis]

### Binance
- **98rDvzr [various hot wallets]** (pattern: 98rd...)
- **jito [validator/MEV wallets]** (pattern: jito...)
- [Binance bridge wallets]

### Other Major CEX
- **Kraken, FTX (defunct), Huobi, Gate.io, Bybit** (patterns vary)

---

## Detection Strategy

### Method 1: Known CEX Address Database

```
cex_funding_accounts table
├─ cex_address (Solana wallet)
├─ exchange_name (Coinbase, Binance, etc.)
├─ wallet_type (Hot Wallet, Custody, Bridge, Distribution)
├─ discovered_date
├─ confidence_level (100% verified, 95% likely, 90% suspected)
└─ notes (how we identified it)
```

### Method 2: On-Chain Behavior Analysis

Characteristics of CEX wallets:
```
CEX Wallet Patterns:
├─ HUGE volume (millions of SOL moving through)
├─ Multiple recipients per transaction
├─ Regular timing patterns
├─ Direct connection to Marinade/Lido (staking)
├─ Connections to other known CEX wallets
├─ Public announcements matching wallet activity
└─ Traceable to official CEX documentation
```

### Method 3: Cross-Reference Data Sources

```
External Validation:
├─ Solscan labels (shows "Coinbase" tag)
├─ DefiLlama whitelists
├─ CEX official disclosures
├─ Reddit/Twitter community findings
└─ GitHub analysis repos
```

---

## Database Schema Addition

### New Table: cex_wallets

```sql
CREATE TABLE cex_wallets (
    cex_address TEXT PRIMARY KEY,
    exchange_name TEXT NOT NULL,        -- 'Coinbase', 'Binance', etc
    wallet_type TEXT NOT NULL,          -- 'Hot Wallet', 'Custody', 'Bridge', 'Distribution'
    confidence_level INTEGER,           -- 100 (verified), 95 (highly likely), 90 (suspected)
    discovered_date TIMESTAMP,
    discovery_source TEXT,              -- 'Solscan', 'Official', 'Community', 'Analysis'
    notes TEXT,
    is_active BOOLEAN DEFAULT 1
);
```

### Example Data

```sql
INSERT INTO cex_wallets VALUES
('DPqsobysNf5iA9w7zrQM8HLzCKZEDMkZsWbiidsAt1xo', 'Coinbase', 'Custody/Staking', 100, '2026-01-05', 'Official', 'Identified as Coinbase Custody wallet', 1),
('GeiExVmVuconFfuxtC8mWBbGe1zxvTa3M8fcEcNc9gS', 'Coinbase', 'Hot Wallet', 95, '2025-12-20', 'Solscan', 'Labeled on Solscan, high volume', 1),
('jitoBogCZ39ZfHWdxJp9b5THW7Ws5Y1tPUH..', 'Jito/Binance', 'MEV/Validator', 90, '2025-11-15', 'Analysis', 'Validator node pattern matching', 1);
```

---

## Detection Implementation

### Function: check_if_cex_funding

```python
def check_if_cex_funding(funding_account_address):
    """
    Check if a funding account is connected to a CEX

    Returns:
    {
        'is_cex': True/False,
        'exchange_name': 'Coinbase' or None,
        'wallet_type': 'Hot Wallet' or None,
        'confidence': 100 (verified) or 95/90,
        'risk_level': 'CRITICAL' / 'HIGH' / 'MEDIUM' / 'LOW',
        'flag': '🏛️ Coinbase Hot Wallet' or similar
    }
    """
    query = """
    SELECT *
    FROM cex_wallets
    WHERE cex_address = ?
    AND is_active = 1
    """

    result = db.query(query, (funding_account_address,))

    if result:
        # Found in CEX database
        return {
            'is_cex': True,
            'exchange_name': result['exchange_name'],
            'wallet_type': result['wallet_type'],
            'confidence': result['confidence_level'],
            'risk_level': determine_cex_risk(result),
            'flag': f"🏛️ {result['exchange_name']} - {result['wallet_type']}"
        }
    else:
        # Not a known CEX wallet
        return {
            'is_cex': False,
            'exchange_name': None,
            'wallet_type': None,
            'confidence': 0,
            'risk_level': 'UNKNOWN',
            'flag': None
        }

def determine_cex_risk(cex_record):
    """Determine risk level based on CEX wallet type"""
    if cex_record['exchange_name'] == 'Coinbase':
        if cex_record['wallet_type'] == 'Hot Wallet':
            return 'CRITICAL'  # Active trading/movement
        elif cex_record['wallet_type'] == 'Custody':
            return 'HIGH'      # Institutional but moving funds
        elif cex_record['wallet_type'] == 'Distribution':
            return 'MEDIUM'    # Deliberate fund distribution

    # Other exchanges
    return 'HIGH'
```

---

## Enhanced Table Output

### Before (Current)

```
Incoming SOL transfers: 71

Source Address                                | SOL Amount   | Transfers  | Type           | Status
dnd5bzqmcnfd6ycnequgumpbabsa764vjj1ccpxh2vmc  | 0.6000       | 6          | 🏦 Treasury    | 🚩 SHARED (3 creators)
9zz1mp5bnayyunuwwmbhpeeckyeoqaskak2uhq35bv9g  | 0.6000       | 6          | 🏦 Treasury    | ✓ Dedicated
```

### After (WITH CEX FLAGS)

```
Incoming SOL transfers: 71

Source Address                                | SOL Amount | Transfers | Type | Coordination    | CEX Status
──────────────────────────────────────────────────────────────────────────────────────────────────────────
DPqsobysNf5iA9w7zrQM8HLzCKZEDMkZsWbiidsAt1xo | 15.5000    | 8         | 🏦  | 🚩 SHARED (5)   | 🏛️ COINBASE
dnd5bzqmcnfd6ycnequgumpbabsa764vjj1ccpxh2vmc  | 0.6000     | 6         | 🏦  | 🚩 SHARED (3)   | ✓ Private
9zz1mp5bnayyunuwwmbhpeeckyeoqaskak2uhq35bv9g  | 0.6000     | 6         | 🏦  | ✓ Dedicated     | ✓ Private
```

---

## Risk Classification

### CEX Funding Risk Levels

| CEX Type | Wallet Type | Risk | Implication |
|----------|-------------|------|------------|
| Coinbase | Hot Wallet | 🔴 CRITICAL | Active CEX trading account - high volatility |
| Coinbase | Custody | 🔴 CRITICAL | Institutional but moving funds out |
| Coinbase | Distribution | 🟠 HIGH | Deliberate fund dispersal |
| Binance | Hot Wallet | 🔴 CRITICAL | Major exchange liquidity source |
| Binance | Bridge | 🟠 HIGH | Cross-chain laundering possible |
| Kraken | Any | 🟠 HIGH | Known for wash trading |
| FTX (defunct) | Any | ⚠️ MEDIUM | Legacy exchanges |
| Generic CEX | Unknown | 🟠 HIGH | Unknown but exchange-linked |
| Private | N/A | 🟢 LOW | Non-CEX wallet |

### Combined Risk Calculation

```python
def calculate_combined_risk(funding_analysis):
    """
    Combine coordination risk + CEX risk

    Base Risk = Coordination Risk (0-100)

    If CEX-linked:
        + 40 points if CRITICAL CEX
        + 25 points if HIGH CEX
        + 10 points if MEDIUM CEX

    Final = min(100, base_risk + cex_bonus)
    """
    base_risk = calculate_funding_risk(funding_analysis)

    for fund_account in funding_analysis:
        cex_check = check_if_cex_funding(fund_account['address'])

        if cex_check['is_cex']:
            if cex_check['risk_level'] == 'CRITICAL':
                base_risk += 40
            elif cex_check['risk_level'] == 'HIGH':
                base_risk += 25
            elif cex_check['risk_level'] == 'MEDIUM':
                base_risk += 10

    return min(100, base_risk)
```

---

## Alert Examples

### Alert 1: CEX + Shared Funding (CRITICAL)

```
🚨 CRITICAL ALERT 🚨

Token: BADTOKEN
Creator: 0xCREATOR123
Risk Score: 95/100 (CRITICAL)

Funding Sources:
1. DPqsobysNf5iA9w7zrQM8HLzCKZEDMkZsWbiidsAt1xo
   ├─ 🏛️ COINBASE Hot Wallet (CRITICAL)
   ├─ 🚩 SHARED with 12 other creators
   ├─ Total funding: 250+ SOL
   └─ Pattern: Likely coordinated pump group using CEX account

INTERPRETATION:
- CEX wallet funding multiple creators
- Signs of organized operation
- Possible wash trading between CEX and creators
- High probability of rug pull or pump & dump

RECOMMENDATION: AVOID / REPORT
```

### Alert 2: Multiple CEX Sources

```
⚠️ HIGH ALERT ⚠️

Token: SHITCOIN
Creator: 0xCREATOR456
Risk Score: 88/100 (HIGH)

Funding Sources:
1. DPqsobysNf5iA9w7zrQM8HLzCKZEDMkZsWbiidsAt1xo - 🏛️ Coinbase Hot
2. GeiExVmVuconFfuxtC8mWBbGe1zxvTa3M8fcEcNc9gS - 🏛️ Coinbase Hot
3. Private_Wallet_A - ✓ Private
4. Private_Wallet_B - ✓ Private

INTERPRETATION:
- Multiple CEX sources (professional operation)
- Mixed CEX + private wallets (obfuscation)
- Risk: Wash trading across CEX wallets

RECOMMENDATION: SUSPICIOUS - Monitor closely
```

### Alert 3: CEX + Single Use (MEDIUM)

```
⚠️ MEDIUM ALERT ⚠️

Token: NEWCOIN
Creator: 0xCREATOR789
Risk Score: 42/100 (MEDIUM)

Funding Sources:
1. DPqsobysNf5iA9w7zrQM8HLzCKZEDMkZsWbiidsAt1xo - 🏛️ Coinbase Hot
   ├─ Only funds THIS creator (dedicated)
   └─ Could be legitimate user withdrawing from CEX

INTERPRETATION:
- Single CEX source, but only for this token
- Possible legitimate user (less organized)
- Still suspicious due to CEX origin

RECOMMENDATION: CAUTION - More research needed
```

---

## Test Cases

### test_pumpswap_listener.py

```python
def test_listener_detects_coinbase_funding():
    """Token funded by Coinbase Hot Wallet"""
    event = {
        'token_mint': 'BADTOKEN123',
        'creator': 'CREATOR_ABC'
    }

    # Assume CREATOR_ABC funded by Coinbase wallet
    result = analyze_creator_wallet(event['creator'])

    assert result['has_cex_funding'] == True
    assert result['cex_type'] == 'Coinbase'
    assert result['risk_level'] == 'CRITICAL'

def test_listener_alerts_on_multiple_cex_sources():
    """Creator funded by multiple CEX accounts"""
    # Multiple Coinbase wallets funding same creator
    result = detect_cex_pattern()

    assert result['cex_count'] > 1
    assert result['alert_level'] == 'HIGH'

def test_listener_distinguishes_cex_vs_private():
    """Correctly identifies CEX vs private wallets"""
    funding_sources = [
        ('DPqsobysNf5iA9w7zrQM8HLzCKZEDMkZsWbiidsAt1xo', 'CEX'),
        ('9zz1mp5bnayyunuwwmbhpeeckyeoqaskak2uhq35bv9g', 'Private'),
    ]

    for address, expected_type in funding_sources:
        result = check_if_cex_funding(address)
        assert (result['is_cex'] and expected_type == 'CEX') or \
               (not result['is_cex'] and expected_type == 'Private')
```

---

## Data Collection Process

### Phase 1: Build CEX Wallet Database

```
Sources:
├─ Solscan labels (most accurate)
├─ Official CEX blog posts/announcements
├─ Community research (Reddit, Twitter)
├─ DefiLlama repository
├─ Blockchain analysis firms
└─ User reports
```

### Phase 2: Cross-Validation

```
For each suspected CEX wallet:
├─ Check Solscan for official label
├─ Verify with exchange (if possible)
├─ Analyze transaction patterns
├─ Compare with known CEX patterns
└─ Rate confidence level
```

### Example: Coinbase Identification

```
Found: DPqsobysNf5iA9w7zrQM8HLzCKZEDMkZsWbiidsAt1xo

Verification:
✓ Solscan label: "Coinbase Custody"
✓ Announced in Coinbase blog
✓ Large volume patterns match CEX behavior
✓ Connected to Marinade (staking service)
✓ Multiple sources confirm

Confidence: 100%
Classification: VERIFIED COINBASE CUSTODY
```

---

## Integration Points

### 1. analyze_creator_wallet.py

```python
# When displaying incoming transfers
for funding_account in incoming_transfers:
    cex_info = check_if_cex_funding(funding_account['address'])

    if cex_info['is_cex']:
        print(f"{funding_account['address']} | {cex_info['flag']}")
    else:
        print(f"{funding_account['address']} | ✓ Private")
```

### 2. test_pumpswap_listener.py

```python
# When token created
def on_pool_created(event):
    creator = event['creator']
    analysis = analyze_creator_wallet(creator)

    # Check for CEX funding
    if analysis['has_cex_funding']:
        logger.critical(f"🏛️ CEX-FUNDED TOKEN DETECTED: {event['token']}")
        alert_user(analysis)
```

### 3. Database Query

```python
# Query to find CEX-funded tokens
SELECT t.token_mint, t.pumpfun_creator, cw.cex_address, cw.exchange_name
FROM pools t
JOIN creator_sol_transfers cst ON t.pumpfun_creator = cst.creator_address
JOIN cex_wallets cw ON cst.counterparty_address = cw.cex_address
WHERE t.status = 'waiting'
ORDER BY cw.confidence_level DESC;
```

---

## Risk Impact Summary

### Before Detection
- All funding looks equal
- No way to know if exchange-sourced
- Can't distinguish pro vs amateur

### After Detection
- **CEX funding identified** → Professional/organized
- **Multiple CEX sources** → Network coordinated
- **CEX + Shared funding** → Criminal operation likely
- **Private only** → Possibly legitimate

### New Alert Tier

```
Risk Score Ranges:

0-20:   🟢 LOW (Private funding, single source)
20-40:  🟡 MEDIUM (Some coordination or new CEX)
40-60:  🟠 HIGH (Multiple sources or direct CEX)
60-80:  🔴 CRITICAL (CEX + shared + multiple creators)
80-100: 🔴🔴 EXTREME (Professional pump operation)
```

---

## Summary: CEX Detection Benefits

1. **Spot Professional Operations** - CEX wallets indicate organized groups
2. **Identify Laundering** - CEX → Private → CEX patterns
3. **Find Wash Trading** - Multiple CEX sources to single creator
4. **Assess Risk** - CEX-funded tokens are higher risk
5. **Build Network Map** - See which CEX accounts feed which creators
6. **Alert Early** - Flag tokens immediately upon detection

**The discovery of Coinbase wallet funding multiple creators is exactly the pattern we want to detect.**
