"""X75.3A PART 7 -- Absorption safety test.

STATUS UPDATE (X76.0): the defect this file originally documented (a
population sharing even ONE wallet with a canonical operator's entity set
gets absorbed, with no minimum-overlap threshold) has since been FIXED by
X76.0 ("Canonical Merge Safety & Identity Boundary Protection") --
src/ops/canonical_merge_contract.py::evaluate_merge() now gates every
absorption decision behind a documented merge contract (wallet overlap
computed symmetrically + at least two independent identity signals + no
REJECTED review decision on any overlapping wallet). See
tests/test_x76_0_canonical_merge_safety.py for the current, authoritative
regression coverage.

Original X75.3 finding (preserved for history): X75.3's audit flagged a
latent risk in EmergingOperatorService._compose()'s canonical-family
absorption logic (src/ops/emerging_operator_service.py, around line 561):
a population sharing even ONE wallet with a canonical operator's entity
set got absorbed, with no minimum-overlap threshold and no role
distinction. Per the X75.3A task brief's explicit instruction ("Do not
change absorption logic in this milestone... If the test exposes a real
defect: stop and report it separately"), this was reported but not fixed
at the time -- X76.0 is that separate, later fix.

This file's tests still pass because the FACTS they assert (field layout,
the resulting non-absorption outcome) remain true independent of which
mechanism produces them -- but the mechanism itself has changed; see
test_bare_intersection_would_have_triggered__historical_mechanism_now_fixed
below for the explicit before/after proof.
"""
from __future__ import annotations

import os

import pytest

DV34 = "Dv34prGm2BT7Ph2n6qKLgzeLgjnii87RJJ7Db6ZQQvKM"
EFKV = "EFKVdKPrxMpofZMkPBWNe9Jp3hREmtoMZmNo7yFAMUo5"
B48K = "B48kNVXs4YK4amkBCH2XokQiv1SeiVQGHDR17xDeKAAn"
WATCHTOWER_OPERATOR_ID = "04265d9f-6eb2-568c-a49e-9253091a4dbb"

_LIVE_DB = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "database", "wt_ops_v2.db"
))


def _skip_if_no_live_db():
    if not os.path.exists(_LIVE_DB) or os.path.getsize(_LIVE_DB) < 1024:
        pytest.skip("live database/wt_ops_v2.db not present")


def test_current_absorption_check_is_asymmetric_across_member_wallets_and_treasuries():
    """DOCUMENTS A REAL DEFECT (reported separately, not fixed here).

    src/ops/emerging_operator_service.py's absorption loop builds
    canonical_entities from BOTH canonical["member_wallets"] AND
    canonical["treasuries"] (the canonical/operator side), but checks
    candidate overlap using ONLY family["member_wallets"] (the population
    side) -- family["treasuries"] is never consulted on the candidate
    side. This is why B48k/Dv34 (which shares its EFKV treasury with
    WATCHTOWER only via the family's OWN "treasuries" field, not
    "member_wallets") is currently NOT absorbed -- not because of any
    deliberate minimum-overlap or role-aware safety check, but because of
    this field-naming asymmetry. A population whose overlapping wallet
    happened to be recorded in "member_wallets" instead of "treasuries"
    would be absorbed on that ONE wallet alone, with no threshold at all.
    """
    _skip_if_no_live_db()
    try:
        from src.ops.operator_routes import _get_emerging_service
        d = _get_emerging_service().list(limit=200, debug=False)
    except Exception:
        pytest.skip("EmergingOperatorService unavailable in this environment")

    watchtower = next(
        (f for f in d.get("confirmed_operations_reconciled", []) if f.get("family_name") == "WATCHTOWER"),
        None,
    )
    b48k_dv34 = next(
        (f for f in d.get("active_investigations_reconciled", []) if "B48k" in str(f.get("family_name", ""))),
        None,
    )
    if watchtower is None or b48k_dv34 is None:
        pytest.skip("WATCHTOWER canonical card or B48k/Dv34 family not present in this snapshot")

    # The defect's precondition, verified against live data:
    assert EFKV in (watchtower.get("treasuries") or []), "expected EFKV in WATCHTOWER's treasuries"
    assert EFKV not in (watchtower.get("member_wallets") or []), (
        "expected WATCHTOWER's member_wallets to NOT independently carry EFKV "
        "(it only appears via treasuries) -- if this changes, the defect's "
        "precondition no longer holds and this test should be re-examined"
    )
    assert EFKV in (b48k_dv34.get("treasuries") or []), "expected EFKV in B48k/Dv34's treasuries"
    assert EFKV not in (b48k_dv34.get("member_wallets") or []), (
        "expected B48k/Dv34's member_wallets to NOT independently carry EFKV "
        "-- this is the exact reason absorption does not currently trigger"
    )

    # Current (accidental, not-safety-designed) outcome: NOT absorbed.
    absorbed_ids = watchtower.get("absorbed_family_ids") or []
    assert not any("0a1cc08d9cdc33b1" in str(fid) for fid in absorbed_ids), (
        "B48k/Dv34 is currently not absorbed -- if this assertion starts "
        "failing, the absorption behaviour has changed and needs review"
    )


def test_bare_intersection_would_have_triggered__historical_mechanism_now_fixed():
    """HISTORICAL: this test used to call the raw wallet-intersection
    snippet that WAS the absorption trigger prior to X76.0, to prove no
    minimum-overlap threshold existed. X76.0 ("Canonical Merge Safety &
    Identity Boundary Protection") replaced that trigger with
    src/ops/canonical_merge_contract.py::evaluate_merge(), which requires
    at least two independent identity signals (not just wallet overlap) --
    see tests/test_x76_0_canonical_merge_safety.py for the current,
    authoritative regression coverage of that contract, including a
    dedicated test proving a single shared wallet in member_wallets alone
    (this exact scenario) is now correctly REJECTED.

    This test is kept only to document what the bare intersection snippet
    itself would still do in isolation (it has no minimum-overlap logic of
    its own) -- it does not exercise the real, current absorption
    pipeline, which no longer uses this snippet at all."""
    canonical_family = {
        "family_id": "canonical:test-operator",
        "member_wallets": ["shared_wallet_1"],
        "treasuries": [],
    }
    candidate_family = {
        "family_id": "family:candidate-with-one-shared-wallet",
        "member_wallets": ["shared_wallet_1", "unrelated_wallet_2"],
        "treasuries": [],
        "launches": 1,
        "last_material_activity_at": 0,
    }

    canonical_entities = set(canonical_family.get("member_wallets") or ())
    canonical_entities.update(canonical_family.get("treasuries") or ())
    matches = [
        family for family in [candidate_family]
        if canonical_entities.intersection(family.get("member_wallets") or ())
    ]
    assert matches, (
        "The bare intersection snippet itself has no minimum-overlap "
        "threshold (expected -- this documents the historical mechanism, "
        "not the current one). See test_x76_0_canonical_merge_safety.py "
        "for proof the REAL pipeline now rejects this exact scenario."
    )

    from src.ops.canonical_merge_contract import evaluate_merge
    decision = evaluate_merge(canonical_family, candidate_family, rejected_wallets=frozenset())
    assert decision.allowed is False, (
        "The CURRENT merge contract must reject this same scenario -- "
        "single wallet overlap alone, no other identity evidence."
    )
