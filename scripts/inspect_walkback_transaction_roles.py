#!/usr/bin/env python3
"""Read-only post-run verifier for prospective retained transaction roles."""
import json,sqlite3
from pathlib import Path
p=Path(__file__).resolve().parents[1]/'database/wt_ops_v2.db'
c=sqlite3.connect(f'file:{p}?mode=ro',uri=True); c.row_factory=sqlite3.Row
n=c.execute('select count(*) from wt_walkback_transaction_roles').fetchone()[0]
r=c.execute('select * from wt_walkback_transaction_roles order by last_observed_at desc limit 1').fetchone()
out={'role_rows':n,'newest':dict(r) if r else None}
if r: out['strict_fields_complete']=all(r[k] is not None for k in ('mint','signature','transfer_source','transfer_destination','transfer_lamports','fee_payer')) and bool(json.loads(r['signers_json'])) and r['route_semantics']=='DIRECT'
print(json.dumps(out,indent=2,sort_keys=True))
