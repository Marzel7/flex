# Funder Analysis Quick Reference

**Status**: ✅ Production Ready

---

## Most Common Uses

### Analyze ALL Funders for a Creator
```bash
python3 test_funder_network.py <creator_address> --all
```
Shows all 100% of funders, identifies repeat funders, logs CEX/INFRA accounts

### Investigate a Suspicious Repeat Funder
```bash
python3 analyze_repeat_funder.py <funder_address>
```
Shows all creators this funder supports, finds coordination patterns

### Find All Repeat Funders in System
```bash
python3 batch_wallet_clustering.py --find-repeat-funders
```
Lists all addresses funding 2+ creators

---

## Account Type Meanings

| Flag | Meaning | Risk | Action |
|------|---------|------|--------|
| ✅ CEX | Exchange account | NONE | Ignore (legitimate) |
| ✅ INFRA | Infrastructure | NONE | Ignore (legitimate) |
| 🎯 PUMPFUN | Token creator | NONE | Ignore (platform) |
| ⚠️ SUSPICIOUS | Unknown suspicious | MEDIUM | Monitor |
| (none) | Unknown | HIGH | Investigate |

---

## Quick Answers

**Q: Which RPC?**
A: Public Solana RPC (`api.mainnet-beta.solana.com`), rate limited to ~30 req/min

**Q: How fast?**
A: Database tools: <100ms. RPC tool: 2-5 sec per funder (rate limited)

**Q: Can I test all funders?**
A: Yes! Use `--all` flag with test_funder_network.py

**Q: How many repeat funders exist?**
A: 45 addresses funding 2+ creators in the current database

---

## Three Tool Comparison

| Tool | Data | Speed | Use |
|------|------|-------|-----|
| test_funder_network.py | DB | FAST | Quick funder check ✅ |
| analyze_repeat_funder.py | DB | FAST | Network investigation ✅ |
| analyze_funder_networks.py | RPC | SLOW | Detailed TX analysis |

**Recommendation**: Use DB tools (test/analyze) for comprehensive analysis, RPC tool for details when needed.

---

## Example Workflow

```bash
# Step 1: Check creator's funders
python3 test_funder_network.py <creator> --all

# Step 2: Found suspicious repeat funder?
# Investigate it deeper
python3 analyze_repeat_funder.py <suspicious_funder> --limit 20

# Step 3: Need SOL transfer details?
# Use RPC tool (slower)
python3 analyze_funder_networks.py <creator> --limit 10
```

---

## Key Discoveries

- ✅ Top repeat funder (65 creators) = Binance 2 Hot Wallet (legitimate)
- ✅ Second repeat funder (33 creators) = Axiom Infrastructure (legitimate)
- 🔍 Unknown repeat funders (27-32 creators) = Need investigation
- 💡 CEX/INFRA detection prevents false positives in risk scoring

---

**Last Updated**: 2026-02-12
**All Tools**: Production Ready ✅
