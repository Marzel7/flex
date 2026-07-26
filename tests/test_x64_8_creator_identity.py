import sqlite3
import time
from pathlib import Path

from src.ops.creator_identity import (
    EXISTING_CREATOR,
    FRESH_CREATOR,
    HISTORY_ROW_CAP,
    HISTORY_ROW_CAP_EXCEEDED,
    IDENTITY_ORDER,
    classify_creator_identity,
    disposable_creator_score,
    enrich_creator_identity,
)


ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "templates" / "discovery.html").read_text()
SOURCE = (ROOT / "src" / "ops" / "creator_identity.py").read_text()


def test_identity_model_has_exactly_two_categories():
    """X67.38 -- Fresh Creator / Existing Creator, mutually exclusive,
    collectively exhaustive. No third value (no Unknown, no behavioural
    sub-categories)."""
    assert set(IDENTITY_ORDER) == {FRESH_CREATOR, EXISTING_CREATOR}


def test_zero_previous_launches_is_fresh_creator():
    assert classify_creator_identity(previous_launch_count=0) == FRESH_CREATOR


def test_one_or_more_previous_launches_is_existing_creator():
    for count in (1, 2, 10, 1000):
        assert classify_creator_identity(previous_launch_count=count) == EXISTING_CREATOR


def test_removed_categories_are_no_longer_importable():
    """The old categories are not just relabelled -- they no longer exist
    as concepts. Confirms SINGLE_USE_CREATOR/REPEAT_CREATOR/
    RETURNING_CREATOR/DORMANT_REACTIVATED/UNKNOWN_CREATOR_IDENTITY are gone
    from the module's public surface."""
    import src.ops.creator_identity as mod
    for removed in ("SINGLE_USE_CREATOR", "REPEAT_CREATOR", "RETURNING_CREATOR",
                    "DORMANT_REACTIVATED", "UNKNOWN_CREATOR_IDENTITY"):
        assert not hasattr(mod, removed), f"{removed} should have been removed"


def test_disposable_score_uses_only_available_persisted_evidence():
    score = disposable_creator_score(
        creator_age_seconds=2, launch_count=1, tx_count=2,
        first_tx_signature="CREATE", create_signature="CREATE",
        last_tx_at=100, migration_at=101,
    )
    assert score["score"] == 100
    assert score["evidence_coverage_pct"] == 100
    assert "Creator balance after migration" in score["unavailable_evidence"]
    assert "Previous SPL activity" in score["unavailable_evidence"]


def test_missing_score_evidence_never_counts_as_passed():
    score = disposable_creator_score(
        creator_age_seconds=None, launch_count=1, tx_count=None,
        first_tx_signature=None, create_signature=None,
        last_tx_at=None, migration_at=None,
    )
    assert score["score"] == 25
    assert score["evidence_coverage_pct"] == 25
    assert all(f["passed"] is None for f in score["evidence"] if not f["available"])


