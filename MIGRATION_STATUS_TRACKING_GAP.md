# Migration Status Tracking - Current Gap Analysis

## Current Data Structure

### Token A94G4PcndyU3ppqGwsii5xzpmkLZN8M1cuWAsTLZpump

#### In `curve_completions` table (PRE-MIGRATION)
```
mint: A94G4PcndyU3ppqGwsii5xzpmkLZN8M1cuWAsTLZpump
detected_at: 1767958425.46749 (Unix timestamp)
market_cap_usd: 59944.0
signature: 2nUe7UmJKwUYG6Fgs3VkmZ43vEsbYQ1HhaoDUaYh9kQWuo18...
created_at: 2026-01-09 11:33:45
```

#### In `token_analysis` table (RISK METRICS)
```
mint: A94G4PcndyU3ppqGwsii5xzpmkLZN8M1cuWAsTLZpump
analyzed_at: 1767958488.67898
amm_rug_probability: 0.772 (77.2%)
amm_risk_level: 🔴 HIGH RISK
... (12 other metrics)
```

## The Problem

**There is NO field tracking whether a token has migrated or not.**

When we detect a migration on PumpSwap, we have:
- Migration transaction signature
- Transaction logs
- Timestamp of migration

But we have **NO WAY** to:
1. Mark that a token HAS migrated
2. Link the migration signature to the original pump.fun detection
3. Track the migration timeline (when it moved from bonding curve to AMM)
4. Know which tokens are still on bonding curve vs already migrated
5. Calculate time-to-migration metrics

## Proposed Solution

Add migration tracking columns to `token_analysis` table:

```sql
ALTER TABLE token_analysis ADD COLUMN migrated_at REAL DEFAULT NULL;
ALTER TABLE token_analysis ADD COLUMN migration_signature TEXT DEFAULT NULL;
ALTER TABLE token_analysis ADD COLUMN migration_transaction_index INTEGER DEFAULT NULL;
ALTER TABLE token_analysis ADD COLUMN migration_detected_at REAL DEFAULT NULL;
ALTER TABLE token_analysis ADD COLUMN has_migrated BOOLEAN DEFAULT 0;
ALTER TABLE token_analysis ADD COLUMN time_to_migration_seconds INTEGER DEFAULT NULL;
ALTER TABLE token_analysis ADD COLUMN pumpswap_pool_address TEXT DEFAULT NULL;
```

## New Columns Explained

| Column | Type | Purpose | Example |
|--------|------|---------|---------|
| `has_migrated` | BOOLEAN | Flag: token migrated? | 0 or 1 |
| `migrated_at` | REAL | Unix timestamp of migration | 1767959000.5 |
| `migration_signature` | TEXT | PumpSwap migration tx sig | `5JzSd9nM3pQ4rKwL8...` |
| `migration_detected_at` | REAL | When we detected migration | 1767959005.2 |
| `time_to_migration_seconds` | INTEGER | Seconds from detect to migrate | 580 (9.6 minutes) |
| `pumpswap_pool_address` | TEXT | Pool address on PumpSwap | `J7u2pW5kX...` |
| `migration_transaction_index` | INTEGER | Which migration tx #? | 1, 2, 3... |

## Data Flow: Current vs Enhanced

### CURRENT (Incomplete)

```
PHASE 1: TOKEN DETECTED
├─ Stored in: curve_completions
├─ Timestamp: detected_at = 1767958425
├─ Market Cap: $59,944
└─ Status: ❓ (unknown if migrated)

PHASE 2: ANALYZED
├─ Stored in: token_analysis
├─ Rug Probability: 77.2%
├─ Risk Level: HIGH
└─ Status: ❓ (still unknown)

PHASE 4: MIGRATION DETECTED
├─ Signature: 5JzSd9nM3pQ4rKwL8...
├─ Timestamp: 1767959000
├─ Logs: Contains "Instruction: Migrate"
└─ Status: ⚠️ (we know it migrated, but not recorded!)

PROBLEM: No connection between Phase 1 & Phase 4 data!
```

### ENHANCED (Complete)

