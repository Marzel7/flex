"""Explicit fail-closed classification of currently configured state sources."""
SOURCES={
 'logsSubscribe':'TRANSACTION_BOUNDARY_ONLY',
 'getTransaction':'TRANSACTION_BOUNDARY_ONLY',
 'accountSubscribe':'SLOT_BOUNDARY_ONLY',
 'programSubscribe':'SLOT_BOUNDARY_ONLY',
 'retained_execution_trace':'INSTRUCTION_BOUNDARY_STATE_UNAVAILABLE',
 'validator_account_write_hook':'INSTRUCTION_BOUNDARY_STATE_UNAVAILABLE',
}
def classify(name):return SOURCES.get(name,'INSTRUCTION_BOUNDARY_STATE_UNAVAILABLE')
def qualified():return False
def submission_capability():return 'NONE'
