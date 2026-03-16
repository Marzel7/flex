# Pool Detector Three-Stage Validation Architecture

**Date:** 2026-03-14
**Status:** Design & Implementation Plan
**Problem:** Current detector returns helper PDAs instead of real pool state accounts

---

## Executive Summary

Current logs show:
```
[POOL_DETECT] AMM-owned account ... data_len=2 (expected >= 296)
```

This indicates the detector is finding **PumpSwap helper PDAs** (2-byte state markers), not real pool state accounts.

**Root Cause:** Single-stage validation (ownership only) is insufficient. AMM programs own multiple account types.

**Solution:** Implement three-stage verification:
1. **Owner Filter** — account owner is known AMM program
2. **Structural Filter** — data length >= minimum pool size
3. **Parser Verification** — account data parses as valid pool state

---

## Current Architecture (Single-Stage)

```
scan transaction accounts
  ↓
if owner in AMMPrograms.ALL
  AND data_len >= MIN
  ↓
return account_addr
```

**Problem:** Returns ANY AMM-owned account >= MIN size, including:
- Helper PDAs (state markers)
- Config accounts
- Authority PDAs
- Non-pool accounts

---

## New Architecture (Three-Stage)

### Stage 1: Collect Candidates

```
candidates = []

for account in all_accounts:
    info = getAccountInfo(account)

    if info.owner in AMMPrograms.ALL:
        if data_len >= MIN_POOL_SIZE:
            candidates.append({
                'address': account,
                'owner': info.owner,
                'data_len': data_len,
                'data': info.data
            })
```

**Output:** List of accounts that pass ownership + size filters

---

### Stage 2: Parse Validation

```
parser_hits = {}
parser_misses = []

for candidate in candidates:
    parser = PoolParserDispatcher.for_owner(candidate['owner'])

    try:
        pool = parser.try_parse(candidate['data'])

        if pool is valid:
            parser_hits[candidate['address']] = pool
        else:
            parser_misses.append(candidate)

    except ParseError:
        parser_misses.append(candidate)

if parser_hits:
    return first(parser_hits)  # or best(parser_hits)
```

**Output:** Only accounts that parse as valid pool state

---

### Stage 3: Fallback Vault Discovery (Improved)

When primary detection fails:

```
vault_account
  ↓
parse as token account
  ↓
extract authority
  ↓
getAccountInfo(authority)
  ↓
if owner in AMMPrograms.ALL
  AND parser validates
  ↓
return pool_address
```

---

## Implementation Details

### 1. Candidate Collection Phase

In `detect_pool_from_tx()`, after scanning all accounts:

```python
# Collect candidates instead of returning immediately
candidates = []
candidate_summary = {
    'pumpswap_helpers': 0,
    'pumpswap_valid': 0,
    'raydium_amm': 0,
    'raydium_clmm': 0,
    'orca': 0,
    'meteora': 0,
}

for i, account_addr in enumerate(all_accounts):
    try:
        account_info = await self._get_account_info_cached(account_addr)

        if not account_info:
            continue

        owner = account_info.get("owner", "")
        data_len = len(account_info.get("data", []))

        # Per-account debug logging
        if self.debug:
            logger.debug(
                f"[POOL_DETECT_DEBUG] idx={i} addr={account_addr[:16]}... "
                f"owner={owner[:16]}... data_len={data_len}"
            )

        # STAGE 1: Owner filter
        if owner not in AMMPrograms.ALL:
            continue

        program_name = AMMPrograms.identify_program(owner)
        min_len = AMMDataLengths.EXPECTED.get(owner, 200)

        # STAGE 2: Structural filter (data length)
        if data_len < min_len:
            # Special handling for extremely small accounts
            if data_len < 32:
                candidate_summary['pumpswap_helpers'] += 1
                logger.debug(
                    f"[POOL_DETECT] Rejected PumpSwap helper PDA "
                    f"{account_addr[:16]}... data_len={data_len}"
                )
            else:
                logger.debug(
                    f"[POOL_DETECT] Candidate {program_name} account "
                    f"{account_addr[:16]}... data_len={data_len} "
                    f"below minimum {min_len}"
                )
            continue

        # Candidate passed owner + size filters
        candidates.append({
            'address': account_addr,
            'owner': owner,
            'program': program_name,
            'data': account_info.get("data", []),
            'data_len': data_len,
            'idx': i
        })

        candidate_summary[program_name] = candidate_summary.get(program_name, 0) + 1

    except Exception as e:
        logger.debug(f"[POOL_DETECT] Error checking account {i}: {e}")
        continue

# Log candidate summary
logger.info(
    f"[POOL_DETECT] Candidate summary: "
    f"pumpswap_helpers={candidate_summary['pumpswap_helpers']} "
    f"pumpswap_valid={candidate_summary['pumpswap_valid']} "
    f"raydium_amm={candidate_summary['raydium_amm']} "
    f"raydium_clmm={candidate_summary['raydium_clmm']} "
    f"orca={candidate_summary['orca']} "
    f"meteora={candidate_summary['meteora']}"
)

if not candidates:
    logger.warning(
        f"[POOL_DETECT] No candidates passed filters (owner + size). "
        f"Trying fallback discovery..."
    )
```

