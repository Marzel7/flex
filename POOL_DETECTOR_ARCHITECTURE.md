# Pool Detector: Architecture & Flow Diagrams

**Purpose:** Visual reference for pool detection system architecture and execution flow

---

## System Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────┐
│                      POOL DETECTION SYSTEM                              │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                           │
│  ┌───────────────────────────────────────────────────────────────────┐  │
│  │  HELIUS WEBSOCKET                                                 │  │
│  │  [Monitoring PumpSwap migrations]                                 │  │
│  └──────────────────────────┬──────────────────────────────────────┘  │
│                             │                                           │
│                             v                                           │
│  ┌───────────────────────────────────────────────────────────────────┐  │
│  │  MIGRATION DETECTED                                               │  │
│  │  Token: 8JQ1UHWeEdXqij9AKGdK9vBFTSNe8HKBu8jAVSLXpump             │  │
│  │  Signature: 5uHXbZuMDYM2sUPa...                                  │  │
│  └──────────────────────────┬──────────────────────────────────────┘  │
│                             │                                           │
│                             v                                           │
│  ┌───────────────────────────────────────────────────────────────────┐  │
│  │  FETCH TRANSACTION                                                │  │
│  │  RPC: getTransaction(signature)                                   │  │
│  └──────────────────────────┬──────────────────────────────────────┘  │
│                             │                                           │
│                             v                                           │
│  ┌───────────────────────────────────────────────────────────────────┐  │
│  │  POOL DETECTOR (NEW HARDENED VERSION)                             │  │
│  │  ┌─────────────────────────────────────────────────────────────┐  │  │
│  │  │  PHASE 1: Normalize Account Keys                            │  │  │
│  │  │  - String → String                                          │  │  │
│  │  │  - Dict{pubkey} → String                                    │  │  │
│  │  │  - Dict{address} → String                                   │  │  │
│  │  └───────────────────────┬────────────────────────────────────┘  │  │
│  │                          │                                         │  │
│  │                          v                                         │  │
│  │  ┌─────────────────────────────────────────────────────────────┐  │  │
│  │  │  PHASE 2: Log Transaction Shape                             │  │  │
│  │  │  [POOL_DETECT] tx_version=... base=25 writable=0           │  │  │
│  │  │                readonly=0 has_lookups=false total=25        │  │  │
│  │  └───────────────────────┬────────────────────────────────────┘  │  │
│  │                          │                                         │  │
│  │                          v                                         │  │
│  │  ┌─────────────────────────────────────────────────────────────┐  │  │
│  │  │  PHASE 3: Scan All Accounts (with optional debug logs)      │  │  │
│  │  │                                                              │  │  │
│  │  │  for idx, account in all_accounts:                          │  │  │
│  │  │    info = getAccountInfo(account)                           │  │  │
│  │  │    [DEBUG] idx=0 owner=11111... data_len=0 amm_match=False  │  │  │
│  │  │    [DEBUG] idx=1 owner=11111... data_len=0 amm_match=False  │  │  │
│  │  │    [DEBUG] idx=2 owner=pAMM... data_len=500 amm_match=True  │  │  │
│  │  └───────────────────────┬────────────────────────────────────┘  │  │
│  │                          │                                         │  │
│  │                          v                                         │  │
│  │  ┌─────────────────────────────────────────────────────────────┐  │  │
│  │  │  PHASE 4: Validate AMM Candidates                           │  │  │
│  │  │                                                              │  │  │
│  │  │  if owner in AMMPrograms.ALL:                               │  │  │
│  │  │    if data_len >= AMMDataLengths.EXPECTED[owner]:           │  │  │
│  │  │      return account_addr  ✅ POOL FOUND                     │  │  │
│  │  │    else:                                                     │  │  │
│  │  │      log "invalid data_len"                                 │  │  │
│  │  │      continue scanning                                       │  │  │
│  │  └───────────────────────┬────────────────────────────────────┘  │  │
│  │                          │                                         │  │
│  │         NO POOL FOUND    │    POOL FOUND                          │  │
│  │         in transaction   v                                         │  │
│  │                    ┌─────────────┐                                │  │
│  │                    │  ✅ Return  │                                │  │
│  │                    │  Pool Addr  │                                │  │
│  │                    └─────────────┘                                │  │
│  │         │                                                          │  │
│  │         v                                                          │  │
│  │  ┌─────────────────────────────────────────────────────────────┐  │  │
│  │  │  PHASE 5: Fallback Discovery (if primary fails)             │  │  │
│  │  │                                                              │  │  │
│  │  │  [POOL_DETECT_FALLBACK] Starting vault discovery...         │  │  │
│  │  │  getTokenLargestAccounts(mint)                              │  │  │
│  │  │  → Check top 5 vaults for owner chain...                    │  │  │
│  │  │  → Return pool_address if found, else None                  │  │  │
│  │  └───────────────────────┬────────────────────────────────────┘  │  │
│  │                          │                                         │  │
│  │                          v                                         │  │
│  │         FALLBACK         │     BOTH FAILED                         │  │
│  │         SUCCEEDED        v                                         │  │
│  │         ┌──────┐  ┌────────────────────┐                          │  │
│  │         │✅Ret │  │  return None       │                          │  │
│  │         │Pool │  │  (undiagnosable)   │                          │  │
│  │         └──────┘  └────────────────────┘                          │  │
│  │                                                                    │  │
│  └──────────────────────────┬──────────────────────────────────────┘  │
│                             │                                           │
│         ┌───────────────────┴───────────────────┐                     │
│         │                                       │                     │
│         v                                       v                     │
│  ┌──────────────────────────┐        ┌──────────────────────┐       │
│  │ POOL PARSING             │        │ FAILURE (FALLBACK)   │       │
│  │ (RaydiumAMMParser, etc)  │        │ Log incident, return │       │
│  │ Extract vaults           │        │ None (None of the    │       │
│  │ Register in DB           │        │ above succeeded)     │       │
│  │ Activate WebSocket       │        └──────────────────────┘       │
│  └──────────────────────────┘                                        │
│         │                                                             │
│         v                                                             │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │ POOL AUTO-REGISTERED                                         │   │
│  │ [POOL] 🚀 Auto-registered pool for WebSocket pricing        │   │
│  │ Database: token_pool_accounts                               │   │
│  │ WebSocket: Subscribed to vault updates                      │   │
│  └──────────────────────────────────────────────────────────────┘   │
│         │                                                             │
│         v                                                             │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │ PRICE API READY                                              │   │
│  │ GET /api/price/{mint} → Real on-chain price                 │   │
│  │ WebSocket updates pushed every ~500ms                        │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                                                                       │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Detection Flow: Success Path

