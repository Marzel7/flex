import sqlite3

from src.ops.watchtower_observation_presentation import qualify_observation_labels
from src.ops.operation_summary import _launch_presentation


def test_retained_statuses_are_read_only_and_membership_authoritative():
    c = sqlite3.connect(':memory:')
    c.executescript('''
      CREATE TABLE operator_launch_membership(mint TEXT PRIMARY KEY,operator_id TEXT);
      CREATE TABLE wt_walkback_queue(mint TEXT PRIMARY KEY,intelligence_outcome TEXT,path_state TEXT);
      CREATE TABLE wt_watchtower_promotion_outcomes(mint TEXT,outcome TEXT,first_rejection_reason TEXT,last_attempted_at INTEGER);
      CREATE TABLE wt_watchtower_launches(mint TEXT PRIMARY KEY,detection_source TEXT,creator_extraction_method TEXT);
      INSERT INTO operator_launch_membership VALUES('admitted','wt');
      INSERT INTO wt_walkback_queue VALUES('A','WATCHTOWER_CONFIRMED','UPSTREAM_EXPANDING');
      INSERT INTO wt_walkback_queue VALUES('B','LINEAGE_GAP','ARCHIVAL_GAP');
      INSERT INTO wt_watchtower_promotion_outcomes VALUES('A','REJECTED_TREASURY_EVIDENCE_CONFLICT','TREASURY_EVIDENCE_CONFLICT',1);
      INSERT INTO wt_watchtower_launches VALUES('B','OPENING_CATCHUP','CLOSE_ACCOUNT_DESTINATION');
    ''')
    c.commit()
    c.execute('PRAGMA query_only=ON')
    rows = [{'mint': m, 'create_time': i} for i, m in enumerate(['A', 'B', 'admitted', 'unknown'])]
    before = c.total_changes
    qualify_observation_labels(c, rows, 'wt')
    assert [r['mint'] for r in rows] == ['A', 'B', 'admitted', 'unknown']
    assert rows[0]['admission_label'] == 'REJECTED / NOT ADMITTED'
    assert rows[0]['admission_reason'] == 'TREASURY_EVIDENCE_CONFLICT'
    assert rows[1]['admission_label'] == 'LINEAGE INCOMPLETE / NOT ADMITTED'
    assert rows[1]['observation_provenance'] == 'OPENING_CATCHUP'
    assert rows[2]['admission_label'] == 'CONFIRMED / ADMITTED'
    assert sum(r['canonical_membership'] for r in rows) == 1
    assert _launch_presentation(rows[1])['walkback_path_state'] == 'ARCHIVAL_GAP'
    assert c.total_changes == before
    c.close()


def test_missing_membership_authority_is_unknown():
    c = sqlite3.connect(':memory:')
    rows = [{'mint': 'unknown'}]
    qualify_observation_labels(c, rows, 'wt')
    assert rows[0]['canonical_membership'] is None
    assert rows[0]['admission_label'] == 'ADMISSION UNKNOWN'
    c.close()
