"""Conservative no-refund provider dispatch ordinals for a bounded observation."""
import sqlite3,time
def claim(c,obs,mint,limit=40):
 c.execute('CREATE TABLE IF NOT EXISTS observation_provider_dispatches(observation_id TEXT, ordinal INTEGER, mint TEXT, claimed_at INTEGER, PRIMARY KEY(observation_id,ordinal))');c.execute('BEGIN IMMEDIATE')
 try:
  n=c.execute('SELECT COUNT(*) FROM observation_provider_dispatches WHERE observation_id=?',(obs,)).fetchone()[0]
  if n>=limit:c.commit();return None
  c.execute('INSERT INTO observation_provider_dispatches VALUES(?,?,?,?)',(obs,n+1,mint,int(time.time())));c.commit();return n+1
 except Exception:c.rollback();raise
