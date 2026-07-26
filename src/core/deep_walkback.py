"""Deep, time-anchored walkback evidence and shadow treasury discovery.

This module never confirms a treasury or reroots attribution. It stores raw
candidate edges, atomic WSOL semantics, lifecycle observations, and explainable
review candidates using deterministic evidence keys.
"""
from __future__ import annotations

import hashlib
import json
import statistics
import time
from dataclasses import dataclass, asdict
from typing import Callable, Iterable, Optional

PATH_STATES = frozenset({
    "QUEUED", "CLAIMED", "CREATE_ANCHORED", "CREATOR_FUNDING_RECOVERED",
    "SUBPROVIDER_RECOVERED", "UPSTREAM_EXPANDING", "KNOWN_TREASURY_REACHED",
    "TREASURY_CANDIDATE_SURFACED", "STOPPED_AT_RESERVOIR",
    "STOPPED_AT_PROVISIONING_HUB", "NO_ATTRIBUTION_FOUND", "ARCHIVAL_GAP",
    "RPC_BUDGET_EXHAUSTED", "FAILED_RETRYABLE", "FAILED_TERMINAL", "COMPLETE",
    "WAITING_FOR_CREATE_ANCHOR", "ARCHIVAL_CREATE_UNAVAILABLE",
})

ROLES = frozenset({
    "COMMON_CAPITAL_SOURCE", "ROTATIONAL_TREASURY", "OPERATIONAL_TREASURY",
    "MIXED_CAPITAL_RESERVOIR", "UPPER_PROVISIONING_HUB", "PROVISIONING_HUB",
    "RELAY", "SINGLE_USE_SUBPROVIDER", "CREATOR", "TEMPORARY_WSOL_ACCOUNT",
    "SERVICE_OR_EXCHANGE", "UNKNOWN_INFRASTRUCTURE", "UNKNOWN",
})

