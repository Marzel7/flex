# Coordinated Funding Detection System

**Status**: Implemented
**Date**: January 6, 2026

## Overview

This system automatically detects and tracks coordinated funding accounts - accounts that fund multiple token creators, indicating pump-and-dump operations.

## Architecture

### 1. Coordinated Accounts Registry (`coordinated_funding_registry.py`)

Maintains a persistent JSON registry of known coordinated funding accounts.

**Structure**:
```json
{
  "5tzFkiKscXHK5ZXCGbXZxdw7gTjjD1mBwuoFbhUvuAi9": [
    "DYPWh3ZE4BJ1nGkdXfqdskU1j25evtVsZNomkS8f6xm5",
    "5AfLRcon7ZHfhpZHNPksZnDBcvERuZji56x18i6HEDFX",
    ...
  ],
  "AxiomRXZAq1Jgjj9pHmNqVP7Lhu67wLXZJZbaK87TTSk": [
    "A54iywr5nXU1UmxD63WEq4wtJfNUgUBjmgyp1Vxm4weq",
    "CJ2XGKsQSJB4gZXKAp97GJ41uscdXNcED8VzEiaz3S4f",
    ...
  ]
}
```

**Key Methods**:
- `add_account(funding_account, creators)` - Register a coordinated account
- `is_coordinated(funding_account)` - Check if account is known to be coordinated
- `get_creator_risk(creator)` - Get risk info for a creator
- `get_linked_creators(account)` - Get all creators funded by an account

### 2. Registry Population (`populate_coordinated_accounts.py`)

Scans the database to find all funding accounts that fund 2+ creators and registers them.

**Run**:
```bash
python populate_coordinated_accounts.py
```

**Current State** (as of backfill):
- **8 coordinated accounts** discovered
- **26 unique creators** in coordinated networks
- **3.25 creators** per account (average)
- **Largest group**: AxiomRXZ account funds 7 creators

### 3. Risk Assessment Pipeline

When a new token is detected (WebSocket):

1. **Creator Detection**: Extract `pumpfun_creator` from on-chain data
2. **Helius Analysis**: Fetch creator's SOL transfer history
3. **Funding Analysis**: Identify treasury/funding accounts
4. **Registry Check**: Check if creator is linked to known coordinated accounts
5. **Risk Assignment**: Mark as HIGH/CRITICAL if linked to coordinated group
6. **Database Update**: Store `funding_risk_level` in pools table

## How Coordinated Funding Is Established

### Discovery Process

1. **Treasury Reuse Detection (Level 1)**:
   - Find accounts that send SOL to multiple creators
   - Flag if 5+ creators share same treasury account
   - **Risk**: CRITICAL

2. **Creator Network Analysis (Level 2)**:
   - Find creators funded by same accounts
   - Check if those accounts also fund other creators
   - **Risk**: HIGH/MEDIUM depending on network density

3. **Registration**:
   - Add to coordinated_accounts.json
   - Available for new token assessment

### Example Flow

```
Funding Account: 5tzFkiK...
├─ Funds Creator: DYPWh3ZE... → Token: 4AxTpM (CRITICAL)
├─ Funds Creator: 5AfLRcon... → Token: 56orHg (CRITICAL)
├─ Funds Creator: 5gK8hsVe... → Token: 9ZzGi2 (CRITICAL)
├─ Funds Creator: 6Dc8gGBn... → Token: HJcGoj (CRITICAL)
├─ Funds Creator: 7Fsgge6h... → Token: AB1A7p (CRITICAL)
├─ Funds Creator: AtLasPGv... → Token: 62YRQu (CRITICAL)
└─ Funds Creator: BoJ3xHCF... → Token: J6ic6R (CRITICAL)
```

All 7 tokens marked CRITICAL because they're in same coordinated network.

## Integration with Listener

### New Token Detection Flow

When WebSocket detects new PumpSwap token:

```
Token Detected → Extract Creator → Fetch Helius Data → Check Registry
                                              ↓
                              Is Creator in Known Group?
                                    ↙               ↘
                                YES                 NO
                                 ↓                  ↓
                          Mark CRITICAL      Perform Level 1/2 Analysis
                          (Coordinated)      to Check for New Patterns
                                 ↓                  ↓
                          Update Database    Register if 2+ Creators Found
                                 ↓                  ↓
                          Display in UI      Update Database
```

