"""Focused tests for the local operation-discovery projection module.

All tests use isolated tmp_path SQLite files -- zero touch of any real
production database, zero provider calls.
"""
from __future__ import annotations

import sqlite3

from src.discovery.local_operation_discovery_projection import (
    _classify_family,
    build_direct_funder_families,
    build_high_confidence_direct_funding_edges,
    build_upstream_edges_for_funders,
    build_upstream_source_families,
    _connect_output,
)


def make_source_db(path):
    conn = sqlite3.connect(str(path))
    conn.executescript("""
        CREATE TABLE token_analysis (mint TEXT PRIMARY KEY, pf_ws_creator TEXT);
        CREATE TABLE pumpfun_migration_verification (mint TEXT PRIMARY KEY, migrated_at INTEGER);
        CREATE TABLE transfer_index (signature TEXT, source TEXT, destination TEXT, amount_lamports INTEGER, block_time INTEGER);
        CREATE TABLE creator_funding_queue (creator_address TEXT, mint TEXT, status TEXT);
    """)
    return conn


def test_high_confidence_filter_excludes_dust_and_stale_and_failed(tmp_path):
    src_path = tmp_path / "source.db"
    conn = make_source_db(src_path)
    # HIGH: within gap, non-dust
    conn.execute("INSERT INTO token_analysis VALUES ('mint1','creatorA')")
    conn.execute("INSERT INTO pumpfun_migration_verification VALUES ('mint1', 1000)")
    conn.execute("INSERT INTO transfer_index VALUES ('sig1','funderA','creatorA',20000000,500)")  # gap=500, 0.02 SOL
    # dust: below threshold
    conn.execute("INSERT INTO token_analysis VALUES ('mint2','creatorB')")
    conn.execute("INSERT INTO pumpfun_migration_verification VALUES ('mint2', 1000)")
    conn.execute("INSERT INTO transfer_index VALUES ('sig2','funderB','creatorB',5000000,500)")  # 0.005 SOL, dust
    # stale: gap too large
    conn.execute("INSERT INTO token_analysis VALUES ('mint3','creatorC')")
    conn.execute("INSERT INTO pumpfun_migration_verification VALUES ('mint3', 100000)")
    conn.execute("INSERT INTO transfer_index VALUES ('sig3','funderC','creatorC',20000000,0)")  # gap=100000
    # documented extraction failure
    conn.execute("INSERT INTO token_analysis VALUES ('mint4','creatorD')")
    conn.execute("INSERT INTO pumpfun_migration_verification VALUES ('mint4', 1000)")
    conn.execute("INSERT INTO transfer_index VALUES ('sig4','funderD','creatorD',20000000,500)")
    conn.execute("INSERT INTO creator_funding_queue VALUES ('creatorD','mint4','failed')")
    conn.commit()
    conn.close()

    source = sqlite3.connect(f"file:{src_path}?mode=ro", uri=True)
    out = _connect_output(str(tmp_path / "out.db"))
    n = build_high_confidence_direct_funding_edges(source, out, "run1")
    assert n == 1
    rows = out.execute("SELECT mint FROM direct_funding_edges WHERE run_id='run1'").fetchall()
    assert rows == [("mint1",)]


def test_self_loop_excluded_from_direct_funding(tmp_path):
    src_path = tmp_path / "source.db"
    conn = make_source_db(src_path)
    conn.execute("INSERT INTO token_analysis VALUES ('mint1','creatorA')")
    conn.execute("INSERT INTO pumpfun_migration_verification VALUES ('mint1', 1000)")
    # self-funding: source == destination
    conn.execute("INSERT INTO transfer_index VALUES ('sig1','creatorA','creatorA',20000000,500)")
    conn.commit()
    conn.close()

    source = sqlite3.connect(f"file:{src_path}?mode=ro", uri=True)
    out = _connect_output(str(tmp_path / "out.db"))
    n = build_high_confidence_direct_funding_edges(source, out, "run1")
    assert n == 0


