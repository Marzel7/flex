from __future__ import annotations

import json
import math
import sqlite3
import statistics
import time
from collections import defaultdict
from typing import Any

from src.utils.infra_mapping import sync_infra_wallets

STRATEGIES = (
    'cascade','target_1_5','target_2_5','target_3','target_3_5',
    'target_5','target_7','target_10','peak','watch_trailing'
)
PRIMARY_STRATEGY = 'cascade'
MIN_LIVE_MARKET_CAP = 5000.0


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def _median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def _qscore_creator(r: dict[str, Any]) -> float:
    roi = float(r.get('roi_pct') or 0)
    deployed = float(r.get('total_deployed_usd') or 0)
    equity = float(r.get('portfolio_equity_usd') or 0)
    migration = float(r.get('migration_rate_pct') or 0) / 100.0
    profitable_launches = float(r.get('profitable_tokens') or 0)
    rug = float(r.get('rug_rate_pct') or 0) / 100.0
    self_funding = float(r.get('self_funding_percentage') or 0) / 100.0
    farm_risk = float(r.get('farm_risk_score') or 0) / 100.0
    survival = float(r.get('survival_rate_pct') or 0) / 100.0
    network_q = float(r.get('network_quality_score') or 50) / 100.0
    funder_q = float(r.get('funder_quality_score') or 50) / 100.0
    roi_norm = _clamp((roi + 100.0) / 300.0)
    retention = _clamp((equity / deployed) / 2.0) if deployed else 0.0
    repeat_profit = _clamp(profitable_launches / 3.0)
    score = (
        0.22 * roi_norm + 0.14 * retention + 0.12 * migration + 0.10 * repeat_profit +
        0.10 * network_q + 0.08 * funder_q + 0.10 * survival - 0.06 * self_funding -
        0.04 * farm_risk - 0.04 * rug
    )
    return round(_clamp(score) * 100.0, 2)


def _qscore_network(r: dict[str, Any]) -> float:
    roi = float(r.get('roi_pct') or 0)
    deployed = float(r.get('aggregate_deployed_usd') or 0)
    equity = float(r.get('aggregate_equity_usd') or 0)
    rug = float(r.get('rug_rate_pct') or 0) / 100.0
    migration = float(r.get('migration_rate_pct') or 0) / 100.0
    repeat = float(r.get('repeat_launcher_pct') or 0) / 100.0
    survival = float(r.get('survival_rate_pct') or 0) / 100.0
    farm_conc = float(r.get('farm_concentration_pct') or 0) / 100.0
    self_funding = float(r.get('self_funding_dominance_pct') or 0) / 100.0
    roi_norm = _clamp((roi + 100.0) / 300.0)
    retention = _clamp((equity / deployed) / 2.0) if deployed else 0.0
    score = 0.25*roi_norm + 0.18*retention + 0.15*migration + 0.12*repeat + 0.12*survival - 0.10*rug - 0.05*farm_conc - 0.03*self_funding
    return round(_clamp(score) * 100.0, 2)


