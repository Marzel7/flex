# Funder, Sender, Creator Roles & Findings Tags

## 📋 Complete Guide to Network Participants and Risk Detection

This document describes:
1. The roles of **Senders**, **Funders**, and **Creators** in the network
2. How **SOL transfers below the MINIMUM_SOL threshold are filtered**
3. All **findings tags**, what they mean, and how they're calculated
4. **Integration into the risk assessment pipeline**

---

## 👥 Network Participant Roles

### The Three-Layer Funding Flow

```
        SENDERS
          ↓↓↓
       (SOL transfers)
          ↓↓↓
      FUNDERS (Intermediaries)
          ↓↓↓
       (SOL transfers)
          ↓↓↓
     CREATORS (Token Creators)
          ↓↓↓
      (Launch Token)
          ↓↓↓
        TOKENS
```

---

## 1️⃣ SENDERS - The Money Source

### Definition
**Senders** are wallet addresses that send SOL to **Funders**. They represent the original source of funding capital.

### Role in Network Analysis
- **Originating Source**: Send SOL to funders who later fund creators
- **Fund Distribution**: May send to many funder addresses
- **Suspicious Pattern Indicator**: If a sender distributes to many addresses, all of which fund the same creator, this indicates **self-funding** or **coordinated distribution**

### Characteristics
- First layer of extraction (funder incoming transfers)
- May be:
  - **CEX accounts** (exchanges like Binance, Coinbase) → Legitimate fund source
  - **INFRA accounts** (automation bots) → Potentially coordinated
  - **Unknown organic wallets** → Highest investigation priority
  - **Individual creators** (reusing wallets) → Red flag for self-funding

### Example
```
Sender: Unknown wallet XYZ
  ↓ Sends 0.5 SOL each to:
    → Funder A
    → Funder B
    → Funder C
    → Funder D
  ↓ All 4 funders then send to:
    → Creator ABC
Result: 🚩 SELF-FUNDING PATTERN
```

### Database Table
- **`funder_incoming_transfers`** - Records of SOL from senders to funders
  - `sender_address` - The sender wallet
  - `funder_address` - Intermediate funder
  - `amount_sol` - SOL transferred
  - `transaction_signature` - TX hash
  - `sender_type` - Classification (cex/infra/unknown)

---

## 2️⃣ FUNDERS - The Intermediaries

### Definition
**Funders** are wallet addresses that:
1. Receive SOL from **Senders**
2. Send SOL to **Creators** or other **Funders**
3. Act as intermediary participants in the funding network

### Role in Network Analysis
- **Bridge Wallets**: Connect senders to creators
- **Relay Points**: May pass funds through multiple layers
- **Coordination Indicators**: If multiple funders share the same sender or same creator, indicates coordination
- **Risk Signals**: Unknown funders with many creators indicate suspicious distribution

### Characteristics
- Second layer of extraction
- May be:
  - **Known intermediaries** (automation bots distributing) → Monitored for patterns
  - **Unknown wallets** → Classified by behavior
  - **Repeat funders** (fund multiple creators) → Higher risk

### Key Metrics
- **Funder Count per Creator**: How many unique funders funded this creator
- **Creator Count per Funder**: How many unique creators did this funder fund
- **Self-Funding Status**: Is this funder controlled by the creator?

### Database Tables
- **`creator_funders`** - Direct creator-funder relationships
  - `creator_address` - The creator funded
  - `funder_address` - The funder
  - `amount_sol` - SOL amount
  - `is_cex` - Boolean, known exchange?
  - `source_type` - 'original_sender' or 'relay'

- **`funder_incoming_transfers`** - Where funders got their money
  - `funder_address` - The funder receiving SOL
  - `sender_address` - Who sent to the funder
  - `amount_sol` - Amount received

- **`funder_outgoing_transfers`** - Where funders sent money
  - `funder_address` - The funder sending SOL
  - `recipient_address` - Recipient (creator or another funder)
  - `amount_sol` - Amount sent

