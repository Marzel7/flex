"""Deterministic, small admission budget separate from raw storage capacity."""
import sqlite3,time
from pathlib import Path
class CaptureSession:
 def __init__(self,path,session_id,limit=20):self.path,self.session_id,self.limit=path,session_id,int(limit);Path(path).parent.mkdir(parents=True,exist_ok=True);self._schema()
 def _c(self):c=sqlite3.connect(self.path);c.row_factory=sqlite3.Row;return c
 def _schema(self):
  with self._c() as c:
   c.execute('CREATE TABLE IF NOT EXISTS pumpswap_capture_sessions(id TEXT PRIMARY KEY,limit_n INTEGER NOT NULL,admitted INTEGER NOT NULL DEFAULT 0,rejected INTEGER NOT NULL DEFAULT 0,state TEXT NOT NULL,created_at INTEGER NOT NULL)')
   c.execute('CREATE TABLE IF NOT EXISTS pumpswap_capture_admissions(session_id TEXT NOT NULL,signature TEXT NOT NULL,PRIMARY KEY(session_id,signature))')
   c.execute('INSERT OR IGNORE INTO pumpswap_capture_sessions VALUES (?,?,0,0,"ADMITTING",?)',(self.session_id,self.limit,int(time.time())))
 def admit(self,signature):
  with self._c() as c:
   row=c.execute('SELECT admitted,limit_n,state FROM pumpswap_capture_sessions WHERE id=?',(self.session_id,)).fetchone()
   if c.execute('SELECT 1 FROM pumpswap_capture_admissions WHERE session_id=? AND signature=?',(self.session_id,signature)).fetchone():return 'DUPLICATE'
   if row['state']!='ADMITTING' or row['admitted']>=row['limit_n']:
    c.execute('UPDATE pumpswap_capture_sessions SET rejected=rejected+1,state="ADMISSION_CLOSED" WHERE id=?',(self.session_id,));return 'CLOSED'
   c.execute('INSERT INTO pumpswap_capture_admissions VALUES (?,?)',(self.session_id,signature));c.execute('UPDATE pumpswap_capture_sessions SET admitted=admitted+1 WHERE id=?',(self.session_id,));return 'ADMITTED'
 def release(self,signature):
  with self._c() as c:
   if c.execute('DELETE FROM pumpswap_capture_admissions WHERE session_id=? AND signature=?',(self.session_id,signature)).rowcount:c.execute('UPDATE pumpswap_capture_sessions SET admitted=admitted-1 WHERE id=?',(self.session_id,))
 def state(self):
  with self._c() as c:return dict(c.execute('SELECT * FROM pumpswap_capture_sessions WHERE id=?',(self.session_id,)).fetchone())
