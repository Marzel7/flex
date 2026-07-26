"""X65.35 (part 2) — Discovery's Stage-1 population must window on each
launch's own create_time, not wt_attribution_outcomes.completed_at
(walkback-completion time). Root cause: wt_attribution_outcomes has no
create_time column at all; funding_topology.py's and
operational_behaviour_tags.py's population queries filtered on
`completed_at >= since`, meaning a launch that finished walkback recently
but launched weeks ago counted as "within the last 24h," while a launch
from today whose walkback hadn't completed yet was invisible. This was
never a deliberate product decision (X29.6.1's own validation report
windows an example launch by create_time for its sanity check, while the
actual filter still ran on completed_at) -- it emerged because
wt_attribution_outcomes was the convenient join table.

Fix: src/ops/discovery_window.launch_create_times_for_mints() resolves
each mint's real launch time (token_analysis.created_at first, falling
back to wt_watchtower_launches.create_time), reusing the exact precedence
already established in operational_intelligence.py's
_enrich_discovery_records. Both funding_topology.build_topology_
classification and operational_behaviour_tags.build_behaviour_
classification now window on this resolved time instead of completed_at,
since build_operational_intelligence zips their results together by mint
-- windowing them on different time bases would reintroduce the same
inconsistency this fix removes.
"""
from __future__ import annotations

import sqlite3
import time

from src.ops.discovery_window import launch_create_times_for_mints
from src.ops.funding_topology import build_topology_classification
from src.ops.operational_behaviour_tags import build_behaviour_classification


