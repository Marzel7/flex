"""
Phase 6: Historical Validation
==============================
Replay confirmed WATCHTOWER token launches to measure what slot the interceptor
would have landed a buy in, relative to the actual CREATE.

This validates the architecture before deploying capital.
"""

import os
import sqlite3
import json
import time
import requests
from typing import Dict, List, Optional
from dataclasses import dataclass
from datetime import datetime

_API_KEY = os.getenv("HELIUS_API_KEY", "16f1a5fc-2592-466c-a5d4-b5799ae8da96")
_RPC_HTTP = f"https://mainnet.helius-rpc.com/?api-key={_API_KEY}"

_DB_PATH = os.getenv("DB_PATH", "")
if not _DB_PATH:
    _root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    _DB_PATH = os.path.join(_root, "database", "flex_complete_database.db")


@dataclass
class LaunchReplay:
    mint: str
    creator: str
    sub_prov: str
    create_sig: str
    create_slot: int
    create_ts: float
    relay_ts: Optional[float] = None  # when relay detected
    create_detected_simulated_ts: Optional[float] = None  # simulated WebSocket notification time
    build_time_ms: float = 45.0  # estimated tx build time
    submit_time_ms: float = 5.0  # estimated RPC submit time
    network_latency_ms: float = 200.0  # simulated network round-trip

    # Calculated results
    simulated_submit_ts: Optional[float] = None
    simulated_submit_slot: Optional[int] = None
    slot_delta: Optional[int] = None  # CREATE slot - submit slot (negative = same or earlier)
    missed: bool = False  # True if submit would be too late

    def calculate_position(self, slot_per_second: float = 2.5):
        """Calculate simulated buy slot position relative to CREATE."""
        if not self.create_detected_simulated_ts or not self.create_slot:
            return

        # Total latency: detect → build → submit
        total_latency_ms = self.build_time_ms + self.submit_time_ms + self.network_latency_ms
        total_latency_s = total_latency_ms / 1000.0

        self.simulated_submit_ts = self.create_detected_simulated_ts + total_latency_s

        # Estimate slot at submit time
        # slot = create_slot + (submit_ts - create_ts) * slot_per_second
        time_after_create = self.simulated_submit_ts - self.create_ts
        slot_distance = time_after_create * slot_per_second
        self.simulated_submit_slot = int(self.create_slot + slot_distance)
        self.slot_delta = self.create_slot - self.simulated_submit_slot

        # Missed if we're submitting >2 slots after CREATE
        self.missed = self.slot_delta < -2


