from __future__ import annotations

import json
import sqlite3
import statistics
import time
from collections import defaultdict
from typing import Any

from src.utils.infra_mapping import sync_infra_wallets
from src.utils.db_locking import db_connect

SURVIVAL_MC_USD = 5000.0
LIVE_ENTRY_MC_MIN_USD = 10000.0
LIVE_ENTRY_MC_MAX_USD = 100000.0


def _median(xs: list[float]) -> float | None:
    return statistics.median(xs) if xs else None


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def _hist_score(r: dict[str, Any]) -> float:
    market_cov = (r['tokens_with_market_data'] / r['total_tokens']) if r['total_tokens'] else 0.0
    migration = float(r.get('migration_rate_pct') or 0) / 100.0
    survival = float(r.get('current_survival_rate_pct') or 0) / 100.0
    dead = float(r.get('rug_or_dead_rate_pct') or 0) / 100.0
    med_peak = float(r.get('median_peak_mc') or 0)
    peak_norm = _clamp(med_peak / 100000.0)
    coverage_penalty = 0.25 + 0.75 * market_cov
    score = (0.28 * migration + 0.28 * survival + 0.24 * peak_norm + 0.20 * (1.0 - dead)) * coverage_penalty
    return round(_clamp(score) * 100.0, 2)


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript('''
    CREATE TABLE IF NOT EXISTS creator_historical_performance (
      creator_address TEXT PRIMARY KEY,
      total_tokens INTEGER NOT NULL,
      tokens_with_market_data INTEGER NOT NULL,
      simulated_tokens INTEGER NOT NULL,
      simulation_coverage_pct REAL NOT NULL,
      migration_count INTEGER NOT NULL,
      migration_rate_pct REAL NOT NULL,
      median_peak_mc REAL,
      max_peak_mc REAL,
      median_current_mc REAL,
      current_survival_rate_pct REAL NOT NULL,
      rug_or_dead_rate_pct REAL NOT NULL,
      first_observed_mc_coverage_pct REAL NOT NULL DEFAULT 0,
      median_peak_multiple REAL,
      median_current_multiple REAL,
      best_runner_token_mint TEXT,
      worst_drawdown_token_mint TEXT,
      best_token_mint TEXT,
      worst_token_mint TEXT,
      latest_token_mint TEXT,
      high_runner_2x_count INTEGER NOT NULL DEFAULT 0,
      high_runner_5x_count INTEGER NOT NULL,
      high_runner_10x_count INTEGER NOT NULL,
      live_eligible_count INTEGER NOT NULL,
      historical_outcome_score REAL NOT NULL,
      refreshed_at INTEGER NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_creator_hist_score ON creator_historical_performance(historical_outcome_score DESC);

    CREATE TABLE IF NOT EXISTS network_historical_performance (
      network_name TEXT PRIMARY KEY,
      network_size INTEGER NOT NULL,
      creators_with_token_analysis INTEGER NOT NULL,
      total_tokens INTEGER NOT NULL,
      tokens_with_market_data INTEGER NOT NULL,
      simulated_tokens INTEGER NOT NULL,
      simulation_coverage_pct REAL NOT NULL,
      migration_count INTEGER NOT NULL,
      migration_rate_pct REAL NOT NULL,
      median_peak_mc REAL,
      max_peak_mc REAL,
      median_current_mc REAL,
      current_survival_rate_pct REAL NOT NULL,
      rug_or_dead_rate_pct REAL NOT NULL,
      high_runner_2x_count INTEGER NOT NULL DEFAULT 0,
      high_runner_5x_count INTEGER NOT NULL,
      high_runner_10x_count INTEGER NOT NULL,
      live_eligible_count INTEGER NOT NULL,
      historical_outcome_score REAL NOT NULL,
      refreshed_at INTEGER NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_network_hist_score ON network_historical_performance(historical_outcome_score DESC);

    CREATE TABLE IF NOT EXISTS funder_historical_performance (
      funder_wallet TEXT PRIMARY KEY,
      creators_funded INTEGER NOT NULL,
      creators_with_token_analysis INTEGER NOT NULL,
      total_tokens INTEGER NOT NULL,
      tokens_with_market_data INTEGER NOT NULL,
      simulated_tokens INTEGER NOT NULL,
      simulation_coverage_pct REAL NOT NULL,
      migration_rate_pct REAL NOT NULL,
      median_peak_mc REAL,
      median_current_mc REAL,
      current_survival_rate_pct REAL NOT NULL,
      rug_or_dead_rate_pct REAL NOT NULL,
      historical_outcome_score REAL NOT NULL,
      attribution_label TEXT NOT NULL,
      refreshed_at INTEGER NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_funder_hist_score ON funder_historical_performance(historical_outcome_score DESC);

    CREATE TABLE IF NOT EXISTS cluster_historical_performance (
      cluster_id INTEGER PRIMARY KEY,
      members_with_token_count INTEGER NOT NULL,
      members_with_token_analysis INTEGER NOT NULL,
      total_tokens INTEGER NOT NULL,
      tokens_with_market_data INTEGER NOT NULL,
      simulated_tokens INTEGER NOT NULL,
      simulation_coverage_pct REAL NOT NULL,
      migration_rate_pct REAL NOT NULL,
      median_peak_mc REAL,
      median_current_mc REAL,
      current_survival_rate_pct REAL NOT NULL,
      rug_or_dead_rate_pct REAL NOT NULL,
      high_runner_2x_count INTEGER NOT NULL DEFAULT 0,
      high_runner_5x_count INTEGER NOT NULL,
      high_runner_10x_count INTEGER NOT NULL,
      live_eligible_count INTEGER NOT NULL,
      historical_outcome_score REAL NOT NULL,
      refreshed_at INTEGER NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_cluster_hist_score ON cluster_historical_performance(historical_outcome_score DESC);
    ''')