---

## 3️⃣ CREATORS - The Token Launchers

### Definition
**Creators** are wallet addresses that:
1. Receive SOL from **Funders**
2. Launch Pump.Fun tokens
3. May send SOL to other addresses (creating outgoing transfer patterns)

### Role in Network Analysis
- **Primary Risk Entity**: Subject of analysis
- **Behavior Indicator**: Outgoing transfers reveal distribution patterns
- **Network Node**: Member of funding networks and clusters

### Characteristics
- Third layer of extraction
- Subject of detailed analysis including:
  - Funding sources (who funded them)
  - Outgoing distributions (who they fund)
  - Self-funding patterns (do they fund themselves through intermediaries?)
  - Coordination (do they share funders with other creators?)
  - Network membership (are they part of a coordinated cluster?)

### Key Metrics
- **Token Count**: How many tokens created
- **Funder Count**: How many funders provided initial funding
- **Self-Funding %**: Percentage of funders who are self-created intermediaries
- **Distribution Pattern**: Do they distribute to many addresses?
- **Network Role**: Are they a hub (fund many others) or spoke (get funded)?

### Database Tables
- **`creator_funders`** - Who funded this creator
  - Primary source of funding data

- **`creator_outgoing_transfers`** - Where creator sends SOL
  - Records of creator's outgoing distributions
  - Scanned every 12 hours to track activity

- **`creator_self_funding`** - Self-funding detection
  - `is_self_funding` - 1 or 0 flag
  - `self_funding_percentage` - % of funders that are intermediaries
  - `self_funding_intermediates` - Count of self-created wallets

- **`funding_chains`** - Creator-to-creator funding
  - Shows when creator funds other creators

---

## 🔍 SOL Transfer Filtering: MINIMUM_SOL Threshold

### Definition

**MINIMUM_SOL = 0.001 SOL** (~$0.15 USD)

All SOL transfers below this threshold are **filtered out** from the analysis.

### Why Filter?

1. **Dust Transfers**: Network spam, test transactions, or minimal amounts
2. **Fee Precision**: Small amounts may be system fees or error corrections
3. **Noise Reduction**: Reduces false positives in suspicious pattern detection
4. **Performance**: Excludes millions of micro-transfers that don't affect risk assessment

### Where Filtering Occurs

**1. Funder Incoming Transfers** (`funder_incoming_extractor.py`, line 51)
```python
MIN_SOL = 0.001

# During extraction:
if amount_sol < 0.001:
    skip_transfer()  # Don't record
else:
    save_to_database()  # Record if >= 0.001 SOL
```

**2. Funder Analysis** (`funder_helius_extractor.py`)
```python
if amount_sol < 0.001:
    continue  # Skip dust amounts
```

**3. Outgoing Transfer Analysis** (`creator_outgoing_extractor.py`)
```python
# Only extract transfers >= 0.001 SOL
if transfer_amount < MIN_SOL:
    ignore()
```

### Impact on Data

- **Recorded Transfers**: Only >= 0.001 SOL
- **Database Size**: Filters out 30-40% of micro-transactions
- **Network Analysis**: Focuses on meaningful funding flows
- **Self-Funding Detection**: Works on meaningful amounts only

### Example

```
Creator: ABC...
Funders detected:

Funder A: 1.5 SOL    ✅ Recorded (>= 0.001)
Funder B: 0.05 SOL   ✅ Recorded (>= 0.001)
Funder C: 0.0005 SOL ❌ Filtered out (< 0.001)
Funder D: 0.15 SOL   ✅ Recorded (>= 0.001)

Total recorded funders: 3
Total recorded amount: 1.7 SOL
```

---

## 🏷️ Findings Tags - Complete Reference

Findings tags are generated based on analyzing creator behavior and funding patterns.

### Tag Categories

