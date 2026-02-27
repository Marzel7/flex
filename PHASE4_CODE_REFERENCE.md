# Phase 4 Code Reference

## 1. Schema Migration SQL

**File**: `PHASE4_NETWORK_SCORING_SCHEMA.sql`

```sql
-- Create main network_scores table
CREATE TABLE IF NOT EXISTS network_scores (
    network_name TEXT PRIMARY KEY,
    score INTEGER NOT NULL DEFAULT 0,  -- 0-100 scale
    score_version INTEGER NOT NULL DEFAULT 1,  -- Track scoring rule updates
    score_components_json TEXT,  -- JSON with {connectivity, lifecycle, evidence} breakdown
    computed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (network_name) REFERENCES networks_release(network_name)
);

-- Create indexes for common queries
CREATE INDEX IF NOT EXISTS idx_network_scores_score
    ON network_scores(score DESC);

CREATE INDEX IF NOT EXISTS idx_network_scores_computed_at
    ON network_scores(computed_at DESC);

CREATE INDEX IF NOT EXISTS idx_network_scores_name
    ON network_scores(network_name);
```

---

## 2. Phase G Build Logic (Excerpt)

**File**: `build_networks_release.py` - Added to `build_networks_release()` function

### A. Schema Creation & Snapshots
```python
# Ensure network_scores table exists
db.execute('''
    CREATE TABLE IF NOT EXISTS network_scores (
        network_name TEXT PRIMARY KEY,
        score INTEGER NOT NULL DEFAULT 0,
        score_version INTEGER NOT NULL DEFAULT 1,
        score_components_json TEXT,
        computed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (network_name) REFERENCES networks_release(network_name)
    );
''')

# Create indexes
db.execute('CREATE INDEX IF NOT EXISTS idx_network_scores_score ON network_scores(score DESC);')
db.execute('CREATE INDEX IF NOT EXISTS idx_network_scores_computed_at ON network_scores(computed_at DESC);')
db.execute('CREATE INDEX IF NOT EXISTS idx_network_scores_name ON network_scores(network_name);')

# Snapshot previous scores for version tracking
db.execute('DROP TABLE IF EXISTS network_scores_prev')
db.execute('''
    CREATE TABLE network_scores_prev AS
    SELECT network_name, score, score_version
    FROM network_scores;
''')
```

### B. Three-Component Scoring Model
```python
db.execute('''
    WITH score_components AS (
      SELECT
        nr.network_name,
        -- Component A: Connectivity Risk (0-40)
        CASE
          WHEN nr.network_type = 'organic' THEN 0
          WHEN nr.network_type = 'cex_connected' THEN 10
          WHEN nr.network_type = 'infra_connected' THEN 15
          WHEN nr.network_type = 'cex_and_infra_connected' THEN 25
          ELSE 0
        END as connectivity_risk,
        -- Component B: Lifecycle Risk (0-25)
        CASE
          WHEN nr.stability_state = 'stable' THEN 0
          WHEN nr.stability_state = 'new' THEN 10
          WHEN nr.stability_state = 'growing' THEN 20
          WHEN nr.stability_state = 'shrinking' THEN 5
          ELSE 0
        END as lifecycle_risk,
        -- Component C: Evidence Risk (0-35)
        CASE
          WHEN ne.total_edges IS NULL THEN 0
          WHEN ne.total_edges = 0 THEN 0
          ELSE MIN(35, CAST((ne.high_confidence_edges + 1) * 35 / CAST(GREATEST(ne.total_edges, 1) AS FLOAT) AS INTEGER))
        END as evidence_risk,
        ne.high_confidence_edges,
        ne.total_edges
      FROM networks_release nr
      LEFT JOIN network_evidence ne ON nr.network_name = ne.network_name
    ),
    final_scores AS (
      SELECT
        network_name,
        connectivity_risk,
        lifecycle_risk,
        evidence_risk,
        MIN(100, connectivity_risk + lifecycle_risk + evidence_risk) as final_score,
        json_object(
          'connectivity', connectivity_risk,
          'lifecycle', lifecycle_risk,
          'evidence', evidence_risk,
          'high_confidence_edges', COALESCE(high_confidence_edges, 0),
          'total_edges', COALESCE(total_edges, 0)
        ) as components_json
      FROM score_components
    )
    INSERT OR REPLACE INTO network_scores
    (network_name, score, score_version, score_components_json, computed_at)
    SELECT
      network_name,
      final_score,
      1,
      components_json,
      CURRENT_TIMESTAMP
    FROM final_scores;
''')
```

