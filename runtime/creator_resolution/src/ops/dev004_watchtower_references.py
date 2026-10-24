"""Explicit fresh-disposable extension of DEV004's unchanged typed v1 owner.
Only reference namespaces change. No attribution, queue, outbox or price writes.
"""
import hashlib, importlib.util, json
from pathlib import Path
_spec=importlib.util.spec_from_file_location('_dev004_reference_base',Path(__file__).with_name('dev004_membership_references.py'))
base=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(base)
WATCHTOWER='04265d9f-6eb2-568c-a49e-9253091a4dbb'
OUTBOX_POP='walkback_watchtower_confirmed_projection_v1'
FUNDING_POP='watchtower_behavioural_paginated_lineage_20260825'
VERSION='DEV004_TYPED_MEMBERSHIP_REFERENCE_V2_WATCHTOWER'
COLS=['mint','operator_id','legacy_event_id','reference_kind','identity_event_id','confirmed_match_id','membership_assigned_at','outbox_event_id','funding_mint','funding_signature','outcome_mint']

def predicates(alias):
 p=alias+'.'
 identity=base.IDENTITY.replace('NEW.',p);match=base.MATCH.replace('NEW.',p)
 outbox=f"""({p}operator_id='{WATCHTOWER}' AND {p}source_population_id='{OUTBOX_POP}'
 AND {p}event_id=('WATCHTOWER_CONFIRMED:'||{p}mint||':'||CAST({p}assigned_at AS TEXT))
 AND EXISTS(SELECT 1 FROM monitor_admission_outbox o WHERE o.membership_id={p}event_id
 AND o.operation_id={p}operator_id AND o.mint={p}mint AND o.committed_at={p}assigned_at
 AND o.semantic_version='WATCHTOWER_CONFIRMED_V1')
 AND EXISTS(SELECT 1 FROM wt_attribution_outcomes a WHERE a.mint={p}mint))"""
 funding=f"""({p}operator_id='{WATCHTOWER}' AND {p}source_population_id='{FUNDING_POP}'
 AND EXISTS(SELECT 1 FROM wt_walkback_queue q WHERE q.mint={p}mint AND q.funder_sig={p}event_id)
 AND EXISTS(SELECT 1 FROM wt_attribution_outcomes a WHERE a.mint={p}mint
 AND a.operator_id={p}operator_id AND a.outcome_type='CANONICAL_OPERATOR_REACHED'))"""
 return {k:'COALESCE(('+v+'),0)' for k,v in {'IDENTITY_EVENT':identity,'CONFIRMED_MATCH':match,'MEMBERSHIP_COMMIT':outbox,'FUNDING_SIGNATURE':funding}.items()}
P=predicates('NEW')
KIND="CASE WHEN NEW.event_id IS NULL THEN 'NONE' "+' '.join("WHEN "+v+" THEN '"+k+"'" for k,v in P.items())+" ELSE 'INVALID' END"
EXPR=["NEW.mint","NEW.operator_id","NEW.event_id",KIND,
 f"CASE WHEN NEW.event_id IS NOT NULL AND {P['IDENTITY_EVENT']} THEN NEW.event_id END",
 f"CASE WHEN NEW.event_id IS NOT NULL AND {P['CONFIRMED_MATCH']} THEN NEW.event_id END",
 'NEW.assigned_at',
 f"CASE WHEN NEW.event_id IS NOT NULL AND {P['MEMBERSHIP_COMMIT']} THEN (SELECT o.event_id FROM monitor_admission_outbox o WHERE o.membership_id=NEW.event_id) END",
 f"CASE WHEN NEW.event_id IS NOT NULL AND {P['FUNDING_SIGNATURE']} THEN NEW.mint END",
 f"CASE WHEN NEW.event_id IS NOT NULL AND {P['FUNDING_SIGNATURE']} THEN NEW.event_id END",
 f"CASE WHEN NEW.event_id IS NOT NULL AND (({P['MEMBERSHIP_COMMIT']}) OR ({P['FUNDING_SIGNATURE']})) THEN NEW.mint END"]
