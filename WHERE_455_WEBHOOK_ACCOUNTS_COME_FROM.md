# Where Do The 455 Helius Webhook Accounts Come From?

## The Answer: GENERAL SOLANA ACTIVITY (not specifically token creators)

### Breakdown:

**455 Total Webhook Accounts:**
```
├─ 123 accounts that ARE in creator_funders (senders)
│  └─ These are some of the 1,263 known creators/funders
│
├─ 55 accounts that ARE in creator_funders (receivers)  
│  └─ These are some of the 1,263 known creators/funders
│
└─ ~277 accounts NOT in creator_funders (~61%)
   └─ Random Solana wallets sending/receiving SOL
```

## What Helius Webhooks Actually Monitor

Helius is sending you **ALL SOL TRANSFERS** on the network, not just token creator activity.

Examples of what comes through:
- ✅ Creator funding transfers (123 accounts match creator_funders)
- ✅ Funder transfers (55 accounts match)
- ❌ Regular wallet-to-wallet transfers
- ❌ DEX swaps settling
- ❌ General trading activity
- ❌ Whale movements
- ❌ Random bot activity

## Why Only 455 Out of 1,263?

```
creator_funders: 1,263 creators/funders from token analysis
Helius webhooks: 455 addresses that have made SOL transfers

Missing 808 creators because:
├─ Never made direct SOL transfers (funded differently)
├─ Funded through DEX/LiquidityPool (not direct transfer)
├─ Historical tokens (before webhooks started)
└─ Batch operations that don't show as individual transfers
```

## So Helius Webhooks Know About:

**The 455 accounts are basically:**
- Some token creators who made SOL transfers
- Some funders who participated in transfers
- **LOTS of random Solana activity** (61% of accounts)

The webhooks are a **noisy stream of general SOL activity**, not a curated list of token creators.

## Visual:

```
creator_funders (1,263 token creators/funders)
├─ Scanned on-chain via RPC
├─ Tracked when tokens are created
└─ Historical complete record

Helius Webhooks (455 accounts with SOL transfers)
├─ Real-time transfers only
├─ ALL network activity
├─ Only accounts that made transfers in capture period
└─ Includes 123 from creator_funders + 277 random accounts

Overlap: 123-178 accounts (creators who happened to make transfers)
Gap: 808 creators who never appeared in transfer webhooks
```

## Key Insight

**The webhook stream is NOT designed to capture all creators.**

It captures **all SOL transfers**, and you're extracting whoever is involved in those transfers. This includes:
- Some token creators
- Most random wallets
- Market makers
- Trading bots
- Rug pull mechanics
- Legitimate traders

**The 808 missing creators aren't "missing" from webhooks - they just never made direct SOL transfers that Helius captured.**

## Conclusion

The 455 webhook accounts come from:
1. **Real-time SOL transfer monitoring** (Helius)
2. **General network activity** (not token-specific)
3. **Partial overlap** with known creators (123 out of 1,263)
4. **Mostly random accounts** (277/455 = 61%)

**Bottom line:** Helius webhooks give you a sample of active SOL addresses, which includes some token creators but is mostly general network noise.
