import json
import sqlite3
import tempfile
from pathlib import Path

import pytest

from src.ops.treasury_mesh_discovery_engine import (
    DISCOVERY_SCHEMA_VERSION, INBOUND, OUTBOUND, PAGE_INCOMPLETE, PROVIDER_LIMIT_STOP,
    DiscoveryBudget, DiscoveryError, ProviderLimited, causal_graph, classify_wallet_role, ensure_schema,
    enqueue_adjacent_jobs, finalize_page, page_coverage, record_signature_page, record_transaction_result,
    open_isolated_store, resume_provider_limited_job, review_only_candidate, run_one_page, seed_confirmed_treasuries, seed_known_intermediary, seed_unresolved_launch,
    walkback_read_interface,
)
from src.ops.treasury_rotation_discovery import (
    CONFIRMED_TREASURY_MATCH, FUNDING_ACCOUNT_MATCH, KNOWN_SUBPROVIDER_MATCH,
    NEW_TREASURY_CANDIDATE, PARTIAL_LINEAGE,
)

AMQ = "AMQ"
EIGHT = "8CUb"
CREATOR = "6eKx"


def _transaction(*, version="legacy", sender=AMQ, receiver=EIGHT, slot=10, index=1, lamports=770_000_000_000):
    return {"version": version, "slot": slot, "transactionIndex": index,
            "transaction": {"message": {"accountKeys": [sender, receiver], "instructions": [
                {"program": "system", "parsed": {"type": "transfer", "info": {"source": sender, "destination": receiver, "lamports": lamports}}}
            ]}}, "meta": {"preBalances": [lamports + 1, 0], "postBalances": [1, lamports]}}


class ReplayClient:
    def __init__(self, pages, transactions, limit_after=None):
        self.pages, self.transactions, self.limit_after, self.calls = pages, transactions, limit_after, 0

    def get_signatures(self, address, direction, before, limit):
        self.calls += 1
        assert 1 <= limit <= 20 and direction in {INBOUND, OUTBOUND}
        return self.pages.pop(0)

    def get_transaction(self, signature, config):
        self.calls += 1
        assert config["maxSupportedTransactionVersion"] == 1
        if self.limit_after is not None and self.calls > self.limit_after:
            raise ProviderLimited()
        return self.transactions[signature]


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    return conn


def test_treasury_first_amq_positive_control_is_discovered_without_transaction_signature_seed():
    conn = _conn()
    seed_confirmed_treasuries(conn, [AMQ], now=1)
    job = conn.execute("SELECT job_id FROM wt_mesh_jobs WHERE address=? AND direction=?", (AMQ, OUTBOUND)).fetchone()[0]
    prior = json.loads((Path(__file__).parents[1] / "docs" / "audits" / "dev_019_amq_coverage_batch2.v1.json").read_text())
    signature = prior["amq_to_8cub_770_sol_facts"][0]["signature"]
    # The treasury seed has no transaction identifier.  The replay page is
    # the discovery source for the recovered compact signature.
    client = ReplayClient([[{"signature": signature}]], {signature: _transaction(version=1)})
    result = run_one_page(conn, job_id=job, client=client, budget=DiscoveryBudget(max_rpc_calls=2, max_seconds=30, max_hops=2), now=2)
    assert result["coverage"]["status"] == "COMPLETE_SIGNATURE_WINDOW"
    graph = causal_graph(conn)
    assert [(edge["sender"], edge["receiver"], edge["lamports"]) for edge in graph] == [(AMQ, EIGHT, 770_000_000_000)]
    children = enqueue_adjacent_jobs(conn, job_id=job, max_hops=2, now=2)
    assert len(children) == 1
    assert tuple(conn.execute("SELECT address,direction,hop FROM wt_mesh_jobs WHERE job_id=?", (children[0],)).fetchone()) == (EIGHT, OUTBOUND, 1)
    assert enqueue_adjacent_jobs(conn, job_id=job, max_hops=2, now=2) == children
    assert conn.execute("SELECT count(*) FROM wt_mesh_signature_decodes").fetchone()[0] == 1


