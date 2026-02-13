# Funder Extraction - Quick Start Guide

## What Is It?

The funder extraction system analyzes incoming/outgoing SOL transfers for wallet addresses that fund token creators. It creates a 3-tier visibility:

```
Sender → Funder → Creator
 (wallets)   (router)  (launcher)
```

---

## Getting Started

### 1. System is Ready Out of the Box
✓ Extraction is **disabled by default** (optimal for speed)
✓ Tokens display **immediately** (~3-5 seconds)
✓ Analysis **available on-demand**

### 2. Analyze a Specific Funder (Recommended)

**Via UI**:
1. View token in main interface
2. Click **"Coordinated Funders"** button
3. Click **"Analyze"** on any funder
4. Wait 10-15 seconds for results
5. See: "Done: X IN / Y OUT | Total SOL: Z"

**Via CLI**:
```bash
python3 funder_incoming_extractor.py <creator_address>
```

### 3. Enable Global Extraction (Optional)

If you want automatic extraction for ALL new tokens:

```bash
# Toggle ON in UI
curl -X POST http://localhost:5002/api/funder-extraction-control \
  -H "Content-Type: application/json" \
  -d '{"action":"toggle"}'

# Or click "Funder Extraction OFF" button → becomes "ON"
```

**Warning**: Global extraction adds 10-15 seconds to each token detection

---

## What Gets Extracted

For each funder, the system finds:

**Incoming** (Who funded the funder):
- Sender address
- Amount of SOL sent
- Transaction signature
- Account classification (CEX, INFRA, unknown)

**Outgoing** (Where the funder sent money):
- Recipient address
- Amount of SOL sent
- Transaction signature
- Account classification

**Example**:
```
Funder: ewVco7VvpJuUZ8oovL1Cz3Xj7TiaGPC9M31Z9ywR4ES
├─ Incoming: 7 senders → 281.58 SOL total
│  ├─ 75.00 SOL from 9obNtb5GyUegcs3a
│  ├─ 27.31 SOL from GJRs4FwHtemZ5ZE9
│  └─ ... (5 more senders)
└─ Outgoing: 22 recipients ← 170.16 SOL total
```

---

## Database

**Tables**:
- `funder_incoming_transfers` - Who funded the funders
- `funder_outgoing_transfers` - Where funders sent money

**Current Data**:
- 428 incoming transfer records
- 273 outgoing transfer records
- 701 total transfers extracted

---

## Performance

| Action | Time | Blocking |
|--------|------|----------|
| **New token detection** | 3-5 sec | No |
| **Funder analysis** | 10-15 sec | No (background) |
| **UI response** | Instant | Yes (fast) |

---

## API Endpoints

### Check Extraction Status
```bash
curl http://localhost:5002/api/funder-extraction-control
# Returns: {"extraction_enabled": false, "status": "disabled"}
```

### Toggle Extraction
```bash
curl -X POST http://localhost:5002/api/funder-extraction-control \
  -H "Content-Type: application/json" \
  -d '{"action":"toggle"}'
```

### Analyze Specific Funder
```bash
curl -X POST http://localhost:5002/api/analyze-funder-transfers \
  -H "Content-Type: application/json" \
  -d '{"funder_address":"ewVco7VvpJuUZ8oovL1Cz3Xj7TiaGPC9M31Z9ywR4ES"}'
```

---

## Configuration

### Helius API (Required for Speed)

Set environment variable:
```bash
export HELIUS_API_KEY=<your-api-key>
```

Or in `.env` file:
```
HELIUS_API_KEY=3b2917b8-9bed-4e2e-8c05-a74adbc34bb8
```

### Extraction Toggle

**Database**:
```sql
SELECT * FROM polling_settings
WHERE setting_name = 'funder_extraction_enabled';
```

**Values**:
- `'0'` = Disabled (default, fast)
- `'1'` = Enabled (slower token detection)

---

## Common Tasks

### Find Funding Sources for a Creator

1. **Get creator address** from token details
2. **View token in UI**
3. **Click "Coordinated Funders" modal**
4. **See all funders** with amounts
5. **Click "Analyze"** on funders of interest
6. **Wait for results** (10-15 seconds)
7. **View incoming/outgoing transfers**

### Batch Analysis (Multiple Creators)

```bash
# Enable extraction for batch processing
curl -X POST http://localhost:5002/api/funder-extraction-control \
  -H "Content-Type: application/json" \
  -d '{"action":"enable"}'

# New tokens will auto-extract
# Monitor listener logs: [FUNDER_EXTRACTION] ...

# When done, disable it again
curl -X POST http://localhost:5002/api/funder-extraction-control \
  -H "Content-Type: application/json" \
  -d '{"action":"disable"}'
```

### Extract Offline (No UI)

```bash
# Single creator
python3 funder_incoming_extractor.py <creator_address>

# Output:
# [COMPLETE] Extraction complete for creator_address
#   Total incoming transfers: 135
#   Total outgoing transfers: 153
#   Total SOL traced: 491.30
```

---

## Troubleshooting

### "No tracked sources" in UI
- Funder may have no pre-migration funding
- Click "Analyze" to force extraction
- Check logs for `[FUNDER_EXTRACTION]` messages

### Extraction is slow
- Helius API key missing? Check `.env` file
- Using public RPC fallback (slower)
- Set `HELIUS_API_KEY` environment variable

### Token not appearing in UI
- Extraction may be enabled (taking 10-15s)
- Or listener may not be running
- Check: `curl http://localhost:5002/`

### Too many API calls
- Disable extraction: Click "Funder Extraction ON" → "OFF"
- Or use on-demand analysis instead

---

## System Flow

### Data Extraction
```
Creator → Funder (detected)
          → Incoming Transfers (Sender → Funder)
          → Outgoing Transfers (Funder → Recipient)
          → Classify Accounts (CEX/INFRA/Unknown)
          → Save to Database
```

### Real-Time Detection
```
New Token Launch
  → 1s: Extract Creator
  → 2-3s: Get Creator Funding
  → 3-5s: Display in UI ✓
  → (Optional) Background: Extract Funder Transfers
```

---

## Next Steps

1. **Use On-Demand Analysis** (recommended)
   - View tokens immediately
   - Analyze specific funders when interested
   - Fast and efficient

2. **Monitor Suspicious Patterns**
   - Look for 1 funder → many creators (coordination)
   - Look for CEX funders (exchange wallet involvement)
   - Track funder behaviors over time

3. **Enable Extraction for Batches**
   - When analyzing large sets of tokens
   - Disable when done to save resources
   - Use CLI for offline analysis

---

## Support

**Extraction not working?**
- Check Helius API key
- Verify database: `sqlite3 pumpswap_tokens.db ".tables"`
- View logs: `tail -f /tmp/flask.log`

**Extraction too slow?**
- Disable global extraction
- Use on-demand analysis instead
- Check network/API connectivity

---

**Last Updated**: 2026-02-13
**Status**: ✅ Production Ready
**Performance**: 3-5 sec token display, 10-15 sec on-demand analysis