def _mint_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    # token_analysis is the source of truth. It does not currently carry a reliable
    # historical initial/entry MC, so peak-multiple fields remain NULL rather than
    # being reconstructed from lossy later observations.
    return conn.execute("""
      WITH creator_mints AS (
        SELECT mint, earliest_tx_creator creator_address FROM token_analysis WHERE earliest_tx_creator IS NOT NULL
        UNION
        SELECT mint, pf_ws_creator creator_address FROM token_analysis WHERE pf_ws_creator IS NOT NULL
      )
      SELECT cm.creator_address, ta.mint, ta.created_at, ta.market_cap_current, ta.market_cap_highest,
             ta.migrated_at, ta.migration_tx, ta.rug_indicator,
             ta.first_observed_mc AS initial_mc,
             CASE WHEN ts.mint IS NULL THEN 0 ELSE 1 END simulated
      FROM creator_mints cm
      JOIN token_analysis ta ON ta.mint=cm.mint
      LEFT JOIN (SELECT DISTINCT mint FROM trade_simulations) ts ON ts.mint=ta.mint
    """).fetchall()


def _summarize(tokens: list[dict[str, Any]]) -> dict[str, Any]:
    total=len(tokens)
    market=[t for t in tokens if (t['market_cap_current'] or 0)>0 or (t['market_cap_highest'] or 0)>0]
    peaks=[float(t['market_cap_highest']) for t in tokens if (t['market_cap_highest'] or 0)>0]
    currents=[float(t['market_cap_current']) for t in tokens if (t['market_cap_current'] or 0)>0]
    multiples=[float(t['market_cap_highest'])/float(t['initial_mc']) for t in tokens if (t['market_cap_highest'] or 0)>0 and (t['initial_mc'] or 0)>0]
    current_multiples=[float(t['market_cap_current'])/float(t['initial_mc']) for t in tokens if (t['market_cap_current'] or 0)>0 and (t['initial_mc'] or 0)>0]
    high2=sum(m>=2 for m in multiples); high5=sum(m>=5 for m in multiples); high10=sum(m>=10 for m in multiples)
    migrations=sum(bool(t['migrated_at'] or t['migration_tx']) for t in tokens)
    simulated=sum(int(t['simulated']) for t in tokens)
    survived=sum((t['market_cap_current'] or 0)>=SURVIVAL_MC_USD for t in market)
    dead=sum(bool(t['rug_indicator']) or ((t['market_cap_current'] or 0)>0 and (t['market_cap_current'] or 0)<SURVIVAL_MC_USD) for t in market)
    eligible=sum(LIVE_ENTRY_MC_MIN_USD <= (t['market_cap_current'] or 0) <= LIVE_ENTRY_MC_MAX_USD for t in tokens)
    best=max(tokens, key=lambda t: t['market_cap_highest'] or -1)['mint'] if tokens else None
    worst=min(market, key=lambda t: t['market_cap_current'] or 0)['mint'] if market else None
    runner_tokens=[t for t in tokens if (t['market_cap_highest'] or 0)>0 and (t['initial_mc'] or 0)>0]
    best_runner=max(runner_tokens, key=lambda t: float(t['market_cap_highest'])/float(t['initial_mc']))['mint'] if runner_tokens else None
    draw_tokens=[t for t in tokens if (t['market_cap_current'] or 0)>0 and (t['initial_mc'] or 0)>0]
    worst_drawdown=min(draw_tokens, key=lambda t: float(t['market_cap_current'])/float(t['initial_mc']))['mint'] if draw_tokens else None
    latest=max(tokens, key=lambda t: str(t['created_at'] or ''))['mint'] if tokens else None
    d={
      'total_tokens': total,
      'tokens_with_market_data': len(market),
      'simulated_tokens': simulated,
      'simulation_coverage_pct': simulated/total*100 if total else 0,
      'migration_count': migrations,
      'migration_rate_pct': migrations/total*100 if total else 0,
      'median_peak_mc': _median(peaks),
      'max_peak_mc': max(peaks) if peaks else None,
      'median_current_mc': _median(currents),
      'current_survival_rate_pct': survived/len(market)*100 if market else 0,
      'rug_or_dead_rate_pct': dead/len(market)*100 if market else 0,
      'first_observed_mc_coverage_pct': len(multiples)/total*100 if total else 0,
      'median_peak_multiple': _median(multiples),
      'median_current_multiple': _median(current_multiples),
      'best_runner_token_mint': best_runner,
      'worst_drawdown_token_mint': worst_drawdown,
      'best_token_mint': best,
      'worst_token_mint': worst,
      'latest_token_mint': latest,
      'high_runner_2x_count': high2,
      'high_runner_5x_count': high5,
      'high_runner_10x_count': high10,
      'live_eligible_count': eligible,
    }
    d['historical_outcome_score']=_hist_score(d)
    return d


