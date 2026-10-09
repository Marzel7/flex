import sqlite3
from types import SimpleNamespace
import pytest
from src.ops.watchtower_current_mc_overlay import CurrentMcOverlay,CurrentMcOverlayConfig,CurrentMcQuoteError,CurrentMcQuoteStore,normalize_token_overview_quote
from src.ops.operation_monitor_worker import MonitorWorker
from src.ops.operator_lifecycle_projection import ensure_schema

class Gate:
 def __init__(self):self.admits=[];self.successes=0
 def admit_provider_dispatch(self,mint,kind):self.admits.append((mint,kind))
 def record_provider_success(self):self.successes+=1
def payload(mc=123.0,trade=100):return {'success':True,'data':{'marketCap':mc,'lastTradeUnixTime':trade}}
def test_valid_direct_market_cap_and_dedupe(tmp_path):
 gate=Gate();store=CurrentMcQuoteStore(tmp_path/'quotes.db');overlay=CurrentMcOverlay(quote_store=store,config=CurrentMcOverlayConfig(True,600,1200),now=lambda:110,transport=lambda _:SimpleNamespace(status_code=200,payload=payload(),response_headers={}))
 assert overlay.acquire_one(mint='mint',queue=gate)['status']=='COMMITTED';assert overlay.acquire_one(mint='mint',queue=gate)['status']=='NOT_DUE';assert gate.admits==[('mint','CURRENT_MC_OVERLAY')];assert store.read_only(now=110)['mint']['current_mc_quote_usd']==123.0
def test_feature_defaults_off_without_provider_work():
 assert CurrentMcOverlayConfig.from_environ({}).enabled is False
def test_http_200_missing_and_stale_fail_closed():
 with pytest.raises(CurrentMcQuoteError,match='MARKETCAP'):normalize_token_overview_quote({'success':True,'data':{}},mint='m',fetched_at=100,max_trade_age_seconds=10)
 with pytest.raises(CurrentMcQuoteError,match='STALE'):normalize_token_overview_quote(payload(trade=1),mint='m',fetched_at=100,max_trade_age_seconds=10)
def test_bad_provider_result_is_bounded_until_the_next_refresh(tmp_path):
 gate=Gate();store=CurrentMcQuoteStore(tmp_path/'quotes.db');overlay=CurrentMcOverlay(quote_store=store,config=CurrentMcOverlayConfig(True,600,1200),now=lambda:110,transport=lambda _:SimpleNamespace(status_code=200,payload={'success':True,'data':{}},response_headers={}))
 assert overlay.acquire_one(mint='m',queue=gate)['status']=='REJECTED';assert overlay.acquire_one(mint='m',queue=gate)['status']=='NOT_DUE';assert gate.admits==[('m','CURRENT_MC_OVERLAY')];assert store.read_only(now=110)['m']['quote_freshness']=='UNAVAILABLE'
def test_expiry_bound_and_fact_isolation(tmp_path):
 store=CurrentMcQuoteStore(tmp_path/'quotes.db',max_records=1);store.put(normalize_token_overview_quote(payload(),mint='a',fetched_at=110,max_trade_age_seconds=10));assert store.read_only(now=121)['a']['quote_freshness']=='STALE'
 with pytest.raises(CurrentMcQuoteError,match='RECORD_LIMIT'):store.put(normalize_token_overview_quote(payload(),mint='b',fetched_at=110,max_trade_age_seconds=10))
 facts=tmp_path/'facts.db';con=sqlite3.connect(facts);con.execute('create table operation_monitor_facts(mint text primary key,latest_mc_usd real,running_peak_mc_usd real)');con.execute("insert into operation_monitor_facts values('a',1,2)");con.commit();assert con.execute("select latest_mc_usd,running_peak_mc_usd from operation_monitor_facts").fetchone()==(1.0,2.0);con.close()