def _create_tables(conn: sqlite3.Connection) -> None:
    conn.executescript('''
    CREATE TABLE IF NOT EXISTS creator_profitability (
      creator_address TEXT PRIMARY KEY,
      total_tokens_launched INTEGER NOT NULL DEFAULT 0,
      total_deployed_usd REAL NOT NULL DEFAULT 0,
      total_realised_usd REAL NOT NULL DEFAULT 0,
      total_unrealised_usd REAL NOT NULL DEFAULT 0,
      portfolio_equity_usd REAL NOT NULL DEFAULT 0,
      portfolio_pnl_usd REAL NOT NULL DEFAULT 0,
      roi_pct REAL NOT NULL DEFAULT 0,
      win_rate_pct REAL NOT NULL DEFAULT 0,
      rug_rate_pct REAL NOT NULL DEFAULT 0,
      migration_rate_pct REAL NOT NULL DEFAULT 0,
      avg_peak_multiple REAL,
      avg_hold_duration_sec REAL,
      best_token_mint TEXT,
      best_token_pnl_usd REAL,
      worst_token_mint TEXT,
      worst_token_pnl_usd REAL,
      profitable_tokens INTEGER NOT NULL DEFAULT 0,
      survival_rate_pct REAL NOT NULL DEFAULT 0,
      self_funding_percentage REAL NOT NULL DEFAULT 0,
      farm_risk_score REAL NOT NULL DEFAULT 0,
      network_quality_score REAL,
      funder_quality_score REAL,
      creator_quality_score REAL NOT NULL DEFAULT 0,
      strategy_breakdown_json TEXT NOT NULL DEFAULT '{}',
      refreshed_at INTEGER NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_creator_profitability_roi ON creator_profitability(roi_pct DESC);
    CREATE INDEX IF NOT EXISTS idx_creator_profitability_score ON creator_profitability(creator_quality_score DESC);

    CREATE TABLE IF NOT EXISTS network_profitability (
      network_name TEXT PRIMARY KEY,
      total_creators INTEGER NOT NULL DEFAULT 0,
      total_tokens INTEGER NOT NULL DEFAULT 0,
      aggregate_deployed_usd REAL NOT NULL DEFAULT 0,
      aggregate_realised_usd REAL NOT NULL DEFAULT 0,
      aggregate_unrealised_usd REAL NOT NULL DEFAULT 0,
      aggregate_equity_usd REAL NOT NULL DEFAULT 0,
      aggregate_pnl_usd REAL NOT NULL DEFAULT 0,
      roi_pct REAL NOT NULL DEFAULT 0,
      median_creator_roi_pct REAL,
      rug_rate_pct REAL NOT NULL DEFAULT 0,
      migration_rate_pct REAL NOT NULL DEFAULT 0,
      repeat_launcher_pct REAL NOT NULL DEFAULT 0,
      coordinator_wallet_count INTEGER NOT NULL DEFAULT 0,
      survival_rate_pct REAL NOT NULL DEFAULT 0,
      farm_concentration_pct REAL NOT NULL DEFAULT 0,
      self_funding_dominance_pct REAL NOT NULL DEFAULT 0,
      network_quality_score REAL NOT NULL DEFAULT 0,
      refreshed_at INTEGER NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_network_profitability_roi ON network_profitability(roi_pct DESC);
    CREATE INDEX IF NOT EXISTS idx_network_profitability_score ON network_profitability(network_quality_score DESC);

    CREATE TABLE IF NOT EXISTS funder_profitability (
      funder_wallet TEXT PRIMARY KEY,
      creators_funded INTEGER NOT NULL DEFAULT 0,
      total_funded_sol REAL NOT NULL DEFAULT 0,
      aggregate_creator_roi_pct REAL NOT NULL DEFAULT 0,
      median_creator_roi_pct REAL,
      creator_survival_pct REAL NOT NULL DEFAULT 0,
      profitable_creator_pct REAL NOT NULL DEFAULT 0,
      top_performing_creator TEXT,
      top_performing_creator_roi_pct REAL,
      repeat_rug_pct REAL NOT NULL DEFAULT 0,
      funder_quality_score REAL NOT NULL DEFAULT 0,
      refreshed_at INTEGER NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_funder_profitability_score ON funder_profitability(funder_quality_score DESC);

    CREATE TABLE IF NOT EXISTS cluster_profitability (
      cluster_id INTEGER PRIMARY KEY,
      cluster_roi_pct REAL NOT NULL DEFAULT 0,
      creator_roi_distribution_json TEXT NOT NULL DEFAULT '[]',
      rug_rate_pct REAL NOT NULL DEFAULT 0,
      repeat_launch_pct REAL NOT NULL DEFAULT 0,
      token_count INTEGER NOT NULL DEFAULT 0,
      profitable_token_pct REAL NOT NULL DEFAULT 0,
      network_concentration_pct REAL NOT NULL DEFAULT 0,
      refreshed_at INTEGER NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_cluster_profitability_roi ON cluster_profitability(cluster_roi_pct DESC);
    ''')


def _token_strategy_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute('''
      WITH sells AS (
        SELECT simulation_id, strategy, SUM(fraction) fraction_sold, SUM(realised_usd) realised_usd
        FROM trade_simulation_sells GROUP BY simulation_id, strategy
      ), base AS (
        SELECT ts.id simulation_id, ts.mint, ta.earliest_tx_creator creator_address, ts.status,
               COALESCE(json_extract(ts.entry_quote_json, '$.raw.swapUsdValue'), 0) entry_usd,
               COALESCE(ts.entry_market_cap, 0) entry_mc,
               COALESCE(ta.market_cap_current, 0) current_mc,
               COALESCE(ta.market_cap_highest, 0) peak_mc,
               ts.opened_at, ts.closed_at,
               CASE WHEN ta.migration_tx IS NOT NULL OR ta.migrated_at IS NOT NULL THEN 1 ELSE 0 END migrated,
               CASE WHEN ta.rug_indicator IS NOT NULL AND ta.rug_indicator <> '' THEN 1 ELSE 0 END rugged,
               CASE WHEN COALESCE(ta.market_cap_current, 0) >= ? THEN 1 ELSE 0 END survived
        FROM trade_simulations ts JOIN token_analysis ta ON ta.mint = ts.mint
        WHERE ta.earliest_tx_creator IS NOT NULL
      )
      SELECT b.*, st.strategy, COALESCE(s.fraction_sold,0) fraction_sold, COALESCE(s.realised_usd,0) realised_usd
      FROM base b CROSS JOIN (SELECT ? strategy UNION ALL SELECT ? UNION ALL SELECT ? UNION ALL SELECT ? UNION ALL SELECT ? UNION ALL SELECT ? UNION ALL SELECT ? UNION ALL SELECT ? UNION ALL SELECT ? UNION ALL SELECT ?) st
      LEFT JOIN sells s ON s.simulation_id=b.simulation_id AND s.strategy=st.strategy
      WHERE b.entry_usd > 0 AND b.entry_mc > 0
    ''', (MIN_LIVE_MARKET_CAP, *STRATEGIES)).fetchall()


