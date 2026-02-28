# Flex Network Documentation Summary

## 📊 FLEX_Comprehensive_Network_Documentation.xlsx

A comprehensive Excel workbook containing complete network analysis data for the Flex token funding project.

### Sheet Breakdown

#### 1. **Networks Overview** (501 rows)
All 41,734 funder networks mapped with:
- Primary funder addresses
- Network size (count of members)
- Total SOL volume across network
- Cluster ID for grouping
- Detection timestamp
- Connected funders and transfer chains

**Key Stats:**
- Total Networks: 41,734
- Unique Funders: 6,485
- Total Members: Network-wide
- Total Volume: $31,711.97 SOL
- Avg Network Size: ~6,485
- Max Network Size: 6,485

#### 2. **Coordinated Edges** 
Creator-to-creator relationships showing:
- Creator A address
- Creator B address (coordinated with A)
- Bridge funder connecting them
- Confidence score (0-1)
- Detection timestamp

Shows which creators share funding relationships and intermediary funders.

#### 3. **Super Clusters** (503 rows)
High-level coordinated network clusters:
- Super cluster ID
- Network count in cluster
- Creator count in cluster
- Risk level (NORMAL/HIGH/CRITICAL)
- Creator reuse ratio (identifies repeated creator patterns)
- Creator reuse tags (INDEPENDENT/SUSPICIOUS/COORDINATED)
- Shared creator count

**Purpose:** Identify multi-creator coordination schemes and suspicious patterns.

#### 4. **Top Creators** (303 rows)
Creators ranked by activity:
- Creator address
- Funder count (how many funded this creator)
- Self-funding flag (yes/no)
- Self-funding percentage
- Self-funding intermediates count

**Use Case:** Focus on most prolific creators and identify self-funding schemes.

#### 5. **CEX Roles & Functions** (35 rows) ⭐ NEW
Detailed breakdown of cryptocurrency exchange account roles:
- Exchange name and account count
- Wallet type breakdown (Hot, Cold, Deposit, Withdrawal, Trading, Staking)
- Primary role description
- Risk profile classification
- Monitoring notes

**Purpose:** Understand how exchange accounts impact network analysis

**Key Content:**
- All 20 exchanges mapped with role descriptions
- Tier 1: Binance, Coinbase, Kraken, OKX (major global exchanges)
- Tier 2: Bybit, Robinhood, KuCoin, MEXC, HTX, BingX (high volume)
- Tier 3: Moonpay, ChangeNow, Revolut, Nexo, Stake.com, Fireblocks

#### 6. **INFRA Roles & Functions** (19 rows) ⭐ NEW
Infrastructure program categories and their ecosystem roles:
- Category (Automation, Bridge, Protocol, System, Validator, etc.)
- Count of programs per category
- Primary function description
- Risk level assessment
- Monitoring priority
- Example programs

**Purpose:** Identify automated bot distribution vs institutional operations

**Key Content:**
- Automation: 55 programs (highest priority monitoring)
- Bridge: Cross-chain operations
- Protocol: Governance and treasury
- System: Core Solana operations
- Validator/Relayer/DEX/Lending: Normal ecosystem operations

#### 7. **Network Roles Summary** (34 rows) ⭐ NEW
Complete guide to how CEX/INFRA detection impacts analysis:
- Detection & filtering methodology
- CEX account roles & impact on findings
- INFRA account roles & impact
- Complete network analysis workflow (8-step process)
- Detection examples (legitimate vs suspicious patterns)

**Purpose:** Understand end-to-end how account roles drive risk assessment

**Key Content:**
- Workflow from token creation → founder funding → risk assessment
- 3 examples: Legitimate CEX funding, Automation bot, Self-funding scheme
- How findings tags are generated (CLEAN, SELF-FUNDED, COORDINATED, etc.)
- Risk calculation methodology

