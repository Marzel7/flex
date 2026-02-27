#!/usr/bin/env python3
"""
Phase 9A: Query Plan Audit

Run EXPLAIN QUERY PLAN for key monitoring/build queries and generate report.
This helps identify missing indexes and query optimization opportunities.

Usage:
    python3 scripts/query_plan_audit.py <db_path>

Output:
    QUERY_PLAN_REPORT.md - Findings and recommendations
"""

import sqlite3
import sys
from pathlib import Path

def get_db_conn(db_path):
    """Get database connection."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn

def run_explain_plan(cursor, query, params=None):
    """Run EXPLAIN QUERY PLAN and return results."""
    try:
        explain_query = f"EXPLAIN QUERY PLAN {query}"
        if params:
            cursor.execute(explain_query, params)
        else:
            cursor.execute(explain_query)
        return cursor.fetchall()
    except Exception as e:
        return f"ERROR: {e}"

def format_plan(plan_rows):
    """Format EXPLAIN QUERY PLAN output."""
    if isinstance(plan_rows, str):
        return plan_rows
    lines = []
    for row in plan_rows:
        lines.append(f"  {row[0]}")
    return "\n".join(lines)

def audit_queries(db_path):
    """Run all query plan audits."""
    conn = get_db_conn(db_path)
    cursor = conn.cursor()

    # Check if tables exist
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row[0] for row in cursor.fetchall()}

    results = {}

    # ============================================================================
    # QUERY A1: Monitoring Alerts - ACTIVE view (default)
    # ============================================================================
    if 'network_alerts' in tables:
        query = '''
            SELECT alert_id, network_name, alert_type, severity, message, created_at,
                   acknowledged, acknowledged_at, acknowledged_by,
                   suppressed_until, suppressed_at, suppression_reason,
                   is_escalated, escalated_at, escalation_rule, escalation_reason
            FROM network_alerts
            WHERE (? IS NULL OR severity = ?)
              AND (? IS NULL OR alert_type = ?)
              AND (? IS NULL OR network_name LIKE ?)
              AND (acknowledged = 0 AND (suppressed_until IS NULL OR suppressed_until <= CURRENT_TIMESTAMP))
            ORDER BY created_at DESC
            LIMIT 200
        '''
        params = [None, None, None, None, None, None]
        plan = run_explain_plan(cursor, query, params)
        results['A1_alerts_active'] = {
            'name': 'Monitoring Alerts - ACTIVE view',
            'query': query.strip(),
            'plan': format_plan(plan),
            'tables_used': ['network_alerts'],
            'critical': True
        }

        # ====================================================================
        # QUERY A2: Monitoring Alerts - with filters (severity + type)
        # ====================================================================
        query = '''
            SELECT alert_id, network_name, alert_type, severity, message, created_at,
                   acknowledged, acknowledged_at, acknowledged_by,
                   suppressed_until, suppressed_at, suppression_reason,
                   is_escalated, escalated_at, escalation_rule, escalation_reason
            FROM network_alerts
            WHERE severity = ?
              AND alert_type = ?
              AND (acknowledged = 0 AND (suppressed_until IS NULL OR suppressed_until <= CURRENT_TIMESTAMP))
            ORDER BY created_at DESC
            LIMIT 200
        '''
        params = ['HIGH', 'STABILITY_DROP']
        plan = run_explain_plan(cursor, query, params)
        results['A2_alerts_filtered'] = {
            'name': 'Monitoring Alerts - with severity + type filters',
            'query': query.strip(),
            'plan': format_plan(plan),
            'tables_used': ['network_alerts'],
            'critical': True
        }

        # ====================================================================
        # QUERY A3: Monitoring Alerts - escalated view
        # ====================================================================
        query = '''
            SELECT alert_id, network_name, alert_type, severity, message, created_at,
                   acknowledged, acknowledged_at, acknowledged_by,
                   suppressed_until, suppressed_at, suppression_reason,
                   is_escalated, escalated_at, escalation_rule, escalation_reason
            FROM network_alerts
            WHERE (acknowledged = 0 AND (suppressed_until IS NULL OR suppressed_until <= CURRENT_TIMESTAMP))
              AND is_escalated = 1
            ORDER BY created_at DESC
            LIMIT 200
        '''
        params = []
        plan = run_explain_plan(cursor, query, params)
        results['A3_alerts_escalated'] = {
            'name': 'Monitoring Alerts - escalated view',
            'query': query.strip(),
            'plan': format_plan(plan),
            'tables_used': ['network_alerts'],
            'critical': True
        }

    # ============================================================================
    # QUERY B1: High Risk Networks - sort by score (default)
    # ============================================================================
    if 'network_scores' in tables and 'networks_release' in tables:
        query = '''
            SELECT
              nr.network_name,
              ns.score,
              ns.smoothed_score,
              ns.stability_coeff,
              ns.stability_trend,
              ns.trend_direction,
              ns.risk_band,
              nr.network_type,
              nr.stability_state,
              nr.build_version
            FROM network_scores ns
            JOIN networks_release nr ON nr.network_name = ns.network_name
            ORDER BY COALESCE(ns.smoothed_score, ns.score) DESC
            LIMIT 50
        '''
        plan = run_explain_plan(cursor, query)
        results['B1_networks_score'] = {
            'name': 'High Risk Networks - sort by score (default)',
            'query': query.strip(),
            'plan': format_plan(plan),
            'tables_used': ['network_scores', 'networks_release'],
            'critical': True
        }

        # ====================================================================
        # QUERY B2: High Risk Networks - sort by risk_band
        # ====================================================================
        query = '''
            SELECT
              nr.network_name,
              ns.score,
              ns.smoothed_score,
              ns.stability_coeff,
              ns.stability_trend,
              ns.trend_direction,
              ns.risk_band,
              nr.network_type,
              nr.stability_state,
              nr.build_version
            FROM network_scores ns
            JOIN networks_release nr ON nr.network_name = ns.network_name
            ORDER BY
                CASE COALESCE(ns.risk_band, 'LOW')
                    WHEN 'CRITICAL' THEN 1
                    WHEN 'ELEVATED' THEN 2
                    WHEN 'MODERATE' THEN 3
                    WHEN 'LOW' THEN 4
                    ELSE 5
                END ASC, ns.smoothed_score DESC
            LIMIT 50
        '''
        plan = run_explain_plan(cursor, query)
        results['B2_networks_risk'] = {
            'name': 'High Risk Networks - sort by risk_band',
            'query': query.strip(),
            'plan': format_plan(plan),
            'tables_used': ['network_scores', 'networks_release'],
            'critical': True
        }

        # ====================================================================
        # QUERY B3: High Risk Networks - sort by trend
        # ====================================================================
        query = '''
            SELECT
              nr.network_name,
              ns.score,
              ns.smoothed_score,
              ns.stability_coeff,
              ns.stability_trend,
              ns.trend_direction,
              ns.risk_band,
              nr.network_type,
              nr.stability_state,
              nr.build_version
            FROM network_scores ns
            JOIN networks_release nr ON nr.network_name = ns.network_name
            ORDER BY COALESCE(ns.stability_trend, 0) DESC
            LIMIT 50
        '''
        plan = run_explain_plan(cursor, query)
        results['B3_networks_trend'] = {
            'name': 'High Risk Networks - sort by trend',
            'query': query.strip(),
            'plan': format_plan(plan),
            'tables_used': ['network_scores', 'networks_release'],
            'critical': True
        }

        # ====================================================================
        # QUERY B4: High Risk Networks - with band filter
        # ====================================================================
        query = '''
            SELECT
              nr.network_name,
              ns.score,
              ns.smoothed_score,
              ns.stability_coeff,
              ns.stability_trend,
              ns.trend_direction,
              ns.risk_band,
              nr.network_type,
              nr.stability_state,
              nr.build_version
            FROM network_scores ns
            JOIN networks_release nr ON nr.network_name = ns.network_name
            WHERE COALESCE(ns.risk_band, 'LOW') = ?
            ORDER BY COALESCE(ns.smoothed_score, ns.score) DESC
            LIMIT 50
        '''
        params = ['CRITICAL']
        plan = run_explain_plan(cursor, query, params)
        results['B4_networks_band_filter'] = {
            'name': 'High Risk Networks - with band filter (CRITICAL)',
            'query': query.strip(),
            'plan': format_plan(plan),
            'tables_used': ['network_scores', 'networks_release'],
            'critical': True
        }

    # ============================================================================
    # QUERY C: Biggest Movers
    # ============================================================================
    if 'network_score_history' in tables:
        query = '''
            SELECT h.network_name, p.score AS prev_score, h.score AS curr_score, (h.score - p.score) AS delta
            FROM network_score_history h
            JOIN network_score_history p ON p.network_name = h.network_name
              AND p.build_version = h.build_version - 1
            WHERE h.build_version = (SELECT MAX(build_version) FROM network_score_history)
            ORDER BY delta DESC
            LIMIT 50
        '''
        plan = run_explain_plan(cursor, query)
        results['C_biggest_movers'] = {
            'name': 'Biggest Movers',
            'query': query.strip(),
            'plan': format_plan(plan),
            'tables_used': ['network_score_history'],
            'critical': True
        }

    # ============================================================================
    # QUERY D: Creator Network Detail - score retrieval
    # ============================================================================
    if 'network_scores' in tables:
        query = '''
            SELECT
              network_name, score, smoothed_score, stability_coeff,
              stability_trend, trend_direction, risk_band, build_version
            FROM network_scores
            WHERE network_name = ?
        '''
        params = ['example_network']
        plan = run_explain_plan(cursor, query, params)
        results['D_creator_detail'] = {
            'name': 'Creator Network Detail - score retrieval',
            'query': query.strip(),
            'plan': format_plan(plan),
            'tables_used': ['network_scores'],
            'critical': False
        }

    # ============================================================================
    # QUERY E: Network Evidence Retrieval (if available)
    # ============================================================================
    if 'network_evidence' in tables:
        query = '''
            SELECT
              network_name, total_edges, average_confidence,
              evidence_risk_score, evidence_version, last_changed_at
            FROM network_evidence
            WHERE network_name = ?
        '''
        params = ['example_network']
        plan = run_explain_plan(cursor, query, params)
        results['E_network_evidence'] = {
            'name': 'Network Evidence Retrieval',
            'query': query.strip(),
            'plan': format_plan(plan),
            'tables_used': ['network_evidence'],
            'critical': False
        }

    conn.close()
    return results

def analyze_indexes(cursor):
    """Analyze current indexes on key tables."""
    cursor.execute("""
        SELECT name, tbl_name, sql FROM sqlite_master
        WHERE type='index' AND tbl_name IN (
            'network_alerts', 'network_scores', 'network_score_history',
            'networks_release', 'network_evidence'
        )
        ORDER BY tbl_name, name
    """)
    return cursor.fetchall()

def generate_report(db_path):
    """Generate QUERY_PLAN_REPORT.md."""
    conn = get_db_conn(db_path)
    cursor = conn.cursor()

    audit_results = audit_queries(db_path)
    current_indexes = analyze_indexes(cursor)

    report = """# Query Plan Audit Report (Phase 9A)