```
MIGRATION DETECTED
       │
       v
FETCH TRANSACTION
       │
       v
NORMALIZE ACCOUNT KEYS
  - String → String
  - Dict → String
       │
       v
LOG TRANSACTION SHAPE
  [POOL_DETECT] tx_version=None base=25 writable=0 readonly=0 total=25
       │
       v
SCAN ACCOUNT 0 → owner=11111... data_len=0 → NOT AMM
       │
       v
SCAN ACCOUNT 1 → owner=11111... data_len=0 → NOT AMM
       │
       v
SCAN ACCOUNT 2 → owner=11111... data_len=0 → NOT AMM
       │
       v
SCAN ACCOUNT 3 → owner=pAMM... data_len=500 → AMM MATCH!
       │
       v
VALIDATE DATA LENGTH → 500 >= 296 ✅ VALID
       │
       v
✅ POOL FOUND: pAMMBay6oce...
       │
       v
PARSE POOL STATE
  Extract vault addresses
       │
       v
REGISTER IN DATABASE
  token_pool_accounts INSERT
       │
       v
ACTIVATE WEBSOCKET
  Subscribe to vault updates
       │
       v
🚀 PRICE READY
  GET /api/price/{mint} → returns on-chain price
```

---

## Detection Flow: Failure Path (with Fallback)

