"""X27.9 — Make Repeat Creator Classification Authoritative.

Root cause (X27.8): evaluate_launcher_profile() measured a creator's
observation span from creator_funders.first_detected_at (a funder-discovery
timestamp) whenever that table had any matching rows, only falling back to
the more-accurate token_analysis-derived span if creator_funders returned
nothing. A creator with a genuine multi-month launch history but whose
funder rows were all backfilled within the same few seconds therefore
measured observation_seconds near zero and failed the Repeat Creator gate,
falling through to a lower-priority bucket (Burst Launches in the X27.8
case) despite satisfying the rule's own stated intent (sustained activity,
not just launch count).

Fix: the observation span is now derived exclusively from the creator's own
token_analysis launch records (Phase 1/3), with deterministic timestamp
normalization across mixed epoch/ISO-8601 formats (Phase 4).
creator_funders is retained only for historical_funder_count and
material_infrastructure_change -- it no longer supplies first_seen/
last_seen/observation_seconds.

These tests exercise assign_bucket()/evaluate_launcher_profile() directly
against isolated in-memory-equivalent sqlite fixtures -- no dependency on
production data.
"""
from __future__ import annotations

import sqlite3
import time

import pytest

from src.ops.attribution_outcome import (
    evaluate_launcher_profile, MIN_LAUNCHER_HISTORY, MIN_LAUNCHER_OBSERVATION_SECONDS,
)
from src.ops.investigation_pipeline import (
    build_pipeline_health,
    KNOWN_OPERATION, KNOWN_INFRASTRUCTURE, REPEAT_CREATOR, RAPID_BIRTH_LAUNCH,
    BURST_LAUNCH, UNKNOWN_INFRASTRUCTURE, LINEAGE_GAP, INSUFFICIENT_EVIDENCE,
)


@pytest.fixture
def db_factory(tmp_path):
    def make(outcome_rows, token_rows=None, funder_rows=None, watchtower_rows=None, now=None):
        now = now or int(time.time())
        ops_path = str(tmp_path / f"ops_{time.time_ns()}.db")
        core_path = str(tmp_path / f"core_{time.time_ns()}.db")

        ops_conn = sqlite3.connect(ops_path)
        ops_conn.execute(
            "CREATE TABLE wt_attribution_outcomes (mint TEXT, outcome_type TEXT, completed_at REAL)")
        for r in outcome_rows:
            ops_conn.execute(
                "INSERT INTO wt_attribution_outcomes (mint, outcome_type, completed_at) VALUES (?,?,?)",
                (r["mint"], r["outcome_type"], r.get("completed_at", now)),
            )
        ops_conn.execute("CREATE TABLE operator_entities (entity_address TEXT)")
        ops_conn.execute("CREATE TABLE wt_wrap_close_candidates (creator TEXT, funded_at REAL, detected_at REAL)")
        ops_conn.execute("CREATE TABLE wt_creator_birth_launch (creator TEXT, funded_at REAL, measured_at REAL)")
        ops_conn.execute("CREATE TABLE wt_candidate_websocket_watches (candidate_wallet TEXT)")
        ops_conn.execute(
            "CREATE TABLE wt_watchtower_launches (mint TEXT, create_time REAL, birth_to_launch_seconds REAL)")
        ops_conn.commit()
        ops_conn.close()

        core_conn = sqlite3.connect(core_path)
        core_conn.execute(
            "CREATE TABLE token_analysis (mint TEXT, pf_ws_creator TEXT, earliest_tx_creator TEXT, "
            "created_at TEXT, migrated_at REAL)"
        )
        for r in (token_rows or []):
            core_conn.execute(
                "INSERT INTO token_analysis (mint, pf_ws_creator, earliest_tx_creator, created_at, migrated_at) "
                "VALUES (?,?,?,?,?)",
                (r["mint"], r.get("pf_ws_creator"), r.get("earliest_tx_creator"), r.get("created_at"),
                 r.get("migrated_at", now)),
            )
        core_conn.execute(
            "CREATE TABLE creator_funders (creator_address TEXT, funder_address TEXT, first_detected_at TEXT)")
        for r in (funder_rows or []):
            core_conn.execute(
                "INSERT INTO creator_funders (creator_address, funder_address, first_detected_at) VALUES (?,?,?)",
                (r["creator_address"], r["funder_address"], r["first_detected_at"]),
            )
        core_conn.commit()
        core_conn.close()
        return ops_path, core_path
    return make


