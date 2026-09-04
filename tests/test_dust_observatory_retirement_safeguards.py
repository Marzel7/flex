"""Live Dust telemetry may retire without weakening funding/spam safeguards."""
from __future__ import annotations

import inspect
from pathlib import Path

from src.ops.unknown_funder_edge_quality import (
    EdgeQuality,
    FundingObservation,
    classify_unknown_funder_edge,
)
from src.utils.dust_addresses import DUST_ADDRESSES


ROOT = Path(__file__).resolve().parents[1]


def test_live_ws_cascade_has_no_dust_observatory_runtime_hook():
    source = (ROOT / "src/core/ws_cascade.py").read_text()
    assert "dust_observatory" not in source
    assert 'kind == "dust"' not in source


def test_creator_funding_dust_plumbing_registry_remains_independent():
    source = (ROOT / "src/extractors/realtime_creator_funding_extractor.py").read_text()
    assert "DUST_ADDRESSES" in source
    assert DUST_ADDRESSES


def test_walkback_confirmed_spam_exclusion_remains_before_candidate_selection():
    from src.core import walkback_worker

    source = inspect.getsource(walkback_worker._find_funder_via_rpc)
    assert source.find("_is_known_spam_sender") < source.find("candidates.append")
    assert "record_spam_transfer" in source


def test_low_value_or_new_genuine_funder_remains_qualifying():
    result, _ = classify_unknown_funder_edge(FundingObservation(
        proven_funding_role=True,
        amount_lamports=1_000,
        funder_account_age_seconds=1,
        broad_unrelated_fanout=True,
    ))
    assert result is EdgeQuality.QUALIFYING_FUNDING_EDGE