SCHEMA = """
CREATE TABLE IF NOT EXISTS wt_walkback_edge_candidates (
 evidence_key TEXT PRIMARY KEY, mint TEXT NOT NULL, wallet TEXT NOT NULL,
 candidate_parent TEXT NOT NULL, signature TEXT NOT NULL, block_time INTEGER,
 amount_lamports INTEGER, mechanism TEXT NOT NULL, instruction_index INTEGER NOT NULL DEFAULT -1,
 inner_instruction_index INTEGER NOT NULL DEFAULT -1, pre_balance INTEGER, post_balance INTEGER,
 net_balance_change INTEGER, anchor_signature TEXT, anchor_block_time INTEGER,
 hop_depth INTEGER NOT NULL, owner TEXT, close_authority TEXT, close_destination TEXT,
 temporary_account TEXT, evidence_strength TEXT NOT NULL, selection_status TEXT NOT NULL,
 rejection_reason TEXT, first_observed_at INTEGER NOT NULL, last_observed_at INTEGER NOT NULL,
 observation_count INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS ix_wwec_mint_hop ON wt_walkback_edge_candidates(mint, hop_depth);
CREATE INDEX IF NOT EXISTS ix_wwec_parent ON wt_walkback_edge_candidates(candidate_parent);

CREATE TABLE IF NOT EXISTS wt_walkback_atomic_flows (
 evidence_key TEXT PRIMARY KEY, mint TEXT, signature TEXT NOT NULL,
 source_wallet TEXT, owner TEXT, temporary_account TEXT NOT NULL, authority TEXT,
 close_destination TEXT, transfer_lamports INTEGER, net_destination_lamports INTEGER,
 has_create INTEGER NOT NULL, has_sync_native INTEGER NOT NULL, has_close INTEGER NOT NULL,
 instruction_order_json TEXT NOT NULL, causal_interpretation TEXT NOT NULL,
 block_time INTEGER, first_observed_at INTEGER NOT NULL, last_observed_at INTEGER NOT NULL,
 observation_count INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS ix_wwaf_signature ON wt_walkback_atomic_flows(signature);

CREATE TABLE IF NOT EXISTS wt_wallet_lifecycle_evidence (
 wallet TEXT PRIMARY KEY, earliest_recoverable_signature TEXT,
 earliest_recoverable_block_time INTEGER, first_inbound_signature TEXT,
 first_inbound_block_time INTEGER, first_outbound_signature TEXT,
 first_outbound_block_time INTEGER, last_activity_block_time INTEGER,
 pre_launch_tx_count INTEGER, total_observed_tx_count INTEGER,
 distinct_creators_funded INTEGER NOT NULL DEFAULT 0,
 distinct_launches_reached INTEGER NOT NULL DEFAULT 0,
 distinct_subproviders_funded INTEGER NOT NULL DEFAULT 0,
 distinct_hubs_funded INTEGER NOT NULL DEFAULT 0,
 lifecycle_quality TEXT NOT NULL DEFAULT 'UNKNOWN', updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS wt_infrastructure_candidates (
 wallet TEXT PRIMARY KEY, candidate_role TEXT NOT NULL, confidence TEXT NOT NULL,
 first_seen_at INTEGER, last_seen_at INTEGER, wallet_birth_at INTEGER,
 lifecycle_quality TEXT NOT NULL, distinct_launches INTEGER NOT NULL DEFAULT 0,
 distinct_creators INTEGER NOT NULL DEFAULT 0, distinct_subproviders INTEGER NOT NULL DEFAULT 0,
 distinct_hubs INTEGER NOT NULL DEFAULT 0, distinct_treasury_branches INTEGER NOT NULL DEFAULT 0,
 funding_epoch_count INTEGER NOT NULL DEFAULT 0, total_sol_distributed REAL NOT NULL DEFAULT 0,
 max_single_transfer_sol REAL NOT NULL DEFAULT 0, median_transfer_sol REAL NOT NULL DEFAULT 0,
 account_close_descendant_count INTEGER NOT NULL DEFAULT 0,
 rapid_migration_descendant_count INTEGER NOT NULL DEFAULT 0,
 competing_inbound_source_count INTEGER NOT NULL DEFAULT 0,
 known_common_capital_links INTEGER NOT NULL DEFAULT 0,
 service_exchange_risk REAL NOT NULL DEFAULT 0, evidence_score REAL NOT NULL DEFAULT 0,
 counterevidence_score REAL NOT NULL DEFAULT 0, role_score_treasury REAL NOT NULL DEFAULT 0,
 role_score_reservoir REAL NOT NULL DEFAULT 0, role_score_hub REAL NOT NULL DEFAULT 0,
 role_score_relay REAL NOT NULL DEFAULT 0, positive_evidence_json TEXT NOT NULL,
 negative_evidence_json TEXT NOT NULL, uncertainties_json TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'SHADOW', review_state TEXT NOT NULL DEFAULT 'PENDING_REVIEW',
 created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS wt_infrastructure_candidate_descendants (
 wallet TEXT NOT NULL, mint TEXT NOT NULL, creator TEXT, subprovider TEXT,
 path_evidence_key TEXT NOT NULL, first_seen_at INTEGER NOT NULL,
 PRIMARY KEY(wallet, mint, path_evidence_key)
);
CREATE TABLE IF NOT EXISTS wt_infrastructure_candidate_evidence (
 wallet TEXT NOT NULL, evidence_key TEXT NOT NULL, evidence_type TEXT NOT NULL,
 evidence_json TEXT NOT NULL, observed_at INTEGER NOT NULL,
 PRIMARY KEY(wallet, evidence_key)
);
CREATE TABLE IF NOT EXISTS wt_infrastructure_candidate_reviews (
 candidate_wallet TEXT PRIMARY KEY, proposed_role TEXT NOT NULL, confidence TEXT NOT NULL,
 review_status TEXT NOT NULL DEFAULT 'PENDING_REVIEW', reviewed_by TEXT, reviewed_at INTEGER,
 review_notes TEXT, evidence_snapshot_json TEXT NOT NULL, created_at INTEGER NOT NULL,
 updated_at INTEGER NOT NULL
);
"""