```
MIGRATION DETECTED
       │
       v
FETCH TRANSACTION
       │
       v
NORMALIZE ACCOUNT KEYS
       │
       v
LOG TRANSACTION SHAPE
  [POOL_DETECT] tx_version=None base=25 writable=0 readonly=0 total=25
       │
       v
SCAN ALL 25 ACCOUNTS
  [POOL_DETECT_DEBUG] idx=0 ... amm_match=False
  [POOL_DETECT_DEBUG] idx=1 ... amm_match=False
  ... (none match) ...
  [POOL_DETECT_DEBUG] idx=24 ... amm_match=False
       │
       v
NO AMM-OWNED ACCOUNT FOUND
  [POOL_DETECT] No AMM-owned pool found in transaction (25+0+0)
       │
       v
FALLBACK: VAULT DISCOVERY
  [POOL_DETECT_FALLBACK] Starting vault-based discovery...
  getTokenLargestAccounts(mint)
  Check vault #1 owner chain... → no pool ref
  Check vault #2 owner chain... → no pool ref
  ...
       │
       v
FALLBACK FAILED
  [POOL_DETECT] Fallback vault discovery failed for TOKEN
       │
       v
⚠️  UNREGISTERED TOKEN
  No price available
  Manual investigation needed
```

---

## Log Output Examples

### Success Case

```
[WEBSOCKET] 🔍 Migration found. listen_to_launches=True
[WEBSOCKET] 🚨 Migration #1 detected: 5uHXbZuMDYM2sUPa...
[TX_CACHE] 💾 CACHED: 5uHXbZuMDYM2sUPa... (32377 bytes)
[EVENT] 🚀 MIGRATION DETECTED: 8JQ1UHWeEdXqij9AKGdK9vBFTSNe8HKBu8jAVSLXpump
[POOL_DETECT] tx_version=None base_keys=25 writable_loaded=0 readonly_loaded=0 has_addressTableLookups=False total=25
[POOL_DETECT] ✅ Found pumpswap pool at index 3: pAMMBay6oceH9fJK... (data_len=500)
[RAYDIUM] Extracted vaults: base=EPjFWaLb... quote=So11111111...
[POOL] 🚀 Auto-registered pool for WebSocket pricing
[WEBSOCKET] Subscribed to vault: EPjFWaLb...
[WEBSOCKET] Subscribed to vault: So11111111...
[PRICE] ✅ Real-time pricing activated
```

### Failure Case with Fallback

```
[EVENT] 🚀 MIGRATION DETECTED: 8JQ1UHWeEdXqij9AKGdK9vBFTSNe8HKBu8jAVSLXpump
[POOL_DETECT] tx_version=None base_keys=25 writable_loaded=0 readonly_loaded=0 has_addressTableLookups=False total=25
[POOL_DETECT] No AMM-owned pool found in 25 accounts (searched 25 + 0 + 0)
[POOL_DETECT] ⏳ Program-ownership detection found no AMM pool, trying vault scan...
[POOL_DETECT_FALLBACK] Starting vault-based discovery for 8JQ1UHWeEdXqij9AKGdK9vBFTSNe8HKBu8jAVSLXpump
[RPC_METRICS] record_request source_file=pumpfun_curve_listener method=getTokenAccountsByMint
[POOL_DETECT_FALLBACK] Checked vault abc123... owner=TokenkegQf8fwkgw...
[POOL_DETECT] ⚠️ Fallback vault discovery failed for 8JQ1UHWeEdXqij9AKGdK9vBFTSNe8HKBu8jAVSLXpump
[POOL] ⚠️ Pool auto-registration failed
```

### Debug Mode Output

```
[EVENT] 🚀 MIGRATION DETECTED: 8JQ1UHWeEdXqij9AKGdK9vBFTSNe8HKBu8jAVSLXpump
[POOL_DETECT] tx_version=None base_keys=25 writable_loaded=0 readonly_loaded=0 has_addressTableLookups=False total=25
[POOL_DETECT_DEBUG] idx=0 addr=11111111111111... owner=11111111111111... exec=False data_len=0 amm_match=False
[POOL_DETECT_DEBUG] idx=1 addr=TokenkegQf8fwkgw... owner=BPFLoaderUpgradeab... exec=True data_len=0 amm_match=False
[POOL_DETECT_DEBUG] idx=2 addr=pAMMBay6oceH9fJK... owner=pAMMBay6oceH9fJK... exec=False data_len=500 amm_match=True
[POOL_DETECT] ✅ Found pumpswap pool at index 2: pAMMBay6oce... (data_len=500)
```

