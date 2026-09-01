#!/usr/bin/env python3
import argparse,json,sqlite3
from pathlib import Path
from src.ops.spam_classification import CONFIRMED_SPAM,digest,project
p=argparse.ArgumentParser();p.add_argument('--db',default='database/wt_ops_v2.db');p.add_argument('--output',default='docs/audits/spam_classification_layer.v1.json');a=p.parse_args()
c=sqlite3.connect(f'file:{a.db}?mode=ro',uri=True); rows=project(c); high={"farm_launches":c.execute('select count(*) from wt_farm_launches').fetchone()[0],"walkback_edges":c.execute('select count(*) from wt_walkback_edge_candidates').fetchone()[0],"max_edge_observed":c.execute('select max(last_observed_at) from wt_walkback_edge_candidates').fetchone()[0]}; c.close()
counts={label:sum(r['label']==label for r in rows) for label in ('SPAM_CONFIRMED','SPAM_PATTERN_A_CANDIDATE','SPAM_PATTERN_B_CANDIDATE','GENUINE_CONTROL','UNKNOWN')}
payload={"version":"v1","proof_provenance":"confirmation-known-but-proof-not-retained","confirmed_spam":sorted(CONFIRMED_SPAM),"confirmed_digest":digest(sorted(CONFIRMED_SPAM)),"source_high_water":high,"counts":counts,"classification_digest":digest(rows),"pattern_a":[r['funder'] for r in rows if r['n']>0 and r['n']==r['m'] and r['w']==r['m'] and r['s']==r['n'] and r['x']==r['n'] and r['z']==0 and r['h']==r['n']],"pattern_b":[r['funder'] for r in rows if r['m']>=4 and r['n']==r['m'] and r['w']==r['m'] and r['s']==r['n'] and r['x']==r['n'] and r['z']==0 and r['h']==r['n']-1 and r['d2']==1]}
Path(a.output).write_text(json.dumps(payload,indent=2,sort_keys=True)+'\n');print(json.dumps(payload,sort_keys=True))