def _ops_db(path):
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE wt_attribution_outcomes (
            mint TEXT PRIMARY KEY, outcome_type TEXT, stop_reason TEXT,
            terminal_entity TEXT, terminal_entity_type TEXT, confidence TEXT,
            evidence_json TEXT, operator_id TEXT,
            should_seed_emerging_operator INTEGER, should_retry INTEGER,
            completed_at INTEGER, source_queue_updated_at INTEGER,
            materialized_at INTEGER
        );
        CREATE TABLE wt_watchtower_launches (
            id INTEGER PRIMARY KEY AUTOINCREMENT, mint TEXT, creator_wallet TEXT,
            create_signature TEXT, create_time INTEGER, create_slot INTEGER,
            treasury_wallet TEXT, subprov_wallet TEXT, subprov_funding_sol REAL,
            wrap_close_sol REAL, wrap_close_signature TEXT,
            birth_to_launch_seconds INTEGER, create_to_migration_secs INTEGER,
            detection_source TEXT, detection_delay_seconds INTEGER,
            funding_mechanism TEXT, creator_extraction_method TEXT,
            confidence TEXT, state TEXT, recorded_at INTEGER
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
        """
    )
    conn.commit()
    conn.close()


def _core_db(path):
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE token_analysis (mint TEXT PRIMARY KEY, created_at TEXT,"
        " migrated_at INTEGER, pf_ws_creator TEXT, earliest_tx_creator TEXT)"
    )
    conn.commit()
    conn.close()


def _insert_outcome(conn, mint, completed_at):
    conn.execute(
        "INSERT INTO wt_attribution_outcomes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (mint, "INSUFFICIENT_EVIDENCE", "stop", None, "wallet", "BASELINE",
         "{}", None, 0, 0, completed_at, None, completed_at),
    )


def test_launch_create_times_prefers_token_analysis_over_watchtower_launches(tmp_path):
    ops_path, core_path = str(tmp_path / "ops.db"), str(tmp_path / "core.db")
    _ops_db(ops_path)
    _core_db(core_path)
    ops = sqlite3.connect(ops_path)
    ops.row_factory = sqlite3.Row
    core = sqlite3.connect(core_path)
    core.row_factory = sqlite3.Row

    # MINT_A has both sources -- token_analysis must win.
    core.execute("INSERT INTO token_analysis (mint, created_at) VALUES ('MINT_A', '1000')")
    ops.execute(
        "INSERT INTO wt_watchtower_launches (mint, create_time) VALUES ('MINT_A', 5000)"
    )
    # MINT_B has only wt_watchtower_launches.
    ops.execute(
        "INSERT INTO wt_watchtower_launches (mint, create_time) VALUES ('MINT_B', 2000)"
    )
    core.commit()
    ops.commit()

    result = launch_create_times_for_mints(ops, core, ["MINT_A", "MINT_B", "MINT_C"])
    assert result["MINT_A"] == 1000.0
    assert result["MINT_B"] == 2000.0
    assert "MINT_C" not in result  # no evidence at all -- omitted, never guessed


def test_launch_create_times_parses_iso_and_epoch_token_analysis_formats(tmp_path):
    ops_path, core_path = str(tmp_path / "ops.db"), str(tmp_path / "core.db")
    _ops_db(ops_path)
    _core_db(core_path)
    ops = sqlite3.connect(ops_path)
    ops.row_factory = sqlite3.Row
    core = sqlite3.connect(core_path)
    core.row_factory = sqlite3.Row
    core.execute("INSERT INTO token_analysis (mint, created_at) VALUES ('MINT_EPOCH', '1700000000.5')")
    core.execute("INSERT INTO token_analysis (mint, created_at) VALUES ('MINT_ISO', '2023-11-14T22:13:20Z')")
    core.commit()

    result = launch_create_times_for_mints(ops, core, ["MINT_EPOCH", "MINT_ISO"])
    assert result["MINT_EPOCH"] == 1700000000.5
    assert abs(result["MINT_ISO"] - 1700000000.0) < 1.0


def test_topology_population_windowed_by_launch_time_not_completed_at(tmp_path):
    ops_path, core_path = str(tmp_path / "ops.db"), str(tmp_path / "core.db")
    _ops_db(ops_path)
    _core_db(core_path)
    now = 1_000_000
    ops = sqlite3.connect(ops_path)
    core = sqlite3.connect(core_path)

    # OLD_LAUNCH: attribution completed RECENTLY (inside a 24h window) but
    # the launch itself happened 30 days ago -- must be EXCLUDED from a 24h
    # window under the create_time basis (it would have been wrongly
    # INCLUDED under the old completed_at basis).
    _insert_outcome(ops, "OLD_LAUNCH_RECENT_COMPLETION", now - 3600)
    core.execute(
        "INSERT INTO token_analysis (mint, created_at) VALUES (?, ?)",
        ("OLD_LAUNCH_RECENT_COMPLETION", str(now - 30 * 86400)),
    )

    # NEW_LAUNCH: launched inside the window, but attribution hasn't
    # completed yet (completed_at far outside the window) -- must be
    # INCLUDED under create_time basis (would have been wrongly EXCLUDED
    # under the old completed_at basis).
    _insert_outcome(ops, "NEW_LAUNCH_SLOW_COMPLETION", now - 30 * 86400)
    core.execute(
        "INSERT INTO token_analysis (mint, created_at) VALUES (?, ?)",
        ("NEW_LAUNCH_SLOW_COMPLETION", str(now - 3600)),
    )

    ops.commit()
    core.commit()
    ops.close()
    core.close()

    result = build_topology_classification(ops_path, core_path, window_seconds=86400, now=now)
    assert "OLD_LAUNCH_RECENT_COMPLETION" not in result["assignments"]
    assert "NEW_LAUNCH_SLOW_COMPLETION" in result["assignments"]


def test_behaviour_population_windowed_by_launch_time_not_completed_at(tmp_path):
    ops_path, core_path = str(tmp_path / "ops.db"), str(tmp_path / "core.db")
    _ops_db(ops_path)
    _core_db(core_path)
    now = 1_000_000
    ops = sqlite3.connect(ops_path)
    core = sqlite3.connect(core_path)

    _insert_outcome(ops, "OLD_LAUNCH_RECENT_COMPLETION", now - 3600)
    core.execute(
        "INSERT INTO token_analysis (mint, created_at) VALUES (?, ?)",
        ("OLD_LAUNCH_RECENT_COMPLETION", str(now - 30 * 86400)),
    )
    _insert_outcome(ops, "NEW_LAUNCH_SLOW_COMPLETION", now - 30 * 86400)
    core.execute(
        "INSERT INTO token_analysis (mint, created_at) VALUES (?, ?)",
        ("NEW_LAUNCH_SLOW_COMPLETION", str(now - 3600)),
    )
    ops.commit()
    core.commit()
    ops.close()
    core.close()

    result = build_behaviour_classification(ops_path, core_path, window_seconds=86400, now=now)
    assignments = result["assignments"]
    assert "OLD_LAUNCH_RECENT_COMPLETION" not in assignments
    assert "NEW_LAUNCH_SLOW_COMPLETION" in assignments


def test_topology_and_behaviour_share_the_same_population_at_the_same_window(tmp_path):
    # The two classifiers must agree on population membership, since
    # build_operational_intelligence zips their results together by mint --
    # different time bases per classifier would silently drop one
    # classifier's contribution to some records.
    ops_path, core_path = str(tmp_path / "ops.db"), str(tmp_path / "core.db")
    _ops_db(ops_path)
    _core_db(core_path)
    now = 1_000_000
    ops = sqlite3.connect(ops_path)
    core = sqlite3.connect(core_path)
    for i in range(5):
        mint = f"MINT_{i}"
        _insert_outcome(ops, mint, now - 100000)  # all completed outside a 1h window
        core.execute(
            "INSERT INTO token_analysis (mint, created_at) VALUES (?, ?)",
            (mint, str(now - i * 1000)),  # launch times spread across ~1.4h
        )
    ops.commit()
    core.commit()
    ops.close()
    core.close()

    topo = build_topology_classification(ops_path, core_path, window_seconds=3600, now=now)
    behav = build_behaviour_classification(ops_path, core_path, window_seconds=3600, now=now)
    assert set(topo["assignments"]) == set(behav["assignments"])


def test_no_launch_time_evidence_excludes_mint_never_guesses(tmp_path):
    ops_path, core_path = str(tmp_path / "ops.db"), str(tmp_path / "core.db")
    _ops_db(ops_path)
    _core_db(core_path)
    now = 1_000_000
    ops = sqlite3.connect(ops_path)
    core = sqlite3.connect(core_path)
    # An attribution outcome with NO resolvable launch time anywhere.
    _insert_outcome(ops, "NO_EVIDENCE_MINT", now - 3600)
    ops.commit()
    core.commit()
    ops.close()
    core.close()

    result = build_topology_classification(ops_path, core_path, window_seconds=365 * 86400, now=now)
    assert "NO_EVIDENCE_MINT" not in result["assignments"]