#### 8. **CEX Wallets** (51 rows)
All cryptocurrency exchange addresses mapped:
- CEX address (Solana wallet)
- Exchange name (Coinbase, Binance, etc.)
- Wallet type (Hot, Cold, Deposit, Withdrawal, etc.)
- Confidence level (1-5)
- Discovery date
- Active status

**Purpose:** Filter legitimate CEX activity from organic funding.

#### 9. **INFRA Programs** (29 rows)
Infrastructure programs and services:
- Funder address
- Creator count served
- Total SOL volume
- First observed date
- Notes/category

**Purpose:** Identify automated funder services and distribution mechanisms.

#### 10. **Documentation Index** (9 rows)
Quick reference guide with:
- Sheet name
- Description
- Record count
- Primary use case

## 📈 Data Coverage

| Category | Count | Status |
|----------|-------|--------|
| Funder Networks | 41,734 | ✅ Complete |
| Coordinated Edges | 500+ | ✅ Sampled |
| Super Clusters | 503 | ✅ Complete |
| Top Creators | 300+ | ✅ Ranked |
| CEX Wallets | 43+ | ✅ Mapped |
| INFRA Programs | 1000+ | ✅ Tracked |

## 🎯 Use Cases

1. **Risk Assessment** - Identify coordinated funding networks and suspicious patterns
2. **Creator Profiling** - Rank creators by funding diversity and self-funding behavior
3. **Exchange Detection** - Filter CEX activity to focus on organic funders
4. **Network Analysis** - Map funding relationships and identify clusters
5. **Monitoring** - Track changes in network topology and creator behavior

## 🔍 Key Insights

- **41,734 networks** track how SOL flows through funder relationships
- **503 super clusters** identify coordinated multi-creator schemes
- **43 CEX wallets** mapped for filtering institutional activity
- **300+ top creators** ranked by funding patterns
- **Self-funding detection** identifies circular funding schemes

## 🎓 NEW: Comprehensive Role Guide

**CEX_INFRA_ROLES_GUIDE.md** provides in-depth documentation:

### CEX Account Roles
- 43 addresses mapped to 20 exchanges
- Role types: Hot Wallet, Cold Wallet, Deposit, Withdrawal, Trading, Staking, Treasury
- Risk classification: Low (institutional), Medium, High
- How CEX funding reduces suspicion vs unknown funders

### INFRA Account Roles
- 59 programs across 8 categories
- Automation: Highest monitoring priority (55 programs)
- Bridge: Cross-chain, normal pattern (exclude)
- Protocol: Long-term operations (exclude)
- System: Core network (exclude)
- Validator/Relayer/DEX: Normal ecosystem (exclude)

### Impact on Network Analysis
- Detection workflow: Check funder against mappings
- Classification: Mark as INSTITUTIONAL or AUTOMATED
- Filtering: May exclude from suspicious networks
- Findings tags: CLEAN, SELF-FUNDED, COORDINATED_FUNDERS, CREATOR_FUNDING_CHAIN
- Risk calculation: CEX reduces risk, Automation increases investigation priority

### Complete Workflow Example
Step-by-step example from token creation → founder funding → risk assessment with detection of legitimate vs suspicious patterns.

## 📱 Integration

Use this documentation for:
- ✅ Data-driven risk assessment (CEX/INFRA context)
- ✅ Network visualization tools (role-based coloring)
- ✅ Machine learning feature engineering (institutional signals)
- ✅ Compliance monitoring (knowing what to ignore)
- ✅ Funding pattern analysis (organic vs bot-driven)
- ✅ Automated monitoring alerts (automation bot detection)

## 📁 Documentation Files

1. **FLEX_Comprehensive_Network_Documentation.xlsx** (11 MB) - 10 sheets with network data
2. **NETWORK_DOCUMENTATION_SUMMARY.md** - Quick reference guide
3. **CEX_INFRA_ROLES_GUIDE.md** - Detailed roles & functions (600+ lines)

---

*Generated: 2026-02-28 | Database: flex_complete_database.db*