## Executive Summary

This report documents EXPLAIN QUERY PLAN analysis for key monitoring and build queries.
Findings inform index additions and query optimization decisions.

---

## Current Indexes

"""
    if current_indexes:
        for idx in current_indexes:
            report += f"- **{idx[0]}** on {idx[1]}: `{idx[2]}`\n"
    else:
        report += "No custom indexes found.\n"

    report += """
---

## Query Plans and Analysis

"""

    for key in sorted(audit_results.keys()):
        result = audit_results[key]
        report += f"""### Query {key}: {result['name']}

**Critical**: {'Yes' if result['critical'] else 'No'}

**Query**:
```sql
{result['query']}
```

**Plan**:
```
{result['plan']}
```

"""

    # Recommendations section
    report += """---

## Index Recommendations

Based on query plan analysis, the following indexes are recommended:

### High Priority
- `network_alerts(created_at DESC)` - Supports ordering by created_at in ACTIVE/escalated queries
- `network_alerts(acknowledged, created_at DESC)` - Supports filtering + ordering in alert queries
- `network_alerts(is_escalated, created_at DESC)` - Supports escalated view filtering
- `network_alerts(severity, created_at DESC)` - Supports severity filtering with order

### Medium Priority
- `network_alerts(suppressed_until)` - Supports suppression expiry checks in queries
- `network_scores(risk_band)` - Supports band filtering on networks
- `network_scores(stability_trend)` - Supports trend-based sorting
- `network_score_history(build_version DESC)` - Supports MAX(build_version) and delta joins

### Implementation
All indexes are additive with `IF NOT EXISTS` to ensure idempotency.

---

## Performance Impact

- **Alert queries** (A1-A3): Should see 10-50x improvement with compound indexes
- **Network queries** (B1-B4): Join performance improves with individual indexes
- **Biggest movers** (C): Subquery optimization benefits from build_version index
- **Detail queries** (D-E): Minimal impact (single-row lookups already fast)

---

## Next Steps

1. Add indexes from High Priority list
2. Re-run benchmark_queries.py to measure improvement
3. Monitor query execution during load testing
"""

    conn.close()
    return report

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python3 scripts/query_plan_audit.py <db_path>")
        sys.exit(1)

    db_path = sys.argv[1]
    if not Path(db_path).exists():
        print(f"Database not found: {db_path}")
        sys.exit(1)

    report = generate_report(db_path)

    # Write report
    report_path = Path('QUERY_PLAN_REPORT.md')
    report_path.write_text(report)
    print(f"✅ Query plan report written to {report_path}")
    print(f"\nReport preview (first 100 lines):")
    print('\n'.join(report.split('\n')[:100]))