#### 1. 🚩 SELF-FUNDING (Risk: CRITICAL)

**Meaning**: Creator funds themselves through intermediate wallet addresses

**Detection Logic**:
```python
# Query creator_self_funding table
if creator.is_self_funding == 1:
    percentage = creator.self_funding_percentage
    intermediates = creator.self_funding_intermediates

    findings.append(f'🚩 SELF-FUNDING ({percentage:.0f}%)')
```

**What It Indicates**:
- Creator owns multiple wallet addresses
- Distributes SOL among these wallets
- These wallets then "fund" the creator's token
- Creates illusion of organic support
- **Classic pump-and-dump indicator**

**Example**:
```
Creator: bwamJzzt...
Self-funding percentage: 85%
Self-funding intermediates: 24 out of 28 funders

Interpretation:
- 24 of 28 "funders" are actually creator's own wallets
- Only 4 genuine external funders
- HIGH RISK: Coordinated self-funding scheme
```

**Risk Level**: 🔴 CRITICAL
**Action**: Investigate creator for pump-and-dump

---

#### 2. ⚠️ CREATOR_FUNDING_CHAIN (Risk: HIGH)

**Meaning**: Creator receives SOL from funders who themselves receive from other creators

**Detection Logic**:
```python
# Query funding_chains table
cursor.execute("""
    SELECT COUNT(*) as chain_count
    FROM funding_chains
    WHERE source_creator = ?
""", (creator,))

if chain_count > 0:
    findings.append('⚠️ CREATOR_FUNDING_CHAIN')
```

**What It Indicates**:
- Creator's funders are funded by OTHER creators
- Multi-layer funding through creator network
- Coordination indicator: Creators funding each other's token launches
- Fund redistribution among coordinated group

**Example**:
```
Creator A launches token
  ↓ Funded by:
    - Funder X (who was funded by Creator C)
    - Funder Y (who was funded by Creator D)
    - Funder Z (who was funded by Creator B)

Result: ⚠️ CREATOR_FUNDING_CHAIN
Interpretation: Coordinated creator group
```

**Risk Level**: 🟠 HIGH
**Action**: Check if creator is part of coordinated network

---

#### 3. ⚠️ DISTRIBUTION_PATTERN (Risk: HIGH)

**Meaning**: Creator distributes SOL to many recipients (more than expected)

**Detection Logic**:
```python
# Query creator_outgoing_transfers
recipient_count = count_distinct_recipients(creator)
funder_count = count_distinct_funders(creator)

if recipient_count > funder_count * 5 and funder_count < 20:
    findings.append('⚠️ DISTRIBUTION_PATTERN')
```

**What It Indicates**:
- Creator receives SOL from few funders
- Distributes to MANY recipients
- Pattern of redistribution
- May indicate:
  - Creating more intermediate funders
  - Spreading funds for next round of tokens
  - Bot-like automated distribution

**Example**:
```
Creator receives from: 10 funders
Creator distributes to: 85 recipients

Ratio: 8.5:1 (many recipients per funder)

Result: ⚠️ DISTRIBUTION_PATTERN
Interpretation: Suspicious redistribution scheme
```

**Risk Level**: 🟠 HIGH
**Action**: Monitor for follow-up token launches

---

#### 4. 🔗 COORDINATED_FUNDERS (Risk: HIGH)

**Meaning**: Creator shares funders with other creators

**Detection Logic**:
```python
# Query coordinated_creator_edges
cursor.execute("""
    SELECT COUNT(*) as coordinated_count
    FROM coordinated_creator_edges
    WHERE creator_a = ? OR creator_b = ?
""", (creator, creator))

if coordinated_count > 0:
    findings.append(f'🔗 COORDINATED_FUNDERS ({coordinated_count})')
```

**What It Indicates**:
- Multiple creators share common funders
- Same wallets fund different tokens
- Strong indicator of coordination
- "Bridge funder" connecting creators

