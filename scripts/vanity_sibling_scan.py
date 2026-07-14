#!/usr/bin/env python3
"""
Offline vanity-sibling RPC scanner.

For each candidate sibling wallet (sharing a >=4-char prefix/suffix with a confirmed
WATCHTOWER treasury), fetches up to MAX_SIGS recent signatures via getSignaturesForAddress
(1cr each), decodes each tx (1cr each), and classifies the wallet's relationship to
known WATCHTOWER infrastructure.

Results are persisted to wt_vanity_sequence_evidence + wt_vanity_sibling_scan_cache.
Safe to re-run: idempotent on (wallet, sig).

Budget: 13 wallets × 100 sigs + 100 tx fetches ≈ 1,300–2,600 credits max.
"""

import os
import sys
import json
import time
import sqlite3
import urllib.request
import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

OPS_DB   = os.environ.get("WT_OPS_DB_PATH",
           os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "database", "wt_ops_v2.db"))
HELIUS_KEY = os.environ.get("HELIUS_API_KEY", "16f1a5fc-2592-466c-a5d4-b5799ae8da96")
RPC_URL    = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_KEY}"

MAX_WALLETS          = 13
MAX_SIGS_PER_WALLET  = 100
MAX_TX_PER_WALLET    = 100
MAX_RUNTIME_PER_WALLET_S = 15.0
RPC_TIMEOUT_S        = 12.0

# ── Target wallets ────────────────────────────────────────────────────────────
# Derived from local suffix/prefix family scan. Each entry: (sibling, family_label, infra_anchor)
TARGETS = [
    # 44or / qJFM  (4 wallets sharing prefix+suffix — highest structural confidence)
    ("44orPwdUubVj3gatChbdxGMfEo7qr85P8UBSimVvqJFM",  "44or_qJFM", "44orWS68MqXG198M2eGkCYxBNwKgz4FtE4Sv7w5SavqJFM"),
    ("44orhfxo7VrfWXu5sEt2C7tVqaVFKsZKRDRFX4APqJFM",  "44or_qJFM", "44orWS68MqXG198M2eGkCYxBNwKgz4FtE4Sv7w5SavqJFM"),
    ("44or4iwE1TfCPaigXDApVqSHd3VjxfhAg8vxQXJCnCFM",  "44or_qJFM", "44orWS68MqXG198M2eGkCYxBNwKgz4FtE4Sv7w5SavqJFM"),
    ("44osr4T83ds2yL66vF8PHVX3N6gCfaLRRZRzdCtqJFM",   "44or_qJFM", "44orWS68MqXG198M2eGkCYxBNwKgz4FtE4Sv7w5SavqJFM"),
    # 6jeT / dUW1
    ("6jeTC3Ef3kfbxRLmzRyU4kZPxpU1Fv6Sj6qCwQqXUW1",  "6jeT_dUW1", "6jeT3WyrfwLxox3yiDHCMpfhKbvBt4VpGCiPUudUW1"),
    ("6jeTN11Ev8q6UbVC2eFkbqQPJV5dUjKhcJVe3ig3dUW1",  "6jeT_dUW1", "6jeT3WyrfwLxox3yiDHCMpfhKbvBt4VpGCiPUudUW1"),
    # N3TK / 3dW7
    ("N3TBMm8Y1tjNKAPVcG8TJdm1ZmMhiKMKh7g4d3dW7",     "N3TK_3dW7", "N3TKf3wMBNu8XmFNd3s7xfPbriYzS7LrNt5Ke9Lc3dW7"),
    ("N3Tp7LwgumwPkQH3Y5dJBPQHKu9oB3dW7",              "N3TK_3dW7", "N3TKf3wMBNu8XmFNd3s7xfPbriYzS7LrNt5Ke9Lc3dW7"),
    # 43PK
    ("43PKfX7cG6vJxhs7mBr49fXHMCxbcG2FN3D",            "43PK",      "43PKjr22AFXtCMmLwEpZSZHfNXuqgquR3p1qUYo3y3D"),
    ("43PKrTh5p2B5oAPLZ9SDr2Ej3y3D",                   "43PK",      "43PKjr22AFXtCMmLwEpZSZHfNXuqgquR3p1qUYo3y3D"),
    # G2CQ / ewPZ
    ("G2CQaizwXnprtMsaXmFhsaX4RnzFvGq4P8TxGjewPZ",   "G2CQ_ewPZ", "G2CQewGxgMrriQ5dBKqRaXbGdPPKYnV3d7CBnGewPZ"),
    # 5JWi / vezf
    ("5JWikKsqwN7PnvGiQUqWjrJmimLa8UgdtHByhgovezf",   "5JWi_vezf", "5JWii73Qc9FzHyCrFtNMK5iWfU8MVXE7M7JGVyTa5Qvezf"),
    # Cgwr
    ("CgwrhyawcKje1GTn57KjLi4W3JcWTMtAJmVfFdq8wUMWTU7Te", "Cgwr", "Cgwr5FAa6d39tqWWJXXkmYivn13aSJkERAa1GZY9hkTe"),
][:MAX_WALLETS]

