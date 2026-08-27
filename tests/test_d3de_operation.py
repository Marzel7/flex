import sqlite3

from src.ops.d3de_operation import SELECTED_ROUTE, is_d0_match


def _evidence(route=SELECTED_ROUTE, *, addresses=True):
    return [{"hop_depth": depth, "mechanism": mechanism, "amount_lamports": amount,
             "wallet": f"creator-{depth}" if addresses else "new-creator",
             "candidate_parent": f"parent-{depth}" if addresses else "new-parent"}
            for depth, mechanism, amount in route]


def test_d0_matches_exact_route_with_entirely_novel_addresses():
    assert is_d0_match(_evidence(addresses=False))


def test_d0_rejects_known_addresses_when_behaviour_is_wrong():
    route = list(SELECTED_ROUTE); route[0] = (1, "PLAIN_XFER", 1)
    assert not is_d0_match(_evidence(route))


def test_d0_rejects_correct_amounts_in_wrong_semantic_order():
    route = list(SELECTED_ROUTE); route[2] = (3, "PLAIN_XFER", route[2][2])
    assert not is_d0_match(_evidence(route))


def test_d0_rejects_broad_wsol_lifecycle_without_the_ladder():
    assert not is_d0_match(_evidence(((1, "WSOL_WRAP_CLOSE", 29_999_985_000),)))


def test_d0_requires_complete_selected_evidence():
    assert not is_d0_match(_evidence(SELECTED_ROUTE[:3]))