**Example**:
```
Creator A receives from: Funder X
Creator B receives from: Funder X
Creator C receives from: Funder X

Funder X = "Bridge Funder"

Result: 🔗 COORDINATED_FUNDERS (3 creators)
Interpretation: Coordinated token launch network
```

**Risk Level**: 🟠 HIGH
**Action**: Investigate entire network

---

#### 5. ⚠️ NETWORK_MEMBER (Risk: MEDIUM)

**Meaning**: Creator is part of a detected funder network

**Detection Logic**:
```python
# Query funding_network_members
cursor.execute("""
    SELECT network_id FROM funding_network_members
    WHERE funder_address = ?
    LIMIT 1
""", (creator,))

if network_row:
    findings.append('⚠️ NETWORK_MEMBER')
```

**What It Indicates**:
- Creator identified as part of network
- Network analysis has mapped relationships
- Part of clustered funding group
- May have multiple connections

**Risk Level**: 🟡 MEDIUM
**Action**: Check network cluster analysis

---

#### 6. 🤖 AUTOMATION_DETECTED (Risk: MEDIUM)

**Meaning**: Creator's funders include automation programs

**Detection Logic**:
```python
for funder in creator.funders:
    funder_info = get_account_info(funder)
    if funder_info['category'] == 'automation':
        findings.append('🤖 AUTOMATION_DETECTED')
        break
```

**What It Indicates**:
- Funding came from bot automation
- RapidLaunch, Axiom, or similar bot
- Scheduled, programmed distribution
- Higher coordination likelihood

**Risk Level**: 🟡 MEDIUM
**Action**: Check automation patterns

---

#### 7. 💱 INSTITUTIONAL_BACKED (Risk: LOW)

**Meaning**: Creator received funding from known CEX address

**Detection Logic**:
```python
for funder in creator.funders:
    cex_info = get_cex_info(funder)
    if cex_info:  # Found in CEX mapping
        findings.append('💱 INSTITUTIONAL_BACKED')
        break
```

**What It Indicates**:
- Funding from legitimate exchange
- Binance, Coinbase, Kraken, etc.
- Higher legitimacy signal
- Reduces suspicion score

**Risk Level**: 🟢 LOW
**Action**: May exclude from suspicious networks

---

#### 8. ✅ CLEAN (Risk: NONE)

**Meaning**: No suspicious patterns detected

**Detection Logic**:
```python
if not any(findings):
    findings.append('✅ CLEAN')
```

**What It Indicates**:
- No self-funding detected
- No coordination with other creators
- Funders are organic/legitimate
- Normal funding pattern
- Low risk token launch

**Risk Level**: 🟢 NONE
**Action**: Monitor for changes

---

## 📊 Findings Tag Calculation Workflow

### Step-by-Step Process

```
1. CREATOR DETECTED
   ↓
2. EXTRACT FUNDERS
   └─ From creator_funders table
   └─ Only >= 0.001 SOL transfers
   ↓
3. CHECK SELF-FUNDING
   ├─ Query creator_self_funding
   ├─ Calculate % of self-created funders
   └─ If > 50%: Add 🚩 SELF-FUNDING tag
   ↓
4. CHECK CREATOR FUNDING CHAIN
   ├─ Query funding_chains where source_creator = this creator
   └─ If exists: Add ⚠️ CREATOR_FUNDING_CHAIN tag
   ↓
5. CHECK DISTRIBUTION PATTERN
   ├─ Count outgoing recipients vs funders
   ├─ If recipients > (funders × 5): Add ⚠️ DISTRIBUTION_PATTERN
   └─ Only if funder_count < 20
   ↓
6. CHECK COORDINATED EDGES
   ├─ Query coordinated_creator_edges
   └─ If creator connected: Add 🔗 COORDINATED_FUNDERS tag
   ↓
7. CHECK NETWORK MEMBERSHIP
   ├─ Query funding_network_members
   └─ If member: Add ⚠️ NETWORK_MEMBER tag
   ↓
8. CHECK CEX/INFRA
   ├─ For each funder:
   │  ├─ Check if CEX → Add 💱 INSTITUTIONAL_BACKED
   │  ├─ Check if INFRA → Add 🤖 AUTOMATION_DETECTED
   │  └─ Check if CEX/INFRA exist → May reduce risk
   ↓
9. FINAL VERDICT
   ├─ If any risk tag: Display findings
   └─ If no tags: Add ✅ CLEAN
   ↓
10. DISPLAY ON UI
    └─ Show badges with emojis and descriptions
```

