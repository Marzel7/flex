#!/usr/bin/env python3
"""Read-only, bounded funding-transaction census for confirmed WATCHTOWER launches.

The only network method used is getTransaction for retained funding signatures.
No wallet-history discovery is performed and no operational database is mutated.
"""
from __future__ import annotations

import concurrent.futures
import hashlib
import json
import re
import sqlite3
import time
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

DB = Path("database/wt_ops_v2.db")
CACHE_DB = Path("database/flex_complete_database.db")
OPERATOR_ID = "04265d9f-6eb2-568c-a49e-9253091a4dbb"
OUT = Path("docs/audits/watchtower_funding_transaction_census.v1.json")


def _rpc_url() -> str:
    match = re.search(r'HELIUS_RPC_URL=["\']?([^"\'\n]+)', Path(".env").read_text())
    if not match:
        raise RuntimeError("HELIUS_RPC_URL unavailable")
    return match.group(1)


def _retained_population(conn: sqlite3.Connection) -> list[dict]:
    rows = [dict(r) for r in conn.execute(
        """SELECT m.mint, wl.create_time launch_time, wl.creator_wallet creator,
                  wl.treasury_wallet, wl.subprov_wallet,
                  wl.funding_mechanism mechanism, wl.wrap_close_signature signature,
                  'REGISTRY' signature_source
             FROM operator_launch_membership m
             LEFT JOIN wt_watchtower_launches wl ON wl.mint=m.mint
            WHERE m.operator_id=?""", (OPERATOR_ID,)
    )]
    missing = [r for r in rows if not r.get("signature")]
    for row in missing:
        edge = conn.execute(
            """SELECT signature, block_time, mechanism, wallet, candidate_parent,
                      owner, close_authority, close_destination, temporary_account
                 FROM wt_walkback_edge_candidates
                WHERE mint=? AND hop_depth=1 AND mechanism='WSOL_WRAP_CLOSE'
                ORDER BY block_time DESC, evidence_key DESC LIMIT 1""", (row["mint"],)
        ).fetchone()
        if edge:
            edge = dict(edge)
            row.update({
                "signature": edge["signature"], "launch_time": row.get("launch_time") or edge["block_time"],
                "mechanism": edge["mechanism"], "creator": row.get("creator") or edge["close_destination"],
                "subprov_wallet": row.get("subprov_wallet") or edge["candidate_parent"],
                "signature_source": "RETAINED_HOP1_EDGE",
            })
    return rows


def _local_cache(signatures: set[str]) -> dict[str, dict]:
    if not CACHE_DB.exists() or not signatures:
        return {}
    out: dict[str, dict] = {}
    try:
        conn = sqlite3.connect(f"file:{CACHE_DB}?mode=ro", uri=True, timeout=5)
        for sig in signatures:
            row = conn.execute(
                "SELECT response_json FROM rpc_response_cache WHERE cache_key=?",
                (f"getTransaction:{sig}",),
            ).fetchone()
            if row:
                value = json.loads(row[0])
                if value:
                    out[sig] = value.get("result", value)
        conn.close()
    except Exception:
        return out
    return out


def _rpc_get(url: str, signature: str) -> dict | None:
    payload = {"jsonrpc": "2.0", "id": 1, "method": "getTransaction",
               "params": [signature, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}]}
    request = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    response = json.loads(urllib.request.urlopen(request, timeout=45).read())
    return response.get("result")


def _instructions(tx: dict) -> list[dict]:
    message = tx.get("transaction", {}).get("message", {})
    result = []
    for position, item in enumerate(message.get("instructions") or []):
        parsed = item.get("parsed") or {}
        parsed = parsed if isinstance(parsed, dict) else {}
        result.append({"position": position, "inner": False, "program": item.get("program"),
                       "type": parsed.get("type"), "info": parsed.get("info") or {}})
    for group in tx.get("meta", {}).get("innerInstructions") or []:
        parent = group.get("index")
        for position, item in enumerate(group.get("instructions") or []):
            parsed = item.get("parsed") or {}
            parsed = parsed if isinstance(parsed, dict) else {}
            result.append({"position": parent + (position + 1) / 1000, "inner": True, "program": item.get("program"),
                           "type": parsed.get("type"), "info": parsed.get("info") or {}})
    return sorted(result, key=lambda x: x["position"])


def _keys(tx: dict) -> list[str]:
    keys = tx.get("transaction", {}).get("message", {}).get("accountKeys") or []
    return [x if isinstance(x, str) else x.get("pubkey") for x in keys]


