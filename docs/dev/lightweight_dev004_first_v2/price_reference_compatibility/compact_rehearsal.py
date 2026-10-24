"""One direct-source full retained-population import attempt; no V1 copies.
FK-on disposable import, precise first blocker, no production activation.
"""
import importlib.util,json,os,resource,sqlite3,sys,hashlib,socket
from pathlib import Path
ROOT=Path(__file__).resolve().parents[4];REF=Path('/private/tmp/dev004-compact-creator-qualified/classification_closure');SRC=Path('/Users/kevinkeaveney/Dev/claude/flex/database')
def load(p,n):
 s=importlib.util.spec_from_file_location(n,p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
price=load(ROOT/'docs/dev/lightweight_dev004_first_v2/price_reference_compatibility/qualify.py','price');old=price.old;typed=load(ROOT/'runtime/listener/src/ops/dev004_watchtower_references.py','typed_watchtower_compat')

def import_row(c,table,keys,row):
 existing=c.execute('SELECT * FROM "'+table+'" WHERE '+' AND '.join('"'+k+'" IS ?' for k in keys),tuple(row[k] for k in keys)).fetchone()
 if existing:
  assert dict(existing)==row,'DUPLICATE_CONTENT_CONFLICT'
  return 0
 info=c.execute('PRAGMA table_xinfo("'+table+'")').fetchall();known={r[1] for r in info};writable={r[1] for r in info if r[6]==0};assert set(row)<=known,'UNQUALIFIED_TARGET_COLUMN'
 columns=[k for k in row if k in writable]
 c.execute('INSERT INTO "'+table+'"('+','.join('"'+k+'"' for k in columns)+') VALUES('+','.join('?' for _ in columns)+')',tuple(row[k] for k in columns))
 actual=c.execute('SELECT * FROM "'+table+'" WHERE '+' AND '.join('"'+k+'" IS ?' for k in keys),tuple(row[k] for k in keys)).fetchone()
 assert dict(actual)==row,'GENERATED_OR_IMPORTED_VALUE_CHANGED'
 return 1

def run(base):
 assert not base.exists();base.mkdir();resource.setrlimit(resource.RLIMIT_FSIZE,(64*1024*1024,64*1024*1024));before=os.statvfs(base).f_bavail*os.statvfs(base).f_frsize;assert before>=2_000_000_000+128*1024*1024
 def deny(*a,**kw):raise RuntimeError('NETWORK_FORBIDDEN')
 socket.socket.connect=deny;socket.socket.connect_ex=deny;socket.create_connection=deny
 def audit(event,args):
  if event=='sqlite3.connect':
   raw=str(args[0]);p=Path(raw[5:].split('?',1)[0] if raw.startswith('file:') else raw).resolve();assert p.is_relative_to(base) or raw.startswith('file:') and 'mode=ro' in raw,'WRITABLE_SOURCE'
 sys.addaudithook(audit)
 classification=json.loads((ROOT/'docs/dev/lightweight_dev004_first_v2/seed_minimization/row_classification_manifest.json').read_text());synthetic={(r['database'],r['table'],json.dumps(r['key'],sort_keys=True)) for r in classification if r['class']=='HISTORICAL_ONLY' and r['reason'].startswith('Explicit synthetic qualification input;')}
 source={k:old.ro(SRC/n) for k,n in [('core','flex_complete_database.db'),('ops','wt_ops_v2.db')]};reference={k:old.ro(REF/(k+'_v2.sqlite')) for k in ['core','ops']};targets={};inserted={};blocker=None;selected=0;encoded=0;stage='schema';current=None;peakalloc=0
 def storage():
  nonlocal peakalloc
  files=[p for p in base.iterdir() if p.is_file()];allocation=sum(p.stat().st_blocks*512 for p in files);peakalloc=max(peakalloc,allocation);assert allocation<128*1024*1024 and all(p.stat().st_size<64*1024*1024 for p in files);assert os.statvfs(base).f_bavail*os.statvfs(base).f_frsize>=2_000_000_000+64*1024*1024
 try:
  tables={}
  for kind in ['core','ops']:
   target=sqlite3.connect(base/(kind+'.sqlite'));target.row_factory=sqlite3.Row;target.execute('PRAGMA foreign_keys=ON');target.execute('PRAGMA max_page_count=8192');target.execute('PRAGMA cache_size=-2048');targets[kind]=target
   objects=reference[kind].execute("SELECT name,sql FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name LIMIT 81").fetchall();assert len(objects)<=80;tables[kind]=[r[0] for r in objects]
   for name,ddl in objects:
    if kind=='core' and name=='token_market_cap_peaks':continue
    target.execute(ddl)
   target.commit()
   if kind=='core':
    target.close();price.owner.initialize(base/'core.sqlite',disposable_root=base,allow_disposable_v2=True,price_identity_parent='token_analysis');target=sqlite3.connect(base/'core.sqlite');target.row_factory=sqlite3.Row;target.execute('PRAGMA foreign_keys=ON');targets[kind]=target
   else:typed.initialize(target,disposable_root=base,allow_disposable_v2=True);target.commit()
   target.execute('BEGIN');target.execute('PRAGMA defer_foreign_keys=ON') # FK stays ON; complete transaction must validate.
  for kind in ['core','ops']:
   ordered=tables[kind]
   if kind=='ops':ordered=[t for t in ['operators','operator_identity_events','confirmed_operation_matches'] if t in ordered]+[t for t in ordered if t not in ['operators','operator_identity_events','confirmed_operation_matches','operator_launch_membership']]+['operator_launch_membership']
   for table in ordered:
    stage='direct_import';current={'database':kind,'table':table};cols=reference[kind].execute('PRAGMA table_info("'+table+'")').fetchall();keys=[r[1] for r in sorted(cols,key=lambda r:r[5]) if r[5]];assert keys
    population=reference[kind].execute('SELECT '+','.join('"'+k+'"' for k in keys)+' FROM "'+table+'" ORDER BY '+','.join('"'+k+'"' for k in keys)+' LIMIT 30001').fetchall();assert len(population)<=30000
    inserted[kind+'.'+table]=0
    for keyrow in population:
     key=dict(keyrow);current={'database':kind,'table':table,'key':key}
     if (kind,table,json.dumps(key,sort_keys=True)) in synthetic:continue
     if table=='dev004_retained_reference_evidence':c=reference[kind]
     else:c=source[kind]
     sql='SELECT * FROM "'+table+'" WHERE '+' AND '.join('"'+k+'" IS ?' for k in keys)+' LIMIT 2';plan=[r[3] for r in c.execute('EXPLAIN QUERY PLAN '+sql,tuple(keyrow))];assert not any('SCAN ' in p for p in plan),'UNINDEXED_SOURCE'
     c.set_progress_handler(lambda:1,200000)
     try:rows=c.execute(sql,tuple(keyrow)).fetchmany(2)
     finally:c.set_progress_handler(None,0)
     assert len(rows)==1,'MISSING_SOURCE_IDENTITY';row=dict(rows[0]);size=len(json.dumps(row,default=str).encode());assert size<65536;selected+=1;encoded+=size;assert selected<=30000 and encoded<64*1024*1024
     import_row(targets[kind],table,keys,row);inserted[kind+'.'+table]+=1
     if selected%128==0:storage()
  # Include the six current Byzantine closure rows beyond the frozen population.
  entries,_,_=old.source_evidence(base)
  for e in entries:
   for t,keys,row in [('operators',['operator_id'],e['operator']),('confirmed_operation_matches',['match_id'],e['match']),('operator_launch_membership',['mint'],e['membership'])]:import_row(targets['ops'],t,keys,row)
   rows,_=old.rows(source['core'],'SELECT * FROM token_analysis WHERE mint=?',(e['key']['mint'],),2);assert len(rows)==1;import_row(targets['core'],'token_analysis',['mint'],rows[0])
  stage='full_reference_integrity'
  for kind,c in targets.items():
   violation=c.execute('SELECT * FROM pragma_foreign_key_check LIMIT 1').fetchone()
   if violation:raise RuntimeError('REFERENCE_INTEGRITY:'+kind+':'+json.dumps(list(violation)))
  stage='commit'
  for c in targets.values():c.commit()
  stage='reopen_idempotency'
  for kind,c in targets.items():c.close();c=sqlite3.connect(base/(kind+'.sqlite'));c.row_factory=sqlite3.Row;c.execute('PRAGMA foreign_keys=ON');assert not c.execute('PRAGMA foreign_key_check').fetchall();targets[kind]=c
  assert dict(targets['core'].execute('SELECT * FROM token_market_cap_peaks WHERE mint=?',(price.MINT,)).fetchone())==dict(reference['core'].execute('SELECT * FROM token_market_cap_peaks WHERE mint=?',(price.MINT,)).fetchone())
 except (Exception,) as error:
  blocker={'stage':stage,'selection':current,'error':str(error)}
  for c in targets.values():
   try:c.rollback()
   except sqlite3.Error:pass
 finally:
  for c in list(source.values())+list(reference.values())+list(targets.values()):
   try:c.close()
   except sqlite3.Error:pass
 storage();result={'status':'HOLD' if blocker else 'FULL_REFERENCE_IMPORT_PASS','blocker':blocker,'selected_rows':selected,'selected_bytes':encoded,'inserted_by_table':inserted,'peak_allocated_sampled':peakalloc,'free_before':before,'free_after':os.statvfs(base).f_bavail*os.statvfs(base).f_frsize,'foreign_keys_enabled':True,'constraints_deferred_not_disabled':True,'production_mutations':0,'provider_calls':0,'full_V1_copies':0,'paired_authority_transfer_executed':False,'activation':False,'rollback':'Disposable transactions rolled back on failure; V1 never fenced, rebound or modified.' if blocker else 'V1 remains authoritative; disposable import not activated.'}
 (base/'result.json').write_text(json.dumps(result,indent=2,sort_keys=True)+'\n');print(json.dumps(result,indent=2))
if __name__=='__main__':run(Path(sys.argv[1]))