---

## 🔢 Risk Score Calculation

### Formula

```
Risk Score = (Self-Funding % × 0.40) +
             (Coordinated Score × 0.30) +
             (Unknown Funder % × 0.20) +
             (Automation Score × 0.10)

Adjustments:
  - CEX Backing: -0.20 (institutional backing reduces risk)
  - INFRA Automation: +0.10 (bots increase risk)
  - Clean Pattern: Base risk = 0.1
```

### Example Calculations

**Example 1: Pure Self-Funding**
```
Creator: malicious_pump
Funders: 20 total
  - 18 self-created (90%)
  - 2 unknown external (10%)

Risk = (90% × 0.40) + (0 × 0.30) + (10% × 0.20) + (0 × 0.10)
Risk = 0.36 + 0 + 0.02 + 0
Risk = 0.38 → CRITICAL (0.38 > 0.30)

Tags: 🚩 SELF-FUNDING (90%)
```

**Example 2: Coordinated Network**
```
Creator: token_launch
Funders: 15 total
  - 3 self-created (20%)
  - 12 coordinated external (80%)
  - Shares 8 funders with 5 other creators

Risk = (20% × 0.40) + (0.80 × 0.30) + (0% × 0.20) + (0.05 × 0.10)
Risk = 0.08 + 0.24 + 0 + 0.005
Risk = 0.325 → CRITICAL (0.325 > 0.30)

Tags: 🔗 COORDINATED_FUNDERS (8 shared), ⚠️ CREATOR_FUNDING_CHAIN
```

**Example 3: CEX-Backed (Legitimate)**
```
Creator: legit_token
Funders: 10 total
  - 0 self-created (0%)
  - 7 from Coinbase (70%)
  - 3 unknown (30%)

Risk = (0% × 0.40) + (0 × 0.30) + (30% × 0.20) + (0 × 0.10)
Risk = 0 + 0 + 0.06 + 0
Risk = 0.06 - 0.20 (CEX adjustment) = -0.14 → Clamped to 0.0 (CLEAN)

Tags: 💱 INSTITUTIONAL_BACKED, ✅ CLEAN
```

---

## 🌐 Network Tier Classifications

Creators are classified into risk tiers based on their findings:

### Tier 1: CRITICAL 🔴
- Risk Score > 0.30
- Tags: 🚩 SELF-FUNDING (>80%)
- Action: Immediate investigation

### Tier 2: HIGH 🟠
- Risk Score 0.15-0.30
- Tags: 🔗 COORDINATED_FUNDERS, ⚠️ CREATOR_FUNDING_CHAIN
- Action: Monitor closely

### Tier 3: MEDIUM 🟡
- Risk Score 0.05-0.15
- Tags: ⚠️ DISTRIBUTION_PATTERN, 🤖 AUTOMATION_DETECTED
- Action: Watch for changes

### Tier 4: LOW 🟢
- Risk Score < 0.05
- Tags: 💱 INSTITUTIONAL_BACKED, ✅ CLEAN
- Action: Normal monitoring

---

## 📱 UI Integration

### Creator Analysis Page
- Displays all findings badges with emojis
- Colored backgrounds indicate risk level
- Clickable for detailed explanation

### Dashboard
- Red badges for CRITICAL findings
- Orange for HIGH risk
- Yellow for MEDIUM risk
- Green for CLEAN/LOW risk

