"""X65.9 — Implement & Verify CREATE Signature Preservation Fix.

Regression-protects the exact SQL statement changed in
_update_token_entry_with_creator()'s _update_creator_write() closure
(src/core/pumpfun_curve_listener.py): the create_tx_signature column
must now use COALESCE(?, create_tx_signature) instead of a bare `?`,
so a NULL incoming value (e.g. a failed migration-time re-validation)
can never overwrite an already-persisted, correctly-captured signature
-- while a genuine non-null incoming value must still overwrite it.

This test exercises the SQL statement in isolation against a real
SQLite schema, matching the live production statement byte-for-byte,
rather than instantiating the full PumpFunCurveListener class (which
carries network/WS/RPC dependencies out of scope for this unit test).
"""
from __future__ import annotations

import sqlite3

import pytest

# The exact statement now live in _update_creator_write() --
# src/core/pumpfun_curve_listener.py, inside _update_token_entry_with_creator().
UPDATE_SQL = (
    "UPDATE token_analysis SET earliest_tx_creator=?, created_at=?, bonding_curve_pda=?, "
    "create_tx_signature=COALESCE(?, create_tx_signature), cluster_id=?, cluster_name=?, "
    "cluster_risk_multiplier=? WHERE mint=?"
)


def _build_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE token_analysis (
            mint TEXT PRIMARY KEY, earliest_tx_creator TEXT, created_at TEXT,
            bonding_curve_pda TEXT, create_tx_signature TEXT, cluster_id TEXT,
            cluster_name TEXT, cluster_risk_multiplier REAL
        )
    """)
    conn.commit()
    return conn


def _run_update(conn, mint, creator="creator", created_at="now", bcp="bcp",
                 cts=None, cid=None, cname=None, crm=1.0):
    conn.execute(UPDATE_SQL, (creator, created_at, bcp, cts, cid, cname, crm, mint))
    conn.commit()


def _get_signature(conn, mint):
    row = conn.execute("SELECT create_tx_signature FROM token_analysis WHERE mint=?", (mint,)).fetchone()
    return row["create_tx_signature"] if row else None


class TestCoalesceGuard:
    def test_null_incoming_preserves_existing_signature(self):
        """The exact defect X65.2/X65.3 identified and confirmed live:
        a NULL incoming value must no longer overwrite an existing one."""
        conn = _build_db()
        conn.execute("INSERT INTO token_analysis (mint, create_tx_signature) VALUES (?,?)",
                     ("MINT1", "EXISTING_SIGNATURE"))
        conn.commit()

        _run_update(conn, "MINT1", cts=None)

        assert _get_signature(conn, "MINT1") == "EXISTING_SIGNATURE"

    def test_non_null_incoming_still_overwrites(self):
        """A genuine new signature must still be written -- the fix must
        never block a legitimate update (X65.3 Phase 5's requirement)."""
        conn = _build_db()
        conn.execute("INSERT INTO token_analysis (mint, create_tx_signature) VALUES (?,?)",
                     ("MINT2", "OLD_SIGNATURE"))
        conn.commit()

        _run_update(conn, "MINT2", cts="NEW_VALIDATED_SIGNATURE")

        assert _get_signature(conn, "MINT2") == "NEW_VALIDATED_SIGNATURE"

    def test_null_incoming_on_row_with_no_prior_signature_stays_null(self):
        """COALESCE(NULL, NULL) = NULL -- a row that never had a signature
        correctly remains NULL, not fabricated."""
        conn = _build_db()
        conn.execute("INSERT INTO token_analysis (mint, create_tx_signature) VALUES (?,?)",
                     ("MINT3", None))
        conn.commit()

        _run_update(conn, "MINT3", cts=None)

        assert _get_signature(conn, "MINT3") is None

    def test_non_signature_columns_still_update_normally(self):
        """The fix is scoped to ONLY the create_tx_signature column -- every
        other column in the same UPDATE must continue to be written
        unconditionally, exactly as before."""
        conn = _build_db()
        conn.execute("INSERT INTO token_analysis (mint, create_tx_signature) VALUES (?,?)",
                     ("MINT4", "SIG"))
        conn.commit()

        _run_update(conn, "MINT4", creator="NEW_CREATOR", created_at="NEW_TIME",
                    bcp="NEW_BCP", cts=None, cid="CID1", cname="CNAME1", crm=2.5)

        row = conn.execute(
            "SELECT earliest_tx_creator, created_at, bonding_curve_pda, create_tx_signature, "
            "cluster_id, cluster_name, cluster_risk_multiplier FROM token_analysis WHERE mint=?",
            ("MINT4",),
        ).fetchone()
        assert row["earliest_tx_creator"] == "NEW_CREATOR"
        assert row["created_at"] == "NEW_TIME"
        assert row["bonding_curve_pda"] == "NEW_BCP"
        assert row["create_tx_signature"] == "SIG"  # preserved
        assert row["cluster_id"] == "CID1"
        assert row["cluster_name"] == "CNAME1"
        assert row["cluster_risk_multiplier"] == 2.5

    def test_repeated_null_incoming_writes_are_idempotent(self):
        """Multiple retries (the caller's own max_retries=6 loop) with a
        NULL incoming value must never progressively degrade the
        signature -- it stays exactly as it was after any number of
        repeated calls."""
        conn = _build_db()
        conn.execute("INSERT INTO token_analysis (mint, create_tx_signature) VALUES (?,?)",
                     ("MINT5", "STABLE_SIGNATURE"))
        conn.commit()

        for _ in range(6):
            _run_update(conn, "MINT5", cts=None)

        assert _get_signature(conn, "MINT5") == "STABLE_SIGNATURE"

    def test_no_diagnostic_logging_artefacts_remain_in_source(self):
        """X65.9 Phase 3: the temporary X65.3 diagnostic instrumentation
        must be fully removed from the live source file -- no lingering
        CREATE_SIG_OVERWRITE_ATTEMPT log lines, diagnostic SELECT, or
        diagnostic-only exception handler."""
        source = open("src/core/pumpfun_curve_listener.py").read()
        assert "CREATE_SIG_OVERWRITE_ATTEMPT" not in source

    def test_live_update_statement_matches_expected_coalesce_form(self):
        """Confirms the actual, currently-deployed SQL statement in
        _update_creator_write() is exactly the COALESCE form validated in
        this test file -- guards against a future edit silently reverting
        to the bare `create_tx_signature=?` form."""
        source = open("src/core/pumpfun_curve_listener.py").read()
        assert "create_tx_signature=COALESCE(?, create_tx_signature)" in source
        # the old, destructive bare-assignment form for THIS specific
        # statement must not be present anywhere in the file
        assert "create_tx_signature=?, cluster_id=?, cluster_name=?, cluster_risk_multiplier=? WHERE mint=?" not in source


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
