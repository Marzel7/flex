"""Feature-gated bounded generic Living components; default remains inactive."""
from __future__ import annotations
import hashlib, json, os, sqlite3, time, uuid
from src.ops.generic_living_pipeline_v2 import compute_generic_living_assessment, resolve_affected_living_candidates
from src.ops.generic_living_lineage_metadata import PipelineLineage, publish_with_lineage, persist_lineage_tx, advance_current_tx

FEATURE_FLAG='LIVING_GENERIC_BOUNDED_DISPATCH_ENABLED'
MAX_PUBLICATION_CONCURRENCY_RETRIES=3  # initial attempt plus at most three restarts
RETRYABLE_SQLITE_CONCURRENCY_ERRORS=frozenset(('SQLITE_BUSY_SNAPSHOT','SQLITE_BUSY'))
ORDINARY_BUSY_BACKOFF_SECONDS=.01

class ConcurrentPublicationRetryExhausted(RuntimeError):
    """A bounded canonical publication restart did not obtain a writable snapshot."""
def generic_dispatch_enabled(env=None): return (env or os.environ).get(FEATURE_FLAG,'false').lower()=='true'

def build_generic_evidence_context(spec,event_context,source_reader):
    """Generic config-driven adapter; source_reader owns persisted-source access."""
    evidence=source_reader(spec,event_context)
    return None if not evidence.get('relevant_new_evidence',False) else evidence

def classify_relevance(spec,event_context,source_reader):
    evidence=source_reader(spec,event_context)
    if evidence.get('relevant_new_evidence'): return 'RELEVANT_NEW_EVIDENCE'
    return 'GLOBAL_HIGH_WATER_ADVANCED_BUT_NOT_RELEVANT' if evidence.get('global_advanced') else 'NO_NEW_EVIDENCE'

def publish_generic_living_assessment(conn,result,association_ids,created_at=0):
    p=result['payload']; aid=str(uuid.uuid5(uuid.NAMESPACE_URL,p['potential_operation_id']+':'+result['digest']))
    assessment={'assessment_id':aid,'potential_operation_id':p['potential_operation_id'],'digest':result['digest'],'generation':result['evidence_generation'],'payload':json.dumps(p,sort_keys=True)}
    publish_with_lineage(conn,assessment,association_ids,PipelineLineage.GENERIC_DECLARATIVE_V2,pipeline_version='generic_living_pipeline_v2',source_contract_version='generic_living_source.v1',created_at=created_at)
    return aid

def _association_content(a):
    return {'potential_operation_id':a['potential_operation_id'],'evidence_identity':a['evidence_identity'],'evidence_type':a['evidence_type'],'association_state':a['association_state'],'source_key':a.get('source_key',''),'provenance':a.get('provenance',{})}
def generic_association_id(a):
    return str(uuid.uuid5(uuid.NAMESPACE_URL,'generic-association:'+json.dumps(_association_content(a),sort_keys=True,separators=(',',':'))))
def persist_generic_associations(conn,target_operation_id,associations,assessment_id,transaction_observer=None):
    """Persist immutable candidate-owned assertions and bind them to one assessment."""
    ids=[]
    for a in associations:
        if a.get('potential_operation_id')!=target_operation_id: raise ValueError('candidate ownership mismatch')
        aid=generic_association_id(a); rationale=json.dumps({'source_key':a.get('source_key',''),'provenance':a.get('provenance',{})},sort_keys=True)
        row=conn.execute('SELECT potential_operation_id,evidence_key,evidence_type,state,rationale_json FROM potential_operation_evidence_association WHERE association_id=?',(aid,)).fetchone()
        if transaction_observer is not None:
            transaction_observer('after_association_pre_lookup',aid,row is None)
        wanted=(target_operation_id,a['evidence_identity'],a['evidence_type'],a['association_state'],rationale)
        if row and tuple(row)!=wanted: raise ValueError('immutable association content conflict')
        conn.execute('INSERT OR IGNORE INTO potential_operation_evidence_association VALUES(?,?,?,?,?,?,?)',(aid,*wanted,0)); ids.append(aid)
    for aid in ids:
        owner_row=conn.execute('SELECT potential_operation_id FROM potential_operation_evidence_association WHERE association_id=?',(aid,)).fetchone()
        if owner_row is None:
            raise RuntimeError('association insert did not produce a visible deterministic row')
        owner=owner_row[0]
        if owner!=target_operation_id: raise ValueError('assessment binding ownership mismatch')
        conn.execute('INSERT OR IGNORE INTO potential_operation_assessment_association_binding VALUES(?,?,?)',(assessment_id,aid,'assessment-association.v1'))
    return ids

