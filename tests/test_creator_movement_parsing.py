"""
Tests for parse_creator_sol_movement.

Run with: python -m pytest tests/test_creator_movement_parsing.py -v
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.creators.movement import parse_creator_sol_movement, MovementEvent

CREATOR = "CreatorAddr1111111111111111111111111111111111"
OTHER_A = "OtherAddrA111111111111111111111111111111111111"
OTHER_B = "OtherAddrB111111111111111111111111111111111111"
FEE_AMT = 5_000


def _base_tx(sig="sig1111", slot=100, ts=1_700_000_000):
    return {
        "signature": sig,
        "slot":      slot,
        "timestamp": ts,
        "fee":       FEE_AMT,
        "feePayer":  CREATOR,
    }


# ---------------------------------------------------------------------------
# 1. Simple inbound SOL
# ---------------------------------------------------------------------------

def test_simple_inbound():
    tx = {
        **_base_tx(),
        "nativeTransfers": [
            {"fromUserAccount": OTHER_A, "toUserAccount": CREATOR, "amount": 2_000_000_000},
        ],
        "feePayer": OTHER_A,   # creator is NOT fee payer
        "fee": FEE_AMT,
    }
    evt = parse_creator_sol_movement(tx, CREATOR)
    assert evt is not None
    assert evt.net_lamports == 2_000_000_000
    assert evt.direction == "in"
    assert evt.abs_lamports == 2_000_000_000
    assert evt.counterparty == OTHER_A
    assert evt.signature == "sig1111"


# ---------------------------------------------------------------------------
# 2. Simple outbound SOL (creator is fee payer)
# ---------------------------------------------------------------------------

def test_simple_outbound_with_fee():
    send_amount = 1_000_000_000
    tx = {
        **_base_tx(),
        "nativeTransfers": [
            {"fromUserAccount": CREATOR, "toUserAccount": OTHER_A, "amount": send_amount},
        ],
        "feePayer": CREATOR,
        "fee": FEE_AMT,
    }
    evt = parse_creator_sol_movement(tx, CREATOR)
    assert evt is not None
    # net = 0 - send_amount - fee
    assert evt.net_lamports == -(send_amount + FEE_AMT)
    assert evt.direction == "out"
    assert evt.counterparty == OTHER_A


# ---------------------------------------------------------------------------
# 3. Multiple transfers — creator sends to two accounts, receives from one
# ---------------------------------------------------------------------------

def test_multiple_transfers():
    tx = {
        **_base_tx(),
        "nativeTransfers": [
            {"fromUserAccount": OTHER_A, "toUserAccount": CREATOR, "amount": 3_000_000_000},
            {"fromUserAccount": CREATOR, "toUserAccount": OTHER_A, "amount": 1_000_000_000},
            {"fromUserAccount": CREATOR, "toUserAccount": OTHER_B, "amount": 500_000_000},
        ],
        "feePayer": CREATOR,
        "fee": FEE_AMT,
    }
    evt = parse_creator_sol_movement(tx, CREATOR)
    assert evt is not None
    # net = 3_000_000_000 - 1_000_000_000 - 500_000_000 - FEE_AMT
    expected = 3_000_000_000 - 1_000_000_000 - 500_000_000 - FEE_AMT
    assert evt.net_lamports == expected
    assert evt.direction == "in"   # net positive


# ---------------------------------------------------------------------------
# 4. Fee-only transaction (no nativeTransfers, creator pays fee)
# ---------------------------------------------------------------------------

def test_fee_only_no_native_transfers():
    """Creator pays fee but no SOL transfers — uses balance diff fallback."""
    pre  = [10_000_000_000, 5_000_000_000]
    post = [10_000_000_000 - FEE_AMT, 5_000_000_000]
    tx = {
        "signature": "sig_fee_only",
        "slot": 200,
        "timestamp": 1_700_000_001,
        "fee": FEE_AMT,
        "feePayer": CREATOR,
        # No nativeTransfers
        "meta": {"preBalances": pre, "postBalances": post, "fee": FEE_AMT},
        "transaction": {
            "message": {
                "accountKeys": [CREATOR, OTHER_A],
            }
        },
    }
    evt = parse_creator_sol_movement(tx, CREATOR)
    # Fee deducted from creator balance = -5000 lamports
    assert evt is not None
    assert evt.net_lamports == -FEE_AMT
    assert evt.direction == "out"
    assert evt.abs_lamports == FEE_AMT


# ---------------------------------------------------------------------------
# 5. Mixed in/out — net is positive (more in than out)
# ---------------------------------------------------------------------------

def test_mixed_net_positive():
    tx = {
        **_base_tx(),
        "nativeTransfers": [
            {"fromUserAccount": OTHER_A, "toUserAccount": CREATOR, "amount": 5_000_000_000},
            {"fromUserAccount": CREATOR, "toUserAccount": OTHER_B, "amount": 1_000_000_000},
        ],
        "feePayer": OTHER_A,   # creator NOT fee payer
        "fee": FEE_AMT,
    }
    evt = parse_creator_sol_movement(tx, CREATOR)
    assert evt is not None
    assert evt.net_lamports == 4_000_000_000   # 5B in - 1B out, no fee adj
    assert evt.direction == "in"
    # Counterparty: largest single transfer is 5B from OTHER_A
    assert evt.counterparty == OTHER_A


# ---------------------------------------------------------------------------
# 6. Creator not in tx — returns None
# ---------------------------------------------------------------------------

def test_creator_not_in_tx():
    tx = {
        **_base_tx(),
        "nativeTransfers": [
            {"fromUserAccount": OTHER_A, "toUserAccount": OTHER_B, "amount": 1_000_000_000},
        ],
        "feePayer": OTHER_A,
    }
    evt = parse_creator_sol_movement(tx, CREATOR)
    assert evt is None


# ---------------------------------------------------------------------------
# 7. Missing signature — returns None
# ---------------------------------------------------------------------------

def test_missing_signature():
    tx = {
        "slot": 100,
        "nativeTransfers": [
            {"fromUserAccount": OTHER_A, "toUserAccount": CREATOR, "amount": 1_000_000_000},
        ],
    }
    evt = parse_creator_sol_movement(tx, CREATOR)
    assert evt is None


# ---------------------------------------------------------------------------
# 8. Zero net movement — returns None
# ---------------------------------------------------------------------------

def test_zero_net_returns_none():
    """Creator sends exactly as much as received with no fee adjustment."""
    amt = 1_000_000_000
    tx = {
        **_base_tx(),
        "nativeTransfers": [
            {"fromUserAccount": OTHER_A, "toUserAccount": CREATOR, "amount": amt},
            {"fromUserAccount": CREATOR, "toUserAccount": OTHER_A, "amount": amt},
        ],
        "feePayer": OTHER_A,   # no fee
        "fee": 0,
    }
    evt = parse_creator_sol_movement(tx, CREATOR)
    assert evt is None


# ---------------------------------------------------------------------------
# 9. source='reconcile' propagates through
# ---------------------------------------------------------------------------

def test_reconcile_source():
    tx = {
        **_base_tx(),
        "nativeTransfers": [
            {"fromUserAccount": OTHER_A, "toUserAccount": CREATOR, "amount": 1_000_000_000},
        ],
        "feePayer": OTHER_A,
        "fee": FEE_AMT,
    }
    evt = parse_creator_sol_movement(tx, CREATOR, source="reconcile")
    assert evt is not None
    assert evt.source == "reconcile"


if __name__ == "__main__":
    import traceback
    tests = [
        test_simple_inbound,
        test_simple_outbound_with_fee,
        test_multiple_transfers,
        test_fee_only_no_native_transfers,
        test_mixed_net_positive,
        test_creator_not_in_tx,
        test_missing_signature,
        test_zero_net_returns_none,
        test_reconcile_source,
    ]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"  ✓ {t.__name__}")
            passed += 1
        except Exception as e:
            print(f"  ✗ {t.__name__}: {e}")
            traceback.print_exc()
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
