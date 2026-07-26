"""X65.7 — Implement Exclusive Campaign Stage in Discovery.

Tests src/ops/campaign_classification.py's read-only, mutually-exclusive
Campaign classification (WATCHTOWER / OTHER_CAMPAIGN / UNCLASSIFIED),
against a minimal in-memory schema mirroring the real wt_watchtower_
launches / wt_attribution_outcomes / wt_active_subprov_sessions /
wt_candidate_websocket_watches / wt_confirmed_treasuries tables.

Must never: write to any table, gate WATCHTOWER membership on treasury
resolution, double-count a launch across buckets, or drop a launch from
every bucket.
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from src.ops.campaign_classification import (
    CAMPAIGN_ORDER,
    CONFIDENCE_BASELINE,
    CONFIDENCE_HIGH,
    CONFIDENCE_MEDIUM,
    OTHER_CAMPAIGN,
    TREASURY_TIER_CONFIRMED,
    TREASURY_TIER_NEW,
    TREASURY_TIER_UNKNOWN,
    UNCLASSIFIED,
    WATCHTOWER,
    build_campaign_classification,
    classify_campaign_for_launch,
    treasury_tier_for_resolution,
)
from src.ops.treasury_resolution import (
    STATUS_KNOWN_TREASURY,
    STATUS_NO_SUBPROV,
    STATUS_UNKNOWN_TREASURY_CANDIDATE,
    STATUS_UNRESOLVED,
)

VALID_SIG = "pokoBD8CxcaQCbcqMCyVwrVSvUmpYoSQAkRD5GzoQpf44MUgQQDdr1ccHfZyaNhrJMeZEXNLYePpihsyQwJMw4J"


def _build_ops_db(tmp_path):
    db = tmp_path / "ops.db"
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE wt_watchtower_launches (
            mint TEXT PRIMARY KEY, creator_wallet TEXT, subprov_wallet TEXT,
            treasury_wallet TEXT, wrap_close_signature TEXT
        );
        CREATE TABLE wt_attribution_outcomes (
            mint TEXT PRIMARY KEY, outcome_type TEXT, terminal_entity TEXT, evidence_json TEXT
        );
        CREATE TABLE wt_active_subprov_sessions (
            subprov_wallet TEXT, treasury_wallet TEXT, funding_signature TEXT,
            funding_amount REAL, funding_time INTEGER, funding_mechanism TEXT,
            state TEXT, open_reason TEXT
        );
        CREATE TABLE wt_candidate_websocket_watches (
            candidate_wallet TEXT, subprov_wallet TEXT, wrap_wallet TEXT,
            detected_at INTEGER
        );
        CREATE TABLE wt_confirmed_treasuries (
            treasury TEXT PRIMARY KEY, method TEXT, confidence TEXT,
            confirmed_at INTEGER, provenance TEXT
        );
        CREATE TABLE wt_ops_v2_wallets (
            operation_uuid TEXT, wallet TEXT, role TEXT, last_seen INTEGER
        );
    """)
    conn.commit()
    conn.close()
    return str(db)


def _insert_launch(conn, mint, creator, subprov, treasury=None, sig=VALID_SIG):
    conn.execute(
        "INSERT INTO wt_watchtower_launches VALUES (?,?,?,?,?)",
        (mint, creator, subprov, treasury, sig),
    )


def _insert_attribution(conn, mint, funder, subprovisioners=None, outcome_type="INSUFFICIENT_EVIDENCE"):
    conn.execute(
        "INSERT INTO wt_attribution_outcomes VALUES (?,?,?,?)",
        (mint, outcome_type, funder, json.dumps({"subprovisioners": subprovisioners or []})),
    )


def _insert_session(conn, subprov, treasury=None, sig=VALID_SIG):
    conn.execute(
        "INSERT INTO wt_active_subprov_sessions VALUES (?,?,?,?,?,?,?,?)",
        (subprov, treasury, sig, 650.0, 1000, "WSOL_WRAP_CLOSE", "EXPIRED", "PROVISION_CANDIDATE"),
    )


def _insert_watch(conn, candidate, subprov, wrap_wallet=None, detected_at=1000):
    conn.execute(
        "INSERT INTO wt_candidate_websocket_watches VALUES (?,?,?,?)",
        (candidate, subprov, wrap_wallet, detected_at),
    )


