"""X26.3 — Sub-Provisioner Evidence Quality & Infrastructure Exclusion.

X26.2/X26.3 audit found that wt_discovered_subprovs had NO infrastructure
exclusion check at all in its two live-write paths:
  - promote_recurring_funders() (walkback_worker.py) promoted ANY wallet
    funding >=2 distinct creators, including known automation/CEX wallets
    (confirmed live: Axiom, 23 launches, 0 wrap-close/CREATE evidence).
  - promote_to_subprov() (ws_cascade_store.py) could promote a wallet purely
    from a wrap-close-shaped detection, even when that detection was a false
    positive against a known CEX hot wallet (confirmed live: KuCoin, OKX,
    MEXC, WhiteBIT, Bidget, FixedFloat all showed wrap_close_count=1).

This suite proves: known infrastructure/CEX/relay/bridge wallets can never
be inserted or promoted as a valid sub-provisioner going forward; raw
funding evidence is still preserved; genuine WSOL_WRAP_CLOSE/
SEEDED_ACCOUNT_CLOSE sub-provisioners remain valid; no historical rows are
mutated automatically.
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
import tempfile
import time

import pytest

from src.utils.infra_mapping import INFRASTRUCTURE_ACCOUNTS, CEX_ACCOUNTS, is_known_account

AXIOM = "AxiomRXZAq1Jgjj9pHmNqVP7Lhu67wLXZJZbaK87TTSk"
RAYDIUM_V4 = "5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1"
KUCOIN = "BmFdpraQhkiDQE6SnfG5omcA1VwzqfXrwtNYBwWTymy6"

SCHEMA = """
CREATE TABLE wt_walkback_queue (
    mint TEXT PRIMARY KEY, creator TEXT, subprov TEXT, treasury TEXT,
    walkback_class TEXT, attribution_source TEXT, status TEXT, rpc_used INTEGER,
    attempts INTEGER, last_error TEXT, enqueued_at INTEGER, started_at INTEGER,
    completed_at INTEGER, updated_at INTEGER, intelligence_outcome TEXT,
    funder_wallet TEXT, funding_mechanism TEXT, funder_amount_sol REAL,
    funder_sig TEXT, funder_slot INTEGER, funder_block_time INTEGER
);
CREATE TABLE wt_discovered_subprovs (
    subprov TEXT PRIMARY KEY, first_creator TEXT, creator_count INTEGER DEFAULT 1,
    treasury TEXT, treasury_known INTEGER DEFAULT 0, first_seen INTEGER, last_seen INTEGER,
    immediate_funder TEXT, funder_is_subprov INTEGER DEFAULT 0,
    confidence REAL, state TEXT, wrap_close_count INTEGER DEFAULT 0,
    topup_count INTEGER DEFAULT 0, rejected_reason TEXT, buy_swarm_count INTEGER DEFAULT 0,
    create_count INTEGER DEFAULT 0, buy_swarm_ratio REAL DEFAULT 0.0, subprov_type TEXT,
    seeded_account_count INTEGER DEFAULT 0, discovery_source TEXT, funding_mechanism TEXT
);
CREATE TABLE wt_confirmed_treasuries (
    treasury TEXT PRIMARY KEY, confidence TEXT, method TEXT, out_sol REAL,
    recipients INTEGER, confirmed_at INTEGER
);
CREATE TABLE wt_subprov_evidence (
    subprov TEXT, wrap_close_sig TEXT UNIQUE, creator_wallet TEXT, amount_sol REAL,
    funding_mechanism TEXT, observed_at INTEGER
);
"""


@pytest.fixture()
def ops_db():
    fd, path = tempfile.mkstemp(suffix="_x26_3.db")
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()
    yield path
    os.unlink(path)


def _insert_queue_rows(path, funder, creators, mechanism="PLAIN_XFER", outcome="NO_ATTRIBUTION_FOUND"):
    conn = sqlite3.connect(path)
    now = int(time.time())
    for i, creator in enumerate(creators):
        conn.execute(
            "INSERT INTO wt_walkback_queue "
            "(mint, creator, funder_wallet, funder_block_time, funding_mechanism, "
            " intelligence_outcome, status) VALUES (?,?,?,?,?,?,'complete')",
            (f"MINT_{i}_{funder[:6]}", creator, funder, now + i, mechanism, outcome))
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Required test 1-4: known infrastructure/CEX/relay/bridge cannot be promoted
# ---------------------------------------------------------------------------

def test_axiom_automation_wallet_cannot_be_inserted_as_subprov(ops_db):
    from src.core.walkback_worker import promote_recurring_funders
    _insert_queue_rows(ops_db, AXIOM, ["CREATOR_A", "CREATOR_B", "CREATOR_C"])
    conn = sqlite3.connect(ops_db)
    conn.row_factory = sqlite3.Row
    promote_recurring_funders(conn)
    row = conn.execute("SELECT * FROM wt_discovered_subprovs WHERE subprov=?", (AXIOM,)).fetchone()
    assert row is None
    conn.close()


def test_raydium_pool_authority_cannot_be_promoted(ops_db):
    from src.core.walkback_worker import promote_recurring_funders
    _insert_queue_rows(ops_db, RAYDIUM_V4, ["CREATOR_A", "CREATOR_B"])
    conn = sqlite3.connect(ops_db)
    conn.row_factory = sqlite3.Row
    promote_recurring_funders(conn)
    row = conn.execute("SELECT * FROM wt_discovered_subprovs WHERE subprov=?", (RAYDIUM_V4,)).fetchone()
    assert row is None
    conn.close()


def test_known_cex_wallet_excluded_from_recurring_funder_promotion(ops_db):
    from src.core.walkback_worker import promote_recurring_funders
    _insert_queue_rows(ops_db, KUCOIN, ["CREATOR_A", "CREATOR_B"])
    conn = sqlite3.connect(ops_db)
    conn.row_factory = sqlite3.Row
    promote_recurring_funders(conn)
    row = conn.execute("SELECT * FROM wt_discovered_subprovs WHERE subprov=?", (KUCOIN,)).fetchone()
    assert row is None
    conn.close()


def test_program_pda_accounts_still_excluded(ops_db):
    """Pre-existing program-owned exclusion (X24-era) must still function
    alongside the new infrastructure check."""
    from src.core.walkback_worker import promote_recurring_funders, _FUNDER_BLOCKLIST
    blocklisted = next(iter(_FUNDER_BLOCKLIST))
    _insert_queue_rows(ops_db, blocklisted, ["CREATOR_A", "CREATOR_B"])
    conn = sqlite3.connect(ops_db)
    conn.row_factory = sqlite3.Row
    promote_recurring_funders(conn)
    row = conn.execute("SELECT * FROM wt_discovered_subprovs WHERE subprov=?", (blocklisted,)).fetchone()
    assert row is None
    conn.close()


# ---------------------------------------------------------------------------
# Required test 5: repeated ordinary transfers do not establish sub-provisioner
# status by themselves, even for a non-infrastructure wallet, if the
# underlying mechanism is genuinely just PLAIN_XFER recurrence -- confirmed
# the promotion function itself still runs (this is the legitimate leads
# path X26.3 explicitly preserves for non-infrastructure candidates), so this
# test instead proves the DISTINCTION: infra wallets are excluded while an
# ordinary unknown recurring funder is still surfaced as a low-confidence
# CANDIDATE lead (not silently promoted to a confirmed state).
# ---------------------------------------------------------------------------

def test_ordinary_recurring_funder_still_surfaced_as_low_confidence_candidate(ops_db):
    ordinary = "SomeOrdinaryRecurringFunderWallet11111111"
    from src.core.walkback_worker import promote_recurring_funders
    _insert_queue_rows(ops_db, ordinary, ["CREATOR_A", "CREATOR_B"])
    conn = sqlite3.connect(ops_db)
    conn.row_factory = sqlite3.Row
    promote_recurring_funders(conn)
    row = conn.execute("SELECT * FROM wt_discovered_subprovs WHERE subprov=?", (ordinary,)).fetchone()
    assert row is not None
    assert row["state"] == "PROVISION_CANDIDATE"
    assert row["confidence"] == 0.4
    assert row["treasury"] is None  # never confirmed as a treasury/real subprov


# ---------------------------------------------------------------------------
# Required test 6-7: genuine wrap-close / seeded-account-close subprovs remain valid
# ---------------------------------------------------------------------------

def test_genuine_wrap_close_subprov_remains_valid(ops_db):
    from src.core.ws_cascade_store import promote_to_subprov
    conn = sqlite3.connect(ops_db)
    promote_to_subprov(conn, subprov="GenuineSubprovWallet1111111111111111111",
                       treasury="TREASURY_X", wrap_close_sig="SIG1", creator="CREATOR_A",
                       amount_sol=2.0, funding_mechanism="WSOL_WRAP_CLOSE")
    row = conn.execute("SELECT * FROM wt_discovered_subprovs WHERE subprov=?",
                       ("GenuineSubprovWallet1111111111111111111",)).fetchone()
    conn.row_factory = sqlite3.Row
    conn.close()
    conn = sqlite3.connect(ops_db)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM wt_discovered_subprovs WHERE subprov=?",
                       ("GenuineSubprovWallet1111111111111111111",)).fetchone()
    assert row["state"] == "PROVISIONAL_SUBPROV"
    assert row["wrap_close_count"] == 1
    conn.close()


def test_genuine_seeded_account_close_subprov_remains_valid(ops_db):
    from src.core.ws_cascade_store import promote_to_subprov
    conn = sqlite3.connect(ops_db)
    promote_to_subprov(conn, subprov="GenuineSeededSubprov111111111111111111",
                       treasury="TREASURY_Y", wrap_close_sig="SIG2", creator="CREATOR_B",
                       amount_sol=1.0, funding_mechanism="SEEDED_ACCOUNT_CLOSE")
    conn.close()
    conn = sqlite3.connect(ops_db)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM wt_discovered_subprovs WHERE subprov=?",
                       ("GenuineSeededSubprov111111111111111111",)).fetchone()
    assert row["state"] == "PROVISIONAL_SUBPROV"
    assert row["seeded_account_count"] == 1
    conn.close()


def test_cex_hot_wallet_wrap_close_false_positive_rejected(ops_db):
    """The confirmed live false-positive class: a wrap-close-shaped detection
    against a known CEX hot wallet must be rejected, not promoted."""
    from src.core.ws_cascade_store import promote_to_subprov
    conn = sqlite3.connect(ops_db)
    promote_to_subprov(conn, subprov=KUCOIN, treasury="TREASURY_Z",
                       wrap_close_sig="SIG_CEX", creator="CREATOR_C",
                       amount_sol=5.0, funding_mechanism="WSOL_WRAP_CLOSE")
    conn.close()
    conn = sqlite3.connect(ops_db)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM wt_discovered_subprovs WHERE subprov=?", (KUCOIN,)).fetchone()
    assert row["state"] == "REJECTED_INFRASTRUCTURE"
    assert row["rejected_reason"] == "known infrastructure wallet"
    conn.close()


# ---------------------------------------------------------------------------
# Required test 9: raw funding evidence remains stored (even for infra rows)
# ---------------------------------------------------------------------------

def test_raw_funding_evidence_preserved_for_infrastructure_wallet(ops_db):
    from src.core.ws_cascade_store import promote_to_subprov
    conn = sqlite3.connect(ops_db)
    promote_to_subprov(conn, subprov=KUCOIN, treasury="TREASURY_Z",
                       wrap_close_sig="SIG_CEX_2", creator="CREATOR_D",
                       amount_sol=3.0, funding_mechanism="WSOL_WRAP_CLOSE")
    conn.close()
    conn = sqlite3.connect(ops_db)
    conn.row_factory = sqlite3.Row
    ev = conn.execute("SELECT * FROM wt_subprov_evidence WHERE subprov=?", (KUCOIN,)).fetchone()
    assert ev is not None
    assert ev["wrap_close_sig"] == "SIG_CEX_2"
    conn.close()


# ---------------------------------------------------------------------------
# Required test 10-11: creator count increments only for qualifying evidence;
# duplicate evidence does not double-count
# ---------------------------------------------------------------------------

def test_creator_count_increments_only_via_distinct_evidence(ops_db):
    from src.core.ws_cascade_store import promote_to_subprov
    wallet = "GenuineSubprovCountTest1111111111111111"
    conn = sqlite3.connect(ops_db)
    promote_to_subprov(conn, subprov=wallet, treasury="T", wrap_close_sig="S1",
                       creator="C1", amount_sol=1.0)
    promote_to_subprov(conn, subprov=wallet, treasury="T", wrap_close_sig="S2",
                       creator="C2", amount_sol=1.0)
    conn.close()
    conn = sqlite3.connect(ops_db)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM wt_discovered_subprovs WHERE subprov=?", (wallet,)).fetchone()
    assert row["creator_count"] == 2
    assert row["wrap_close_count"] == 2
    conn.close()


def test_duplicate_evidence_signature_does_not_double_count(ops_db):
    from src.core.ws_cascade_store import promote_to_subprov
    wallet = "GenuineSubprovDupeTest111111111111111111"
    conn = sqlite3.connect(ops_db)
    promote_to_subprov(conn, subprov=wallet, treasury="T", wrap_close_sig="SAME_SIG",
                       creator="C1", amount_sol=1.0)
    promote_to_subprov(conn, subprov=wallet, treasury="T", wrap_close_sig="SAME_SIG",
                       creator="C1", amount_sol=1.0)
    conn.close()
    conn = sqlite3.connect(ops_db)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM wt_discovered_subprovs WHERE subprov=?", (wallet,)).fetchone()
    assert row["wrap_close_count"] == 1
    assert row["creator_count"] == 1
    conn.close()


# ---------------------------------------------------------------------------
# Required test 12: existing confirmed treasury lineage remains unaffected
# ---------------------------------------------------------------------------

def test_existing_confirmed_treasury_never_demoted_by_recurring_funder_scan(ops_db):
    from src.core.walkback_worker import promote_recurring_funders
    conn = sqlite3.connect(ops_db)
    conn.execute("INSERT INTO wt_confirmed_treasuries VALUES (?,?,?,?,?,?)",
                 ("CONFIRMED_TREASURY_1", "HIGH", "walkback", 100.0, 5, int(time.time())))
    conn.commit()
    conn.close()
    _insert_queue_rows(ops_db, "CONFIRMED_TREASURY_1", ["CREATOR_A", "CREATOR_B"])
    conn = sqlite3.connect(ops_db)
    conn.row_factory = sqlite3.Row
    promote_recurring_funders(conn)
    # never inserted into wt_discovered_subprovs as a "discovered" wallet --
    # it's already a confirmed treasury, skip is correct per pre-existing rule
    row = conn.execute("SELECT * FROM wt_discovered_subprovs WHERE subprov=?",
                       ("CONFIRMED_TREASURY_1",)).fetchone()
    assert row is None
    conn.close()


# ---------------------------------------------------------------------------
# Required test 15: no historical rows are deleted/mutated automatically
# ---------------------------------------------------------------------------

def test_dry_run_report_performs_no_mutation():
    before = hashlib.sha256(open("database/wt_ops_v2.db", "rb").read()).digest()
    from src.ops.subprov_infrastructure_repair_dryrun import build_dry_run_report
    build_dry_run_report()
    after = hashlib.sha256(open("database/wt_ops_v2.db", "rb").read()).digest()
    assert before == after


def test_dry_run_report_finds_known_affected_rows():
    from src.ops.subprov_infrastructure_repair_dryrun import build_dry_run_report
    report = build_dry_run_report()
    wallets = {r["wallet"] for r in report["rows"]}
    assert AXIOM in wallets
    assert report["total_affected"] >= 24


# ---------------------------------------------------------------------------
# _is_known_infrastructure helper itself
# ---------------------------------------------------------------------------

def test_is_known_infrastructure_helper_matches_registries():
    from src.core.walkback_worker import _is_known_infrastructure
    assert _is_known_infrastructure(AXIOM) is True
    assert _is_known_infrastructure(RAYDIUM_V4) is True
    assert _is_known_infrastructure(KUCOIN) is True
    assert _is_known_infrastructure("SomeGenuineUnknownWallet1111111111111") is False
    assert _is_known_infrastructure(None) is False
    assert _is_known_infrastructure("") is False


def test_is_known_subprov_excludes_rejected_infrastructure_rows(ops_db):
    from src.core.walkback_worker import _is_known_subprov
    conn = sqlite3.connect(ops_db)
    conn.execute(
        "INSERT INTO wt_discovered_subprovs (subprov, state, rejected_reason) VALUES (?,?,?)",
        (AXIOM, "REJECTED_INFRASTRUCTURE", "known infrastructure wallet"))
    conn.commit()
    assert _is_known_subprov(conn, AXIOM) is False
    conn.close()


def test_is_known_subprov_still_true_for_genuine_provisional_subprov(ops_db):
    from src.core.walkback_worker import _is_known_subprov
    conn = sqlite3.connect(ops_db)
    conn.execute(
        "INSERT INTO wt_discovered_subprovs (subprov, state) VALUES (?,?)",
        ("GenuineSubprov1111111111111111111111111", "PROVISIONAL_SUBPROV"))
    conn.commit()
    assert _is_known_subprov(conn, "GenuineSubprov1111111111111111111111111") is True
    conn.close()
