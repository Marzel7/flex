"""Unified presentation contract; no acquisition or database mutation."""
import ast
import subprocess
import time
from pathlib import Path

from src.ops.operator_routes import _authoritative_live, _recent_operation_tokens

ROOT = Path(__file__).resolve().parents[1]

def test_live_eligibility_bytecode_structure_unchanged():
    before = subprocess.check_output(['git','show','8c27f1fb:src/ops/operator_routes.py'],cwd=ROOT,text=True)
    after = (ROOT/'src/ops/operator_routes.py').read_text()
    def symbol(text):
        return ast.dump(next(n for n in ast.parse(text).body if isinstance(n,ast.FunctionDef) and n.name=='_authoritative_live'))
    assert symbol(before)==symbol(after)

def test_latest20_identity_order_and_dedup_unchanged():
    rows=[dict(mint=str(i),operation_id=['watchtower','byzantine','nexus'][i%3],assignment_timestamp=i//2) for i in range(40)]
    rows += [dict(rows[-1])]
    projected=_recent_operation_tokens(rows)
    assert len(projected)==20
    assert len({r['mint'] for r in projected})==20
    assert [(r['assignment_timestamp'],r['mint']) for r in projected]==sorted({(r['assignment_timestamp'],r['mint']) for r in rows},reverse=True)[:20]

def test_price_scope_missing_and_live_evidence():
    now=int(time.time())
    retained=dict(mint='retained',operation_id='byzantine',assignment_timestamp=1,entry_timestamp=1,
                  entry_mc_usd=100,entry_status='QUALIFIED',monitor_state='MONITORING_ACTIVE',latest_mc_usd=120,
                  byzantine_actual_entry_enrichment=True,ath_mc_usd=200,max_multiple=2,time_to_ath_seconds=120)
    history=_recent_operation_tokens([retained],now=now)[0]
    assert history['current_mc_usd'] is None
    assert history['peak_mc_usd']==200 and history['max_multiple']==2
    assert history['peak_scope']=='2H PEAK'
    assert history['freshness_seconds'] is None
    live=dict(retained,mint='live',entry_timestamp=now-60,next_observation_at=now+60)
    assert _authoritative_live(live,now=now)
    projected=_recent_operation_tokens([live],now=now)[0]
    assert projected['current_mc_usd']==120 and projected['next_check_at']==now+60
    assert _recent_operation_tokens([live],exclude_live=True,now=now)==[]
    incompatible=dict(retained,entry_exactness='CLOSE_ONLY_LOWER_BOUND')
    value=_recent_operation_tokens([incompatible],now=now)[0]
    assert value['entry_mc_usd'] is None and value['max_multiple'] is None

def test_unified_template_contract_only():
    text=(ROOT/'templates/operators_index.html').read_text()
    assert "['TOKEN','OPERATION','AGE','OPENING / ENTRY MC','CURRENT MC','RUNNING PEAK','RUNNING MULTIPLE','TIME TO PEAK','NEXT CHECK','STATUS']" in text
    assert "['TOKEN','OPERATION','AGE','OPENING / ENTRY MC','ATH / PEAK','MAX MULTIPLE','TIME TO ATH / PEAK','EVIDENCE']" in text
    assert 'colspan=' in text  # empty state retains column headers
    assert "'watchtower_deep'?'Watchtower'" in text
    assert "window.flexRegistryRequest('/api/operations/live-monitor'" in text
    assert 'DOMParser' not in text
    assert 'LIVE WATCHTOWER' not in text and 'LIVE BYZANTINE' not in text
    assert 'not the complete historical database' in text
