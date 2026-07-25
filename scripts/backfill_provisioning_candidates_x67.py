"""X67.3 — Backfill the original 19-launch investigation cohort into
wt_provisioning_candidate_workflow.

Idempotent, dry-run capable (default). Reconciles the exact set of 19 mints
against the classification established in X67.0/X67.1 before writing anything;
refuses to run if the set is incomplete or the counts don't match 6/2/9/2.

Usage:
    python3 scripts/backfill_provisioning_candidates_x67.py            # dry run
    python3 scripts/backfill_provisioning_candidates_x67.py --apply    # writes

This script only ever writes to wt_provisioning_candidate_workflow (additive,
new table). It never writes wt_watchtower_launches, wt_confirmed_treasuries,
or any other Model 1 table.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.ops.provisioning_candidates_workflow import ensure_schema, VALID_CLOSURE_REASONS

OPS_DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "database", "wt_ops_v2.db")

# ── Reconciled cohort (X65.85/96/X67.0/X67.1 — do not silently edit) ────────

PENDING_VERIFICATION = {
    "7z4cgsb7egGx4iWXioU5agYP2cU5tyoXZakSCxafpump": {
        "creator": "GznEnJ571FDYjA7L1ABUFsjHeZDQu3RuVpASYEwV2KkJ",
        "subprov_wallet": "3jjFDWjfcTDZv5UeENSA2bypNRXRZoWroAJCDkdUz751",
        "funding_mechanism": "WSOL_WRAP_CLOSE",
        "session_treasury": "4231KLYipwRTmFdQ6ZBa1H4Jf3EpfF62Gzg6DWHWvhPZ",
    },
    "3gosQAi7WAKRnkCibW2hamv9NkVvFLXePAPQZb5Gpump": {
        "creator": "4r66fqHWXcYyaosoohfsvZsvBtV7HNzaurP9B3ys3XJR",
        "subprov_wallet": "3wFKaqt9ZiYrjVTQEnP32FTFftEWEmZ1Vi9YFLVoAih5",
        "funding_mechanism": "WSOL_WRAP_CLOSE",
        "session_treasury": "4231KLYipwRTmFdQ6ZBa1H4Jf3EpfF62Gzg6DWHWvhPZ",
    },
    "HqDzBCPHMNKu66kBoKFgrJWiSqYP7VS4LcSqTnBXpump": {
        "creator": "GAkFWY9pGkFKatxZneqaTCMeRazUEVmFW68z4GFTMr9s",
        "subprov_wallet": "AaZkwhkiDStDcgrU37XAj9fpNLrD8Erz5PNkdm4k5hjy",
        "funding_mechanism": "SEEDED_ACCOUNT_CLOSE",
        "session_treasury": "DchJquEZzM6VqBaxhA9i7r3qAUngPggQJHoTBhwdFEUK",
    },
    "9QLyikZbyjmv9FNnJzjhTFEoVPuap1cWmaHYhvrqpump": {
        "creator": "G4hK51m4FwUsPKcsXpzwJRK3iix2mujcHjbddBR2WTbW",
        "subprov_wallet": "ERKZ8eHsRN38QsDjh6AZDX6YXemDsvJ29srj3a4vA7yz",
        "funding_mechanism": "WSOL_WRAP_CLOSE",
        "session_treasury": "69SNcRC8NqjHBSXEcugCN5oFKRQoKmddmWzZYc3tqtxk",
    },
    "9Pp8MeVxT5kuCx12YqNMTG39GwJxazzJr4Ettenkpump": {
        "creator": "5tGznhvAVvtodSbgRftCG3TLWgEGN2JxcGAMXj7qmJY6",
        "subprov_wallet": "FncazAs6omJJjtLVzquzT9KoyXn6tFixr9kGjr42ktLj",
        "funding_mechanism": "WSOL_WRAP_CLOSE",
        "session_treasury": "69SNcRC8NqjHBSXEcugCN5oFKRQoKmddmWzZYc3tqtxk",
    },
    "wGEyTQEyhE5sQUTsq9f2A3nnEu2EPTsAetmurRCpump": {
        "creator": "3FV5tvxyxgEhio847CU6pj8ZcDz4XiNZeTdtJP6PCjhM",
        "subprov_wallet": "Eko1xSSigAj1rj6uCYZMKgBgyQFVtbdzyotRvfFuywae",
        "funding_mechanism": "WSOL_WRAP_CLOSE",
        "session_treasury": "4231KLYipwRTmFdQ6ZBa1H4Jf3EpfF62Gzg6DWHWvhPZ",
    },
}

TREASURY_VERIFIED = {
    "HJQC4xW9k3gxstQ65UjAwq7D9EQ38NaJiQNayjPopump": {
        "creator": "31CL6WhUHs6iDzSezHay9dB2Gkr9VFYceHjvLGHoKgWn",
        "subprov_wallet": "HMZ7ACDXnpdPBcwAHtcAT8vt76Fro2NwXjdnh26zaKpy",
        "funding_mechanism": "WSOL_WRAP_CLOSE",
        "verified_treasury": "4231KLYipwRTmFdQ6ZBa1H4Jf3EpfF62Gzg6DWHWvhPZ",
        "evidence": {
            "treasury_to_subprov_signature": "2UWkBBQoFX8BKDA21UNhebo5mJR8zAB1WygS7q6erqbYnS27RmdGTwyp6yPuEDLuxcfcrLbvizFA1jyuo8ahRmmz",
            "wrap_close_signature": "2FCwXsEuxr6JfvHSuLifPbF59P2TNLcbzoqjxmKorbiNbNcXzJCbiU1bFrT7gXEqVi89kP2X4vu7F63BirMKbRqT",
            "funding_amount": 1189.0,
            "lineage_gap_seconds": 123,
        },
    },
    "Af72QENbvReeKywXQvi3GRgfWbKF8LdncwCjoQ9npump": {
        "creator": "BPzKAWcrNiiyfAUd2wCREANoonuUdQJT7Wmfgaf3WnZe",
        "subprov_wallet": "4J7uc7eGHQJLtTRHkrCreEEnqJK4tTRyASjpPW1LnLd8",
        "funding_mechanism": "WSOL_WRAP_CLOSE",
        "verified_treasury": "FkccGTEh6tJe7FGg3hk1dMsz67FDKr5aMh6CWYnTu1f8",
        "evidence": {
            "treasury_to_subprov_signature": "26wsVfR4ar8ZLE9ozp4i7ZK6etjaFrTXEoVb6XvJ499JzbGJD7FsCmMJn9cjWac3nZWAWeyaFKmn4mxmvVL9p14X",
            "wrap_close_signature": "2t8qvxUTFL5SKT7hKPvuLtsziDKbfXUnK9kdf6oMzxYmM3AVTihh8i8DF1grwkzXqgkdm6ZUCNa8tjYteCJNEjNe",
            "funding_amount": 1200.0,
            "lineage_gap_seconds": 64,
        },
    },
}

INVESTIGATION_CLOSED = {
    "H98z4JrinRSbrdwyZzhbS6cGu1fd7njM37E5vm75EYH6": {
        "creator": "84UU3QuQQdCtHTZSzfKr5WnePFKjo78PrCZSZN1JHuQp",
        "subprov_wallet": "Dv34prGm2BT7Ph2n6qKLgzeLgjnii87RJJ7Db6ZQQvKM",
        "funding_mechanism": "WSOL_WRAP_CLOSE",
        "closure_reason": "EXCHANGE_BOUNDARY",
        "closure_note": "Immediate funder independently RPC-confirmed as Binance 2, not a WATCHTOWER treasury",
    },
    "4fmhCviNejMSrzAYMfyyAsoa4mJvWoFMLqxbRz8Bpump": {
        "creator": "Ww9M1n1bCAPRV7ekHaxTD2KpUfPR1e9iquU5ANPX9uP",
        "subprov_wallet": "Dv34prGm2BT7Ph2n6qKLgzeLgjnii87RJJ7Db6ZQQvKM",
        "funding_mechanism": "WSOL_WRAP_CLOSE",
        "closure_reason": "EXCHANGE_BOUNDARY",
        "closure_note": "Immediate funder independently RPC-confirmed as Binance 2, not a WATCHTOWER treasury",
    },
    "91ykjLVJPqknwAoHKESqqBVmgdpH5PJqe8AZXGHWpump": {
        "creator": "4ExeHvDVoBvgzHKzXEkWwWqv646tYeY4TE93MUtXcACf",
        "subprov_wallet": "Dv34prGm2BT7Ph2n6qKLgzeLgjnii87RJJ7Db6ZQQvKM",
        "funding_mechanism": "WSOL_WRAP_CLOSE",
        "closure_reason": "EXCHANGE_BOUNDARY",
        "closure_note": "Immediate funder independently RPC-confirmed as Binance 2, not a WATCHTOWER treasury",
    },
    "53tPFYDtc5m6F7xCiDCNHeYdit5iLF4E8QSD7Nuypump": {
        "creator": "Heo8ULNR3B8La3YbyEhBFRNMu8cWRQ9rqTuyzpUigBGq",
        "subprov_wallet": "Dv34prGm2BT7Ph2n6qKLgzeLgjnii87RJJ7Db6ZQQvKM",
        "funding_mechanism": "WSOL_WRAP_CLOSE",
        "closure_reason": "EXCHANGE_BOUNDARY",
        "closure_note": "Immediate funder independently RPC-confirmed as Binance 2, not a WATCHTOWER treasury",
    },
    "12uxfjozqd7iWKKAb6FY7pCE9SSnnQxB4MhfYy7Ypump": {
        "creator": "4hJuGNoTG4qESjr9VemUKmpdyWe8pDhH6a6xNWru3jQs",
        "subprov_wallet": "Dv34prGm2BT7Ph2n6qKLgzeLgjnii87RJJ7Db6ZQQvKM",
        "funding_mechanism": "WSOL_WRAP_CLOSE",
        "closure_reason": "EXCHANGE_BOUNDARY",
        "closure_note": "Immediate funder independently RPC-confirmed as Binance 2, not a WATCHTOWER treasury",
    },
    "6ANRcu9SxHyWr5MCbBWLehYzgVPhMrS9j9sszCxfpump": {
        "creator": "CiDVSrE3qx73goBem7bCDQocekstkuDLs7AJZ6x4y146",
        "subprov_wallet": "B48kNVXs4YK4amkBCH2XokQiv1SeiVQGHDR17xDeKAAn",
        "funding_mechanism": "WSOL_WRAP_CLOSE",
        "closure_reason": "MULTI_SOURCE_RELAY",
        "closure_note": "Immediate funder (6a1EevkQZHL...) does not match this subprovider's other launches",
    },
    "FB44zC6s2jkysjaB2NC8u6XqwhPJwir1DYFzEhXbpump": {
        "creator": "2Vgmp9bTNzvj1D5XaFNdUtNDxaRFH2UGPJXrcUPVGj1B",
        "subprov_wallet": "B48kNVXs4YK4amkBCH2XokQiv1SeiVQGHDR17xDeKAAn",
        "funding_mechanism": "WSOL_WRAP_CLOSE",
        "closure_reason": "MULTI_SOURCE_RELAY",
        "closure_note": "Immediate funder (AcALUUfV5Hs...) does not match this subprovider's other launches",
    },
    "BNz8HBXTkYUtsn22fZSzu3Fb461AttKwScGgwHR7a5sp": {
        "creator": "9DPZGyDw3gg1vrd9nvPA6xsj86wdYEVugfSUbscw9ahx",
        "subprov_wallet": "B48kNVXs4YK4amkBCH2XokQiv1SeiVQGHDR17xDeKAAn",
        "funding_mechanism": "WSOL_WRAP_CLOSE",
        "closure_reason": "MULTI_SOURCE_RELAY",
        "closure_note": "Immediate funder (AURpTD49Xbr...) does not match this subprovider's other launches",
    },
    "73Ldwtam8mZZALK4veHMDsnMBcsPJMQcapaYk8bHpump": {
        "creator": "DQtYptCD4HEyKyqWTqKsEj8jNvWHBo8FovDypjCGaRKw",
        "subprov_wallet": "B48kNVXs4YK4amkBCH2XokQiv1SeiVQGHDR17xDeKAAn",
        "funding_mechanism": "WSOL_WRAP_CLOSE",
        "closure_reason": "INSUFFICIENT_EVIDENCE",
        "closure_note": "Upstream funder not further traced past the subprovider -- closed pending new evidence",
    },
}

# Mechanism-excluded from the WATCHTOWER queue entirely (PLAIN_XFER). May still
# be visible in Shared Provisioning Intelligence (a separate view; not written
# by this script).
EXCLUDED_WRONG_MECHANISM = {
    "6Fs99bqVxGnuvBk9B1aM6PxTYuP321sjrq7zPJmfpump",
    "GjnQytRZEueWpAutSgxt6nutaNfehL6zzegP4Gwfpump",
}


def reconcile() -> None:
    all_sets = [set(PENDING_VERIFICATION), set(TREASURY_VERIFIED), set(INVESTIGATION_CLOSED),
                EXCLUDED_WRONG_MECHANISM]
    counts = [len(s) for s in all_sets]
    if counts != [6, 2, 9, 2]:
        raise SystemExit(f"REFUSING BACKFILL: expected counts [6,2,9,2], got {counts}")
    union = set().union(*all_sets)
    if len(union) != 19:
        raise SystemExit(f"REFUSING BACKFILL: expected 19 unique mints, got {len(union)}")
    overlap = set()
    for i, a in enumerate(all_sets):
        for b in all_sets[i + 1:]:
            overlap |= (a & b)
    if overlap:
        raise SystemExit(f"REFUSING BACKFILL: mint(s) appear in more than one category: {overlap}")
    print(f"Reconciliation OK: 6 pending + 2 verified + 9 closed + 2 excluded = {len(union)} unique mints")


def run(apply: bool) -> None:
    reconcile()
    conn = sqlite3.connect(OPS_DB_PATH)
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    now = int(time.time())

    plan = []
    for mint, d in PENDING_VERIFICATION.items():
        plan.append(("PENDING_VERIFICATION", mint, d))
    for mint, d in TREASURY_VERIFIED.items():
        plan.append(("TREASURY_VERIFIED", mint, d))
    for mint, d in INVESTIGATION_CLOSED.items():
        plan.append(("INVESTIGATION_CLOSED", mint, d))

    for state, mint, d in plan:
        existing = conn.execute(
            "SELECT workflow_state FROM wt_provisioning_candidate_workflow WHERE mint=?", (mint,)
        ).fetchone()
        if existing:
            print(f"SKIP (already present, state={existing['workflow_state']}): {mint[:14]}")
            continue
        print(f"{'APPLY' if apply else 'DRY-RUN'}: {mint[:14]} -> {state}")
        if not apply:
            continue
        import json
        if state == "PENDING_VERIFICATION":
            conn.execute(
                "INSERT INTO wt_provisioning_candidate_workflow "
                "(mint, workflow_state, discovered_at, updated_at, creator, subprov_wallet, "
                " funding_mechanism, session_treasury, reconstructed) "
                "VALUES (?, 'PENDING_VERIFICATION', ?, ?, ?, ?, ?, ?, 1)",
                (mint, now, now, d["creator"], d["subprov_wallet"], d["funding_mechanism"],
                 d["session_treasury"]),
            )
        elif state == "TREASURY_VERIFIED":
            conn.execute(
                "INSERT INTO wt_provisioning_candidate_workflow "
                "(mint, workflow_state, discovered_at, updated_at, creator, subprov_wallet, "
                " funding_mechanism, verified_treasury, lineage_gap_seconds, "
                " verification_attempted_at, verification_outcome, evidence_json, "
                " attribution_source, reconstructed) "
                "VALUES (?, 'TREASURY_VERIFIED', ?, ?, ?, ?, ?, ?, ?, ?, 'PASS', ?, "
                " 'SESSION_HINT_RPC_VERIFIED', 1)",
                (mint, now, now, d["creator"], d["subprov_wallet"], d["funding_mechanism"],
                 d["verified_treasury"], d["evidence"]["lineage_gap_seconds"], now,
                 json.dumps(d["evidence"])),
            )
        elif state == "INVESTIGATION_CLOSED":
            conn.execute(
                "INSERT INTO wt_provisioning_candidate_workflow "
                "(mint, workflow_state, discovered_at, updated_at, creator, subprov_wallet, "
                " funding_mechanism, verification_attempted_at, verification_outcome, "
                " closure_reason, closure_note, reconstructed) "
                "VALUES (?, 'INVESTIGATION_CLOSED', ?, ?, ?, ?, ?, ?, 'FAIL', ?, ?, 1)",
                (mint, now, now, d["creator"], d["subprov_wallet"], d["funding_mechanism"], now,
                 d["closure_reason"], d["closure_note"]),
            )

    if apply:
        conn.commit()
        rows = conn.execute(
            "SELECT workflow_state, COUNT(*) c FROM wt_provisioning_candidate_workflow "
            "WHERE reconstructed=1 GROUP BY workflow_state"
        ).fetchall()
        print("\nPost-backfill counts (reconstructed rows only):")
        for r in rows:
            print(f"  {r['workflow_state']}: {r['c']}")
    conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Write changes (default is dry-run)")
    args = parser.parse_args()
    run(apply=args.apply)