def _conns(ops_path, core_path):
    ops_conn = sqlite3.connect(ops_path)
    ops_conn.row_factory = sqlite3.Row
    core_conn = sqlite3.connect(core_path)
    core_conn.row_factory = sqlite3.Row
    return ops_conn, core_conn


# ── Core fix: observation span comes from token_analysis, not creator_funders ──

def test_established_creator_above_both_thresholds_qualifies(db_factory):
    now = int(time.time())
    long_ago = now - 20 * 86400
    token_rows = [
        {"mint": f"T{i}", "pf_ws_creator": "Creator1", "created_at": str(long_ago + i * 3 * 86400)}
        for i in range(6)
    ]
    ops_path, core_path = db_factory([], token_rows=token_rows, now=now)
    ops_conn, core_conn = _conns(ops_path, core_path)
    profile = evaluate_launcher_profile(ops_conn, core_conn, "Creator1", now=now)
    assert profile["launch_count"] >= MIN_LAUNCHER_HISTORY
    assert profile["observation_seconds"] >= MIN_LAUNCHER_OBSERVATION_SECONDS
    assert profile["established"] is True


def test_clustered_funder_rows_do_not_shrink_a_months_long_history(db_factory):
    """The exact X27.8 defect: creator_funders rows all first_detected within
    a few seconds of each other must NOT override a genuine multi-month
    token_analysis launch-history span."""
    now = int(time.time())
    ninety_days_ago = now - 90 * 86400
    token_rows = [
        {"mint": f"T{i}", "pf_ws_creator": "ClusteredFunderCreator",
         "created_at": str(ninety_days_ago + i * 9 * 86400)}
        for i in range(10)  # 10 launches spanning ~81 days (i * 9 days apart)
    ]
    # 5 funder rows, all first_detected within the same 6-second window --
    # mirrors the real production data found in X27.8.
    funder_base = now - 60 * 86400
    funder_rows = [
        {"creator_address": "ClusteredFunderCreator", "funder_address": f"F{i}",
         "first_detected_at": str(funder_base + i)}
        for i in range(5)
    ]
    ops_path, core_path = db_factory([], token_rows=token_rows, funder_rows=funder_rows, now=now)
    ops_conn, core_conn = _conns(ops_path, core_path)
    profile = evaluate_launcher_profile(ops_conn, core_conn, "ClusteredFunderCreator", now=now)
    assert profile["historical_funder_count"] == 5
    # observation_seconds must reflect the ~81-day launch history, not the
    # ~5-second funder-discovery window.
    assert profile["observation_seconds"] >= 80 * 86400
    assert profile["established"] is True


def test_high_launch_count_within_seven_days_does_not_qualify(db_factory):
    """Launch count alone is insufficient -- sustained history over time is
    still mandatory, unchanged by this fix (non-goal: do not weaken the
    threshold)."""
    now = int(time.time())
    token_rows = [
        {"mint": f"T{i}", "pf_ws_creator": "BurstyCreator", "created_at": str(now - i * 3600)}
        for i in range(20)  # 20 launches, all within the last 20 hours
    ]
    outcomes = [{"mint": "T0", "outcome_type": "INSUFFICIENT_EVIDENCE"}]
    ops_path, core_path = db_factory(outcomes, token_rows=token_rows, now=now)
    ops_conn, core_conn = _conns(ops_path, core_path)
    profile = evaluate_launcher_profile(ops_conn, core_conn, "BurstyCreator", now=now)
    assert profile["launch_count"] >= MIN_LAUNCHER_HISTORY
    assert profile["observation_seconds"] < MIN_LAUNCHER_OBSERVATION_SECONDS
    assert profile["established"] is False


# ── Phase 4: timestamp normalization ──

