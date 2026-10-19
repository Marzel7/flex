"""Additive, disposable-only assessment lineage qualification."""
from __future__ import annotations
from enum import StrEnum
import json, sqlite3

class PipelineLineage(StrEnum):
    LEGACY_CANDIDATE_SPECIFIC='LEGACY_CANDIDATE_SPECIFIC'
    GENERIC_DECLARATIVE_V2='GENERIC_DECLARATIVE_V2'

LINEAGE_DDL='''
CREATE TABLE potential_operation_assessment_lineage(
 assessment_id TEXT PRIMARY KEY, potential_operation_id TEXT NOT NULL,
 pipeline_lineage TEXT NOT NULL CHECK(pipeline_lineage IN ('LEGACY_CANDIDATE_SPECIFIC','GENERIC_DECLARATIVE_V2')),
 pipeline_version TEXT NOT NULL, source_contract_version TEXT,
 association_contract_version TEXT NOT NULL, created_at INTEGER NOT NULL,
 FOREIGN KEY(assessment_id) REFERENCES potential_operation_assessment_version(assessment_id));
CREATE TABLE potential_operation_assessment_association_binding(
 assessment_id TEXT NOT NULL, association_id TEXT NOT NULL, association_contract_version TEXT NOT NULL,
 PRIMARY KEY(assessment_id,association_id), FOREIGN KEY(assessment_id) REFERENCES potential_operation_assessment_version(assessment_id));
CREATE INDEX ix_poa_lineage_operation ON potential_operation_assessment_lineage(potential_operation_id,created_at DESC);
'''

def ensure_lineage_schema(conn): conn.executescript(LINEAGE_DDL)

def persist_lineage_tx(conn,assessment,lineage,pipeline_version,source_contract_version,association_contract_version,created_at):
    conn.execute('INSERT OR IGNORE INTO potential_operation_assessment_version VALUES(?,?,?,?,?,?)',(assessment['assessment_id'],assessment['potential_operation_id'],assessment['digest'],assessment['generation'],assessment['payload'],created_at))
    conn.execute('INSERT OR IGNORE INTO potential_operation_assessment_lineage VALUES(?,?,?,?,?,?,?)',(assessment['assessment_id'],assessment['potential_operation_id'],lineage.value,pipeline_version,source_contract_version,association_contract_version,created_at))
def advance_current_tx(conn,assessment,created_at):
    old=conn.execute('SELECT assessment_id,freshness_key FROM potential_operation_current WHERE potential_operation_id=?',(assessment['potential_operation_id'],)).fetchone()
    if old and old[1]==assessment['generation'] and old[0]!=assessment['assessment_id']: raise ValueError('equal freshness conflict')
    advanced=not old or assessment['generation']>old[1]
    if advanced: conn.execute('INSERT INTO potential_operation_current VALUES(?,?,?,?) ON CONFLICT(potential_operation_id) DO UPDATE SET assessment_id=excluded.assessment_id,freshness_key=excluded.freshness_key,updated_at=excluded.updated_at',(assessment['potential_operation_id'],assessment['assessment_id'],assessment['generation'],created_at))
    return advanced

def publish_with_lineage(conn, assessment, associations, lineage:PipelineLineage, *, pipeline_version='v2', source_contract_version=None, association_contract_version='assessment-association.v1', created_at=0):
    """One short transaction: version, lineage, association bindings, then current."""
    aid=assessment['assessment_id']; op=assessment['potential_operation_id']
    conn.execute('BEGIN')
    conn.execute('INSERT OR IGNORE INTO potential_operation_assessment_version VALUES(?,?,?,?,?,?)',(aid,op,assessment['digest'],assessment['generation'],assessment['payload'],created_at))
    conn.execute('INSERT OR IGNORE INTO potential_operation_assessment_lineage VALUES(?,?,?,?,?,?,?)',(aid,op,lineage.value,pipeline_version,source_contract_version,association_contract_version,created_at))
    for association_id in associations:
        conn.execute('INSERT OR IGNORE INTO potential_operation_assessment_association_binding VALUES(?,?,?)',(aid,association_id,association_contract_version))
    old=conn.execute('SELECT freshness_key FROM potential_operation_current WHERE potential_operation_id=?',(op,)).fetchone()
    if not old or assessment['generation']>old[0]: conn.execute('INSERT INTO potential_operation_current VALUES(?,?,?,?) ON CONFLICT(potential_operation_id) DO UPDATE SET assessment_id=excluded.assessment_id,freshness_key=excluded.freshness_key,updated_at=excluded.updated_at',(op,aid,assessment['generation'],created_at))
    conn.commit()

def history_projection(conn, operation_id):
    rows=conn.execute('''SELECT v.assessment_id,v.freshness_key,l.pipeline_lineage,l.pipeline_version,l.association_contract_version,
     v.payload_json,c.assessment_id AS current_id,COUNT(b.association_id) association_count
     FROM potential_operation_assessment_version v JOIN potential_operation_assessment_lineage l USING(assessment_id)
     LEFT JOIN potential_operation_current c ON c.potential_operation_id=v.potential_operation_id
     LEFT JOIN potential_operation_assessment_association_binding b USING(assessment_id)
     WHERE v.potential_operation_id=? GROUP BY v.assessment_id,c.assessment_id ORDER BY v.freshness_key''',(operation_id,)).fetchall()
    return [{'assessment_id':r[0],'evidence_generation':r[1],'pipeline_lineage':r[2],'pipeline_version':r[3],'association_contract_version':r[4],'current':r[0]==r[6],'association_count':r[7],'inherited_historical_context':'historical_inherited_context' in json.loads(r[5])} for r in rows]
