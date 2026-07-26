"""X65.10 — Implement Candidate-Watch Topology Classification.

Tests src/ops/funding_topology.py's new candidate-watch-based Fan-Out/
Linear evidence path (X65.8's design), against a minimal in-memory
schema mirroring the real wt_candidate_websocket_watches/
wt_provisioning_edges tables.

Must never: call or import campaign_classification, remove the
existing wt_provisioning_edges fallback, break conservation, or force
a WATCHTOWER-campaign launch into FAN_OUT without direct observed
evidence.
"""
from __future__ import annotations

import sqlite3

import pytest

from src.ops.funding_topology import (
    FAN_OUT,
    LINEAR,
    MESH,
    MULTI_LEVEL_FAN_OUT,
    UNKNOWN,
    TOPOLOGY_ORDER,
    _subprov_candidate_watch_counts,
    build_topology_classification,
    classify_topology_for_launch,
)


def _build_ops_db(tmp_path):
    db = tmp_path / "ops.db"
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE wt_attribution_outcomes (
            mint TEXT PRIMARY KEY, evidence_json TEXT, completed_at INTEGER
        );
        CREATE TABLE wt_watchtower_launches (
            mint TEXT PRIMARY KEY, subprov_wallet TEXT, treasury_wallet TEXT,
            create_time INTEGER
        );
        CREATE TABLE token_analysis (
            mint TEXT PRIMARY KEY, created_at TEXT
        );
        CREATE TABLE wt_provisioning_edges (
            edge_type TEXT, from_wallet TEXT, to_wallet TEXT
        );
        CREATE TABLE wt_candidate_websocket_watches (
            candidate_wallet TEXT, subprov_wallet TEXT
        );
        CREATE TABLE wt_active_subprov_sessions (
            subprov_wallet TEXT, treasury_wallet TEXT
        );
        CREATE TABLE watchtower_events (
            wallet_address TEXT, event_type TEXT, payload_json TEXT
        );
        CREATE TABLE wt_walkback_edge_candidates (
            mint TEXT, wallet TEXT, candidate_parent TEXT, hop_depth INTEGER,
            selection_status TEXT
        );
        CREATE TABLE wt_walkback_queue (
            mint TEXT, termination_reason_json TEXT
        );
    """)
    conn.commit()
    conn.close()
    return str(db)


class TestCandidateWatchCountsReader:
    def test_empty_table_returns_empty_dict(self, tmp_path):
        db = _build_ops_db(tmp_path)
        conn = sqlite3.connect(db)
        assert _subprov_candidate_watch_counts(conn) == {}

    def test_counts_distinct_candidates_per_subprov(self, tmp_path):
        db = _build_ops_db(tmp_path)
        conn = sqlite3.connect(db)
        conn.executemany(
            "INSERT INTO wt_candidate_websocket_watches VALUES (?,?)",
            [("C1", "SP1"), ("C2", "SP1"), ("C3", "SP1"), ("C4", "SP2")],
        )
        conn.commit()
        counts = _subprov_candidate_watch_counts(conn)
        assert counts["SP1"] == 3
        assert counts["SP2"] == 1

    def test_null_subprov_excluded(self, tmp_path):
        db = _build_ops_db(tmp_path)
        conn = sqlite3.connect(db)
        conn.execute("INSERT INTO wt_candidate_websocket_watches VALUES ('C1', NULL)")
        conn.commit()
        assert _subprov_candidate_watch_counts(conn) == {}


class TestClassifyTopologyCandidateWatchPriority:
    def test_candidate_watch_fanout_takes_priority_over_provisioning_edges(self):
        """Core X65.8/X65.10 requirement: candidate-watch evidence is
        checked BEFORE wt_provisioning_edges."""
        result = classify_topology_for_launch(
            None,
            subprov_wallet="SP1",
            treasury_wallet=None,
            candidate_watch_counts={"SP1": 5},
            sibling_counts={"SP1": 1},  # would say LINEAR if consulted
        )
        assert result["topology"] == FAN_OUT
        assert "wt_candidate_websocket_watches_count=5" in result["derived_from"]

    def test_candidate_watch_linear(self):
        result = classify_topology_for_launch(
            None,
            subprov_wallet="SP1",
            treasury_wallet=None,
            candidate_watch_counts={"SP1": 1},
        )
        assert result["topology"] == LINEAR
        assert "wt_candidate_websocket_watches_count=1" in result["derived_from"]

    def test_falls_back_to_provisioning_edges_when_no_candidate_watch_data(self):
        """When candidate_watch_counts has no entry for the subprov, the
        EXISTING sibling_counts (wt_provisioning_edges) logic must fire
        exactly as before -- the fallback path is untouched."""
        result = classify_topology_for_launch(
            None,
            subprov_wallet="SP1",
            treasury_wallet=None,
            candidate_watch_counts={},  # no data
            sibling_counts={"SP1": 3},
        )
        assert result["topology"] == FAN_OUT
        assert "wt_provisioning_edges_sibling_count=3" in result["derived_from"]

    def test_falls_back_to_walkback_when_neither_source_has_data(self):
        result = classify_topology_for_launch(
            None,
            subprov_wallet="SP1",
            treasury_wallet=None,
            candidate_watch_counts={},
            sibling_counts={},
            walkback_evidence={"depth": 1, "parents": {"SP1"}},
            walkback_fanout_counts={"SP1": 2},
        )
        assert result["topology"] == FAN_OUT
        assert "selected_walkback_parent_fanout" in result["derived_from"]

    def test_unknown_when_no_evidence_anywhere(self):
        result = classify_topology_for_launch(
            None,
            subprov_wallet="SP1",
            treasury_wallet=None,
            candidate_watch_counts={},
            sibling_counts={},
        )
        assert result["topology"] == UNKNOWN

    def test_mesh_still_takes_priority_over_candidate_watch(self):
        """Mesh detection (step 4) must remain entirely unaffected --
        it runs BEFORE the candidate-watch check."""
        result = classify_topology_for_launch(
            None,
            subprov_wallet="SP1",
            treasury_wallet="T1",
            candidate_watch_counts={"SP1": 10},  # would be FAN_OUT otherwise
            mesh_treasuries={"T1"},
        )
        assert result["topology"] == MESH

    def test_multi_level_fan_out_still_takes_priority(self):
        """Multi-Level Fan-Out (steps 1/3) must remain entirely
        unaffected by the new candidate-watch check."""
        result = classify_topology_for_launch(
            None,
            subprov_wallet="SP1",
            treasury_wallet=None,
            candidate_watch_counts={"SP1": 10},
            multi_level_subprovs={"SP1"},
        )
        assert result["topology"] == MULTI_LEVEL_FAN_OUT

    def test_no_subprov_no_treasury_still_unknown(self):
        result = classify_topology_for_launch(
            None,
            subprov_wallet=None,
            treasury_wallet=None,
            candidate_watch_counts={"SHOULD_NOT_MATTER": 99},
        )
        assert result["topology"] == UNKNOWN

    def test_treasury_direct_no_subprov_still_linear(self):
        result = classify_topology_for_launch(
            None,
            subprov_wallet=None,
            treasury_wallet="T1",
            candidate_watch_counts={},
        )
        assert result["topology"] == LINEAR
        assert result["derived_from"] == "treasury_direct_no_subprov"

    def test_missing_candidate_watch_counts_argument_defaults_safely(self):
        """Omitting candidate_watch_counts entirely (backward
        compatibility with any caller not yet updated) must not crash
        and must fall through to the existing sibling_counts logic."""
        result = classify_topology_for_launch(
            None,
            subprov_wallet="SP1",
            treasury_wallet=None,
            sibling_counts={"SP1": 2},
        )
        assert result["topology"] == FAN_OUT
        assert "wt_provisioning_edges_sibling_count=2" in result["derived_from"]


class TestIndependenceFromCampaign:
    def test_funding_topology_source_has_no_campaign_import(self):
        """X65.8 Phase 5 / X65.10 Phase 2's explicit architectural
        requirement: Topology must NEVER import, call, or read from
        campaign_classification.py. A regression here would silently
        reintroduce the exact anti-pattern the task forbids
        (Observed Evidence -> Campaign -> Topology)."""
        source = open("src/ops/funding_topology.py").read()
        assert "import campaign_classification" not in source
        assert "from src.ops.campaign_classification" not in source
        assert "from src.ops import campaign_classification" not in source

    def test_funding_topology_never_reads_a_campaign_field(self):
        """No reference to records[...]['campaign'] or a campaign-shaped
        key anywhere in the classifier."""
        source = open("src/ops/funding_topology.py").read()
        assert '"campaign"' not in source
        assert "'campaign'" not in source

    def test_classify_topology_signature_has_no_campaign_parameter(self):
        """Structural guard: classify_topology_for_launch() must not
        accept a campaign-related keyword argument."""
        import inspect
        sig = inspect.signature(classify_topology_for_launch)
        for name in sig.parameters:
            assert "campaign" not in name.lower()


class TestConservation:
    def test_build_topology_classification_conserves_with_candidate_watch_evidence(self, tmp_path):
        db = _build_ops_db(tmp_path)
        conn = sqlite3.connect(db)
        conn.execute("INSERT INTO wt_watchtower_launches VALUES ('M1','SP1',NULL,1000)")
        conn.execute("INSERT INTO wt_attribution_outcomes VALUES ('M1','{}', 1000)")
        conn.executemany(
            "INSERT INTO wt_candidate_websocket_watches VALUES (?,?)",
            [("C1", "SP1"), ("C2", "SP1")],
        )
        conn.execute("INSERT INTO wt_watchtower_launches VALUES ('M2','SP2',NULL,1000)")
        conn.execute("INSERT INTO wt_attribution_outcomes VALUES ('M2','{}', 1000)")
        conn.execute("INSERT INTO wt_candidate_websocket_watches VALUES ('C3','SP2')")
        conn.execute("INSERT INTO wt_attribution_outcomes VALUES ('M3','{}', 1000)")
        conn.execute("INSERT INTO token_analysis VALUES ('M3','1000')")
        conn.commit()
        conn.close()

        result = build_topology_classification(db, db, window_seconds=365 * 86400, now=2000)

        assert result["conserved"] is True
        assert result["total_launches"] == 3
        assert sum(t["count"] for t in result["topologies"]) == 3
        assert result["assignments"]["M1"]["topology"] == FAN_OUT
        assert result["assignments"]["M2"]["topology"] == LINEAR
        assert result["assignments"]["M3"]["topology"] == UNKNOWN

    def test_every_topology_value_is_from_the_fixed_order(self, tmp_path):
        db = _build_ops_db(tmp_path)
        conn = sqlite3.connect(db)
        conn.execute("INSERT INTO wt_watchtower_launches VALUES ('M1','SP1',NULL,1000)")
        conn.execute("INSERT INTO wt_attribution_outcomes VALUES ('M1','{}', 1000)")
        conn.executemany(
            "INSERT INTO wt_candidate_websocket_watches VALUES (?,?)",
            [("C1", "SP1"), ("C2", "SP1")],
        )
        conn.commit()
        conn.close()

        result = build_topology_classification(db, db, window_seconds=365 * 86400, now=2000)
        for a in result["assignments"].values():
            assert a["topology"] in TOPOLOGY_ORDER


class TestNoForcedWatchtowerFanOut:
    """X65.10 Phase 4's explicit requirement: do not force a WATCHTOWER-
    campaign launch into FAN_OUT simply because it belongs to the
    Campaign bucket -- Topology must reach FAN_OUT only via its OWN
    observed evidence, never via a campaign field."""

    def test_single_candidate_watch_recipient_stays_linear_even_if_otherwise_watchtower_shaped(self):
        # A launch whose subprov shows only ONE recorded recipient must
        # be LINEAR, regardless of any other context -- Topology has no
        # concept of "this is a WATCHTOWER launch" to override its own
        # evidence-based decision.
        result = classify_topology_for_launch(
            None,
            subprov_wallet="SP1",
            treasury_wallet=None,
            candidate_watch_counts={"SP1": 1},
        )
        assert result["topology"] == LINEAR

    def test_no_evidence_at_all_stays_unknown_even_for_a_known_subprov(self):
        result = classify_topology_for_launch(
            None,
            subprov_wallet="SP_WITH_NO_DATA",
            treasury_wallet=None,
            candidate_watch_counts={},
            sibling_counts={},
        )
        assert result["topology"] == UNKNOWN


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