```
PHASE 1: TOKEN DETECTED
├─ Stored in: curve_completions
├─ Timestamp: detected_at = 1767958425
├─ Market Cap: $59,944
└─ Status: has_migrated = 0 ✓

PHASE 2: ANALYZED
├─ Stored in: token_analysis
├─ Rug Probability: 77.2%
├─ Risk Level: HIGH
├─ has_migrated: 0
└─ Status: Tracked ✓

PHASE 4: MIGRATION DETECTED
├─ Signature: 5JzSd9nM3pQ4rKwL8...
├─ Timestamp: 1767959000
├─ Logs: Contains "Instruction: Migrate"
└─ UPDATE token_analysis SET:
    - has_migrated = 1 ✓
    - migrated_at = 1767959000 ✓
    - migration_signature = '5JzSd9nM3pQ4rKwL8...' ✓
    - time_to_migration_seconds = 575 ✓

RESULT: Complete migration lifecycle tracked! ✓
```

## Questions This Enables

With migration tracking, we can answer:

1. **How many analyzed tokens have migrated?**
   ```sql
   SELECT COUNT(*) FROM token_analysis WHERE has_migrated = 1;
   ```

2. **Which tokens migrated?**
   ```sql
   SELECT mint, amm_rug_probability, migrated_at
   FROM token_analysis
   WHERE has_migrated = 1
   ORDER BY migrated_at DESC;
   ```

3. **How accurate was our analysis?**
   ```sql
   SELECT
     COUNT(*) as total,
     SUM(CASE WHEN has_migrated = 1 THEN 1 ELSE 0 END) as migrated_count,
     ROUND(100.0 * SUM(CASE WHEN has_migrated = 1 THEN 1 ELSE 0 END) / COUNT(*), 1) as migration_rate
   FROM token_analysis;
   ```

4. **Average time from detection to migration?**
   ```sql
   SELECT
     AVG(time_to_migration_seconds) as avg_seconds,
     AVG(time_to_migration_seconds) / 60 as avg_minutes,
     MIN(time_to_migration_seconds) as fastest_seconds,
     MAX(time_to_migration_seconds) as slowest_seconds
   FROM token_analysis
   WHERE has_migrated = 1;
   ```

5. **Did high-risk tokens actually get rugged?**
   ```sql
   SELECT
     amm_risk_level,
     COUNT(*) as total,
     SUM(CASE WHEN has_migrated = 1 THEN 1 ELSE 0 END) as migrated
   FROM token_analysis
   GROUP BY amm_risk_level;
   ```

6. **Compare prediction vs reality**
   ```sql
   SELECT
     mint,
     amm_rug_probability,
     amm_risk_level,
     has_migrated,
     migration_signature,
     CASE
       WHEN amm_rug_probability > 0.75 AND has_migrated = 1 THEN 'Prediction Correct'
       WHEN amm_rug_probability <= 0.25 AND has_migrated = 0 THEN 'Safe as predicted'
       ELSE 'Unexpected outcome'
     END as prediction_accuracy
   FROM token_analysis
   ORDER BY migrated_at DESC;
   ```

## Implementation Steps

### 1. Add Columns to Existing Database
```sql
ALTER TABLE token_analysis ADD COLUMN has_migrated BOOLEAN DEFAULT 0;
ALTER TABLE token_analysis ADD COLUMN migrated_at REAL DEFAULT NULL;
ALTER TABLE token_analysis ADD COLUMN migration_signature TEXT DEFAULT NULL;
ALTER TABLE token_analysis ADD COLUMN migration_detected_at REAL DEFAULT NULL;
ALTER TABLE token_analysis ADD COLUMN time_to_migration_seconds INTEGER DEFAULT NULL;
ALTER TABLE token_analysis ADD COLUMN pumpswap_pool_address TEXT DEFAULT NULL;
```

### 2. Update Code in `test_complete_workflow.py`

