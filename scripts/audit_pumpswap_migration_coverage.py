#!/usr/bin/env python3
"""
Standalone PumpSwap migration coverage audit.

Fetches recent signatures from the pump.fun migration account (39azUYFW...),
extracts pump-suffixed mints from accountKeys, deduplicates by mint, and
cross-checks against token_analysis in the hot DB.

Writes result to logs/migration_coverage_audit.json.

Usage:
    python scripts/audit_pumpswap_migration_coverage.py
    python scripts/audit_pumpswap_migration_coverage.py --sig-limit 100

Designed to run hourly via cron/supervisor. ~20 RPC credits per run.
Read-only: no writes to any database. No listener impact.
"""

import argparse
import json
import os
import sqlite3
import sys
import time
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_PATH  = PROJECT_ROOT / "logs" / "migration_coverage_audit.json"
DB_PATH      = os.environ.get("DB_PATH") or str(PROJECT_ROOT / "database" / "flex_complete_database.db")
RPC_URL      = os.environ.get("HELIUS_RPC_URL", "https://mainnet.helius-rpc.com/?api-key=16f1a5fc-2592-466c-a5d4-b5799ae8da96")

MIGRATION_ACCOUNT = "39azUYFWPz3VHgKCf3VChUwbpURdCHRxjWVowf5jUJjg"
PUMP_SUFFIX       = "pump"


def _rpc(method, params, retries=3):
    for attempt in range(retries):
        try:
            body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
            req  = urllib.request.Request(RPC_URL, data=body, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read())["result"]
        except Exception as e:
            if attempt == retries - 1:
                raise
            time.sleep(2 ** attempt)


def _iso(ts):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts)) if ts else None


