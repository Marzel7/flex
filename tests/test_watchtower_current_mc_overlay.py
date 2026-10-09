import sqlite3
from types import SimpleNamespace
import pytest
from src.ops.watchtower_current_mc_overlay import CurrentMcOverlay,CurrentMcOverlayConfig,CurrentMcQuoteError,CurrentMcQuoteStore,normalize_token_overview_quote

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