def test_incomplete_page_resumes_same_cursor_before_any_advance_and_deduplicates_facts():
    conn = _conn(); job = seed_confirmed_treasuries(conn, [AMQ], now=1)[0]
    record_signature_page(conn, job_id=job, signatures=["a", "b"], now=1)
    record_transaction_result(conn, job_id=job, signature="a", transaction=_transaction(slot=1), now=1)
    assert finalize_page(conn, job_id=job, now=1)["status"] == "INCOMPLETE_SIGNATURE_WINDOW"
    assert conn.execute("SELECT state FROM wt_mesh_jobs WHERE job_id=?", (job,)).fetchone()[0] == PAGE_INCOMPLETE
    client = ReplayClient([], {"b": _transaction(slot=2)})
    result = run_one_page(conn, job_id=job, client=client, budget=DiscoveryBudget(max_rpc_calls=1, max_seconds=30, max_hops=2), now=2)
    assert result["coverage"]["status"] == "COMPLETE_SIGNATURE_WINDOW"
    assert conn.execute("SELECT next_page FROM wt_mesh_jobs WHERE job_id=?", (job,)).fetchone()[0] == 2
    assert conn.execute("SELECT count(*) FROM wt_mesh_funding_facts").fetchone()[0] == 2


def test_provider_limit_stops_with_durable_resume_point_and_no_false_absence():
    conn = _conn(); job = seed_confirmed_treasuries(conn, [AMQ], now=1)[0]
    client = ReplayClient([[{"signature": "a"}, {"signature": "b"}]], {"a": _transaction(), "b": _transaction()}, limit_after=2)
    result = run_one_page(conn, job_id=job, client=client, budget=DiscoveryBudget(max_rpc_calls=3, max_seconds=30, max_hops=2), now=2)
    assert result["status"] == PROVIDER_LIMIT_STOP
    assert conn.execute("SELECT state FROM wt_mesh_jobs WHERE job_id=?", (job,)).fetchone()[0] == PROVIDER_LIMIT_STOP
    assert page_coverage(conn, job_id=job)["status"] == "INCOMPLETE_SIGNATURE_WINDOW"
    resume_provider_limited_job(conn, job_id=job, now=3)
    assert conn.execute("SELECT state FROM wt_mesh_jobs WHERE job_id=?", (job,)).fetchone()[0] == PAGE_INCOMPLETE


def test_launch_backward_requires_canonical_creator_evidence_and_read_interface_is_non_mutating():
    conn = _conn()
    assert seed_unresolved_launch(conn, launch_mint="2BVf", creator_event={"creator": CREATOR, "canonical_creator_launch": False}, now=1) is None
    assert conn.execute("SELECT state FROM wt_mesh_launch_seeds WHERE launch_mint='2BVf'").fetchone()[0] == "NEEDS_AUTHORITATIVE_CREATOR"
    job = seed_unresolved_launch(conn, launch_mint="canonical", creator_event={"creator": CREATOR, "signature": "create", "slot": 5, "transaction_index": 1, "instruction_index": 1, "canonical_creator_launch": True}, now=1)
    assert job is not None
    view = walkback_read_interface(conn, launch_mint="canonical")
    assert view["read_only"] is True and view["canonical_writes"] is False and view["requires_review"] is True


def test_roles_review_only_candidates_and_wsol_context_are_not_conflated():
    assert classify_wallet_role(wallet="t", confirmed_treasuries={"t"}, known_subproviders=set(), funding_accounts=set()) == CONFIRMED_TREASURY_MATCH
    assert classify_wallet_role(wallet="s", confirmed_treasuries=set(), known_subproviders={"s"}, funding_accounts=set()) == KNOWN_SUBPROVIDER_MATCH
    assert classify_wallet_role(wallet="f", confirmed_treasuries=set(), known_subproviders=set(), funding_accounts={"f"}) == FUNDING_ACCOUNT_MATCH
    assert classify_wallet_role(wallet="u", confirmed_treasuries=set(), known_subproviders=set(), funding_accounts=set()) == PARTIAL_LINEAGE
    candidate = review_only_candidate(wallet="u", role=PARTIAL_LINEAGE, route_complete=True, full_fingerprint=True)
    assert candidate["classification"] == NEW_TREASURY_CANDIDATE and candidate["writes_canonical_state"] is False
    conn = _conn(); job = seed_confirmed_treasuries(conn, [AMQ], now=1)[0]
    record_signature_page(conn, job_id=job, signatures=["close"], now=1)
    close = {"version": "legacy", "slot": 1, "transactionIndex": 1, "transaction": {"message": {"accountKeys": ["wsol", CREATOR], "instructions": [{"program": "spl-token", "parsed": {"type": "closeAccount", "info": {"account": "wsol", "destination": CREATOR}}}]}}, "meta": {"preBalances": [1, 0], "postBalances": [0, 1]}}
    record_transaction_result(conn, job_id=job, signature="close", transaction=close, now=1)
    assert causal_graph(conn)[0]["route_semantics"] == "ACCOUNT_CLOSE"


