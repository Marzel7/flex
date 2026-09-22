"""Strict bridge from committed Scenario-D evidence to a Monitor entry reference.

The bridge never reconstructs ordering, calls a provider, or treats Byzantine
membership as entry evidence.  Its caller must supply the compact output of
the existing post-commit opening-cluster projection.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from src.ops.operation_strategy_trigger_producer import evaluate_byzantine_scenario_d


def _digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def derive_monitor_entry(evidence: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return a qualified Scenario-D Monitor entry only from committed inputs."""
    # Admission is assignment-first.  A missing envelope means the production
    # Scenario-D producer has not supplied its post-commit evidence yet; it is
    # not token-level negative evidence and must remain a visible waiting row.
    if not evidence:
        return {
            'result': 'WAITING_FOR_ENTRY_REFERENCE',
            'reason': 'BYZANTINE_ENTRY_EVIDENCE_DUE',
            'missing_evidence': ['committed Scenario-D 12/13 envelope'],
            'entry_evidence_work_type': 'BYZANTINE_ENTRY_EVIDENCE_DUE',
        }
    raw = dict(evidence or {})
    trigger = evaluate_byzantine_scenario_d(raw)
    if trigger['result'] != 'TRIGGER_QUALIFIED':
        return {'result': 'INSUFFICIENT_EVIDENCE', 'reason': 'SCENARIO_D_COMMITTED_EVIDENCE_UNAVAILABLE:'+trigger['result'],
                'missing_evidence': ['Scenario-D ordered positions 1..13', 'recurrent cluster identity', 'pre-entry state reference']}
    if not raw.get('entry_timestamp') or not raw.get('entry_mc_usd'):
        return {'result': 'INSUFFICIENT_EVIDENCE', 'reason': 'SCENARIO_D_USD_ENTRY_VALUATION_UNAVAILABLE',
                'missing_evidence': ['qualified Scenario-D USD entry valuation']}
    return {'result': 'ENTRY_REFERENCE_QUALIFIED', 'entry_method': 'SCENARIO_D_ACTIONABLE_COUNTERFACTUAL_ENTRY_FLOOR',
            'entry_timestamp': int(raw['entry_timestamp']), 'entry_mc_usd': float(raw['entry_mc_usd']),
            'entry_exactness': 'SCENARIO_D_12_13_COMMITTED', 'entry_provenance': _digest(raw),
            'trigger_boundary': trigger['boundary']}