def ensure_schema(conn) -> None:
    conn.executescript(SCHEMA)
    columns = {r[1] for r in conn.execute("PRAGMA table_info(wt_walkback_queue)")}
    additions = {
        "claimed_by": "TEXT", "claimed_at": "INTEGER", "lease_expires_at": "INTEGER",
        "next_retry_at": "INTEGER", "path_state": "TEXT",
        "termination_reason_json": "TEXT", "create_anchor_signature": "TEXT",
        "create_anchor_slot": "INTEGER", "create_anchor_block_time": "INTEGER",
        "create_anchor_source": "TEXT", "create_anchor_audit_state": "TEXT",
    }
    for name, sql_type in additions.items():
        if name not in columns:
            conn.execute(f"ALTER TABLE wt_walkback_queue ADD COLUMN {name} {sql_type}")
    conn.commit()


def valid_signature(signature: Optional[str]) -> bool:
    return bool(signature and 80 <= len(signature) <= 90 and signature.isalnum())


def evidence_key(*parts: object) -> str:
    return hashlib.sha256("\x1f".join("" if p is None else str(p) for p in parts).encode()).hexdigest()


def _key(account) -> str:
    return account.get("pubkey", "") if isinstance(account, dict) else str(account)


def _instruction_groups(tx: dict):
    yield -1, tx.get("transaction", {}).get("message", {}).get("instructions") or []
    for group in (tx.get("meta") or {}).get("innerInstructions") or []:
        yield int(group.get("index", -1)), group.get("instructions") or []


@dataclass(frozen=True)
class AtomicFlow:
    signature: str
    source_wallet: str
    owner: str
    temporary_account: str
    authority: str
    close_destination: str
    transfer_lamports: int
    net_destination_lamports: int
    has_create: bool
    has_sync_native: bool
    has_close: bool
    instruction_order: tuple[str, ...]
    block_time: Optional[int]


def materialize_atomic_wsol(tx: dict, signature: str = "") -> list[AtomicFlow]:
    """Return one event per temporary account; never emit the account as a hop."""
    events: dict[str, dict] = {}
    order: list[str] = []
    for outer, instructions in _instruction_groups(tx):
        for index, ix in enumerate(instructions):
            parsed = ix.get("parsed") if isinstance(ix, dict) else None
            if not isinstance(parsed, dict): continue
            kind = parsed.get("type") or ""
            info = parsed.get("info") or {}; order.append(kind)
            account = info.get("account") or info.get("newAccount") or info.get("destination")
            if kind in ("createAccount", "createAccountWithSeed") and account:
                e = events.setdefault(account, {}); e["has_create"] = True
                e["source_wallet"] = info.get("source") or info.get("base") or e.get("source_wallet", "")
                e["owner"] = info.get("owner") or e.get("owner", "")
            elif kind in ("initializeAccount", "initializeAccount2", "initializeAccount3") and account:
                events.setdefault(account, {})["owner"] = info.get("owner") or ""
            elif kind in ("transfer", "transferChecked"):
                destination = info.get("destination")
                if destination:
                    e = events.setdefault(destination, {}); amount = info.get("lamports")
                    if amount is None:
                        amount = (info.get("tokenAmount") or {}).get("amount")
                    try: e["transfer_lamports"] = e.get("transfer_lamports", 0) + int(amount or 0)
                    except (TypeError, ValueError): pass
                    e["source_wallet"] = info.get("source") or e.get("source_wallet", "")
            elif kind == "syncNative" and account:
                events.setdefault(account, {})["has_sync_native"] = True
            elif kind == "closeAccount" and account:
                e = events.setdefault(account, {}); e["has_close"] = True
                e["close_destination"] = info.get("destination") or ""
                e["authority"] = info.get("authority") or info.get("owner") or ""
    keys = [_key(a) for a in tx.get("transaction", {}).get("message", {}).get("accountKeys") or []]
    pre = (tx.get("meta") or {}).get("preBalances") or []; post = (tx.get("meta") or {}).get("postBalances") or []
    flows=[]
    for account,e in events.items():
        if not e.get("has_close") or not (e.get("has_sync_native") or e.get("transfer_lamports")): continue
        destination=e.get("close_destination", ""); net=0
        if destination in keys:
            i=keys.index(destination)
            if i < len(pre) and i < len(post): net=post[i]-pre[i]
        owner=e.get("owner") or e.get("authority") or ""
        flows.append(AtomicFlow(signature, e.get("source_wallet", ""), owner, account,
            e.get("authority", ""), destination, int(e.get("transfer_lamports", 0)), net,
            bool(e.get("has_create")), bool(e.get("has_sync_native")), True,
            tuple(order), tx.get("blockTime")))
    return flows