def _insert_confirmed_treasury(conn, treasury):
    conn.execute(
        "INSERT INTO wt_confirmed_treasuries VALUES (?,?,?,?,?)",
        (treasury, "3SIGNAL", "CONFIRMED", 500, "TEST_PROVENANCE"),
    )


# ---------------------------------------------------------------------------
# classify_campaign_for_launch() -- pure decision function
# ---------------------------------------------------------------------------

class TestClassifyCampaignForLaunch:
    def test_fresh_creator_plus_wrap_close_is_watchtower(self):
        result = classify_campaign_for_launch(
            creator_identity="FRESH_CREATOR",
            wrap_close_evidence={"subprov_wallet": "SP1", "source": "wt_watchtower_launches"},
            has_other_funding_lineage=True,
        )
        assert result["campaign"] == WATCHTOWER

    def test_non_fresh_creator_with_lineage_is_other_campaign(self):
        result = classify_campaign_for_launch(
            creator_identity="SERIAL_DEPLOYER",
            wrap_close_evidence=None,
            has_other_funding_lineage=True,
        )
        assert result["campaign"] == OTHER_CAMPAIGN

    def test_non_fresh_creator_no_lineage_is_unclassified(self):
        result = classify_campaign_for_launch(
            creator_identity="SERIAL_DEPLOYER",
            wrap_close_evidence=None,
            has_other_funding_lineage=False,
        )
        assert result["campaign"] == UNCLASSIFIED

    def test_fresh_creator_no_wrap_close_with_lineage_is_other_campaign(self):
        result = classify_campaign_for_launch(
            creator_identity="FRESH_CREATOR",
            wrap_close_evidence=None,
            has_other_funding_lineage=True,
        )
        assert result["campaign"] == OTHER_CAMPAIGN

    def test_fresh_creator_no_wrap_close_no_lineage_is_unclassified(self):
        result = classify_campaign_for_launch(
            creator_identity="FRESH_CREATOR",
            wrap_close_evidence=None,
            has_other_funding_lineage=False,
        )
        assert result["campaign"] == UNCLASSIFIED

    def test_missing_creator_identity_never_crashes_and_is_not_watchtower(self):
        result = classify_campaign_for_launch(
            creator_identity=None,
            wrap_close_evidence={"subprov_wallet": "SP1", "source": "x"},
            has_other_funding_lineage=False,
        )
        assert result["campaign"] == UNCLASSIFIED


class TestMandatoryCriteriaTolerateIncompleteEvidence:
    """X65.5/X65.6 Phase 3's core requirement: only 2 signals are mandatory;
    every other signal's absence must never exclude a launch from WATCHTOWER."""

    def _watchtower(self, **overrides):
        kwargs = dict(
            creator_identity="FRESH_CREATOR",
            wrap_close_evidence={"subprov_wallet": "SP1", "source": "wt_watchtower_launches"},
            has_other_funding_lineage=True,
            fanout_evidence=None,
            treasury_tier=None,
        )
        kwargs.update(overrides)
        return classify_campaign_for_launch(**kwargs)

    def test_watchtower_with_zero_confidence_signals(self):
        result = self._watchtower()
        assert result["campaign"] == WATCHTOWER
        assert result["confidence"] == CONFIDENCE_BASELINE

    def test_watchtower_with_empty_fanout_evidence_dict(self):
        result = self._watchtower(fanout_evidence={})
        assert result["campaign"] == WATCHTOWER
        assert result["confidence"] == CONFIDENCE_BASELINE

    def test_watchtower_unaffected_by_unknown_treasury(self):
        result = self._watchtower(treasury_tier=TREASURY_TIER_UNKNOWN)
        assert result["campaign"] == WATCHTOWER
        assert result["confidence"] == CONFIDENCE_BASELINE

    def test_watchtower_unaffected_by_new_treasury(self):
        """The task's explicit requirement: New/Unknown treasury launches
        must still be WATCHTOWER-eligible when the fingerprint matches."""
        result = self._watchtower(treasury_tier=TREASURY_TIER_NEW)
        assert result["campaign"] == WATCHTOWER
        assert result["confidence"] == CONFIDENCE_MEDIUM  # treasury_known raises confidence, never gates