In the `on_token_migrated()` callback:
```python
async def on_token_migrated(self, signature: str, logs: list) -> None:
    """Callback when migration detected - UPDATE database"""

    # Extract token mint from signature or logs
    token_mint = extract_mint_from_migration(signature, logs)

    if token_mint:
        # Query original detection timestamp
        cursor = sqlite3.connect(DB_PATH).cursor()
        cursor.execute(
            "SELECT analyzed_at FROM token_analysis WHERE mint = ?",
            (token_mint,)
        )
        result = cursor.fetchone()

        if result:
            analyzed_at = result[0]
            now = time.time()
            time_to_migration = int(now - analyzed_at)

            # UPDATE: Mark token as migrated
            cursor.execute("""
                UPDATE token_analysis SET
                    has_migrated = 1,
                    migrated_at = ?,
                    migration_signature = ?,
                    migration_detected_at = ?,
                    time_to_migration_seconds = ?
                WHERE mint = ?
            """, (now, signature, now, time_to_migration, token_mint))

            cursor.connection.commit()

            print(f"[DB] Updated migration status for {token_mint[:30]}...")
            print(f"[DB] Time to migration: {time_to_migration} seconds ({time_to_migration/60:.1f} minutes)")
```

### 3. Update Phase 4 Display

Show migration timeline:
```python
def check_pumpswap_migrations(self):
    """Display migrations with timeline"""

    for migration in self.migration_queue[:5]:
        # Query updated database
        cursor = sqlite3.connect(DB_PATH).cursor()
        cursor.execute("""
            SELECT
                mint, amm_rug_probability, amm_risk_level,
                analyzed_at, migrated_at, time_to_migration_seconds
            FROM token_analysis
            WHERE has_migrated = 1
            ORDER BY migrated_at DESC
            LIMIT 5
        """)

        for row in cursor.fetchall():
            mint, rug_prob, risk, analyzed_at, migrated_at, time_to_mig = row

            analysis_time = datetime.fromtimestamp(analyzed_at).strftime('%H:%M:%S')
            migration_time = datetime.fromtimestamp(migrated_at).strftime('%H:%M:%S')

            print(f"Token: {mint[:30]}...")
            print(f"  Analysis: {analysis_time}")
            print(f"  Migration: {migration_time}")
            print(f"  Time elapsed: {time_to_mig} seconds ({time_to_mig/60:.1f} minutes)")
            print(f"  Rug Probability: {rug_prob:.1%}")
            print(f"  Prediction: {risk}")
```

## Current State vs Enhanced State

### Current (A94G4PcndyU3ppqGwsii5xzpmkLZN8M1cuWAsTLZpump)

If this token migrated tomorrow, we would:
- ✅ Detect the migration via WebSocket
- ✅ Get the transaction signature
- ✅ Know the timestamp
- ❌ Have NO way to mark it as migrated in our database
- ❌ Have NO way to know which pre-migration analysis applies
- ❌ Have NO way to track the migration timeline
- ❌ Have NO way to validate our predictions

### Enhanced (With Migration Tracking)

If this token migrates, we would:
- ✅ Detect the migration via WebSocket
- ✅ Get the transaction signature
- ✅ **UPDATE database: has_migrated = 1**
- ✅ **Record: migrated_at timestamp**
- ✅ **Record: migration_signature**
- ✅ **Calculate: time_to_migration_seconds**
- ✅ **Link to pre-migration analysis automatically**
- ✅ **Track complete lifecycle**
- ✅ **Enable prediction validation**

## Summary

| Aspect | Current | Enhanced |
|--------|---------|----------|
| Pre-migration detection | ✅ | ✅ |
| Risk analysis | ✅ | ✅ |
| Migration detection | ✅ | ✅ |
| Track if migrated | ❌ | ✅ |
| Link migration to analysis | ❌ | ✅ |
| Timeline tracking | ❌ | ✅ |
| Prediction validation | ❌ | ✅ |
| Outcome analysis | ❌ | ✅ |

**Status**: GAP IDENTIFIED
- Current system: "We detect migrations but don't record them"
- Enhanced system: "Complete lifecycle tracking with validation"

This enhancement would transform the system from **detection-only** to **complete lifecycle tracking**.

---

## Recommended Next Steps

1. Add migration tracking columns to database
2. Update `on_token_migrated()` callback to record migration data
3. Add token extraction logic from migration transactions
4. Update Phase 4 display to show migration timeline
5. Add summary statistics for prediction accuracy
6. Track which predictions were correct vs incorrect

This would enable:
- **Data validation**: Compare predictions to reality
- **Model improvement**: Use historical outcomes to refine analysis
- **Performance tracking**: See which metrics are most predictive
- **Complete audit trail**: Full token lifecycle from creation to rug/success