### C. Idempotent Version Tracking
```python
# Update score versions idempotently
db.execute('''
    CREATE TEMP TABLE score_deltas AS
    SELECT
      ns.network_name,
      ns.score,
      COALESCE(old.score, -1) as old_score,
      CASE
        WHEN old.network_name IS NULL THEN 1
        WHEN ns.score != old.score THEN 1
        ELSE 0
      END as changed_flag
    FROM network_scores ns
    LEFT JOIN network_scores_prev old ON ns.network_name = old.network_name;
''')

db.execute('''
    UPDATE network_scores
    SET score_version = CASE
      WHEN (SELECT changed_flag FROM score_deltas
            WHERE score_deltas.network_name = network_scores.network_name) = 1
      THEN score_version + 1
      ELSE score_version
    END
    WHERE network_name IN (SELECT network_name FROM score_deltas);
''')
```

### D. Reporting
```python
# Verify scoring
score_stats = db.execute('''
    SELECT
      COUNT(*) as total_networks,
      ROUND(AVG(score), 2) as avg_score,
      MAX(score) as max_score,
      MIN(score) as min_score,
      COUNT(CASE WHEN score >= 70 THEN 1 END) as high_risk,
      COUNT(CASE WHEN score >= 30 AND score < 70 THEN 1 END) as medium_risk,
      COUNT(CASE WHEN score < 30 THEN 1 END) as low_risk
    FROM network_scores
''').fetchone()

print(f"   ✅ Scores computed: {score_stats['total_networks']} networks")
print(f"      Average score: {score_stats['avg_score']}")
print(f"      Risk distribution: High({score_stats['high_risk']}) | Med({score_stats['medium_risk']}) | Low({score_stats['low_risk']})")

# Cleanup temp tables
db.execute('DROP TABLE IF EXISTS network_scores_prev')
```

---

## 3. UI Query Helper

**File**: `main.py` - Inserted after `get_network_release_by_name()` (~Line 179)

```python
def get_network_score(network_name: str) -> dict:
    """
    Retrieve precomputed network score for UI display.

    Returns dict with:
    - score: 0-100 integer
    - score_version: version of scoring model
    - components: dict with {connectivity, lifecycle, evidence} breakdown
    - score_badge: 'high' (70+), 'medium' (30-69), or 'low' (0-29)
    """
    try:
        conn, cursor = get_db_conn()
        cursor.execute('''
            SELECT
              score,
              score_version,
              score_components_json
            FROM network_scores
            WHERE network_name = ?
        ''', (network_name,))

        row = cursor.fetchone()
        conn.close()

        if not row:
            return {
                'score': None,
                'score_version': None,
                'components': None,
                'score_badge': None,
            }

        score = row['score']
        components = json.loads(row['score_components_json']) if row['score_components_json'] else {}

        # Determine risk badge
        if score >= 70:
            badge = 'high'
        elif score >= 30:
            badge = 'medium'
        else:
            badge = 'low'

        return {
            'score': score,
            'score_version': row['score_version'],
            'components': components,
            'score_badge': badge,
        }
    except Exception as e:
        print(f"[ERROR] get_network_score: {e}")
        return {
            'score': None,
            'score_version': None,
            'components': None,
            'score_badge': None,
        }
```

---

## 4. Example Usage in Route Handlers

```python
@app.route('/api/network-score/<network_name>')
def api_network_score(network_name):
    """API endpoint to retrieve network score."""
    score_info = get_network_score(network_name)

    if score_info['score'] is None:
        return jsonify({'error': 'Network not found'}), 404

    return jsonify({
        'network_name': network_name,
        'score': score_info['score'],
        'score_badge': score_info['score_badge'],
        'components': score_info['components'],
        'score_version': score_info['score_version'],
    })
```

---

## 5. Example Template Usage

```html
{% if score %}
  <div class="score-container">
    <h3>Network Risk Score: <span class="badge-{{ score_badge }}">{{ score }}/100</span></h3>

    {% if components %}
      <div class="score-breakdown">
        <p>Connectivity: {{ components.connectivity }}/40</p>
        <p>Lifecycle: {{ components.lifecycle }}/25</p>
        <p>Evidence: {{ components.evidence }}/35</p>
      </div>
    {% endif %}
  </div>
{% endif %}
```

---

## Scoring Model Summary

| Component | Range | Values | Purpose |
|-----------|-------|--------|---------|
| **Connectivity** | 0–40 | organic:0, cex:10, infra:15, cex+infra:25 | External exchange/infra exposure |
| **Lifecycle** | 0–25 | stable:0, new:10, growing:20, shrinking:5 | Growth/stability risk |
| **Evidence** | 0–35 | Normalized by edges | Coordinated activity level |

**Final Score**: `min(100, connectivity + lifecycle + evidence)`

---

End of Phase 4 Code Reference