def _monitor_store(path, *, mint, state):
 with sqlite3.connect(path) as con:
  ensure_schema(con)
  con.execute("""INSERT INTO operation_monitor_facts(
   operation_id,mint,cohort_class,entry_method,entry_status,entry_exactness,
   monitor_state,entry_timestamp,entry_mc_usd,latest_mc_usd,
   running_peak_mc_usd,running_peak_timestamp,running_peak_multiple,
   drawdown_percent,next_observation_at,provenance_digest,created_at,updated_at
  ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",(
   'watchtower',mint,'PROSPECTIVE_MONITOR_COHORT','FIRST_FULL_POST_MIGRATION_SECOND_MC','QUALIFIED','EXACT',
   state,100,144175.59776543334,442327.04,594764.794662167,150,4.125,25.0,700,'proof',100,100,
  ))

def _queue_root(path):
 for state in ('pending','retry','processing','dead_letter'):
  (path/state).mkdir(parents=True,exist_ok=True)
 return path

def test_live_only_worker_sidecar_and_api_projection_preserve_monitor_facts(monkeypatch,tmp_path):
 """A retained CatTok-shaped contract payload qualifies only a synthetic LIVE mint."""
 active='synthetic-monitoring-active'; terminal='DNtZMNDZ65hAJbJXRiP9NnV98ZTrwLTEJZ78fWKCpump'
 facts=tmp_path/'monitor.sqlite';_monitor_store(facts,mint=active,state='MONITORING_ACTIVE');_monitor_store(facts,mint=terminal,state='PRICE_MONITOR_COMPLETE_COLLAPSED')
 quote_path=tmp_path/'quotes.sqlite';monkeypatch.setenv('WATCHTOWER_CURRENT_MC_OVERLAY_ENABLED','1');monkeypatch.setenv('WATCHTOWER_CURRENT_MC_QUOTE_DB_PATH',str(quote_path))
 gate=Gate();worker=MonitorWorker(gate,transport=lambda _:None,db_path=str(facts))
 worker._current_mc_overlay=CurrentMcOverlay(quote_store=CurrentMcQuoteStore(quote_path),config=CurrentMcOverlayConfig(True,600,1200),now=lambda:1791530136,transport=lambda _:SimpleNamespace(status_code=200,payload=payload(3265.40428003735,1791530121),response_headers={}))
 with sqlite3.connect(facts) as con:
  before=con.execute('SELECT entry_mc_usd,latest_mc_usd,running_peak_mc_usd,drawdown_percent,next_observation_at,monitor_state FROM operation_monitor_facts ORDER BY mint').fetchall()
 assert worker.process_current_mc_overlay_once()['status']=='COMMITTED'
 assert gate.admits==[(active,'CURRENT_MC_OVERLAY')]
 assert CurrentMcQuoteStore(quote_path).read_only(now=1791530136)[active]['current_mc_quote_usd']==3265.40428003735
 with sqlite3.connect(facts) as con:
  after=con.execute('SELECT entry_mc_usd,latest_mc_usd,running_peak_mc_usd,drawdown_percent,next_observation_at,monitor_state FROM operation_monitor_facts ORDER BY mint').fetchall()
 assert after==before
 with sqlite3.connect(facts) as con:
  con.execute("UPDATE operation_monitor_facts SET monitor_state='PRICE_MONITOR_COMPLETE_COLLAPSED' WHERE mint=?",(active,))
 assert worker.process_current_mc_overlay_once()=={'status':'NO_ELIGIBLE_LIVE_TOKEN'}
 assert gate.admits==[(active,'CURRENT_MC_OVERLAY')]
 assert terminal not in CurrentMcQuoteStore(quote_path).read_only(now=1791530136)

 monkeypatch.setenv('WATCHTOWER_MONITOR_UI_DB_PATH',str(facts));monkeypatch.setenv('WATCHTOWER_MONITOR_QUEUE_PATH',str(_queue_root(tmp_path/'queue')))
 import src.ops.operator_routes as routes
 monkeypatch.setattr(routes.time,'time',lambda:1791530136)
 rows={row['mint']:row for row in routes._monitor_live_projection()['rows']}
 row=rows[active]
 assert row['current_mc_quote_usd']==3265.40428003735
 assert row['quote_freshness']=='FRESH'
 assert row['latest_mc_usd']==442327.04
 assert row['running_peak_mc_usd']==594764.794662167
 assert rows[terminal]['current_mc_quote_usd'] is None
 assert rows[terminal]['next_check_state']=='NO_FURTHER_CHECK'

def test_current_mc_store_hard_bounds_and_invalid_payloads_fail_closed(tmp_path):
 store=CurrentMcQuoteStore(tmp_path/'quotes.sqlite',max_records=64,max_store_bytes=1_000_000)
 for index in range(64):store.put(normalize_token_overview_quote(payload(100+index,100),mint=f'm{index}',fetched_at=110,max_trade_age_seconds=1200))
 with pytest.raises(CurrentMcQuoteError,match='RECORD_LIMIT'):store.put(normalize_token_overview_quote(payload(),mint='overflow',fetched_at=110,max_trade_age_seconds=1200))
 for broken in ({'success':True,'data':{}},payload(float('nan'),100),payload(1,1)):
  with pytest.raises(CurrentMcQuoteError):normalize_token_overview_quote(broken,mint='invalid',fetched_at=2000,max_trade_age_seconds=10)