def persist_atomic_flows(conn, mint: str, flows: Iterable[AtomicFlow]) -> int:
    now=int(time.time()); count=0
    for flow in flows:
        key=evidence_key(flow.signature, flow.temporary_account, flow.owner, flow.close_destination)
        conn.execute("""INSERT INTO wt_walkback_atomic_flows
          (evidence_key,mint,signature,source_wallet,owner,temporary_account,authority,
           close_destination,transfer_lamports,net_destination_lamports,has_create,
           has_sync_native,has_close,instruction_order_json,causal_interpretation,
           block_time,first_observed_at,last_observed_at,observation_count)
          VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)
          ON CONFLICT(evidence_key) DO UPDATE SET last_observed_at=excluded.last_observed_at,
            observation_count=wt_walkback_atomic_flows.observation_count+1""",
          (key,mint,flow.signature,flow.source_wallet,flow.owner,flow.temporary_account,
           flow.authority,flow.close_destination,flow.transfer_lamports,
           flow.net_destination_lamports,int(flow.has_create),int(flow.has_sync_native),
           int(flow.has_close),json.dumps(flow.instruction_order),
           "SOURCE_FUNDS_TEMPORARY_WSOL_OWNED_BY_OWNER_CLOSES_TO_DESTINATION",
           flow.block_time,now,now)); count += 1
    return count


def claim_with_lease(conn, mint: str, worker_id: str, lease_seconds: int = 300) -> bool:
    ensure_schema(conn); now=int(time.time())
    cur=conn.execute("""UPDATE wt_walkback_queue SET status='running',path_state='CLAIMED',
      claimed_by=?,claimed_at=?,lease_expires_at=?,started_at=COALESCE(started_at,?),
      updated_at=?,attempts=attempts+1
      WHERE mint=? AND attempts < 100 AND (status='pending' OR
        (status='running' AND COALESCE(lease_expires_at,0) < ?))
      AND COALESCE(next_retry_at,0) <= ?""",
      (worker_id,now,now+lease_seconds,now,now,mint,now,now))
    conn.commit(); return cur.rowcount == 1


def set_path_state(conn, mint: str, state: str, reason: Optional[dict] = None) -> None:
    if state not in PATH_STATES: raise ValueError(f"invalid path state: {state}")
    conn.execute("UPDATE wt_walkback_queue SET path_state=?,termination_reason_json=?,updated_at=? WHERE mint=?",
                 (state,json.dumps(reason or {},sort_keys=True),int(time.time()),mint)); conn.commit()


