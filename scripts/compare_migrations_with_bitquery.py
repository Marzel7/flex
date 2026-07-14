#!/usr/bin/env python3
"""
Compare WATCHTOWER persisted migrations against Bitquery PumpSwap create_pool
events.

Read-only audit tool. It does not modify the local database, listener, queues,
or supervisor state.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple


PUMPSWAP_PROGRAM = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"
DEFAULT_DB_PATH = "database/flex_complete_database.db"
DEFAULT_ENDPOINT = "https://streaming.bitquery.io/eap"
DEFAULT_WINDOWS = (1, 3, 6)
DEFAULT_LIMIT = 500


BITQUERY_QUERY = """
query PumpSwapMigrations($since: DateTime!, $till: DateTime!, $limit: Int!) {
  Solana {
    Instructions(
      where: {
        Block: { Time: { since: $since, till: $till } }
        Instruction: {
          Program: {
            Address: { is: "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA" }
            Method: { is: "create_pool" }
          }
          CallerIndex: { is: 2 }
          Depth: { is: 1 }
          CallPath: { includes: 2 }
        }
      }
      orderBy: { descending: Block_Time }
      limit: { count: $limit }
    ) {
      Block {
        Time
        Slot
      }
      Transaction {
        Signature
      }
      Instruction {
        Program {
          Address
          Method
        }
        CallerIndex
        Depth
        CallPath
        Accounts {
          Address
          IsWritable
          Token {
            Mint
          }
        }
      }
    }
  }
}
""".strip()


def utc_now() -> int:
    return int(time.time())


def iso_utc(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def parse_bitquery_time(value: Optional[str]) -> Optional[int]:
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        return int(datetime.fromisoformat(text).timestamp())
    except Exception:
        return None


def fmt_ts(ts: Optional[int]) -> str:
    if not ts:
        return "unknown"
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def short(value: Optional[str], width: int = 18) -> str:
    if not value:
        return ""
    return value if len(value) <= width else value[:width]


def load_env_file(path: str = ".env") -> None:
    env_path = Path(path)
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :]
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def bitquery_token() -> str:
    load_env_file()
    for name in ("BITQUERY_API_KEY", "BITQUERY_TOKEN", "BITQUERY_ACCESS_TOKEN"):
        token = os.environ.get(name, "").strip()
        if token:
            return token
    raise RuntimeError("Missing Bitquery token. Set BITQUERY_API_KEY or BITQUERY_TOKEN.")


@dataclass(frozen=True)
class BitqueryMigration:
    signature: str
    block_time: Optional[int]
    mint: Optional[str]
    pool_accounts: Tuple[str, ...]
    raw_accounts: Tuple[str, ...]


def post_bitquery(endpoint: str, token: str, query: str, variables: Dict[str, Any]) -> Dict[str, Any]:
    payload = json.dumps({"query": query, "variables": variables}).encode("utf-8")
    auth_headers = bitquery_auth_headers(token)
    req = urllib.request.Request(
        endpoint,
        data=payload,
        headers={
            "Content-Type": "application/json",
            **auth_headers,
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=45) as resp:
        body = resp.read().decode("utf-8")
    data = json.loads(body)
    if data.get("errors"):
        raise RuntimeError(json.dumps(data["errors"], indent=2)[:4000])
    return data


def bitquery_auth_headers(token: str) -> Dict[str, str]:
    """
    Bitquery accepts OAuth bearer tokens and legacy UUID-style API keys.
    UUID-style keys must be sent as X-API-KEY.
    """
    if re.fullmatch(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", token):
        return {"X-API-KEY": token}
    return {"Authorization": f"Bearer {token}"}


def extract_accounts(instruction: Dict[str, Any]) -> Tuple[Optional[str], Tuple[str, ...], Tuple[str, ...]]:
    accounts = instruction.get("Accounts") or []
    raw_accounts: List[str] = []
    token_mints: List[str] = []
    for account in accounts:
        if not isinstance(account, dict):
            continue
        address = account.get("Address")
        if address:
            raw_accounts.append(str(address))
        token = account.get("Token") or {}
        mint = token.get("Mint") if isinstance(token, dict) else None
        if mint:
            token_mints.append(str(mint))

    # Pump.fun mints conventionally end in "pump"; prefer that when present.
    mint_hint = next((m for m in token_mints + raw_accounts if str(m).endswith("pump")), None)
    if not mint_hint and token_mints:
        mint_hint = token_mints[0]

    # The exact pool account role depends on Bitquery's account decoding. Keep the
    # account list small and useful for manual inspection.
    pool_accounts = tuple(raw_accounts[:8])
    return mint_hint, pool_accounts, tuple(raw_accounts)


def fetch_bitquery_migrations(endpoint: str, hours: int, limit: int) -> List[BitqueryMigration]:
    token = bitquery_token()
    till_ts = utc_now()
    since_ts = till_ts - hours * 3600
    data = post_bitquery(
        endpoint,
        token,
        BITQUERY_QUERY,
        {"since": iso_utc(since_ts), "till": iso_utc(till_ts), "limit": limit},
    )
    rows = (((data.get("data") or {}).get("Solana") or {}).get("Instructions") or [])
    migrations: List[BitqueryMigration] = []
    seen: Set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        sig = (((row.get("Transaction") or {}).get("Signature")) or "").strip()
        if not sig or sig in seen:
            continue
        seen.add(sig)
        block_time = parse_bitquery_time((row.get("Block") or {}).get("Time"))
        instruction = row.get("Instruction") or {}
        mint, pool_accounts, raw_accounts = extract_accounts(instruction)
        migrations.append(
            BitqueryMigration(
                signature=sig,
                block_time=block_time,
                mint=mint,
                pool_accounts=pool_accounts,
                raw_accounts=raw_accounts,
            )
        )
    migrations.sort(key=lambda item: item.block_time or 0, reverse=True)
    return migrations


def connect_readonly(db_path: str) -> sqlite3.Connection:
    path = Path(db_path).resolve()
    uri = f"file:{path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def load_watchtower_migrations(db_path: str, hours: int) -> Dict[str, sqlite3.Row]:
    since_ts = utc_now() - hours * 3600
    conn = connect_readonly(db_path)
    rows = conn.execute(
        """
        SELECT migration_tx, mint, migrated_at
        FROM token_analysis
        WHERE migrated_at >= ?
          AND migration_tx IS NOT NULL
          AND migration_tx != ''
        """,
        (since_ts,),
    ).fetchall()
    conn.close()
    return {str(row["migration_tx"]): row for row in rows if row["migration_tx"]}


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def fetch_optional_one(conn: sqlite3.Connection, sql: str, args: Sequence[Any]) -> Optional[sqlite3.Row]:
    try:
        return conn.execute(sql, args).fetchone()
    except sqlite3.Error:
        return None


def grep_logs(signature: str, log_paths: Sequence[str]) -> List[str]:
    needles = {signature, signature[:18], signature[:16]}
    matches: List[str] = []
    for path_text in log_paths:
        path = Path(path_text)
        if not path.exists() or not path.is_file():
            continue
        try:
            with path.open("r", encoding="utf-8", errors="ignore") as handle:
                for line in handle:
                    if any(needle and needle in line for needle in needles):
                        matches.append(f"{path_text}: {line.strip()[:240]}")
                        if len(matches) >= 5:
                            return matches
        except OSError:
            continue
    return matches


def diagnose_missing(db_path: str, missing: Sequence[BitqueryMigration], log_paths: Sequence[str]) -> List[Dict[str, Any]]:
    conn = connect_readonly(db_path)
    has_inbox = table_exists(conn, "migration_inbox")
    has_persist_queue = table_exists(conn, "migration_persist_queue")
    diagnostics: List[Dict[str, Any]] = []
    for event in missing:
        sig = event.signature
        inbox = None
        persist = None
        token_without_tx = None
        if has_inbox:
            inbox = fetch_optional_one(
                conn,
                "SELECT status, attempts, datetime(received_at,'unixepoch') AS received_at, last_error FROM migration_inbox WHERE signature=?",
                (sig,),
            )
        if has_persist_queue:
            persist = fetch_optional_one(
                conn,
                "SELECT status, retry_count, datetime(received_at,'unixepoch') AS received_at, last_error FROM migration_persist_queue WHERE signature=?",
                (sig,),
            )
        if event.mint:
            token_without_tx = fetch_optional_one(
                conn,
                """
                SELECT mint, lifecycle_stage, migrated_at, migration_tx
                FROM token_analysis
                WHERE mint=?
                  AND (migration_tx IS NULL OR migration_tx='')
                """,
                (event.mint,),
            )
        diagnostics.append(
            {
                "signature": sig,
                "block_time": event.block_time,
                "mint": event.mint,
                "pool_accounts": event.pool_accounts,
                "listener_logs": grep_logs(sig, log_paths),
                "migration_inbox": dict(inbox) if inbox else None,
                "migration_persist_queue": dict(persist) if persist else None,
                "token_analysis_without_migration_tx": dict(token_without_tx) if token_without_tx else None,
            }
        )
    conn.close()
    return diagnostics


def print_coverage_table(rows: Sequence[Dict[str, Any]]) -> None:
    print("\nWindow     Bitquery migrations   WATCHTOWER persisted   matched   missing   coverage")
    for row in rows:
        bq = row["bitquery_count"]
        wt = row["watchtower_count"]
        matched = row["matched"]
        missing = row["missing"]
        coverage = "n/a" if bq == 0 else f"{(matched / bq) * 100:.1f}%"
        print(f"{row['hours']:>2}h       {bq:<20} {wt:<22} {matched:<8} {missing:<8} {coverage}")


def run(args: argparse.Namespace) -> int:
    if args.print_query:
        print(BITQUERY_QUERY)
        return 0

    windows = tuple(sorted(set(args.windows)))
    by_window: Dict[int, List[BitqueryMigration]] = {}
    coverage_rows: List[Dict[str, Any]] = []

    for hours in windows:
        bitquery_events = fetch_bitquery_migrations(args.endpoint, hours, args.limit)
        watchtower = load_watchtower_migrations(args.db, hours)
        bitquery_sigs = {event.signature for event in bitquery_events}
        matched_sigs = bitquery_sigs & set(watchtower.keys())
        missing_sigs = bitquery_sigs - set(watchtower.keys())
        by_window[hours] = bitquery_events
        coverage_rows.append(
            {
                "hours": hours,
                "bitquery_count": len(bitquery_sigs),
                "watchtower_count": len(watchtower),
                "matched": len(matched_sigs),
                "missing": len(missing_sigs),
            }
        )

    print("[BITQUERY] Query used:")
    print(BITQUERY_QUERY)
    print("\n[LOCAL SQL] WATCHTOWER comparison:")
    print(
        """