def publish_generic_assessment_atomic(conn,result,associations,created_at=0,fail_stage=None,failure_injector=None,transaction_observer=None):
    """The sole generic write boundary: assessment, assertions, lineage, bindings, current."""
    p=result['payload']; owner=p['potential_operation_id'];
    if any(a.get('potential_operation_id')!=owner for a in associations): raise ValueError('candidate ownership mismatch')
    aid=str(uuid.uuid5(uuid.NAMESPACE_URL,owner+':'+result['digest']))
    assessment={'assessment_id':aid,'potential_operation_id':owner,'digest':result['digest'],'generation':result['evidence_generation'],'payload':json.dumps(p,sort_keys=True)}
    def inject(stage):
        if failure_injector is not None:
            failure_injector(stage, {"potential_operation_id": owner, "assessment_id": aid})
    retry_count=0; retry_causes=[]
    attempt_ids=[]
    while True:
        attempt_ids.append(retry_count + 1)
        try:
            # Associations are first so a stale read-to-write WAL upgrade is
            # observable before this publisher owns the write lock.  All rows
            # remain in this one transaction and commit together below.
            conn.execute('BEGIN'); persist_generic_associations(conn,owner,associations,aid,transaction_observer)
            inject('after_associations')
            if fail_stage=='associations': raise RuntimeError('injected failure')
            persist_lineage_tx(conn,assessment,PipelineLineage.GENERIC_DECLARATIVE_V2,'generic_living_pipeline_v2','generic_living_source.v1','assessment-association.v1',created_at)
            inject('after_assessment')
            if fail_stage in ('assessment','lineage','bindings'): raise RuntimeError('injected failure')
            inject('after_lineage')
            inject('after_bindings')
            advanced=advance_current_tx(conn,assessment,created_at)
            inject('after_current_before_commit')
            if fail_stage in ('current','before_commit'): raise RuntimeError('injected failure')
            conn.commit(); return {'assessment_id':aid,'advanced_current':advanced,'reused':not advanced,
                                   'retry_count':retry_count,'retry_reason':None if not retry_count else retry_causes[-1],
                                   'retry_exhausted':False,'retry_causes':retry_causes,
                                   'attempt_ids':attempt_ids,'full_transaction_restart':retry_count>0}
        except sqlite3.OperationalError as exc:
            error_name=getattr(exc,'sqlite_errorname',None)
            if error_name not in RETRYABLE_SQLITE_CONCURRENCY_ERRORS:
                conn.rollback(); raise
            conn.rollback(); retry_causes.append(error_name)
            if retry_count >= MAX_PUBLICATION_CONCURRENCY_RETRIES:
                raise ConcurrentPublicationRetryExhausted(
                    f'publication concurrency retry exhausted after {retry_count + 1} attempts') from exc
            retry_count += 1
            # The transaction and SQLite write lock are released before this
            # bounded yield; it never sleeps while a publication is open.
            if error_name=='SQLITE_BUSY': time.sleep(ORDINARY_BUSY_BACKOFF_SECONDS * retry_count)
        except Exception:
            conn.rollback(); raise

def dispatch_bounded_living(registry,event_context,source_reader,legacy_handler,conn=None,env=None):
    """Exactly one path per event: generic when enabled, legacy otherwise."""
    if not generic_dispatch_enabled(env): return {'path':'LEGACY_FALLBACK','result':legacy_handler(event_context)}
    affected=resolve_affected_living_candidates(registry,event_context); results=[]
    for spec in registry.values():
        if spec['potential_operation_id'] not in affected: continue
        evidence=build_generic_evidence_context(spec,event_context,source_reader)
        if evidence is None: continue
        result=compute_generic_living_assessment(spec,evidence)
        if conn is not None: publish_generic_living_assessment(conn,result,evidence.get('association_ids',()))
        results.append(result)
    return {'path':'GENERIC_DECLARATIVE_V2','affected':affected,'results':results}
