# Phase 4C Code Reference

**Files**: build_networks_release.py, main.py, PHASE4C_MONITORING_SCHEMA.sql

---

## 1. Alert Rules Logic

### Rule A: SCORE_SPIKE (Score increased by 20+)

```sql
WITH score_deltas AS (
  SELECT
    h.network_name,
    h.build_version,
    h.score as curr_score,
    (SELECT score FROM network_score_history p
     WHERE p.network_name = h.network_name
     AND p.build_version = h.build_version - 1) as prev_score
  FROM network_score_history h
  WHERE h.build_version = ?
)
SELECT
  sd.network_name,
  sd.build_version,
  'SCORE_SPIKE',
  CASE
    WHEN (sd.curr_score - COALESCE(sd.prev_score, 0)) >= 35 THEN 'high'
    ELSE 'medium'
  END as severity,
  'Score increased by ' || (sd.curr_score - COALESCE(sd.prev_score, 0)) ||
    ' points (from ' || COALESCE(sd.prev_score, 'N/A') || ' to ' || sd.curr_score || ')' as message,
  json_object(
    'prev_score', sd.prev_score,
    'curr_score', sd.curr_score,
    'delta', sd.curr_score - COALESCE(sd.prev_score, 0)
  ) as details_json
FROM score_deltas sd
WHERE COALESCE(sd.prev_score, 0) IS NOT NULL
  AND (sd.curr_score - COALESCE(sd.prev_score, 0)) >= 20;
```

**Idempotency**: UNIQUE(network_name, build_version, alert_type) prevents duplicates

---

### Rule B: NEW_HIGH_RISK (New network with score >= 70)

```sql
WITH new_high_risk AS (
  SELECT
    h.network_name,
    h.build_version,
    h.score as curr_score
  FROM network_score_history h
  WHERE h.build_version = ?
    AND h.score >= 70
    AND NOT EXISTS (
      SELECT 1 FROM network_score_history p
      WHERE p.network_name = h.network_name
      AND p.build_version = h.build_version - 1
    )
)
SELECT
  nhr.network_name,
  nhr.build_version,
  'NEW_HIGH_RISK' as alert_type,
  'high' as severity,
  'New network with high risk score: ' || nhr.curr_score || ' / 100' as message,
  json_object('score', nhr.curr_score) as details_json
FROM new_high_risk nhr;
```

**Detection**: Network doesn't exist in previous build_version + score >= 70

---

### Rule C: TYPE_FLIP (network_type changed)

```sql
WITH type_changes AS (
  SELECT
    nr.network_name,
    nr.build_version,
    nr.network_type as new_type,
    (SELECT network_type FROM networks_release p
     WHERE p.network_name = nr.network_name
     AND p.build_version = nr.build_version - 1) as old_type
  FROM networks_release nr
  WHERE nr.build_version = ?
)
SELECT
  tc.network_name,
  tc.build_version,
  'TYPE_FLIP' as alert_type,
  CASE
    WHEN tc.new_type = 'cex_and_infra_connected' THEN 'high'
    WHEN tc.new_type IN ('infra_connected', 'cex_connected') THEN 'medium'
    ELSE 'low'
  END as severity,
  'Network type changed from ' || COALESCE(tc.old_type, 'unknown') || ' to ' || tc.new_type as message,
  json_object(
    'old_type', tc.old_type,
    'new_type', tc.new_type
  ) as details_json
FROM type_changes tc
WHERE tc.old_type IS NOT NULL
  AND tc.old_type != tc.new_type;
```

**Severity Levels**:
- `high` → cex_and_infra_connected (most exposure)
- `medium` → infra_connected or cex_connected
- `low` → organic (least exposure)

---

### Rule D: LIFECYCLE_FLIP (stability_state changed + score >= 50)

```sql
WITH state_changes AS (
  SELECT
    nr.network_name,
    nr.build_version,
    nr.stability_state as new_state,
    ns.score,
    (SELECT stability_state FROM networks_release p
     WHERE p.network_name = nr.network_name
     AND p.build_version = nr.build_version - 1) as old_state
  FROM networks_release nr
  LEFT JOIN network_scores ns ON nr.network_name = ns.network_name
  WHERE nr.build_version = ?
)
SELECT
  sc.network_name,
  sc.build_version,
  'LIFECYCLE_FLIP' as alert_type,
  CASE
    WHEN sc.new_state = 'growing' THEN 'medium'
    ELSE 'low'
  END as severity,
  'Network lifecycle changed from ' || COALESCE(sc.old_state, 'unknown') || ' to ' || sc.new_state as message,
  json_object(
    'old_state', sc.old_state,
    'new_state', sc.new_state,
    'score', sc.score
  ) as details_json
FROM state_changes sc
WHERE sc.old_state IS NOT NULL
  AND sc.old_state != sc.new_state
  AND COALESCE(sc.score, 0) >= 50;
```

**Severity**: medium if growing, low otherwise

---

## 2. Query Helpers (main.py)

### get_latest_alerts(limit=100)

