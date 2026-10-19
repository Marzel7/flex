"""Exceptional request-evidence gate for terminally unqualified runs."""
TOKEN_DATA_ABORTED_RUN_RESULT_ADMISSIBILITY_VERSION='TOKEN_DATA_ABORTED_RUN_RESULT_ADMISSIBILITY_V1'
def assess_request_result(*,request,compact_results,digest_verified,run_aborted_before_dispatch=False):
    if run_aborted_before_dispatch:return False,'RESULT_AFTER_ABORT'
    if request.get('state')!='SUCCEEDED':return False,'NON_SUCCESS_TERMINAL_STATE'
    if request.get('outcome_state')=='OUTCOME_UNRETAINED':return False,'OUTCOME_UNRETAINED'
    if not request.get('request_identity_hash') or not request.get('parameters_digest'):return False,'MISSING_IDENTITY_OR_DIGEST'
    if not digest_verified:return False,'DIGEST_MISMATCH'
    matches=[x for x in compact_results if x.get('execution_request_id')==request['request_identity_hash']]
    if len(matches)!=1:return False,'MISSING_OR_CONFLICTING_COMPACT_RESULT'
    if not matches[0].get('parser_version'):return False,'MISSING_PARSER_VERSION'
    return True,'ADMISSIBLE_FROM_UNQUALIFIED_RUN'
