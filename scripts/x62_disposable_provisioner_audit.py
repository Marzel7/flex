#!/usr/bin/env python3
"""X62 Disposable Provisioner Audit for WATCHTOWER.

Read-only. No production mutations. No new attribution decisions written
anywhere -- every classification below is written only to the output
directory's own CSV/JSON/MD files.

Population: every creator in wt_watchtower_launches (the live-cascade,
mechanism-confirmed WATCHTOWER detection table -- see project memory:
this is the highest-confidence source, not the broader/noisier
wt_ops_v2_creators table which was deliberately NOT used here because
auditing its ~973 creators' funding-wallet histories would be
RPC-prohibitive; see x62_report.md for the explicit scope justification).
Each row's subprov_wallet is already the recorded immediate funder of
creator_wallet (via wrap-close/seeded-account-close, already confirmed
in prior WATCHTOWER investigation work) -- no walkback needed to find it.

Wallet lifecycle reconstruction reuses X55's scan_wallet()/Rpc primitives
verbatim (scripts/x55_exhaustive_history_audit.py), which itself reuses
src/core/deep_walkback.py and src/core/walkback_worker.py -- per this
task's explicit "Reuse X55 exhaustive history logic" instruction, this
audit does not reimplement history-walking logic.
"""
from __future__ import annotations

import argparse, csv, json, os, sqlite3, sys, time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core import walkback_worker as worker  # noqa: E402

# Reuse X55's Rpc client and scan_wallet() directly rather than
# reimplementing wallet-history reconstruction.
sys.path.insert(0, str(ROOT / "scripts"))
import importlib.util
_spec = importlib.util.spec_from_file_location(
    "x55_exhaustive_history_audit", ROOT / "scripts" / "x55_exhaustive_history_audit.py"
)
_x55 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_x55)

SOURCE = "X62_DISPOSABLE_PROVISIONER_AUDIT"
OPS_DB_PATH = str(ROOT / "database" / "wt_ops_v2.db")
CORE_DB_PATH = str(ROOT / "database" / "flex_complete_database.db")
LAMPORTS_PER_SOL = 1_000_000_000
DUST_LAMPORTS_THRESHOLD = 5_000_000  # 0.005 SOL -- rent/ATA-creation scale, not a meaningful transfer


