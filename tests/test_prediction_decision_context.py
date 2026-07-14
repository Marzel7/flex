import sqlite3
from src.core.prediction_decision_context import PredictionDecisionContextAnalyzer


def row(**overrides):
    base = dict(
        mint='m', prediction_score=None, risk_level='WATCH', prediction_label='FRESH_UNLINKED_EVENT',
        prediction_status='COMPLETE', prediction_confidence='LOW', reason_codes='["fresh_unlinked_creator"]',
        explanation_json='{"creator_migrated_tokens": 0, "reasons": ["Fresh creator"]}',
        creator_score=0, network_score=0, funding_score=0, outcome_history_score=0,
        liquidation_score=0, predicted_at=1000, creator_was_fresh=1,
    )
    base.update(overrides)
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    cols = ','.join(base.keys())
    conn.execute('create table t (%s)' % ','.join(f'{k} text' for k in base))
    conn.execute(f"insert into t ({cols}) values ({','.join('?' for _ in base)})", tuple(base.values()))
    return conn.execute('select * from t').fetchone()


def test_fresh_creator_snapshot_has_no_prior_history():
    d = PredictionDecisionContextAnalyzer('')._snapshot(row(), 'prediction_creation')
    assert d['creator_history_count_at_prediction'] == 0
    assert 'NO_PRIOR_CREATOR_HISTORY' in d['positive_evidence_flags_json']
    assert 'STRONG_CREATOR' not in d['positive_evidence_flags_json']


def test_prior_creator_history_uses_frozen_prediction_fields():
    d = PredictionDecisionContextAnalyzer('')._snapshot(row(
        risk_level='LOW', prediction_label='LIKELY_MIGRATOR', prediction_confidence='HIGH',
        explanation_json='{"creator_migrated_tokens": 5}', creator_was_fresh=0,
    ), 'prediction_creation')
    assert d['creator_history_count_at_prediction'] == 5
    assert 'PRIOR_CREATOR_HISTORY_AVAILABLE' in d['positive_evidence_flags_json']


def test_snapshot_contains_no_post_prediction_simulation_quality():
    d = PredictionDecisionContextAnalyzer('')._snapshot(row(), 'prediction_creation')
    assert 'SIMULATION_QUALITY' not in d['positive_evidence_flags_json']
