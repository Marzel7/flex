# Coordinated Funder Analysis View

**Date**: 2026-02-13
**Status**: ✅ COMPLETE & DEPLOYED

## Overview

New modal view to display the results of coordinated funder analysis - identifying suspicious funding networks where multiple creators are funded by the same sources, indicating potential rug pull coordination.

## What It Shows

The **Coordinated Network** modal displays:

### 1. Network Risk Summary (3-Card View)
- **Network Risk Level** - CRITICAL/HIGH/MEDIUM/LOW with color coding
  - RED (#ef4444) for CRITICAL
  - ORANGE (#f97316) for HIGH
  - YELLOW (#fbbf24) for MEDIUM
  - GREEN (#4ade80) for LOW
- **Connected Creators Count** - How many creators share funders with this one
- **Shared Destinations Count** - How many wallet addresses are used by multiple creators

### 2. Connected Creators List
Shows creators that share funding sources with the analyzed creator:
- Creator address (first 16 chars)
- Risk level badge with color coding
- Rug probability percentage
- Scrollable list (up to 10 shown, with count of remaining)

### 3. Shared Destinations
Shows wallet addresses used as destinations by multiple creators:
- Full wallet address
- Numbered list format
- Scrollable (up to 20 shown, with count of remaining)

### 4. Analysis Timestamp
Shows when the analysis was performed

## How to Use

### From Token View
1. Open a token in the dashboard
2. Click **"Coordinated Network"** button (orange button next to "View Funding Patterns")
3. View the coordination analysis:
   - **If analyzed**: See network risk, connected creators, and shared destinations
   - **If not analyzed**: Message "Not yet analyzed. Run Coordinated Funder Analysis first."

### From Creator View
1. Click on a creator address
2. Click **"Coordinated Network"** button
3. View results same as above

## Technical Details

### API Endpoint
```
GET /api/coordinated-funder-analysis/<creator_address>
```

**Response (if analyzed)**:
```json
{
  "creator_address": "CfKCTNb8rekLn6BggAyBgFpfbRUidcy27aJQUjCYVnvX",
  "status": "analyzed",
  "network_size": 5,
  "network_risk_level": "CRITICAL",
  "connected_creators_count": 5,
  "shared_destinations_count": 12,
  "connected_creators": [
    {
      "creator_address": "other_creator_1",
      "risk_level": "CRITICAL",
      "rug_probability": 0.89,
      "market_cap_highest": 500000,
      "created_at": "2026-02-13T12:00:00Z"
    }
  ],
  "shared_destinations": [
    "wallet_address_1",
    "wallet_address_2"
  ],
  "detected_at": "2026-02-13T13:00:00Z",
  "updated_at": "2026-02-13T13:00:00Z"
}
```

**Response (if not yet analyzed)**:
```json
{
  "creator_address": "CfKCTNb8...",
  "status": "not_analyzed",
  "message": "Coordinated funder analysis not yet performed for this creator"
}
```
(Returns 404 HTTP status)

### Database Tables
- `creator_networks` - Stores coordinated funder analysis results
  - `creator_address` - The analyzed creator
  - `connected_creators` - JSON array of connected creators
  - `shared_destinations` - JSON array of shared wallet destinations
  - `network_size` - Number of creators in the network
  - `network_risk_level` - CRITICAL/HIGH/MEDIUM/LOW
  - `detected_at` - When analysis was first run
  - `updated_at` - When analysis was last updated

### JavaScript Functions
- `showCoordinatedFunderAnalysis(creatorAddress)` - Opens modal and fetches data
- `closeCoordinatedFunderAnalysis()` - Closes the modal

## Interpretation Guide

### What Does Each Metric Mean?

**Network Risk Level**
- **CRITICAL** (RED) - High probability of coordinated rug pull
  - Same funders funding multiple creators
  - Multiple creators with high rug scores
  - Shared destination wallets suggest coordination
- **HIGH** (ORANGE) - Suspicious funding pattern
  - Some shared funding sources
  - Some connected creators with rug indicators
- **MEDIUM** (YELLOW) - Moderate coordination detected
  - Multiple funders across creators
  - Lower individual rug probabilities
- **LOW** (GREEN) - Minimal coordination signals

**Connected Creators Count**
- Number of other creators funded by the same sources
- Higher count = greater coordination network
- 0 = No coordinated funding detected

**Shared Destinations Count**
- Wallet addresses that receive funds from multiple creators
- Indicates organized fund consolidation
- Higher count = more organized coordination

### Red Flags in Results

🚨 **Critical Signals**:
- Network Risk = CRITICAL
- 5+ connected creators
- 10+ shared destinations
- Connected creators with high rug probabilities
- All sharing the same small set of destination wallets

⚠️ **Warning Signs**:
- Network Risk = HIGH
- 3+ connected creators
- Same funder addresses appear repeatedly
- Destination wallets are all exchange addresses

## Limitations

- Analysis must be performed separately (by running Coordinated Funder Analysis script)
- Only shows results that exist in `creator_networks` table
- Shows up to 20 shared destinations and 10 connected creators (performance)
- Relies on funder extraction data being complete

## Future Enhancements

Potential improvements:
1. Add "Run Analysis" button if not yet analyzed
2. Show funding flow visualization
3. Add time-series view of when coordination happened
4. Export results as CSV/JSON
5. Compare networks across multiple creators
6. Alert system for highly coordinated networks

## Files Modified

- `main.py`
  - New API endpoint: `api_coordinated_funder_analysis()`
  - New HTML modal: `coordinatedFunderAnalysisModal`
  - New JS functions: `showCoordinatedFunderAnalysis()`, `closeCoordinatedFunderAnalysis()`
  - Added button in token metrics modal
  - Updated window click handlers and escape key handlers

## Status

✅ **COMPLETE & DEPLOYED**
- API endpoint implemented and working
- Modal UI fully styled and integrated
- Graceful handling of missing data
- Ready for production use

**Next Step**: Run coordinated funder analysis script to populate `creator_networks` table, then the view will display results automatically.
