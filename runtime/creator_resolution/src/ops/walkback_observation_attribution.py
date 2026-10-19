"""Durable, prospective observation linkage for an existing Walkback job."""
from __future__ import annotations
import sqlite3,time
def ensure_schema(c): c.execute('CREATE TABLE IF NOT EXISTS walkback_observation_attribution(mint TEXT PRIMARY KEY,observation_id TEXT NOT NULL,admission_id TEXT NOT NULL,priority_request_id TEXT NOT NULL,effective_at INTEGER NOT NULL,state TEXT NOT NULL DEFAULT "ACTIVE")')
def bind(c,mint,observation_id,admission_id,request_id):
 ensure_schema(c);c.execute('INSERT OR IGNORE INTO walkback_observation_attribution VALUES(?,?,?,?,?,"ACTIVE")',(mint,observation_id,admission_id,request_id,int(time.time())));c.commit()
def context(c,mint):
 ensure_schema(c);r=c.execute('SELECT observation_id,admission_id,priority_request_id FROM walkback_observation_attribution WHERE mint=? AND state="ACTIVE"',(mint,)).fetchone()
 return {"observation_id":r[0],"admission_id":r[1],"priority_request_id":r[2]} if r else None