def test_enrichment_reuses_indexed_launch_and_ledger_history(tmp_path):
    db = tmp_path / "core.db"
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE token_analysis(
          mint TEXT PRIMARY KEY,pf_ws_creator TEXT,earliest_tx_creator TEXT,
          created_at TEXT,migrated_at INTEGER,create_tx_signature TEXT
        );
        CREATE INDEX idx_ta_pf_ws_creator ON token_analysis(pf_ws_creator);
        CREATE INDEX idx_ta_earliest_creator ON token_analysis(earliest_tx_creator);
        CREATE TABLE creator_tx_ledger(
          creator_pubkey TEXT,signature TEXT,blockTime INTEGER
        );
        CREATE INDEX idx_creator_tx_ledger ON creator_tx_ledger(creator_pubkey);
    """)
    conn.execute("INSERT INTO token_analysis VALUES ('M','C',NULL,'1970-01-01T00:01:40Z',101,'CREATE')")
    conn.execute("INSERT INTO creator_tx_ledger VALUES ('C','CREATE',100)")
    conn.commit(); conn.close()
    records = {"M": {"creator": "C", "creator_age_seconds": 0}}
    summary = enrich_creator_identity(str(db), records)
    assert records["M"]["creator_identity"] == FRESH_CREATOR
    assert records["M"]["disposable_creator_score"]["score"] == 100
    assert summary["classified"] == 1


def test_enrichment_marks_existing_creator_with_one_prior_launch(tmp_path):
    """X67.38 -- a creator with one launch strictly before the current one
    must resolve to EXISTING_CREATOR (previous_launch_count == 1)."""
    db = tmp_path / "core.db"
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE token_analysis(
          mint TEXT PRIMARY KEY,pf_ws_creator TEXT,earliest_tx_creator TEXT,
          created_at TEXT,migrated_at INTEGER,create_tx_signature TEXT
        );
    """)
    conn.execute("INSERT INTO token_analysis VALUES ('EARLIER','C',NULL,'1970-01-01T00:00:00Z',NULL,'S0')")
    conn.execute("INSERT INTO token_analysis VALUES ('CURRENT','C',NULL,'1970-01-01T00:01:40Z',NULL,'S1')")
    conn.commit(); conn.close()
    records = {"CURRENT": {"creator": "C", "creator_age_seconds": 0}}
    summary = enrich_creator_identity(str(db), records)
    assert records["CURRENT"]["creator_identity"] == EXISTING_CREATOR
    assert records["CURRENT"]["creator_launch_count"] == 2  # total including current
    assert summary["classified"] == 1
    assert summary["unknown"] == 0


def test_current_launch_never_counted_as_its_own_previous_launch(tmp_path):
    """X67.38's explicit requirement: the determination must always use
    launches strictly earlier than the current launch -- a creator's FIRST
    ever launch must be FRESH_CREATOR even though it appears once in its
    own launch history (never counted as a 'previous' launch relative to
    itself)."""
    db = tmp_path / "core.db"
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE token_analysis(
          mint TEXT PRIMARY KEY,pf_ws_creator TEXT,earliest_tx_creator TEXT,
          created_at TEXT,migrated_at INTEGER,create_tx_signature TEXT
        );
    """)
    conn.execute("INSERT INTO token_analysis VALUES ('ONLY','C',NULL,'1970-01-01T00:00:00Z',NULL,'S0')")
    conn.commit(); conn.close()
    records = {"ONLY": {"creator": "C", "creator_age_seconds": 0}}
    summary = enrich_creator_identity(str(db), records)
    assert records["ONLY"]["creator_identity"] == FRESH_CREATOR
    assert records["ONLY"]["creator_launch_count"] == 1


def test_two_launches_same_timestamp_neither_counts_as_previous_of_the_other(tmp_path):
    """Two launches sharing the exact same created_at (a real, if rare,
    data shape) must not count each other as 'previous' -- strictly-earlier
    means strictly less-than, not less-than-or-equal."""
    db = tmp_path / "core.db"
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE token_analysis(
          mint TEXT PRIMARY KEY,pf_ws_creator TEXT,earliest_tx_creator TEXT,
          created_at TEXT,migrated_at INTEGER,create_tx_signature TEXT
        );
    """)
    conn.execute("INSERT INTO token_analysis VALUES ('A','C',NULL,'1970-01-01T00:00:00Z',NULL,'SA')")
    conn.execute("INSERT INTO token_analysis VALUES ('B','C',NULL,'1970-01-01T00:00:00Z',NULL,'SB')")
    conn.commit(); conn.close()
    records = {
        "A": {"creator": "C", "creator_age_seconds": 0},
        "B": {"creator": "C", "creator_age_seconds": 0},
    }
    summary = enrich_creator_identity(str(db), records)
    assert records["A"]["creator_identity"] == FRESH_CREATOR
    assert records["B"]["creator_identity"] == FRESH_CREATOR