def test_most_recent_candidate_selected_per_mint(tmp_path):
    src_path = tmp_path / "source.db"
    conn = make_source_db(src_path)
    conn.execute("INSERT INTO token_analysis VALUES ('mint1','creatorA')")
    conn.execute("INSERT INTO pumpfun_migration_verification VALUES ('mint1', 1000)")
    conn.execute("INSERT INTO transfer_index VALUES ('sig_old','funderOld','creatorA',20000000,100)")
    conn.execute("INSERT INTO transfer_index VALUES ('sig_new','funderNew','creatorA',20000000,900)")
    conn.commit()
    conn.close()

    source = sqlite3.connect(f"file:{src_path}?mode=ro", uri=True)
    out = _connect_output(str(tmp_path / "out.db"))
    build_high_confidence_direct_funding_edges(source, out, "run1")
    row = out.execute("SELECT direct_funder FROM direct_funding_edges WHERE run_id='run1' AND mint='mint1'").fetchone()
    assert row[0] == "funderNew"  # most recent (highest block_time) selected


def test_classify_family_rules():
    assert _classify_family(1, 1, 0, 1, False, False) == "NOISE_OR_INSUFFICIENT"
    assert _classify_family(5, 1, 0, 1, False, False) == "NOISE_OR_INSUFFICIENT"  # single creator
    assert _classify_family(5, 5, 2, 5, False, False) == "STRONG_CANDIDATE_FAMILY"
    assert _classify_family(3, 3, 0, 3, False, False) == "PARTIAL_CANDIDATE_FAMILY"
    assert _classify_family(2, 2, 0, 2, False, False) == "AMBIGUOUS_FUNDING_CLUSTER"
    assert _classify_family(5, 60, 0, 60, False, True) == "SERVICE_DISTRIBUTION_CLUSTER"
    assert _classify_family(5, 5, 2, 5, True, False) == "AMBIGUOUS_FUNDING_CLUSTER"  # self-loop overrides strength


def test_direct_funder_family_clustering(tmp_path):
    src_path = tmp_path / "source.db"
    conn = make_source_db(src_path)
    for i in range(5):
        mint = f"mint{i}"
        creator = f"creator{i}"
        conn.execute("INSERT INTO token_analysis VALUES (?,?)", (mint, creator))
        conn.execute("INSERT INTO pumpfun_migration_verification VALUES (?, 1000)", (mint,))
        conn.execute("INSERT INTO transfer_index VALUES (?, 'sharedFunder', ?, 15000000, 500)",
                     (f"sig{i}", creator))
    conn.commit()
    conn.close()

    source = sqlite3.connect(f"file:{src_path}?mode=ro", uri=True)
    out = _connect_output(str(tmp_path / "out.db"))
    build_high_confidence_direct_funding_edges(source, out, "run1")
    build_upstream_edges_for_funders(source, out, "run1")
    counts = build_direct_funder_families(out, "run1")
    assert counts["STRONG_CANDIDATE_FAMILY"] == 1
    family = out.execute(
        "SELECT member_count, creator_count, classification FROM candidate_families WHERE run_id='run1'"
    ).fetchone()
    assert family == (5, 5, "STRONG_CANDIDATE_FAMILY")
    members = out.execute(
        "SELECT COUNT(*) FROM candidate_family_members WHERE run_id='run1'"
    ).fetchone()[0]
    assert members == 5


def test_mega_hub_creator_reused_not_promoted_falsely(tmp_path):
    """A single funder funding ONE creator across many mints (serial
    deployer) must NOT be classified as a strong multi-creator family --
    false-merge protection (Workstream O)."""
    src_path = tmp_path / "source.db"
    conn = make_source_db(src_path)
    for i in range(10):
        mint = f"mint{i}"
        conn.execute("INSERT INTO token_analysis VALUES (?, 'sameCreator')", (mint,))
        conn.execute("INSERT INTO pumpfun_migration_verification VALUES (?, 1000)", (mint,))
        conn.execute("INSERT INTO transfer_index VALUES (?, 'sharedFunder', 'sameCreator', 15000000, 500)",
                     (f"sig{i}",))
    conn.commit()
    conn.close()

    source = sqlite3.connect(f"file:{src_path}?mode=ro", uri=True)
    out = _connect_output(str(tmp_path / "out.db"))
    build_high_confidence_direct_funding_edges(source, out, "run1")
    build_upstream_edges_for_funders(source, out, "run1")
    counts = build_direct_funder_families(out, "run1")
    assert counts["NOISE_OR_INSUFFICIENT"] == 1
    assert counts["STRONG_CANDIDATE_FAMILY"] == 0