class CreatorProfitabilityAnalyzer:
    def __init__(self, db_path: str): self.db_path = db_path
    def run(self) -> dict[str, int]:
        now = int(time.time())
        conn = sqlite3.connect(self.db_path); conn.row_factory = sqlite3.Row
        sync_infra_wallets(conn); _create_tables(conn)
        rows = _token_strategy_rows(conn)
        by_creator: dict[str, dict[str, Any]] = defaultdict(lambda: {'tokens': {}, 'strategies': defaultdict(lambda: {'deployed':0.0,'realised':0.0,'unrealised':0.0})})
        for r in rows:
            creator = r['creator_address']; strat = r['strategy']; entry = float(r['entry_usd']); frac = float(r['fraction_sold']); realised = float(r['realised_usd'])
            unsold = max(0.0, 1.0-frac)
            unreal = entry * unsold * (float(r['current_mc'])/float(r['entry_mc'])) if r['status']=='OPEN' and unsold else 0.0
            s = by_creator[creator]['strategies'][strat]; s['deployed'] += entry; s['realised'] += realised; s['unrealised'] += unreal
            if strat == PRIMARY_STRATEGY:
                by_creator[creator]['tokens'][r['mint']] = {
                  'entry': entry, 'realised': realised, 'unrealised': unreal, 'equity': realised+unreal,
                  'pnl': realised+unreal-entry, 'migrated': int(r['migrated']), 'rugged': int(r['rugged']), 'survived': int(r['survived']),
                  'peak_multiple': (float(r['peak_mc'])/float(r['entry_mc'])) if r['peak_mc'] else None,
                  'hold': max(0, int((r['closed_at'] or now) - r['opened_at'])) if r['opened_at'] else None,
                }
        sf = {r['creator_address']: float(r['self_funding_percentage'] or 0) for r in conn.execute('SELECT creator_address,self_funding_percentage FROM creator_self_funding')}
        farm = {r['wallet_address']: float(r['farm_risk_score'] or 0) for r in conn.execute('''SELECT fcm.wallet_address, MAX(fc.farm_risk_score) farm_risk_score FROM farm_cluster_members fcm JOIN farm_clusters fc ON fc.cluster_id=fcm.cluster_id WHERE fcm.wallet_role='creator' GROUP BY fcm.wallet_address''')}
        conn.execute('DELETE FROM creator_profitability')
        for creator, agg in by_creator.items():
            toks = list(agg['tokens'].items()); vals = [v for _,v in toks]
            if not vals: continue
            deployed=sum(v['entry'] for v in vals); realised=sum(v['realised'] for v in vals); unreal=sum(v['unrealised'] for v in vals); equity=realised+unreal; pnl=equity-deployed
            roip=(pnl/deployed*100) if deployed else 0.0
            best=max(toks,key=lambda kv:kv[1]['pnl']); worst=min(toks,key=lambda kv:kv[1]['pnl'])
            strategy_json={k:{**v,'equity':v['realised']+v['unrealised'],'pnl':v['realised']+v['unrealised']-v['deployed'],'roi_pct':((v['realised']+v['unrealised']-v['deployed'])/v['deployed']*100 if v['deployed'] else 0)} for k,v in agg['strategies'].items()}
            row={
              'roi_pct':roip,'total_deployed_usd':deployed,'portfolio_equity_usd':equity,'migration_rate_pct':100*sum(v['migrated'] for v in vals)/len(vals),
              'profitable_tokens':sum(v['pnl']>0 for v in vals),'rug_rate_pct':100*sum(v['rugged'] for v in vals)/len(vals),
              'survival_rate_pct':100*sum(v['survived'] for v in vals)/len(vals),'self_funding_percentage':sf.get(creator,0),'farm_risk_score':farm.get(creator,0)
            }
            score=_qscore_creator(row)
            conn.execute('''INSERT INTO creator_profitability VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(
              creator,len(vals),deployed,realised,unreal,equity,pnl,roip,100*sum(v['pnl']>0 for v in vals)/len(vals),row['rug_rate_pct'],row['migration_rate_pct'],
              statistics.mean([v['peak_multiple'] for v in vals if v['peak_multiple'] is not None]) if any(v['peak_multiple'] is not None for v in vals) else None,
              statistics.mean([v['hold'] for v in vals if v['hold'] is not None]) if any(v['hold'] is not None for v in vals) else None,
              best[0],best[1]['pnl'],worst[0],worst[1]['pnl'],row['profitable_tokens'],row['survival_rate_pct'],sf.get(creator,0),farm.get(creator,0),None,None,score,json.dumps(strategy_json,sort_keys=True),now
            ))
        conn.commit(); n=conn.execute('SELECT COUNT(*) FROM creator_profitability').fetchone()[0]; conn.close(); return {'creators_written': n}


class NetworkProfitabilityAnalyzer:
    def __init__(self, db_path: str): self.db_path=db_path
    def run(self)->dict[str,int]:
        now=int(time.time()); conn=sqlite3.connect(self.db_path); conn.row_factory=sqlite3.Row; sync_infra_wallets(conn); _create_tables(conn)
        conn.execute('DELETE FROM network_profitability')
        networks=conn.execute('''SELECT nm.network_name, cp.* FROM network_membership nm JOIN creator_profitability cp ON cp.creator_address=nm.creator_address JOIN networks_release nr ON nr.network_name=nm.network_name WHERE COALESCE(nr.has_cex_funder,0)=0 AND COALESCE(nr.has_infra_funder,0)=0''').fetchall()
        grouped=defaultdict(list)
        for r in networks: grouped[r['network_name']].append(dict(r))
        for name, rows in grouped.items():
            creators=len(rows); toks=sum(r['total_tokens_launched'] for r in rows); dep=sum(r['total_deployed_usd'] for r in rows); rea=sum(r['total_realised_usd'] for r in rows); unr=sum(r['total_unrealised_usd'] for r in rows); eq=rea+unr; pnl=eq-dep; roi=pnl/dep*100 if dep else 0
            creators_set=[r['creator_address'] for r in rows]
            ph=','.join('?'*len(creators_set))
            coord=conn.execute(f'''SELECT COUNT(DISTINCT wc.funder_wallet) FROM wallet_clusters wc JOIN creator_funders cf ON cf.funder_address=wc.funder_wallet WHERE cf.creator_address IN ({ph}) AND cf.is_cex=0 AND wc.funder_wallet NOT IN (SELECT address FROM infra_wallets)''', creators_set).fetchone()[0]
            cluster_counts=[x[0] for x in conn.execute(f'''SELECT COUNT(*) FROM farm_cluster_members WHERE wallet_role='creator' AND wallet_address IN ({ph}) GROUP BY cluster_id''', creators_set)]
            farm_conc=(max(cluster_counts)/creators*100) if cluster_counts else 0
            sf_dom=100*sum((r['self_funding_percentage'] or 0)>=50 for r in rows)/creators
            d={'roi_pct':roi,'aggregate_deployed_usd':dep,'aggregate_equity_usd':eq,'rug_rate_pct':statistics.mean(r['rug_rate_pct'] for r in rows),'migration_rate_pct':statistics.mean(r['migration_rate_pct'] for r in rows),'repeat_launcher_pct':100*sum(r['total_tokens_launched']>=2 for r in rows)/creators,'survival_rate_pct':statistics.mean(r['survival_rate_pct'] for r in rows),'farm_concentration_pct':farm_conc,'self_funding_dominance_pct':sf_dom}
            score=_qscore_network(d)
            conn.execute('''INSERT INTO network_profitability VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(name,creators,toks,dep,rea,unr,eq,pnl,roi,_median([r['roi_pct'] for r in rows]),d['rug_rate_pct'],d['migration_rate_pct'],d['repeat_launcher_pct'],coord,d['survival_rate_pct'],farm_conc,sf_dom,score,now))
        conn.execute('''UPDATE creator_profitability SET network_quality_score=(SELECT AVG(np.network_quality_score) FROM network_membership nm JOIN network_profitability np ON np.network_name=nm.network_name WHERE nm.creator_address=creator_profitability.creator_address)''')
        conn.commit(); n=conn.execute('SELECT COUNT(*) FROM network_profitability').fetchone()[0]; conn.close(); return {'networks_written':n}