def test_budget_hop_and_schema_contract_are_bounded():
    assert DISCOVERY_SCHEMA_VERSION == "dev019-mesh-v1"
    conn = _conn(); job = seed_confirmed_treasuries(conn, [AMQ], now=1)[0]
    conn.execute("UPDATE wt_mesh_jobs SET hop=3 WHERE job_id=?", (job,))
    try:
        run_one_page(conn, job_id=job, client=ReplayClient([], {}), budget=DiscoveryBudget(max_rpc_calls=1, max_seconds=30, max_hops=2), now=2)
    except Exception as exc:
        assert "HOP_BUDGET_EXCEEDED" in str(exc)
    else:
        raise AssertionError("hop budget must fail closed")


def test_confirmed_treasuries_seed_both_directions_without_role_collapse():
    conn = _conn()
    jobs = seed_confirmed_treasuries(conn, ["Gzaa"])
    assert len(jobs) == 2
    assert {(row[0], row[1]) for row in conn.execute("SELECT address,direction FROM wt_mesh_jobs")} == {("Gzaa", INBOUND), ("Gzaa", OUTBOUND)}


def test_one_signature_activity_page_preserves_complete_coverage_contract():
    conn = _conn(); job = seed_confirmed_treasuries(conn, [AMQ], now=1)[0]
    client = ReplayClient([[{"signature": "only", "blockTime": 10}]], {"only": _transaction()})
    result = run_one_page(conn, job_id=job, client=client, budget=DiscoveryBudget(max_rpc_calls=2, max_seconds=30, max_hops=2), now=2, page_limit=1)
    assert result["coverage"]["status"] == "COMPLETE_SIGNATURE_WINDOW"
    assert client.calls == 2


def test_known_intermediary_is_bounded_review_context_not_confirmed_identity():
    conn = _conn()
    job_id = seed_known_intermediary(conn, treasury_seed="Gza", address="AMQ", hop=1)
    row = conn.execute("SELECT seed_kind,seed_value,address,hop FROM wt_mesh_jobs WHERE job_id=?", (job_id,)).fetchone()
    assert dict(row) == {"seed_kind": "TREASURY", "seed_value": "Gza", "address": "AMQ", "hop": 1}


def test_dust_and_cycles_are_retained_but_never_expand_discovery_jobs():
    conn = _conn(); seed_confirmed_treasuries(conn, [AMQ], now=1)
    root = conn.execute("SELECT job_id FROM wt_mesh_jobs WHERE address=? AND direction=?", (AMQ, OUTBOUND)).fetchone()[0]
    record_signature_page(conn, job_id=root, signatures=["dust", "material"], now=1)
    record_transaction_result(conn, job_id=root, signature="dust", transaction=_transaction(receiver="dust", lamports=1), now=1)
    record_transaction_result(conn, job_id=root, signature="material", transaction=_transaction(receiver=EIGHT), now=1)
    finalize_page(conn, job_id=root, now=1)
    children = enqueue_adjacent_jobs(conn, job_id=root, max_hops=2, now=1)
    assert len(children) == 1
    child = children[0]
    record_signature_page(conn, job_id=child, signatures=["cycle"], now=2)
    record_transaction_result(conn, job_id=child, signature="cycle", transaction=_transaction(sender=EIGHT, receiver=AMQ, slot=20), now=2)
    finalize_page(conn, job_id=child, now=2)
    assert enqueue_adjacent_jobs(conn, job_id=child, max_hops=2, now=2) == []
    assert len(causal_graph(conn)) == 3


def test_store_is_temporary_bounded_and_has_no_raw_payload_columns(tmp_path):
    with pytest.raises(DiscoveryError, match="DISCOVERY_STORE_MUST_BE_TEMPORARY"):
        open_isolated_store(str(tmp_path / "not-allowed.db"))
    with tempfile.TemporaryDirectory(dir="/private/tmp", prefix="dev019-engine-") as root:
        conn = open_isolated_store(str(Path(root) / "evidence.db"))
        columns = {row[1] for row in conn.execute("PRAGMA table_info(wt_mesh_funding_facts)")}
        assert not {"raw", "payload", "block", "transaction_json"} & columns
        conn.close()