---

### 2. Parser Validation Phase

```python
# STAGE 3: Parser validation
for candidate in candidates:
    try:
        parser = PoolParserDispatcher.for_program(candidate['owner'])

        if parser is None:
            logger.debug(
                f"[POOL_DETECT] No parser for program {candidate['program']}, "
                f"skipping {candidate['address'][:16]}..."
            )
            continue

        pool_state = parser.try_parse(candidate['data'])

        if pool_state:
            logger.info(
                f"[POOL_DETECT] ✅ Pool validated via {candidate['program']} parser: "
                f"{candidate['address'][:16]}... "
                f"(data_len={candidate['data_len']}, idx={candidate['idx']})"
            )
            return candidate['address']
        else:
            logger.debug(
                f"[POOL_DETECT] Parser rejected {candidate['program']} candidate "
                f"{candidate['address'][:16]}... (invalid structure)"
            )

    except Exception as e:
        logger.debug(
            f"[POOL_DETECT] Parser error for {candidate['program']} candidate "
            f"{candidate['address'][:16]}...: {e}"
        )
        continue
```

---

### 3. Improved Fallback Discovery

```python
async def _discover_pool_via_vaults_improved(self, token_mint: str) -> Optional[str]:
    """
    Improved fallback pool discovery via vault analysis.

    Flow:
    1. Get largest token accounts (likely pool vaults)
    2. Parse as token accounts
    3. Extract authority
    4. Get authority owner
    5. Validate with parser
    """
    try:
        logger.info(f"[POOL_DETECT_FALLBACK] Starting improved vault-based discovery")

        # Fetch largest token accounts
        import aiohttp
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTokenLargestAccounts",
            "params": [token_mint]
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(
                self.rpc_url,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status != 200:
                    return None

                result = await resp.json()
                if "result" not in result or not result["result"]["value"]:
                    return None

                accounts = result["result"]["value"]

        # Inspect each vault
        for vault_account in accounts[:5]:  # Check top 5
            vault_addr = vault_account["address"]

            vault_info = await self._get_account_info_cached(vault_addr)
            if not vault_info:
                continue

            vault_owner = vault_info.get("owner")

            # Special case: if vault owner is System Program, it's a user account
            if vault_owner == "11111111111111111111111111111111":
                logger.debug(
                    f"[POOL_DETECT_FALLBACK] Vault {vault_addr[:16]}... "
                    f"owned by System Program (user account), skipping"
                )
                continue

            # Attempt to parse as token account and extract authority
            try:
                # Token account layout: 32 bytes = mint, then 32 bytes = owner/authority
                vault_data = vault_info.get("data", [])
                if len(vault_data) < 72:
                    continue

                # Extract authority (bytes 32-64)
                authority_bytes = vault_data[32:64]
                authority = self._bytes_to_base58(authority_bytes)

                if not authority:
                    continue

                logger.debug(
                    f"[POOL_DETECT_FALLBACK] Vault {vault_addr[:16]}... "
                    f"authority={authority[:16]}..."
                )

                # Get authority account info
                authority_info = await self._get_account_info_cached(authority)
                if not authority_info:
                    continue

                authority_owner = authority_info.get("owner")

                # VALIDATE: authority owner must be AMM program
                if authority_owner not in AMMPrograms.ALL:
                    logger.debug(
                        f"[POOL_DETECT_FALLBACK] Authority {authority[:16]}... "
                        f"not owned by AMM program (owner={authority_owner[:16]}...)"
                    )
                    continue

                # VALIDATE: parse authority as pool with appropriate parser
                parser = PoolParserDispatcher.for_program(authority_owner)
                if parser:
                    pool_state = parser.try_parse(authority_info.get("data", []))

                    if pool_state:
                        logger.info(
                            f"[POOL_DETECT_FALLBACK] ✅ Pool found via vault authority: "
                            f"{authority[:16]}..."
                        )
                        return authority

            except Exception as e:
                logger.debug(f"[POOL_DETECT_FALLBACK] Error processing vault: {e}")
                continue

        logger.warning(f"[POOL_DETECT_FALLBACK] Failed to resolve pool via vaults")
        return None

    except Exception as e:
        logger.debug(f"[POOL_DETECT_FALLBACK] Error in vault discovery: {e}")
        return None
```

