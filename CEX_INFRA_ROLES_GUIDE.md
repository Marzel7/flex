# CEX & INFRA Account Roles in Flex Network Analysis

## 📋 Overview

The Flex system tracks **43 CEX addresses** and **59 INFRA programs** to understand their role in funding networks. These mapped accounts are critical for:

- Filtering legitimate exchange operations from suspicious funding patterns
- Identifying automated bot distribution networks
- Distinguishing organic funder activity from institutional sources
- Reducing false positives in coordinated funding detection

---

## 🏦 CEX ACCOUNT ROLES

### What Are CEX Accounts?

Cryptocurrency exchange addresses on Solana that handle user deposits, withdrawals, trading, and custody operations.

### CEX Account Types & Their Functions

| Type | Function | Impact on Analysis |
|------|----------|-------------------|
| **Hot Wallet** | Actively trades SOL for users, frequent deposits/withdrawals | Filter from suspicious networks - expected high velocity |
| **Cold Wallet** | Long-term storage of exchange reserves, rare movements | Low risk, institutional pattern |
| **Deposit Account** | Receives user SOL deposits, routes to hot wallets | Normal pattern, exclude from analysis |
| **Withdrawal Account** | Distributes traded funds back to users | Normal pattern, exclude from analysis |
| **Trading Account** | Active market-making and price discovery | Expected high frequency, exclude |
| **Staking Account** | Holds customer staked SOL and rewards | Normal pattern, exclude |
| **Treasury** | Long-term strategic holdings | Low frequency, institutional |

### CEX Exchanges Tracked (20 exchanges, 43 addresses)

#### Tier 1: Major Global Exchanges
- **Binance** (4 addresses) - Largest exchange, primary liquidity provider
  - Hot wallet: High frequency SOL trading
  - Cold wallet: Treasury reserves
  - Staking account: Customer staking operations

- **Coinbase** (12 addresses) - US regulated, institutional custody
  - Hot wallets: User trading
  - Deposit/Withdrawal: Institutional on/off ramps
  - All verified, low risk

- **Kraken** (2 addresses) - Secure EUR/USD trading
  - Hot wallet: Primary trading
  - Deposit: User inflows

- **OKX** (2 addresses) - Asian market leader
  - Main SOL account: Primary operations
  - Hot wallet: Spot & perpetuals trading

#### Tier 2: High-Volume Exchanges
- **Bybit** (2) - Derivatives exchange, high volume
- **Robinhood** (6) - Retail brokerage, many user accounts
- **KuCoin** (1) - Community exchange
- **MEXC** (1) - Emerging market
- **HTX** (1) - Asian exchange (formerly Huobi)
- **BingX** (1) - Copy trading platform

#### Tier 3: Specialized Services
- **Moonpay** - Fiat on-ramp (legitimate user entry)
- **Crypto.com** - Payments & trading
- **ChangeNow** - Atomic swaps
- **FixedFloat** - Instant crypto swaps
- **Revolut** - Fintech payments
- **Nexo** - Lending platform
- **Stake.com** - Crypto casino (higher risk activity)

#### Special Cases
- **Fireblocks** - Institutional custody (low risk)
- **FTX** - Historical legacy account (2022 collapse, no longer operational)
- **Bidget** - Unknown exchange

### Risk Classification

**Low Risk (Institutional):**
- Binance, Coinbase, Kraken, OKX, Bybit, Robinhood
- Fireblocks
- Verified on-chain addresses with high transaction volume

**Medium Risk (Emerging/Specialized):**
- Smaller exchanges with lower verification
- Lending/DeFi platforms
- Atomic swap services

**High Risk (Specialized Services):**
- Gambling/casino platforms
- Unverified addresses
- Services with unusual transaction patterns

### How CEX Accounts Are Used in Analysis

1. **Detection**: When a creator is funded, check if funder is a known CEX address
2. **Classification**: Mark as "INSTITUTIONAL" if matches CEX mapping
3. **Filtering**: May exclude CEX wallets from suspicious funding networks
4. **Confidence**: High confidence in legitimacy due to institutional regulation
5. **Pattern Analysis**: Monitor CEX hot wallet flows for unusual volume spikes