def run_audit(sig_limit=50):
    t0 = time.time()
    rpc_calls = 0

    # 1. Fetch recent signatures from migration account
    sigs = _rpc("getSignaturesForAddress", [MIGRATION_ACCOUNT, {"limit": sig_limit, "commitment": "confirmed"}])
    rpc_calls += 1
    good = [s for s in sigs if not s.get("err")]

    if not good:
        return {"error": "no signatures returned", "rpc_credits_used": rpc_calls}

    window_end_ts   = good[0]["blockTime"]
    window_start_ts = good[-1]["blockTime"]

    # 2. Fetch full transactions — cap at sig_limit
    seen_mints = {}   # mint -> {sig, blocktime, status, has_create_pool}

    for s in good:
        time.sleep(0.25)  # gentle rate limit
        try:
            tx = _rpc("getTransaction", [s["signature"], {
                "encoding": "json",
                "maxSupportedTransactionVersion": 0,
                "commitment": "confirmed",
            }])
        except Exception as e:
            print(f"  WARN: getTransaction failed {s['signature'][:20]}... {e}", file=sys.stderr)
            continue
        rpc_calls += 1
        if not tx:
            continue

        logs = tx.get("meta", {}).get("logMessages") or []
        log_str = "\n".join(logs)
        has_create_pool = "Instruction: CreatePool" in log_str

        # Extract account keys — format varies (list of str or list of dict)
        raw_keys = tx.get("transaction", {}).get("message", {}).get("accountKeys") or []
        acct_keys = []
        for k in raw_keys:
            acct_keys.append(k.get("pubkey", "") if isinstance(k, dict) else k)

        # Pump-suffix mint is the discriminating field
        mint = next(
            (k for k in acct_keys if isinstance(k, str) and k.endswith(PUMP_SUFFIX) and len(k) > 30),
            None,
        )

        if mint and mint not in seen_mints:
            seen_mints[mint] = {
                "signature": s["signature"],
                "block_time": s["blockTime"],
                "has_create_pool": has_create_pool,
                "mint": mint,
            }
        elif not mint:
            # No pump-suffix mint in this tx — it's a setup tx for a token we may already have
            # Try to match by sig against DB later
            if s["signature"] not in seen_mints:
                seen_mints[s["signature"]] = {
                    "signature": s["signature"],
                    "block_time": s["blockTime"],
                    "has_create_pool": has_create_pool,
                    "mint": None,
                    "_sig_only": True,
                }

    # 3. Cross-check against DB (read-only)
    try:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
    except Exception as e:
        return {"error": f"DB open failed: {e}", "rpc_credits_used": rpc_calls}

    matched = missing = uncertain = 0
    sample  = []

    for key, entry in seen_mints.items():
        mint = entry.get("mint")
        sig  = entry["signature"]
        row  = None

        if mint:
            row = conn.execute(
                "SELECT mint FROM token_analysis WHERE mint = ? LIMIT 1", (mint,)
            ).fetchone()
        if not row:
            row = conn.execute(
                "SELECT mint FROM token_analysis WHERE migration_tx = ? LIMIT 1", (sig,)
            ).fetchone()

        if entry.get("_sig_only") and not row:
            # Setup tx with no pump mint — genuinely uncertain
            uncertain += 1
            status = "UNCERTAIN"
        elif row:
            matched += 1
            status = "MATCH"
        elif mint:
            missing += 1
            status = "MISSING"
        else:
            uncertain += 1
            status = "UNCERTAIN"

        entry["status"] = status
        sample.append({
            "signature": sig,
            "mint": mint,
            "block_time": entry["block_time"],
            "status": status,
            "has_create_pool": entry["has_create_pool"],
        })

    conn.close()

    # 4. Classify
    unique_tokens = sum(1 for e in seen_mints.values() if not e.get("_sig_only"))
    total_classified = matched + missing  # uncertain excluded from coverage %
    coverage_pct = round(matched / total_classified * 100, 1) if total_classified else 0.0

    if missing > 0:
        classification = "C"
        confidence     = "LOW"
    elif unique_tokens < 3:
        classification = "B"
        confidence     = "MEDIUM"
    elif coverage_pct == 100.0:
        classification = "A"
        confidence     = "HIGH"
    else:
        classification = "B"
        confidence     = "MEDIUM"

    runtime = round(time.time() - t0, 1)

    missing_candidates = [
        {"signature": e["signature"], "mint": e.get("mint"), "block_time": e["block_time"]}
        for e in seen_mints.values()
        if e.get("status") == "MISSING"
    ]

    return {
        "last_run":              int(time.time()),
        "window_start":          _iso(window_start_ts),
        "window_end":            _iso(window_end_ts),
        "window_hours":          round((window_end_ts - window_start_ts) / 3600, 1) if window_start_ts and window_end_ts else None,
        "rpc_credits_used":      rpc_calls,
        "runtime_seconds":       runtime,
        "sigs_fetched":          len(good),
        "unique_tokens_sampled": unique_tokens,
        "matched":               matched,
        "missing":               missing,
        "uncertain":             uncertain,
        "coverage_pct":          coverage_pct,
        "classification":        classification,
        "confidence":            confidence,
        "evidence_of_missing":   missing > 0,
        "source":                "pumpswap_migration_account_rpc_audit",
        "methodology":           "pump_suffix_mint_from_account_keys",
        "missing_candidates":    missing_candidates,
        "sample":                sorted(sample, key=lambda x: x["block_time"], reverse=True),
    }


def main():
    parser = argparse.ArgumentParser(description="PumpSwap migration coverage audit")
    parser.add_argument("--sig-limit", type=int, default=50,
                        help="Number of signatures to fetch from migration account (default 50)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print result JSON without writing to file")
    args = parser.parse_args()

    print(f"[audit] Running PumpSwap migration coverage audit (sig_limit={args.sig_limit})...")
    result = run_audit(sig_limit=args.sig_limit)

    output = json.dumps(result, indent=2)

    if args.dry_run:
        print(output)
        return

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(output)

    # Summary to stdout for cron/supervisor logs
    if "error" in result:
        print(f"[audit] ERROR: {result['error']}")
        sys.exit(1)

    print(
        f"[audit] Done: {result['unique_tokens_sampled']} tokens sampled | "
        f"{result['matched']} matched | {result['missing']} missing | "
        f"coverage={result['coverage_pct']}% | class={result['classification']} | "
        f"credits={result['rpc_credits_used']} | runtime={result['runtime_seconds']}s"
    )
    if result["evidence_of_missing"]:
        print(f"[audit] ⚠ MISSING MIGRATIONS DETECTED: {result['missing_candidates']}")


if __name__ == "__main__":
    main()
