import json
from src.ops.wsol_10_sol_four_step_operation import AMOUNT_LAMPORTS, ATOMIC_SEQUENCE, is_strict_match
def evidence(**overrides):
 row={'selection_status':'SELECTED','hop_depth':1,'mechanism':'WSOL_WRAP_CLOSE','amount_lamports':AMOUNT_LAMPORTS,'has_create':1,'has_sync_native':1,'has_close':1,'instruction_order_json':json.dumps(ATOMIC_SEQUENCE)}; row.update(overrides); return row
def test_exact_b1_predicate_accepts_four_step_route(): assert is_strict_match(evidence())
def test_same_amount_without_exact_atomic_lifecycle_is_rejected(): assert not is_strict_match(evidence(instruction_order_json=json.dumps(['createAccount','initializeAccount','transfer','syncNative','closeAccount'])))
def test_wrong_amount_or_unselected_edge_is_rejected():
 assert not is_strict_match(evidence(amount_lamports=AMOUNT_LAMPORTS-1))
 assert not is_strict_match(evidence(selection_status='REJECTED'))