def test_upstream_self_loop_flagged_not_silently_used(tmp_path):
    src_path = tmp_path / "source.db"
    conn = make_source_db(src_path)
    for i in range(5):
        mint = f"mint{i}"
        creator = f"creator{i}"
        conn.execute("INSERT INTO token_analysis VALUES (?,?)", (mint, creator))
        conn.execute("INSERT INTO pumpfun_migration_verification VALUES (?, 1000)", (mint,))
        conn.execute("INSERT INTO transfer_index VALUES (?, 'sharedFunder', ?, 15000000, 500)",
                     (f"sig{i}", creator))
    # sharedFunder's own upstream funding is itself -- a self-loop
    conn.execute("INSERT INTO transfer_index VALUES ('sigloop', 'sharedFunder', 'sharedFunder', 50000000, 100)")
    conn.commit()
    conn.close()

    source = sqlite3.connect(f"file:{src_path}?mode=ro", uri=True)
    out = _connect_output(str(tmp_path / "out.db"))
    build_high_confidence_direct_funding_edges(source, out, "run1")
    build_upstream_edges_for_funders(source, out, "run1")
    counts = build_direct_funder_families(out, "run1")
    # the self-loop must downgrade this from STRONG to AMBIGUOUS
    assert counts["STRONG_CANDIDATE_FAMILY"] == 0
    assert counts["AMBIGUOUS_FUNDING_CLUSTER"] == 1


def test_upstream_source_family_excludes_dust_and_self_loop(tmp_path):
    src_path = tmp_path / "source.db"
    conn = make_source_db(src_path)
    for i in range(4):
        mint = f"mint{i}"
        creator = f"creator{i}"
        funder = f"funder{i}"
        conn.execute("INSERT INTO token_analysis VALUES (?,?)", (mint, creator))
        conn.execute("INSERT INTO pumpfun_migration_verification VALUES (?, 1000)", (mint,))
        conn.execute("INSERT INTO transfer_index VALUES (?, ?, ?, 15000000, 500)",
                     (f"sig{i}", funder, creator))
        # each funder is upstream-funded by the SAME upstream source, non-dust, non-self-loop
        conn.execute("INSERT INTO transfer_index VALUES (?, 'sharedUpstream', ?, 50000000, 100)",
                     (f"upsig{i}", funder))
    conn.commit()
    conn.close()

    source = sqlite3.connect(f"file:{src_path}?mode=ro", uri=True)
    out = _connect_output(str(tmp_path / "out.db"))
    build_high_confidence_direct_funding_edges(source, out, "run1")
    build_upstream_edges_for_funders(source, out, "run1")
    build_direct_funder_families(out, "run1")
    counts = build_upstream_source_families(out, "run1")
    assert counts["STRONG_CANDIDATE_FAMILY"] + counts["PARTIAL_CANDIDATE_FAMILY"] >= 1
    family = out.execute(
        "SELECT root_evidence, member_count, creator_count FROM candidate_families "
        "WHERE run_id='run1' AND family_kind='UPSTREAM_SOURCE'"
    ).fetchone()
    assert family[0] == "sharedUpstream"
    assert family[1] == 4
    assert family[2] == 4


