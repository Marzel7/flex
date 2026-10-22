"""Offline contract qualification. No application imports or persistent DB writes.
Production sampling is opt-in; all CREATE/INSERT statements target :memory: only.
"""
import argparse, datetime as dt, json, pathlib, sqlite3, time
ROOT = pathlib.Path(__file__).resolve().parent
META = json.loads((ROOT / 'evidence.json').read_text())

def sample():
    c = sqlite3.connect(pathlib.Path(META['path']).as_uri()+'?mode=ro', uri=True, timeout=.1)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA query_only=ON'); c.execute('PRAGMA cache_size=-1024')
    rows=[]; plans=[]
    fields='id,timestamp,recorded_at,section,provider,method,status_code,latency_ms,credits,cache_action,credits_saved,optimization_layer,source_file,mode,retries'
    for days in (0,7,30,90):
        sql=f'SELECT {fields} FROM rpc_metrics WHERE timestamp<=? ORDER BY timestamp DESC LIMIT 32'
        args=(time.time()-days*86400,)
        plans.append(c.execute('EXPLAIN QUERY PLAN '+sql,args).fetchone()['detail'])
        start=time.monotonic(); steps=[0]
        def abort():
            steps[0]+=1000
            return steps[0]>30000 or time.monotonic()-start>.075
        c.set_progress_handler(abort,1000)
        try: rows.extend(dict(r) for r in c.execute(sql,args))
        finally: c.set_progress_handler(None,0)
    c.close()
    return rows,plans

def fixture():
    # Values reused from retained test_rpc_metrics_uk_midnight_reset.py:
    # before/after UK midnight: 10/20 credits; 3 paid calls and zero-credit skip.
    midnight=dt.datetime(2026,10,22,tzinfo=dt.timezone(dt.timedelta(hours=1))).timestamp()
    rows=[]
    for i,(offset,credits,action,saved) in enumerate([(-3600,10,'none',0),(3600,20,'none',0),(1,10,'none',0),(2,10,'none',0),(3,10,'none',0),(4,0,'skip',10)]):
        rows.append(dict(id=i+1,timestamp=midnight+offset,recorded_at=dt.datetime.fromtimestamp(midnight+offset,dt.timezone.utc).strftime('%Y-%m-%d %H:%M:%S'),section='test',provider='helius_rpc',method='getTransaction',status_code=200,latency_ms=10.,credits=credits,cache_action=action,credits_saved=saved,optimization_layer='none',source_file='test.py',mode='realtime',retries=0))
    # Explicit synthetic edge cases, never represented as retained history.
    for i in range(18):
        row=rows[i%6].copy(); row.update(id=7+i,section=['test','other'][i%2],provider=['helius_rpc','other'][i%2],method=['getTransaction','getBalance'][i%2],status_code=[200,429,500,None][i%4],latency_ms=[None,0.,10.,80.][i%4],credits=[None,0,3,10][i%4],cache_action=['none','skip','refresh','full_scan'][i%4],credits_saved=[None,0,3,20][i%4]); rows.append(row)
    return rows

