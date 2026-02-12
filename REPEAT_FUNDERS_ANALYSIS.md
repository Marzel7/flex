# Repeat Funders Analysis Report

**Date**: 2026-02-12
**Analysis Type**: Database query of creator_funders table
**Method**: Solana RPC (public API)

---

## Summary

Found **45 addresses that fund multiple creators** across the system, indicating potential coordinated funding networks or operational wallets.

### Top Repeat Funders

| Rank | Address | Creators Funded | Type |
|------|---------|-----------------|------|
| 1 | 5tzFkiKscXHK5ZXCGbXZxdw7gTjjD1mBwuoFbhUvuAi9 | **65** | Major Network Hub |
| 2 | AxiomRXZAq1Jgjj9pHmNqVP7Lhu67wLXZJZbaK87TTSk | **33** | Major Network Hub |
| 3 | iGdFcQoyR2MwbXMHQskhmNsqddZ6rinsipHc4TNSdwu | **32** | Major Network Hub |
| 4 | G2YxRa6wt1qePMwfJzdXZG62ej4qaTC7YURzuh2Lwd3t | **27** | Major Network Hub |
| 5 | ASTyfSima4LLAdDgoFGkgqoKowG1LZFDr9fAQrg7iaJZ | **14** | CEX (MEXC Hot Wallet) |
| 6 | BmFdpraQhkiDQE6SnfG5omcA1VwzqfXrwtNYBwWTymy6 | **12** | Operational Wallet |
| 7 | 2AQdpHJ2JpcEgPiATUXjQxA8QmafFegfQwSLWSprPicm | **9** | Operational Wallet |
| 8 | D89hHJT5Aqyx1trP6EnGY9jJUB3whgnq3aUvvCqedvzf | **8** | Operational Wallet |
| 9 | GJRs4FwHtemZ5ZE9x3FNvJ8TMwitKTh21yxdRPqn7npE | **8** | Operational Wallet |
| 10 | 2snHHreXbpJ7UwZxPe37gnUNf7Wx7wv6UKDSR2JckKuS | **6** | Operational Wallet |

---

## Key Findings

### Network Hubs (Most Connected)

**5tzFkiKscXHK5ZXCGbXZxdw7gTjjD1mBwuoFbhUvuAi9** (65 creators)
- Likely a major funding/distribution hub
- Could indicate coordinated token launches or PumpFun infrastructure

**AxiomRXZAq1Jgjj9pHmNqVP7Lhu67wLXZJZbaK87TTSk** (33 creators)
- Secondary hub for multi-creator funding
- Pattern suggests operational account managing multiple token launches

**iGdFcQoyR2MwbXMHQskhmNsqddZ6rinsipHc4TNSdwu** (32 creators)
- Consistent funding across ecosystem
- May be infrastructure provider or coordinated backer

### CEX Identified

**ASTyfSima4LLAdDgoFGkgqoKowG1LZFDr9fAQrg7iaJZ** - MEXC Hot Wallet (14 creators)
- Legitimate exchange account funding creators
- Flagged as CEX in system mapping (should be excluded from suspicious analysis)

---

## Distribution

- **Total repeat funders**: 45
- **Total creators in system**: 432
- **Creators funded by multiple addresses**: ~145 (34%)
- **Average creators per repeat funder**: 7.2

---

## Risk Assessment

### High Risk (20+ creators)
- 4 addresses (top network hubs)
- May indicate coordinated pump & dump schemes
- Recommend deeper analysis on transaction patterns

### Medium Risk (5-19 creators)
- 11 addresses
- Could be legitimate infrastructure or exchange accounts
- Some already identified as CEX

### Low Risk (2-4 creators)
- 30 addresses
- May be legitimate funding relationships
- Operational wallets managing multiple projects

---

## Next Steps

1. **Analyze transaction patterns** for top 4 repeat funders
2. **Check blockchain history** for coordination indicators
3. **Cross-reference with CEX accounts** (already done - MEXC identified)
4. **Monitor for new repeat funders** as system grows
5. **Add to risk scoring** for tokens funded by these addresses

---

## Data Quality Notes

- Analysis based on `creator_funders` table (pre-migration SOL transfers)
- May miss post-migration funding relationships
- Some addresses may be legitimate infrastructure accounts
- Requires manual review of top addresses for confirmation

---

## Generated Report

```bash
python3 batch_wallet_clustering.py --find-repeat-funders
```

This analyzes the full `creator_funders` table and identifies any wallet that funds more than one creator.

---

**Status**: Analysis Complete ✅
**Last Updated**: 2026-02-12 12:45 UTC