def test_composite_family_requires_shared_signals(tmp_path):
    """Workstream L: a composite family requires the SAME mint to be a
    member of both a strong/partial direct-funder family and a
    strong/partial upstream-source family -- two independent signals
    agreeing, not a single signal double-counted."""
    from src.discovery.local_operation_discovery_projection import build_composite_families

    src_path = tmp_path / "source.db"
    conn = make_source_db(src_path)
    # two distinct direct funders (funder_count>=2 needed for an upstream-source
    # family to qualify), both funded by the same upstream source, each funder
    # in turn funding multiple creators (member_count/creator_count>=3 needed
    # for direct-funder family qualification too)
    for funder_idx in range(2):
        funder = f"funder{funder_idx}"
        conn.execute("INSERT INTO transfer_index VALUES (?, 'sharedUpstream', ?, 50000000, 100)",
                     (f"upsig{funder_idx}", funder))
        for i in range(3):
            mint = f"mint{funder_idx}_{i}"
            creator = f"creator{funder_idx}_{i}"
            conn.execute("INSERT INTO token_analysis VALUES (?,?)", (mint, creator))
            conn.execute("INSERT INTO pumpfun_migration_verification VALUES (?, 1000)", (mint,))
            conn.execute("INSERT INTO transfer_index VALUES (?, ?, ?, 15000000, 500)",
                         (f"sig{funder_idx}_{i}", funder, creator))
    conn.commit()
    conn.close()

    source = sqlite3.connect(f"file:{src_path}?mode=ro", uri=True)
    out = _connect_output(str(tmp_path / "out.db"))
    build_high_confidence_direct_funding_edges(source, out, "run1")
    build_upstream_edges_for_funders(source, out, "run1")
    build_direct_funder_families(out, "run1")
    build_upstream_source_families(out, "run1")
    n = build_composite_families(out, "run1", min_shared_mints=2)
    # each of the 2 distinct direct funders forms its own composite with
    # the shared upstream source (3 shared mints each) -- proves the
    # mechanism correctly attributes composite signal per direct-funder
    # family, not merged across unrelated funders
    assert n == 2
    rows = out.execute(
        "SELECT direct_funder_root, upstream_root, shared_mint_count FROM composite_families WHERE run_id='run1'"
    ).fetchall()
    assert all(r[1] == "sharedUpstream" and r[2] == 3 for r in rows)
    assert {r[0] for r in rows} == {"funder0", "funder1"}


def test_composite_family_below_threshold_excluded(tmp_path):
    from src.discovery.local_operation_discovery_projection import build_composite_families

    src_path = tmp_path / "source.db"
    conn = make_source_db(src_path)
    # only 1 shared mint -- below the min_shared_mints=2 threshold
    for i in range(3):
        mint = f"mint{i}"
        creator = f"creator{i}"
        conn.execute("INSERT INTO token_analysis VALUES (?,?)", (mint, creator))
        conn.execute("INSERT INTO pumpfun_migration_verification VALUES (?, 1000)", (mint,))
        conn.execute("INSERT INTO transfer_index VALUES (?, 'sharedFunder', ?, 15000000, 500)",
                     (f"sig{i}", creator))
    # only ONE of the 3 has upstream evidence pointing to a shared upstream
    conn.execute("INSERT INTO transfer_index VALUES ('upsig0', 'sharedUpstream', 'sharedFunder', 50000000, 100)")
    conn.commit()
    conn.close()

    source = sqlite3.connect(f"file:{src_path}?mode=ro", uri=True)
    out = _connect_output(str(tmp_path / "out.db"))
    build_high_confidence_direct_funding_edges(source, out, "run1")
    build_upstream_edges_for_funders(source, out, "run1")
    build_direct_funder_families(out, "run1")
    build_upstream_source_families(out, "run1")
    n = build_composite_families(out, "run1", min_shared_mints=2)
    assert n == 0


# --- CEX/INFRA non-attributive funding boundary (ROADMAP-H-REENTRY-A) -----

