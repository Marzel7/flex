# Faded Runner Category Implementation

## Overview

Added a new `faded_runner` category to the token behaviour classification system to capture tokens that perform well initially but then decline materially without fully collapsing like a rug.

## Problem Solved

Previously, tokens that:
- Reached strong peaks (3x-50x+)
- Then declined significantly (50-85% from peak)
- But retained residual value (15-50% of peak)

...could be misclassified as:
- `choppy_runner` (which implies still structurally healthy)
- `rug` (which implies near-terminal collapse)
- `unknown` (ambiguous signals)

This loss of nuance made creator analysis noisier, as tokens with different lifecycles (winners that lost momentum vs. complete failures) were grouped together.

## Solution

### New Classification Rule

```python
FADED_RUNNER_MAX_RETURN_MIN = 3.0         # Multiple of initial price
FADED_RUNNER_DRAWDOWN_MIN = 0.50          # Lower bound: 50% drawdown
FADED_RUNNER_DRAWDOWN_MAX = 0.85          # Upper bound: 85% drawdown
FADED_RUNNER_RECOVERY_MIN = 0.15          # Lower bound: 15% recovery
FADED_RUNNER_RECOVERY_MAX = 0.50          # Upper bound: 50% recovery
```

**Classification condition:**
```
if (
    max_return_multiple >= 3.0
    AND 0.50 <= drawdown_from_peak <= 0.85
    AND 0.15 <= recovery_ratio <= 0.50
):
    return "faded_runner"
```

### Confidence Scoring

Confidence blends three factors:
- **Multiple quality (40%)**: How much above 3.0x threshold
- **Drawdown quality (30%)**: How clearly centered in 50-85% range
- **Recovery quality (30%)**: How clearly centered in 15-50% range

Result capped at 0.85 confidence since faded runners are inherently uncertain (unclear if momentum loss is temporary or terminal).

### Classification Priority Order

1. `immediate_rug` — fastest detection (no lifetime gate)
2. `runner` — strong performers still healthy
3. **`faded_runner`** — strong performers that lost momentum ← NEW
4. `choppy_runner` — volatile but still alive
5. `rug` — severe collapse (2x+ then 90%+ down)
6. `slow_rug` — weak/no upside with gradual bleed
7. `insufficient_history` — <8 snapshots
8. `unknown` — conflicting signals

## Implementation Details

### Files Modified

1. **src/core/token_behavior.py**
   - Added faded_runner constants (5 thresholds)
   - Added `_faded_runner_confidence(f)` function
   - Updated `classify_token()` to check faded_runner between runner and choppy_runner
   - Updated schema CHECK constraint to include 'faded_runner'
   - Updated module docstring

2. **src/core/flex_dashboard_routes.py**
   - Updated `/api/token-behaviour` endpoint documentation
   - Now accepts 'faded_runner' as valid category filter

3. **templates/flex_dashboard.html**
   - Added faded_runner to CATEGORY_INFO:
     * Icon: ⬇️🚀 (runner falling)
     * Color: #84cc16 (lime green - between runner green and decline orange)
     * Label: "Faded Runner"
     * Description: "Strong upside then material decline"
   - Added 'faded_runner' to categories array in data fetching
   - Updated about section with faded_runner definition and explanation

4. **Database Migration**
   - Migrated token_behavior table to include 'faded_runner' in CHECK constraint
   - Preserved all existing data (362 tokens)

### Example Tokens

Real tokens classified as faded_runner:

1. **5cDhM4yMKipQkjSGdvYnqPdiJz685Z96rbe6GSYppump**
   - Max return: 48.92x
   - Drawdown: 82%
   - Recovery: 18%
   - Confidence: 0.494 (strong signal)
   - Snapshots: 14,635 (full-tier data quality)

2. **2AUFGMvx1bJdh42yR8j7Kji39TCcfnf1bSGqHxc7pump**
   - Max return: 4.01x
   - Drawdown: 61%
   - Recovery: 39%
   - Confidence: 0.439 (solid signal)
   - Snapshots: 701 (mid-tier data quality)

## API Usage

### Get all faded_runner tokens

```bash
curl "http://localhost:5002/api/token-behaviour?category=faded_runner&limit=20"
```

### Get faded_runners with high confidence

```bash
curl "http://localhost:5002/api/token-behaviour?category=faded_runner&min_confidence=0.3&limit=20"
```

### Get summary including faded_runner distribution

```bash
curl "http://localhost:5002/api/token-behaviour/stats/summary"
```

Response includes:
```json
{
  "by_category": {
    "faded_runner": {
      "count": 4,
      "pct": 1.1,
      "avg_confidence": 0.319
    },
    ...
  },
  "total_classified": 358
}
```

## Quality Implications

### What Improves

1. **Creator Pattern Analysis**
   - Can now distinguish "creator launches consistent winners that fade" from "creator launches rugs"
   - Better identifies tokens losing momentum vs. completely failing

2. **Portfolio Tracking**
   - Investors can identify which of their tokens faded vs. which are true runners
   - Faded runners are often worth monitoring for potential recovery

3. **Risk Assessment**
   - Faded runner = high risk but not complete loss
   - Rug = total loss
   - Clear distinction improves risk modeling

4. **Dashboard Usefulness**
   - Token behaviour page now shows distinct category
   - Easier to spot and analyze tokens with specific lifecycle pattern

### Data Quality

- **Current faded_runner distribution**: 1.1% of 358 classified tokens
- **Avg confidence**: 0.319 (reasonable for this inherently uncertain category)
- **Snapshot distribution**: Mix of early (701 snapshots) and full-tier (14,635 snapshots) data

The lower percentage reflects the specificity of the thresholds - tokens that meet all three conditions (good upside, then 50-85% decline, with 15-50% recovery) are a subset of all interesting tokens.

## Testing

Verified classification logic:
- ✅ Tokens correctly matched to faded_runner rule
- ✅ Confidence calculation produces sensible values
- ✅ API returns faded_runner category correctly
- ✅ Stats endpoint includes faded_runner distribution
- ✅ Dashboard displays faded_runner with correct styling

## Future Tuning

The thresholds can be adjusted based on observed distribution and user feedback:

- **Widen drawdown range**: If too few tokens qualify, raise FADED_RUNNER_DRAWDOWN_MAX or lower MIN
- **Adjust recovery window**: If missing important tokens, expand RECOVERY_MIN/MAX
- **Confidence ceiling**: Currently 0.85 (inherent uncertainty). Can lower to 0.70 if want more conservative estimates
- **Multiple threshold**: Currently 3.0x. Could lower to 2.5x if want earlier detection

Recommendation: Monitor for 1-2 weeks, then adjust if distribution seems skewed.