REFERENCE='''CREATE TABLE operator_membership_reference_v1 (
 mint TEXT PRIMARY KEY REFERENCES operator_launch_membership(mint) ON DELETE CASCADE,
 operator_id TEXT NOT NULL REFERENCES operators(operator_id), legacy_event_id TEXT,
 reference_kind TEXT NOT NULL CHECK(reference_kind IN ('NONE','IDENTITY_EVENT','CONFIRMED_MATCH','MEMBERSHIP_COMMIT','FUNDING_SIGNATURE')),
 identity_event_id TEXT,confirmed_match_id TEXT,membership_assigned_at INTEGER NOT NULL,
 outbox_event_id TEXT,funding_mint TEXT,funding_signature TEXT,outcome_mint TEXT,
 CHECK(typeof(membership_assigned_at)='integer'),
 CHECK(outbox_event_id IS NULL OR (length(CAST(outbox_event_id AS BLOB))=64 AND outbox_event_id NOT GLOB '*[^0-9a-f]*')),
 CHECK(length(CAST(mint AS BLOB)) BETWEEN 1 AND 128),
 CHECK(length(CAST(operator_id AS BLOB)) BETWEEN 1 AND 128),
 CHECK(legacy_event_id IS NULL OR length(CAST(legacy_event_id AS BLOB)) BETWEEN 1 AND 128),
 CHECK((reference_kind='NONE' AND legacy_event_id IS NULL AND identity_event_id IS NULL AND confirmed_match_id IS NULL AND outbox_event_id IS NULL AND funding_mint IS NULL AND funding_signature IS NULL AND outcome_mint IS NULL)
 OR (reference_kind='IDENTITY_EVENT' AND legacy_event_id IS NOT NULL AND identity_event_id IS NOT NULL AND identity_event_id=legacy_event_id AND confirmed_match_id IS NULL AND outbox_event_id IS NULL AND funding_mint IS NULL AND funding_signature IS NULL AND outcome_mint IS NULL)
 OR (reference_kind='CONFIRMED_MATCH' AND legacy_event_id IS NOT NULL AND confirmed_match_id IS NOT NULL AND confirmed_match_id=legacy_event_id AND identity_event_id IS NULL AND outbox_event_id IS NULL AND funding_mint IS NULL AND funding_signature IS NULL AND outcome_mint IS NULL)
 OR (reference_kind='MEMBERSHIP_COMMIT' AND legacy_event_id IS NOT NULL AND outbox_event_id IS NOT NULL AND identity_event_id IS NULL AND confirmed_match_id IS NULL AND funding_mint IS NULL AND funding_signature IS NULL AND outcome_mint IS NOT NULL AND outcome_mint=mint)
 OR (reference_kind='FUNDING_SIGNATURE' AND legacy_event_id IS NOT NULL AND funding_mint IS NOT NULL AND funding_mint=mint AND funding_signature IS NOT NULL AND funding_signature=legacy_event_id AND identity_event_id IS NULL AND confirmed_match_id IS NULL AND outbox_event_id IS NULL AND outcome_mint IS NOT NULL AND outcome_mint=mint)),
 FOREIGN KEY(identity_event_id,operator_id) REFERENCES operator_identity_events(event_id,operator_id),
 FOREIGN KEY(confirmed_match_id,operator_id,mint) REFERENCES confirmed_operation_matches(match_id,operator_id,mint),
 FOREIGN KEY(outbox_event_id,legacy_event_id,operator_id,mint,membership_assigned_at) REFERENCES monitor_admission_outbox(event_id,membership_id,operation_id,mint,committed_at),
 FOREIGN KEY(funding_mint,funding_signature) REFERENCES wt_walkback_queue(mint,funder_sig),
 FOREIGN KEY(outcome_mint) REFERENCES wt_attribution_outcomes(mint)
)'''
OBJECTS={k:v for k,v in base.OBJECTS.items() if k in ['dev004_identity_event_operator_key','dev004_confirmed_match_operator_mint_key','ix_operator_launch_membership_operator']}
OBJECTS.update({
 'dev004_outbox_membership_identity_key':'CREATE UNIQUE INDEX dev004_outbox_membership_identity_key ON monitor_admission_outbox(event_id,membership_id,operation_id,mint,committed_at)',
 'dev004_queue_funding_identity_key':'CREATE UNIQUE INDEX dev004_queue_funding_identity_key ON wt_walkback_queue(mint,funder_sig)',
 'operator_membership_reference_v1':REFERENCE})
