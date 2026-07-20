"""X29.7 — Operations-Centred Discovery.

Covers:
  1. Role derivation (roles_for_wallet) recognizes TREASURY/SUBPROVIDER/
     CREATOR from both wt_provisioning_edges AND wt_watchtower_launches
     direct columns.
  2. A wallet can hold multiple roles (Mesh-style: treasury also subprov).
  3. Variable-depth lineage: 2-hop (treasury->creator, no subprov), 3-hop
     (treasury->subprov->creator), never a fabricated fixed-4 template.
  4. Fan-out count / historical launches / funding mechanisms attach ONLY
     to the Subprovider node; subprovider_count attaches ONLY to Treasury.
  5. build_lineage never fabricates a node with no evidence, and terminates
     safely (max_hops) rather than looping on malformed cyclic data.
  6. operations_summary aggregates role counts + mechanism/boundary
     distributions from already-persisted data, zero new calculation.
  7. The confirmed WATCHTOWER example (Treasury 9hGcx -> Subprovider ANen
     -> Creator HTR9U7) resolves exactly, matching the live-verified trace.
  8. Zero RPC anywhere in these modules.
  9. Existing intelligence (funding_topology/behaviour/mechanism/boundary/
     wallet_quality) modules are untouched by this sprint.
"""
from __future__ import annotations

import inspect
import sqlite3

import pytest

from src.ops.operational_lineage import (
    roles_for_wallet, build_lineage,
    ROLE_TREASURY, ROLE_SUBPROVIDER, ROLE_CREATOR,
)
from src.ops.operations_summary import summarize_operation, build_operations_summary


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript("""
        CREATE TABLE wt_confirmed_treasuries (treasury TEXT PRIMARY KEY);
        CREATE TABLE wt_provisioning_edges (
            edge_id TEXT PRIMARY KEY, edge_type TEXT, from_wallet TEXT, to_wallet TEXT,
            first_observed_by_flex INTEGER, last_observed_by_flex INTEGER
        );
        CREATE TABLE wt_watchtower_launches (
            mint TEXT, creator_wallet TEXT, subprov_wallet TEXT, treasury_wallet TEXT,
            create_time INTEGER, funding_mechanism TEXT
        );
        CREATE TABLE wt_funding_boundary (
            launch_mint TEXT, boundary_status TEXT
        );
    """)
    return c


def _seed_three_hop(conn):
    conn.execute("INSERT INTO wt_confirmed_treasuries VALUES ('TREASURY1')")
    conn.execute(
        "INSERT INTO wt_provisioning_edges VALUES ('e1','SUBPROV_TO_CREATOR','SUBPROV1','CREATOR1',1,1)"
    )
    conn.execute(
        "INSERT INTO wt_watchtower_launches VALUES ('MINT1','CREATOR1','SUBPROV1','TREASURY1',1000,'WSOL_WRAP_CLOSE')"
    )
    conn.commit()


# ─────────────────────── 1. Role derivation from both sources ───────────────────────

def test_roles_for_treasury_via_confirmed_table(conn):
    _seed_three_hop(conn)
    assert roles_for_wallet(conn, "TREASURY1") == [ROLE_TREASURY]


def test_roles_for_subprovider_via_edges_and_launches(conn):
    _seed_three_hop(conn)
    assert roles_for_wallet(conn, "SUBPROV1") == [ROLE_SUBPROVIDER]


def test_roles_for_creator_via_edges_and_launches(conn):
    _seed_three_hop(conn)
    assert roles_for_wallet(conn, "CREATOR1") == [ROLE_CREATOR]


def test_unknown_wallet_has_no_roles(conn):
    _seed_three_hop(conn)
    assert roles_for_wallet(conn, "NOBODY") == []


