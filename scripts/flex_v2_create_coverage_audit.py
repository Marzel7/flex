"""Read-only prospective CREATE audit; explicit matched V2 authority only."""
import argparse
import datetime
import json
import os
from pathlib import Path
import sqlite3
import sys

def main():
 p=argparse.ArgumentParser(description=__doc__)
 for arg in ('core','ops','generation','pair-manifest','authority','start-utc','end-utc'):p.add_argument('--'+arg,required=True)
 a=p.parse_args();root=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root/'runtime/creator_resolution'))
 def utc(s):
  d=datetime.datetime.fromisoformat(s.replace('Z','+00:00'))
  if d.tzinfo is None or d.utcoffset()!=datetime.timedelta(0):raise ValueError('UTC_OFFSET_REQUIRED')
  return d.timestamp()
 for s in (a.core,a.ops,a.pair_manifest,a.authority):
  if not os.path.isabs(s) or os.path.realpath(s)!=s:raise ValueError('EXPLICIT_PHYSICAL_AUTHORITY_REQUIRED')
 os.environ.update(DB_PATH=a.core,WT_OPS_DB_PATH=a.ops,FLEX_DB_PAIR_MANIFEST=a.pair_manifest,FLEX_DB_PAIR_AUTHORITY=a.authority,DEV004_PAIRED_BINDING_REQUIRED='1',FLEX_DB_PAIR_ROLE='runtime')
 from src.core.paired_binding_guard import validate_pair
 from src.ops.create_coverage import read_coverage
 if validate_pair()!=a.generation:raise ValueError('GENERATION_MISMATCH')
 with sqlite3.connect(Path(a.core).as_uri()+'?mode=ro',uri=True,timeout=2) as c,sqlite3.connect(Path(a.ops).as_uri()+'?mode=ro',uri=True,timeout=2) as o:
  c.execute('PRAGMA query_only=ON');o.execute('PRAGMA query_only=ON');result=read_coverage(c,o,utc(a.start_utc),utc(a.end_utc))
 result['window_utc']=[a.start_utc,a.end_utc];result['generation']=a.generation
 result['percent']={k:(100*v/result['total_canonical_migrations'] if result['total_canonical_migrations'] else None) for k,v in result['source'].items()};print(json.dumps(result,indent=2))
if __name__=='__main__':main()