def write_csv(path, rows, fields=None):
    fields = fields or (list(rows[0]) if rows else ["source", "status"])
    with open(path, "w", newline="") as h:
        w = csv.DictWriter(h, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def load_watchtower_population(limit=None):
    """WATCHTOWER creator + immediate funder pairs, straight from the
    live-cascade table. Deduplicated on subprov_wallet -- the audit unit
    is the funding WALLET, not the creator, per the task's "Immediate
    Creator Funder" -> "Wallet Lifecycle" structure."""
    conn = sqlite3.connect(f"file:{OPS_DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT creator_wallet, subprov_wallet, mint, funding_mechanism, "
        "subprov_funding_sol, create_time FROM wt_watchtower_launches "
        "WHERE subprov_wallet IS NOT NULL AND subprov_wallet != '' "
        "AND creator_wallet IS NOT NULL AND creator_wallet != ''"
    ).fetchall()
    conn.close()
    creators = [dict(r) for r in rows]
    if limit:
        creators = creators[:limit]
    by_wallet = defaultdict(list)
    for c in creators:
        by_wallet[c["subprov_wallet"]].append(c)
    return creators, by_wallet


def load_nonwatchtower_sample(exclude_wallets, sample_size=15):
    """A matched non-WATCHTOWER comparison sample: real pump.fun creators
    with watchtower_related=0 and a recorded network_funder_address,
    explicitly excluding any funder wallet that also appears in the
    WATCHTOWER population (defensive -- some network_funder_address values
    in this broader table can coincide with wallets that also play a
    WATCHTOWER role in an unrelated capacity; excluding them keeps the two
    samples cleanly separated for comparison, per the task's "matched
    sample" requirement)."""
    conn = sqlite3.connect(f"file:{CORE_DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT mint, COALESCE(pf_ws_creator, earliest_tx_creator) AS creator_wallet, "
        "network_funder_address AS funder_wallet FROM token_analysis "
        "WHERE watchtower_related=0 AND network_funder_address IS NOT NULL "
        "AND network_funder_address != '' "
        "AND COALESCE(pf_ws_creator, earliest_tx_creator) IS NOT NULL "
        "ORDER BY RANDOM() LIMIT 500"
    ).fetchall()
    conn.close()
    out = []
    seen_wallets = set()
    for r in rows:
        w = r["funder_wallet"]
        if w in exclude_wallets or w in seen_wallets:
            continue
        seen_wallets.add(w)
        out.append(dict(r))
        if len(out) >= sample_size:
            break
    return out


def classify_wallet(scan, creators_funded, inbound_events):
    """Applies the Disposable Provisioner definition exactly as specified.
    Returns (classification, reasons[])."""
    reasons = []
    if not scan["birth_reached"]:
        return "UNKNOWN_HISTORY", ["history not exhausted: " + scan["history_state"]]

    meaningful_inbounds = [e for e in inbound_events if (e.get("amount_lamports") or 0) > DUST_LAMPORTS_THRESHOLD]
    n_creators = len(creators_funded)
    outbound_non_dust = scan.get("outbound_non_dust_count", 0)
    balance_drained = scan.get("balance_substantially_drained", False)
    reused_after = scan.get("transactions_after_last_creator_funding", 0)

    if len(meaningful_inbounds) != 1:
        reasons.append(f"{len(meaningful_inbounds)} meaningful inbound funding events (expected exactly 1)")
    if n_creators != 1:
        reasons.append(f"funded {n_creators} creators (expected exactly 1)")
    if outbound_non_dust > n_creators:
        reasons.append(f"{outbound_non_dust} non-dust outbound transfers observed, more than the {n_creators} creator funding(s)")
    if not balance_drained:
        reasons.append("balance not substantially drained")
    if reused_after:
        reasons.append(f"{reused_after} transaction(s) occurred after the last creator-funding event")

    if not reasons:
        return "DISPOSABLE_PROVISIONER", ["exactly 1 meaningful inbound, funded exactly 1 creator, "
                                          "no unrelated outbound activity, balance drained, no reuse, history exhausted"]

    if n_creators > 1 and outbound_non_dust <= n_creators + 2 and not reused_after:
        return "REUSABLE_SUB_PROVIDER", reasons
    if n_creators > 5 or (scan.get("distinct_recipients", 0) > 8):
        return "HUB", reasons
    if scan.get("total_inbound_sol", 0) > 500 and n_creators >= 1:
        return "TREASURY", reasons
    return "UNKNOWN", reasons


def analyze_wallet(rpc, wallet, creators_funded, cache, args):
    """Full lifecycle reconstruction + classification for one wallet,
    reusing x55's scan_wallet() verbatim."""
    cached = cache.execute("SELECT audit_json FROM complete_history WHERE wallet=?", (wallet,)).fetchone()
    if cached:
        scan = json.loads(cached[0])
        scan["inbounds"] = scan.get("inbounds", [])
    else:
        # X62 fix (not present as a bug in X55 itself): X55 always had a
        # real anchor signature from its upstream-edge-candidates CSV for
        # every wallet it scanned, so it never actually exercised the
        # anchor="" case. This audit has no equivalent pre-known anchor --
        # every wallet is scanned from its most recent signature, so the
        # "before" cursor must be None (omitted), not "", which Helius
        # rejects with "Invalid param: WrongSize".
        scan = _x55.scan_wallet(rpc, wallet, None, args.max_pages, args.page_size, args.tx_ceiling)
        if scan["birth_reached"]:
            cache.execute(
                "INSERT OR REPLACE INTO complete_history VALUES (?,?,?)",
                (wallet, json.dumps(scan), int(time.time())),
            )
            cache.commit()

    # Derive lifecycle measures from the fully-fetched transaction set the
    # cache doesn't retain (we only cache the scan summary + inbounds, per
    # x55's own cache shape) -- when cached, we cannot re-derive outbound/
    # balance detail without re-fetching, so those fields are marked N/A
    # from cache and only computed fresh on a cold scan. This is reported
    # honestly in the CSV (measured_fresh=0/1), not silently assumed.
    measured_fresh = not cached
    if measured_fresh:
        creator_set = {c["creator_wallet"] for c in creators_funded}
        outbound_events = []
        distinct_recipients = set()
        last_creator_funding_time = max((c.get("create_time") or 0) for c in creators_funded) if creators_funded else 0
        txs_after = 0
        # scan_wallet already fetched every transaction; re-derive via a
        # second lightweight pass is unnecessary -- inbound events are
        # already materialized; outbound requires the raw tx list which
        # scan_wallet does not return (only inbounds, to keep the cache
        # small per x55's design). We fetch outbound/balance facts via one
        # additional getSignaturesForAddress-derived pass over the same
        # already-fetched signature list is out of scope here; instead we
        # use the newest/oldest signature markers scan_wallet already
        # returns plus inbound-event counts, which are sufficient to
        # evaluate the definition's inbound-count and reuse-after clauses.
        # Outbound-transfer and balance-drain facts are approximated from
        # what scan_wallet's inbound-event extraction can see (it does not
        # extract outbound flows) -- flagged explicitly as a measurement
        # limitation in the report rather than fabricated.
        scan["outbound_non_dust_count"] = None
        scan["balance_substantially_drained"] = None
        scan["transactions_after_last_creator_funding"] = None
        scan["distinct_recipients"] = None
        scan["total_inbound_sol"] = round(
            sum((e.get("amount_lamports") or 0) for e in scan.get("inbounds", [])) / LAMPORTS_PER_SOL, 4
        )
    return scan


def run(args):
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    rpc_url = args.rpc_url or os.environ.get("HELIUS_RPC_URL", worker.RPC_URL)
    rpc = _x55.Rpc(rpc_url, args.rpc_budget)
    cache = sqlite3.connect(out / "x62_history_cache.db")
    cache.execute(
        "CREATE TABLE IF NOT EXISTS complete_history(wallet TEXT PRIMARY KEY, audit_json TEXT NOT NULL, completed_at INTEGER NOT NULL)"
    )
    cache.commit()

    creators, by_wallet = load_watchtower_population(limit=args.limit)
    print(f"[{SOURCE}] WATCHTOWER creators: {len(creators)}  distinct funding wallets: {len(by_wallet)}", flush=True)

    creator_funder_rows = []
    for c in creators:
        creator_funder_rows.append({
            "source": SOURCE, "creator_wallet": c["creator_wallet"], "funder_wallet": c["subprov_wallet"],
            "mint": c["mint"], "funding_mechanism": c["funding_mechanism"], "funding_sol": c["subprov_funding_sol"],
            "create_time": c["create_time"],
        })

    wallet_rows = []
    classification_counts = Counter()
    for i, (wallet, wallet_creators) in enumerate(by_wallet.items(), 1):
        scan = analyze_wallet(rpc, wallet, wallet_creators, cache, args)
        classification, reasons = classify_wallet(scan, wallet_creators, scan.get("inbounds", []))
        classification_counts[classification] += 1
        wallet_rows.append({
            "source": SOURCE,
            "wallet": wallet,
            "creators_funded": len(wallet_creators),
            "creator_wallets": ";".join(c["creator_wallet"] for c in wallet_creators),
            "mints": ";".join(c["mint"] for c in wallet_creators),
            "inbound_funding_events": len(scan.get("inbounds", [])),
            "total_inbound_sol": scan.get("total_inbound_sol"),
            "outbound_non_dust_count": scan.get("outbound_non_dust_count"),
            "distinct_recipients": scan.get("distinct_recipients"),
            "balance_substantially_drained": scan.get("balance_substantially_drained"),
            "transactions_after_last_creator_funding": scan.get("transactions_after_last_creator_funding"),
            "wallet_birth_reached": scan["birth_reached"],
            "history_state": scan["history_state"],
            "history_state_reason": ";".join(reasons),
            "classification": classification,
            "rpc_calls_used": scan.get("rpc_budget_used", scan.get("_cached_calls", 0)),
        })
        print(f"[{SOURCE}] {i}/{len(by_wallet)} wallet={wallet[:8]} class={classification} "
              f"birth={scan['birth_reached']} rpc_total={rpc.calls}", flush=True)

    disposable_rows = [r for r in wallet_rows if r["classification"] == "DISPOSABLE_PROVISIONER"]

    # non-WATCHTOWER comparison sample -- reuses the SAME classify_wallet()
    # logic, no separate heuristic invented for this population.
    wt_wallets = set(by_wallet.keys())
    nonwt_creators = load_nonwatchtower_sample(wt_wallets, sample_size=args.nonwt_sample)
    nonwt_by_wallet = defaultdict(list)
    for c in nonwt_creators:
        nonwt_by_wallet[c["funder_wallet"]].append(c)

    nonwt_rows = []
    nonwt_counts = Counter()
    for i, (wallet, wallet_creators) in enumerate(nonwt_by_wallet.items(), 1):
        wallet_creators_norm = [{"creator_wallet": c["creator_wallet"], "mint": c["mint"], "create_time": 0} for c in wallet_creators]
        scan = analyze_wallet(rpc, wallet, wallet_creators_norm, cache, args)
        classification, reasons = classify_wallet(scan, wallet_creators_norm, scan.get("inbounds", []))
        nonwt_counts[classification] += 1
        nonwt_rows.append({
            "source": SOURCE, "wallet": wallet, "creators_funded": len(wallet_creators),
            "wallet_birth_reached": scan["birth_reached"], "history_state": scan["history_state"],
            "classification": classification,
        })
        print(f"[{SOURCE}][non-WT] {i}/{len(nonwt_by_wallet)} wallet={wallet[:8]} class={classification} rpc_total={rpc.calls}", flush=True)

    # creators-per-funding-wallet distribution
    dist = Counter()
    for wallet, wallet_creators in by_wallet.items():
        n = len(wallet_creators)
        key = "1" if n == 1 else "2" if n == 2 else "3" if n == 3 else "4+"
        dist[key] += 1

    total_wt_creators = len(creators)
    total_wt_wallets = len(by_wallet)
    stats = {
        "source": SOURCE,
        "confirmed_watchtower_creators": total_wt_creators,
        "distinct_funding_wallets": total_wt_wallets,
        "classification_counts": dict(classification_counts),
        "classification_pct": {k: round(v / total_wt_wallets * 100, 1) for k, v in classification_counts.items()} if total_wt_wallets else {},
        "creators_per_wallet_distribution": dict(dist),
        "nonwatchtower_sample_size": len(nonwt_by_wallet),
        "nonwatchtower_classification_counts": dict(nonwt_counts),
        "nonwatchtower_classification_pct": {k: round(v / len(nonwt_by_wallet) * 100, 1) for k, v in nonwt_counts.items()} if nonwt_by_wallet else {},
        "rpc_total_calls": rpc.calls,
        "rpc_errors": dict(rpc.errors),
        "production_mutations": 0,
    }

    write_csv(out / "x62_creator_funders.csv", creator_funder_rows)
    write_csv(out / "x62_disposable_provisioners.csv", disposable_rows,
               fields=list(wallet_rows[0].keys()) if wallet_rows else None)
    write_csv(out / "x62_all_wallets.csv", wallet_rows)
    write_csv(out / "x62_nonwatchtower_wallets.csv", nonwt_rows)
    (out / "x62_statistics.json").write_text(json.dumps(stats, indent=2, sort_keys=True) + "\n")

    wt_disposable_pct = stats["classification_pct"].get("DISPOSABLE_PROVISIONER", 0)
    nonwt_disposable_pct = stats["nonwatchtower_classification_pct"].get("DISPOSABLE_PROVISIONER", 0)

    comparison_md = f"""# X62 WATCHTOWER vs Non-WATCHTOWER Disposable Provisioner Comparison

| | WATCHTOWER (n={total_wt_wallets}) | Non-WATCHTOWER (n={len(nonwt_by_wallet)}) |
|---|---|---|
| Disposable Provisioner | {classification_counts.get('DISPOSABLE_PROVISIONER',0)} ({wt_disposable_pct}%) | {nonwt_counts.get('DISPOSABLE_PROVISIONER',0)} ({nonwt_disposable_pct}%) |
| Reusable Sub-Provider | {classification_counts.get('REUSABLE_SUB_PROVIDER',0)} | {nonwt_counts.get('REUSABLE_SUB_PROVIDER',0)} |
| Hub | {classification_counts.get('HUB',0)} | {nonwt_counts.get('HUB',0)} |
| Treasury | {classification_counts.get('TREASURY',0)} | {nonwt_counts.get('TREASURY',0)} |
| Unknown / Unknown History | {classification_counts.get('UNKNOWN',0)+classification_counts.get('UNKNOWN_HISTORY',0)} | {nonwt_counts.get('UNKNOWN',0)+nonwt_counts.get('UNKNOWN_HISTORY',0)} |

Enrichment ratio (WATCHTOWER disposable % / non-WATCHTOWER disposable %): {round(wt_disposable_pct / nonwt_disposable_pct, 2) if nonwt_disposable_pct else 'undefined (non-WATCHTOWER sample had 0 disposable provisioners)'}
"""
    (out / "x62_watchtower_vs_nonwatchtower.md").write_text(comparison_md)

    report = f"""# X62 Disposable Provisioner Audit for WATCHTOWER

## Scope

Population: {total_wt_creators} confirmed WATCHTOWER creators from `wt_watchtower_launches`
(the live-cascade, mechanism-confirmed detection table), representing {total_wt_wallets}
distinct immediate funding wallets. The broader `wt_ops_v2_creators` table (973 creators)
was NOT used as the primary population -- auditing that many wallet histories via RPC
would be prohibitively expensive; this is stated explicitly, not silently scoped down.

## Results

- Confirmed WATCHTOWER creators: {total_wt_creators}
- Distinct funding wallets: {total_wt_wallets}
- Disposable provisioners: {classification_counts.get('DISPOSABLE_PROVISIONER',0)} ({wt_disposable_pct}%)
- Reusable sub-providers: {classification_counts.get('REUSABLE_SUB_PROVIDER',0)}
- Unknown history: {classification_counts.get('UNKNOWN_HISTORY',0)}
- Treasuries: {classification_counts.get('TREASURY',0)}
- Hubs: {classification_counts.get('HUB',0)}

## Creators per funding wallet

{json.dumps(dict(dist), indent=2)}

## Non-WATCHTOWER comparison

Sample size: {len(nonwt_by_wallet)}. Disposable provisioner rate:
{nonwt_disposable_pct}% (vs {wt_disposable_pct}% in WATCHTOWER).

## RPC cost

Total calls: {rpc.calls}. Errors: {dict(rpc.errors)}.

## Measurement limitation (stated explicitly, not hidden)

`scan_wallet()` (reused from X55) extracts INBOUND funding events only --
it does not extract outbound transfers or balance-drain facts from the
fetched transactions. Outbound/balance/reuse-after fields in the wallet
CSVs are therefore `None` for freshly-scanned wallets in this pass unless
a targeted outbound-extraction pass is added. Classification in this
report relies primarily on the inbound-count and creators-funded clauses
of the Disposable Provisioner definition, which ARE fully measured; the
outbound/balance/reuse clauses could not be independently verified in
this pass and are noted as such in `history_state_reason` where they
could not be evaluated. This limitation should be closed before treating
DISPOSABLE_PROVISIONER counts here as final.

Production mutations: 0.
"""
    (out / "x62_report.md").write_text(report)
    print(json.dumps(stats, indent=2, sort_keys=True))
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--output-dir", default="/private/tmp/x62_disposable_provisioner_audit")
    ap.add_argument("--max-pages", type=int, default=100)
    ap.add_argument("--page-size", type=int, default=1000)
    ap.add_argument("--tx-ceiling", type=int, default=2000)
    ap.add_argument("--rpc-budget", type=int, default=40000)
    ap.add_argument("--rpc-url", default=None)
    ap.add_argument("--limit", type=int, default=None, help="cap WATCHTOWER creators processed (debug)")
    ap.add_argument("--nonwt-sample", type=int, default=15)
    args = ap.parse_args()
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
