"""Default-off, compact Current-MC quote overlay for Watchtower LIVE rows."""
from __future__ import annotations

import math, os, sqlite3, time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from src.ops.token_data_provider_bindings import BirdeyeProductionBinding, ProviderTransportOutcome, build_birdeye_token_overview_request

QUOTE_SOURCE="BIRDEYE_TOKEN_OVERVIEW_MARKETCAP_V1"
DEFAULT_REFRESH_SECONDS=600
DEFAULT_MAX_TRADE_AGE_SECONDS=1200
MAX_RECORDS=64
MAX_STORE_BYTES=1_000_000

class CurrentMcQuoteError(ValueError): pass

def _bounded(value,default,minimum,maximum,name):
 if value in (None,""): return default
 try: parsed=int(value)
 except (TypeError,ValueError) as exc: raise CurrentMcQuoteError(f"INVALID_{name}") from exc
 if not minimum<=parsed<=maximum: raise CurrentMcQuoteError(f"INVALID_{name}")
 return parsed

@dataclass(frozen=True)
class CurrentMcOverlayConfig:
 enabled: bool
 refresh_seconds: int=DEFAULT_REFRESH_SECONDS
 max_trade_age_seconds: int=DEFAULT_MAX_TRADE_AGE_SECONDS
 max_records: int=MAX_RECORDS
 max_store_bytes: int=MAX_STORE_BYTES
 @classmethod
 def from_environ(cls,environ: Mapping[str,str]|None=None):
  values=os.environ if environ is None else environ
  return cls(str(values.get('WATCHTOWER_CURRENT_MC_OVERLAY_ENABLED','')).strip().lower() in {'1','true','yes','on'},_bounded(values.get('WATCHTOWER_CURRENT_MC_REFRESH_SECONDS'),600,60,3600,'CURRENT_MC_REFRESH_SECONDS'),_bounded(values.get('WATCHTOWER_CURRENT_MC_MAX_TRADE_AGE_SECONDS'),1200,60,86400,'CURRENT_MC_MAX_TRADE_AGE_SECONDS'))

def normalize_token_overview_quote(payload: Any,*,mint: str,fetched_at: int,max_trade_age_seconds: int)->dict[str,Any]:
 """Accept only a direct, positive finite marketCap and fresh trade timestamp."""
 if not isinstance(payload,Mapping) or payload.get('success') is not True: raise CurrentMcQuoteError('TOKEN_OVERVIEW_UNSUCCESSFUL')
 data=payload.get('data')
 if not isinstance(data,Mapping): raise CurrentMcQuoteError('TOKEN_OVERVIEW_DATA_MISSING')
 try: market_cap=float(data.get('marketCap'))
 except (TypeError,ValueError) as exc: raise CurrentMcQuoteError('MARKETCAP_MISSING_OR_MALFORMED') from exc
 if not math.isfinite(market_cap) or market_cap<=0: raise CurrentMcQuoteError('MARKETCAP_NOT_POSITIVE_FINITE')
 try: trade=int(data.get('lastTradeUnixTime'))
 except (TypeError,ValueError) as exc: raise CurrentMcQuoteError('TRADE_TIME_MISSING_OR_MALFORMED') from exc
 if trade<=0 or trade>fetched_at+60: raise CurrentMcQuoteError('TRADE_TIME_INVALID')
 if fetched_at-trade>max_trade_age_seconds: raise CurrentMcQuoteError('TRADE_TIME_STALE')
 return {'mint':str(mint),'current_mc_quote_usd':market_cap,'quote_fetched_at':int(fetched_at),'quote_last_trade_at':trade,'quote_source':QUOTE_SOURCE,'quote_freshness':'FRESH','quote_expires_at':int(fetched_at)+max_trade_age_seconds}

