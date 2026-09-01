#!/usr/bin/env python3
"""Read-only local delta monitor; no provider polling or suppression."""
import argparse,json,sqlite3
from pathlib import Path
from src.ops.spam_classification import project

p=argparse.ArgumentParser();p.add_argument('--db',default='database/wt_ops_v2.db');p.add_argument('--baseline',default='docs/audits/spam_classification_layer.v2.json');a=p.parse_args()
base=json.loads(Path(a.baseline).read_text()); known=set(base['pattern_a'])|set(base['pattern_b'])|set(base['confirmed_spam'])
c=sqlite3.connect(f'file:{a.db}?mode=ro',uri=True); rows=project(c); current={r['funder']:r for r in rows if r['label'] in {'SPAM_PATTERN_A_CANDIDATE','SPAM_PATTERN_B_CANDIDATE','SPAM_CONFIRMED'}}
new=sorted(f for f in current if f not in known)
print(json.dumps({'baseline':a.baseline,'baseline_high_water':base['source_high_water'],'current_edge_high_water':c.execute('select max(last_observed_at) from wt_walkback_edge_candidates').fetchone()[0],'new_organic_candidates':[{'funder':f,'classification':current[f]['label']} for f in new],'provider_calls':0,'suppression_occurred':False},sort_keys=True))