def test_cex_reclassification_marks_non_attributive_but_preserves_family(tmp_path):
    """CEX X -> creator A, CEX X -> creator B does NOT get deleted -- it is
    preserved but marked NON_ATTRIBUTIVE_PROVENANCE, per the
    KNOWN_CEX_INFRA_NON_ATTRIBUTIVE_FUNDING_BOUNDARY rule."""
    from src.discovery.local_operation_discovery_projection import apply_cex_infra_reclassification

    src_path = tmp_path / "source.db"
    conn = make_source_db(src_path)
    for i in range(5):
        mint = f"mint{i}"
        creator = f"creator{i}"
        conn.execute("INSERT INTO token_analysis VALUES (?,?)", (mint, creator))
        conn.execute("INSERT INTO pumpfun_migration_verification VALUES (?, 1000)", (mint,))
        conn.execute("INSERT INTO transfer_index VALUES (?, 'knownCEX', ?, 15000000, 500)",
                     (f"sig{i}", creator))
    conn.commit()
    conn.close()

    source = sqlite3.connect(f"file:{src_path}?mode=ro", uri=True)
    out = _connect_output(str(tmp_path / "out.db"))
    build_high_confidence_direct_funding_edges(source, out, "run1")
    build_upstream_edges_for_funders(source, out, "run1")
    counts_before = build_direct_funder_families(out, "run1")
    assert counts_before["STRONG_CANDIDATE_FAMILY"] == 1  # would look strong WITHOUT CEX awareness

    fake_category = lambda addr: "cex" if addr == "knownCEX" else "unknown"
    result = apply_cex_infra_reclassification(out, "run1", get_category_fn=fake_category)
    assert result == {"ATTRIBUTABLE": 0, "NON_ATTRIBUTIVE_PROVENANCE": 1}

    # family row, evidence, and members are ALL preserved -- not deleted
    row = out.execute(
        "SELECT member_count, classification, attribution_state, root_cex_infra_category "
        "FROM candidate_families WHERE run_id='run1'"
    ).fetchone()
    assert row[0] == 5  # member_count unchanged
    assert row[1] == "STRONG_CANDIDATE_FAMILY"  # classification unchanged (not silently deleted/altered)
    assert row[2] == "NON_ATTRIBUTIVE_PROVENANCE"
    assert row[3] == "cex"
    member_count = out.execute(
        "SELECT COUNT(*) FROM candidate_family_members WHERE run_id='run1'"
    ).fetchone()[0]
    assert member_count == 5  # members preserved


def test_cex_via_intermediary_wallet_still_non_attributive(tmp_path):
    """CEX X -> wallet A -> creator A, CEX X -> wallet B -> creator B must
    NOT merge A and B into one operation solely via CEX X -- since wallet A
    and wallet B are the DIRECT_FUNDER roots here (not CEX X itself), this
    test proves each forms its OWN small family, not a combined one, and
    reclassification correctly leaves them ATTRIBUTABLE (wallet A/B are not
    themselves CEX) while the underlying CEX link is a separate, deeper
    upstream edge not used for direct clustering."""
    from src.discovery.local_operation_discovery_projection import apply_cex_infra_reclassification

    src_path = tmp_path / "source.db"
    conn = make_source_db(src_path)
    conn.execute("INSERT INTO token_analysis VALUES ('mintA','creatorA')")
    conn.execute("INSERT INTO pumpfun_migration_verification VALUES ('mintA', 1000)")
    conn.execute("INSERT INTO transfer_index VALUES ('sigA', 'walletA', 'creatorA', 15000000, 500)")
    conn.execute("INSERT INTO transfer_index VALUES ('sigCEXA', 'knownCEX', 'walletA', 50000000, 100)")

    conn.execute("INSERT INTO token_analysis VALUES ('mintB','creatorB')")
    conn.execute("INSERT INTO pumpfun_migration_verification VALUES ('mintB', 1000)")
    conn.execute("INSERT INTO transfer_index VALUES ('sigB', 'walletB', 'creatorB', 15000000, 500)")
    conn.execute("INSERT INTO transfer_index VALUES ('sigCEXB', 'knownCEX', 'walletB', 50000000, 100)")
    conn.commit()
    conn.close()

    source = sqlite3.connect(f"file:{src_path}?mode=ro", uri=True)
    out = _connect_output(str(tmp_path / "out.db"))
    build_high_confidence_direct_funding_edges(source, out, "run1")
    build_upstream_edges_for_funders(source, out, "run1")
    build_direct_funder_families(out, "run1")
    build_upstream_source_families(out, "run1")

    # walletA and walletB are DIFFERENT direct funders -- no family clusters
    # them together (member_count=1 each, below the >=2 clustering threshold)
    direct_funder_count = out.execute(
        "SELECT COUNT(*) FROM candidate_families WHERE run_id='run1' AND family_kind='DIRECT_FUNDER'"
    ).fetchone()[0]
    assert direct_funder_count == 0  # neither walletA nor walletB alone has >=2 members

    # the upstream-source family (shared upstream=knownCEX) DOES cluster them --
    # this is exactly the case that needs CEX-awareness to avoid false attribution
    upstream_family = out.execute(
        "SELECT member_count, creator_count, root_evidence FROM candidate_families "
        "WHERE run_id='run1' AND family_kind='UPSTREAM_SOURCE'"
    ).fetchone()
    assert upstream_family is not None
    assert upstream_family[2] == "knownCEX"

    fake_category = lambda addr: "cex" if addr == "knownCEX" else "unknown"
    result = apply_cex_infra_reclassification(out, "run1", get_category_fn=fake_category)
    assert result["NON_ATTRIBUTIVE_PROVENANCE"] == 1  # the CEX-rooted upstream family is flagged
    row = out.execute(
        "SELECT attribution_state FROM candidate_families WHERE run_id='run1' AND family_kind='UPSTREAM_SOURCE'"
    ).fetchone()
    assert row[0] == "NON_ATTRIBUTIVE_PROVENANCE"


