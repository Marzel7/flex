# Example Transaction - Parsed and Analyzed

**Signature**: `3v5kHrMdRcb7VSrE7jHhL4T4Vwyaw1BbakNsiNjMPMXeMvuMGu9Zxx964yxw6n6gKWRQsRbpJrLdjQ3HicuDUpyC`

**Source**: Fetched with `getTransaction(..., {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0})`

---

## Critical Finding

**Fee Payer (accountKeys[0])**: `qNGhUruCGJpXJdsnV74USHErcbm3CrXRsnP8D6Z34Hh`

This is the **transaction creator** because:
- ✅ Always first in accountKeys array
- ✅ Has `"signer": true` (must sign the transaction)
- ✅ Has `"writable": true` (will pay fees)
- ✅ Fee payer is the only required signer for any transaction

---

## Full Account Keys Array (27 accounts)

```json
[
  {
    "pubkey": "qNGhUruCGJpXJdsnV74USHErcbm3CrXRsnP8D6Z34Hh",
    "signer": true,
    "source": "transaction",
    "writable": true
  },
  {
    "pubkey": "X5QPJcpph4mBAJDzc4hRziFftSbcygV59kRb2Fu6Je1",
    "signer": false,
    "source": "transaction",
    "writable": true
  },
  {
    "pubkey": "taw7GrEhArVdk7a24AyQDF6SGMbUkyrL8VGSGtzq75Z",
    "signer": false,
    "source": "transaction",
    "writable": true
  },
  {
    "pubkey": "3cif7fWLq3aV44mds8AEcJgSCsgLWDtTte8nw5bNpFMD",
    "signer": false,
    "source": "transaction",
    "writable": true
  },
  {
    "pubkey": "4a4jPVBLLZUuvNHTtF9TUh2U7zg771ZrZqrz7WXnygZT",
    "signer": false,
    "source": "transaction",
    "writable": true
  },
  {
    "pubkey": "5xYYRu3PUXFpr4R1EbYGh67cdSH4iXFWpGi8siENYuF1",
    "signer": false,
    "source": "transaction",
    "writable": true
  },
  {
    "pubkey": "6KWVa3kqV32xZd3qh9pTdmgFk33hGL8q6eC6Zeb1Pxsr",
    "signer": false,
    "source": "transaction",
    "writable": true
  },
  {
    "pubkey": "7hZk539vpFoxPzLNge961adaHVoEc4STWGzqDvW8qYm3",
    "signer": false,
    "source": "transaction",
    "writable": true
  },
  {
    "pubkey": "C2aFPdENg4A2HQsmrd5rTw5TaYBX5Ku887cWjbFKtZpw",
    "signer": false,
    "source": "transaction",
    "writable": true
  },
  {
    "pubkey": "DttWaMuVvTiduZRnguLF7jNxTgiMBZ1hyAumKUiL2KRL",
    "signer": false,
    "source": "transaction",
    "writable": true
  },
  {
    "pubkey": "E8nyC8sJACCt6uGN4YUy4SRJFiXKXFfD4Y2gn2Fz9Ga1",
    "signer": false,
    "source": "transaction",
    "writable": true
  },
  {
    "pubkey": "HLkPCmpjhvvNfjdYsYj9FAu63Q83SFfMNu98Zu8Xagv7",
    "signer": false,
    "source": "transaction",
    "writable": true
  },
  {
    "pubkey": "11111111111111111111111111111111",
    "signer": false,
    "source": "transaction",
    "writable": false
  },
  {
    "pubkey": "ComputeBudget111111111111111111111111111111",
    "signer": false,
    "source": "transaction",
    "writable": false
  },
  {
    "pubkey": "So11111111111111111111111111111111111111112",
    "signer": false,
    "source": "transaction",
    "writable": false
  },
  {
    "pubkey": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
    "signer": false,
    "source": "transaction",
    "writable": false
  },
  {
    "pubkey": "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",
    "signer": false,
    "source": "transaction",
    "writable": false
  },
  {
    "pubkey": "jitodontfront1111shittertech111111111111111",
    "signer": false,
    "source": "transaction",
    "writable": false
  },
  {
    "pubkey": "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA",
    "signer": false,
    "source": "transaction",
    "writable": false
  },
  {
    "pubkey": "pfeeUxB6jkeY1Hxd7CsFCAjcbHA9rWtchMGdZ6VojVZ",
    "signer": false,
    "source": "transaction",
    "writable": false
  },
  {
    "pubkey": "3bzeQJqDPfQQbxJ6VmtMkXyyQEJrj9HS1SMvHR1Hpump",
    "signer": false,
    "source": "transaction",
    "writable": false
  },
  {
    "pubkey": "5PHirr8joyTMp9JMm6nW7hNDVyEYdkzDqazxPD7RaTjx",
    "signer": false,
    "source": "transaction",
    "writable": false
  },
  {
    "pubkey": "7hTckgnGnLQR6sdH7YkqFTAA7VwTfYFaZ6EhEsU3saCX",
    "signer": false,
    "source": "transaction",
    "writable": false
  },
  {
    "pubkey": "83nSUB5UsGiJ2DmVE6bzSH8QrQUm6BsodHA7nQQc9xgf",
    "signer": false,
    "source": "transaction",
    "writable": false
  },
  {
    "pubkey": "ADyA8hdefvWN2dbGGWFotbzWxrAvLW83WG6QCVXvJKqw",
    "signer": false,
    "source": "transaction",
    "writable": false
  },
  {
    "pubkey": "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL",
    "signer": false,
    "source": "transaction",
    "writable": false
  },
  {
    "pubkey": "GS4CU59F31iL7aR2Q8zVS8DRrcRnXX1yjQ66TqNVQnaR",
    "signer": false,
    "source": "transaction",
    "writable": false
  }
]
```

