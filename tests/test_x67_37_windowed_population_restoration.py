"""X67.37 -- Restore Windowed Operational Intelligence Population.

X67.36 found build_operational_intelligence()'s registry-widening step
(X61) unconditionally unioned the ENTIRE wt_watchtower_launches registry
into `records`, regardless of window_seconds -- a 24h request's `records`
silently included every historical canonical launch (907 rows instead of
the true ~760 windowed population; 164 is_watchtower=True rows instead of
the Canonical panel's own correctly-windowed 20).

The corrected architecture (per explicit user decision after this task's
own investigation surfaced a real tension: 21 of 163 registry mints have
no wt_attribution_outcomes row and so can NEVER enter the windowed
Stage-1 population under any window, meaning "All" would also lose them
without special handling):

  finite window (24h/7d/30d): population = windowed launches ONLY.
    Registry membership never adds a mint -- it only ANNOTATES mints
    already present (is_watchtower/is_cascade_confirmed, unchanged).
  all-time window (window_seconds >= _WINDOW_ALL_SECONDS): population =
    windowed launches UNION the full registry -- the ONE place a union is
    architecturally correct, since "All" means "everything we know about"
    and there is no other window under which a registry-only mint could
    ever appear.
"""
from __future__ import annotations

import sqlite3
import time

import pytest

from src.ops.operational_intelligence import build_operational_intelligence, _WINDOW_ALL_SECONDS


def _ops_db(path, *, attribution_rows=(), registry_rows=()):
    """attribution_rows: list of (mint, operator_id, completed_at) tuples.
    registry_rows: list of (mint, creator_wallet, create_time) tuples."""
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
    for mint, operator_id, completed_at in attribution_rows:
        conn.execute(
            "INSERT INTO wt_attribution_outcomes "
            "(mint, outcome_type, operator_id, should_seed_emerging_operator, should_retry, completed_at) "
            "VALUES (?, 'KNOWN_CEX_REACHED', ?, 0, 0, ?)",
            (mint, operator_id, completed_at),
        )
    for mint, creator_wallet, create_time in registry_rows:
        conn.execute(
            "INSERT INTO wt_watchtower_launches (mint, creator_wallet, create_time, confidence, state) "
            "VALUES (?, ?, ?, 'STRICT', 'FIRED_CREATE')",
            (mint, creator_wallet, create_time),
        )
    conn.commit()
    conn.close()


def _core_db(path, *, token_analysis_rows=()):
    """token_analysis_rows: list of (mint, created_at) tuples."""
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE token_analysis (mint TEXT PRIMARY KEY, created_at TEXT, migrated_at INTEGER,"
        " pf_ws_creator TEXT, earliest_tx_creator TEXT, create_tx_signature TEXT)"
    )
    for mint, created_at in token_analysis_rows:
        conn.execute(
            "INSERT INTO token_analysis (mint, created_at) VALUES (?, ?)", (mint, str(created_at)),
        )
    conn.commit()
    conn.close()


# ── 1. Population construction ──────────────────────────────────────────────