---

## Data Length Validation

```
Account Scan Loop:

for idx, account in all_accounts:
    info = RPC.getAccountInfo(account)
    owner = info.owner
    data_len = len(info.data)

    if owner in AMMPrograms.ALL:
        program_name = AMMPrograms.identify_program(owner)
        min_expected = AMMDataLengths.EXPECTED[owner]

        ┌─────────────────────────┐
        │ owner in AMMPrograms?   │
        └────────┬────────────────┘
                 yes
                 │
        ┌────────v──────────────┐
        │ data_len >= min_len?  │
        └────────┬──────┬───────┘
                yes    no
                │      │
            ✅ VALID   ❌ TOO SMALL
            Return    Skip & Continue
            Address   (log warning)

Minimums:
  Raydium AMM:     296 bytes
  Orca Whirlpool:  232 bytes
  Meteora DLMM:    232 bytes
  PumpSwap:        296 bytes (uses Raydium layout)
```

---

## State Machine: Pool Detection

```
          ┌─────────────────────────┐
          │   MIGRATION DETECTED    │
          └────────────┬────────────┘
                       │
                       v
          ┌─────────────────────────┐
          │   TX SHAPE LOGGED       │
          │   (v0? lookups? size?)  │
          └────────────┬────────────┘
                       │
                       v
          ┌─────────────────────────┐
          │   SCANNING ACCOUNTS     │
          │   (idx 0 → idx N)       │
          └────────────┬────────────┘
                       │
         ┌─────────────┴──────────────┐
         │                            │
         v                            v
    FOUND AMM-OWNED             NO AMM-OWNED
    CANDIDATE                   CANDIDATE
         │                            │
         v                            v
    VALIDATE DATA            TRY FALLBACK
    LENGTH                   VAULT DISCOVERY
         │                            │
    ┌────┴────┐               ┌───────┴──────┐
    │          │               │              │
   VALID      INVALID         SUCCESS       FAILURE
    │          │               │              │
    v          v               v              v
  ✅ POOL    SKIP &        ✅ POOL       ⚠️  UNREGISTERED
  FOUND      CONTINUE      FOUND            (Manual
  REGISTER   SCANNING              REGISTER  Investigation)

  Final States:
    SUCCESS: Pool registered, WebSocket active, pricing live
    FAILURE: No pool found via all methods, manual action needed
```

---

## Account Normalization Flow

```
RPC Provider Response
│
├─ Format Type 1: String
│  "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"
│  │
│  v
│  _normalize_account_key(str)
│  → return str as-is ✅
│
├─ Format Type 2: Dict with "pubkey"
│  {"pubkey": "pAMMBay6...", "signer": false, "writable": true}
│  │
│  v
│  _normalize_account_key(dict)
│  → return dict["pubkey"] ✅
│
└─ Format Type 3: Dict with "address"
   {"address": "pAMMBay6...", "executable": false}
   │
   v
   _normalize_account_key(dict)
   → return dict["address"] ✅

Result: All normalized to string pubkey
        Ready for getAccountInfo() RPC call
```

---

## Performance Profile

```
Operation                    RPC Calls    Time Est.    Cached?
──────────────────────────────────────────────────────────────
getTransaction               1            50ms         Yes (TX_CACHE)
getAccountInfo (per account) 25           ~500ms       Yes (pool_detector cache)
getTokenLargestAccounts      1            50ms         No (fallback only)
getTokenAccountBalance       2            100ms        Yes (price_worker cache)
──────────────────────────────────────────────────────────────
SUCCESS PATH TOTAL:                       ~650ms
FALLBACK PATH TOTAL:                      ~700ms

Caching Strategy:
- TX Cache: 30 min TTL, prevents duplicate RPC calls
- Account Info Cache: Per-detector lifecycle (session)
- Token Balance: price_worker caches for ~1 min
- Pool Definition: DB persists forever

Result: 2nd detection of same token → ~50ms (tx cache hit)
```

