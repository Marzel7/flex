"""Provider-free composition of the canonical outbox consumer and existing Opening bridge."""
from __future__ import annotations
from src.ops.universal_assignment_opening_bridge import trigger, commit_qualified
from src.ops.universal_assignment_bridge_consumer import normalized_operation_id

def durable_opening_then_handoff(opening_db, universal_handoff):
    """Use the existing registry/work ledger; only the actual adapter is injected."""
    def handoff(event, opening, handoff_identity):
        assignment={'id':str(event['outbox_id']),'operation_id':normalized_operation_id(event['operation_id']),'mint':event['mint']}
        prepared=trigger(opening_db,assignment)
        if prepared['status'] != 'OPENING_WORK_READY':
            raise RuntimeError('OPENING_COMPOSITION_NOT_READY')
        result=commit_qualified(opening_db,prepared['work_id'],opening,
            lambda assignment, persisted: universal_handoff(assignment,persisted,handoff_identity))
        if result['status'] not in {'QUALIFIED_AND_HANDED_OFF','ALREADY_QUALIFIED'}:
            raise RuntimeError('QUALIFIED_OPENING_COMMIT_FAILED')
    return handoff
