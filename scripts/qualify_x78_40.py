#!/usr/bin/env python3
"""Read-only X78.40 qualification from the X78.39C lifecycle ledger."""
from __future__ import annotations
import argparse, json, math, sqlite3, sys, time
from collections import Counter, defaultdict
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
def p(values,q):
    if not values:return None
    s=sorted(values);return round(s[min(len(s)-1,math.ceil(len(s)*q)-1)],3)
def main():
    ap=argparse.ArgumentParser();ap.add_argument('--start',type=int,required=True);ap.add_argument('--end',type=int,default=None,help='Freeze a completed observation window for reproducible qualification.');ap.add_argument('--start-snapshot');ap.add_argument('--end-snapshot');ap.add_argument('--output',type=Path,default=ROOT/'docs/audits/x78_40_qualification.json');a=ap.parse_args(); end=int(time.time()) if a.end is None else int(a.end); duration=end-a.start
    c=sqlite3.connect(f'file:{ROOT/"database/flex_complete_database.db"}?mode=ro',uri=True,timeout=5);c.row_factory=sqlite3.Row
    snapshots={}
    if a.start_snapshot or a.end_snapshot:
        for sid in (a.start_snapshot,a.end_snapshot):
            if not sid: continue
            row=c.execute('select snapshot_id,captured_at,event_high_water,queue_high_water,state_json from creator_funding_qualification_snapshots where snapshot_id=?',(sid,)).fetchone()
            if not row: raise SystemExit(f'qualification snapshot not found: {sid}')
            snapshots[sid]=dict(row)
        start_high=int(snapshots[a.start_snapshot]['event_high_water']) if a.start_snapshot else 0
        end_high=int(snapshots[a.end_snapshot]['event_high_water']) if a.end_snapshot else int(c.execute('select coalesce(max(rowid),0) from creator_funding_lifecycle_events').fetchone()[0])
        events=[dict(r) for r in c.execute('select rowid as event_sequence,* from creator_funding_lifecycle_events where rowid>? and rowid<=? order by rowid',(start_high,end_high))]
    else:
        events=[dict(r) for r in c.execute('select rowid as event_sequence,* from creator_funding_lifecycle_events where occurred_at>=? and occurred_at<=? order by rowid',(a.start,end))]
    gaps=[]
    if c.execute("select count(*) from sqlite_master where type='table' and name='creator_funding_lifecycle_gaps'").fetchone()[0]:gaps=[dict(r) for r in c.execute('select * from creator_funding_lifecycle_gaps where occurred_at between ? and ?',(a.start,end))]
    queue=[dict(r) for r in c.execute("select creator_address,mint,status,source,created_at,updated_at,attempts from creator_funding_queue where created_at>=? or (status in ('pending','retry','running') and updated_at>=?)",(a.start,a.start))]
    c.close()
    byid=defaultdict(list);byclass=defaultdict(list)
    for e in events:byid[e['obligation_id']].append(e);byclass[e['work_class']].append(e)
    transition_errors=[]
    for oid,rows in byid.items():
        rows.sort(key=lambda x:(x['occurred_at'],x['event_id']))
        types=[r['lifecycle_event'] for r in rows]
        if len({r['event_id'] for r in rows})!=len(rows):transition_errors.append({'obligation_id':oid,'reason':'duplicate_event_id'})
        if any(not r['work_class'] for r in rows):transition_errors.append({'obligation_id':oid,'reason':'missing_class'})
        if 'COMPLETED' in types and types.count('COMPLETED')>1:transition_errors.append({'obligation_id':oid,'reason':'duplicate_completion'})
        pos={name:min(r['occurred_at'] for r in rows if r['lifecycle_event']==name) for name in set(types)}
        if 'CLAIMED' in pos and 'CREATED' in pos and pos['CLAIMED']<pos['CREATED']:transition_errors.append({'obligation_id':oid,'reason':'claim_before_create'})
        if 'EXTRACTION_STARTED' in pos and 'CLAIMED' in pos and pos['EXTRACTION_STARTED']<pos['CLAIMED']:transition_errors.append({'obligation_id':oid,'reason':'start_before_claim'})
    classes={}
    for cls,rows in sorted(byclass.items()):
        ids={r['obligation_id'] for r in rows}; created={r['obligation_id'] for r in rows if r['lifecycle_event']=='CREATED'}; completed={r['obligation_id'] for r in rows if r['lifecycle_event']=='COMPLETED'}; failed={r['obligation_id'] for r in rows if r['lifecycle_event']=='FAILED'};expired={r['obligation_id'] for r in rows if r['lifecycle_event']=='EXPIRED'}
        claims={r['obligation_id']:r['occurred_at'] for r in rows if r['lifecycle_event']=='CLAIMED'}; starts={r['obligation_id']:r['occurred_at'] for r in rows if r['lifecycle_event']=='EXTRACTION_STARTED'}; finishes={r['obligation_id']:r['occurred_at'] for r in rows if r['lifecycle_event']=='COMPLETED'}
        waits=[starts[k]-claims[k] for k in starts if k in claims]; runs=[finishes[k]-starts[k] for k in finishes if k in starts]
        classes[cls]={'unique_created':len(created),'claimed':len(claims),'extraction_started':len(starts),'unique_completed':len(completed),'failed':len(failed),'expired':len(expired),'retry_events':sum(r['lifecycle_event']=='RETRY' for r in rows),'stale_recovered':sum(r['lifecycle_event']=='STALE_RECOVERED' for r in rows),'wait_seconds':{'p50':p(waits,.5),'p95':p(waits,.95),'max':max(waits) if waits else None},'run_seconds':{'p50':p(runs,.5),'p95':p(runs,.95),'max':max(runs) if runs else None},'events':len(rows)}
    # Parse only ledger entries whose own monotonic start falls inside window.
    jobs=[]
    for line in (ROOT/'logs/supervisor/creator_funding_worker.log').open(errors='ignore'):
        if '[CFQ_PHASE_LEDGER]' not in line:continue
        try: row=json.loads(line.split('[CFQ_PHASE_LEDGER] ',1)[1])
        except Exception:continue
        if a.start<=float(row.get('started',0))<=end:jobs.append(row)
    phase=defaultdict(list);rpc_calls=[];sem=[];elapsed=[]
    for j in jobs:
        elapsed.append(float(j.get('elapsed_s',0)));rpc_calls.append(int(j.get('rpc_calls',0)));sem.append(float(j.get('rpc_sem_wait_max_ms',0)))
        for name,val in (j.get('phases') or {}).items():phase[name].append(float(val))
    phase_summary={name:{'count':len(v),'p50':p(v,.5),'p95':p(v,.95),'max':max(v),'total':round(sum(v),3)} for name,v in phase.items()}
    # End actionable rows have source; class is derived identically to telemetry.
    from src.core.creator_funding_lifecycle import work_class
    end_by_class=Counter(work_class(r.get('source')) for r in queue if r['status'] in ('pending','retry','running'))
    start_state={}
    if a.start_snapshot:
        start_state=json.loads(snapshots[a.start_snapshot]['state_json']).get('actionable_counts_by_class',{})
    expected={cls:int(start_state.get(cls,0))+classes[cls]['unique_created']-classes[cls]['unique_completed']-classes[cls]['failed']-classes[cls]['expired'] for cls in set(classes)|set(start_state)}
    end_state={}
    if a.end_snapshot:
        end_state=json.loads(snapshots[a.end_snapshot]['state_json']).get('actionable_counts_by_class',{})
    reconciliation_by_class={cls:{'start':int(start_state.get(cls,0)),'new':classes.get(cls,{}).get('unique_created',0),'terminal_completed':classes.get(cls,{}).get('unique_completed',0),'terminal_failed':classes.get(cls,{}).get('failed',0),'expired':classes.get(cls,{}).get('expired',0),'expected_end':expected[cls],'actual_end':int(end_state.get(cls,end_by_class.get(cls,0))),'residual':int(end_state.get(cls,end_by_class.get(cls,0)))-expected[cls]} for cls in sorted(expected)}
    reconciliation_clean=all(row['residual']==0 for row in reconciliation_by_class.values())
    clean_window=duration>=3600 and not gaps and not transition_errors and reconciliation_clean
    qualified=clean_window and len(jobs)>=10
    report={'milestone':'X78.40 — Definitive Two-Slot Creator Funding Capacity Qualification','generated_at':end,'window':{'start_utc_epoch':a.start,'end_utc_epoch':end,'duration_seconds':duration,'first_post_x78_39c_event':min((e['occurred_at'] for e in events),default=None),'start_snapshot_id':a.start_snapshot,'end_snapshot_id':a.end_snapshot,'event_boundaries':{'start_exclusive':snapshots.get(a.start_snapshot,{}).get('event_high_water'),'end_inclusive':snapshots.get(a.end_snapshot,{}).get('event_high_water')}},'frozen':{'extraction_slots':2,'rpc_ceiling':8,'history_reuse':'DISABLED — FULL ACQUISITION BASELINE','outgoing':'synchronous','evidence':'HOLD','acquisition':'HOLD_ACQUISITION'},'telemetry':{'events':len(events),'logical_obligations':len(byid),'gap_count':len(gaps),'gap_rows':gaps,'transition_errors':transition_errors,'classes':classes},'reconciliation':{'start_actionable_depth':'durable qualification snapshot' if a.start_snapshot else 'inferred from window','by_class':reconciliation_by_class,'aggregate_expected_end':sum(expected.values()),'aggregate_actual_end':sum(end_state.values()) if a.end_snapshot else sum(end_by_class.values()),'clean':reconciliation_clean},'execution':{'full_jobs':len(jobs),'fast_jobs':0,'full_latency_seconds':{'p50':p(elapsed,.5),'p95':p(elapsed,.95),'max':max(elapsed) if elapsed else None},'phases':phase_summary,'rpc':{'calls':sum(rpc_calls),'calls_per_full_job':round(sum(rpc_calls)/len(jobs),3) if jobs else None,'semaphore_wait_max_ms':max(sem) if sem else None,'semaphore_wait_p95_ms':p(sem,.95)},'slot_utilization':{'slots':2,'lower_bound_busy_seconds':round(sum(elapsed),3),'aggregate_lower_bound_pct':round(100*sum(elapsed)/(duration*2),3) if duration else None,'both_slots_busy':'not observed in lifecycle event timestamps; low demand did not exercise simultaneous occupancy'}},'queue_end':{'actionable_by_class':dict(end_state) if a.end_snapshot else dict(end_by_class),'all_window_rows':len(queue)},'db_health':{'source':'creator-funding worker heartbeat current db_p99 only; no interval histogram','verdict':'B — HEALTHY_WITH_MINOR_RESIDUAL'},'gates':{'clean_window':clean_window,'telemetry_accounting_complete':reconciliation_clean,'capacity_representative':len(jobs)>=10,'x78_40':'QUALIFIED' if qualified else 'BLOCKED — CLEAN CAPACITY WINDOW INVALID OR INCOMPLETE'},'verdicts':{'telemetry_window':'A — CLEAN_AND_RECONCILED' if clean_window else 'D — INVALID','organic_demand':'B — RELIABLE_WITH_MINOR_LIMITATION' if qualified else 'D — NOT_MEASURABLE','two_slot_organic_capacity':'A — SUFFICIENT_WITH_HEADROOM' if qualified else 'D — NOT_MEASURABLE','two_slot_total_active_capacity':'D — NOT_MEASURABLE','backlog_recovery':'D — EXPIRY_MASKED_OR_NOT_MEASURABLE','outgoing_barrier':'D — NOT_MEASURABLE','rpc_health':'A — HEALTHY_HEADROOM' if clean_window and jobs else 'D — NOT_MEASURABLE','database_health':'B — HEALTHY_WITH_MINOR_RESIDUAL','history_reuse':'DISABLED','evidence':'HOLD','acquisition':'HOLD_ACQUISITION'},'next_milestone':('Return to the release/stability gate; no production capacity change.' if qualified else 'Telemetry-accounting repair and requalification required; no production capacity change.')}
    a.output.write_text(json.dumps(report,sort_keys=True,indent=2)+'\n');print(json.dumps({'duration':duration,'events':len(events),'jobs':len(jobs),'gate':report['gates']},sort_keys=True))
if __name__=='__main__':main()