---

## Integration Points

```
┌─────────────────────────────────────────────────────────┐
│  pumpfun_curve_listener.py                              │
│  ┌───────────────────────────────────────────────────┐  │
│  │ WebSocket Event Handler                          │  │
│  │ - Detects migration                              │  │
│  │ - Fetches transaction                            │  │
│  │ - Calls: pool_detector.detect_pool_from_tx()     │  │
│  │ - If pool found: register + activate WebSocket   │  │
│  └──────────────────────┬────────────────────────────┘  │
└─────────────────────────┼────────────────────────────────┘
                          │
          Pool Detector:  │ ← detect_pool_from_tx(tx_data, mint)
          ┌───────────────┘
          │
          ├─ pool_detector.py
          │  ├─ Account normalization (Phase 1)
          │  ├─ Transaction shape logging (Phase 2)
          │  ├─ Account scanning + validation (Phases 3-4)
          │  └─ Fallback vault discovery (Phase 5)
          │
          └─ AMMPrograms class
             └─ Program registry + identification

          Output: pool_address OR None
                   │
                   ├─ If pool_address:
                   │  └─ Register in token_pool_accounts
                   │  └─ Activate WebSocket subscriptions
                   │  └─ Real-time price available via API
                   │
                   └─ If None:
                      └─ Log failure
                      └─ Try manual registration path
                      └─ Or external pricing fallback
```

---

## Debug Workflow Execution

```
User Action: POOL_DETECTOR_DEBUG=true python -m src.core.pumpfun_curve_listener
              │
              v
    Listener starts with debug=True
              │
              v
    Awaits WebSocket migration event
              │
              v
    Migration detected (real or injected)
              │
              v
    PoolDetector runs with debug logging enabled
              │
    ┌─────────┴──────────────────┐
    │                            │
    v                            v
  PRODUCTION LOGS           DEBUG LOGS
  (always on)               (POOL_DETECTOR_DEBUG=true)
  ├─ Migration detected      ├─ [POOL_DETECT_DEBUG] idx=0 ...
  ├─ tx_version=...          ├─ [POOL_DETECT_DEBUG] idx=1 ...
  ├─ base_keys=X             ├─ [POOL_DETECT_DEBUG] idx=2 ...
  ├─ Pool found OR           └─ [POOL_DETECT_DEBUG] idx=N ...
  └─ Fallback attempted

  User checks logs:
  ├─ grep "[POOL_DETECT]" → Transaction shape visible
  ├─ grep "[POOL_DETECT_DEBUG]" → Per-account breakdown
  ├─ grep "[POOL_DETECT_FALLBACK]" → Fallback path details
  └─ Answers 7 questions from logs
```

---

## File Dependencies

```
pool_detector.py (Core)
├─ AMMPrograms (known programs)
├─ AMMDataLengths (validation thresholds)
├─ PoolDetector class
│  ├─ detect_pool_from_tx() [MAIN ENTRY]
│  ├─ _normalize_account_key() [NEW]
│  └─ _get_account_info_cached()
├─ PoolParser hierarchy
│  ├─ RaydiumAMMParser
│  ├─ OrcaWhirlpoolParser
│  └─ MeteoraParser
└─ Helper functions
   └─ _bytes_to_pubkey()
   └─ _fetch_token_account_info()

pumpfun_curve_listener.py (Integration)
├─ Instantiates: pool_detector = PoolDetector(..., debug=flag)
├─ Calls: await pool_detector.detect_pool_from_tx(tx, mint)
├─ Registers: pool in DB and WebSocket on success
└─ Logs: migration + detection status

price_api.py (Health endpoint)
├─ GET /api/price/health
└─ Returns: detection stats (optional Phase 7)
```

---

This visual reference should make the architecture and execution flow clear at a glance.