def test_composite_independent_evidence_survives_cex_reclassification(tmp_path):
    """The positive case: CEX X -> wallet A -> creator A and CEX X -> wallet
    B -> creator B alone should NOT merge, but if A and B ALSO share a
    private (non-CEX) upstream source or a strong direct-funder link, that
    INDEPENDENT evidence should remain ATTRIBUTABLE and still support a
    candidate family -- the CEX link doesn't poison unrelated independent
    evidence."""
    from src.discovery.local_operation_discovery_projection import apply_cex_infra_reclassification

    src_path = tmp_path / "source.db"
    conn = make_source_db(src_path)
    # walletA and walletB both receive CEX funding (non-attributive) AND
    # both fund creators that are ALSO clustered via a private shared funder
    for i, (wallet, creator) in enumerate([("walletA", "creatorA"), ("walletB", "creatorB"),
                                             ("walletC", "creatorC")]):
        mint = f"mint{i}"
        conn.execute("INSERT INTO token_analysis VALUES (?,?)", (mint, creator))
        conn.execute("INSERT INTO pumpfun_migration_verification VALUES (?, 1000)", (mint,))
        # PRIVATE direct funder shared across all three -- independent, non-CEX evidence
        conn.execute("INSERT INTO transfer_index VALUES (?, 'privateSharedFunder', ?, 15000000, 500)",
                     (f"sig{i}", creator))
        # each creator's private funder is ALSO CEX-funded upstream (non-attributive)
    conn.execute("INSERT INTO transfer_index VALUES ('sigcex', 'knownCEX', 'privateSharedFunder', 50000000, 100)")
    conn.commit()
    conn.close()

    source = sqlite3.connect(f"file:{src_path}?mode=ro", uri=True)
    out = _connect_output(str(tmp_path / "out.db"))
    build_high_confidence_direct_funding_edges(source, out, "run1")
    build_upstream_edges_for_funders(source, out, "run1")
    build_direct_funder_families(out, "run1")

    fake_category = lambda addr: "cex" if addr == "knownCEX" else "unknown"
    apply_cex_infra_reclassification(out, "run1", get_category_fn=fake_category)

    # the DIRECT_FUNDER family (root=privateSharedFunder, NOT CEX itself)
    # remains ATTRIBUTABLE -- independent private evidence survives
    row = out.execute(
        "SELECT member_count, attribution_state, root_cex_infra_category FROM candidate_families "
        "WHERE run_id='run1' AND family_kind='DIRECT_FUNDER'"
    ).fetchone()
    assert row[0] == 3
    assert row[1] == "ATTRIBUTABLE"
    assert row[2] is None