### API Response
```json
{
  "creator_address": "bwamJzzt...",
  "findings": [
    "🚩 SELF-FUNDING (85%)",
    "⚠️ CREATOR_FUNDING_CHAIN",
    "🔗 COORDINATED_FUNDERS (5)"
  ],
  "risk_level": "CRITICAL",
  "risk_score": 0.38
}
```

---

## 🔄 Complete Data Flow

### Token Creation to Risk Assessment

```
1. TOKEN DETECTED
   └─ Pump.Fun WebSocket listener
   └─ Extract creator address

2. CREATOR FUNDING EXTRACTION
   └─ realtime_creator_funding_extractor.py
   └─ Query: Who funded this creator?
   └─ Records in creator_funders (>= 0.001 SOL)

3. FUNDER SOURCE EXTRACTION
   └─ funder_incoming_extractor.py
   └─ Query: Where did funders get their money?
   └─ Records in funder_incoming_transfers (>= 0.001 SOL)
   └─ Classify senders (CEX/INFRA/Unknown)

4. CREATOR OUTGOING EXTRACTION
   └─ creator_outgoing_extractor.py (12-hour scan)
   └─ Query: Where does creator send SOL?
   └─ Records in creator_outgoing_transfers

5. NETWORK CLUSTERING
   └─ cross_funding_network_analyzer.py
   └─ Build relationship graphs
   └─ Identify coordinated groups

6. SELF-FUNDING DETECTION
   └─ Query: Do funders match creator pattern?
   └─ Update creator_self_funding table

7. FINDINGS GENERATION
   └─ Analyze all data points
   └─ Generate findings tags
   └─ Calculate risk score

8. DISPLAY ON UI
   └─ Creator Analysis Page
   └─ Dashboard
   └─ API endpoints
```

---

## 📌 Summary Table

| Role | Definition | Risk Level | Key Metric |
|------|-----------|-----------|-----------|
| **SENDER** | Original SOL source | Variable | Fund distribution width |
| **FUNDER** | Intermediary wallet | Variable | Creator count served |
| **CREATOR** | Token launcher | Primary Target | Funding pattern |

| Finding | Risk | Emoji | Trigger |
|---------|------|-------|---------|
| SELF-FUNDING | CRITICAL | 🚩 | > 50% self-created funders |
| CREATOR_FUNDING_CHAIN | HIGH | ⚠️ | Creator's funder funded by other creator |
| DISTRIBUTION_PATTERN | HIGH | ⚠️ | Recipients > 5× Funders |
| COORDINATED_FUNDERS | HIGH | 🔗 | Share funders with other creators |
| NETWORK_MEMBER | MEDIUM | ⚠️ | Part of detected network |
| AUTOMATION_DETECTED | MEDIUM | 🤖 | Funded by automation bot |
| INSTITUTIONAL_BACKED | LOW | 💱 | Funded by known CEX |
| CLEAN | NONE | ✅ | No suspicious patterns |

---

## 🚀 Implementation Details

### Threshold: MINIMUM_SOL = 0.001 SOL
- **Location**: `funder_incoming_extractor.py:51`
- **Effect**: Filters dust transfers, focuses on meaningful funding
- **Impact**: Reduces ~30-40% of micro-transactions from analysis

### Findings Detection: API `/api/creator-recent-checks`
- **Location**: `main.py:16502-16649`
- **Frequency**: Real-time generation
- **Storage**: Computed on-demand from database tables

### Risk Calculation: Weighted Formula
- **Self-Funding Weight**: 40% (strongest indicator)
- **Coordination Weight**: 30% (network effect)
- **Unknown Funder Weight**: 20% (unverified sources)
- **Automation Weight**: 10% (bot activity)

---

*Documentation compiled: 2026-02-28*
*Covers all roles, thresholds, and findings tags with complete detection logic*
