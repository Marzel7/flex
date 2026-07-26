#!/usr/bin/env python3
"""X49.1 non-mutating anchored walkback replay.

Production SQLite databases are opened read-only. Checkpoints, RPC cache, and
CSV reports are written only beneath --output-dir. No attribution or registry
writer is imported or called.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import signal
import sqlite3
import threading
import time
import urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in os.sys.path:
    os.sys.path.insert(0, str(REPO_ROOT))

from src.core import walkback_worker as worker
from src.utils.infra_mapping import is_known_account

SOURCE = "X49_1_SHADOW_REPLAY"
FINAL_CLASSES = (
    "CONFIRMED_WATCHTOWER", "HIGH_PRIORITY_WATCHTOWER_CANDIDATE",
    "WT_ACCOUNT_CLOSE_LINEAGE_PENDING", "GENERIC_ACCOUNT_CLOSE",
    "NON_WATCHTOWER", "INSUFFICIENT_EVIDENCE", "UNEVALUABLE_ARCHIVAL_GAP",
)
TERMINALS = {
    "KNOWN_WATCHTOWER_TREASURY", "KNOWN_NON_WATCHTOWER_ENTITY",
    "NEW_TREASURY_CANDIDATE", "UPSTREAM_INFRASTRUCTURE_CANDIDATE",
    "EXCHANGE_OR_SERVICE_STOP", "AMM_STOP", "AMBIGUOUS_UPSTREAM",
    "NO_INBOUND_FOUND", "HISTORY_EXHAUSTED", "RPC_ARCHIVAL_GAP",
    "DEPTH_LIMIT", "CREATE_ANCHOR_MISSING", "CREATOR_FUNDING_NOT_FOUND",
    "TRANSACTION_UNAVAILABLE", "PERMANENT_PARSE_FAILURE", "RETRYABLE_RPC_FAILURE",
}


def ro(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{Path(path).resolve()}?mode=ro", uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def file_state(path: str) -> tuple[int, int, str]:
    stat = os.stat(path)
    with open(path, "rb") as handle:
        head = handle.read(1024 * 1024)
    return stat.st_size, stat.st_mtime_ns, hashlib.sha256(head).hexdigest()


class ShadowRpc:
    def __init__(self, url: str, db_path: Path, *, rate: float, budget: int,
                 retries: int, dry_run: bool = False):
        self.url, self.rate, self.budget = url, max(rate, 0.1), budget
        self.retries, self.dry_run = retries, dry_run
        self.lock = threading.Lock()
        self.last_call = 0.0
        self.calls = self.hits = self.misses = self.retry_count = 0
        self.local = threading.local()
        self.cancelled = False
        self.db = sqlite3.connect(db_path, check_same_thread=False, timeout=30)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("CREATE TABLE IF NOT EXISTS rpc_cache "
                        "(cache_key TEXT PRIMARY KEY,response_json TEXT,cached_at INTEGER)")
        self.db.commit()

    @staticmethod
    def key(method: str, params: list) -> str:
        raw = json.dumps([method, params], sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode()).hexdigest()

    def call(self, method: str, params: list) -> Optional[object]:
        if not hasattr(self.local, "stats"):
            self.local.stats = Counter()
        key = self.key(method, params)
        with self.lock:
            row = self.db.execute("SELECT response_json FROM rpc_cache WHERE cache_key=?", (key,)).fetchone()
            if row:
                self.hits += 1
                self.local.stats["cache_hits"] += 1
                return json.loads(row[0])
            self.misses += 1
            self.local.stats["cache_misses"] += 1
            if self.dry_run or self.cancelled or self.calls >= self.budget:
                return None
            wait = (1.0 / self.rate) - (time.monotonic() - self.last_call)
            if wait > 0:
                time.sleep(wait)
            self.last_call = time.monotonic()
            self.calls += 1
            self.local.stats["rpc_calls"] += 1

        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
        for attempt in range(self.retries + 1):
            try:
                req = urllib.request.Request(self.url, data=body,
                                             headers={"Content-Type": "application/json",
                                                      "User-Agent": "x49.1-shadow-replay/1.0"})
                payload = json.loads(urllib.request.urlopen(req, timeout=20).read())
                result = payload.get("result")
                with self.lock:
                    self.db.execute("INSERT OR REPLACE INTO rpc_cache VALUES (?,?,?)",
                                    (key, json.dumps(result), int(time.time())))
                    self.db.commit()
                return result
            except Exception:
                if attempt >= self.retries:
                    return None
                with self.lock:
                    self.retry_count += 1
                    self.local.stats["retries"] += 1
                time.sleep(min(8, 2 ** attempt))
        return None

    def reset_thread_stats(self) -> None:
        self.local.stats = Counter()

    def thread_stats(self) -> Counter:
        return Counter(getattr(self.local, "stats", {}))


def one(conn: sqlite3.Connection, sql: str, args: tuple = ()) -> dict:
    row = conn.execute(sql, args).fetchone()
    return dict(row) if row else {}


def recover_inputs(population_csv: str, ops_path: str, live_path: str) -> list[dict]:
    ops, live = ro(ops_path), ro(live_path)
    population = list(csv.DictReader(open(population_csv, newline="")))
    seen, rows = set(), []
    for ordinal, source_row in enumerate(population, 1):
        mint = source_row["mint"]
        duplicate = mint in seen
        seen.add(mint)
        ta = one(live, "SELECT * FROM token_analysis WHERE mint=?", (mint,))
        queue = one(ops, "SELECT * FROM wt_walkback_queue WHERE mint=?", (mint,))
        cfq = one(live, "SELECT * FROM creator_funding_queue WHERE mint=? ORDER BY updated_at DESC LIMIT 1", (mint,))
        launch = one(ops, "SELECT * FROM wt_watchtower_launches WHERE mint=?", (mint,))
        birth = one(ops, "SELECT * FROM wt_creator_birth_launch WHERE token_mint=?", (mint,))
        creator = (source_row.get("creator") or queue.get("creator") or ta.get("pf_ws_creator")
                   or ta.get("earliest_tx_creator") or launch.get("creator_wallet") or birth.get("creator"))
        anchors = []
        for signature, source, confidence in (
            (ta.get("create_tx_signature"), "token_analysis.create_tx_signature", "HIGH"),
            (cfq.get("create_tx_signature"), "creator_funding_queue.create_tx_signature", "HIGH"),
            (launch.get("create_signature"), "wt_watchtower_launches.create_signature", "HIGH"),
            (birth.get("launch_sig"), "wt_creator_birth_launch.launch_sig", "HIGH"),
        ):
            if signature and signature not in [x[0] for x in anchors]:
                anchors.append((signature, source, confidence))
        conflict = len(anchors) > 1
        anchor = anchors[0] if len(anchors) == 1 else (None, None, None)
        if duplicate:
            state, reason = "DUPLICATE_POPULATION_ROW", "duplicate token in frozen input"
        elif not creator:
            state, reason = "MISSING_CREATOR", "no creator in authoritative local sources"
        elif conflict:
            state, reason = "INVALID_CREATE_ANCHOR", "CREATE_ANCHOR_CONFLICT"
        elif not anchor[0]:
            state, reason = "MISSING_CREATE_ANCHOR", "no retained CREATE signature"
        else:
            state, reason = "READY", ""
        rows.append({
            "source": SOURCE, "ordinal": ordinal, "token": mint, "creator": creator or "",
            "create_transaction": anchor[0] or "", "create_evidence_source": anchor[1] or "",
            "create_confidence": anchor[2] or "", "create_conflict": int(conflict),
            "create_candidates": json.dumps(anchors), "create_slot": launch.get("create_slot") or "",
            "create_time": launch.get("create_time") or ta.get("created_at") or "",
            "migration_transaction": ta.get("migration_tx") or "",
            "migration_delay": source_row.get("migration_delay_seconds") or "",
            "existing_walkback_row": int(bool(queue)),
            "legacy_funding_mechanism": queue.get("funding_mechanism") or "",
            "existing_immediate_funder": queue.get("funder_wallet") or "",
            "replay_state": state, "replay_eligibility": int(state == "READY"),
            "reason_ineligible": reason,
        })
    ops.close(); live.close()
    return rows


def recover_anchor_rpc(item: dict, rpc: ShadowRpc) -> None:
    if item["replay_state"] != "MISSING_CREATE_ANCHOR" or not item["creator"]:
        return
    sigs = rpc.call("getSignaturesForAddress", [item["creator"], {"limit": 100, "commitment": "confirmed"}]) or []
    mint = item["token"]
    matches = []
    for entry in sigs[:30]:
        sig = entry.get("signature")
        if not sig:
            continue
        tx = rpc.call("getTransaction", [sig, {"encoding": "jsonParsed",
                                               "maxSupportedTransactionVersion": 0,
                                               "commitment": "confirmed"}])
        if tx and mint in json.dumps(tx):
            logs = (tx.get("meta") or {}).get("logMessages") or []
            if any("Instruction: Create" in log for log in logs):
                matches.append((sig, tx.get("slot"), tx.get("blockTime")))
    if len(matches) == 1:
        sig, slot, block_time = matches[0]
        item.update(create_transaction=sig, create_evidence_source="RPC_SIGNATURE_HISTORY",
                    create_confidence="MEDIUM", create_slot=slot or "",
                    create_time=block_time or "", replay_state="READY",
                    replay_eligibility=1, reason_ineligible="")
    elif len(matches) > 1:
        item.update(create_conflict=1, create_candidates=json.dumps(matches),
                    replay_state="INVALID_CREATE_ANCHOR", replay_eligibility=0,
                    reason_ineligible="CREATE_ANCHOR_CONFLICT")


def known_treasuries(ops_path: str) -> set[str]:
    conn = ro(ops_path)
    values = {row[0] for row in conn.execute("SELECT treasury FROM wt_confirmed_treasuries")}
    conn.close()
    return values


def mechanism_evidence(tx: Optional[dict], creator: str, legacy: str = "") -> tuple[str, str, str]:
    if tx is None:
        return ("TRANSACTION_UNAVAILABLE", "UNKNOWN", "")
    destination = worker._close_account_destination(tx)
    if destination:
        if destination == creator:
            return ("ACCOUNT_CLOSE_PROVEN", "ACCOUNT_CLOSE_PROVEN", destination)
        return ("ACCOUNT_CLOSE_DESTINATION_MISMATCH", "ACCOUNT_CLOSE_PROVEN", destination)
    detected = worker._detect_mechanism(tx, "", creator)
    if detected in ("WSOL_WRAP_CLOSE", "SEEDED_ACCOUNT_CLOSE") or legacy in (
            "WSOL_WRAP_CLOSE", "SEEDED_ACCOUNT_CLOSE"):
        return ("ACCOUNT_CLOSE_LABEL_ONLY", "ACCOUNT_CLOSE_LABEL_ONLY", "")
    if detected == "PLAIN_XFER":
        return ("PLAIN_TRANSFER", "PLAIN_TRANSFER", "")
    return ("OTHER_MECHANISM", "OTHER", "")


def replay_one(item: dict, rpc: ShadowRpc, treasuries: set[str], max_depth: int,
               per_launch_budget: int) -> tuple[dict, list[dict], dict]:
    start = time.monotonic(); rpc.reset_thread_stats(); counter = [0]
    hops, terminal, mechanism, close_status, close_destination = [], "", "UNKNOWN", "UNKNOWN", ""
    if item["replay_state"] != "READY":
        terminal = "CREATE_ANCHOR_MISSING" if item["replay_state"] == "MISSING_CREATE_ANCHOR" else "PERMANENT_PARSE_FAILURE"
        final = "UNEVALUABLE_ARCHIVAL_GAP" if terminal == "CREATE_ANCHOR_MISSING" else "INSUFFICIENT_EVIDENCE"
    else:
        wallet, anchor = item["creator"], item["create_transaction"]
        first_mech = ""
        for depth in range(1, max_depth + 1):
            if counter[0] >= per_launch_budget:
                terminal = "RETRYABLE_RPC_FAILURE"; break
            funder = worker._find_funder_via_rpc(
                wallet, counter, None, before_signature=anchor,
                prefer_oldest=depth > 1)
            source, sig, slot, block_time, amount, detected = funder
            if not source:
                terminal = "NO_INBOUND_FOUND" if counter[0] else "RPC_ARCHIVAL_GAP"
                break
            tx = worker._get_tx(sig) if sig else None
            counter[0] += int(bool(sig))
            if depth == 1:
                first_mech = detected or ""
                close_status, mechanism, close_destination = mechanism_evidence(
                    tx, item["creator"], item["legacy_funding_mechanism"])
            entity = "KNOWN_WT_TREASURY" if source in treasuries else (
                "KNOWN_SERVICE" if is_known_account(source) else "UNKNOWN")
            hops.append({
                "source": SOURCE, "token": item["token"], "hop_number": depth,
                "source_wallet": source, "destination_wallet": wallet,
                "signature": sig or "", "amount": amount if amount is not None else "",
                "block_time": block_time or "", "slot": slot or "",
                "role_classification": "TREASURY" if entity == "KNOWN_WT_TREASURY" else (
                    "SERVICE" if entity == "KNOWN_SERVICE" else "UNKNOWN_INFRASTRUCTURE"),
                "known_entity_status": entity, "confidence": "HIGH" if sig else "LOW",
                "selection_basis": "closest_pre_create" if depth == 1 else "oldest_bounded_capital_edge",
                "rejected_alternatives": "[]", "ambiguity_status": "UNASSESSED",
            })
            if source in treasuries:
                terminal = "KNOWN_WATCHTOWER_TREASURY"; break
            if is_known_account(source):
                terminal = "EXCHANGE_OR_SERVICE_STOP"; break
            wallet, anchor = source, sig
        else:
            terminal = "DEPTH_LIMIT"
        proven = close_status == "ACCOUNT_CLOSE_PROVEN"
        if terminal == "KNOWN_WATCHTOWER_TREASURY" and proven:
            final = "CONFIRMED_WATCHTOWER"
        elif proven and terminal in ("DEPTH_LIMIT", "HISTORY_EXHAUSTED") and len(hops) >= 2:
            final = "HIGH_PRIORITY_WATCHTOWER_CANDIDATE"
            terminal = "UPSTREAM_INFRASTRUCTURE_CANDIDATE"
        elif proven and terminal not in ("KNOWN_NON_WATCHTOWER_ENTITY", "EXCHANGE_OR_SERVICE_STOP", "AMM_STOP"):
            final = "WT_ACCOUNT_CLOSE_LINEAGE_PENDING"
        elif proven:
            final = "GENERIC_ACCOUNT_CLOSE"
        elif mechanism == "PLAIN_TRANSFER" and terminal in ("KNOWN_NON_WATCHTOWER_ENTITY", "EXCHANGE_OR_SERVICE_STOP"):
            final = "NON_WATCHTOWER"
        elif terminal in ("RPC_ARCHIVAL_GAP", "TRANSACTION_UNAVAILABLE"):
            final = "UNEVALUABLE_ARCHIVAL_GAP"
        else:
            final = "INSUFFICIENT_EVIDENCE"
    if terminal not in TERMINALS:
        terminal = "PERMANENT_PARSE_FAILURE"
    result = {
        "source": SOURCE, "ordinal": item["ordinal"], "token": item["token"],
        "creator": item["creator"], "create_transaction": item["create_transaction"],
        "mechanism_dimension": mechanism, "mechanism_evidence": close_status,
        "close_destination": close_destination, "close_destination_matches_creator": int(bool(close_destination and close_destination == item["creator"])),
        "immediate_funder": hops[0]["source_wallet"] if hops else "",
        "known_treasury": next((h["source_wallet"] for h in hops if h["known_entity_status"] == "KNOWN_WT_TREASURY"), ""),
        "highest_upstream": hops[-1]["source_wallet"] if hops else "",
        "hop_count": len(hops), "treasury_dimension": (
            "KNOWN_WT_TREASURY_REACHED" if terminal == "KNOWN_WATCHTOWER_TREASURY" else
            "PARTIAL_UPSTREAM" if hops else "NO_UPSTREAM_LINEAGE"),
        "final_classification": final, "terminal_reason": terminal,
        "migration_delay": item["migration_delay"],
        "existing_x46_classification": "", "elapsed_seconds": round(time.monotonic() - start, 3),
    }
    stats = rpc.thread_stats()
    usage = {"source": SOURCE, "token": item["token"], "rpc_calls": stats.get("rpc_calls", 0),
             "cache_hits": stats.get("cache_hits", 0), "cache_misses": stats.get("cache_misses", 0),
             "retries": stats.get("retries", 0), "unavailable_signatures": int(not hops and item["replay_state"] == "READY"),
             "elapsed_seconds": result["elapsed_seconds"]}
    return result, hops, usage


def ground_truth_inputs(ops_path: str, population_inputs: list[dict]) -> list[dict]:
    conn = ro(ops_path)
    rows = [dict(row) for row in conn.execute(
        "SELECT DISTINCT l.* FROM wt_watchtower_launches l "
        "JOIN operator_entities e ON e.entity_address=l.treasury_wallet "
        "JOIN operators o ON o.operator_id=e.operator_id WHERE o.status='CONFIRMED' "
        "ORDER BY l.mint")]
    conn.close()
    population_map = {row["token"]: row for row in population_inputs}
    output = []
    for index, row in enumerate(rows, 1):
        signature = row.get("create_signature") or ""
        output.append({
            "source": SOURCE, "ordinal": 10000 + index, "token": row["mint"],
            "creator": row.get("creator_wallet") or "", "create_transaction": signature,
            "create_evidence_source": "wt_watchtower_launches.create_signature" if signature else "",
            "create_confidence": row.get("confidence") or "", "create_conflict": 0,
            "create_candidates": json.dumps([(signature, "wt_watchtower_launches", "HIGH")]) if signature else "[]",
            "create_slot": row.get("create_slot") or "", "create_time": row.get("create_time") or "",
            "migration_transaction": "", "migration_delay": row.get("create_to_migration_secs") or "",
            "existing_walkback_row": 0, "legacy_funding_mechanism": row.get("funding_mechanism") or "",
            "existing_immediate_funder": row.get("subprov_wallet") or "",
            "replay_state": "READY" if signature and row.get("creator_wallet") else "MISSING_CREATE_ANCHOR",
            "replay_eligibility": int(bool(signature and row.get("creator_wallet"))),
            "reason_ineligible": "" if signature else "no retained CREATE signature",
        })
    target = "2GU9TB56hem9mYVV6N2o6A5TtsyV5w8R8DHiXZ11pump"
    if target in population_map:
        output.append(dict(population_map[target], ordinal=10999))
    return output


def write_csv(path: Path, rows: list[dict], fields: Optional[list[str]] = None) -> None:
    if not rows and not fields:
        path.write_text("")
        return
    fields = fields or list(rows[0])
    with open(path, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader(); writer.writerows(rows)


def normalize_record(record: dict) -> None:
    """Conservatively reject cycles and unsupported treasury elevation."""
    result, hops = record["result"], record["hops"]
    seen = {result["creator"]}
    ambiguous = False
    for hop in hops:
        source, destination = hop["source_wallet"], hop["destination_wallet"]
        if source == destination or source in seen:
            hop["ambiguity_status"] = "CYCLE_DETECTED"
            ambiguous = True
        seen.add(source)
    if ambiguous:
        result["terminal_reason"] = "AMBIGUOUS_UPSTREAM"
        result["treasury_dimension"] = "PARTIAL_UPSTREAM"
        result["known_treasury"] = ""
        result["final_classification"] = (
            "WT_ACCOUNT_CLOSE_LINEAGE_PENDING"
            if result["mechanism_evidence"] == "ACCOUNT_CLOSE_PROVEN"
            else "INSUFFICIENT_EVIDENCE")
    elif result["final_classification"] == "HIGH_PRIORITY_WATCHTOWER_CANDIDATE":
        # Depth-limit plus an unassessed oldest edge is infrastructure evidence,
        # not enough behavioural/path evidence for treasury-level elevation.
        result["final_classification"] = "WT_ACCOUNT_CLOSE_LINEAGE_PENDING"
        result["terminal_reason"] = "UPSTREAM_INFRASTRUCTURE_CANDIDATE"
    elif result["terminal_reason"] == "RETRYABLE_RPC_FAILURE":
        result["final_classification"] = "UNEVALUABLE_ARCHIVAL_GAP"
        result["treasury_dimension"] = "UNEVALUABLE"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--population", default="/private/tmp/x46_watchtower_24h_audit.csv")
    parser.add_argument("--ops-db", default="database/wt_ops_v2.db")
    parser.add_argument("--live-db", default="database/flex_complete_database.db")
    parser.add_argument("--output-dir", default="/private/tmp/x49_1_shadow_replay")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--rate", type=float, default=15)
    parser.add_argument("--per-launch-budget", type=int, default=30)
    parser.add_argument("--global-budget", type=int, default=16000)
    parser.add_argument("--max-depth", type=int, default=4)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    out = Path(args.output_dir); out.mkdir(parents=True, exist_ok=True)
    inputs = recover_inputs(args.population, args.ops_db, args.live_db)
    if len(inputs) != 658 or len({x["token"] for x in inputs}) != 658:
        raise SystemExit("Frozen population integrity failure")
    rpc = ShadowRpc(os.environ.get("HELIUS_RPC_URL", worker.RPC_URL), out/"x49_1_shadow.db",
                    rate=args.rate, budget=args.global_budget, retries=args.retries,
                    dry_run=args.dry_run)
    worker._rpc = rpc.call
    stop = lambda *_: setattr(rpc, "cancelled", True)
    signal.signal(signal.SIGINT, stop); signal.signal(signal.SIGTERM, stop)
    treasuries = known_treasuries(args.ops_db)
    target_token = "2GU9TB56hem9mYVV6N2o6A5TtsyV5w8R8DHiXZ11pump"
    target_item = next(item for item in inputs if item["token"] == target_token)
    target_result, target_hops, target_usage = replay_one(
        target_item, rpc, treasuries, args.max_depth, args.per_launch_budget)
    if not args.dry_run and target_result["final_classification"] != "CONFIRMED_WATCHTOWER":
        (out/"x49_1_ground_truth_regression.json").write_text(json.dumps({
            "source": SOURCE, "result": target_result, "hops": target_hops,
            "usage": target_usage}, indent=2) + "\n")
        raise SystemExit("Ground-truth regression: mandatory Dch token was not recovered")
    if args.preflight_only:
        payload = {"source": SOURCE, "result": target_result, "hops": target_hops,
                   "usage": target_usage}
        (out/"x49_1_ground_truth_preflight.json").write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n")
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    for item in inputs:
        recover_anchor_rpc(item, rpc)
    write_csv(out/"x49_1_population_inputs.csv", inputs)
    selected = sorted(inputs, key=lambda x: int(x["ordinal"]))[:args.limit]
    checkpoint_path = out/"x49_1_checkpoint.jsonl"
    completed = {}
    if checkpoint_path.exists():
        for line in checkpoint_path.read_text().splitlines():
            record = json.loads(line); completed[record["result"]["token"]] = record
    if target_token in {item["token"] for item in selected} and target_token not in completed:
        completed[target_token] = {"result": target_result, "hops": target_hops,
                                   "usage": target_usage}
    futures, records = {}, list(completed.values())
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        for item in selected:
            if item["token"] not in completed:
                futures[pool.submit(replay_one, item, rpc, treasuries, args.max_depth,
                                    args.per_launch_budget)] = item
        for number, future in enumerate(as_completed(futures), 1):
            result, hops, usage = future.result()
            record = {"result": result, "hops": hops, "usage": usage}
            records.append(record)
            with open(checkpoint_path, "a") as handle:
                handle.write(json.dumps(record, sort_keys=True) + "\n")
            if number % 25 == 0:
                print(f"[{SOURCE}] completed={len(completed)+number}/{len(selected)} rpc={rpc.calls} cache_hits={rpc.hits}", flush=True)
            if rpc.cancelled:
                break
    records.sort(key=lambda x: int(x["result"]["ordinal"]))
    for record in records:
        normalize_record(record)
    results = [x["result"] for x in records]; hops = [h for x in records for h in x["hops"]]; usage = [x["usage"] for x in records]
    result_map = {row["token"]: row for row in results}
    x46 = {x["mint"]: x for x in csv.DictReader(open(args.population))}
    for row in results:
        row["existing_x46_classification"] = x46[row["token"]].get("audit_conclusion", "")
    write_csv(out/"x49_1_walkback_paths.csv", hops, ["source","token","hop_number","source_wallet","destination_wallet","signature","amount","block_time","slot","role_classification","known_entity_status","confidence","selection_basis","rejected_alternatives","ambiguity_status"])
    write_csv(out/"x49_1_population_results.csv", results)
    write_csv(out/"x49_1_rpc_usage.csv", usage)
    unresolved = [r for r in results if r["final_classification"] in ("INSUFFICIENT_EVIDENCE","UNEVALUABLE_ARCHIVAL_GAP")]
    write_csv(out/"x49_1_unresolved_evidence.csv", unresolved, list(results[0]) if results else None)
    by_upstream = defaultdict(list)
    for r in results:
        if r["final_classification"] == "HIGH_PRIORITY_WATCHTOWER_CANDIDATE":
            by_upstream[r["highest_upstream"]].append(r)
    treasury_candidates = [{"source":SOURCE,"wallet":w,"associated_launches":len(rs),"tokens":json.dumps([r['token'] for r in rs]),"status":"PENDING_HUMAN_REVIEW","recommended_action":"REVIEW_NEW_TREASURY"} for w,rs in by_upstream.items()]
    write_csv(out/"x49_1_treasury_candidates.csv", treasury_candidates, ["source","wallet","associated_launches","tokens","status","recommended_action"])
    infra = [{"source":SOURCE,"wallet":r["highest_upstream"],"token":r["token"],"hop_count":r["hop_count"],"terminal_reason":r["terminal_reason"],"recommended_action":"REVIEW_INFRASTRUCTURE_ROLE"} for r in results if r["final_classification"] == "WT_ACCOUNT_CLOSE_LINEAGE_PENDING" and r["highest_upstream"]]
    write_csv(out/"x49_1_infrastructure_candidates.csv", infra, ["source","wallet","token","hop_count","terminal_reason","recommended_action"])
    # Replay all 43 canonical/attested positives separately from population counts.
    gt_items = ground_truth_inputs(args.ops_db, inputs)
    gt_records = []
    for item in gt_items:
        if item["token"] == target_token:
            r, hs, u = target_result, target_hops, target_usage
        else:
            r, hs, u = replay_one(item, rpc, treasuries, args.max_depth,
                                  args.per_launch_budget)
        gt_records.append((item, r, hs, u))
    gt_rows=[]
    for item,r,hs,u in gt_records:
        gt_record = {"result": r, "hops": hs, "usage": u}
        normalize_record(gt_record)
        gt_rows.append({"source":SOURCE,"token":item["token"],"in_frozen_population":int(item["token"] in x46),"replayable":int(item["replay_state"]=="READY"),"create_anchor_recovered":int(bool(item["create_transaction"])),"creator_funding_recovered":int(bool(hs)),"account_close_destination_proven":int(r["mechanism_evidence"]=="ACCOUNT_CLOSE_PROVEN"),"immediate_funder_recovered":int(bool(r["immediate_funder"])),"complete_upstream_path_recovered":int(r["terminal_reason"]=="KNOWN_WATCHTOWER_TREASURY"),"confirmed_treasury_reached":r["known_treasury"],"final_classification":r["final_classification"],"terminal_reason":r["terminal_reason"]})
    write_csv(out/"x49_1_ground_truth_validation.csv",gt_rows)
    controls=set()
    for path,col,val in (("/private/tmp/x46_watchtower_24h_audit.csv","audit_conclusion","Unrelated"),("/private/tmp/x47_2_operational_signature_matrix.csv","signature_classification","Operational Signature Match")):
        if Path(path).exists():
            for row in csv.DictReader(open(path)):
                if row.get(col)==val:controls.add(row["mint"])
    controls.update({
        "EeEAiL7g3YQxb1P85CBLbfsYtPHTUcQw46ZpFDEMpump",
        "JAWowRFZjrBKr1AMoa44Yn3Du7oQ1Nm2zYokfqnvpump",
        "4SYtZoBnsCgybwYkbiX6frfCBksR1qTb6VMNowbhpump",
        "Cx917pXMVoQHsU2TgzoZKWBkbaD2ReqWHLdDuwbkpump",
        "EZXrZeeqcDKgf8vGEFJSq3oiqSTdUjLEFUJLdpLmpump",
        "A8xGqaiiB4Xs4sXnHoVmQ58vyrCtsB7YvcXfYSULpump",
    })
    control_rows=[]
    for token in sorted(controls):
        r=result_map.get(token,{})
        control_rows.append({"source":SOURCE,"token":token,"replayed":int(bool(r)),"final_classification":r.get("final_classification","NOT_IN_FROZEN_POPULATION"),"terminal_reason":r.get("terminal_reason","NOT_IN_FROZEN_POPULATION"),"false_positive":int(r.get("final_classification") in ("CONFIRMED_WATCHTOWER","HIGH_PRIORITY_WATCHTOWER_CANDIDATE"))})
    write_csv(out/"x49_1_false_positive_controls.csv",control_rows)
    review_pack = []
    for r in results:
        if r["final_classification"] not in (
                "CONFIRMED_WATCHTOWER", "HIGH_PRIORITY_WATCHTOWER_CANDIDATE",
                "WT_ACCOUNT_CLOSE_LINEAGE_PENDING", "GENERIC_ACCOUNT_CLOSE"):
            continue
        review_pack.append({
            "source": SOURCE, "token": r["token"], "creator": r["creator"],
            "funding_mechanism": r["mechanism_dimension"],
            "funding_transaction": next((h["signature"] for h in hops
                                          if h["token"] == r["token"] and h["hop_number"] == 1), ""),
            "immediate_funder": r["immediate_funder"],
            "highest_upstream": r["highest_upstream"],
            "known_treasury": r["known_treasury"],
            "hop_count": r["hop_count"], "terminal_reason": r["terminal_reason"],
            "classification": r["final_classification"],
            "migration_delay": r["migration_delay"],
            "existing_x46_classification": r["existing_x46_classification"],
            "recommended_action": "REVIEW_ONLY_NO_AUTOMATIC_PROMOTION",
        })
    write_csv(out/"x49_1_review_pack.csv", review_pack)
    comparisons = []
    comparison_specs = (
        ("X46_STRONG", "/private/tmp/x46_watchtower_24h_audit.csv", "audit_conclusion", "Strong candidate"),
        ("X46_WEAK", "/private/tmp/x46_watchtower_24h_audit.csv", "audit_conclusion", "Weak candidate"),
        ("X46_UNKNOWN", "/private/tmp/x46_watchtower_24h_audit.csv", "audit_conclusion", "Unknown"),
        ("X47_2_OPERATIONAL_MATCH", "/private/tmp/x47_2_operational_signature_matrix.csv", "signature_classification", "Operational Signature Match"),
        ("X48_1_SIGNATURE_MATCH", "/private/tmp/x48_1_population_matrix.csv", "classification", "WATCHTOWER Signature Match"),
        ("X48_1_NEAR_MATCH", "/private/tmp/x48_1_population_matrix.csv", "classification", "Near Match"),
    )
    for cohort, path, column, expected in comparison_specs:
        tokens = set()
        if Path(path).exists():
            for row in csv.DictReader(open(path)):
                if row.get(column) == expected:
                    tokens.add(row.get("mint") or row.get("token"))
        for token in sorted(tokens):
            r = result_map.get(token, {})
            comparisons.append({"source": SOURCE, "cohort": cohort, "token": token,
                                "x49_1_classification": r.get("final_classification", "NOT_REPLAYED"),
                                "x49_1_terminal_reason": r.get("terminal_reason", "NOT_REPLAYED")})
    for item in inputs:
        if item["legacy_funding_mechanism"] == "WSOL_WRAP_CLOSE":
            r = result_map[item["token"]]
            comparisons.append({"source": SOURCE, "cohort": "LEGACY_WSOL_WRAP_CLOSE",
                                "token": item["token"],
                                "x49_1_classification": r["final_classification"],
                                "x49_1_terminal_reason": r["terminal_reason"]})
    write_csv(out/"x49_1_comparisons.csv", comparisons,
              ["source", "cohort", "token", "x49_1_classification", "x49_1_terminal_reason"])
    gt_complete = sum(r[1]["final_classification"] == "CONFIRMED_WATCHTOWER" for r in gt_records)
    gt_replayable = sum(r[0]["replay_state"] == "READY" for r in gt_records)
    false_positives = sum(row["false_positive"] for row in control_rows)
    cached_responses = rpc.db.execute("SELECT COUNT(*) FROM rpc_cache").fetchone()[0]
    summary={"source":SOURCE,"frozen_launches":658,"inputs":dict(Counter(x["replay_state"] for x in inputs)),"anchors":{"original_usable":536,"recovered":sum(x["create_evidence_source"]=="RPC_SIGNATURE_HISTORY" for x in inputs),"still_missing":sum(x["replay_state"]=="MISSING_CREATE_ANCHOR" for x in inputs),"conflicted":sum(x["replay_state"]=="INVALID_CREATE_ANCHOR" for x in inputs)},"outcomes":dict(Counter(r["final_classification"] for r in results)),"terminals":dict(Counter(r["terminal_reason"] for r in results)),"mechanisms":dict(Counter(r["mechanism_evidence"] for r in results)),"lineage":dict(Counter(r["treasury_dimension"] for r in results)),"treasury_discovery":{"new_treasury_candidates":len(treasury_candidates),"infrastructure_candidate_rows":len(infra),"unique_infrastructure_wallets":len({r['wallet'] for r in infra})},"ground_truth":{"total":len(gt_records),"replayable":gt_replayable,"fully_recovered":gt_complete,"partially_or_not_recovered":len(gt_records)-gt_complete,"classifications":dict(Counter(r[1]["final_classification"] for r in gt_records))},"controls":{"total":len(control_rows),"false_positives":false_positives,"specificity_percent":round(100*(len(control_rows)-false_positives)/len(control_rows),2) if control_rows else None},"rpc":{"acquisition_network_calls":cached_responses,"current_cached_pass_calls":rpc.calls,"cache_hits_current_pass":rpc.hits,"retries_current_pass":rpc.retry_count,"budget":args.global_budget,"rows_stopped_by_budget":sum(r["terminal_reason"]=="RETRYABLE_RPC_FAILURE" for r in results)},"production_connections":"SQLITE_MODE_RO_QUERY_ONLY","production_unchanged_by_replay":True}
    (out/"x49_1_summary.json").write_text(json.dumps(summary,indent=2,sort_keys=True)+"\n")
    print(json.dumps(summary,indent=2,sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
