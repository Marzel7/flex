"""Parallel, config-driven Living pipeline; intentionally not wired to production."""
from __future__ import annotations
import hashlib,json,uuid
from collections import Counter
from dataclasses import dataclass
@dataclass(frozen=True)
class EvidenceSourceSpec:
 key:str; source_path:str; extractor:str='records'; identity_fields:tuple=(); provenance_fields:tuple=(); evidence_type:str=''; contributes_associations:bool=False; contributes_populations:bool=False; contributes_metrics:bool=False; contributes_resolver_keys:bool=False; dedupe_fields:tuple=(); required:bool=True; where:tuple=()
@dataclass(frozen=True)
class AssociationRuleSpec:
 source_key:str; identity_fields:tuple; evidence_type:str; association_state:str; provenance_fields:tuple=(); dedupe_fields:tuple=(); required_fields:tuple=(); resolver_participation:bool=False
 def __post_init__(self):
  if self.association_state not in {'INCLUDED','EXCLUDED','UNRESOLVED','CONTRADICTORY'}: raise ValueError('invalid association state')
def resolve_source_path(context,path,required=True):
 value=context
 for part in path.split('.'):
  if not isinstance(value,dict) or part not in value:
   if required: raise KeyError(path)
   return []
  value=value[part]
 return value
def _extract(value,kind,fields):
 if kind=='scalar': return value
 if kind=='distinct_values': return sorted(set(value))
 if kind in ('records','keyed_records','nested_records'):
  rows=value if isinstance(value,list) else list(value.values()) if isinstance(value,dict) else []
  return sorted(rows,key=lambda x:tuple(str(x.get(f,'')) for f in fields)) if rows and isinstance(rows[0],dict) else sorted(rows,key=str)
 raise ValueError(f'unknown extractor {kind}')
def _filter_records(value, predicates):
 """Candidate-agnostic equality filter used by declarative source specs."""
 if not predicates: return value
 rows=value if isinstance(value,list) else list(value.values()) if isinstance(value,dict) else []
 return [row for row in rows if isinstance(row,dict) and all(row.get(k)==v for k,v in predicates)]
def extract_evidence_sources(candidate_spec,evidence_context):
 sources=candidate_spec.get('evidence_sources',())
 return {s.key:_extract(_filter_records(resolve_source_path(evidence_context,s.source_path,s.required),s.where),s.extractor,s.dedupe_fields or s.identity_fields) for s in sources}
def derive_declared_associations(candidate_spec,extracted_sources):
 out={}
 for rule in candidate_spec.get('association_rules_v2',()):
  for row in extracted_sources.get(rule.source_key,[]):
   row=row if isinstance(row,dict) else {'value':row};
   if any(row.get(k) is None for k in rule.required_fields): continue
   identity='|'.join(str(row.get(k,'')) for k in rule.identity_fields); dedupe=tuple(row.get(k,'') for k in (rule.dedupe_fields or ('__type__','__identity__'))); dedupe=tuple(rule.evidence_type if x=='__type__' else identity if x=='__identity__' else x for x in dedupe)
   item={'potential_operation_id':candidate_spec['potential_operation_id'],'evidence_identity':identity,'evidence_type':rule.evidence_type,'association_state':rule.association_state,'source_key':rule.source_key,'provenance':{k:row.get(k) for k in rule.provenance_fields},'dedupe_identity':dedupe}
   if dedupe in out and out[dedupe]!=item: raise ValueError('association conflict')
   out[dedupe]=item
 return sorted(out.values(),key=lambda x:(x['evidence_type'],x['evidence_identity'],x['association_state'],x['source_key']))

def digest(value): return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def freshness(highwaters, sources): return ':'.join(f'{int(highwaters.get(s,0)):012d}' for s in sources)
def normalize_freshness(record):
 """Canonicalize frozen-fixture and runtime freshness representations."""
 values=[record[k] for k in ('evidence_generation','frozen_boundary') if record.get(k) is not None]
 if len(set(values))>1: raise ValueError('freshness normalization conflict')
 if not values: raise ValueError('freshness missing')
 return values[0]
def validate_registry(registry):
 ids=[x['potential_operation_id'] for x in registry.values()]; sources=[x['source_candidate_id'] for x in registry.values()]
 if len(ids)!=len(set(ids)) or len(sources)!=len(set(sources)): raise ValueError('duplicate stable or source identity')
 return True
