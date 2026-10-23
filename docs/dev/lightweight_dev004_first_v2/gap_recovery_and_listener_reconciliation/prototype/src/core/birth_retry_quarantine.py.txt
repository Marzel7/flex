"""Explicit, evidence-preserving disposition for compact birth identity conflicts.

No receipt I/O, provider acquisition or canonical writes. Caller owns the Core
transaction; reads must occur after BEGIN IMMEDIATE to serialize the decision.
"""
REASON = 'CORE_AUTHORITY_CONFLICT'


def ensure_schema(conn):
    columns = {row[1] for row in conn.execute('PRAGMA table_info(webhook_birth_queue)')}
    if 'quarantine_reason' not in columns:
        conn.execute("ALTER TABLE webhook_birth_queue ADD COLUMN quarantine_reason TEXT "
                     "CHECK(quarantine_reason IS NULL OR quarantine_reason='CORE_AUTHORITY_CONFLICT')")


def quarantine(conn, ops, row_id, signature, raw_payload, data):
    """Return True for a proven conflict (including an idempotent prior decision).

    Missing/inconsistent authority leaves the row retryable. Exceptions propagate
    to the existing retry handler. Never use a broad exception as conflict proof.
    """
    if not conn.in_transaction:
        raise RuntimeError('QUARANTINE_TRANSACTION_REQUIRED')
    row = conn.execute('SELECT signature,payload,source,consumed,quarantine_reason '
                       'FROM webhook_birth_queue WHERE id=?', (row_id,)).fetchone()
    if not row or tuple(row[:4]) != (signature, raw_payload, 'pumpportal_retry', 0):
        return False
    if row[4] == REASON:
        return True
    if row[4] is not None:
        return False
    # The receiving consumer has already validated the complete compact envelope.
    if (data.get('signature') != signature or data.get('_birth_retry_schema') != 1
            or data.get('provenance') != 'PUMPPORTAL_NEW_TOKEN'):
        return False
    mint, creator = data.get('mint'), data.get('traderPublicKey')
    if not mint or not creator:
        return False
    core = conn.execute('SELECT earliest_tx_creator,pf_ws_creator,create_tx_signature,migrated_at '
                        'FROM token_analysis WHERE mint=?', (mint,)).fetchone()
    if not core or core[3] is None:
        return False
    creators = {v for v in core[:2] if v}
    if not creators or not core[2]:
        return False
    anchor = ops.execute('SELECT status,creator,create_anchor_signature,create_anchor_audit_state '
                         'FROM wt_walkback_queue WHERE mint=?', (mint,)).fetchone()
    if (not anchor or anchor[0] != 'complete' or anchor[1] not in creators
            or anchor[2] != core[2] or anchor[3] != 'VALID'):
        return False
    # Creator disagreement alone is not proof of a permanent birth-signature conflict.
    # Keep both source-specific Core creator values intact; corroborate the
    # canonical CREATE signature with a completed VALID anchor instead.
    if signature == core[2]:
        return False
    changed = conn.execute("UPDATE webhook_birth_queue SET quarantine_reason=? "
                           "WHERE id=? AND signature=? AND payload=? AND source='pumpportal_retry' "
                           "AND consumed=0 AND quarantine_reason IS NULL",
                           (REASON, row_id, signature, raw_payload))
    return changed.rowcount == 1