def qualify(rows):
    c=sqlite3.connect(':memory:'); c.row_factory=sqlite3.Row
    c.execute(next(x['sql'] for x in META['schema'] if x['name']=='rpc_metrics'))
    for row in rows:
        names=','.join(row); c.execute(f"INSERT INTO rpc_metrics ({names}) VALUES ({','.join('?' for _ in row)})",tuple(row.values()))
    for view in META['schema']:
        if view['type']=='view': c.execute(view['sql'])
    # This is a bounded mathematical representation, not proposed runtime DDL.
    c.execute('''CREATE TABLE compact AS SELECT DATE(recorded_at) day,section,provider,method,source_file,cache_action,optimization_layer,status_code,
    COUNT(*) n,SUM(COALESCE(credits,0)) spent,SUM(COALESCE(credits_saved,0)) saved,
    SUM(credits_saved) saved_nullable,SUM(credits) spent_nullable,
    MIN(COALESCE(credits_saved,0)) saved_min,MAX(COALESCE(credits_saved,0)) saved_max,
    MIN(recorded_at) first_recorded,MAX(recorded_at) last_recorded,
    SUM(latency_ms) latency_sum,COUNT(latency_ms) latency_n,
    SUM(CASE WHEN credits>0 THEN 1 ELSE 0 END) paid_n,
    SUM(CASE WHEN status_code>=400 THEN 1 ELSE 0 END) errors,
    SUM(CASE WHEN status_code=429 THEN 1 ELSE 0 END) limited
    FROM rpc_metrics WHERE recorded_at IS NOT NULL
    GROUP BY DATE(recorded_at),section,provider,method,source_file,cache_action,optimization_layer,status_code''')
    replacements={
        'DATE(MIN(recorded_at))':'DATE(MIN(first_recorded))',
        'DATE(MAX(recorded_at))':'DATE(MAX(last_recorded))',
        'MIN(DATE(recorded_at))':'MIN(day)', 'MAX(DATE(recorded_at))':'MAX(day)',
        'COUNT(DISTINCT DATE(recorded_at))':'COUNT(DISTINCT day)',
        'SUM(COALESCE(credits_saved, 0))':'SUM(saved)',
        'SUM(COALESCE(credits, 0))':'SUM(spent)',
        'AVG(COALESCE(credits_saved, 0))':'(1.0*SUM(saved)/SUM(n))',
        'MIN(COALESCE(credits_saved, 0))':'MIN(saved_min)',
        'MAX(COALESCE(credits_saved, 0))':'MAX(saved_max)',
        'COUNT(*)':'SUM(n)',
        'SUM(CASE WHEN cache_action IN (\'skip\', \'refresh\') THEN 1 ELSE 0 END)':"SUM(CASE WHEN cache_action IN ('skip', 'refresh') THEN n ELSE 0 END)",
        'DATE(recorded_at)':'day',
        'FROM rpc_metrics':'FROM compact',
        'recorded_at IS NOT NULL':'1=1',
    }
    for action in ['skip','refresh','full_scan','none']:
        replacements[f"SUM(CASE WHEN cache_action = '{action}' THEN 1 ELSE 0 END)"]=f"SUM(CASE WHEN cache_action = '{action}' THEN n ELSE 0 END)"
        replacements[f"SUM(CASE WHEN cache_action = '{action}' THEN credits_saved ELSE 0 END)"]=f"SUM(CASE WHEN cache_action = '{action}' THEN saved_nullable ELSE 0 END)"
    passed=[]
    names=['v_rpc_daily_savings','v_rpc_savings_by_section','v_rpc_cache_performance','v_rpc_cumulative_savings','v_rpc_efficiency_score','v_rpc_efficiency_all_time','v_rpc_efficiency_by_section','v_rpc_efficiency_health','v_rpc_efficiency_trend_7day','v_rpc_rolling_7day_average']
    def values(sql):
        return sorted([tuple(r) for r in c.execute(sql)],key=repr)
    for name in names:
        original=next(x['sql'] for x in META['schema'] if x['name']==name).split(' AS\n',1)[1]
        proposed=original
        for old,new in replacements.items(): proposed=proposed.replace(old,new)
        assert values(original)==values(proposed),name
        passed.append(name)
    # Independent additive API inputs; preserve unweighted section-average behavior.
    raw='SELECT source_file,section,COUNT(*),SUM(credits),SUM(status_code>=400),SUM(status_code=429),SUM(latency_ms),COUNT(latency_ms) FROM rpc_metrics WHERE recorded_at IS NOT NULL GROUP BY source_file,section'
    agg='SELECT source_file,section,SUM(n),SUM(spent_nullable),SUM(errors),SUM(limited),SUM(latency_sum),SUM(latency_n) FROM compact GROUP BY source_file,section'
    # SQL SUM(boolean) is nullable for all-null status; production COUNT(CASE) yields zero.
    raw=raw.replace('SUM(status_code>=400)','COUNT(CASE WHEN status_code>=400 THEN 1 END)').replace('SUM(status_code=429)','COUNT(CASE WHEN status_code=429 THEN 1 END)')
    assert values(raw)==values(agg),'database API group sufficient statistics'
    passed.append('database_api_group_inputs')
    n=c.execute('SELECT COUNT(*) FROM compact').fetchone()[0]
    result={'rows':len(rows),'compact_cells':n,'exact_checks':passed,'scope':'whole observed days/all-time only; no arbitrary sub-day claim'}
    c.close(); return result

def counterexamples():
    # Equal whole-day count/sum, different answers at an intra-day boundary.
    a=[(1,10),(3,20)]; b=[(2,10),(4,20)]
    assert (len(a),sum(v for _,v in a))==(len(b),sum(v for _,v in b))
    assert sum(v for t,v in a if t>3)!=sum(v for t,v in b if t>3)
    # Count/sum/min/max do not determine an exact p95 distribution.
    a=[0]+[1]*98+[100]; b=[0]*98+[98,100]
    assert (len(a),sum(a),min(a),max(a))==(len(b),sum(b),min(b),max(b))
    assert sorted(a)[95]!=sorted(b)[95]
    # Backdated event is recent on the independent ingestion clock.
    event_time, recorded_time, cutoff=1,10,5
    assert (event_time>cutoff)!=(recorded_time>cutoff)
    return ['arbitrary sub-day boundary requires within-bucket ordering', 'p95 not reconstructible from count/sum/min/max', 'timestamp and recorded_at cannot be substituted']

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--sample-production',action='store_true');args=p.parse_args()
    out={'fixture':qualify(fixture()),'counterexamples':counterexamples(),'production_writes':0,'persistent_fixture_bytes':0}
    if args.sample_production:
        rows,plans=sample();out['bounded_sample']=qualify(rows);out['selection_plans']=plans
    print(json.dumps(out,indent=2))
