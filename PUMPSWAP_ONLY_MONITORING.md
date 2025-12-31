# PumpSwap-Only Monitoring

**Important Clarification**: The system ONLY monitors the PumpSwap program, not Raydium V4 or Meteora.

---

## What This Means

The WebSocket listener is configured to **exclusively monitor**:
- **PumpSwap Program**: `pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA`

This means:
- ✅ All PumpSwap token migrations from Pump.fun → PumpSwap are detected
- ❌ Raydium V4 pool creations are NOT monitored
- ❌ Meteora DLMM pool creations are NOT monitored
- ❌ Other DEX launches are NOT monitored

---

## Code Configuration

### WebSocket Subscription (main.py, line 2143)

```python
# Subscribe to PumpSwap program for token migrations from Pump.fun bonding curve
# PumpSwap: pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA
await self.subscribe_to_program(ws, self.PUMPSWAP_PROGRAM)
```

This subscription call connects the WebSocket to **only** the PumpSwap program. No other programs are subscribed to.

### Program Constant (main.py, line 602)

```python
PUMPSWAP_PROGRAM = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"  # PumpSwap/Pump.fun AMM
```

### DEX Detection (main.py, line 2166)

```python
# Since we're only subscribed to PumpSwap program, all events are PumpSwap
dex_source = "PumpSwap"
```

Since we only subscribe to one program, we can definitively set `dex_source = "PumpSwap"` for every transaction received.

---

## Why PumpSwap Only?

### Reason 1: Focused Monitoring
The original requirement was to detect **PumpSwap tokens specifically** - tokens that migrate from Pump.fun bonding curve to the PumpSwap AMM. This is a specific use case that only requires monitoring the PumpSwap program.

### Reason 2: Cleaner Architecture
By listening to a single program:
- No need to differentiate between multiple DEX types
- Simpler detection logic
- Guaranteed accuracy (anything from PumpSwap program IS PumpSwap)
- No false positives from other DEX types

### Reason 3: Resource Efficiency
Listening to only PumpSwap program means:
- Less WebSocket traffic
- Fewer transactions to process
- Faster event processing
- Lower RPC bandwidth usage

---

## What Gets Detected

### ✅ Will Be Detected

1. **PumpSwap Pool Creation**
   - Pools created in the PumpSwap program (pAMMBay6...)
   - Tokens migrating from Pump.fun bonding curve
   - Price extracted from transaction post-balances
   - Metadata extracted and stored

2. **Example Events**
   ```
   [WEBSOCKET] Received PumpSwap transaction: 5xYz9ABC...
   New PumpSwap pool launch: 5xYz9ABC...
   Token Address: EPjFWaLb3od...
   Token Symbol: PUMP

   [PUMPSWAP] 🚀 DETECTED: Token migrated from Pump.fun bonding curve → PumpSwap!
   [PUMPSWAP PRICE] ✓ Calculated price: 80000.0400000000 SOL per token
   [PRICE INIT] ✓ Initial price set: $0.0000637600
   ```

### ❌ Will NOT Be Detected

1. **Raydium V4 Pools**
   - Program: `675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8`
   - Status: NOT subscribed, so no events received
   - Not monitored

2. **Raydium CPMM Pools**
   - Program: `CPMDWBwJoYu2j6qqcS2wK9FqoZcXXdkL3gMt3Sujy5z5`
   - Status: NOT subscribed, so no events received
   - Not monitored

3. **Meteora DLMM Pools**
   - Program: Various addresses
   - Status: NOT subscribed, so no events received
   - Not monitored

4. **Other DEX Launches**
   - Orca, Magic Eden, Phantom, etc.
   - Status: NOT subscribed
   - Not monitored

---

## If You Need Other DEX Monitoring

If you want to monitor **multiple DEX types** (Raydium, Meteora, etc.):

1. **Current Approach**: This system is PumpSwap-only
2. **To Add Others**: Would need to:
   - Subscribe to additional program IDs
   - Implement multi-DEX detection logic
   - Update `get_dex_source()` to identify each DEX
   - Handle DEX-specific price extraction methods

This would require architectural changes beyond the current PumpSwap-focused implementation.

---

## Console Output Examples

### What You WILL See

```
[WEBSOCKET] Received PumpSwap transaction: 5xYz9ABC...
[PUMPSWAP] 🚀 DETECTED: Token migrated from Pump.fun bonding curve → PumpSwap!
[PUMPSWAP PRICE] ✓ Calculated price: 80000.0400000000 SOL per token
[PRICE INIT] ✓ Initial price set: $0.0000637600
[BROADCAST] 🚀 PUMPSWAP TOKEN DETECTED: PumpSwap Token (PUMP)
```

### What You WON'T See

```
# Raydium V4 launches - NO events (not subscribed)
# Meteora launches - NO events (not subscribed)
# Orca launches - NO events (not subscribed)
# Other DEX launches - NO events (not subscribed)
```

---

## Verification

To confirm the system is monitoring only PumpSwap:

1. **Check the subscription** (main.py, line 2143):
   ```python
   await self.subscribe_to_program(ws, self.PUMPSWAP_PROGRAM)
   # Only this program is subscribed to
   ```

2. **Check the DEX source** (main.py, line 2166):
   ```python
   dex_source = "PumpSwap"
   # Always set to PumpSwap since that's the only program
   ```

3. **Run the listener**:
   ```bash
   python test_pumpswap_listener.py
   # Will show: "Monitoring PumpSwap Program: pAMMBay6..."
   ```

---

## Summary

| Aspect | Status |
|--------|--------|
| **PumpSwap Program** | ✅ Monitored (pAMMBay6...) |
| **Raydium V4** | ❌ Not monitored |
| **Meteora DLMM** | ❌ Not monitored |
| **Other DEX** | ❌ Not monitored |
| **Focus** | 🎯 PumpSwap tokens only |
| **Why Single Program** | Focused monitoring, cleaner code, resource efficient |

---

## Related Documentation

- [SESSION_FIXES_INDEX.md](SESSION_FIXES_INDEX.md) - Overview of current session
- [PUMPSWAP_ARCHITECTURE.md](PUMPSWAP_ARCHITECTURE.md) - Architecture details
- [DEPLOYMENT_SUMMARY.md](DEPLOYMENT_SUMMARY.md) - Deployment status

---

**Status**: This is the intended behavior. The system is designed to monitor **only PumpSwap** tokens migrating from Pump.fun bonding curve.
