"""Read-only persisted evidence adapter for bounded generic Living candidates."""
from __future__ import annotations
import os, sqlite3

SOURCE_TABLES=('p3r_v2_candidate_membership','wt_walkback_queue','wt_walkback_edge_candidates','wt_walkback_atomic_flows')

def _ro(path): return sqlite3.connect('file:'+os.path.abspath(path)+'?mode=ro',uri=True)
def _generation_values(value): return [int(x) for x in value.split(':')]

def read_generic_living_source_context(spec, db_path, event_context=None):
    """Config-driven, bounded reader.  It issues SELECT statements only."""
    cfg=spec['persisted_source']; c=_ro(db_path)
    try:
        mints=[r[0] for r in c.execute('SELECT mint FROM p3r_v2_candidate_membership WHERE run_id=? AND candidate_id=? ORDER BY mint',(cfg['run_id'],spec['source_candidate_id']))]
        if not mints: raise ValueError('configured candidate has no persisted membership')
        marks={k:c.execute('SELECT COALESCE(MAX(rowid),0) FROM '+table).fetchone()[0] for k,table in cfg['highwater_tables'].items()}
        q=','.join('?'*len(mints)); rows={}
        for key,table in cfg['evidence_tables'].items():
            rows[key]=[dict(zip(('rowid','mint','funder_wallet'),r)) for r in c.execute('SELECT rowid,mint,'+cfg['funder_column'].get(key,'NULL')+' FROM '+table+' WHERE mint IN ('+q+')',mints)]
        prior=(event_context or {}).get('current_generation')
        advanced=bool(prior and any(a>b for a,b in zip(marks.values(),_generation_values(prior))))
        newer=any(any(r['rowid']>old for r in rows[key]) for key,old in zip(rows,_generation_values(prior))) if prior else False
        event_mint=(event_context or {}).get('mint'); relevant=newer and (event_mint in set(mints) if event_mint else True)
        return {'members':mints,'funders':sorted({r['funder_wallet'] for r in rows['queue'] if r['funder_wallet']}),'highwaters':marks,'source_rows':{k:len(v) for k,v in rows.items()},'global_advanced':advanced,'relevant_new_evidence':relevant,'association_ids':[]}
    finally: c.close()
