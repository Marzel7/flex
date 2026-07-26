"""X65.41 — Establish the Canonical Definition of a WATCHTOWER Launch.

Investigation (against database/wt_ops_v2.db, see conversation record for
the full audit): wt_watchtower_launches is documented in its own writer
(src/core/ws_cascade_store.py's record_launch docstring: "Authoritative
launch record") as accepting THREE independent evidence paths -- live
cascade detection (confidence=STRICT), post-migration backfill lineage
walk (confidence=BACKFILL, src/core/watchtower_backfill.py), and manual
attestation (confidence=MANUAL_ATTESTATION) -- all writing into the SAME
table because membership in it IS the canonical WATCHTOWER definition,
regardless of which evidence path established it.

wt_attribution_outcomes is a SEPARATE, general-purpose walkback ledger
("Canonical terminal outcomes for token attribution") covering every
migrated token, WATCHTOWER and non-WATCHTOWER alike -- not a WATCHTOWER-
specific confirmation gate. Of the 43 real registry rows audited: 22 have
a wt_attribution_outcomes row (CANONICAL_OPERATOR_REACHED, zero rows with
any OTHER outcome type), and 21 have no wt_attribution_outcomes row AND no
wt_walkback_queue row -- meaning they never entered the walkback pipeline
at all, not that walkback examined and rejected them. 16 of those 21 have
already migrated, ruling out "hasn't migrated yet" as the explanation.

Decision: Option 1 -- canonical WATCHTOWER = membership in
wt_watchtower_launches alone. Requiring an ADDITIONAL wt_attribution_
outcomes row would make WATCHTOWER status contingent on an unrelated
pipeline's incidental per-mint coverage, not on any stronger evidence
about the launch itself. wt_attribution_outcomes coverage (22/43) is a
separate, useful metric -- evidence coverage, not the confirmation gate.

This test file guards the is_cascade_confirmed field definition itself
(membership-only, no outcome-row requirement) so a future change cannot
silently re-narrow it back to the accidental 22-mint intersection X65.34-
X65.40 uncovered and this task resolved.
"""
from __future__ import annotations

import json
import sqlite3
import time

from src.ops.operational_intelligence import build_operational_intelligence


def _ops_db(path):
    conn = sqlite3.connect(path)
    conn.execute(
        """CREATE TABLE wt_attribution_outcomes (
            mint TEXT PRIMARY KEY, outcome_type TEXT, stop_reason TEXT,
            terminal_entity TEXT, terminal_entity_type TEXT, confidence TEXT,
            evidence_json TEXT, operator_id TEXT,
            should_seed_emerging_operator INTEGER, should_retry INTEGER,
            completed_at INTEGER, source_queue_updated_at INTEGER,
            materialized_at INTEGER
        )"""
    )
    conn.execute(
        """CREATE TABLE wt_watchtower_launches (
            id INTEGER PRIMARY KEY AUTOINCREMENT, mint TEXT, creator_wallet TEXT,
            create_signature TEXT, create_time INTEGER, create_slot INTEGER,
            treasury_wallet TEXT, subprov_wallet TEXT, subprov_funding_sol REAL,
            wrap_close_sol REAL, wrap_close_signature TEXT,
            birth_to_launch_seconds INTEGER, create_to_migration_secs INTEGER,
            detection_source TEXT, detection_delay_seconds INTEGER,
            funding_mechanism TEXT, creator_extraction_method TEXT,
            confidence TEXT, state TEXT, recorded_at INTEGER
        )"""
    )
    now = int(time.time())

    # WITH_OUTCOME: live-cascade-detected, walkback also confirmed it.
    conn.execute(
        "INSERT INTO wt_watchtower_launches (mint,creator_wallet,create_time,confidence,state) "
        "VALUES ('WITH_OUTCOME','creatorA',?,'STRICT','FIRED_CREATE')", (now - 86400,),
    )
    conn.execute(
        "INSERT INTO wt_attribution_outcomes VALUES "
        "('WITH_OUTCOME','CANONICAL_OPERATOR_REACHED','stop',NULL,'wallet','CONFIRMED',?,NULL,0,0,?,NULL,?)",
        (json.dumps({}), now - 3600, now - 3600),
    )

    # NO_OUTCOME_BACKFILL: post-migration backfill lineage walk, never
    # entered the walkback queue -- still a real, evidenced WATCHTOWER
    # launch, not a placeholder.
    conn.execute(
        "INSERT INTO wt_watchtower_launches (mint,creator_wallet,create_time,confidence,state) "
        "VALUES ('NO_OUTCOME_BACKFILL','creatorB',?,'BACKFILL','FIRED_CREATE')", (now - 10 * 86400,),
    )

    # NO_OUTCOME_STRICT: live-cascade-detected, never reached by the
    # separate walkback pipeline at all.
    conn.execute(
        "INSERT INTO wt_watchtower_launches (mint,creator_wallet,create_time,confidence,state) "
        "VALUES ('NO_OUTCOME_STRICT','creatorC',?,'STRICT','FIRED_CREATE')", (now - 5 * 86400,),
    )

    conn.commit()
    conn.close()


def _core_db(path):
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE token_analysis (mint TEXT PRIMARY KEY, created_at TEXT, migrated_at INTEGER,"
        " pf_ws_creator TEXT, earliest_tx_creator TEXT, create_tx_signature TEXT)"
    )
    conn.commit()
    conn.close()


def test_canonical_confirmed_includes_registry_members_without_an_outcome_row(tmp_path):
    ops_path, core_path = str(tmp_path / "ops.db"), str(tmp_path / "core.db")
    _ops_db(ops_path)
    _core_db(core_path)

    intel = build_operational_intelligence(ops_path, core_path, window_seconds=365 * 86400)
    confirmed_mints = {m for m, r in intel["records"].items() if r.get("is_cascade_confirmed")}

    # All THREE registry members count as canonical WATCHTOWER, regardless
    # of confidence tier or whether a separate wt_attribution_outcomes row
    # exists -- this is the core X65.41 decision.
    assert confirmed_mints == {"WITH_OUTCOME", "NO_OUTCOME_BACKFILL", "NO_OUTCOME_STRICT"}


def test_confirmation_completed_at_is_none_for_registry_members_without_an_outcome_row(tmp_path):
    ops_path, core_path = str(tmp_path / "ops.db"), str(tmp_path / "core.db")
    _ops_db(ops_path)
    _core_db(core_path)

    intel = build_operational_intelligence(ops_path, core_path, window_seconds=365 * 86400)
    records = intel["records"]

    assert records["WITH_OUTCOME"]["confirmation_completed_at"] is not None
    assert records["NO_OUTCOME_BACKFILL"]["confirmation_completed_at"] is None
    assert records["NO_OUTCOME_STRICT"]["confirmation_completed_at"] is None


def test_walkback_evidence_coverage_is_a_strict_subset_of_canonical(tmp_path):
    ops_path, core_path = str(tmp_path / "ops.db"), str(tmp_path / "core.db")
    _ops_db(ops_path)
    _core_db(core_path)

    intel = build_operational_intelligence(ops_path, core_path, window_seconds=365 * 86400)
    records = intel["records"]

    canonical = {m for m, r in records.items() if r.get("is_cascade_confirmed")}
    with_walkback_evidence = {
        m for m, r in records.items()
        if r.get("is_cascade_confirmed") and r.get("confirmation_completed_at") is not None
    }
    assert with_walkback_evidence < canonical  # strict subset, not equal
    assert with_walkback_evidence == {"WITH_OUTCOME"}