def persist_edge_candidate(conn, *, mint: str, wallet: str, parent: str,
                           signature: str, block_time: Optional[int], amount_lamports: Optional[int],
                           mechanism: str, anchor_signature: Optional[str], anchor_block_time: Optional[int],
                           hop_depth: int, selection_status: str, rejection_reason: str = "",
                           instruction_index: int = -1, inner_instruction_index: int = -1,
                           pre_balance: Optional[int] = None, post_balance: Optional[int] = None,
                           owner: str = "", close_authority: str = "", close_destination: str = "",
                           temporary_account: str = "", evidence_strength: str = "TRANSACTION_DERIVED") -> str:
    now=int(time.time()); key=evidence_key(signature,instruction_index,inner_instruction_index,parent,wallet,mechanism)
    conn.execute("""INSERT INTO wt_walkback_edge_candidates
      (evidence_key,mint,wallet,candidate_parent,signature,block_time,amount_lamports,
       mechanism,instruction_index,inner_instruction_index,pre_balance,post_balance,
       net_balance_change,anchor_signature,anchor_block_time,hop_depth,owner,
       close_authority,close_destination,temporary_account,evidence_strength,
       selection_status,rejection_reason,first_observed_at,last_observed_at,observation_count)
      VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)
      ON CONFLICT(evidence_key) DO UPDATE SET last_observed_at=excluded.last_observed_at,
       observation_count=wt_walkback_edge_candidates.observation_count+1,
       selection_status=excluded.selection_status,rejection_reason=excluded.rejection_reason""",
      (key,mint,wallet,parent,signature,block_time,amount_lamports,mechanism,
       instruction_index,inner_instruction_index,pre_balance,post_balance,
       (post_balance-pre_balance) if pre_balance is not None and post_balance is not None else None,
       anchor_signature,anchor_block_time,hop_depth,owner,close_authority,
       close_destination,temporary_account,evidence_strength,selection_status,
       rejection_reason,now,now)); return key


def score_candidate(conn, wallet: str, *, service_or_exchange: bool = False) -> dict:
    rows=conn.execute("SELECT * FROM wt_walkback_edge_candidates WHERE candidate_parent=?",(wallet,)).fetchall()
    launches={r["mint"] for r in rows}; children={r["wallet"] for r in rows}
    amounts=[(r["amount_lamports"] or 0)/1e9 for r in rows]; closes=sum("CLOSE" in (r["mechanism"] or "") for r in rows)
    positive=[]; negative=[]; uncertain=[]; treasury=0; reservoir=0; hub=0; relay=0
    if len(launches)>=2: treasury+=25; positive.append(f"{len(launches)} distinct launch descendants")
    if len(children)>=2: treasury+=20; hub+=25; positive.append(f"{len(children)} distinct downstream wallets")
    if amounts and max(amounts)>=100: treasury+=20; positive.append(f"high-value provisioning max {max(amounts):.6f} SOL")
    if closes>=2: treasury+=15; positive.append(f"{closes} account-close descendant edges")
    parent_sources={r["candidate_parent"] for r in conn.execute("SELECT candidate_parent FROM wt_walkback_edge_candidates WHERE wallet=?",(wallet,))}
    if len(parent_sources)>1: reservoir+=30; negative.append("multiple inbound capital sources; reservoir explanation strengthened")
    if len(launches)==1: treasury-=15; negative.append("one-off ancestry only")
    if service_or_exchange: treasury-=100; negative.append("known service or exchange")
    if not rows: uncertain.append("no transaction-derived descendant evidence")
    score=max(0,treasury); role="SERVICE_OR_EXCHANGE" if service_or_exchange else (
        "MIXED_CAPITAL_RESERVOIR" if reservoir>treasury else "PROVISIONING_HUB" if hub>treasury else
        "OPERATIONAL_TREASURY" if treasury>=40 else "UNKNOWN_INFRASTRUCTURE")
    review="REJECTED_SERVICE" if service_or_exchange else "HIGH_REVIEW" if score>=60 else "MEDIUM_REVIEW" if score>=35 else "LOW_REVIEW" if rows else "INSUFFICIENT"
    return {"wallet":wallet,"candidate_role":role,"confidence":review,"total_score":score,
        "role_score_treasury":treasury,"role_score_reservoir":reservoir,"role_score_hub":hub,
        "role_score_relay":relay,"positive_evidence":positive,"negative_evidence":negative,
        "uncertainties":uncertain,"distinct_launches":len(launches),"distinct_children":len(children),
        "amounts":amounts,"account_close_descendants":closes,"competing_inbound_sources":len(parent_sources)}


