"""Scaled kernel pre-write limit experiment, disposable SQLite only."""
import json,os,pathlib,resource,signal,sqlite3,sys,tempfile
CAP=128*1024
if __name__=='__main__':
 root=pathlib.Path(sys.argv[1]);root.mkdir(exist_ok=False);db=root/'limited.db';c=sqlite3.connect(db);c.execute('PRAGMA journal_mode=WAL');c.execute('PRAGMA wal_autocheckpoint=0');c.execute('CREATE TABLE evidence(id INTEGER PRIMARY KEY,body BLOB)');c.execute('INSERT INTO evidence VALUES(1,?)',(b'accepted',));c.commit();c.execute('PRAGMA wal_checkpoint(TRUNCATE)');reader=sqlite3.connect(db);reader.execute('BEGIN');reader.execute('SELECT * FROM evidence').fetchall()
 signal.signal(signal.SIGXFSZ,signal.SIG_IGN);resource.setrlimit(resource.RLIMIT_FSIZE,(CAP,CAP))
 failures=[]
 try:
  c.execute('BEGIN');c.execute('INSERT INTO evidence VALUES(2,?)',(b'x'*(4*CAP),));c.commit()
 except sqlite3.Error as e:
  failures.append({'type':type(e).__name__,'code':getattr(e,'sqlite_errorcode',None),'name':getattr(e,'sqlite_errorname',None)})
  try:c.rollback()
  except sqlite3.Error:pass
 sizes={p.name:p.stat().st_size for p in root.iterdir()};assert max(sizes.values())<=CAP
 reader.close();c.close();r=sqlite3.connect(db);rows=r.execute('SELECT id,body FROM evidence').fetchall();assert rows==[(1,b'accepted')],[(x,len(y)) for x,y in rows];assert r.execute('PRAGMA quick_check').fetchone()[0]=='ok';r.close()
 result={'status':'PASS','scaled_prewrite_cap':CAP,'file_sizes':sizes,'unsafe_transaction_rejected':bool(failures),'errors':failures,'prior_committed_evidence_preserved':True,'failed_transaction_absent_after_reopen':True,'quick_check':'ok','controls':'Inherited hard RLIMIT_FSIZE with SIGXFSZ ignored; SQLite receives EFBIG/IOERR rather than allowing a file past limit. Applies per writer process, not only SQLite payloads.'};(root/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