## Display Integration

### Main Listener Output

Shows suspicious token count:
```
FUNDING ACCOUNTS SUMMARY - Linked Funding Sources
================================================

Suspicious Tokens: 26/93 (28%)
CRITICAL: 14 tokens
HIGH: 1 token
MEDIUM: 11 tokens

Token Details:
4AxTpM  | Creator: DYPWh3ZE... | CRITICAL | 5tzFkiK... | 5.01 SOL | 6 linked creators
56orHg  | Creator: 5AfLRcon... | CRITICAL | 5tzFkiK... | 0.96 SOL | 6 linked creators
...
```

### Key Metrics

- **Total Tokens**: 93
- **Suspicious (CRITICAL+HIGH+MEDIUM)**: 26 (28%)
- **Safe (LOW)**: 67 (72%)
- **Coordinated Funding Accounts**: 8
- **Creators in Coordinated Groups**: 26

## Usage

### Check a Token

```python
from coordinated_funding_registry import CoordinatedFundingRegistry

registry = CoordinatedFundingRegistry()
risk_info = registry.get_creator_risk("DYPWh3ZE4BJ1nGkdXfqdskU1j25evtVsZNomkS8f6xm5")
print(f"Is coordinated: {risk_info['is_coordinated']}")
print(f"Linked creators: {risk_info['total_linked_creators']}")
```

### Check an Account

```python
registry = CoordinatedFundingRegistry()
if registry.is_coordinated("5tzFkiKscXHK5ZXCGbXZxdw7gTjjD1mBwuoFbhUvuAi9"):
    creators = registry.get_linked_creators("5tzFkiKscXHK5ZXCGbXZxdw7gTjjD1mBwuoFbhUvuAi9")
    print(f"This account funds {len(creators)} creators")
```

### Get Registry Stats

```python
registry = CoordinatedFundingRegistry()
stats = registry.get_stats()
print(f"Total coordinated accounts: {stats['total_coordinated_accounts']}")
print(f"Total unique creators: {stats['total_unique_creators']}")
```

## New Token Workflow

When a new token launches:

1. **Detect**: WebSocket listener picks up pool creation
2. **Identify Creator**: Extract from on-chain metadata
3. **Analyze**: Fetch Helius SOL transfer history
4. **Check Registry**: Is creator linked to known coordinated account?
5. **Auto-Flag**: If yes → Mark HIGH/CRITICAL automatically
6. **Store**: Save risk assessment to database
7. **Display**: Show in FUNDING ACCOUNTS SUMMARY with other suspicious tokens

## Security Benefits

✓ **Immediate Detection**: New tokens linked to known pump groups flagged instantly
✓ **Network Analysis**: Shows which creators are in same group
✓ **Persistent Tracking**: Coordinated accounts logged for future reference
✓ **Comprehensive Coverage**: Both known and newly discovered pump patterns covered
✓ **User Visibility**: Clear display of suspicious tokens and their connections

## Statistics

### Current Coordinated Groups

| Account | Creators | Risk Level | Status |
|---------|----------|-----------|--------|
| 5tzFkiK... | 7 | CRITICAL | Active |
| AxiomRXZ... | 7 | CRITICAL | Active |
| G2YxRa6... | 3 | MEDIUM | Active |
| BmFdpra... | 2 | MEDIUM | Active |
| BY4StcU... | 2 | MEDIUM | Active |
| BTLG71P... | 2 | MEDIUM | Active |
| ASTyfSi... | 2 | HIGH | Active |
| 4NyK1Ad... | 2 | MEDIUM | Active |

### Risk Distribution

- **CRITICAL**: 14 tokens (15%)
- **HIGH**: 1 token (1%)
- **MEDIUM**: 11 tokens (12%)
- **LOW**: 67 tokens (72%)

## Future Enhancements

1. **Real-time Updates**: Automatically update registry when new coordinated patterns detected
2. **Risk Scoring**: Weighted scoring based on group size and activity
3. **Alerts**: Real-time alerts when new token joins coordinated group
4. **Time-based Analysis**: Detect if coordinated groups are active vs dormant
5. **Network Visualization**: Visual graph of creator-to-funding-account relationships