def _ensure_extra_columns(conn: sqlite3.Connection) -> None:
    wanted = {
      'creator_historical_performance': [
        ('first_observed_mc_coverage_pct','REAL NOT NULL DEFAULT 0'),('median_current_multiple','REAL'),
        ('best_runner_token_mint','TEXT'),('worst_drawdown_token_mint','TEXT'),('high_runner_2x_count','INTEGER NOT NULL DEFAULT 0')],
      'network_historical_performance': [('first_observed_mc_coverage_pct','REAL NOT NULL DEFAULT 0'),('median_peak_multiple','REAL'),('median_current_multiple','REAL'),('high_runner_2x_count','INTEGER NOT NULL DEFAULT 0')],
      'funder_historical_performance': [('first_observed_mc_coverage_pct','REAL NOT NULL DEFAULT 0'),('median_peak_multiple','REAL'),('median_current_multiple','REAL'),('high_runner_2x_count','INTEGER NOT NULL DEFAULT 0'),('high_runner_5x_count','INTEGER NOT NULL DEFAULT 0'),('high_runner_10x_count','INTEGER NOT NULL DEFAULT 0')],
      'cluster_historical_performance': [('first_observed_mc_coverage_pct','REAL NOT NULL DEFAULT 0'),('median_peak_multiple','REAL'),('median_current_multiple','REAL'),('high_runner_2x_count','INTEGER NOT NULL DEFAULT 0')],
    }
    for table, cols in wanted.items():
        have={r[1] for r in conn.execute(f'pragma table_info({table})')}
        for name, typ in cols:
            if name not in have: conn.execute(f'alter table {table} add column {name} {typ}')


