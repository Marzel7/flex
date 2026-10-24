"""Return to the full operational seed: bounded, indexed direct-source authority gate.
Stops at the first missing/incompatible selector. No database copies or repairs.
"""
import importlib.util,json,os,resource,sqlite3,sys,time,hashlib,socket
from pathlib import Path
ROOT=Path(__file__).resolve().parents[4];REF=Path('/private/tmp/dev004-compact-creator-qualified/classification_closure');SOURCE=Path('/Users/kevinkeaveney/Dev/claude/flex/database')
def main(base):
 assert not base.exists();base.mkdir();resource.setrlimit(resource.RLIMIT_FSIZE,(64*1024*1024,64*1024*1024));before=os.statvfs(base).f_bavail*os.statvfs(base).f_frsize;assert before>=2_000_000_000+64*1024*1024
 def deny(*a,**kw):raise RuntimeError('NETWORK_FORBIDDEN')
 socket.socket.connect=deny;socket.create_connection=deny
 classification=json.loads((ROOT/'docs/dev/lightweight_dev004_first_v2/seed_minimization/row_classification_manifest.json').read_text());synthetic={(r['database'],r['table'],json.dumps(r['key'],sort_keys=True)) for r in classification if r['class']=='SYNTHETIC_CONTROL'}
 # Inspect classification spellings rather than assuming a marker silently matched.
 synthetic_classes=sorted({r['class'] for r in classification if 'SYNTH' in r['class']})
 for r in classification:
  if r['class']=='HISTORICAL_ONLY' and r['reason'].startswith('Explicit synthetic qualification input;'):synthetic.add((r['database'],r['table'],json.dumps(r['key'],sort_keys=True)))
 examined=0;excluded=0;blocker=None;counts={};selected_bytes=0
 for kind,name in [('core','flex_complete_database.db'),('ops','wt_ops_v2.db')]:
  ref=sqlite3.connect((REF/(kind+'_v2.sqlite')).as_uri()+'?mode=ro',uri=True);ref.row_factory=sqlite3.Row
  source=sqlite3.connect((SOURCE/name).as_uri()+'?mode=ro',uri=True,timeout=.2);source.row_factory=sqlite3.Row;source.execute('PRAGMA query_only=ON');source.execute('BEGIN')
  tables=ref.execute("SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name LIMIT 81").fetchall();assert len(tables)<=80
  for (table,) in tables:
   if table=='dev004_retained_reference_evidence':continue # Accepted explicit external-reference authority, not V1 data.
   columns=ref.execute('PRAGMA table_info("'+table+'")').fetchall();keys=[r[1] for r in sorted(columns,key=lambda r:r[5]) if r[5]];assert keys
   refrows=ref.execute('SELECT '+','.join('"'+k+'"' for k in keys)+' FROM "'+table+'" ORDER BY '+','.join('"'+k+'"' for k in keys)+' LIMIT 30001').fetchall();assert len(refrows)<=30000
   counts[kind+'.'+table]=0
   if not source.execute('SELECT 1 FROM sqlite_schema WHERE type=\'table\' AND name=?',(table,)).fetchone():blocker={'kind':'MISSING_ACCEPTED_SOURCE_TABLE','database':kind,'table':table};break
   for row in refrows:
    key=dict(row)
    if (kind,table,json.dumps(key,sort_keys=True)) in synthetic:excluded+=1;continue
    sql='SELECT * FROM "'+table+'" WHERE '+' AND '.join('"'+k+'" IS ?' for k in keys)+' LIMIT 2';args=tuple(row);plan=[r[3] for r in source.execute('EXPLAIN QUERY PLAN '+sql,args)]
    if any('SCAN ' in p for p in plan):blocker={'kind':'NO_INDEXED_SOURCE_SELECTOR','database':kind,'table':table,'key':key,'plan':plan};break
    source.set_progress_handler(lambda:1,200000)
    try:values=source.execute(sql,args).fetchmany(2)
    finally:source.set_progress_handler(None,0)
    examined+=1
    if len(values)!=1:blocker={'kind':'SOURCE_ROW_NOT_AVAILABLE','database':kind,'table':table,'key':key,'source_rows':len(values),'plan':plan};break
    size=len(json.dumps(dict(values[0]),default=str).encode());assert size<65536;selected_bytes+=size;assert selected_bytes<64*1024*1024 and examined<=30000;counts[kind+'.'+table]+=1
   if blocker:break
  source.close();ref.close()
  if blocker:break
 result={'status':'HOLD' if blocker else 'SOURCE_AUTHORITY_GATE_PASS','scope':'One bounded return to full operational seed rehearsal; stops at first mandatory source authority blocker','blocker':blocker,'indexed_source_rows_examined':examined,'selected_encoded_bytes':selected_bytes,'synthetic_controls_excluded':excluded,'synthetic_class_names':synthetic_classes,'table_counts':counts,'full_migration_executed':False,'production_mutations':0,'provider_calls':0,'full_source_copies':0,'free_before':before,'free_after':os.statvfs(base).f_bavail*os.statvfs(base).f_frsize,'next_action':'Resolve only the recorded direct-source selector authority; do not substitute stale fixture data or broaden research.' if blocker else 'Execute the one complete disposable transfer using these source selections and the newly qualified price profile.'}
 (base/'result.json').write_text(json.dumps(result,indent=2,sort_keys=True)+'\n');print(json.dumps(result,indent=2))
if __name__=='__main__':main(Path(sys.argv[1]))