def test_mixed_epoch_and_iso_timestamps_normalized_correctly(db_factory):
    now = int(time.time())
    ninety_days_ago = now - 90 * 86400
    import datetime
    iso_ts = datetime.datetime.fromtimestamp(now - 86400, tz=datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    token_rows = [
        {"mint": "T0", "pf_ws_creator": "MixedFormatCreator", "created_at": str(ninety_days_ago)},
        {"mint": "T1", "pf_ws_creator": "MixedFormatCreator", "created_at": iso_ts},
        {"mint": "T2", "pf_ws_creator": "MixedFormatCreator", "created_at": str(ninety_days_ago + 86400)},
        {"mint": "T3", "pf_ws_creator": "MixedFormatCreator", "created_at": str(ninety_days_ago + 2 * 86400)},
        {"mint": "T4", "pf_ws_creator": "MixedFormatCreator", "created_at": str(ninety_days_ago + 3 * 86400)},
    ]
    ops_path, core_path = db_factory([], token_rows=token_rows, now=now)
    ops_conn, core_conn = _conns(ops_path, core_path)
    profile = evaluate_launcher_profile(ops_conn, core_conn, "MixedFormatCreator", now=now)
    # A lexical/truncating cast would corrupt the ISO row into a bogus small
    # or huge number; the real span here is ~89 days.
    assert profile["observation_seconds"] >= 88 * 86400
    assert profile["observation_seconds"] <= 91 * 86400
    assert profile["established"] is True


def test_invalid_timestamps_do_not_fabricate_history(db_factory):
    now = int(time.time())
    token_rows = [
        {"mint": "T0", "pf_ws_creator": "GarbageTimestampCreator", "created_at": "not-a-timestamp"},
        {"mint": "T1", "pf_ws_creator": "GarbageTimestampCreator", "created_at": ""},
        {"mint": "T2", "pf_ws_creator": "GarbageTimestampCreator", "created_at": None},
        {"mint": "T3", "pf_ws_creator": "GarbageTimestampCreator", "created_at": str(now)},
        {"mint": "T4", "pf_ws_creator": "GarbageTimestampCreator", "created_at": str(now - 100)},
    ]
    ops_path, core_path = db_factory([], token_rows=token_rows, now=now)
    ops_conn, core_conn = _conns(ops_path, core_path)
    profile = evaluate_launcher_profile(ops_conn, core_conn, "GarbageTimestampCreator", now=now)
    # Only 2 rows have valid, parseable timestamps (~100s apart) -- garbage
    # values must be ignored, not coerced into "now" or 0.
    assert profile["valid_launch_timestamp_count"] == 2
    assert profile["observation_seconds"] < 200
    assert profile["established"] is False


def test_fewer_than_two_valid_timestamps_fails_observation_gate(db_factory):
    now = int(time.time())
    token_rows = [
        {"mint": f"T{i}", "pf_ws_creator": "SingleTimestampCreator", "created_at": "garbage"}
        for i in range(5)
    ]
    # Only one row gets a real timestamp -- a single point cannot establish
    # a span; the gate must fail honestly rather than treat span=0 as "just
    # barely" satisfying anything, and must not fabricate a second point.
    token_rows[0]["created_at"] = str(now)
    ops_path, core_path = db_factory([], token_rows=token_rows, now=now)
    ops_conn, core_conn = _conns(ops_path, core_path)
    profile = evaluate_launcher_profile(ops_conn, core_conn, "SingleTimestampCreator", now=now)
    assert profile["valid_launch_timestamp_count"] <= 1
    assert profile["observation_seconds"] == 0
    assert profile["established"] is False


# ── Phase 5: Repeat Creator authority over every lower-priority bucket ──

def _established_creator_token_rows(creator: str, now: int, n: int = 6):
    long_ago = now - 20 * 86400
    return [
        {"mint": f"T{i}_{creator}", "pf_ws_creator": creator, "created_at": str(long_ago + i * 3 * 86400)}
        for i in range(n)
    ]


def test_repeat_creator_wins_over_burst_launch(db_factory):
    now = int(time.time())
    creator = "RepeatVsBurst"
    token_rows = _established_creator_token_rows(creator, now)
    target = token_rows[0]["mint"]
    # Add a 3-launch migration cluster within 60s so burst evidence is genuine.
    cluster_rows = [
        {"mint": f"C{i}", "pf_ws_creator": f"OtherCreator{i}", "created_at": str(now), "migrated_at": now + i * 20}
        for i in range(3)
    ]
    token_rows[0]["migrated_at"] = now
    outcomes = [{"mint": target, "outcome_type": "INSUFFICIENT_EVIDENCE"}]
    ops_path, core_path = db_factory(outcomes, token_rows=token_rows + cluster_rows, now=now)
    pipeline = build_pipeline_health(ops_path, core_path, now=now, window_seconds=86400)
    assert pipeline["assignments"][target]["bucket"] == REPEAT_CREATOR
    # Burst evidence remains visible as supplementary drill-down metadata.
    assert pipeline["assignments"][target]["secondary_evidence"]["burst_launch"] is not None


def test_repeat_creator_wins_over_rapid_birth_launch(db_factory):
    now = int(time.time())
    creator = "RepeatVsRapidBirth"
    token_rows = _established_creator_token_rows(creator, now)
    target = token_rows[0]["mint"]
    outcomes = [{"mint": target, "outcome_type": "INSUFFICIENT_EVIDENCE"}]
    ops_path, core_path = db_factory(outcomes, token_rows=token_rows, now=now)
    # Inject genuine rapid-birth evidence directly into wt_watchtower_launches.
    ops_conn = sqlite3.connect(ops_path)
    ops_conn.execute(
        "INSERT INTO wt_watchtower_launches (mint, create_time, birth_to_launch_seconds) VALUES (?,?,?)",
        (target, now, 2),
    )
    ops_conn.commit()
    ops_conn.close()
    pipeline = build_pipeline_health(ops_path, core_path, now=now, window_seconds=86400)
    assert pipeline["assignments"][target]["bucket"] == REPEAT_CREATOR
    assert pipeline["assignments"][target]["secondary_evidence"]["rapid_birth_launch"] is not None


def test_repeat_creator_wins_over_unknown_infrastructure(db_factory):
    now = int(time.time())
    creator = "RepeatVsUnknownInfra"
    token_rows = _established_creator_token_rows(creator, now)
    target = token_rows[0]["mint"]
    outcomes = [{"mint": target, "outcome_type": "UNKNOWN_INFRASTRUCTURE"}]
    ops_path, core_path = db_factory(outcomes, token_rows=token_rows, now=now)
    pipeline = build_pipeline_health(ops_path, core_path, now=now, window_seconds=86400)
    assert pipeline["assignments"][target]["bucket"] == REPEAT_CREATOR


def test_repeat_creator_wins_over_lineage_gap(db_factory):
    now = int(time.time())
    creator = "RepeatVsLineageGap"
    token_rows = _established_creator_token_rows(creator, now)
    target = token_rows[0]["mint"]
    outcomes = [{"mint": target, "outcome_type": "LINEAGE_GAP"}]
    ops_path, core_path = db_factory(outcomes, token_rows=token_rows, now=now)
    pipeline = build_pipeline_health(ops_path, core_path, now=now, window_seconds=86400)
    assert pipeline["assignments"][target]["bucket"] == REPEAT_CREATOR


def test_repeat_creator_wins_over_insufficient_evidence(db_factory):
    now = int(time.time())
    creator = "RepeatVsInsufficientEvidence"
    token_rows = _established_creator_token_rows(creator, now)
    target = token_rows[0]["mint"]
    outcomes = [{"mint": target, "outcome_type": "INSUFFICIENT_EVIDENCE"}]
    ops_path, core_path = db_factory(outcomes, token_rows=token_rows, now=now)
    pipeline = build_pipeline_health(ops_path, core_path, now=now, window_seconds=86400)
    assert pipeline["assignments"][target]["bucket"] == REPEAT_CREATOR


def test_known_operation_still_wins_over_repeat_creator(db_factory):
    now = int(time.time())
    creator = "RepeatVsKnownOperation"
    token_rows = _established_creator_token_rows(creator, now)
    target = token_rows[0]["mint"]
    outcomes = [{"mint": target, "outcome_type": "CANONICAL_OPERATOR_REACHED"}]
    ops_path, core_path = db_factory(outcomes, token_rows=token_rows, now=now)
    pipeline = build_pipeline_health(ops_path, core_path, now=now, window_seconds=86400)
    assert pipeline["assignments"][target]["bucket"] == KNOWN_OPERATION


def test_known_infrastructure_still_wins_over_repeat_creator(db_factory):
    now = int(time.time())
    creator = "RepeatVsKnownInfra"
    token_rows = _established_creator_token_rows(creator, now)
    target = token_rows[0]["mint"]
    outcomes = [{"mint": target, "outcome_type": "KNOWN_CEX_REACHED"}]
    ops_path, core_path = db_factory(outcomes, token_rows=token_rows, now=now)
    pipeline = build_pipeline_health(ops_path, core_path, now=now, window_seconds=86400)
    assert pipeline["assignments"][target]["bucket"] == KNOWN_INFRASTRUCTURE


# ── Phase 7: the X27.8 case specifically ──

def test_x27_8_creator_classified_repeat_creator(db_factory):
    """Reproduces the exact X27.8 shape: 895-style launch count over ~90
    days, funder rows clustered in a 6-second window -- must now land in
    Repeat Creator, not Burst Launch."""
    now = int(time.time())
    ninety_three_days_ago = now - 93 * 86400
    creator = "C2N2Ac5E9m128Tct2AepMpN4VdULpxWAX3gcigqup7rc"
    # 50 launches spread evenly across ~93 days (real X27.8 shape: 895
    # launches over ~93 days), not crammed into a short window.
    span_seconds = 93 * 86400
    token_rows = [
        {"mint": f"T{i}", "pf_ws_creator": creator,
         "created_at": str(ninety_three_days_ago + int(i * span_seconds / 49))}
        for i in range(50)
    ]
    target = token_rows[25]["mint"]
    # Burst cluster around the target, mirroring the real X27.8 evidence.
    token_rows[25]["migrated_at"] = now
    cluster_rows = [
        {"mint": f"Neighbor{i}", "pf_ws_creator": f"Other{i}", "created_at": str(now), "migrated_at": now + i * 30}
        for i in range(2)
    ]
    funder_rows = [
        {"creator_address": creator, "funder_address": f"F{i}", "first_detected_at": str(now - 30 * 86400 + i)}
        for i in range(5)
    ]
    outcomes = [{"mint": target, "outcome_type": "INSUFFICIENT_EVIDENCE"}]
    ops_path, core_path = db_factory(
        outcomes, token_rows=token_rows + cluster_rows, funder_rows=funder_rows, now=now)
    pipeline = build_pipeline_health(ops_path, core_path, now=now, window_seconds=86400)
    assert pipeline["assignments"][target]["bucket"] == REPEAT_CREATOR
    assert pipeline["assignments"][target]["bucket"] != BURST_LAUNCH


# ── Conservation / overlap, unaffected by this fix ──

def test_bucket_conservation_holds_after_fix(db_factory):
    now = int(time.time())
    creator = "ConservationCreator"
    token_rows = _established_creator_token_rows(creator, now)
    outcomes = [
        {"mint": token_rows[0]["mint"], "outcome_type": "INSUFFICIENT_EVIDENCE"},
        {"mint": "Other1", "outcome_type": "UNKNOWN_INFRASTRUCTURE"},
        {"mint": "Other2", "outcome_type": "LINEAGE_GAP"},
    ]
    token_rows.append({"mint": "Other1", "pf_ws_creator": "SomeoneElse1", "created_at": str(now)})
    token_rows.append({"mint": "Other2", "pf_ws_creator": "SomeoneElse2", "created_at": str(now)})
    ops_path, core_path = db_factory(outcomes, token_rows=token_rows, now=now)
    pipeline = build_pipeline_health(ops_path, core_path, now=now, window_seconds=86400)
    total_from_buckets = sum(b["count"] for b in pipeline["buckets"])
    assert total_from_buckets == pipeline["total_launches"]
    assert pipeline["conserved"] is True


def test_every_launch_appears_in_exactly_one_bucket_after_fix(db_factory):
    now = int(time.time())
    creator = "ExactlyOneBucketCreator"
    token_rows = _established_creator_token_rows(creator, now)
    outcomes = [
        {"mint": token_rows[0]["mint"], "outcome_type": "INSUFFICIENT_EVIDENCE"},
        {"mint": "X1", "outcome_type": "UNKNOWN_INFRASTRUCTURE"},
    ]
    token_rows.append({"mint": "X1", "pf_ws_creator": "SomeoneElse", "created_at": str(now)})
    ops_path, core_path = db_factory(outcomes, token_rows=token_rows, now=now)
    pipeline = build_pipeline_health(ops_path, core_path, now=now, window_seconds=86400)
    seen = set()
    for bucket in (KNOWN_OPERATION, KNOWN_INFRASTRUCTURE, REPEAT_CREATOR, RAPID_BIRTH_LAUNCH,
                   BURST_LAUNCH, UNKNOWN_INFRASTRUCTURE, LINEAGE_GAP, INSUFFICIENT_EVIDENCE):
        mints = [m for m, a in pipeline["assignments"].items() if a["bucket"] == bucket]
        overlap = seen & set(mints)
        assert not overlap, f"overlap found in {bucket}: {overlap}"
        seen |= set(mints)
    assert len(seen) == pipeline["total_launches"]


def test_zero_cross_bucket_overlap_after_fix(db_factory):
    now = int(time.time())
    creator = "ZeroOverlapCreator"
    token_rows = _established_creator_token_rows(creator, now)
    target = token_rows[0]["mint"]
    token_rows[0]["migrated_at"] = now
    cluster_rows = [
        {"mint": f"C{i}", "pf_ws_creator": f"CO{i}", "created_at": str(now), "migrated_at": now + i * 15}
        for i in range(3)
    ]
    outcomes = [{"mint": target, "outcome_type": "INSUFFICIENT_EVIDENCE"}]
    ops_path, core_path = db_factory(outcomes, token_rows=token_rows + cluster_rows, now=now)
    pipeline = build_pipeline_health(ops_path, core_path, now=now, window_seconds=86400)
    # target must appear in exactly Repeat Creator, never simultaneously counted
    # as a Burst Launch bucket member even though it has genuine burst evidence.
    buckets_containing_target = [
        b["bucket"] for b in pipeline["buckets"]
        for m, a in pipeline["assignments"].items()
        if m == target and a["bucket"] == b["bucket"]
    ]
    assert buckets_containing_target == [REPEAT_CREATOR]


def test_secondary_burst_evidence_available_after_bucket_change(db_factory):
    """Phase 8 — a Repeat Creator launch that also matched Burst Launch must
    not lose that evidence; it's demoted to drill-down metadata, not deleted."""
    now = int(time.time())
    creator = "SecondaryEvidenceCreator"
    token_rows = _established_creator_token_rows(creator, now)
    target = token_rows[0]["mint"]
    token_rows[0]["migrated_at"] = now
    cluster_rows = [
        {"mint": f"SE{i}", "pf_ws_creator": f"SEOther{i}", "created_at": str(now), "migrated_at": now + i * 10}
        for i in range(3)
    ]
    outcomes = [{"mint": target, "outcome_type": "INSUFFICIENT_EVIDENCE"}]
    ops_path, core_path = db_factory(outcomes, token_rows=token_rows + cluster_rows, now=now)
    pipeline = build_pipeline_health(ops_path, core_path, now=now, window_seconds=86400)
    assignment = pipeline["assignments"][target]
    assert assignment["bucket"] == REPEAT_CREATOR
    assert assignment["secondary_evidence"]["burst_launch"]["matched"] is True
    assert assignment["secondary_evidence"]["burst_launch"]["cluster_size"] >= 3
