import hashlib

from src.ops.pumpfun_curve_birth_recovery import (
    PUMP_PROGRAM,
    recover_creation_from_complete_curve_history,
)


_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def _b58(data):
    number = int.from_bytes(data, "big")
    result = ""
    while number:
        number, remainder = divmod(number, 58)
        result = _B58[remainder] + result
    return "1" * (len(data) - len(data.lstrip(b"\0"))) + (result or "1")


def _tx(*, mint="mint", curve="curve", creator="creator", kind="create", slot=7, failed=False):
    discriminator = hashlib.sha256(f"global:{kind}".encode()).digest()[:8]
    creator_index = 7 if kind == "create" else 5
    accounts = [mint, "x", curve, "assoc", "four", creator, "six", creator]
    return {"slot": slot, "meta": {"err": {"bad": 1} if failed else None}, "transaction": {"message": {
        "accountKeys": accounts + [PUMP_PROGRAM],
        "instructions": [{"programId": PUMP_PROGRAM, "accounts": list(range(len(accounts))), "data": _b58(discriminator)}],
    }}}


def _recover(rows, transactions, *, limit=10, candidates=2):
    calls = []
    result = recover_creation_from_complete_curve_history(
        mint="mint", bonding_curve="curve", fixed_limit=limit, candidate_limit=candidates,
        signature_history_source=lambda curve, requested: rows,
        transaction_source=lambda signature: calls.append(signature) or transactions.get(signature),
    )
    return result, calls


def test_oldest_create_and_no_pagination():
    result, calls = _recover([{"signature": "new"}, {"signature": "old"}], {"old": _tx()})
    assert result.qualification == "RECOVERED"
    assert result.creation_signature == "old" and result.create_type == "CREATE"
    assert calls == ["old"]


def test_oldest_create_v2():
    result, _ = _recover([{"signature": "old"}], {"old": _tx(kind="create_v2")})
    assert result.qualification == "RECOVERED" and result.create_type == "CREATE_V2"


def test_full_page_fails_closed_without_candidate_fetch():
    result, calls = _recover([{"signature": str(n)} for n in range(10)], {}, limit=10)
    assert result.qualification == "HISTORY_EXCEEDS_BOUND" and not calls


def test_wrong_mint_failed_and_curve_mismatch_are_rejected():
    result, _ = _recover([{"signature": "old"}], {"old": _tx(mint="other")})
    assert result.qualification == "CREATE_MINT_MISMATCH"
    result, _ = _recover([{"signature": "old"}], {"old": _tx(curve="other")})
    assert result.qualification == "CURVE_MISMATCH"
    result, _ = _recover([{"signature": "old"}], {"old": _tx(failed=True)})
    assert result.qualification == "NO_VALID_CREATE_IN_FIXED_CANDIDATES"


def test_second_oldest_candidate_and_fixed_exhaustion():
    result, calls = _recover([{"signature": "new"}, {"signature": "middle"}, {"signature": "old"}],
                             {"old": {}, "middle": _tx()}, candidates=2)
    assert result.qualification == "RECOVERED" and calls == ["old", "middle"]
    result, calls = _recover([{"signature": "new"}, {"signature": "middle"}, {"signature": "old"}],
                             {"old": {}, "middle": _tx()}, candidates=1)
    assert result.qualification == "NO_VALID_CREATE_IN_FIXED_CANDIDATES" and calls == ["old"]


def test_duplicate_signatures_are_idempotent():
    result, calls = _recover([{"signature": "old"}, {"signature": "old"}], {"old": _tx()}, candidates=2)
    assert result.qualification == "RECOVERED" and calls == ["old"]
