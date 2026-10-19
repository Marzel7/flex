"""Short transactional admission gate for bounded priority observations."""
from __future__ import annotations
import sqlite3,time

def ensure_schema(conn):
 conn.executescript('''CREATE TABLE IF NOT EXISTS bounded_observation_state(observation_id TEXT PRIMARY KEY,cap INTEGER NOT NULL,candidate_count INTEGER NOT NULL DEFAULT 0,state TEXT NOT NULL DEFAULT 'ACTIVE',stop_reason TEXT,updated_at INTEGER NOT NULL);CREATE TABLE IF NOT EXISTS bounded_observation_admissions(observation_id TEXT NOT NULL,request_id TEXT NOT NULL,mint TEXT NOT NULL,admitted_at INTEGER NOT NULL,PRIMARY KEY(observation_id,request_id),UNIQUE(observation_id,mint));''')

def admit(conn,observation_id,request_id,mint,now=None):
 """Atomically reserve a slot. Replays return existing; cap rejects before request work."""
 ensure_schema(conn);now=int(now or time.time());conn.execute('BEGIN IMMEDIATE')
 try:
  old=conn.execute('SELECT 1 FROM bounded_observation_admissions WHERE observation_id=? AND request_id=?',(observation_id,request_id)).fetchone()
  if old: conn.commit();return 'EXISTING'
  state=conn.execute('SELECT cap,candidate_count,state FROM bounded_observation_state WHERE observation_id=?',(observation_id,)).fetchone()
  if not state or state[2]!='ACTIVE' or state[1]>=state[0]:
   if state and state[1]>=state[0]:conn.execute("UPDATE bounded_observation_state SET stop_reason='CANDIDATE_CAP',updated_at=? WHERE observation_id=?",(now,observation_id))
   conn.commit();return 'REJECTED_CAP'
  conn.execute('INSERT INTO bounded_observation_admissions VALUES(?,?,?,?)',(observation_id,request_id,mint,now));conn.execute('UPDATE bounded_observation_state SET candidate_count=candidate_count+1,updated_at=? WHERE observation_id=?',(now,observation_id));conn.commit();return 'ADMITTED'
 except Exception: conn.rollback();raise