---

## 🔧 INFRA ACCOUNT ROLES

### What Are INFRA Accounts?

Solana ecosystem programs and automation services that operate infrastructure, bridges, automation bots, and core network operations.

### INFRA Categories & Their Functions

#### 1. **AUTOMATION** (55 accounts)
Programs that automate token distribution, scheduling, and monitoring

**Examples:**
- **RapidLaunch** - Token launch platform automation
- **Axiom** - Monitoring & automation infrastructure
- **Trojan Trade** - Bot automation for trading

**Role in Network:**
- Distribute SOL to many funders on a schedule
- Flag if creating artificial funding patterns
- May indicate organized distribution schemes

**Analysis Impact:**
- ⚠️ HIGH PRIORITY - Watch for suspicious coordination
- Normal: Legitimate automation for user services
- Suspicious: Coordinated distribution across multiple creators

#### 2. **BRIDGE** (1 account)
Cross-chain token transfers and liquidity bridges

**Example:**
- **deBridge** - Cross-chain token transfer vault

**Role in Network:**
- Move SOL between Solana and other chains
- Natural, expected pattern for cross-chain users

**Analysis Impact:**
- 📊 NORMAL - Exclude from suspicious networks
- Low risk pattern

#### 3. **PROTOCOL** (2 accounts)
Protocol operations, treasury, and governance

**Examples:**
- **Rollbit Treasury** - Protocol treasury account
- **SolCasino** - Protocol operations and distribution

**Role in Network:**
- Long-term holdings, strategic distribution
- Governance operations

**Analysis Impact:**
- 📊 NORMAL - Institutional pattern
- Exclude from suspicious networks

#### 4. **SYSTEM** (1 account)
Core Solana network operations

**Example:**
- **System Program** - Basic network operations, account creation, rent

**Role in Network:**
- Core infrastructure, not user-facing
- Affects all accounts

**Analysis Impact:**
- ❌ EXCLUDE - System level operations
- Not relevant to token funding analysis

#### 5. **VALIDATOR** (accounts)
Staking participation and block validation

**Role in Network:**
- Participate in Solana consensus
- Receive staking rewards

**Analysis Impact:**
- 📊 NORMAL - Low risk
- Exclude from suspicious networks

#### 6. **RELAYER** (accounts)
Message relaying between blockchains

**Role in Network:**
- Cross-chain communication
- Bridge operations

**Analysis Impact:**
- 📊 NORMAL - Low risk
- Exclude from suspicious networks

#### 7. **DEX** (accounts)
Decentralized exchange liquidity pools and automation

**Role in Network:**
- Provide trading liquidity
- Automated market making

**Analysis Impact:**
- 📊 NORMAL - Expected pattern
- Exclude from suspicious networks

#### 8. **LENDING** (accounts)
Lending protocol operations and loan management

**Role in Network:**
- Loan operations and collateral management
- Interest distribution

**Analysis Impact:**
- 📊 NORMAL - Institutional pattern
- Monitor for unusual distributions

### Risk Classifications

**Low Risk (Exclude from Analysis):**
- Bridge, Protocol, System, Validator, Relayer, DEX, Lending
- These are normal ecosystem operations

**Medium Risk (Monitor):**
- Automation: Watch for suspicious distribution patterns
- Only flag if coordinating with unknown funders

**Investigation Priority:**
1. 🔴 HIGH: Automation bots creating unusual funding patterns
2. 🟡 MEDIUM: Unknown category programs with unusual activity
3. 🟢 LOW: Known infrastructure with expected patterns

---

## 🌐 NETWORK ANALYSIS WORKFLOW

### Step-by-Step Flow

```
Token Created
    ↓
Extract Creator Funders (who funded the creator?)
    ↓
For Each Funder:
    ├─ Check against CEX mapping → If match: "INSTITUTIONAL"
    ├─ Check against INFRA mapping → If match: Category name
    └─ If no match → "ORGANIC" or "UNKNOWN"
    ↓
Extract Funder Sources (where did the funders get their money?)
    ├─ Build incoming transfer chains
    ├─ Check each source against CEX/INFRA
    └─ Track to original source
    ↓
Network Clustering (find coordinated funders)
    ├─ Identify shared bridge funders
    ├─ Calculate coordination confidence
    └─ Flag suspicious patterns
    ↓
Output Risk Assessment
    ├─ Self-Funded: % of funders that are bot/automation
    ├─ Coordinated: Multiple creators sharing funders
    ├─ Institutional: CEX involvement (may reduce suspicion)
    └─ Overall Risk: Combined assessment
```