---

## Required New Component: PoolParserDispatcher

```python
class PoolParserDispatcher:
    """Routes account data to appropriate pool parser based on program owner."""

    @staticmethod
    def for_program(program_owner: str):
        """Get parser for given program, or None if unsupported."""
        parser_map = {
            AMMPrograms.RAYDIUM_AMM: RaydiumAMMParser(),
            AMMPrograms.PUMPSWAP: RaydiumAMMParser(),  # PumpSwap uses Raydium layout
            AMMPrograms.RAYDIUM_CLMM: RaydiumCLMMParser(),
            AMMPrograms.ORCA_WHIRLPOOL: OrcaWhirlpoolParser(),
            AMMPrograms.METEORA_DLMM: MeteoraDLMMParser(),
        }
        return parser_map.get(program_owner)

class PoolParser:
    """Base class for pool parsers."""

    def try_parse(self, data: List[int]) -> Optional[Dict]:
        """
        Attempt to parse account data as pool state.

        Returns:
            Dict with pool metadata if valid, None if parsing fails
        """
        raise NotImplementedError

class RaydiumAMMParser(PoolParser):
    def try_parse(self, data: List[int]) -> Optional[Dict]:
        """
        Parse Raydium AMM pool state.

        Raydium AMM v4 layout (296+ bytes):
        - Offset 0-8: Status flags
        - Offset 8-40: OpenOrders (pubkey)
        - Offset 40-72: Owner (pubkey)
        - ... (reserves, etc.)

        Minimal validation: check for recognizable magic bytes or structure.
        """
        try:
            if len(data) < 296:
                return None

            # Check for valid structure markers
            # Raydium pools typically have specific patterns in first few bytes
            # This is a basic check; real implementation would verify more thoroughly

            return {
                'type': 'raydium_amm',
                'data_len': len(data),
                'valid': True
            }
        except:
            return None
```

---

## Logging Examples

### Before (Current - Opaque)
```
[POOL_DETECT] AMM-owned account ADyA8h... (owner=pumpswap) has invalid data_len=2 (expected >= 296)
[POOL_DETECT] AMM-owned account C2aFPd... (owner=pumpswap) has invalid data_len=2 (expected >= 296)
[POOL_DETECT] No AMM-owned pool found in transaction (38 base + 0 writable + 0 readonly)
[POOL_DETECT_FALLBACK] Failed to resolve pool via vaults
[POOL_DETECT] All pool discovery methods failed
```