def test_every_launch_classified_no_unknown_remains(tmp_path):
    """X67.38 validation requirement: totals always equal the Discovery
    population, and every launch resolves to exactly one of the two
    categories -- no Unknown bucket, no launch left unclassified."""
    db = tmp_path / "core.db"
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE token_analysis(
          mint TEXT PRIMARY KEY,pf_ws_creator TEXT,earliest_tx_creator TEXT,
          created_at TEXT,migrated_at INTEGER,create_tx_signature TEXT
        );
    """)
    conn.execute("INSERT INTO token_analysis VALUES ('M1','C1',NULL,'1970-01-01T00:00:00Z',NULL,'S1')")
    conn.execute("INSERT INTO token_analysis VALUES ('M2','C2',NULL,'1970-01-01T00:00:00Z',NULL,'S2')")
    conn.execute("INSERT INTO token_analysis VALUES ('M3','C2',NULL,'1970-01-01T00:01:00Z',NULL,'S3')")
    conn.commit(); conn.close()
    records = {
        "M1": {"creator": "C1", "creator_age_seconds": 0},
        "M2": {"creator": "C2", "creator_age_seconds": 0},
        "M3": {"creator": "C2", "creator_age_seconds": 0},
        "NO_CREATOR": {"creator": None, "creator_age_seconds": None},
    }
    summary = enrich_creator_identity(str(db), records)
    assert summary["classified"] == len(records) == 4
    assert summary["unknown"] == 0
    assert sum(i["count"] for i in summary["identities"]) == len(records)
    for record in records.values():
        assert record["creator_identity"] in (FRESH_CREATOR, EXISTING_CREATOR)
    # a creator with no identifier at all still resolves (FRESH_CREATOR,
    # since previous_launch_count defaults to 0), never UNKNOWN
    assert records["NO_CREATOR"]["creator_identity"] == FRESH_CREATOR


def test_creator_identity_is_between_behaviour_and_topology_in_ui():
    # X65.24 — Discovery Flow Reorder: Creator Identity is now Stage 1 (the
    # cascade's entry point) and Topology immediately follows it as Stage 2;
    # Campaign moved to Stage 3, after Topology. Headings are asserted to
    # exist (raw source-text position of the CONTAINING FUNCTIONS is not
    # meaningful -- JS function declaration order doesn't affect render/DOM
    # order, which the mount-order test in test_x58_discovery_clarity.py
    # covers); the actual row-filter dependency chain is checked below.
    assert "1. Creator Identity" in HTML
    assert "2. Topology" in HTML
    assert "3. Campaign" in HTML
    assert "function x60CreatorIdentityRows" in HTML
    topology = HTML[HTML.index("function x60TopologyRows"):HTML.index("function x60CampaignRows")]
    assert "x60CreatorIdentityRows()" in topology


def test_current_count_uses_deepest_progressive_cohort():
    current = HTML[HTML.index("function x60CurrentRows"):HTML.index("function x60SanitizeSelection")]
    for stage in ("operation", "funding", "topology", "creator_identity", "behaviour"):
        assert f"TOPO_SELECTION.{stage}" in current
    mounts = HTML[HTML.index("function renderX58Mounts"):HTML.index("function renderTopoLevel")]
    assert "X58_FILTERED_ROWS=x60CurrentRows()" in mounts


def test_repeat_creator_moved_out_of_behaviour_stage():
    # X65.24 -- renderBehaviourCohorts (the old exclusive Behaviour Cohort
    # entry point) was removed; Behaviour is now the additive, terminal
    # stage rendered by renderObservedPatterns.
    behaviour = HTML[HTML.index("function renderObservedPatterns"):HTML.index("function x60TopologySourceLabel")]
    assert "REPEAT_CREATOR:'Repeat Creator'" not in behaviour


def test_classifier_has_no_rpc_or_network_path():
    assert "_rpc(" not in SOURCE
    assert "requests." not in SOURCE
    assert "urlopen" not in SOURCE


def test_batched_identity_enrichment_performance(tmp_path):
    db = tmp_path / "perf.db"
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE token_analysis(mint TEXT,pf_ws_creator TEXT,earliest_tx_creator TEXT,created_at INTEGER,migrated_at INTEGER,create_tx_signature TEXT);
        CREATE INDEX idx_pf ON token_analysis(pf_ws_creator);
        CREATE INDEX idx_early ON token_analysis(earliest_tx_creator);
        CREATE TABLE creator_tx_ledger(creator_pubkey TEXT,signature TEXT,blockTime INTEGER);
        CREATE INDEX idx_ledger ON creator_tx_ledger(creator_pubkey);
    """)
    rows = [(f"M{i}", f"C{i}", None, 100, 101, f"S{i}") for i in range(1000)]
    conn.executemany("INSERT INTO token_analysis VALUES (?,?,?,?,?,?)", rows)
    conn.executemany("INSERT INTO creator_tx_ledger VALUES (?,?,?)", [(f"C{i}", f"S{i}", 100) for i in range(1000)])
    conn.commit(); conn.close()
    records = {f"M{i}": {"creator": f"C{i}", "creator_age_seconds": 0} for i in range(1000)}
    started = time.perf_counter()
    enrich_creator_identity(str(db), records)
    assert time.perf_counter() - started < 1.0


