from src.ops.wsol_10_sol_four_step_operation import DETECTOR_VERSION, DISPLAY_NAME, OPERATOR_ID
def test_byzantine_is_display_name_not_detector_identity():
    assert DISPLAY_NAME == 'Byzantine'
    assert DETECTOR_VERSION == 'WSOL_10_SOL_FOUR_STEP_PROVISION_CLOSE.v1'
    assert OPERATOR_ID == 'd8ee4d7a-fcd6-5a5b-b897-24f6ab56e334'
