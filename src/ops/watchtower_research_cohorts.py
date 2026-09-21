"""Read-only Watchtower historical + prospective research cohort projection."""
from __future__ import annotations
import json, os, sqlite3
from pathlib import Path
from statistics import median, mean

ROOT=Path(os.environ.get('OPERATION_RESEARCH_ROOT') or Path(__file__).resolve().parents[2]).resolve()
HISTORICAL=ROOT/'docs/audits/watchtower_51_generic_price_fact_replay.v1.json'
DB=Path(os.environ.get('WT_OPS_DB_PATH') or ROOT/'database/wt_ops_v2.db').resolve()

def _stats(rows, *, prospective=False):
    assigned=len(rows); qualified=[r for r in rows if r.get('entry_mc_usd') is not None and r.get('max_proven_mc_usd') is not None]
    multiples=[float(r['entry_to_max_multiple']) for r in qualified]
    return {'assigned':assigned,'ath_qualified':f'{len(qualified)}/{assigned}','entry_qualified':sum(r.get('entry_mc_usd') is not None for r in rows),
            'finalized':sum(r.get('ath_finalization_status')=='FINALIZED' for r in rows) if prospective else len(rows),
            'median_entry_mc_usd':median([float(r['entry_mc_usd']) for r in qualified]) if qualified else None,
            'median_peak_multiple':median(multiples) if multiples else None,'mean_peak_multiple':mean(multiples) if multiples else None,
            'reached_2x':f"{sum(bool(r.get('reached_2x')) for r in qualified)}/{assigned}",
            'reached_5x':f"{sum(bool(r.get('reached_5x')) for r in qualified)}/{assigned}",
            'reached_10x':f"{sum(bool(r.get('reached_10x')) for r in qualified)}/{assigned}",
            'collapsed':f"{sum(r.get('terminal_state')=='PRICE_MONITOR_COMPLETE_COLLAPSED' for r in rows)}/{assigned}"}

def read_cohorts():
    historical=json.loads(HISTORICAL.read_text())['rows']
    prospective=[]
    with sqlite3.connect(f'file:{DB.resolve()}?mode=ro',uri=True) as conn:
        conn.row_factory=sqlite3.Row
        for row in conn.execute("SELECT * FROM operation_monitor_facts WHERE operation_id='watchtower' AND cohort_class='PROSPECTIVE_MONITOR_COHORT' ORDER BY assignment_timestamp,mint"):
            r=dict(row); prospective.append({'mint':r['mint'],'cohort':'prospective','operation_id':'watchtower','assigned_at':r['assignment_timestamp'],'entry_method':r['entry_method'],'entry_timestamp':r['entry_timestamp'],'entry_mc_usd':r['entry_mc_usd'],'entry_status':r['entry_status'],'current_final_mc_usd':r['latest_mc_usd'],'max_proven_mc_usd':r['final_proven_ath_mc'],'entry_to_max_multiple':r['final_ath_multiple'],'final_ath_evidence':r['final_ath_evidence'],'final_ath_resolution':r['final_ath_resolution'],'ath_bucket_start':r['final_ath_bucket_start'],'ath_bucket_end':r['final_ath_bucket_end'],'reached_2x':bool(r['reached_2x']),'reached_5x':bool(r['reached_5x']),'reached_10x':bool(r['reached_10x']),'drawdown_percent':r['drawdown_percent'],'terminal_state':r['monitor_state'],'terminal_timestamp':r['monitor_completed_at'],'ath_finalization_status':'FINALIZED' if r['final_proven_ath_mc'] is not None else 'PENDING','price_evidence_status':r['evidence_status'],'price_fact_contract_version':'WATCHTOWER_PRICE_FACT_CONTRACT_V1','provider_call_count':r['provider_call_count'],'provenance_digest':r['ath_finalization_provenance_digest'] or r['provenance_digest']})
    historical_normalized=[{'entry_mc_usd':r['entry_mc_usd'],'max_proven_mc_usd':r['max_proven_mc_usd'],'entry_to_max_multiple':r['entry_to_max_multiple'],'reached_2x':r['reached_2x'],'reached_5x':r['reached_5x'],'reached_10x':r['reached_10x'],'terminal_state':None} for r in historical]
    return {'historical_rows':historical,'prospective_rows':prospective,'all_rows':historical+prospective,
            'statistics':{'historical51':_stats(historical_normalized),'prospective':_stats(prospective,prospective=True),'all_watchtower':_stats(historical_normalized+prospective)}}