def _build_history_row_cap_db(tmp_path, *, pathological_launch_count: int):
    """One pathological creator with `pathological_launch_count` rows, plus
    a handful of ordinary creators well under HISTORY_ROW_CAP, matching this
    project's own live-data shape (docs/CLAUDE.md's "Self-Funding
    Detection" section documents exactly this kind of wallet)."""
    db = tmp_path / "cap.db"
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE token_analysis(
          mint TEXT PRIMARY KEY,pf_ws_creator TEXT,earliest_tx_creator TEXT,
          created_at TEXT,migrated_at INTEGER,create_tx_signature TEXT
        );
        CREATE INDEX idx_ta_pf_ws_creator ON token_analysis(pf_ws_creator);
        CREATE INDEX idx_ta_earliest_creator ON token_analysis(earliest_tx_creator);
        CREATE TABLE creator_tx_ledger(
          creator_pubkey TEXT,signature TEXT,blockTime INTEGER
        );
    """)
    pathological_rows = [
        (f"PM{i}", "PATHOLOGICAL_CREATOR", None, "1970-01-01T00:01:40Z", 101, f"PS{i}")
        for i in range(pathological_launch_count)
    ]
    conn.executemany("INSERT INTO token_analysis VALUES (?,?,?,?,?,?)", pathological_rows)
    conn.execute(
        "INSERT INTO token_analysis VALUES ('NORMAL_M','NORMAL_CREATOR',NULL,"
        "'1970-01-01T00:01:40Z',101,'NORMAL_S')"
    )
    conn.commit()
    conn.close()
    return db


def test_history_row_cap_excludes_pathological_creator_from_full_fetch(tmp_path):
    """X67.38 -- a creator with far more than HISTORY_ROW_CAP token_analysis
    rows has an unknowable previous_launch_count (defaults to 0), so
    resolves to FRESH_CREATOR -- never a distinct 'Unknown' identity -- but
    the data-quality condition is still fully observable via
    creator_identity_skip_reason, and creator_launch_count/prior_launch_gap
    remain None (never a misleadingly precise-looking count)."""
    db = _build_history_row_cap_db(tmp_path, pathological_launch_count=HISTORY_ROW_CAP + 500)
    records = {
        "PM0": {"creator": "PATHOLOGICAL_CREATOR", "creator_age_seconds": 0},
        "NORMAL_M": {"creator": "NORMAL_CREATOR", "creator_age_seconds": 0},
    }
    summary = enrich_creator_identity(str(db), records)

    assert records["PM0"]["creator_identity"] == FRESH_CREATOR
    assert records["PM0"]["creator_identity_skip_reason"] == HISTORY_ROW_CAP_EXCEEDED
    assert records["PM0"]["creator_launch_count"] is None
    assert records["PM0"]["prior_launch_gap_seconds"] is None
    assert summary["history_row_cap_exceeded_count"] == 1


def test_history_row_cap_does_not_affect_creators_under_the_cap(tmp_path):
    """The guard must be fully invisible to every creator below the
    threshold -- ordinary classification proceeds exactly as before."""
    db = _build_history_row_cap_db(tmp_path, pathological_launch_count=HISTORY_ROW_CAP + 500)
    records = {
        "PM0": {"creator": "PATHOLOGICAL_CREATOR", "creator_age_seconds": 0},
        "NORMAL_M": {"creator": "NORMAL_CREATOR", "creator_age_seconds": 0},
    }
    summary = enrich_creator_identity(str(db), records)

    assert records["NORMAL_M"]["creator_identity"] == FRESH_CREATOR
    assert records["NORMAL_M"]["creator_identity_skip_reason"] is None
    assert records["NORMAL_M"]["creator_launch_count"] == 1
    # X67.38 -- both records are always classified (FRESH_CREATOR or
    # EXISTING_CREATOR); there is no "unknown" bucket to fall into anymore,
    # so "classified" covers the whole population and "unknown" is always 0.
    assert summary["classified"] == 2
    assert summary["unknown"] == 0


def test_creator_exactly_at_history_row_cap_is_not_skipped(tmp_path):
    """A creator with EXACTLY HISTORY_ROW_CAP rows (not one more) must still
    go through the normal path -- the guard triggers on `> HISTORY_ROW_CAP`,
    never `>=`, matching classify_creator_identity's own boundary style."""
    db = _build_history_row_cap_db(tmp_path, pathological_launch_count=HISTORY_ROW_CAP)
    records = {"PM0": {"creator": "PATHOLOGICAL_CREATOR", "creator_age_seconds": 0}}
    summary = enrich_creator_identity(str(db), records)

    assert records["PM0"]["creator_identity_skip_reason"] is None
    assert records["PM0"]["creator_launch_count"] == HISTORY_ROW_CAP
    assert summary["history_row_cap_exceeded_count"] == 0