class TestConfidenceTiers:
    def _watchtower(self, **overrides):
        kwargs = dict(
            creator_identity="FRESH_CREATOR",
            wrap_close_evidence={"subprov_wallet": "SP1", "source": "wt_watchtower_launches"},
            has_other_funding_lineage=True,
        )
        kwargs.update(overrides)
        return classify_campaign_for_launch(**kwargs)

    def test_high_requires_all_three_fanout_signals(self):
        result = self._watchtower(fanout_evidence={
            "fan_out_observed": True, "single_use_confirmed": True, "not_reused_confirmed": True,
        })
        assert result["confidence"] == CONFIDENCE_HIGH

    def test_medium_with_only_fanout_observed(self):
        result = self._watchtower(fanout_evidence={
            "fan_out_observed": True, "single_use_confirmed": False, "not_reused_confirmed": False,
        })
        assert result["confidence"] == CONFIDENCE_MEDIUM

    def test_medium_with_only_single_use_confirmed(self):
        result = self._watchtower(fanout_evidence={
            "fan_out_observed": False, "single_use_confirmed": True, "not_reused_confirmed": False,
        })
        assert result["confidence"] == CONFIDENCE_MEDIUM

    def test_baseline_with_no_signals_at_all(self):
        result = self._watchtower(fanout_evidence={
            "fan_out_observed": False, "single_use_confirmed": False, "not_reused_confirmed": False,
        })
        assert result["confidence"] == CONFIDENCE_BASELINE

    def test_confidence_is_none_for_non_watchtower(self):
        result = classify_campaign_for_launch(
            creator_identity="SERIAL_DEPLOYER",
            wrap_close_evidence=None,
            has_other_funding_lineage=True,
        )
        assert result["confidence"] is None


class TestTreasuryTierMapping:
    def test_known_treasury_maps_to_confirmed(self):
        assert treasury_tier_for_resolution({"status": STATUS_KNOWN_TREASURY}) == TREASURY_TIER_CONFIRMED

    def test_unknown_treasury_candidate_maps_to_new(self):
        assert treasury_tier_for_resolution({"status": STATUS_UNKNOWN_TREASURY_CANDIDATE}) == TREASURY_TIER_NEW

    def test_unresolved_maps_to_unknown(self):
        assert treasury_tier_for_resolution({"status": STATUS_UNRESOLVED}) == TREASURY_TIER_UNKNOWN

    def test_no_subprov_maps_to_unknown(self):
        assert treasury_tier_for_resolution({"status": STATUS_NO_SUBPROV}) == TREASURY_TIER_UNKNOWN


# ---------------------------------------------------------------------------
# build_campaign_classification() -- batch entry point against a real DB
# ---------------------------------------------------------------------------