class TestPopulationConstruction:
    def test_windowed_request_returns_only_launches_inside_window(self, tmp_path):
        now = int(time.time())
        ops_path, core_path = str(tmp_path / "ops.db"), str(tmp_path / "core.db")
        _ops_db(
            ops_path,
            attribution_rows=[
                ("RecentMint", None, now - 100),
                ("OldMint", None, now - 40 * 86400),
            ],
        )
        _core_db(core_path, token_analysis_rows=[
            ("RecentMint", now - 100), ("OldMint", now - 40 * 86400),
        ])
        intel = build_operational_intelligence(ops_path, core_path, window_seconds=86400, now=now)
        assert "RecentMint" in intel["records"]
        assert "OldMint" not in intel["records"]

    def test_registry_membership_never_introduces_additional_rows_for_finite_window(self, tmp_path):
        """The exact X67.36 defect: a registry-only mint (no attribution
        outcome) must not appear in a finite-window population."""
        now = int(time.time())
        ops_path, core_path = str(tmp_path / "ops.db"), str(tmp_path / "core.db")
        _ops_db(ops_path, registry_rows=[("RegistryOnlyMint", "Creator1", now - 40 * 86400)])
        _core_db(core_path)
        intel = build_operational_intelligence(ops_path, core_path, window_seconds=86400, now=now)
        assert "RegistryOnlyMint" not in intel["records"]

    def test_annotation_works_without_widening(self, tmp_path):
        """A launch that IS in the windowed population and IS in the
        registry gets is_watchtower=True without the registry needing to
        have introduced the row itself."""
        now = int(time.time())
        ops_path, core_path = str(tmp_path / "ops.db"), str(tmp_path / "core.db")
        _ops_db(
            ops_path,
            attribution_rows=[("WindowedCanonical", None, now - 100)],
            registry_rows=[("WindowedCanonical", "Creator1", now - 100)],
        )
        _core_db(core_path, token_analysis_rows=[("WindowedCanonical", now - 100)])
        intel = build_operational_intelligence(ops_path, core_path, window_seconds=86400, now=now)
        assert intel["records"]["WindowedCanonical"]["is_watchtower"] is True

    def test_total_launches_equals_records_length_for_finite_window(self, tmp_path):
        """The core invariant X67.37 restores: no more registry-inflation
        gap between the topology classifier's own count and the final
        payload size."""
        now = int(time.time())
        ops_path, core_path = str(tmp_path / "ops.db"), str(tmp_path / "core.db")
        _ops_db(
            ops_path,
            attribution_rows=[(f"Mint{i}", None, now - 100) for i in range(10)],
            registry_rows=[(f"RegistryOnly{i}", f"Creator{i}", now - 40 * 86400) for i in range(5)],
        )
        _core_db(core_path, token_analysis_rows=[(f"Mint{i}", now - 100) for i in range(10)])
        intel = build_operational_intelligence(ops_path, core_path, window_seconds=86400, now=now)
        assert intel["total_launches"] == len(intel["records"]) == 10


# ── 2. Annotation semantics ──────────────────────────────────────────────────

class TestAnnotationSemantics:
    def test_registry_launch_inside_window_is_watchtower_true(self, tmp_path):
        now = int(time.time())
        ops_path, core_path = str(tmp_path / "ops.db"), str(tmp_path / "core.db")
        _ops_db(
            ops_path,
            attribution_rows=[("InWindowRegistry", None, now - 100)],
            registry_rows=[("InWindowRegistry", "Creator1", now - 100)],
        )
        _core_db(core_path, token_analysis_rows=[("InWindowRegistry", now - 100)])
        intel = build_operational_intelligence(ops_path, core_path, window_seconds=86400, now=now)
        assert intel["records"]["InWindowRegistry"]["is_watchtower"] is True

    def test_registry_launch_outside_window_is_absent_not_just_unwatched(self, tmp_path):
        """A registry launch outside the window must be ABSENT from
        records entirely -- not present with is_watchtower=False."""
        now = int(time.time())
        ops_path, core_path = str(tmp_path / "ops.db"), str(tmp_path / "core.db")
        _ops_db(
            ops_path,
            attribution_rows=[("OutsideWindowRegistry", None, now - 40 * 86400)],
            registry_rows=[("OutsideWindowRegistry", "Creator1", now - 40 * 86400)],
        )
        _core_db(core_path, token_analysis_rows=[("OutsideWindowRegistry", now - 40 * 86400)])
        intel = build_operational_intelligence(ops_path, core_path, window_seconds=86400, now=now)
        assert "OutsideWindowRegistry" not in intel["records"]

    def test_explicit_confirmed_operation_inside_window_is_watchtower_true(self, tmp_path):
        """is_watchtower's OTHER path (explicit_confirmed_operation via
        wt_attribution_outcomes.operator_id), not registry membership --
        confirms this annotation path is unaffected by the population fix."""
        from src.ops.watchtower_alignment import WATCHTOWER_OPERATOR_ID
        now = int(time.time())
        ops_path, core_path = str(tmp_path / "ops.db"), str(tmp_path / "core.db")
        _ops_db(
            ops_path,
            attribution_rows=[("ExplicitOpMint", WATCHTOWER_OPERATOR_ID, now - 100)],
        )
        _core_db(core_path, token_analysis_rows=[("ExplicitOpMint", now - 100)])
        intel = build_operational_intelligence(ops_path, core_path, window_seconds=86400, now=now)
        assert intel["records"]["ExplicitOpMint"]["is_watchtower"] is True

    def test_registry_launch_included_under_all_time_window(self, tmp_path):
        """The explicit all-time population branch: a registry-only mint
        (no attribution outcome) DOES appear under window_seconds >=
        _WINDOW_ALL_SECONDS, with is_watchtower=True."""
        now = int(time.time())
        ops_path, core_path = str(tmp_path / "ops.db"), str(tmp_path / "core.db")
        _ops_db(ops_path, registry_rows=[("AllTimeOnlyMint", "Creator1", now - 400 * 86400)])
        _core_db(core_path)
        intel = build_operational_intelligence(ops_path, core_path, window_seconds=_WINDOW_ALL_SECONDS, now=now)
        assert "AllTimeOnlyMint" in intel["records"]
        assert intel["records"]["AllTimeOnlyMint"]["is_watchtower"] is True