def _decode(row: dict, tx: dict, source: str) -> dict:
    ins = _instructions(tx)
    creates = [x for x in ins if x["type"] in ("createAccount", "createAccountWithSeed")]
    transfers = [x for x in ins if x["type"] == "transfer" and "lamports" in x["info"]]
    syncs = [x for x in ins if x["type"] == "syncNative"]
    closes = [x for x in ins if x["type"] == "closeAccount"]
    temps = {x["info"].get("newAccount") for x in creates if x["info"].get("newAccount")}
    temps |= {x["info"].get("account") for x in syncs if x["info"].get("account")}
    temps |= {x["info"].get("account") for x in closes if x["info"].get("account")}
    temps.discard(None)
    fee_payer = next((x for x in _keys(tx) if x), None)
    create_sources = {x["info"].get("source") or x["info"].get("from") for x in creates}
    create_sources.discard(None)
    owners = {x["info"].get("owner") for x in closes}
    owners.discard(None)
    authorities = {x["info"].get("authority") for x in closes}
    authorities.discard(None)
    destinations = {x["info"].get("destination") for x in closes}
    destinations.discard(None)
    created = {x["info"].get("newAccount") for x in creates}
    synced = {x["info"].get("account") for x in syncs}
    closed = {x["info"].get("account") for x in closes}
    continuity = bool(temps) and bool(created & closed) and (not syncs or bool(created & synced & closed))
    sequence = [x["type"] or f"{x['program']}:unparsed" for x in ins]
    deposits = [x["info"].get("lamports") for x in creates if isinstance(x["info"].get("lamports"), int)]
    amounts = [x["info"]["lamports"] for x in transfers]
    temp_transfer_amounts = [x["info"]["lamports"] for x in transfers if x["info"].get("destination") in temps]
    close_to_creator = bool(row.get("creator") and row["creator"] in destinations)
    close_to_funder = bool((row.get("subprov_wallet") in destinations) or (row.get("treasury_wallet") in destinations))
    return {
        "mint": row["mint"], "launch_timestamp": row.get("launch_time") or tx.get("blockTime"),
        "funding_signature": row["signature"], "retained_mechanism": row.get("mechanism") or "UNKNOWN",
        "signature_source": row["signature_source"], "decode_source": source,
        "instruction_sequence": sequence, "create_methods": [x["type"] for x in creates],
        "creation_deposit_lamports": deposits, "separate_transfer_count": len(transfers),
        "ordered_transfer_lamports": amounts, "sync_native": bool(syncs), "close_account": bool(closes),
        "close_redeemed_lamports": sum(deposits) + sum(temp_transfer_amounts) if continuity and closes else None,
        "same_temporary_account_continuity": continuity,
        "fee_payer_role": "CREATE_SOURCE" if fee_payer in create_sources else "OTHER",
        "funding_source_role": "TEMP_OWNER" if create_sources & owners else "DISTINCT_FROM_TEMP_OWNER",
        "temporary_account_owner_role": "CLOSE_OWNER" if owners else "UNAVAILABLE",
        "close_authority_role": "OWNER" if authorities & owners else ("DISTINCT" if authorities else "UNAVAILABLE"),
        "close_destination_role": "CREATOR" if close_to_creator else ("RETAINED_FUNDER" if close_to_funder else ("DISTINCT_OTHER" if destinations else "UNAVAILABLE")),
        "_family_key": json.dumps({"mechanism": row.get("mechanism") or "UNKNOWN", "sequence": sequence,
            "create": [x["type"] for x in creates], "deposits": deposits, "transfers": amounts,
            "sync": bool(syncs), "close": bool(closes), "continuity": continuity,
            "source_owner": bool(create_sources & owners), "close_destination": "CREATOR" if close_to_creator else ("RETAINED_FUNDER" if close_to_funder else ("DISTINCT_OTHER" if destinations else "UNAVAILABLE"))}, sort_keys=True),
    }