### After (Clear Diagnostics)
```
[POOL_DETECT] tx_version=None base_keys=38 writable_loaded=0 readonly_loaded=0 total=38
[POOL_DETECT_DEBUG] idx=0 addr=1111... owner=1111... data_len=0
[POOL_DETECT_DEBUG] idx=1 addr=8D3c... owner=8D3c... data_len=65
[POOL_DETECT_DEBUG] idx=2 addr=pAMM... owner=pAMM... data_len=296
[POOL_DETECT] Candidate summary: pumpswap_helpers=2 pumpswap_valid=1 raydium_amm=0 orca=0 meteora=0
[POOL_DETECT] Validated via pumpswap parser: pAMM...[:16] (data_len=296, idx=2)
[POOL_DETECT] ✅ Pool validated: pAMM...
```

---

## Deployment Plan

### Phase 1: New Components (30 min)
- [ ] Create `PoolParserDispatcher` class
- [ ] Create `PoolParser` base class
- [ ] Implement `RaydiumAMMParser`

### Phase 2: Candidate Collection (20 min)
- [ ] Refactor `detect_pool_from_tx()` to collect candidates
- [ ] Add candidate summary logging
- [ ] Add helper PDA detection logging

### Phase 3: Parser Validation (20 min)
- [ ] Integrate `PoolParserDispatcher` into detection
- [ ] Add parser validation loop
- [ ] Add parser error handling

### Phase 4: Fallback Improvement (30 min)
- [ ] Refactor `_discover_pool_via_vaults()`
- [ ] Add authority extraction logic
- [ ] Add parser validation in fallback path

### Phase 5: Testing (30 min)
- [ ] Syntax check
- [ ] Test with next token launch
- [ ] Verify logs show candidate summary and parser validation

---

## Testing Strategy

### Scenario 1: Valid Pool in Transaction
```
Expected:
[POOL_DETECT] Candidate summary: pumpswap_valid=1
[POOL_DETECT] ✅ Pool validated via pumpswap parser
```

### Scenario 2: Helper PDAs Only
```
Expected:
[POOL_DETECT] Rejected PumpSwap helper PDA ... data_len=2
[POOL_DETECT] Candidate summary: pumpswap_helpers=2 pumpswap_valid=0
[POOL_DETECT_FALLBACK] Starting improved vault-based discovery
```

### Scenario 3: Pool in Fallback (Vault Authority)
```
Expected:
[POOL_DETECT_FALLBACK] Authority ... not owned by AMM program
[POOL_DETECT_FALLBACK] ✅ Pool found via vault authority
```

---

## Backwards Compatibility

✅ All changes are additive
✅ No breaking changes to public interfaces
✅ Return value remains `Optional[str]` (pool address or None)
✅ Debug flag behavior unchanged
✅ RPC call pattern unchanged (same or fewer calls)

---

## Success Metrics

| Metric | Before | After | Goal |
|--------|--------|-------|------|
| Helper PDA false positives | High | 0 | Eliminate |
| Invalid fallback addresses | Occurs | 0 | Eliminate |
| Parser validation rate | N/A | >95% | Reliable |
| Detection success rate | Low | >90% | Improve |

---

## Files to Modify

```
src/core/pool_detector.py        (+150 lines, 3-stage detection)
src/core/pool_parser_dispatcher.py (NEW, ~200 lines)
```

Optional improvements:
```
src/apis/price_api.py             (detection metrics)
```

---

## Risk Assessment

**Risk Level:** ✅ LOW

- Changes are additive (no destructive refactoring)
- Parser validation is defensive (fails safely to None)
- Fallback logic is improved, not removed
- Debug logging can be disabled
- Rollback is simple (revert one file)

---

## Expected Result

After implementation, every pool detection will have:

1. ✅ Candidate collection with summary
2. ✅ Parser validation stage
3. ✅ Clear logs distinguishing helpers from valid pools
4. ✅ Improved fallback with validator
5. ✅ Detection success rate >90%