# ── 3. Distinct mint counts / no duplication ─────────────────────────────────

class TestDistinctMintCounts:
    def test_no_duplicate_mints_finite_window(self, tmp_path):
        now = int(time.time())
        ops_path, core_path = str(tmp_path / "ops.db"), str(tmp_path / "core.db")
        _ops_db(
            ops_path,
            attribution_rows=[("Mint1", None, now - 100)],
            registry_rows=[("Mint1", "Creator1", now - 100)],  # same mint, both sources
        )
        _core_db(core_path, token_analysis_rows=[("Mint1", now - 100)])
        intel = build_operational_intelligence(ops_path, core_path, window_seconds=86400, now=now)
        assert len(intel["records"]) == 1  # one dict entry, not duplicated

    def test_no_duplicate_mints_all_time_window(self, tmp_path):
        now = int(time.time())
        ops_path, core_path = str(tmp_path / "ops.db"), str(tmp_path / "core.db")
        _ops_db(
            ops_path,
            attribution_rows=[("Mint1", None, now - 100)],
            registry_rows=[("Mint1", "Creator1", now - 100), ("Mint2", "Creator2", now - 400 * 86400)],
        )
        _core_db(core_path, token_analysis_rows=[("Mint1", now - 100)])
        intel = build_operational_intelligence(ops_path, core_path, window_seconds=_WINDOW_ALL_SECONDS, now=now)
        assert len(intel["records"]) == 2  # Mint1 (union of both sources, once) + Mint2 (registry-only)


# ── 4. Regression: X67.35 exclusion, X67.33 window filtering, panel parity ──

class TestRegressionRealDatabase:
    """These hit the real, live databases (same pattern the session has used
    throughout this task's investigation) rather than fixtures, since the
    thing being verified is cross-module consistency against real data."""

    def test_x67_35_is_watchtower_field_still_present_and_boolean(self):
        import sqlite3 as _sq
        intel = build_operational_intelligence(
            "database/wt_ops_v2.db", "database/flex_complete_database.db", window_seconds=86400,
        )
        for r in intel["records"].values():
            assert isinstance(r["is_watchtower"], bool)

    def test_operational_and_canonical_panel_agree_on_24h_population_shape(self):
        """Not exact equality (a separate, pre-existing timestamp-precedence
        discrepancy between launch_create_times_for_mints() and
        wt_watchtower_launches.create_time affects a handful of mints near
        the 24h boundary -- flagged in this task's own investigation as a
        distinct, out-of-scope issue) -- but the gross inflation (164 vs 20,
        a ~8x factor) must be gone. The corrected populations should be
        within a small, boundary-explainable margin of each other, not off
        by two orders of magnitude."""
        import sqlite3 as _sq
        import time as _time
        now = int(_time.time())
        intel = build_operational_intelligence(
            "database/wt_ops_v2.db", "database/flex_complete_database.db",
            window_seconds=86400, now=now,
        )
        is_wt_count = sum(1 for r in intel["records"].values() if r.get("is_watchtower"))

        from src.ops.canonical_launches import get_canonical_watchtower_launches
        ov = _sq.connect("database/wt_ops_v2.db")
        ov.row_factory = _sq.Row
        live = _sq.connect("database/flex_complete_database.db")
        live.row_factory = _sq.Row
        canonical = get_canonical_watchtower_launches(ov, live, window_start=now - 86400, window_end=now)
        canonical_count = len(canonical)

        assert abs(is_wt_count - canonical_count) <= 10, (
            f"is_watchtower count ({is_wt_count}) and Canonical panel count "
            f"({canonical_count}) diverge by more than the known boundary "
            f"margin -- registry-widening may have regressed"
        )

    def test_total_launches_conserves_against_records_for_real_24h_window(self):
        intel = build_operational_intelligence(
            "database/wt_ops_v2.db", "database/flex_complete_database.db", window_seconds=86400,
        )
        assert intel["total_launches"] == len(intel["records"])