def main() -> None:
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    population = _retained_population(conn)
    conn.close()
    signatures = {r["signature"] for r in population if r.get("signature")}
    local = _local_cache(signatures)
    results: list[dict] = []
    unresolved: list[dict] = []
    for row in population:
        if not row.get("signature"):
            unresolved.append({"mint": row["mint"], "reason": "NO_RETAINED_FUNDING_SIGNATURE"})
    pending = [r for r in population if r.get("signature") and r["signature"] not in local]
    for row in population:
        if row.get("signature") in local:
            results.append(_decode(row, local[row["signature"]], "LOCAL_CACHE"))
    url = _rpc_url()
    def fetch(row: dict):
        try:
            tx = _rpc_get(url, row["signature"])
            return row, tx, None
        except Exception as exc:
            return row, None, type(exc).__name__
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        for row, tx, error in executor.map(fetch, pending):
            if tx:
                results.append(_decode(row, tx, "RPC"))
            else:
                unresolved.append({"mint": row["mint"], "signature": row["signature"], "reason": error or "RPC_RETURNED_NULL"})
    families: dict[str, list[dict]] = defaultdict(list)
    for record in results:
        families[record.pop("_family_key")].append(record)
    family_rows = []
    for index, (key, records) in enumerate(sorted(families.items(), key=lambda item: (-len(item[1]), item[0])), 1):
        sample = records[0]
        family_rows.append({"architecture_id": f"WT_ARCH_{index:02d}", "retained_mechanism": sample["retained_mechanism"],
            "launch_count": len(records), "percentage_of_evaluable": round(100 * len(records) / len(results), 2),
            "instruction_structure": sample["instruction_sequence"], "amount_structure": {"creation_deposit_lamports": sample["creation_deposit_lamports"], "ordered_transfer_lamports": sample["ordered_transfer_lamports"]},
            "role_structure": {k: sample[k] for k in ("fee_payer_role", "funding_source_role", "temporary_account_owner_role", "close_authority_role", "close_destination_role")},
            "earliest_occurrence": min(x["launch_timestamp"] for x in records if x["launch_timestamp"] is not None),
            "latest_occurrence": max(x["launch_timestamp"] for x in records if x["launch_timestamp"] is not None), "members": [x["mint"] for x in records]})
    totals = Counter(r["retained_mechanism"] for r in results)
    supplied_count = sum(r["creation_deposit_lamports"] == [2_122_039_280] and r["separate_transfer_count"] == 0 and r["close_account"] for r in results)
    leviathan_exact = {
        "create_method": "createAccountWithSeed", "creation_deposit_lamports": 2_039_280,
        "separate_transfer_lamports": 99_997_955_720, "sync_native": True,
        "close_account": True, "same_temporary_account_continuity": True,
        "source_equals_owner": True, "close_destination": "DISTINCT_LAUNCH_ASSOCIATED",
    }
    collisions = sum(
        r["create_methods"] == [leviathan_exact["create_method"]]
        and r["creation_deposit_lamports"] == [leviathan_exact["creation_deposit_lamports"]]
        and r["ordered_transfer_lamports"] == [leviathan_exact["separate_transfer_lamports"]]
        and r["sync_native"] and r["close_account"] and r["same_temporary_account_continuity"]
        and r["funding_source_role"] == "TEMP_OWNER"
        and r["close_destination_role"] == leviathan_exact["close_destination"]
        for r in results
    )
    payload = {"schema_version": "WATCHTOWER_FUNDING_TRANSACTION_CENSUS_V1", "research_only": True,
        "scope": "current canonical confirmed WATCHTOWER membership; retained funding signatures only", "rpc_method": "getTransaction", "wallet_history_calls": 0,
        "denominators": {"WATCHTOWER_CONFIRMED_LAUNCHES": len(population), "WATCHTOWER_FUNDING_PATH_AVAILABLE": sum(bool(r.get("signature")) for r in population), "WATCHTOWER_FUNDING_SIGNATURE_AVAILABLE": len(signatures), "WATCHTOWER_LOCAL_DECODED": sum(r["decode_source"] == "LOCAL_CACHE" for r in results), "WATCHTOWER_RPC_DECODED": sum(r["decode_source"] == "RPC" for r in results), "WATCHTOWER_EVALUABLE": len(results), "WATCHTOWER_UNRESOLVED": len(unresolved)},
        "mechanism_counts": {"WATCHTOWER_WSOL_WRAP_CLOSE": totals["WSOL_WRAP_CLOSE"], "WATCHTOWER_PLAIN_XFER": totals["PLAIN_XFER"], "WATCHTOWER_OTHER": len(results) - totals["WSOL_WRAP_CLOSE"] - totals["PLAIN_XFER"]},
        "WATCHTOWER_FUNDING_ARCHITECTURE_FAMILIES": len(family_rows), "families": family_rows,
        "supplied_2_12203928_SOL_create_close_count": supplied_count, "records": results, "unresolved": unresolved,
        "leviathan_comparison": {"established_lifecycle": leviathan_exact,
            "LEVIATHAN_WATCHTOWER_EXACT_ARCHITECTURE_COLLISIONS": collisions,
            "LEVIATHAN_WATCHTOWER_FUNDING_ARCHITECTURE_SEPARATION": "STRONG" if collisions == 0 else "WEAK"},
        "generated_at": int(time.time())}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    payload["digest"] = hashlib.sha256(canonical).hexdigest()
    OUT.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"denominators": payload["denominators"], "mechanisms": payload["mechanism_counts"], "families": len(family_rows), "supplied": supplied_count, "digest": payload["digest"]}, sort_keys=True))


if __name__ == "__main__":
    main()