class FunderProfitabilityAnalyzer:
    def __init__(self, db_path: str): self.db_path=db_path
    def run(self)->dict[str,int]:
        now=int(time.time()); conn=sqlite3.connect(self.db_path); conn.row_factory=sqlite3.Row; sync_infra_wallets(conn); _create_tables(conn)
        conn.execute('DELETE FROM funder_profitability')
        rows=conn.execute('''SELECT cf.funder_address, cf.amount_sol, cp.* FROM creator_funders cf JOIN creator_profitability cp ON cp.creator_address=cf.creator_address WHERE cf.is_cex=0 AND cf.funder_address NOT IN (SELECT address FROM infra_wallets)''').fetchall()
        grouped=defaultdict(list)
        for r in rows: grouped[r['funder_address']].append(dict(r))
        for wallet, rs in grouped.items():
            uniq={r['creator_address']:r for r in rs}; vals=list(uniq.values()); creators=len(vals)
            dep=sum(r['total_deployed_usd'] for r in vals); pnl=sum(r['portfolio_pnl_usd'] for r in vals); agg_roi=pnl/dep*100 if dep else 0
            surv=statistics.mean(r['survival_rate_pct'] for r in vals); prof=100*sum(r['roi_pct']>0 for r in vals)/creators; repeat_rug=100*sum(r['total_tokens_launched']>=2 and r['rug_rate_pct']>=50 for r in vals)/creators
            top=max(vals,key=lambda r:r['roi_pct']); quality=round(_clamp(0.35*_clamp((agg_roi+100)/300)+0.25*(surv/100)+0.25*(prof/100)-0.15*(repeat_rug/100))*100,2)
            conn.execute('''INSERT INTO funder_profitability VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',(wallet,creators,sum(float(r['amount_sol'] or 0) for r in rs),agg_roi,_median([r['roi_pct'] for r in vals]),surv,prof,top['creator_address'],top['roi_pct'],repeat_rug,quality,now))
        conn.execute('''UPDATE creator_profitability SET funder_quality_score=(SELECT AVG(fp.funder_quality_score) FROM creator_funders cf JOIN funder_profitability fp ON fp.funder_wallet=cf.funder_address WHERE cf.creator_address=creator_profitability.creator_address AND cf.is_cex=0)''')
        # final creator score now that network/funder quality are populated
        for r in conn.execute('SELECT * FROM creator_profitability').fetchall():
            conn.execute('UPDATE creator_profitability SET creator_quality_score=? WHERE creator_address=?', (_qscore_creator(dict(r)), r['creator_address']))
        conn.commit(); n=conn.execute('SELECT COUNT(*) FROM funder_profitability').fetchone()[0]; conn.close(); return {'funders_written':n}


class ClusterProfitabilityAnalyzer:
    def __init__(self, db_path: str): self.db_path=db_path
    def run(self)->dict[str,int]:
        now=int(time.time()); conn=sqlite3.connect(self.db_path); conn.row_factory=sqlite3.Row; _create_tables(conn)
        conn.execute('DELETE FROM cluster_profitability')
        clusters=conn.execute('''SELECT fcm.cluster_id, cp.* FROM farm_cluster_members fcm JOIN creator_profitability cp ON cp.creator_address=fcm.wallet_address WHERE fcm.wallet_role='creator' ''').fetchall()
        grouped=defaultdict(list)
        for r in clusters: grouped[r['cluster_id']].append(dict(r))
        for cid, rows in grouped.items():
            dep=sum(r['total_deployed_usd'] for r in rows); pnl=sum(r['portfolio_pnl_usd'] for r in rows); roi=pnl/dep*100 if dep else 0; toks=sum(r['total_tokens_launched'] for r in rows)
            creators=[r['creator_address'] for r in rows]; ph=','.join('?'*len(creators)); net_counts=[x[0] for x in conn.execute(f'''SELECT COUNT(*) FROM network_membership WHERE creator_address IN ({ph}) GROUP BY network_name''', creators)]
            conn.execute('''INSERT INTO cluster_profitability VALUES (?,?,?,?,?,?,?,?,?)''',(cid,roi,json.dumps(sorted([r['roi_pct'] for r in rows])),statistics.mean(r['rug_rate_pct'] for r in rows),100*sum(r['total_tokens_launched']>=2 for r in rows)/len(rows),toks,100*sum(r['profitable_tokens'] for r in rows)/toks if toks else 0,(max(net_counts)/sum(net_counts)*100) if net_counts else 0,now))
        conn.commit(); n=conn.execute('SELECT COUNT(*) FROM cluster_profitability').fetchone()[0]; conn.close(); return {'clusters_written':n}