# Classification constants
NO_ACTIVITY              = "NO_ACTIVITY"
UNRELATED_ACTIVITY       = "UNRELATED_ACTIVITY"
SAME_INFRA_COUNTERPARTY  = "SAME_INFRA_COUNTERPARTY"
PREP_THEN_FUND           = "PREP_THEN_FUND"
TREASURY_MESH            = "TREASURY_MESH"
SUBPROV_RELAY            = "SUBPROV_RELAY"
WRAP_CLOSE_LINKED        = "WRAP_CLOSE_LINKED"
CREATE_LINKED            = "CREATE_LINKED"


def _rpc(method, params):
    try:
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
        req  = urllib.request.Request(RPC_URL, data=body,
                                      headers={"Content-Type": "application/json"})
        return json.loads(urllib.request.urlopen(req, timeout=RPC_TIMEOUT_S).read()).get("result")
    except Exception as e:
        print(f"  RPC {method} failed: {e}")
        return None


def _get_tx(sig):
    return _rpc("getTransaction", [sig, {
        "encoding": "jsonParsed",
        "maxSupportedTransactionVersion": 0,
        "commitment": "confirmed",
    }])


def ensure_schema(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS wt_vanity_sibling_scan_cache (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            wallet          TEXT NOT NULL,
            family_label    TEXT NOT NULL,
            infra_anchor    TEXT NOT NULL,
            scan_result     TEXT,
            classification  TEXT,
            sigs_fetched    INTEGER DEFAULT 0,
            txns_decoded    INTEGER DEFAULT 0,
            infra_counterparties_json TEXT,
            interesting_sigs_json     TEXT,
            notes           TEXT,
            scanned_at      INTEGER NOT NULL DEFAULT (strftime('%s','now')),
            UNIQUE(wallet)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS wt_vanity_sequence_evidence (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            family_label    TEXT NOT NULL,
            wallet_a        TEXT NOT NULL,
            wallet_b        TEXT NOT NULL,
            role_a          TEXT,
            role_b          TEXT,
            a_to_b_seconds  INTEGER,
            a_amount_sol    REAL,
            b_amount_sol    REAL,
            behaviour_type  TEXT,
            confirmed_at    INTEGER NOT NULL DEFAULT (strftime('%s','now')),
            rpc_sig_a       TEXT,
            rpc_sig_b       TEXT,
            notes           TEXT
        )
    """)
    conn.commit()


def load_infra(conn):
    """Return sets of all known infra wallets."""
    treasuries = {r[0] for r in conn.execute("SELECT treasury FROM wt_confirmed_treasuries").fetchall()}
    subprovs   = {r[0] for r in conn.execute("SELECT subprov  FROM wt_discovered_subprovs").fetchall()}
    return treasuries, subprovs, treasuries | subprovs


def classify_tx(tx, wallet, infra_set, treasuries, subprovs):
    """
    Return (classification, notes, counterparties_in_infra, amount_sol_to_wallet, amount_sol_from_wallet).
    """
    if not tx:
        return UNRELATED_ACTIVITY, "tx_null", [], 0.0, 0.0

    meta = tx.get("meta") or {}
    keys_raw = tx.get("transaction", {}).get("message", {}).get("accountKeys") or []
    keys = [k.get("pubkey") if isinstance(k, dict) else k for k in keys_raw]
    pre  = meta.get("preBalances")  or []
    post = meta.get("postBalances") or []

    infra_cptys = [k for k in keys if k in infra_set and k != wallet]
    amount_in   = 0.0
    amount_out  = 0.0
    try:
        idx = keys.index(wallet)
        if idx < len(pre) and idx < len(post):
            delta = (post[idx] - pre[idx]) / 1e9
            if delta > 0: amount_in  =  delta
            if delta < 0: amount_out = -delta
    except ValueError:
        pass

    # Check for closeAccount (wrap-close pattern)
    inner_ixs = []
    for ix in meta.get("innerInstructions") or []:
        inner_ixs.extend(ix.get("instructions") or [])
    all_ixs = list((tx.get("transaction", {}).get("message", {}).get("instructions") or [])) + inner_ixs
    has_close = any(
        (ix.get("parsed", {}).get("type") == "closeAccount"
         or ix.get("programId") == "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")
        for ix in all_ixs if isinstance(ix, dict)
    )

    if not infra_cptys and not has_close:
        return UNRELATED_ACTIVITY, "no_infra_contact", [], amount_in, amount_out

    notes_parts = []
    classification = UNRELATED_ACTIVITY

    if infra_cptys:
        t_cptys = [c for c in infra_cptys if c in treasuries]
        s_cptys = [c for c in infra_cptys if c in subprovs]
        if t_cptys:
            classification = TREASURY_MESH if amount_out > 10 else SAME_INFRA_COUNTERPARTY
            notes_parts.append(f"treasury_cpty={t_cptys[0][:14]}")
        if s_cptys:
            classification = SUBPROV_RELAY
            notes_parts.append(f"subprov_cpty={s_cptys[0][:14]}")

    if has_close and infra_cptys:
        classification = WRAP_CLOSE_LINKED
        notes_parts.append("wrap_close_detected")

    return classification, ";".join(notes_parts), infra_cptys, amount_in, amount_out


def scan_wallet(conn, wallet, family_label, infra_anchor, infra_set, treasuries, subprovs):
    # Skip if already scanned
    existing = conn.execute(
        "SELECT scanned_at, classification FROM wt_vanity_sibling_scan_cache WHERE wallet=?",
        (wallet,)).fetchone()
    if existing:
        print(f"  SKIP (already scanned {datetime.datetime.utcfromtimestamp(existing[0]).strftime('%Y-%m-%d')}): {existing[1]}")
        return existing[1]

    t0 = time.time()
    print(f"  Fetching signatures (max {MAX_SIGS_PER_WALLET})...")
    sigs_raw = _rpc("getSignaturesForAddress", [wallet, {
        "limit": MAX_SIGS_PER_WALLET,
        "commitment": "confirmed",
    }]) or []
    sigs_fetched = len(sigs_raw)
    print(f"  Got {sigs_fetched} signatures")

    if sigs_fetched == 0:
        conn.execute("""
            INSERT OR REPLACE INTO wt_vanity_sibling_scan_cache
              (wallet, family_label, infra_anchor, scan_result, classification,
               sigs_fetched, txns_decoded, infra_counterparties_json,
               interesting_sigs_json, notes)
            VALUES (?,?,?,?,?,0,0,'[]','[]','no signatures found')
        """, (wallet, family_label, infra_anchor, "DONE", NO_ACTIVITY))
        conn.commit()
        return NO_ACTIVITY

    # Decode each tx (budget: MAX_TX_PER_WALLET, time-bound)
    all_classifications = []
    all_infra_cptys     = set()
    interesting_sigs    = []
    txns_decoded        = 0

    for item in sigs_raw[:MAX_TX_PER_WALLET]:
        if time.time() - t0 > MAX_RUNTIME_PER_WALLET_S:
            print(f"  Time limit hit after {txns_decoded} txns")
            break
        sig_str = item.get("signature") if isinstance(item, dict) else item
        if not sig_str:
            continue
        tx = _get_tx(sig_str)
        txns_decoded += 1
        if not tx:
            continue

        cls, notes, infra_c, amt_in, amt_out = classify_tx(tx, wallet, infra_set, treasuries, subprovs)
        all_classifications.append(cls)
        all_infra_cptys.update(infra_c)
        if cls not in (UNRELATED_ACTIVITY, NO_ACTIVITY):
            interesting_sigs.append({
                "sig":     sig_str,
                "cls":     cls,
                "notes":   notes,
                "amt_in":  round(amt_in, 6),
                "amt_out": round(amt_out, 6),
                "ts":      item.get("blockTime") if isinstance(item, dict) else None,
            })
        time.sleep(0.05)  # gentle pacing

    # Aggregate classification
    cls_priority = [CREATE_LINKED, WRAP_CLOSE_LINKED, PREP_THEN_FUND,
                    SUBPROV_RELAY, TREASURY_MESH, SAME_INFRA_COUNTERPARTY,
                    UNRELATED_ACTIVITY, NO_ACTIVITY]
    cls_set = set(all_classifications) or {NO_ACTIVITY}
    final_cls = next((c for c in cls_priority if c in cls_set), NO_ACTIVITY)

    elapsed = time.time() - t0
    print(f"  Decoded {txns_decoded} txns in {elapsed:.1f}s → {final_cls}")
    if all_infra_cptys:
        print(f"  Infra counterparties: {list(all_infra_cptys)[:5]}")
    if interesting_sigs:
        for s in interesting_sigs[:3]:
            print(f"    {s['cls']} amt_in={s['amt_in']} amt_out={s['amt_out']} {s['notes']}")

    conn.execute("""
        INSERT OR REPLACE INTO wt_vanity_sibling_scan_cache
          (wallet, family_label, infra_anchor, scan_result, classification,
           sigs_fetched, txns_decoded, infra_counterparties_json,
           interesting_sigs_json, notes)
        VALUES (?,?,?,?,?,?,?,?,?,?)
    """, (wallet, family_label, infra_anchor, "DONE", final_cls,
          sigs_fetched, txns_decoded,
          json.dumps(list(all_infra_cptys)),
          json.dumps(interesting_sigs),
          f"sigs={sigs_fetched} txns={txns_decoded} t={elapsed:.1f}s"))
    conn.commit()
    return final_cls


def check_prep_then_fund(conn, family_label, infra_anchor):
    """
    Pattern A: sibling small tx close in time to anchor large tx.
    Reads from the scan cache + watchtower_infra_events for timing.
    """
    # Get interesting sigs for this family
    rows = conn.execute("""
        SELECT wallet, interesting_sigs_json, scanned_at
        FROM wt_vanity_sibling_scan_cache
        WHERE family_label=? AND classification != ?
    """, (family_label, NO_ACTIVITY)).fetchall()

    if not rows:
        return

    # Load anchor timeline from infra_events
    hot = sqlite3.connect(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                       "database", "flex_complete_database.db"))
    anchor_events = hot.execute("""
        SELECT block_time, direction, counterparty, amount_sol, signature
        FROM watchtower_infra_events
        WHERE infra_address=?
        ORDER BY block_time ASC
    """, (infra_anchor,)).fetchall()
    hot.close()

    if not anchor_events:
        return

    anchor_large = [(bt, amt, sig) for bt, dir_, cpty, amt, sig in anchor_events
                    if dir_ == "outbound" and amt and amt >= 10.0]

    for wallet, sigs_json, _ in rows:
        try:
            sigs = json.loads(sigs_json) if sigs_json else []
        except Exception:
            sigs = []
        for sig_data in sigs:
            ts = sig_data.get("ts")
            amt_out = sig_data.get("amt_out", 0)
            if not ts or amt_out <= 0 or amt_out > 5:
                continue
            # Small outbound from sibling — check if anchor large tx within ±10 min
            for a_ts, a_amt, a_sig in anchor_large:
                if abs(a_ts - ts) <= 600:  # 10 min window
                    delta_s = a_ts - ts
                    print(f"\n  *** PATTERN A CANDIDATE: {family_label}")
                    print(f"      sibling={wallet[:14]}… small_out={amt_out:.4f}◎ at {datetime.datetime.utcfromtimestamp(ts)}")
                    print(f"      anchor ={infra_anchor[:14]}… large_out={a_amt:.2f}◎ at {datetime.datetime.utcfromtimestamp(a_ts)}")
                    print(f"      delta  ={delta_s}s  sig_sibling={sig_data['sig'][:20]}…")
                    conn.execute("""
                        INSERT INTO wt_vanity_sequence_evidence
                          (family_label, wallet_a, wallet_b, role_a, role_b,
                           a_to_b_seconds, a_amount_sol, b_amount_sol,
                           behaviour_type, rpc_sig_a, rpc_sig_b, notes)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                    """, (family_label, wallet, infra_anchor,
                          "SIBLING", "TREASURY",
                          delta_s, amt_out, a_amt,
                          PREP_THEN_FUND,
                          sig_data["sig"], a_sig,
                          "auto-detected within 600s window"))
                    conn.commit()


def print_summary(conn):
    print("\n" + "="*60)
    print("VANITY SIBLING SCAN — RESULTS SUMMARY")
    print("="*60)
    rows = conn.execute("""
        SELECT family_label, wallet, classification, sigs_fetched, txns_decoded,
               infra_counterparties_json, interesting_sigs_json, notes
        FROM wt_vanity_sibling_scan_cache
        ORDER BY family_label, classification
    """).fetchall()

    current_family = None
    for family, wallet, cls, sigs, txns, cptys_j, sigs_j, notes in rows:
        if family != current_family:
            print(f"\n── {family} ──")
            current_family = family
        cptys = json.loads(cptys_j) if cptys_j else []
        interesting = json.loads(sigs_j) if sigs_j else []
        flag = "🔴" if cls in (WRAP_CLOSE_LINKED, SUBPROV_RELAY, TREASURY_MESH, CREATE_LINKED, PREP_THEN_FUND) else \
               "🟡" if cls == SAME_INFRA_COUNTERPARTY else \
               "⚪" if cls == UNRELATED_ACTIVITY else "⬛"
        print(f"  {flag} {cls:<30} {wallet[:16]}…{wallet[-6:]}  sigs={sigs} txns={txns}")
        if cptys:
            print(f"       infra_cpty: {cptys[:3]}")
        if interesting:
            for s in interesting[:2]:
                print(f"       → {s['cls']} amt_in={s['amt_in']} amt_out={s['amt_out']} {s.get('notes','')}")

    seq = conn.execute("SELECT * FROM wt_vanity_sequence_evidence").fetchall()
    if seq:
        print(f"\n{'='*60}")
        print(f"SEQUENCE EVIDENCE: {len(seq)} pattern(s) found")
        for s in seq:
            print(f"  {s[9]} {s[1]} A={s[2][:14]}… B={s[3][:14]}… Δ={s[5]}s a={s[6]:.3f}◎ b={s[7]:.2f}◎")
    else:
        print(f"\nNo temporal sequences found in local data.")

    # Verdict
    active_cls = {r[2] for r in rows}
    high_signal = active_cls & {WRAP_CLOSE_LINKED, SUBPROV_RELAY, TREASURY_MESH, PREP_THEN_FUND, CREATE_LINKED}
    mid_signal  = active_cls & {SAME_INFRA_COUNTERPARTY}
    if high_signal:
        print(f"\n🔴 VERDICT: OPERATOR IDENTITY SIGNAL CONFIRMED — promote suffix evidence")
        print(f"   Classifications: {high_signal}")
    elif mid_signal:
        print(f"\n🟡 VERDICT: WEAK SIGNAL — shared infra counterparty observed, monitor")
    else:
        print(f"\n⬛ VERDICT: COSMETIC ONLY — no operational link found in 100-sig window")


def main():
    print(f"Vanity sibling scanner — {len(TARGETS)} targets")
    print(f"Budget: {MAX_SIGS_PER_WALLET} sigs × {MAX_TX_PER_WALLET} txns × {MAX_RUNTIME_PER_WALLET_S}s per wallet\n")

    conn = sqlite3.connect(OPS_DB)
    ensure_schema(conn)
    treasuries, subprovs, infra_set = load_infra(conn)
    print(f"Loaded infra: {len(treasuries)} treasuries, {len(subprovs)} subprovs\n")

    families_seen = set()
    for i, (wallet, family_label, infra_anchor) in enumerate(TARGETS):
        print(f"[{i+1}/{len(TARGETS)}] {family_label}  {wallet[:16]}…{wallet[-6:]}")
        scan_wallet(conn, wallet, family_label, infra_anchor, infra_set, treasuries, subprovs)
        families_seen.add((family_label, infra_anchor))
        print()

    # Pattern A check across all families
    print("Checking Pattern A (prep→fund) across families...")
    for family_label, infra_anchor in families_seen:
        check_prep_then_fund(conn, family_label, infra_anchor)

    print_summary(conn)
    conn.close()


if __name__ == "__main__":
    main()