def materialize_candidate(conn, wallet: str, *, service_or_exchange: bool = False) -> dict:
    ensure_schema(conn); s=score_candidate(conn,wallet,service_or_exchange=service_or_exchange); now=int(time.time())
    amounts=s.pop("amounts"); positive=s.pop("positive_evidence"); negative=s.pop("negative_evidence"); uncertainties=s.pop("uncertainties")
    conn.execute("""INSERT INTO wt_infrastructure_candidates
      (wallet,candidate_role,confidence,lifecycle_quality,distinct_launches,
       distinct_creators,distinct_subproviders,distinct_hubs,funding_epoch_count,
       total_sol_distributed,max_single_transfer_sol,median_transfer_sol,
       account_close_descendant_count,competing_inbound_source_count,
       service_exchange_risk,evidence_score,counterevidence_score,role_score_treasury,
       role_score_reservoir,role_score_hub,role_score_relay,positive_evidence_json,
       negative_evidence_json,uncertainties_json,status,review_state,created_at,updated_at)
      VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
      ON CONFLICT(wallet) DO UPDATE SET candidate_role=excluded.candidate_role,
       confidence=excluded.confidence,distinct_launches=excluded.distinct_launches,
       distinct_subproviders=excluded.distinct_subproviders,total_sol_distributed=excluded.total_sol_distributed,
       max_single_transfer_sol=excluded.max_single_transfer_sol,median_transfer_sol=excluded.median_transfer_sol,
       account_close_descendant_count=excluded.account_close_descendant_count,
       competing_inbound_source_count=excluded.competing_inbound_source_count,
       service_exchange_risk=excluded.service_exchange_risk,evidence_score=excluded.evidence_score,
       counterevidence_score=excluded.counterevidence_score,role_score_treasury=excluded.role_score_treasury,
       role_score_reservoir=excluded.role_score_reservoir,role_score_hub=excluded.role_score_hub,
       role_score_relay=excluded.role_score_relay,positive_evidence_json=excluded.positive_evidence_json,
       negative_evidence_json=excluded.negative_evidence_json,uncertainties_json=excluded.uncertainties_json,
       updated_at=excluded.updated_at""",
      (wallet,s["candidate_role"],s["confidence"],"UNKNOWN",s["distinct_launches"],0,
       s["distinct_children"],0,0,sum(amounts),max(amounts,default=0),statistics.median(amounts) if amounts else 0,
       s["account_close_descendants"],s["competing_inbound_sources"],1.0 if service_or_exchange else 0,
       s["total_score"],max(0,-s["role_score_treasury"]),s["role_score_treasury"],
       s["role_score_reservoir"],s["role_score_hub"],s["role_score_relay"],json.dumps(positive),
       json.dumps(negative),json.dumps(uncertainties),"SHADOW","PENDING_REVIEW",now,now))
    snapshot={**s,"positive_evidence":positive,"negative_evidence":negative,"uncertainties":uncertainties}
    conn.execute("""INSERT INTO wt_infrastructure_candidate_reviews
      (candidate_wallet,proposed_role,confidence,review_status,evidence_snapshot_json,created_at,updated_at)
      VALUES (?,?,?,'PENDING_REVIEW',?,?,?) ON CONFLICT(candidate_wallet) DO UPDATE SET
      proposed_role=excluded.proposed_role,confidence=excluded.confidence,
      evidence_snapshot_json=excluded.evidence_snapshot_json,updated_at=excluded.updated_at""",
      (wallet,s["candidate_role"],s["confidence"],json.dumps(snapshot,sort_keys=True),now,now)); conn.commit(); return snapshot