class TestBuildCampaignClassification:
    def test_empty_records_returns_conserved_zero_counts(self, tmp_path):
        ops_db = _build_ops_db(tmp_path)
        result = build_campaign_classification(ops_db, {})
        assert result["campaign_conserved"] is True
        assert result["assignments"] == {}
        assert {c["campaign"] for c in result["campaign_summary"]} == set(CAMPAIGN_ORDER)
        assert all(c["count"] == 0 for c in result["campaign_summary"])

    def test_watchtower_launch_via_wt_watchtower_launches(self, tmp_path):
        ops_db = _build_ops_db(tmp_path)
        conn = sqlite3.connect(ops_db)
        _insert_launch(conn, "MINT1", "CREATOR1", "SP1")
        conn.commit()
        conn.close()

        records = {"MINT1": {"creator_identity": "FRESH_CREATOR"}}
        result = build_campaign_classification(ops_db, records)

        assert result["assignments"]["MINT1"]["campaign"] == WATCHTOWER
        assert result["campaign_conserved"] is True

    def test_watchtower_launch_via_evidence_json_with_real_session(self, tmp_path):
        """A walkback-resolved mint (not in wt_watchtower_launches) with a
        real, session-backed subprov mention should also qualify."""
        ops_db = _build_ops_db(tmp_path)
        conn = sqlite3.connect(ops_db)
        _insert_attribution(conn, "MINT2", "FUNDER2", subprovisioners=["SP2"])
        _insert_session(conn, "SP2")
        conn.commit()
        conn.close()

        records = {"MINT2": {"creator_identity": "FRESH_CREATOR"}}
        result = build_campaign_classification(ops_db, records)

        assert result["assignments"]["MINT2"]["campaign"] == WATCHTOWER

    def test_bare_evidence_json_mention_without_session_is_not_watchtower(self, tmp_path):
        """A bare subprovisioners mention with NO wt_active_subprov_sessions
        row must not be treated as confirmed wrap-close evidence -- mirrors
        treasury_resolution.py's own CONFIRMED/PROBABLE discipline."""
        ops_db = _build_ops_db(tmp_path)
        conn = sqlite3.connect(ops_db)
        _insert_attribution(conn, "MINT3", "FUNDER3", subprovisioners=["SP3"])
        conn.commit()
        conn.close()

        records = {"MINT3": {"creator_identity": "FRESH_CREATOR"}}
        result = build_campaign_classification(ops_db, records)

        assert result["assignments"]["MINT3"]["campaign"] == OTHER_CAMPAIGN

    def test_no_lineage_at_all_is_unclassified(self, tmp_path):
        ops_db = _build_ops_db(tmp_path)
        records = {"MINT4": {"creator_identity": "FRESH_CREATOR"}}
        result = build_campaign_classification(ops_db, records)
        assert result["assignments"]["MINT4"]["campaign"] == UNCLASSIFIED

    def test_new_treasury_launch_is_still_watchtower(self, tmp_path):
        """Core task requirement: a launch funded by a never-before-seen
        treasury must still be WATCHTOWER-eligible when the fingerprint
        matches -- Campaign never gates on treasury resolution."""
        ops_db = _build_ops_db(tmp_path)
        conn = sqlite3.connect(ops_db)
        _insert_launch(conn, "MINT5", "CREATOR5", "SP5")
        _insert_attribution(conn, "MINT5", "SP5")
        _insert_session(conn, "SP5", treasury="TREASURY_NEVER_SEEN")
        # deliberately NOT inserting TREASURY_NEVER_SEEN into wt_confirmed_treasuries
        conn.commit()
        conn.close()

        records = {"MINT5": {"creator_identity": "FRESH_CREATOR"}}
        result = build_campaign_classification(ops_db, records)

        assignment = result["assignments"]["MINT5"]
        assert assignment["campaign"] == WATCHTOWER
        assert assignment["evidence"]["treasury_tier"] == TREASURY_TIER_NEW

    def test_confirmed_treasury_launch_is_watchtower_and_tagged_confirmed(self, tmp_path):
        ops_db = _build_ops_db(tmp_path)
        conn = sqlite3.connect(ops_db)
        _insert_launch(conn, "MINT6", "CREATOR6", "SP6")
        _insert_attribution(conn, "MINT6", "SP6")
        _insert_session(conn, "SP6", treasury="TREASURY_KNOWN")
        _insert_confirmed_treasury(conn, "TREASURY_KNOWN")
        conn.commit()
        conn.close()

        records = {"MINT6": {"creator_identity": "FRESH_CREATOR"}}
        result = build_campaign_classification(ops_db, records)

        assignment = result["assignments"]["MINT6"]
        assert assignment["campaign"] == WATCHTOWER
        assert assignment["evidence"]["treasury_tier"] == TREASURY_TIER_CONFIRMED

    def test_unknown_treasury_launch_is_still_watchtower(self, tmp_path):
        """A launch whose funder has NO subprov session at all (fully
        unresolved treasury) must still qualify for WATCHTOWER if it has
        its own direct wrap-close evidence."""
        ops_db = _build_ops_db(tmp_path)
        conn = sqlite3.connect(ops_db)
        _insert_launch(conn, "MINT7", "CREATOR7", "SP7")
        # No wt_attribution_outcomes / wt_active_subprov_sessions row at all
        # for SP7's own upstream treasury -- fully unresolved.
        conn.commit()
        conn.close()

        records = {"MINT7": {"creator_identity": "FRESH_CREATOR"}}
        result = build_campaign_classification(ops_db, records)

        assignment = result["assignments"]["MINT7"]
        assert assignment["campaign"] == WATCHTOWER
        assert assignment["evidence"]["treasury_tier"] == TREASURY_TIER_UNKNOWN

    def test_fanout_confidence_signals_computed_from_candidate_watches(self, tmp_path):
        ops_db = _build_ops_db(tmp_path)
        conn = sqlite3.connect(ops_db)
        _insert_launch(conn, "MINT8", "CREATOR8", "SP8")
        # SubProv SP8 fanned out to 3 distinct candidates via 1 wrap wallet
        # each -- single-use and not-reused both hold.
        _insert_watch(conn, "CREATOR8", "SP8", wrap_wallet="WRAP8")
        _insert_watch(conn, "SIBLING_A", "SP8", wrap_wallet="WRAPA")
        _insert_watch(conn, "SIBLING_B", "SP8", wrap_wallet="WRAPB")
        conn.commit()
        conn.close()

        records = {"MINT8": {"creator_identity": "FRESH_CREATOR"}}
        result = build_campaign_classification(ops_db, records)

        assignment = result["assignments"]["MINT8"]
        assert assignment["campaign"] == WATCHTOWER
        assert assignment["evidence"]["fan_out_observed"] is True
        assert assignment["evidence"]["single_use_confirmed"] is True
        assert assignment["evidence"]["not_reused_confirmed"] is True
        assert assignment["confidence"] == CONFIDENCE_HIGH

    def test_reused_wrap_wallet_does_not_confirm_not_reused(self, tmp_path):
        ops_db = _build_ops_db(tmp_path)
        conn = sqlite3.connect(ops_db)
        _insert_launch(conn, "MINT9", "CREATOR9", "SP9")
        _insert_watch(conn, "CREATOR9", "SP9", wrap_wallet="SHARED_WRAP")
        # The SAME wrap wallet also used by a different subprov -- reused.
        _insert_watch(conn, "OTHER_CANDIDATE", "SP_OTHER", wrap_wallet="SHARED_WRAP")
        conn.commit()
        conn.close()

        records = {"MINT9": {"creator_identity": "FRESH_CREATOR"}}
        result = build_campaign_classification(ops_db, records)

        assignment = result["assignments"]["MINT9"]
        assert assignment["evidence"]["not_reused_confirmed"] is False