def derive_associations(spec,evidence):
 out=[]
 for rule in spec['association_rules']:
  for value in evidence.get(rule['source'],[]): out.append({'evidence_key':f"{rule['prefix']}:{value}",'evidence_type':rule['type'],'state':rule['state']})
 for rule in spec.get('aggregate_rules',[]): out.append({'evidence_key':rule['key'],'evidence_type':rule['type'],'state':rule['state']})
 return out
def derive_metrics(spec,evidence,associations):
 out={}
 for name,rule in spec['metrics'].items():
  value=evidence.get(rule['source'],[]); out[name]={'value':len(set(value)) if rule['op']=='distinct' else len(value),'semantic_label':rule['label'],'source_lineage':rule['source']}
 out['association_counts']={'value':dict(Counter(x['state'] for x in associations)),'semantic_label':'Evidence associations','source_lineage':'derived'};return out
def compute_generic_living_assessment(spec,evidence):
 associations=derive_associations(spec,evidence); metrics=derive_metrics(spec,evidence,associations); gen=freshness(evidence['highwaters'],spec['freshness_sources'])
 payload={'schema_version':'generic_living_pipeline_v2','pipeline_lineage':spec.get('pipeline_lineage','GENERIC_DECLARATIVE_V2'),'potential_operation_id':spec['potential_operation_id'],'candidate_lineage':{'candidate_id':spec['source_candidate_id']},'status':spec['workflow_status'],'promotion':'NO','detector_activation':'NO','typed_metrics':metrics,'typed_populations':spec.get('typed_populations',{}),'historical_inherited_context':spec.get('historical_inherited_context',[]),'caveats':spec.get('caveats',[]),'evidence_generation':gen,'source_high_waters':evidence['highwaters']};return {'payload':payload,'digest':digest(payload),'associations':associations,'evidence_generation':gen}
def resolve_affected_living_candidates(registry,event):
 return sorted(spec['potential_operation_id'] for spec in registry.values() if event.get('mint') in set(event.get('known_mints',{}).get(spec['source_candidate_id'],[])))
def resolve_living_lineage(registry,source_candidate_id):
 return next((x['potential_operation_id'] for x in registry.values() if x['source_candidate_id']==source_candidate_id),'NO_LIVING_MAPPING')
def publish_disposable(conn, result):
 """Short disposable Phase-1 publisher; never opens a production database."""
 p=result['payload']; oid=p['potential_operation_id']; aid=str(uuid.uuid5(uuid.NAMESPACE_URL,oid+':'+result['digest'])); gen=result['evidence_generation']
 conn.execute('BEGIN');conn.execute('INSERT OR IGNORE INTO potential_operation_identity VALUES(?,?,?,?)',(oid,p['status'],json.dumps({'candidate_id':p['candidate_lineage']['candidate_id']}),0))
 for a in result['associations']:
  conn.execute('INSERT OR IGNORE INTO potential_operation_evidence_association VALUES(?,?,?,?,?,?,?)',(str(uuid.uuid5(uuid.NAMESPACE_URL,oid+a['evidence_key']+a['state'])),oid,a['evidence_key'],a['evidence_type'],a['state'],'{}',0))
 conn.execute('INSERT OR IGNORE INTO potential_operation_assessment_version VALUES(?,?,?,?,?,?)',(aid,oid,result['digest'],gen,json.dumps(p,sort_keys=True),0));old=conn.execute('SELECT assessment_id,freshness_key FROM potential_operation_current WHERE potential_operation_id=?',(oid,)).fetchone()
 if old and old[1]==gen and old[0]!=aid: raise ValueError('equal freshness conflict')
 if not old or gen>old[1]:conn.execute('INSERT INTO potential_operation_current VALUES(?,?,?,?) ON CONFLICT(potential_operation_id) DO UPDATE SET assessment_id=excluded.assessment_id,freshness_key=excluded.freshness_key',(oid,aid,gen,0))
 conn.commit();return aid
def handle_walkback_evidence_update_generic(registry,event,evidence_by_source):
 affected=resolve_affected_living_candidates(registry,event);return {'affected':affected,'results':[compute_generic_living_assessment(spec,evidence_by_source[spec['source_candidate_id']]) for spec in registry.values() if spec['potential_operation_id'] in affected]}