def test_role_recognized_from_watchtower_launches_alone_no_edges_row(conn):
    """A launch with subprov_wallet/treasury_wallet/creator_wallet but NO
    corresponding wt_provisioning_edges row (exactly the confirmed WATCHTOWER
    example's real shape) must still resolve roles correctly."""
    conn.execute("INSERT INTO wt_confirmed_treasuries VALUES ('T2')")
    conn.execute(
        "INSERT INTO wt_watchtower_launches VALUES ('M2','C2','S2','T2',500,'WSOL_WRAP_CLOSE')"
    )
    conn.commit()
    assert roles_for_wallet(conn, "T2") == [ROLE_TREASURY]
    assert roles_for_wallet(conn, "S2") == [ROLE_SUBPROVIDER]
    assert roles_for_wallet(conn, "C2") == [ROLE_CREATOR]


# ─────────────────────── 2. Multi-role wallet (Mesh-style) ───────────────────────

def test_wallet_can_hold_multiple_roles(conn):
    """A treasury that is ALSO structurally a subprov elsewhere (the Mesh
    pattern) must report both roles, never collapse to one."""
    conn.execute("INSERT INTO wt_confirmed_treasuries VALUES ('DUAL')")
    conn.execute(
        "INSERT INTO wt_provisioning_edges VALUES ('e2','TREASURY_TO_SUBPROV','ROOT_TREASURY','DUAL',1,1)"
    )
    conn.execute(
        "INSERT INTO wt_provisioning_edges VALUES ('e3','SUBPROV_TO_CREATOR','DUAL','CREATOR_X',1,1)"
    )
    conn.commit()
    roles = roles_for_wallet(conn, "DUAL")
    assert ROLE_TREASURY in roles
    assert ROLE_SUBPROVIDER in roles


# ─────────────────────── 3. Variable-depth lineage ───────────────────────

def test_three_hop_lineage_treasury_subprov_creator(conn):
    _seed_three_hop(conn)
    lineage = build_lineage(conn, "CREATOR1")
    roles_in_order = [n["role"] for n in lineage["chain"]]
    assert roles_in_order == [ROLE_TREASURY, ROLE_SUBPROVIDER, ROLE_CREATOR]
    wallets_in_order = [n["wallet"] for n in lineage["chain"]]
    assert wallets_in_order == ["TREASURY1", "SUBPROV1", "CREATOR1"]


def test_two_hop_lineage_treasury_direct_to_creator_no_subprov(conn):
    """Not every operation has a subprov hop -- a direct treasury->creator
    launch must produce a 2-node chain, never a fabricated 3rd node."""
    conn.execute("INSERT INTO wt_confirmed_treasuries VALUES ('DIRECT_TREASURY')")
    conn.execute(
        "INSERT INTO wt_watchtower_launches VALUES ('M3','DIRECT_CREATOR',NULL,'DIRECT_TREASURY',100,'PLAIN_TRANSFER')"
    )
    conn.commit()
    lineage = build_lineage(conn, "DIRECT_CREATOR")
    roles_in_order = [n["role"] for n in lineage["chain"]]
    assert roles_in_order == [ROLE_TREASURY, ROLE_CREATOR]
    assert len(lineage["chain"]) == 2


def test_lineage_from_subprover_shows_full_chain_both_directions(conn):
    """Querying the MIDDLE node (subprov) must resolve both its ancestor
    (treasury) and a representative descendant (creator)."""
    _seed_three_hop(conn)
    lineage = build_lineage(conn, "SUBPROV1")
    assert lineage["primary_role"] == ROLE_SUBPROVIDER
    roles_in_order = [n["role"] for n in lineage["chain"]]
    assert roles_in_order == [ROLE_TREASURY, ROLE_SUBPROVIDER, ROLE_CREATOR]


def test_lineage_for_wallet_with_no_roles_returns_empty_chain(conn):
    _seed_three_hop(conn)
    lineage = build_lineage(conn, "NOBODY")
    assert lineage["chain"] == []
    assert lineage["primary_role"] is None