# ---------------------------------------------------------------------------
# Exclusivity / population-conservation invariants (X65.6 Phase 5A/5B)
# ---------------------------------------------------------------------------

class TestExclusivityAndConservation:
    def test_every_launch_gets_exactly_one_campaign(self, tmp_path):
        ops_db = _build_ops_db(tmp_path)
        conn = sqlite3.connect(ops_db)
        _insert_launch(conn, "WT1", "C1", "SP1")
        _insert_attribution(conn, "OTHER1", "FUNDER_X")
        conn.commit()
        conn.close()

        records = {
            "WT1": {"creator_identity": "FRESH_CREATOR"},
            "OTHER1": {"creator_identity": "SERIAL_DEPLOYER"},
            "UNCL1": {"creator_identity": "SERIAL_DEPLOYER"},
        }
        result = build_campaign_classification(ops_db, records)

        for mint in records:
            assert mint in result["assignments"]
            assert result["assignments"][mint]["campaign"] in CAMPAIGN_ORDER

    def test_population_conservation_holds_across_a_mixed_population(self, tmp_path):
        ops_db = _build_ops_db(tmp_path)
        conn = sqlite3.connect(ops_db)
        for i in range(5):
            _insert_launch(conn, f"WT{i}", f"C{i}", f"SP{i}")
        for i in range(5):
            _insert_attribution(conn, f"OTHER{i}", f"FUNDER{i}")
        conn.commit()
        conn.close()

        records = {}
        for i in range(5):
            records[f"WT{i}"] = {"creator_identity": "FRESH_CREATOR"}
        for i in range(5):
            records[f"OTHER{i}"] = {"creator_identity": "SERIAL_DEPLOYER"}
        for i in range(5):
            records[f"UNCL{i}"] = {"creator_identity": "SERIAL_DEPLOYER"}

        result = build_campaign_classification(ops_db, records)

        assert result["campaign_conserved"] is True
        total_from_summary = sum(c["count"] for c in result["campaign_summary"])
        assert total_from_summary == len(records) == 15

    def test_campaign_summary_counts_match_assignments(self, tmp_path):
        ops_db = _build_ops_db(tmp_path)
        conn = sqlite3.connect(ops_db)
        _insert_launch(conn, "WT1", "C1", "SP1")
        conn.commit()
        conn.close()

        records = {
            "WT1": {"creator_identity": "FRESH_CREATOR"},
            "UNCL1": {"creator_identity": "SERIAL_DEPLOYER"},
        }
        result = build_campaign_classification(ops_db, records)

        counted = {c["campaign"]: c["count"] for c in result["campaign_summary"]}
        actual = {}
        for a in result["assignments"].values():
            actual[a["campaign"]] = actual.get(a["campaign"], 0) + 1
        for campaign in CAMPAIGN_ORDER:
            assert counted[campaign] == actual.get(campaign, 0)

    def test_no_launch_appears_under_two_campaigns(self, tmp_path):
        """Structural proof: assignments is a dict keyed by mint, so a mint
        cannot appear twice -- but this test additionally proves the SUM
        across campaign_summary buckets never exceeds the assignment count,
        which would be the symptom of any accidental double-classification
        logic (e.g. a bug that both continued and returned early)."""
        ops_db = _build_ops_db(tmp_path)
        conn = sqlite3.connect(ops_db)
        _insert_launch(conn, "WT1", "C1", "SP1")
        conn.commit()
        conn.close()

        records = {"WT1": {"creator_identity": "FRESH_CREATOR"}}
        result = build_campaign_classification(ops_db, records)

        assert len(result["assignments"]) == len(records)
        assert sum(c["count"] for c in result["campaign_summary"]) == len(records)