class CreatorHistoricalPerformanceAnalyzer:
    def __init__(self, db_path: str): self.db_path=db_path
    def run(self)->dict[str,int]:
        now=int(time.time()); conn=db_connect(self.db_path, timeout=60, row_factory=sqlite3.Row); ensure_schema(conn); _ensure_extra_columns(conn)
        grouped=defaultdict(list)
        for r in _mint_rows(conn): grouped[r['creator_address']].append(dict(r))
        conn.execute('DELETE FROM creator_historical_performance')
        for creator,tokens in grouped.items():
            d=_summarize(tokens)
            conn.execute('''INSERT INTO creator_historical_performance (creator_address,total_tokens,tokens_with_market_data,simulated_tokens,simulation_coverage_pct,migration_count,migration_rate_pct,median_peak_mc,max_peak_mc,median_current_mc,current_survival_rate_pct,rug_or_dead_rate_pct,first_observed_mc_coverage_pct,median_peak_multiple,median_current_multiple,best_runner_token_mint,worst_drawdown_token_mint,best_token_mint,worst_token_mint,latest_token_mint,high_runner_2x_count,high_runner_5x_count,high_runner_10x_count,live_eligible_count,historical_outcome_score,refreshed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(creator,d['total_tokens'],d['tokens_with_market_data'],d['simulated_tokens'],d['simulation_coverage_pct'],d['migration_count'],d['migration_rate_pct'],d['median_peak_mc'],d['max_peak_mc'],d['median_current_mc'],d['current_survival_rate_pct'],d['rug_or_dead_rate_pct'],d['first_observed_mc_coverage_pct'],d['median_peak_multiple'],d['median_current_multiple'],d['best_runner_token_mint'],d['worst_drawdown_token_mint'],d['best_token_mint'],d['worst_token_mint'],d['latest_token_mint'],d['high_runner_2x_count'],d['high_runner_5x_count'],d['high_runner_10x_count'],d['live_eligible_count'],d['historical_outcome_score'],now))
        conn.commit(); n=conn.execute('select count(*) from creator_historical_performance').fetchone()[0]; conn.close(); return {'creator_history_rows':n}


class NetworkHistoricalPerformanceAnalyzer:
    def __init__(self, db_path: str): self.db_path=db_path
    def run(self)->dict[str,int]:
        now=int(time.time()); conn=db_connect(self.db_path, timeout=60, row_factory=sqlite3.Row); ensure_schema(conn); _ensure_extra_columns(conn)
        tokens_by_creator=defaultdict(list)
        for r in _mint_rows(conn): tokens_by_creator[r['creator_address']].append(dict(r))
        conn.execute('DELETE FROM network_historical_performance')
        for nr in conn.execute('select network_name, network_size from networks_release'):
            creators=[r[0] for r in conn.execute('select creator_address from network_membership where network_name=?',(nr['network_name'],))]
            tokens=[]
            for cr in creators: tokens.extend(tokens_by_creator.get(cr,[]))
            d=_summarize(tokens)
            conn.execute('''INSERT INTO network_historical_performance (network_name,network_size,creators_with_token_analysis,total_tokens,tokens_with_market_data,simulated_tokens,simulation_coverage_pct,migration_count,migration_rate_pct,median_peak_mc,max_peak_mc,median_current_mc,current_survival_rate_pct,rug_or_dead_rate_pct,first_observed_mc_coverage_pct,median_peak_multiple,median_current_multiple,high_runner_2x_count,high_runner_5x_count,high_runner_10x_count,live_eligible_count,historical_outcome_score,refreshed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(
              nr['network_name'],nr['network_size'],sum(bool(tokens_by_creator.get(c)) for c in creators),d['total_tokens'],d['tokens_with_market_data'],d['simulated_tokens'],d['simulation_coverage_pct'],d['migration_count'],d['migration_rate_pct'],d['median_peak_mc'],d['max_peak_mc'],d['median_current_mc'],d['current_survival_rate_pct'],d['rug_or_dead_rate_pct'],d['first_observed_mc_coverage_pct'],d['median_peak_multiple'],d['median_current_multiple'],d['high_runner_2x_count'],d['high_runner_5x_count'],d['high_runner_10x_count'],d['live_eligible_count'],d['historical_outcome_score'],now))
        conn.commit(); n=conn.execute('select count(*) from network_historical_performance').fetchone()[0]; conn.close(); return {'network_history_rows':n}


class FunderHistoricalPerformanceAnalyzer:
    def __init__(self, db_path: str): self.db_path=db_path
    def run(self)->dict[str,int]:
        now=int(time.time()); conn=db_connect(self.db_path, timeout=60, row_factory=sqlite3.Row); ensure_schema(conn); _ensure_extra_columns(conn)
        tokens_by_creator=defaultdict(list)
        for r in _mint_rows(conn): tokens_by_creator[r['creator_address']].append(dict(r))
        grouped=defaultdict(set)
        for r in conn.execute("select creator_address,funder_address from creator_funders where is_cex=0 and funder_address not in (select address from infra_wallets)"):
            grouped[r['funder_address']].add(r['creator_address'])
        conn.execute('DELETE FROM funder_historical_performance')
        for wallet, creators in grouped.items():
            toks=[]
            for c in creators: toks.extend(tokens_by_creator.get(c,[]))
            d=_summarize(toks)
            conn.execute('''INSERT INTO funder_historical_performance (funder_wallet,creators_funded,creators_with_token_analysis,total_tokens,tokens_with_market_data,simulated_tokens,simulation_coverage_pct,migration_rate_pct,median_peak_mc,median_current_mc,current_survival_rate_pct,rug_or_dead_rate_pct,first_observed_mc_coverage_pct,median_peak_multiple,median_current_multiple,high_runner_2x_count,high_runner_5x_count,high_runner_10x_count,historical_outcome_score,attribution_label,refreshed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(
              wallet,len(creators),sum(bool(tokens_by_creator.get(c)) for c in creators),d['total_tokens'],d['tokens_with_market_data'],d['simulated_tokens'],d['simulation_coverage_pct'],d['migration_rate_pct'],d['median_peak_mc'],d['median_current_mc'],d['current_survival_rate_pct'],d['rug_or_dead_rate_pct'],d['first_observed_mc_coverage_pct'],d['median_peak_multiple'],d['median_current_multiple'],d['high_runner_2x_count'],d['high_runner_5x_count'],d['high_runner_10x_count'],d['historical_outcome_score'],'expectancy attribution, not funder cashflow PnL',now))
        conn.commit(); n=conn.execute('select count(*) from funder_historical_performance').fetchone()[0]; conn.close(); return {'funder_history_rows':n}


class ClusterHistoricalPerformanceAnalyzer:
    def __init__(self, db_path: str): self.db_path=db_path
    def run(self)->dict[str,int]:
        now=int(time.time()); conn=db_connect(self.db_path, timeout=60, row_factory=sqlite3.Row); ensure_schema(conn); _ensure_extra_columns(conn)
        tokens_by_creator=defaultdict(list)
        for r in _mint_rows(conn): tokens_by_creator[r['creator_address']].append(dict(r))
        conn.execute('DELETE FROM cluster_historical_performance')
        for cid in [r[0] for r in conn.execute('select cluster_id from farm_clusters')]:
            members=[r[0] for r in conn.execute('select wallet_address from farm_cluster_members where cluster_id=? and coalesce(token_count,0)>0',(cid,))]
            toks=[]
            for m in members: toks.extend(tokens_by_creator.get(m,[]))
            d=_summarize(toks)
            conn.execute('''INSERT INTO cluster_historical_performance (cluster_id,members_with_token_count,members_with_token_analysis,total_tokens,tokens_with_market_data,simulated_tokens,simulation_coverage_pct,migration_rate_pct,median_peak_mc,median_current_mc,current_survival_rate_pct,rug_or_dead_rate_pct,first_observed_mc_coverage_pct,median_peak_multiple,median_current_multiple,high_runner_2x_count,high_runner_5x_count,high_runner_10x_count,live_eligible_count,historical_outcome_score,refreshed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(
              cid,len(members),sum(bool(tokens_by_creator.get(m)) for m in members),d['total_tokens'],d['tokens_with_market_data'],d['simulated_tokens'],d['simulation_coverage_pct'],d['migration_rate_pct'],d['median_peak_mc'],d['median_current_mc'],d['current_survival_rate_pct'],d['rug_or_dead_rate_pct'],d['first_observed_mc_coverage_pct'],d['median_peak_multiple'],d['median_current_multiple'],d['high_runner_2x_count'],d['high_runner_5x_count'],d['high_runner_10x_count'],d['live_eligible_count'],d['historical_outcome_score'],now))
        conn.commit(); n=conn.execute('select count(*) from cluster_historical_performance').fetchone()[0]; conn.close(); return {'cluster_history_rows':n}
