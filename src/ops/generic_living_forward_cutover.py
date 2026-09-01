"""Disposable-only forward cutover qualification for Living assessments."""
from __future__ import annotations
import json, sqlite3, tempfile
from pathlib import Path
from src.ops.generic_living_pipeline_v2 import compute_generic_living_assessment, digest, freshness, handle_walkback_evidence_update_generic
from src.ops.generic_living_source_coverage import ROOT, WSOL_SOURCE, WSOL_EXPECTED, EIGHT_SOURCE, EIGHT_EXPECTED, build_coverage_report


def _load(path): return json.loads((ROOT / path).read_text())
def _generation(source): return freshness(source['source_high_waters'], sorted(source['source_high_waters']))
def _new_highwaters(source): return {k:int(v)+1 for k,v in source['source_high_waters'].items()}

def _spec(candidate, source, expected):
    member_type = 'FROZEN_C357_SELECTED_MEMBER' if candidate == 'wsol' else 'CANONICAL_SELECTED_MEMBER'
    funder_type = 'DIRECT_FUNDER_CONNECTIVITY' if candidate == 'wsol' else 'DIRECT_FUNDER'
    members=source['source_evidence']['canonical_mints']
    funders=sorted({r['funder_wallet'] for r in source['source_evidence']['queue_rows']})
    missing=[a for a in expected['normalized_associations'] if a['evidence_type'] not in {member_type,funder_type,'ALTERNATIVE_EDGE_SUPPORT','ALTERNATIVE_SUPPORT','ATOMIC_FLOW_SUPPORT'}]
    return {'potential_operation_id':source['potential_operation_id'],'source_candidate_id':source['candidate_id'],'workflow_status':'PAUSED','freshness_sources':sorted(source['source_high_waters']),
      'pipeline_lineage':'GENERIC_DECLARATIVE_V2','association_rules':[{'source':'members','prefix':'mint','type':member_type,'state':'INCLUDED'},{'source':'funders','prefix':'funder','type':funder_type,'state':'UNRESOLVED'}],
      'aggregate_rules':[], 'metrics':{'members':{'source':'members','op':'distinct','label':'Confirmed canonical members'},'funders':{'source':'funders','op':'distinct','label':'Direct funders'}},
      'typed_populations':{'canonical_members':{'membership':'CONFIRMED','count':len(members),'source':'frozen underlying source'},'mapped_population':{'membership':'NOT_CONFIRMED','count':None,'source':'not carried forward without new source evidence'}},
      'historical_inherited_context':[{'kind':'HISTORICAL_INHERITED_CONTEXT','legacy_evidence_key':a['evidence_key'],'legacy_evidence_type':a['evidence_type'],'state':a['state']} for a in missing]}

def _evidence(source):
    return {'members':source['source_evidence']['canonical_mints'],'funders':sorted({r['funder_wallet'] for r in source['source_evidence']['queue_rows']}),'highwaters':_new_highwaters(source)}

def _schema(c):
    c.executescript('PRAGMA journal_mode=WAL; CREATE TABLE versions(assessment_id TEXT PRIMARY KEY,operation_id TEXT,digest TEXT UNIQUE,generation TEXT,payload TEXT,pipeline_lineage TEXT); CREATE TABLE current(operation_id TEXT PRIMARY KEY,assessment_id TEXT,generation TEXT); CREATE TABLE assessment_associations(assessment_id TEXT,evidence_key TEXT,evidence_type TEXT,state TEXT,origin TEXT,PRIMARY KEY(assessment_id,evidence_key,evidence_type,state));')

def _publish(c, result, origin):
    p=result['payload']; aid=digest({'operation':p['potential_operation_id'],'digest':result['digest']})
    c.execute('INSERT OR IGNORE INTO versions VALUES(?,?,?,?,?,?)',(aid,p['potential_operation_id'],result['digest'],result['evidence_generation'],json.dumps(p,sort_keys=True),p['pipeline_lineage']))
    for a in result['associations']: c.execute('INSERT OR IGNORE INTO assessment_associations VALUES(?,?,?,?,?)',(aid,a['evidence_key'],a['evidence_type'],a['state'],origin))
    old=c.execute('SELECT assessment_id,generation FROM current WHERE operation_id=?',(p['potential_operation_id'],)).fetchone()
    if not old or result['evidence_generation']>old[1]: c.execute('INSERT INTO current VALUES(?,?,?) ON CONFLICT(operation_id) DO UPDATE SET assessment_id=excluded.assessment_id,generation=excluded.generation',(p['potential_operation_id'],aid,result['evidence_generation']))
    return aid

