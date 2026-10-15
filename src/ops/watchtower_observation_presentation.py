"""Read-only labels for already selected Watchtower observations."""


def qualify_observation_labels(conn, rows, operator_id):
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for row in rows:
        mint = row['mint']
        member = conn.execute('SELECT operator_id FROM operator_launch_membership WHERE mint=?', (mint,)).fetchone() if 'operator_launch_membership' in tables else None
        admitted = member is not None and member[0] == operator_id
        queue = conn.execute('SELECT intelligence_outcome,path_state FROM wt_walkback_queue WHERE mint=?', (mint,)).fetchone() if 'wt_walkback_queue' in tables else None
        decision = conn.execute('SELECT outcome,first_rejection_reason FROM wt_watchtower_promotion_outcomes WHERE mint=? ORDER BY last_attempted_at DESC LIMIT 1', (mint,)).fetchone() if 'wt_watchtower_promotion_outcomes' in tables else None
        ledger = conn.execute('SELECT detection_source,creator_extraction_method FROM wt_watchtower_launches WHERE mint=?', (mint,)).fetchone() if 'wt_watchtower_launches' in tables else None
        row['canonical_membership'] = admitted if 'operator_launch_membership' in tables else None
        row['walkback_status'] = queue[0] if queue else None
        row['walkback_path_state'] = queue[1] if queue else None
        row['admission_outcome'] = decision[0] if decision else None
        row['admission_reason'] = decision[1] if decision else None
        row['observation_provenance'] = (ledger[0] or ledger[1]) if ledger else None
        row['admission_label'] = (
            'CONFIRMED / ADMITTED' if admitted else
            'REJECTED / NOT ADMITTED' if decision and decision[0].startswith('REJECTED_') else
            'LINEAGE INCOMPLETE / NOT ADMITTED' if queue and queue[0] == 'LINEAGE_GAP' else
            'DETECTED / NOT ADMITTED' if 'operator_launch_membership' in tables else 'ADMISSION UNKNOWN'
        )