for action in ['INSERT','UPDATE']:
 name='dev004_membership_validate_'+action.lower()
 cap="SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM operator_launch_membership WHERE mint=NEW.mint) AND EXISTS(SELECT 1 FROM operator_launch_membership LIMIT 1 OFFSET 49999) THEN RAISE(ABORT,'membership capacity') END;" if action=='INSERT' else "SELECT CASE WHEN NEW.mint IS NOT OLD.mint THEN RAISE(ABORT,'membership identity is immutable') END;"
 OBJECTS[name]=f"CREATE TRIGGER {name} BEFORE {action} ON operator_launch_membership BEGIN {cap} SELECT CASE WHEN NEW.event_id IS NOT NULL AND ("+'+'.join('('+x+')' for x in P.values())+")<>1 THEN RAISE(ABORT,'unknown or ambiguous membership reference') END; END"
OBJECTS['dev004_membership_project_insert']='CREATE TRIGGER dev004_membership_project_insert AFTER INSERT ON operator_launch_membership BEGIN INSERT INTO operator_membership_reference_v1('+','.join(COLS)+') VALUES('+','.join(EXPR)+'); END'
OBJECTS['dev004_membership_project_update']='CREATE TRIGGER dev004_membership_project_update AFTER UPDATE ON operator_launch_membership BEGIN UPDATE operator_membership_reference_v1 SET '+','.join(k+'='+v for k,v in zip(COLS[1:],EXPR[1:]))+" WHERE mint=NEW.mint; SELECT CASE WHEN changes()<>1 THEN RAISE(ABORT,'missing typed membership reference') END; END"
for action in ['INSERT','UPDATE']:
 name='dev004_reference_validate_'+action.lower()
 immutable="SELECT CASE WHEN EXISTS(SELECT 1 FROM operator_launch_membership m WHERE m.mint=OLD.mint AND m.operator_id=OLD.operator_id AND m.event_id IS OLD.legacy_event_id AND m.assigned_at=OLD.membership_assigned_at) AND ("+' OR '.join('NEW.'+k+' IS NOT OLD.'+k for k in COLS)+") THEN RAISE(ABORT,'typed reference is canonical projection') END;" if action=='UPDATE' else ''
 OBJECTS[name]=f"CREATE TRIGGER {name} BEFORE {action} ON operator_membership_reference_v1 BEGIN {immutable} SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM operator_launch_membership m WHERE m.mint=NEW.mint AND m.operator_id=NEW.operator_id AND m.event_id IS NEW.legacy_event_id AND m.assigned_at=NEW.membership_assigned_at) THEN RAISE(ABORT,'typed reference disagrees with membership') END; END"
OBJECTS['dev004_reference_preserve']=base.OBJECTS['dev004_reference_preserve']