def test_history_row_cap_guard_uses_batched_not_percreator_queries():
    """Regression test for a measured-live perf regression: a per-creator
    probe loop over ~2,000 creators cost 38s despite each individual query
    being fast, purely from per-call overhead. The batched GROUP BY ...
    HAVING form must be used instead -- this test greps the actual
    implementation to guard against silently reintroducing the slow
    per-creator loop shape."""
    assert "for creator in creators:" not in SOURCE.split("def _creators_over_history_row_cap")[1].split("def ")[0]
    assert "HAVING COUNT(*) > ?" in SOURCE


def test_history_row_cap_perf_with_one_pathological_creator(tmp_path):
    """The whole point of this guard: a single pathological creator with
    tens of thousands of rows must not make enrichment slow. Mirrors
    test_batched_identity_enrichment_performance's shape/threshold but adds
    one wallet far over HISTORY_ROW_CAP alongside 1,000 ordinary creators."""
    db = tmp_path / "cap_perf.db"
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE token_analysis(mint TEXT,pf_ws_creator TEXT,earliest_tx_creator TEXT,created_at INTEGER,migrated_at INTEGER,create_tx_signature TEXT);
        CREATE INDEX idx_pf ON token_analysis(pf_ws_creator);
        CREATE INDEX idx_early ON token_analysis(earliest_tx_creator);
        CREATE TABLE creator_tx_ledger(creator_pubkey TEXT,signature TEXT,blockTime INTEGER);
        CREATE INDEX idx_ledger ON creator_tx_ledger(creator_pubkey);
    """)
    rows = [(f"M{i}", f"C{i}", None, 100, 101, f"S{i}") for i in range(1000)]
    pathological_rows = [
        (f"PM{i}", "PATHOLOGICAL_CREATOR", None, 100, 101, f"PS{i}")
        for i in range(HISTORY_ROW_CAP + 5000)
    ]
    conn.executemany("INSERT INTO token_analysis VALUES (?,?,?,?,?,?)", rows + pathological_rows)
    conn.executemany("INSERT INTO creator_tx_ledger VALUES (?,?,?)", [(f"C{i}", f"S{i}", 100) for i in range(1000)])
    conn.commit(); conn.close()
    records = {f"M{i}": {"creator": f"C{i}", "creator_age_seconds": 0} for i in range(1000)}
    records["PM0"] = {"creator": "PATHOLOGICAL_CREATOR", "creator_age_seconds": 0}
    started = time.perf_counter()
    summary = enrich_creator_identity(str(db), records)
    elapsed = time.perf_counter() - started
    assert elapsed < 2.0, f"expected the pathological creator to be excluded cheaply, took {elapsed}s"
    assert records["PM0"]["creator_identity_skip_reason"] == HISTORY_ROW_CAP_EXCEEDED
    assert summary["history_row_cap_exceeded_count"] == 1