---

## Account Analysis

### Signer Analysis
- **Total Signers**: 1 (just the fee payer at [0])
- **Fee Payer**: `qNGhUruCGJpXJdsnV74USHErcbm3CrXRsnP8D6Z34Hh` (signer=true)
- **Others**: All non-signers (signer=false)

### Writable Analysis
- **Writable Accounts** (0-11): User-controlled accounts being modified
  - Likely: User token accounts, pool accounts, etc.
- **Read-Only Accounts** (12-26): System programs and state accounts

### Known Programs (Read-Only)
| Address | Program | Purpose |
|---------|---------|---------|
| 11111111111111111111111111111111 | System Program | Account creation, transfers |
| ComputeBudget111111111111111111111111111111 | Compute Budget | Transaction prioritization |
| So111111111111111111111111111111111111111112 | WSOL (Wrapped SOL) | SOL token wrapper |
| TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA | Token Program | Token operations |
| TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb | Token Extensions | Extended token features |
| jitodontfront1111shittertech111111111111111 | Jito (MEV Blocker) | MEV protection |
| pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA | Pump.fun AMM | Swaps and liquidity |
| pfeeUxB6jkeY1Hxd7CsFCAjcbHA9rWtchMGdZ6VojVZ | Pump.fun Fee Program | Fee collection |
| ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL | ATA Program | Associated Token Accounts |

### User Accounts (Writable)
- **[0]**: Fee payer (also a user account)
- **[1-11]**: User-controlled accounts (token accounts, pool accounts, etc.)

### Data Accounts (Read-Only)
- **[20]**: `3bzeQJqDPfQQbxJ6VmtMkXyyQEJrj9HS1SMvHR1Hpump` - Token mint
- **[21]**: `5PHirr8joyTMp9JMm6nW7hNDVyEYdkzDqazxPD7RaTjx` - Possibly bonding curve
- **[22-26]**: Additional program/data accounts

---

## Key Takeaways

### Creator Attribution
```
Fee Payer = qNGhUruCGJpXJdsnV74USHErcbm3CrXRsnP8D6Z34Hh
↑
This is the creator because:
1. ALWAYS first signer (accountKeys[0])
2. ALWAYS has signer=true
3. ALWAYS pays transaction fees
4. Proves private key holder signed the tx
5. Cannot be spoofed (requires signature)
```

### Why Fee Payer = Creator

In a CREATE transaction:
- Fee payer must sign the transaction
- Only the creator has the private key
- Only one signer per transaction (the fee payer)
- Therefore: fee payer ≡ creator (cryptographically proven)

### Why NOT to Use Earliest Bonding Curve TX Fee Payer

The earliest transaction on a bonding curve account could be:
- A swap/trade (not creation)
- A program instruction reuse
- A later activity that happens to be earliest

That fee payer is just "who paid for that activity" — not necessarily the creator.

---

## UI Display in Transaction Viewer

When user clicks "View" button next to CREATE tx:

```
╔════════════════════════════════════════════════════════════════╗
║ Transaction Details                                           ║
║ 3v5kHrMdRcb7VSrE7jHhL4T4Vwyaw1BbakNsiNjMPMXeMvuMGu9Zxx...  ║
├────────────────────────────────────────────────────────────────┤
│ 🔗 View on Solscan    📋 Copy Signature                        │
├────────────────────────────────────────────────────────────────┤
│ Account Keys (jsonParsed)                                      │
│ [                                                              │
│   {                                                            │
│     "pubkey": "qNGhUruCGJpXJdsnV74USHErcbm3CrXRsnP8D6Z34Hh", │
│     "signer": true,                                            │
│     "source": "transaction",                                   │
│     "writable": true                                           │
│   },                                                           │
│   ... (26 more accounts)                                       │
│ ]                                                              │
├────────────────────────────────────────────────────────────────┤
│ Fee Payer (Creator)                                            │
│                                                                │
│ qNGhUruCGJpXJdsnV74USHErcbm3CrXRsnP8D6Z34Hh                   │
│ ✓ Fee payer (always first signer) = transaction creator       │
└────────────────────────────────────────────────────────────────┘
```

---

**Ready for verification**: All data can be cross-checked on [Solscan](https://solscan.io/tx/3v5kHrMdRcb7VSrE7jHhL4T4Vwyaw1BbakNsiNjMPMXeMvuMGu9Zxx964yxw6n6gKWRQsRbpJrLdjQ3HicuDUpyC)