def initialize(c,*,disposable_root,allow_disposable_v2=False):
 """Fresh DEV only; no upgrade/rewrite of an existing v1 sidecar or its rows."""
 base.profile(c,disposable_root,allow_disposable_v2)
 for t in ['operators','operator_identity_events','confirmed_operation_matches','monitor_admission_outbox','wt_walkback_queue','wt_attribution_outcomes']:
  if not c.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name=?",(t,)).fetchone():raise base.ReferenceContractError('PARENT_SCHEMA_REQUIRED:'+t)
 c.execute('SAVEPOINT dev004_watchtower_refs')
 try:
  old=c.execute("SELECT sql FROM sqlite_schema WHERE name='operator_launch_membership'").fetchone()
  if old and base.normalized(old[0])!=base.normalized(base.MEMBERSHIP):
   if c.execute('SELECT 1 FROM operator_launch_membership LIMIT 1').fetchone() or c.execute("SELECT 1 FROM sqlite_schema WHERE name='operator_membership_reference_v1'").fetchone():raise base.ReferenceContractError('FRESH_EMPTY_BOOTSTRAP_REQUIRED')
   c.execute('DROP TABLE operator_launch_membership');old=None
  if old is None:c.execute(base.MEMBERSHIP)
  for name,sql in OBJECTS.items():
   actual=c.execute('SELECT sql FROM sqlite_schema WHERE name=?',(name,)).fetchone()
   if actual:
    if base.normalized(actual[0])!=base.normalized(sql):raise base.ReferenceContractError('INCOMPATIBLE_OBJECT:'+name)
   else:c.execute(sql)
  validate(c);c.execute('RELEASE dev004_watchtower_refs')
 except BaseException:
  c.execute('ROLLBACK TO dev004_watchtower_refs');c.execute('RELEASE dev004_watchtower_refs');raise
 return {'contract':VERSION,'max_memberships':base.MAX_MEMBERSHIPS,'attribution_writes':0,'outbox_state_writes':0}
def validate(c):
 base.validate(c)
 if c.execute('SELECT 1 FROM operator_launch_membership m JOIN operator_membership_reference_v1 r ON r.mint=m.mint WHERE r.membership_assigned_at IS NOT m.assigned_at LIMIT 1').fetchone():raise base.ReferenceContractError('REFERENCE_TIMESTAMP_MISMATCH')
 return True

def resolve(c,row):
 """Read-only shared contract for preflight, including durable outbox hash proof."""
 names=['mint','operator_id','source_population_id','assigned_at','event_id'];params=[row[k] for k in names];ps=predicates('r');sql='WITH r('+','.join(names)+') AS (VALUES(?,?,?,?,?)) SELECT '+','.join(ps.values())+' FROM r'
 values=c.execute(sql,params).fetchone()
 if row['event_id'] is None:return {'kind':'NONE','supported':True}
 matches=[k for k,v in zip(ps,values) if v]
 if len(matches)!=1:return {'kind':'UNSUPPORTED','supported':False,'eligible_branches':matches}
 kind=matches[0];result={'kind':kind,'supported':True}
 if kind=='MEMBERSHIP_COMMIT':
  old=c.row_factory;c.row_factory=None
  try:o=c.execute('SELECT event_id,operation_id,mint,membership_id,committed_at,semantic_version,opening_id,state FROM monitor_admission_outbox WHERE membership_id=?',(row['event_id'],)).fetchone()
  finally:c.row_factory=old
  expected=hashlib.sha256(json.dumps(['monitor-live-admission.v1',o[1],o[2],o[3],int(o[4]),o[5],o[6]],separators=(',',':')).encode()).hexdigest()
  if expected!=o[0]:return {'kind':'UNSUPPORTED','supported':False,'cause':'OUTBOX_IDENTITY_HASH_MISMATCH'}
  result.update(parent='monitor_admission_outbox',parent_columns=['event_id','membership_id','operation_id','mint','committed_at'],parent_values=[o[0],o[3],o[1],o[2],o[4]],outcome_mint=row['mint'],outbox_state=o[7])
 elif kind=='FUNDING_SIGNATURE':result.update(parent='wt_walkback_queue',parent_columns=['mint','funder_sig'],parent_values=[row['mint'],row['event_id']],outcome_mint=row['mint'])
 return result