SELECT migration_tx, mint, migrated_at
FROM token_analysis
WHERE migrated_at >= ?
  AND migration_tx IS NOT NULL;
""".strip()
    )
    print_coverage_table(coverage_rows)

    largest = max(windows)
    largest_events = by_window[largest]
    largest_wt = load_watchtower_migrations(args.db, largest)
    missing = [event for event in largest_events if event.signature not in largest_wt]
    if missing:
        print(f"\nMissing Bitquery migrations in last {largest}h:")
        diagnostics = diagnose_missing(args.db, missing, args.logs)
        for item in diagnostics:
            print(f"\n- signature: {item['signature']}")
            print(f"  block_time: {fmt_ts(item['block_time'])}")
            print(f"  possible_mint: {item['mint'] or 'unknown'}")
            pools = ", ".join(item["pool_accounts"][:6]) if item["pool_accounts"] else "unknown"
            print(f"  pool_accounts: {pools}")
            print(f"  seen_in_listener_logs: {'yes' if item['listener_logs'] else 'no'}")
            for line in item["listener_logs"][:3]:
                print(f"    log: {line}")
            print(f"  migration_inbox: {item['migration_inbox'] or 'no'}")
            print(f"  migration_persist_queue: {item['migration_persist_queue'] or 'no'}")
            print(
                "  token_analysis_without_migration_tx: "
                f"{item['token_analysis_without_migration_tx'] or 'no'}"
            )
    else:
        print(f"\nNo missing Bitquery migrations in last {largest}h.")

    most_recent_window = coverage_rows[0] if coverage_rows else None
    largest_window = coverage_rows[-1] if coverage_rows else None
    if largest_window and largest_window["bitquery_count"] == 0:
        print("\nConclusion: Bitquery also reports zero migrations in the largest window; low volume appears real for that window.")
    elif largest_window and largest_window["missing"] == 0:
        print("\nConclusion: WATCHTOWER matches Bitquery for the audited window; no listener miss shown by this benchmark.")
    elif largest_window:
        print("\nConclusion: Bitquery reports migrations not persisted by WATCHTOWER; investigate the missing signatures above.")

    print("\nRecommendation: surface this as an external coverage metric in /system-health after this one-shot audit is trusted.")
    return 0


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only Bitquery migration coverage audit")
    parser.add_argument("--db", default=DEFAULT_DB_PATH, help="SQLite DB path")
    parser.add_argument("--endpoint", default=os.environ.get("BITQUERY_ENDPOINT", DEFAULT_ENDPOINT))
    parser.add_argument("--windows", nargs="+", type=int, default=list(DEFAULT_WINDOWS), help="Lookback windows in hours")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="Max Bitquery rows per window")
    parser.add_argument(
        "--logs",
        nargs="+",
        default=[
            "logs/supervisor/listener.log",
            "logs/supervisor/listener.log.1",
            "logs/supervisor/listener_err.log",
        ],
        help="Listener logs to scan for missing signatures",
    )
    parser.add_argument("--print-query", action="store_true", help="Print GraphQL query and exit")
    return parser.parse_args(argv)


if __name__ == "__main__":
    try:
        raise SystemExit(run(parse_args()))
    except KeyboardInterrupt:
        raise SystemExit(130)
    except Exception as exc:
        print(f"[BITQUERY_AUDIT] ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
