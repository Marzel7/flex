#!/usr/bin/env python3
"""Parity-gated, one-wallet/eleven-mint topology promotion executor."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sqlite3
import time

from src.core.watchtower_registry_promotion import (
    project_watchtower_confirmed_membership,
    promote_walkback_confirmed_watchtower,
)
from src.ops.treasury_topology_classifier import (
    classify_unknown_treasury,
    confirm_topology_candidate,
    replay_topology_candidate,
)
from src.utils.db_locking import db_connect
from src.utils.infra_mapping import is_known_account


WALLET = "GzaaMeT8osXc71tFhVZ8pgtDv9dy3nPK7xKMkDZm7zac"
MINTS = (
    "2cXEPqkLAEdYCw6aoRVRddxGB8gRUM7PWZoLXUwXpump",
    "3tUn8dGmDceJ9pCwFLUVo2jkD6FLWaZ78YEpAa3bpump",
    "5qdo3zMKtvh8J7iHSyL6xRH1qZ5aK83KLFLiKnoLpump",
    "6Fs5Ar1Qn291Xs1PZiuBJ85x13DEZQJc9cr7X4U5pump",
    "784rob2DZqKnGPMfDJf9kRhuFK2FtDNZAhXRo5Ykpump",
    "9SJ85ox8LW1v8agNcBMjT2WvbAeSdWrDLfJu6TRRpump",
    "9iFYQUZrB5BwA8m4zydUA3R9QND9KaUyAS19aXK1pump",
    "B26VSXMfTJWtWWGA9gspZY5ujP8w183ndCrzTYCnpump",
    "Btw9zgKfXV71WNsFDGnQ1yFnbEUajciukURwJ7drpump",
    "DcecmqrqWvZRh5eYrEBQMNiBvZWxhp7GZF9nXWSrpump",
    "GTLyz3y3J2Sdedo389kR7Q7evL8KmPNyjg5jp9Arpump",
)


def _rows(conn, table: str, where: str, params: tuple[str, ...]) -> list[dict]:
    return [dict(r) for r in conn.execute(f"SELECT * FROM {table} WHERE {where}", params)]


def snapshot(conn) -> dict:
    placeholders = ",".join("?" for _ in MINTS)
    mint_where = f"mint IN ({placeholders})"
    return {
        "captured_at": int(time.time()),
        "wallet": WALLET,
        "mints": list(MINTS),
        "wt_confirmed_treasuries": _rows(conn, "wt_confirmed_treasuries", "treasury=?", (WALLET,)),
        "wt_treasury_fingerprint_decisions": _rows(
            conn, "wt_treasury_fingerprint_decisions", "wallet=?", (WALLET,)
        ),
        "wt_walkback_queue": _rows(conn, "wt_walkback_queue", mint_where, MINTS),
        "watchtower_token_attribution": _rows(conn, "watchtower_token_attribution", mint_where, MINTS),
        "wt_attribution_outcomes": _rows(conn, "wt_attribution_outcomes", mint_where, MINTS),
        "wt_watchtower_launches": _rows(conn, "wt_watchtower_launches", mint_where, MINTS),
        "operator_launch_membership": _rows(conn, "operator_launch_membership", mint_where, MINTS),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--core-db", required=True)
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    conn = db_connect(os.path.realpath(args.db), timeout=30, row_factory=sqlite3.Row)
    core = db_connect(
        os.path.realpath(args.core_db), timeout=30, row_factory=sqlite3.Row, read_only=True,
    )
    try:
        candidate = classify_unknown_treasury(
            conn, WALLET, infrastructure_check=is_known_account, _allow_confirmed_topology=True,
        )
        if candidate.get("verdict") != "QUALIFIED_TOPOLOGY":
            raise SystemExit(f"ABORT candidate={candidate.get('verdict')}")
        fresh_mints = {c["mint"] for c in candidate["chains"]}
        if not set(MINTS).issubset(fresh_mints):
            raise SystemExit("ABORT manifest is not a subset of fresh evidence")
        before = snapshot(conn)
        Path(args.snapshot).write_text(json.dumps(before, indent=2, sort_keys=True, default=str) + "\n")
        if not args.apply:
            print(json.dumps({"verdict": "DRY_RUN_PASS", "count": len(MINTS)}))
            return 0

        timestamp = int(time.time())
        confirmation = confirm_topology_candidate(
            conn, candidate, now=timestamp, infrastructure_check=is_known_account,
        )
        if confirmation.get("verdict") not in {"CONFIRMED", "ALREADY_CONFIRMED"}:
            raise RuntimeError(f"treasury confirmation failed: {confirmation}")
        replay = replay_topology_candidate(
            conn, candidate, allowed_mints=MINTS, now=timestamp,
            infrastructure_check=is_known_account, core_conn=core,
        )
        conn.commit()

        projections = []
        for mint in MINTS:
            outcome = conn.execute(
                "SELECT outcome_type,operator_id,evidence_json,completed_at "
                "FROM wt_attribution_outcomes WHERE mint=?", (mint,),
            ).fetchone()
            evidence = json.loads(outcome["evidence_json"]) if outcome else {}
            registry = promote_walkback_confirmed_watchtower(
                conn, mint, outcome_type=outcome["outcome_type"] if outcome else None,
                operator_id=outcome["operator_id"] if outcome else None,
                evidence=evidence, completed_at=outcome["completed_at"] if outcome else None,
                core_conn=core,
            )
            membership = project_watchtower_confirmed_membership(
                conn, mint, core_db_path=args.core_db, now=timestamp, refresh_activity=False,
            )
            if registry["action"] not in {"promoted", "already_present"}:
                raise RuntimeError(f"registry projection failed: {registry}")
            if membership["action"] not in {"projected", "already_present"}:
                raise RuntimeError(f"membership projection failed: {membership}")
            projections.append({"mint": mint, "registry": registry["action"], "membership": membership["action"]})
        conn.commit()
        print(json.dumps({
            "verdict": "APPLIED", "confirmation": confirmation["verdict"],
            "replayed": replay["count"], "projections": projections,
        }, sort_keys=True))
        return 0
    finally:
        core.close()
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