class CurrentMcQuoteStore:
 """One row per mint; physical store bound is 1MB and 64 records by default."""
 def __init__(self,path,*,max_records=MAX_RECORDS,max_store_bytes=MAX_STORE_BYTES): self.path=Path(path).expanduser();self.max_records=int(max_records);self.max_store_bytes=int(max_store_bytes)
 def _capacity(self):
  if self.path.exists() and self.path.stat().st_size>self.max_store_bytes: raise CurrentMcQuoteError('QUOTE_STORE_SIZE_LIMIT')
 def _connect(self):
  self.path.parent.mkdir(parents=True,exist_ok=True);self._capacity();con=sqlite3.connect(self.path)
  con.execute('CREATE TABLE IF NOT EXISTS current_mc_quotes (mint TEXT PRIMARY KEY,current_mc_quote_usd REAL,quote_fetched_at INTEGER NOT NULL,quote_last_trade_at INTEGER,quote_source TEXT NOT NULL,quote_expires_at INTEGER NOT NULL,quote_status TEXT NOT NULL,updated_at INTEGER NOT NULL)')
  return con
 def put(self,q):
  with self._connect() as con:
   exists=con.execute('SELECT 1 FROM current_mc_quotes WHERE mint=?',(q['mint'],)).fetchone() is not None
   if not exists and int(con.execute('SELECT COUNT(*) FROM current_mc_quotes').fetchone()[0])>=self.max_records: raise CurrentMcQuoteError('QUOTE_STORE_RECORD_LIMIT')
   con.execute('INSERT INTO current_mc_quotes VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(mint) DO UPDATE SET current_mc_quote_usd=excluded.current_mc_quote_usd,quote_fetched_at=excluded.quote_fetched_at,quote_last_trade_at=excluded.quote_last_trade_at,quote_source=excluded.quote_source,quote_expires_at=excluded.quote_expires_at,quote_status=excluded.quote_status,updated_at=excluded.updated_at',(q['mint'],q['current_mc_quote_usd'],q['quote_fetched_at'],q['quote_last_trade_at'],q['quote_source'],q['quote_expires_at'],'FRESH',q['quote_fetched_at']))
  self._capacity()
 def put_failure(self,*,mint,fetched_at,retry_at):
  """Persist only a bounded negative cadence marker, never a provider payload."""
  with self._connect() as con:
   exists=con.execute('SELECT 1 FROM current_mc_quotes WHERE mint=?',(mint,)).fetchone() is not None
   if not exists and int(con.execute('SELECT COUNT(*) FROM current_mc_quotes').fetchone()[0])>=self.max_records: raise CurrentMcQuoteError('QUOTE_STORE_RECORD_LIMIT')
   con.execute('INSERT INTO current_mc_quotes VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(mint) DO UPDATE SET current_mc_quote_usd=NULL,quote_fetched_at=excluded.quote_fetched_at,quote_last_trade_at=NULL,quote_source=excluded.quote_source,quote_expires_at=excluded.quote_expires_at,quote_status=excluded.quote_status,updated_at=excluded.updated_at',(mint,None,fetched_at,None,QUOTE_SOURCE,retry_at,'UNAVAILABLE',fetched_at))
  self._capacity()
 def read_only(self,*,now):
  if not self.path.is_file() or self.path.stat().st_size>self.max_store_bytes:return {}
  try:
   con=sqlite3.connect(f'{self.path.resolve().as_uri()}?mode=ro',uri=True); rows=con.execute('SELECT mint,current_mc_quote_usd,quote_fetched_at,quote_last_trade_at,quote_source,quote_expires_at,quote_status FROM current_mc_quotes').fetchall();con.close()
  except sqlite3.Error:return {}
  return {str(m):{'current_mc_quote_usd':v if status=='FRESH' and int(e)>=int(now) else None,'quote_fetched_at':f,'quote_last_trade_at':t,'quote_source':s,'quote_freshness':'FRESH' if status=='FRESH' and int(e)>=int(now) else 'STALE' if status=='FRESH' else 'UNAVAILABLE','quote_expires_at':e} for m,v,f,t,s,e,status in rows}

class CurrentMcOverlay:
 """One worker-owned acquisition attempt. It owns no scheduler or queue."""
 def __init__(self,*,quote_store,config,transport=None,now=time.time): self.quote_store=quote_store;self.config=config;self.transport=transport or BirdeyeProductionBinding(endpoint='https://public-api.birdeye.so/defi/token_overview');self.now=now
 def acquire_one(self,*,mint,queue):
  if not self.config.enabled:return {'status':'DISABLED'}
  fetched=int(self.now()); prior=self.quote_store.read_only(now=fetched).get(str(mint))
  if prior and int(prior.get('quote_fetched_at') or 0)+self.config.refresh_seconds>fetched:return {'status':'NOT_DUE'}
  queue.admit_provider_dispatch(str(mint),'CURRENT_MC_OVERLAY')
  try: outcome: ProviderTransportOutcome=self.transport(build_birdeye_token_overview_request(address=str(mint)))
  except Exception:
   self.quote_store.put_failure(mint=str(mint),fetched_at=fetched,retry_at=fetched+self.config.refresh_seconds);return {'status':'TRANSPORT_REJECTED'}
  if int(outcome.status_code)!=200:
   self.quote_store.put_failure(mint=str(mint),fetched_at=fetched,retry_at=fetched+self.config.refresh_seconds);return {'status':'PROVIDER_REJECTED','http_status':int(outcome.status_code)}
  try: quote=normalize_token_overview_quote(outcome.payload,mint=str(mint),fetched_at=fetched,max_trade_age_seconds=self.config.max_trade_age_seconds)
  except CurrentMcQuoteError as exc:
   self.quote_store.put_failure(mint=str(mint),fetched_at=fetched,retry_at=fetched+self.config.refresh_seconds);return {'status':'REJECTED','reason':str(exc)}
  self.quote_store.put(quote);queue.record_provider_success();return {'status':'COMMITTED',**quote}

def read_current_mc_quote_projection(path,*,now=None):
 return {} if not path else CurrentMcQuoteStore(path).read_only(now=int(time.time() if now is None else now))