class HistoricalValidator:
    def __init__(self, db_path: str = None):
        self.db_path = db_path or _DB_PATH
        self.launches: List[LaunchReplay] = []
        self.same_slot = 0
        self.next_slot = 0
        self.plus_two = 0
        self.missed = 0

    def load_confirmed_launches(self) -> List[Dict]:
        """
        Load all known confirmed WATCHTOWER launches from the database.
        Sources: wt_detected_creates if available, or hardcoded known launches.
        """
        known = [
            {
                "mint": "CUdwRcEH2fqEuKQkALbHzpv81XUKbEamCEPreHSBpump",
                "creator": "8RW8MeyB9AzBS9TiZtTtuCh6yzib6PLrC7bRtmh3bfJe",
                "sub_prov": "DzRrCaXNDG5usCo4oEtAPW8wVrEAwysVddgobrdUjXJ1",
                "create_sig": "5fyyCmfAYhHS898pPedHuEoEJpdrusofsgBRycPxazQ7BC21CVSQPiUGSAE4EwxhKq3xKJL77hQ1ZV5rbXpFdYUr",
                "create_ts": 1780261007.0,  # 20:56:47
                "relay_ts": 1780261006.0,  # relay at 20:56:46
                "token": "Gaynald Trump",
            },
            {
                "mint": "8AYsSaPyptd6dgQ1dvXsEbPZMuzM6MMRQXAJM9pQpump",
                "creator": "6NV84W76QUxAicY4dGACtuuTfCr6QJU3ZfyRmRP6CgY5",
                "sub_prov": "2ujRcf1fwQjW8cjUPK6krBJBMdbiMiSKvNscYjdbFW6R",
                "create_sig": "9ri9eZHwn7LFv5PFtjodwrgKex4KAkRXtsnTN3oZB1219t2t8aSB6fFbpc5xMMTRF1ebC8jY6gGwd6GFfGi9x89",
                "create_ts": 1780133008.0,  # 09:23:28
                "relay_ts": 1780135710.0,  # relay at 10:08:30 (after create — pattern broken)
                "token": "TRUMPCUM",
                "note": "Creator pre-existing — relay is buy-in, not create seed",
            },
            {
                "mint": "3Cj1XSskaWrKMo2xN4ucnUi94JFZXTSePGAv4sZApump",
                "creator": "HLucJQyQy6XmiudWYE5XA4t5y8o5WAJr1CoFE2BsFA2a",
                "sub_prov": "8U7zfBcS7UWhpHiQLvExLNd6tvtEsGFX1MP1N8QhmoPK",
                "create_sig": "mxHQJfoELLLywnpcvbPiSRfz97bGxr7GaTkEtJ2BKSF9f8E2GbWgG35SFvGsTZMDVEkFRLdqjMEk6JLbSDNGkrh",
                "create_ts": 1780328196.0,  # 15:36:36
                "relay_ts": 1780327840.0,  # relay at 15:30:40 (5.9 min before)
                "token": "Sellategy",
            },
        ]

        # Try to load from DB if available
        try:
            conn = sqlite3.connect(self.db_path, timeout=5)
            rows = conn.execute("""
                SELECT mint, creator, bonding_curve, slot, signature, detected_at,
                       relay_detected_at, creator_funded_at, create_seen_at
                FROM wt_detected_creates
                ORDER BY detected_at DESC
            """).fetchall()
            conn.close()

            if rows:
                for row in rows:
                    known.append({
                        "mint": row[0],
                        "creator": row[1],
                        "sub_prov": None,  # not in this table
                        "create_sig": row[4],
                        "create_ts": row[5],
                        "relay_ts": row[6],
                        "token": f"mint:{row[0][:10]}",
                    })
        except Exception as e:
            print(f"[VALIDATOR] DB load error (using hardcoded): {e}")

        return known

    def fetch_create_slot(self, sig: str) -> Optional[int]:
        """Fetch actual slot number for a CREATE transaction."""
        try:
            resp = requests.post(_RPC_HTTP, json={
                "jsonrpc": "2.0", "id": 1,
                "method": "getTransaction",
                "params": [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}]
            }, timeout=5)
            tx = resp.json().get("result")
            if tx:
                return tx.get("slot")
        except Exception as e:
            print(f"[VALIDATOR] fetch_create_slot error: {e}")
        return None

    def replay(self, build_time_ms: float = 45.0,
               network_latency_ms: float = 200.0,
               slot_per_second: float = 2.5):
        """
        Simulate interceptor execution against historical launches.

        Args:
            build_time_ms: how long to build the tx template
            network_latency_ms: simulated RPC round-trip time
            slot_per_second: Solana slot frequency (normally ~2.5)
        """
        launches = self.load_confirmed_launches()
        print(f"\n[VALIDATOR] Loading {len(launches)} confirmed WATCHTOWER launches...")

        for launch in launches:
            print(f"\n{'─'*70}")
            print(f"Token: {launch.get('token', 'unknown')}")
            print(f"Mint: {launch['mint'][:20]}...")
            print(f"Creator: {launch['creator'][:20]}...")

            # Fetch actual CREATE slot
            create_slot = self.fetch_create_slot(launch["create_sig"])
            if not create_slot:
                print(f"⚠️  Could not fetch slot for {launch['create_sig'][:20]}... — skipping")
                continue

            create_ts = launch["create_ts"]
            relay_ts = launch.get("relay_ts")

            # Simulate WebSocket notification latency
            # In practice: Helius WSS latency ~200ms, RPC fetch ~100ms
            # Total: ~300ms from CREATE block to our logsSubscribe notification
            ws_detect_latency = 0.3  # 300ms
            create_detected_ts = create_ts + ws_detect_latency

            replay = LaunchReplay(
                mint=launch["mint"],
                creator=launch["creator"],
                sub_prov=launch.get("sub_prov", "unknown"),
                create_sig=launch["create_sig"],
                create_slot=create_slot,
                create_ts=create_ts,
                relay_ts=relay_ts,
                create_detected_simulated_ts=create_detected_ts,
                build_time_ms=build_time_ms,
                network_latency_ms=network_latency_ms,
            )
            replay.calculate_position(slot_per_second)
            self.launches.append(replay)

            # Print results
            relay_gap = ""
            if relay_ts:
                relay_gap_s = create_ts - relay_ts
                relay_gap = f" (relay {relay_gap_s:.1f}s before)"

            print(f"CREATE slot: {create_slot}{relay_gap}")
            print(f"Simulated submit slot: {replay.simulated_submit_slot}")
            print(f"Slot delta: {replay.slot_delta} (negative = early, 0 = same slot, positive = late)")

            if replay.slot_delta is None:
                print(f"❌ Could not calculate position")
            elif replay.slot_delta <= 0:
                print(f"✓ SAME SLOT (delta {replay.slot_delta})")
                self.same_slot += 1
            elif replay.slot_delta == 1:
                print(f"✓ +1 SLOT (delta {replay.slot_delta})")
                self.next_slot += 1
            elif replay.slot_delta == 2:
                print(f"⚠️  +2 SLOTS (delta {replay.slot_delta})")
                self.plus_two += 1
            else:
                print(f"❌ MISSED (delta {replay.slot_delta})")
                self.missed += 1
                replay.missed = True

        self.print_summary()

    def print_summary(self):
        """Print final results."""
        if not self.launches:
            print("\n[VALIDATOR] No launches to analyze")
            return

        total = len(self.launches)
        total_hit = self.same_slot + self.next_slot
        success_rate = (total_hit / total * 100) if total else 0

        print(f"\n{'━'*70}")
        print(f"HISTORICAL VALIDATION SUMMARY")
        print(f"{'━'*70}")
        print(f"Total launches analyzed: {total}")
        print(f"Same slot (-): {self.same_slot} ({self.same_slot/total*100:.1f}%)")
        print(f"Next slot (+1): {self.next_slot} ({self.next_slot/total*100:.1f}%)")
        print(f"Plus two slots (+2): {self.plus_two} ({self.plus_two/total*100:.1f}%)")
        print(f"Missed (>+2): {self.missed} ({self.missed/total*100:.1f}%)")
        print(f"\n🎯 Success Rate (same/next slot): {success_rate:.1f}%")
        print(f"{'━'*70}\n")

        if success_rate >= 80:
            print("✅ ARCHITECTURE VALIDATED — Ready to deploy capital")
        elif success_rate >= 50:
            print("⚠️  MARGINAL — May need latency optimization (faster build/network)")
        else:
            print("❌ INSUFFICIENT — Need architecture redesign")

    def export_csv(self, path: str = "interceptor_validation.csv"):
        """Export detailed results to CSV."""
        import csv
        try:
            with open(path, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "Token", "Mint", "Creator", "SUB_PROV",
                    "CREATE_Slot", "CREATE_Ts", "Relay_Ts",
                    "Simulated_Submit_Slot", "Slot_Delta", "Result"
                ])
                for replay in self.launches:
                    result = "HIT" if not replay.missed else "MISS"
                    if replay.slot_delta is not None:
                        if replay.slot_delta <= 0:
                            result = "SAME_SLOT"
                        elif replay.slot_delta == 1:
                            result = "NEXT_SLOT"
                        elif replay.slot_delta == 2:
                            result = "+2_SLOTS"

                    relay_ts = datetime.utcfromtimestamp(replay.relay_ts).isoformat() if replay.relay_ts else ""
                    create_ts_str = datetime.utcfromtimestamp(replay.create_ts).isoformat()

                    writer.writerow([
                        replay.mint[:20],  # truncated for readability
                        replay.mint,
                        replay.creator[:20],
                        replay.sub_prov[:20] if replay.sub_prov else "",
                        replay.create_slot,
                        create_ts_str,
                        relay_ts,
                        replay.simulated_submit_slot,
                        replay.slot_delta,
                        result,
                    ])
            print(f"[VALIDATOR] Results exported to {path}")
        except Exception as e:
            print(f"[VALIDATOR] CSV export error: {e}")


def main():
    """Run full validation."""
    import argparse
    parser = argparse.ArgumentParser(description="WATCHTOWER historical validation")
    parser.add_argument("--build-ms", type=float, default=45.0,
                       help="Tx build time in ms (default 45)")
    parser.add_argument("--latency-ms", type=float, default=200.0,
                       help="Network latency in ms (default 200)")
    parser.add_argument("--slots-per-sec", type=float, default=2.5,
                       help="Solana slot frequency (default 2.5)")
    parser.add_argument("--export-csv", action="store_true",
                       help="Export results to CSV")
    parser.add_argument("--db", type=str, default=None,
                       help="Database path (default from env)")

    args = parser.parse_args()

    validator = HistoricalValidator(db_path=args.db)
    validator.replay(
        build_time_ms=args.build_ms,
        network_latency_ms=args.latency_ms,
        slot_per_second=args.slots_per_sec,
    )

    if args.export_csv:
        validator.export_csv()


if __name__ == "__main__":
    main()