### Detection Examples

#### Example 1: Legitimate CEX Funding
```
Creator: bwamJzzt...
Funder: Binance Hot Wallet (verified)
Result: ✅ INSTITUTIONAL - Reduces suspicion
Action: May exclude from suspicious networks
Risk: LOW - User deposits from major exchange
```

#### Example 2: Automation Bot Distribution
```
Creator A: xyz...
Creator B: abc...
Creator C: def...
Shared Funder: RapidLaunch bot
Result: ⚠️ COORDINATED + AUTOMATED
Action: Flag for investigation
Risk: MEDIUM - Multiple creators, same automation
```

#### Example 3: Self-Funding Scheme
```
Creator: malicious...
Funder A: wallet_1... → Created by creator
Funder B: wallet_2... → Created by creator
...
Funder Z: wallet_z... → Created by creator
Result: 🚩 SELF-FUNDED (100%)
Action: Flag as PUMP & DUMP scheme
Risk: CRITICAL - All funders are intermediate accounts
```

---

## 📊 Impact on Dashboard & Findings

### Creator Analysis Page

**Findings Tags Show:**
- ✅ CLEAN - No concerning patterns
- 🚩 SELF-FUNDING - Coordinated intermediaries
- ⚠️ CREATOR_FUNDING_CHAIN - Multiple layer funding
- 🔗 COORDINATED_FUNDERS - Shared with other creators
- 💱 INSTITUTIONAL - CEX-backed funding
- 🤖 AUTOMATED - Bot-based distribution

### Risk Calculation

```
Risk Score = (Self-Funding % × 0.4) +
             (Coordinated Score × 0.3) +
             (Unknown Funder % × 0.2) +
             (Automation Pattern × 0.1)
```

**CEX/INFRA Impact:**
- CEX funding: Reduces risk (institutional backing)
- INFRA automated: Increases investigation priority
- Unknown funding: Highest risk weight

---

## 🎯 Monitoring Recommendations

### CEX Accounts
- **Daily**: Monitor Binance & Coinbase hot wallet volumes
- **Weekly**: Check for unusual withdrawal patterns
- **Monthly**: Verify mappings against official exchange documentation

### INFRA Accounts
- **Real-time**: Alert on automation bot unusual distribution (>100 new funders/hour)
- **Daily**: Check bridge flows for massive cross-chain movements
- **Weekly**: Review new INFRA accounts discovered

### Detection Improvements
- Use CEX confidence scores (1-5) to weight institutional signals
- Create automation bot reputation scores
- Track CEX deposit flows to creator addresses
- Monitor for INFRA program updates that change behavior

---

## 📁 Reference Data

**Mapped Accounts:**
- CEX Wallets: 43 addresses across 20 exchanges
- INFRA Programs: 59 accounts across 8 categories
- Success Rate: ~98% coverage of major Solana programs

**Data Sources:**
- CEX addresses: Official exchange documentation, community verification
- INFRA accounts: On-chain program registry, Solana documentation
- Risk scores: Historical transaction analysis

**Last Updated:** 2026-02-28

---

## 🔗 Integration Points

**In Code:**
- `infra_mapping.py` - Main mapping definitions
- `automatic_cex_detection.py` - CEX detection logic
- `funder_incoming_extractor.py` - Applied during extraction
- `funder_helius_extractor.py` - Helius API integration
- `main.py` - Web UI displays CEX/INFRA labels

**In Database:**
- `cex_wallets` table - Store verified CEX addresses
- `infra_funders_observed` table - Track INFRA program activity
- `address_labels` table - Tag system for dynamic discovery

**In UI:**
- Creator analysis page: Shows CEX/INFRA involvement
- Funding hub page: Displays wallet type and exchange
- Networks page: Colors by account category