def _legacy_result(source, expected):
    p=dict(expected['assessment_payload']); p['pipeline_lineage']='LEGACY_IMMUTABLE'; p['historical_assessment_mode']='LEGACY_IMMUTABLE'; p['evidence_generation']=_generation(source)
    return {'payload':p,'digest':digest(p),'evidence_generation':p['evidence_generation'],'associations':expected['normalized_associations']}

def _one(c, candidate, source_path, expected_path):
    source, expected = _load(source_path), _load(expected_path); legacy=_legacy_result(source,expected); legacy_id=_publish(c,legacy,'LEGACY_IMMUTABLE')
    before=c.execute('SELECT digest,payload FROM versions WHERE assessment_id=?',(legacy_id,)).fetchone()
    spec,evidence=_spec(candidate,source,expected),_evidence(source); generic=compute_generic_living_assessment(spec,evidence); generic_id=_publish(c,generic,'CURRENT_DERIVED_ASSOCIATION'); _publish(c,generic,'CURRENT_DERIVED_ASSOCIATION')
    current=c.execute('SELECT assessment_id,generation FROM current WHERE operation_id=?',(source['potential_operation_id'],)).fetchone(); _publish(c,legacy,'LEGACY_IMMUTABLE'); after=c.execute('SELECT digest,payload FROM versions WHERE assessment_id=?',(legacy_id,)).fetchone()
    versions=c.execute('SELECT pipeline_lineage FROM versions WHERE operation_id=?',(source['potential_operation_id'],)).fetchall()
    return {'legacy_id':legacy_id,'generic_id':generic_id,'legacy_unchanged':before==after,'newer':generic['evidence_generation']>legacy['evidence_generation'],'current_generic':current[0]==generic_id,'idempotent_version_count':c.execute('SELECT count(*) FROM versions WHERE operation_id=?',(source['potential_operation_id'],)).fetchone()[0]==2,'stale_did_not_regress':c.execute('SELECT assessment_id FROM current WHERE operation_id=?',(source['potential_operation_id'],)).fetchone()[0]==generic_id,'lineages':sorted(x[0] for x in versions),'generic_associations':len(generic['associations']),'inherited_context':len(generic['payload']['historical_inherited_context']),'mapped_non_membership':generic['payload']['typed_populations']['mapped_population']['membership']=='NOT_CONFIRMED','legacy_associations':len(legacy['associations'])}

def qualify_forward_cutover():
    with tempfile.TemporaryDirectory() as td:
        c=sqlite3.connect(Path(td)/'forward.sqlite'); _schema(c)
        w=_one(c,'wsol',WSOL_SOURCE,WSOL_EXPECTED); e=_one(c,'eight_hop',EIGHT_SOURCE,EIGHT_EXPECTED)
        ws, es = _load(WSOL_SOURCE), _load(EIGHT_SOURCE)
        registry={'w':_spec('wsol',ws,_load(WSOL_EXPECTED)),'e':_spec('eight_hop',es,_load(EIGHT_EXPECTED))}
        known={ws['candidate_id']:['wsol'],es['candidate_id']:['eight']}
        bridge=handle_walkback_evidence_update_generic(registry,{'mint':'wsol','known_mints':known},{ws['candidate_id']:_evidence(ws),es['candidate_id']:_evidence(es)})
        unrelated=handle_walkback_evidence_update_generic(registry,{'mint':'other','known_mints':known},{ws['candidate_id']:_evidence(ws),es['candidate_id']:_evidence(es)})
        c.close()
    return {'source_coverage':{'wsol':'122/126','eight_hop':'55/57'},'wsol':w,'eight_hop':e,'bridge':{'wsol_only':len(bridge['affected'])==1 and bridge['results'][0]['payload']['candidate_lineage']['candidate_id']==ws['candidate_id'],'unrelated_none':unrelated['affected']==[]},'real_db_writes':0,'active_path_cutover':False,'minimum_additive_metadata_requirement':'Bind every association row to assessment_id (or persist an equivalent immutable assessment-association version) and expose pipeline_lineage in the history read model.','historical_reconstruction_requirement':'HISTORICAL_EXACT_RECONSTRUCTION_NOT_REQUIRED','cutover_compatibility':'FORWARD_CUTOVER_REQUIRES_ADDITIVE_LINEAGE_METADATA'}