# ---------------------------------------------------------------------------
# New-launch vs reclassification accounting (X65.6 Phase 5B)
# ---------------------------------------------------------------------------

class TestNewLaunchAndReclassificationAccounting:
    def test_adding_a_new_launch_increases_total_and_exactly_one_bucket(self, tmp_path):
        ops_db = _build_ops_db(tmp_path)
        conn = sqlite3.connect(ops_db)
        _insert_launch(conn, "WT1", "C1", "SP1")
        conn.commit()
        conn.close()

        records_before = {
            "WT1": {"creator_identity": "FRESH_CREATOR"},
            "UNCL1": {"creator_identity": "SERIAL_DEPLOYER"},
        }
        before = build_campaign_classification(ops_db, records_before)
        counts_before = {c["campaign"]: c["count"] for c in before["campaign_summary"]}
        total_before = sum(counts_before.values())

        # A NEW launch arrives, immediately recognised as WATCHTOWER.
        conn = sqlite3.connect(ops_db)
        _insert_launch(conn, "WT2", "C2", "SP2")
        conn.commit()
        conn.close()

        records_after = dict(records_before)
        records_after["WT2"] = {"creator_identity": "FRESH_CREATOR"}
        after = build_campaign_classification(ops_db, records_after)
        counts_after = {c["campaign"]: c["count"] for c in after["campaign_summary"]}
        total_after = sum(counts_after.values())

        assert total_after == total_before + 1
        assert counts_after[WATCHTOWER] == counts_before[WATCHTOWER] + 1
        assert counts_after[OTHER_CAMPAIGN] == counts_before[OTHER_CAMPAIGN]
        assert counts_after[UNCLASSIFIED] == counts_before[UNCLASSIFIED]

    def test_reclassification_moves_between_buckets_without_changing_total(self, tmp_path):
        """A previously UNCLASSIFIED launch gets new evidence (a
        wt_watchtower_launches row appears for it) and is re-evaluated --
        the total must stay the same; exactly one bucket loses what
        exactly one other bucket gains."""
        ops_db = _build_ops_db(tmp_path)
        records = {
            "TARGET": {"creator_identity": "SERIAL_DEPLOYER"},
            "STABLE1": {"creator_identity": "SERIAL_DEPLOYER"},
        }
        before = build_campaign_classification(ops_db, records)
        counts_before = {c["campaign"]: c["count"] for c in before["campaign_summary"]}
        assert before["assignments"]["TARGET"]["campaign"] == UNCLASSIFIED

        # New evidence arrives for TARGET: it now has wrap-close provisioning
        # AND its creator_identity is re-evaluated as FRESH_CREATOR.
        conn = sqlite3.connect(ops_db)
        _insert_launch(conn, "TARGET", "CREATOR_T", "SP_T")
        conn.commit()
        conn.close()

        records_after = {
            "TARGET": {"creator_identity": "FRESH_CREATOR"},
            "STABLE1": {"creator_identity": "SERIAL_DEPLOYER"},
        }
        after = build_campaign_classification(ops_db, records_after)
        counts_after = {c["campaign"]: c["count"] for c in after["campaign_summary"]}

        total_before = sum(counts_before.values())
        total_after = sum(counts_after.values())
        assert total_after == total_before  # population unchanged

        assert after["assignments"]["TARGET"]["campaign"] == WATCHTOWER
        assert counts_after[WATCHTOWER] == counts_before[WATCHTOWER] + 1
        assert counts_after[UNCLASSIFIED] == counts_before[UNCLASSIFIED] - 1
        assert counts_after[OTHER_CAMPAIGN] == counts_before[OTHER_CAMPAIGN]


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