def test_lineage_never_fabricates_a_provisioning_wallet_node(conn):
    """Explicit regression for the brief's own finding: the intermediate
    'provisioning wallet' hop is NOT separately persisted anywhere in this
    schema (a wrap-close account is ephemeral) -- the chain must be exactly
    3 nodes (Treasury/Subprovider/Creator), never a fabricated 4th."""
    _seed_three_hop(conn)
    lineage = build_lineage(conn, "CREATOR1")
    assert len(lineage["chain"]) == 3
    assert "PROVISIONING_WALLET" not in [n["role"] for n in lineage["chain"]]


# ─────────────────────── 4. Properties attach only to the correct role ───────────────────────

def test_fan_out_count_attaches_only_to_subprovider_node(conn):
    _seed_three_hop(conn)
    lineage = build_lineage(conn, "CREATOR1")
    subprov_node = next(n for n in lineage["chain"] if n["role"] == ROLE_SUBPROVIDER)
    treasury_node = next(n for n in lineage["chain"] if n["role"] == ROLE_TREASURY)
    creator_node = next(n for n in lineage["chain"] if n["role"] == ROLE_CREATOR)
    assert "fan_out_count" in subprov_node["properties"]
    assert "fan_out_count" not in treasury_node["properties"]
    assert "fan_out_count" not in creator_node["properties"]


def test_subprovider_count_attaches_only_to_treasury_node(conn):
    _seed_three_hop(conn)
    lineage = build_lineage(conn, "CREATOR1")
    treasury_node = next(n for n in lineage["chain"] if n["role"] == ROLE_TREASURY)
    subprov_node = next(n for n in lineage["chain"] if n["role"] == ROLE_SUBPROVIDER)
    assert "subprovider_count" in treasury_node["properties"]
    assert "subprovider_count" not in subprov_node["properties"]


def test_fan_out_count_reflects_multiple_downstream_creators(conn):
    conn.execute("INSERT INTO wt_confirmed_treasuries VALUES ('T4')")
    for i in range(5):
        conn.execute(
            f"INSERT INTO wt_watchtower_launches VALUES ('M4_{i}','CREATOR4_{i}','SUBPROV4','T4',{100+i},'WSOL_WRAP_CLOSE')"
        )
    conn.commit()
    lineage = build_lineage(conn, "SUBPROV4")
    subprov_node = next(n for n in lineage["chain"] if n["role"] == ROLE_SUBPROVIDER)
    assert subprov_node["properties"]["fan_out_count"] == 5
    assert subprov_node["properties"]["historical_launches"] == 5


def test_creator_node_has_no_supporting_properties(conn):
    """Creator is a leaf role in this model -- no fan-out/mechanism/
    subprovider-count properties belong on it."""
    _seed_three_hop(conn)
    lineage = build_lineage(conn, "CREATOR1")
    creator_node = next(n for n in lineage["chain"] if n["role"] == ROLE_CREATOR)
    assert creator_node["properties"] == {}


# ─────────────────────── 5. Cycle safety ───────────────────────

def test_build_lineage_terminates_on_malformed_cyclic_data(conn):
    """Two subprov-like wallets funding each other in wt_provisioning_edges
    (malformed data) must not cause an infinite loop -- max_hops bounds it."""
    conn.execute("INSERT INTO wt_provisioning_edges VALUES ('c1','TREASURY_TO_SUBPROV','X','Y',1,1)")
    conn.execute("INSERT INTO wt_provisioning_edges VALUES ('c2','TREASURY_TO_SUBPROV','Y','X',1,1)")
    conn.commit()
    lineage = build_lineage(conn, "X", max_hops=5)
    assert len(lineage["chain"]) < 20  # terminates, does not hang


# ─────────────────────── 6. Operations summary aggregation ───────────────────────