```python
def get_latest_alerts(limit: int = 100) -> list:
    """Get latest network alerts for monitoring dashboard."""
    try:
        conn, cursor = get_db_conn()
        cursor.execute('''
            SELECT
              network_name,
              alert_type,
              severity,
              message,
              details_json,
              created_at
            FROM network_alerts
            ORDER BY created_at DESC
            LIMIT ?
        ''', (limit,))

        alerts = []
        for row in cursor.fetchall():
            alerts.append({
                'network_name': row['network_name'],
                'alert_type': row['alert_type'],
                'severity': row['severity'],
                'message': row['message'],
                'details': json.loads(row['details_json']) if row['details_json'] else {},
                'created_at': row['created_at'],
            })

        conn.close()
        return alerts
    except Exception as e:
        print(f"[ERROR] get_latest_alerts: {e}")
        return []
```

**Returns**: List of alert dicts ordered by creation time (newest first)

---

### get_top_risky_networks(limit=50)

```python
def get_top_risky_networks(limit: int = 50) -> list:
    """Get current top risky networks by score."""
    try:
        conn, cursor = get_db_conn()
        cursor.execute('''
            SELECT
              network_name,
              score
            FROM network_scores
            ORDER BY score DESC
            LIMIT ?
        ''', (limit,))

        networks = []
        for row in cursor.fetchall():
            score = row['score']
            badge = 'high' if score >= 70 else ('medium' if score >= 30 else 'low')
            networks.append({
                'network_name': row['network_name'],
                'score': score,
                'score_badge': badge,
            })

        conn.close()
        return networks
    except Exception as e:
        print(f"[ERROR] get_top_risky_networks: {e}")
        return []
```

**Returns**: Top networks by current score with auto-computed badges

---

### get_biggest_score_movers(limit=50)

```python
def get_biggest_score_movers(limit: int = 50) -> list:
    """Get networks with biggest score changes in the last build."""
    try:
        conn, cursor = get_db_conn()
        cursor.execute('''
            SELECT
              h.network_name,
              (h.score - p.score) AS delta,
              p.score AS prev_score,
              h.score AS curr_score
            FROM network_score_history h
            JOIN network_score_history p
              ON p.network_name = h.network_name
              AND p.build_version = h.build_version - 1
            WHERE h.build_version = (SELECT MAX(build_version) FROM network_score_history)
            ORDER BY delta DESC
            LIMIT ?
        ''', (limit,))

        movers = []
        for row in cursor.fetchall():
            movers.append({
                'network_name': row['network_name'],
                'delta': row['delta'],
                'prev_score': row['prev_score'],
                'curr_score': row['curr_score'],
            })

        conn.close()
        return movers
    except Exception as e:
        print(f"[ERROR] get_biggest_score_movers: {e}")
        return []
```

**Returns**: Networks with largest score changes, ordered by delta DESC

---

## 3. Idempotency Pattern

### INSERT OR IGNORE + UNIQUE Constraint

**network_score_history**:
```python
# Primary key: (network_name, build_version)
db.execute('''
    INSERT OR IGNORE INTO network_score_history
    (network_name, build_version, score, score_version, components_json, computed_at)
    SELECT ... FROM networks_release ...
''')

# Behavior:
# - First run: All inserts succeed
# - Same build_version rerun: All inserts ignored (key exists)
# - New build_version: New records inserted
```

**network_alerts**:
```python
# UNIQUE constraint: (network_name, build_version, alert_type)
db.execute('''
    INSERT OR IGNORE INTO network_alerts
    (network_name, build_version, alert_type, severity, message, details_json)
    SELECT ... -- Alert rule logic
''')

# Behavior:
# - First run: Matching alerts inserted
# - Same build_version rerun: All duplicate inserts ignored
# - New build_version: New alerts inserted
```

---

## 4. Key Design Patterns

### Pattern 1: Deferred Lookups with Subqueries

Get previous build's score without separate query:

```sql
(SELECT score FROM network_score_history p
 WHERE p.network_name = h.network_name
 AND p.build_version = h.build_version - 1) as prev_score
```

Avoids N+1 problem, all in single SQL statement.

### Pattern 2: Conditional Severity

Map values to severity levels in SQL:

```sql
CASE
  WHEN (new_value - old_value) >= 35 THEN 'high'
  WHEN (new_value - old_value) >= 20 THEN 'medium'
  ELSE 'low'
END
```

No computation in application, purely declarative.

### Pattern 3: JSON Component Storage

Store arbitrary details in JSON for flexibility:

```sql
json_object(
  'prev_score', sd.prev_score,
  'curr_score', sd.curr_score,
  'delta', sd.curr_score - COALESCE(sd.prev_score, 0)
) as details_json
```

Parsed on read in Python: `json.loads(row['details_json'])`

---

## 5. Monitoring Query Examples

### Get all high-severity alerts in last 7 days

```sql
SELECT network_name, alert_type, message
FROM network_alerts
WHERE severity = 'high'
  AND created_at >= datetime('now', '-7 days')
ORDER BY created_at DESC;
```

### Get alert counts by type this build

```sql
SELECT alert_type, COUNT(*) as count
FROM network_alerts
WHERE build_version = (SELECT MAX(build_version) FROM networks_release)
GROUP BY alert_type
ORDER BY count DESC;
```

### Get networks with multiple alerts

```sql
SELECT network_name, COUNT(*) as alert_count
FROM network_alerts
WHERE build_version = (SELECT MAX(build_version) FROM networks_release)
GROUP BY network_name
HAVING alert_count >= 2
ORDER BY alert_count DESC;
```

---

End of Phase 4C Code Reference

