"""X65.44 — canonical registry creator fallback.

Reported symptom: the Canonical WATCHTOWER address table showed "Unknown"
for the Creator column on a subset of the 43 registry rows -- exactly the
same 21 mints X65.41's audit found have no wt_attribution_outcomes row
(never entered the walkback pipeline). Root cause (original, X65.40): the
registry-widening step included every wt_watchtower_launches mint
regardless of window (so is_cascade_confirmed/confirmation_completed_at
could reach them), but each record's "creator" field was set ONLY from the
behaviour classifier's output (behaviour["assignments"][mint]["creator"])
-- which never runs for a mint outside the windowed Stage-1 population.
The creator wallet IS available directly on
wt_watchtower_launches.creator_wallet (a first-party column on the launch
record itself, not an inference) -- this was just never wired through as
a fallback. Fix: fall back to the registry's own creator_wallet when the
behaviour-derived creator is absent.

X67.37 UPDATE -- the widening this fallback originally relied on
(un unconditional union applied to EVERY window) was itself the bug X67.36/
X67.37 fixed: it silently inflated every windowed (24h/7d/30d) request with
the entire historical registry. The corrected architecture widens ONLY for
an all-time request (window_seconds >= the "all" threshold) -- "All" means
"everything we know about," which legitimately includes the 21 registry
mints with no attribution-outcome row, since no finite window could ever
include them anyway. A finite window (e.g. 3600s here) now correctly
excludes a registry-only mint from `records` entirely (see
test_registry_only_mint_absent_under_finite_window below) -- this is the
INTENDED post-X67.37 behaviour, not a regression. The creator-fallback
logic itself (registry creator_wallet backfill when the behaviour-derived
value is absent) is unchanged and still exercised here, just under an
all-time window where the mint is actually present.
"""
from __future__ import annotations

import sqlite3
import time

from src.ops.operational_intelligence import build_operational_intelligence, _WINDOW_ALL_SECONDS


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
    # OUTSIDE_WINDOW_MINT: registry member, never entered the walkback
    # pipeline (no wt_attribution_outcomes row) -- this is exactly the
    # class of mint that showed "Unknown" creator before the fix.
    conn.execute(
        "INSERT INTO wt_watchtower_launches (mint,creator_wallet,create_time,confidence,state) "
        "VALUES ('OUTSIDE_WINDOW_MINT','CreatorWalletXYZ',?,'STRICT','FIRED_CREATE')",
        (now - 40 * 86400,),
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


def test_creator_falls_back_to_registry_creator_wallet_under_all_time_window(tmp_path):
    ops_path, core_path = str(tmp_path / "ops.db"), str(tmp_path / "core.db")
    _ops_db(ops_path)
    _core_db(core_path)

    # X67.37 -- an ALL-TIME window is required for OUTSIDE_WINDOW_MINT (a
    # registry-only mint, no wt_attribution_outcomes row) to be present in
    # `records` at all -- registry widening now applies only there.
    intel = build_operational_intelligence(ops_path, core_path, window_seconds=_WINDOW_ALL_SECONDS)
    record = intel["records"]["OUTSIDE_WINDOW_MINT"]

    assert record["is_cascade_confirmed"] is True
    assert record["creator"] == "CreatorWalletXYZ"


def test_registry_only_mint_absent_under_finite_window(tmp_path):
    """X67.37's actual fix, verified directly: a registry-only mint (no
    attribution-outcome row) must NOT appear in `records` for a finite
    window -- this is the corrected behaviour, replacing X65.40's original
    (buggy) expectation that it always appears regardless of window."""
    ops_path, core_path = str(tmp_path / "ops.db"), str(tmp_path / "core.db")
    _ops_db(ops_path)
    _core_db(core_path)

    intel = build_operational_intelligence(ops_path, core_path, window_seconds=3600)
    assert "OUTSIDE_WINDOW_MINT" not in intel["records"]


def test_behaviour_derived_creator_still_takes_precedence_when_present(tmp_path):
    # When a mint IS inside the windowed population and the behaviour
    # classifier already resolved a creator, that value must still win --
    # the registry fallback only applies when the classifier-derived value
    # is absent, never overriding it.
    ops_path, core_path = str(tmp_path / "ops.db"), str(tmp_path / "core.db")
    conn = sqlite3.connect(ops_path)
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
    conn.execute(
        "INSERT INTO wt_attribution_outcomes VALUES "
        "('INSIDE_WINDOW_MINT','CANONICAL_OPERATOR_REACHED','stop',"
        "NULL,'wallet','CONFIRMED','{}',NULL,0,0,?,NULL,?)",
        (now - 3600, now - 3600),
    )
    conn.execute(
        "INSERT INTO wt_watchtower_launches (mint,creator_wallet,create_time,confidence,state) "
        "VALUES ('INSIDE_WINDOW_MINT','RegistryCreatorShouldNotWin',?,'STRICT','FIRED_CREATE')",
        (now - 3600,),
    )
    conn.commit()
    conn.close()

    core_path = str(tmp_path / "core.db")
    core = sqlite3.connect(core_path)
    core.execute(
        "CREATE TABLE token_analysis (mint TEXT PRIMARY KEY, created_at TEXT, migrated_at INTEGER,"
        " pf_ws_creator TEXT, earliest_tx_creator TEXT, create_tx_signature TEXT)"
    )
    core.execute(
        "INSERT INTO token_analysis (mint, pf_ws_creator, created_at) VALUES "
        "('INSIDE_WINDOW_MINT', 'BehaviourDerivedCreator', ?)", (str(now - 3600),),
    )
    core.commit()
    core.close()

    intel = build_operational_intelligence(str(ops_path), core_path, window_seconds=365 * 86400)
    record = intel["records"]["INSIDE_WINDOW_MINT"]
    assert record["creator"] == "BehaviourDerivedCreator"


def test_creator_none_when_neither_source_available(tmp_path):
    ops_path, core_path = str(tmp_path / "ops.db"), str(tmp_path / "core.db")
    conn = sqlite3.connect(ops_path)
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
    # No creator_wallet on the registry row either -- must stay None, never
    # fabricated.
    conn.execute(
        "INSERT INTO wt_watchtower_launches (mint,creator_wallet,create_time,confidence,state) "
        "VALUES ('NO_CREATOR_ANYWHERE',NULL,?,'STRICT','FIRED_CREATE')",
        (now - 40 * 86400,),
    )
    conn.commit()
    conn.close()
    _core_db(core_path)

    # X67.37 -- all-time window: NO_CREATOR_ANYWHERE is registry-only (no
    # attribution-outcome row), so it only enters `records` under the
    # all-time population branch.
    intel = build_operational_intelligence(ops_path, core_path, window_seconds=_WINDOW_ALL_SECONDS)
    record = intel["records"]["NO_CREATOR_ANYWHERE"]
    assert record["creator"] is None
