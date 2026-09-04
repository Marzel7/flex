from __future__ import annotations

import json
import sqlite3
import time
from typing import Any

from src.utils.db_locking import db_connect


class PredictionDecisionContextAnalyzer:
    """Materialize immutable prediction-time decision snapshots.

    This table is intentionally built only from fields already frozen on
    token_prediction_scores at prediction time. Current profitability/history
    tables are *not* allowed into this layer; they belong to post-prediction
    feedback in the UI/API.
    """

    def __init__(self, db_path: str):
        self.db_path = db_path

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS prediction_decision_context (
                mint TEXT PRIMARY KEY,
                prediction_id TEXT,
                predicted_at INTEGER NOT NULL,
                suggested_action TEXT NOT NULL,
                action_reason TEXT,
                blocking_risk_flags_json TEXT,
                positive_evidence_flags_json TEXT,
                why_reasons_json TEXT,
                creator_quality_at_prediction REAL,
                creator_history_count_at_prediction INTEGER,
                ecosystem_quality_at_prediction REAL,
                network_size_at_prediction INTEGER,
                coordinator_exposure_at_prediction INTEGER,
                liquidity_health_at_prediction TEXT,
                risk_state_at_prediction TEXT,
                funding_context_at_prediction TEXT,
                prediction_features_json TEXT,
                evidence_summary_json TEXT,
                confidence_at_prediction TEXT,
                snapshot_source TEXT NOT NULL DEFAULT 'prediction_creation',
                created_at INTEGER NOT NULL
            )
        ''')
        cols = {row[1] for row in conn.execute('PRAGMA table_info(prediction_decision_context)').fetchall()}
        wanted = {
            'prediction_id': 'TEXT',
            'predicted_at': 'INTEGER',
            'creator_quality_at_prediction': 'REAL',
            'creator_history_count_at_prediction': 'INTEGER',
            'ecosystem_quality_at_prediction': 'REAL',
            'network_size_at_prediction': 'INTEGER',
            'coordinator_exposure_at_prediction': 'INTEGER',
            'liquidity_health_at_prediction': 'TEXT',
            'risk_state_at_prediction': 'TEXT',
            'funding_context_at_prediction': 'TEXT',
            'prediction_features_json': 'TEXT',
            'evidence_summary_json': 'TEXT',
            'confidence_at_prediction': 'TEXT',
            'snapshot_source': "TEXT NOT NULL DEFAULT 'prediction_creation'",
            'created_at': 'INTEGER',
        }
        for name, typ in wanted.items():
            if name not in cols:
                conn.execute(f'ALTER TABLE prediction_decision_context ADD COLUMN {name} {typ}')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_pdc_action ON prediction_decision_context(suggested_action)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_pdc_predicted_at ON prediction_decision_context(predicted_at DESC)')

    @staticmethod
    def _json(raw: Any, fallback):
        try:
            return json.loads(raw) if raw else fallback
        except Exception:
            return fallback

    def _snapshot(self, row: sqlite3.Row, snapshot_source: str) -> dict[str, Any]:
        ex = self._json(row['explanation_json'], {})
        reasons = self._json(row['reason_codes'], [])
        status = row['prediction_status'] or 'COMPLETE'
        risk = row['risk_level'] or 'UNKNOWN'
        label = row['prediction_label'] or ''
        confidence = row['prediction_confidence'] or 'LOW'
        prior_history = int(ex.get('creator_migrated_tokens') or 0)
        blocking: list[str] = []
        positive: list[str] = []
        why: list[str] = []

        incomplete = status in {'PENDING_FUNDING', 'PENDING_RISK_SCORE', 'PENDING_CREATOR', 'INSUFFICIENT_HISTORY', 'NO_FUNDING_FOUND'}
        if incomplete:
            blocking.append(status)
            why.append(f"prediction context incomplete: {status.replace('_', ' ').lower()}")
        if risk in {'CRITICAL', 'HIGH'}:
            blocking.append(f'{risk}_RISK')
            why.append(f'{risk.lower()} token risk at prediction time')
        if label in {'LIKELY_DUMP', 'LIQUIDATION_RISK'}:
            blocking.append(label)
            why.append(label.replace('_', ' ').lower())
        if prior_history <= 0:
            positive.append('NO_PRIOR_CREATOR_HISTORY')
            why.append('no prior creator history; prediction based on live token/funding signals only')
        elif prior_history < 3:
            positive.append('THIN_PRIOR_CREATOR_HISTORY')
            why.append('creator had only thin prior history at prediction time')
        else:
            positive.append('PRIOR_CREATOR_HISTORY_AVAILABLE')
            why.append('creator had prior history available at prediction time')
        if ex.get('self_funding'):
            blocking.append('SELF_FUNDING')
            why.append('self-funding detected at prediction time')
        for reason in reasons[:5]:
            readable = reason.replace('_', ' ').lower()
            if readable not in why:
                why.append(readable)

        if incomplete:
            action = 'WATCH'
            action_reason = 'Wait for missing prediction-time context before approval.'
        elif blocking:
            action = 'IGNORE'
            action_reason = 'Blocking prediction-time risk evidence outweighs allocation case.'
        elif risk == 'WATCH':
            action = 'WATCH'
            action_reason = 'Fresh or mixed prediction-time evidence; observe before escalation.'
        elif risk == 'LOW' and confidence == 'HIGH' and prior_history >= 3:
            action = 'AUTO_ELIGIBLE'
            action_reason = 'Low-risk prediction with meaningful prior evidence available at prediction time.'
        elif risk == 'LOW':
            action = 'AUTO_ELIGIBLE'
            action_reason = 'Low-risk prediction, but evidence depth was limited at prediction time.'
        else:
            action = 'WATCH'
            action_reason = 'Mixed prediction-time evidence; keep in queue.'

        evidence = {
            'creator_history_count_at_prediction': prior_history,
            'creator_was_fresh': bool(row['creator_was_fresh']),
            'creator_score_at_prediction': row['creator_score'],
            'network_score_at_prediction': row['network_score'],
            'funding_score_at_prediction': row['funding_score'],
            'outcome_history_score_at_prediction': row['outcome_history_score'],
            'liquidation_score_at_prediction': row['liquidation_score'],
            'reason_codes': reasons,
            'reasons': ex.get('reasons') or [],
        }
        return {
            'mint': row['mint'],
            'prediction_id': row['mint'],
            'predicted_at': row['predicted_at'],
            'suggested_action': action,
            'action_reason': action_reason,
            'blocking_risk_flags_json': json.dumps(blocking),
            'positive_evidence_flags_json': json.dumps(positive),
            'why_reasons_json': json.dumps(why[:5]),
            'creator_quality_at_prediction': row['creator_score'],
            'creator_history_count_at_prediction': prior_history,
            'ecosystem_quality_at_prediction': row['network_score'],
            'network_size_at_prediction': None,
            'coordinator_exposure_at_prediction': 1 if 'network_risk_token' in reasons else 0,
            'liquidity_health_at_prediction': (
                row['liquidity_health_at_prediction']
                if 'liquidity_health_at_prediction' in row.keys() and row['liquidity_health_at_prediction']
                else 'UNKNOWN'
            ),
            'risk_state_at_prediction': risk,
            'funding_context_at_prediction': json.dumps({
                'funding_score': row['funding_score'],
                'self_funding': bool(ex.get('self_funding')),
                'second_hop': ex.get('second_hop'),
                'triggering_funder': ex.get('triggering_funder'),
            }),
            'prediction_features_json': row['explanation_json'] or '{}',
            'evidence_summary_json': json.dumps(evidence),
            'confidence_at_prediction': confidence,
            'snapshot_source': snapshot_source,
                'created_at': int(time.time()),
                # Compatibility for the earlier live-enrichment schema. These
                # columns remain present in upgraded databases but are no longer
                # used as decision-time evidence.
                'coordinator_exposed': 0,
                'liquidity_removed': 0,
                'refreshed_at': int(time.time()),
        }

    def run(self) -> dict[str, int]:
        with db_connect(self.db_path, timeout=30) as conn:
            conn.row_factory = sqlite3.Row
            self._ensure_schema(conn)
            # Rows created by the earlier live-enrichment design have no frozen
            # predicted_at snapshot. Replace only those legacy rows once.
            conn.execute('DELETE FROM prediction_decision_context WHERE predicted_at IS NULL')
            existing = {r[0] for r in conn.execute('SELECT mint FROM prediction_decision_context WHERE predicted_at IS NOT NULL')}
            rows = conn.execute('''
                SELECT mint, prediction_score, risk_level, prediction_label, prediction_status,
                       prediction_confidence, reason_codes, explanation_json, creator_score,
                       network_score, funding_score, outcome_history_score, liquidation_score,
                       predicted_at, creator_was_fresh,
                       NULL AS liquidity_health_at_prediction
                FROM token_prediction_scores
            ''').fetchall()
            payload = [self._snapshot(r, 'legacy_reconstructed_from_prediction_score') for r in rows if r['mint'] not in existing]
            if payload:
                conn.executemany('''
                    INSERT OR IGNORE INTO prediction_decision_context (
                      mint,prediction_id,predicted_at,suggested_action,action_reason,blocking_risk_flags_json,
                      positive_evidence_flags_json,why_reasons_json,creator_quality_at_prediction,
                      creator_history_count_at_prediction,ecosystem_quality_at_prediction,network_size_at_prediction,
                      coordinator_exposure_at_prediction,liquidity_health_at_prediction,risk_state_at_prediction,
                      funding_context_at_prediction,prediction_features_json,evidence_summary_json,
                      confidence_at_prediction,snapshot_source,created_at
                      ,coordinator_exposed,liquidity_removed,refreshed_at
                    ) VALUES (:mint,:prediction_id,:predicted_at,:suggested_action,:action_reason,:blocking_risk_flags_json,
                      :positive_evidence_flags_json,:why_reasons_json,:creator_quality_at_prediction,
                      :creator_history_count_at_prediction,:ecosystem_quality_at_prediction,:network_size_at_prediction,
                      :coordinator_exposure_at_prediction,:liquidity_health_at_prediction,:risk_state_at_prediction,
                      :funding_context_at_prediction,:prediction_features_json,:evidence_summary_json,
                      :confidence_at_prediction,:snapshot_source,:created_at,
                      :coordinator_exposed,:liquidity_removed,:refreshed_at)
                ''', payload)
            conn.commit()
            repaired = self.repair_legacy_prior_history(conn)
        return {'decision_context_rows': len(payload), 'legacy_prior_history_repaired': repaired}

    def repair_legacy_prior_history(self, conn: sqlite3.Connection) -> int:
        """Repair legacy snapshots using strict pre-prediction creator history.

        This is safe because it relies only on token_analysis rows whose
        migrated_at timestamp is strictly earlier than the frozen predicted_at.
        """
        conn.executescript('''
            DROP TABLE IF EXISTS _prediction_creator_mints;
            CREATE TEMP TABLE _prediction_creator_mints AS
            SELECT mint, earliest_tx_creator AS creator_address, migrated_at
            FROM token_analysis
            WHERE earliest_tx_creator IS NOT NULL AND migrated_at IS NOT NULL
            UNION
            SELECT mint, pf_ws_creator AS creator_address, migrated_at
            FROM token_analysis
            WHERE pf_ws_creator IS NOT NULL AND migrated_at IS NOT NULL;
            CREATE INDEX _idx_prediction_creator_mints
            ON _prediction_creator_mints(creator_address, migrated_at, mint);
        ''')
        rows = conn.execute('''
            SELECT pdc.mint, pdc.predicted_at, tps.creator_address,
                   pdc.creator_history_count_at_prediction,
                   tps.reason_codes,
                   COUNT(DISTINCT cm.mint) AS strict_prior_count
            FROM prediction_decision_context pdc
            JOIN token_prediction_scores tps ON tps.mint=pdc.mint
            LEFT JOIN _prediction_creator_mints cm
              ON cm.mint <> pdc.mint
             AND cm.creator_address=tps.creator_address
             AND CAST(cm.migrated_at AS INTEGER) < pdc.predicted_at
            WHERE pdc.snapshot_source='legacy_reconstructed_from_prediction_score'
              AND pdc.predicted_at IS NOT NULL
              AND tps.creator_address IS NOT NULL
            GROUP BY pdc.mint, pdc.predicted_at, tps.creator_address,
                     pdc.creator_history_count_at_prediction, tps.reason_codes
        ''').fetchall()
        repaired = 0
        for row in rows:
            prior = row['strict_prior_count']
            current = row['creator_history_count_at_prediction']
            if prior != current:
                # Rebuild only the evidence labels/reasons that depend on this count.
                if prior <= 0:
                    flags = ['NO_PRIOR_CREATOR_HISTORY']
                    why = ['no prior creator history; prediction based on live token/funding signals only']
                elif prior < 3:
                    flags = ['THIN_PRIOR_CREATOR_HISTORY']
                    why = ['creator had only thin prior history at prediction time']
                else:
                    flags = ['PRIOR_CREATOR_HISTORY_AVAILABLE']
                    why = ['creator had prior history available at prediction time']
                reason_codes = self._json(row['reason_codes'], [])
                for reason in reason_codes[:5]:
                    readable = reason.replace('_', ' ').lower()
                    if readable not in why:
                        why.append(readable)
                conn.execute('''
                    UPDATE prediction_decision_context
                    SET creator_history_count_at_prediction=?,
                        positive_evidence_flags_json=?,
                        why_reasons_json=?
                    WHERE mint=?
                ''', (prior, json.dumps(flags), json.dumps(why[:5]), row['mint']))
                repaired += 1
        return repaired


def insert_prediction_snapshots(conn: sqlite3.Connection, score_rows: list[dict[str, Any]]) -> None:
    """Insert immutable snapshots at prediction creation time; never overwrite."""
    analyzer = PredictionDecisionContextAnalyzer('')
    analyzer._ensure_schema(conn)
    payload = [analyzer._snapshot(row, 'prediction_creation') for row in score_rows]
    conn.executemany('''
        INSERT OR IGNORE INTO prediction_decision_context (
          mint,prediction_id,predicted_at,suggested_action,action_reason,blocking_risk_flags_json,
          positive_evidence_flags_json,why_reasons_json,creator_quality_at_prediction,
          creator_history_count_at_prediction,ecosystem_quality_at_prediction,network_size_at_prediction,
          coordinator_exposure_at_prediction,liquidity_health_at_prediction,risk_state_at_prediction,
          funding_context_at_prediction,prediction_features_json,evidence_summary_json,
          confidence_at_prediction,snapshot_source,created_at
          ,coordinator_exposed,liquidity_removed,refreshed_at
        ) VALUES (:mint,:prediction_id,:predicted_at,:suggested_action,:action_reason,:blocking_risk_flags_json,
          :positive_evidence_flags_json,:why_reasons_json,:creator_quality_at_prediction,
          :creator_history_count_at_prediction,:ecosystem_quality_at_prediction,:network_size_at_prediction,
          :coordinator_exposure_at_prediction,:liquidity_health_at_prediction,:risk_state_at_prediction,
          :funding_context_at_prediction,:prediction_features_json,:evidence_summary_json,
          :confidence_at_prediction,:snapshot_source,:created_at,
          :coordinator_exposed,:liquidity_removed,:refreshed_at)
    ''', payload)
