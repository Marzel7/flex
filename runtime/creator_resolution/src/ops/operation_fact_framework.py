"""Generic, compact, idempotent operation-fact persistence.

Adapters build facts outside transactions; this module only persists compact
already-normalized facts in a short SQLite transaction.
"""
from __future__ import annotations
import hashlib, json, sqlite3, time
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

class EvidenceCompleteness(StrEnum): COMPLETE='COMPLETE'; PARTIAL='PARTIAL'; INSUFFICIENT='INSUFFICIENT'; NOT_APPLICABLE='NOT_APPLICABLE'
@dataclass(frozen=True)
class OperationContract: operation_id:str; version:str; detector_version:str|None=None
@dataclass(frozen=True)
class OperationFact:
 operation_id:str; mint:str; fact_type:str; fact_version:str; contract_version:str; payload:dict; provenance:dict; completeness:EvidenceCompleteness; effective_at:int|None=None; boundary_identity:str|None=None
 def fact_id(self)->str:return hashlib.sha256(json.dumps((self.operation_id,self.mint,self.fact_type,self.fact_version,self.boundary_identity,self.contract_version),separators=(',',':')).encode()).hexdigest()
class OperationAdapter(Protocol):
 def build_facts(self,evidence:dict,contract:OperationContract)->list[OperationFact]: ...
def ensure(conn):
 conn.execute('CREATE TABLE IF NOT EXISTS operation_compact_facts (fact_id TEXT PRIMARY KEY,operation_id TEXT,mint TEXT,fact_type TEXT,fact_version TEXT,contract_version TEXT,payload TEXT,provenance TEXT,completeness TEXT,effective_at INTEGER,created_at INTEGER)')
def write_facts(conn, facts:list[OperationFact])->int:
 """No acquisition, decoding, detector replay, or scan belongs in this function."""
 ensure(conn); added=0
 with conn:
  for f in facts:
   fid=f.fact_id(); row=conn.execute('SELECT payload,provenance FROM operation_compact_facts WHERE fact_id=?',(fid,)).fetchone()
   if row:
    if row[0]!=json.dumps(f.payload,sort_keys=True) or row[1]!=json.dumps(f.provenance,sort_keys=True): raise ValueError('OPERATION_FACT_CONFLICT_FAIL_CLOSED')
    continue
   conn.execute('INSERT INTO operation_compact_facts VALUES(?,?,?,?,?,?,?,?,?,?,?)',(fid,f.operation_id,f.mint,f.fact_type,f.fact_version,f.contract_version,json.dumps(f.payload,sort_keys=True),json.dumps(f.provenance,sort_keys=True),f.completeness.value,f.effective_at,int(time.time()))); added+=1
 return added