def test_summarize_operation_counts_roles_and_mechanisms(conn):
    _seed_three_hop(conn)
    operation = {
        "operation_id": "op_test",
        "display_name": "Operation TEST",
        "confidence": "CONFIRMED",
        "treasuries": [{"wallet": "TREASURY1", "role": "ROOT", "launch_count": 1}],
        "launch_count": 1,
        "last_launch_at": 1000,
    }
    summary = summarize_operation(conn, operation)
    assert summary["treasury_count"] == 1
    assert summary["subprovider_count"] == 1
    assert summary["creator_count"] == 1
    assert summary["funding_mechanisms"]["WSOL_WRAP_CLOSE"]["pct"] == 100.0


def test_build_operations_summary_zero_state_on_empty_db():
    empty_db = ":memory:"
    import tempfile, os
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        result = build_operations_summary(path)
        assert result["total_operations"] == 0
        assert result["operations"] == []
    finally:
        os.unlink(path)


# ─────────────────────── 7. Confirmed WATCHTOWER example ───────────────────────

def test_confirmed_watchtower_example_resolves_exactly(conn):
    """The exact traced example from X29.5/X29.6/X29.7's briefs: Treasury
    9hGcx... funds Subprovider ANen..., which funds Creator HTR9U7....
    No fabricated Provisioning Wallet hop (per the audit's finding that
    this intermediate is not separately persisted)."""
    treasury = "9hGcxVHFajR4xMZVUBQBwbxN2J3tt1aXE5KrUB87EZk4"
    subprov = "ANenEukvmpYsyP52LgDsZN6kj3n7igjbJDTCtj4xCAXq"
    creator = "HTR9U7dkk1eEwmyFyzCzERdy3vr8CM6T8hW5FY1s24gt"
    conn.execute(f"INSERT INTO wt_confirmed_treasuries VALUES ('{treasury}')")
    conn.execute(
        "INSERT INTO wt_provisioning_edges VALUES ('e_wt','SUBPROV_TO_CREATOR',?,?,1,1)",
        (subprov, creator),
    )
    conn.execute(
        "INSERT INTO wt_watchtower_launches VALUES (?,?,?,?,?,?)",
        ("EGB4sv9ddNhWeUhnsAvpqP8xaEps4cx5bc956LPcpump", creator, subprov, treasury, 1784048633, "WSOL_WRAP_CLOSE"),
    )
    conn.commit()

    lineage = build_lineage(conn, creator)
    chain_wallets = [n["wallet"] for n in lineage["chain"]]
    chain_roles = [n["role"] for n in lineage["chain"]]
    assert chain_wallets == [treasury, subprov, creator]
    assert chain_roles == [ROLE_TREASURY, ROLE_SUBPROVIDER, ROLE_CREATOR]


# ─────────────────────── 8. Zero RPC ───────────────────────

def test_no_rpc_client_in_lineage_or_summary_modules():
    from src.ops import operational_lineage, operations_summary
    for module in (operational_lineage, operations_summary):
        source = inspect.getsource(module)
        assert "requests." not in source
        assert "getSignaturesForAddress" not in source
        assert "getTransaction" not in source
        assert "helius" not in source.lower()


# ─────────────────────── 9. Existing intelligence untouched ───────────────────────

def test_funding_topology_module_unmodified_by_this_sprint():
    """Structural guard: this sprint must not have added any lineage/role
    concepts INTO funding_topology.py -- topology classification logic
    stays exactly where X29.1/X29.5 left it, only its PRESENTATION moves."""
    from src.ops import funding_topology
    source = inspect.getsource(funding_topology)
    assert "roles_for_wallet" not in source
    assert "build_lineage" not in source


def test_attribution_outcome_module_unmodified_by_this_sprint():
    from src.ops import attribution_outcome
    source = inspect.getsource(attribution_outcome)
    assert "roles_for_wallet" not in source
    assert "build_lineage" not in source


def test_funding_boundary_module_unmodified_by_this_sprint():
    from src.ops import funding_boundary
    source = inspect.getsource(funding_boundary)
    assert "roles_for_wallet" not in source


def test_wallet_quality_module_unmodified_by_this_sprint():
    from src.ops import wallet_quality
    source = inspect.getsource(wallet_quality)
    assert "roles_for_wallet" not in source
