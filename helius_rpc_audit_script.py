#!/usr/bin/env python3
"""
Helius vs Local RPC Metrics Audit Script

Compares Helius billed RPC credits against locally tracked RPC metrics.
Runs creator and funder extraction jobs, capturing before/after metrics and deltas.
"""

import sqlite3
import json
import csv
import subprocess
import time
import sys
from datetime import datetime
from typing import Dict, List, Optional, Tuple

# Configuration
FLEX_DB = "flex_complete_database.db"
METRICS_DB = FLEX_DB  # Same database
HELIUS_API_TIMEOUT = 10
WAIT_TIME_SECONDS = 60
CREATOR_ITERATIONS = 3
FUNDER_ITERATIONS = 3

# Output files
AUDIT_JSON = "helius_audit_results.json"
AUDIT_CSV = "helius_audit_results.csv"


class HeliusAudit:
    """Audit script for comparing Helius vs local RPC metrics"""

    def __init__(self):
        self.results: List[Dict] = []
        self.selected_creator: Optional[str] = None
        self.start_time = datetime.now()

    def get_helius_usage(self) -> Optional[int]:
        """Fetch current Helius usage from CLI"""
        try:
            from helius_cli_monitor import get_helius_usage_cli

            usage_data = get_helius_usage_cli()
            if not usage_data:
                print(f"❌ Failed to get usage from CLI", file=sys.stderr)
                return None

            credits_used = usage_data.get("credits_used", 0)
            print(f"  ✅ Helius credits_used: {credits_used}")
            return credits_used

        except Exception as e:
            print(f"❌ Failed to fetch Helius usage: {e}", file=sys.stderr)
            return None

    def get_local_metrics_summary(self, since_timestamp: Optional[float] = None) -> Dict:
        """Get local RPC metrics from database, optionally since a timestamp"""
        try:
            conn = sqlite3.connect(METRICS_DB, timeout=5)
            cursor = conn.cursor()

            # Build query with optional timestamp filter
            where_clause = ""
            params = []
            if since_timestamp is not None:
                where_clause = "WHERE timestamp > ? - 0.5"  # Account for small time drift
                params = [since_timestamp]

            # Total credits - use whole table if no filter, avoids timestamp drift issues
            if since_timestamp is None:
                cursor.execute("SELECT SUM(credits) FROM rpc_metrics")
            else:
                cursor.execute("SELECT SUM(credits) FROM rpc_metrics WHERE timestamp > ?", params)
            total_credits = cursor.fetchone()[0] or 0

            # Total RPC calls
            if since_timestamp is None:
                cursor.execute("SELECT COUNT(*) FROM rpc_metrics")
            else:
                cursor.execute("SELECT COUNT(*) FROM rpc_metrics WHERE timestamp > ?", params)
            total_calls = cursor.fetchone()[0] or 0

            # Credits by source_file
            if since_timestamp is None:
                cursor.execute("""
                    SELECT source_file, COUNT(*), SUM(credits)
                    FROM rpc_metrics
                    GROUP BY source_file
                    ORDER BY SUM(credits) DESC
                """)
            else:
                cursor.execute("""
                    SELECT source_file, COUNT(*), SUM(credits)
                    FROM rpc_metrics
                    WHERE timestamp > ?
                    GROUP BY source_file
                    ORDER BY SUM(credits) DESC
                """, params)
            by_source_file = {row[0]: {"calls": row[1], "credits": row[2]} for row in cursor.fetchall()}

            # Credits by method
            cursor.execute(f"""
                SELECT method, COUNT(*), SUM(credits)
                FROM rpc_metrics
                {where_clause}
                GROUP BY method
                ORDER BY SUM(credits) DESC
                LIMIT 10
            """, params)
            by_method = {row[0]: {"calls": row[1], "credits": row[2]} for row in cursor.fetchall()}

            conn.close()

            return {
                "total_credits": total_credits,
                "total_calls": total_calls,
                "by_source_file": by_source_file,
                "by_method": by_method,
            }

        except Exception as e:
            print(f"❌ Failed to fetch local metrics: {e}", file=sys.stderr)
            return {"total_credits": 0, "total_calls": 0, "by_source_file": {}, "by_method": {}}

    def get_raw_rpc_calls(self, since_timestamp: Optional[float] = None, limit: int = 50) -> List[Dict]:
        """Get raw RPC call details from metrics database"""
        try:
            conn = sqlite3.connect(METRICS_DB, timeout=5)
            cursor = conn.cursor()

            where_clause = ""
            params = []
            if since_timestamp is not None:
                where_clause = "WHERE timestamp > ?"
                params = [since_timestamp]

            cursor.execute(f"""
                SELECT timestamp, source_file, method, provider, credits, cache_action
                FROM rpc_metrics
                {where_clause}
                ORDER BY timestamp DESC
                LIMIT ?
            """, params + [limit])

            results = []
            for row in cursor.fetchall():
                results.append({
                    "timestamp": row[0],
                    "source_file": row[1],
                    "method": row[2],
                    "provider": row[3],
                    "credits": row[4],
                    "cache_action": row[5],
                })

            conn.close()
            return results

        except Exception as e:
            print(f"❌ Failed to fetch raw RPC calls: {e}", file=sys.stderr)
            return []

    def select_random_creator(self) -> Optional[str]:
        """Select a random creator from the database, preferring unanalyzed ones"""
        try:
            conn = sqlite3.connect(FLEX_DB, timeout=5)
            cursor = conn.cursor()

            # First, try to get an unanalyzed creator (fully_analyzed = 0)
            cursor.execute("""
                SELECT DISTINCT creator_address
                FROM creator_funders
                WHERE creator_address IS NOT NULL
                AND fully_analyzed = 0
                ORDER BY RANDOM()
                LIMIT 1
            """)

            result = cursor.fetchone()
            
            # If no unanalyzed creators, fall back to any creator
            if not result:
                print("  ℹ  No unanalyzed creators found, will reset analysis flag for one...")
                cursor.execute("""
                    SELECT DISTINCT creator_address
                    FROM creator_funders
                    WHERE creator_address IS NOT NULL
                    ORDER BY RANDOM()
                    LIMIT 1
                """)
                result = cursor.fetchone()
                
                # Reset the fully_analyzed flag to force re-extraction
                if result:
                    creator = result[0]
                    cursor.execute(
                        "UPDATE creator_funders SET fully_analyzed = 0 WHERE creator_address = ?",
                        (creator,)
                    )
                    conn.commit()
                    print(f"  ✅ Reset fully_analyzed flag for {creator[:16]}...")
            
            conn.close()

            if result:
                return result[0]
            return None

        except Exception as e:
            print(f"❌ Failed to select creator: {e}", file=sys.stderr)
            return None

    def run_creator_extraction(self, creator: str) -> Tuple[int, str, float]:
        """Run creator extraction for a given creator"""
        start = time.time()
        try:
            # Use the extraction function directly (async)
            import asyncio
            from realtime_creator_funding_extractor import extract_funding_for_new_token
            from datetime import datetime

            # Run the async function with ISO format timestamp
            migration_timestamp = datetime.utcnow().isoformat()
            result = asyncio.run(extract_funding_for_new_token(
                creator=creator,
                migration_timestamp_str=migration_timestamp,
                create_tx_signature="audit_test",
                mint="audit_test_mint"
            ))

            elapsed = time.time() - start
            return 0, "Creator extraction completed", elapsed

        except Exception as e:
            elapsed = time.time() - start
            return 1, f"Creator extraction failed: {str(e)}", elapsed

    def run_funder_extraction(self, creator: str) -> Tuple[int, str, float]:
        """Run funder extraction for a given creator's funders"""
        start = time.time()
        try:
            # Query for funders of this creator
            conn = sqlite3.connect(FLEX_DB, timeout=5)
            cursor = conn.cursor()

            cursor.execute("""
                SELECT DISTINCT funder_address
                FROM creator_funders
                WHERE creator_address = ?
                LIMIT 5
            """, (creator,))

            funders = [row[0] for row in cursor.fetchall()]
            conn.close()

            if not funders:
                elapsed = time.time() - start
                return 0, "No funders found for creator", elapsed

            # Import and run extraction (async)
            import asyncio
            from funder_incoming_extractor import extract_for_creator

            async def run_extractions():
                for funder in funders:
                    try:
                        await extract_for_creator(funder)
                    except Exception as e:
                        pass  # Log errors silently

            asyncio.run(run_extractions())

            elapsed = time.time() - start
            return 0, f"Funder extraction completed for {len(funders)} funders", elapsed

        except Exception as e:
            elapsed = time.time() - start
            return 1, f"Funder extraction failed: {str(e)}", elapsed

    def record_audit_phase(
        self,
        phase: str,
        iteration: int,
        helius_before: int,
        helius_after: int,
        local_before: Dict,
        local_after: Dict,
        duration: float,
        returncode: int,
        output: str,
        phase_start_timestamp: Optional[float] = None
    ):
        """Record results for one audit phase"""
        helius_delta = helius_after - helius_before
        local_credits_before = local_before.get("total_credits", 0)
        local_credits_after = local_after.get("total_credits", 0)
        local_calls_before = local_before.get("total_calls", 0)
        local_calls_after = local_after.get("total_calls", 0)

        local_credits_delta = local_credits_after - local_credits_before
        local_calls_delta = local_calls_after - local_calls_before

        # Get source_file breakdown for this phase
        by_source_file = local_after.get("by_source_file", {})
        by_source_file_before = local_before.get("by_source_file", {})

        # Calculate deltas per source file
        source_file_deltas = {}
        for source_file, after_stats in by_source_file.items():
            before_stats = by_source_file_before.get(source_file, {"credits": 0, "calls": 0})
            source_file_deltas[source_file] = {
                "credits": after_stats["credits"] - before_stats["credits"],
                "calls": after_stats["calls"] - before_stats["calls"],
            }

        # Get raw RPC calls (last few recorded, regardless of phase timing)
        raw_calls = self.get_raw_rpc_calls(limit=10)

        result = {
            "timestamp": datetime.now().isoformat(),
            "phase": phase,
            "iteration": iteration,
            "creator": self.selected_creator,
            "duration_seconds": round(duration, 2),
            "helius_before": helius_before,
            "helius_after": helius_after,
            "helius_delta": helius_delta,
            "local_credits_before": local_credits_before,
            "local_credits_after": local_credits_after,
            "local_credits_delta": local_credits_delta,
            "local_calls_before": local_calls_before,
            "local_calls_after": local_calls_after,
            "local_calls_delta": local_calls_delta,
            "helius_vs_local_diff": helius_delta - local_credits_delta,
            "source_file_breakdown": source_file_deltas,
            "raw_rpc_calls": raw_calls,
            "returncode": returncode,
            "output": output,
        }

        self.results.append(result)

        # Print summary with source file info
        status = "✅" if returncode == 0 else "❌"
        source_info = " | ".join([f"{sf}:{d['credits']}" for sf, d in source_file_deltas.items()])
        if source_info:
            source_info = f" | {source_info}"

        print(
            f"{status} {phase.upper()} #{iteration}: "
            f"Helius Δ={helius_delta} | Local Δ={local_credits_delta} | "
            f"Diff={result['helius_vs_local_diff']} | "
            f"Calls Δ={local_calls_delta}{source_info}"
        )

    def write_results(self):
        """Write audit results to JSON and CSV"""
        # JSON
        with open(AUDIT_JSON, "w") as f:
            json.dump(self.results, f, indent=2)
        print(f"\n✅ Results written to {AUDIT_JSON}")

        # CSV
        if self.results:
            keys = self.results[0].keys()
            with open(AUDIT_CSV, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=keys)
                writer.writeheader()
                writer.writerows(self.results)
            print(f"✅ Results written to {AUDIT_CSV}")

    def print_summary(self):
        """Print audit summary"""
        if not self.results:
            return

        print("\n" + "=" * 80)
        print("AUDIT SUMMARY")
        print("=" * 80)

        # Group by phase
        by_phase = {}
        for result in self.results:
            phase = result["phase"]
            if phase not in by_phase:
                by_phase[phase] = {
                    "helius_deltas": [],
                    "local_deltas": [],
                    "diffs": [],
                    "source_files": {},
                    "raw_calls_sample": [],
                }
            by_phase[phase]["helius_deltas"].append(result["helius_delta"])
            by_phase[phase]["local_deltas"].append(result["local_credits_delta"])
            by_phase[phase]["diffs"].append(result["helius_vs_local_diff"])

            # Collect source_file info
            for source_file, deltas in result.get("source_file_breakdown", {}).items():
                if source_file not in by_phase[phase]["source_files"]:
                    by_phase[phase]["source_files"][source_file] = {"credits": 0, "calls": 0}
                by_phase[phase]["source_files"][source_file]["credits"] += deltas.get("credits", 0)
                by_phase[phase]["source_files"][source_file]["calls"] += deltas.get("calls", 0)

            # Collect sample of raw calls
            if result.get("raw_rpc_calls"):
                by_phase[phase]["raw_calls_sample"].extend(result["raw_rpc_calls"])

        for phase, data in by_phase.items():
            helius_total = sum(data["helius_deltas"])
            local_total = sum(data["local_deltas"])
            diff_total = sum(data["diffs"])
            avg_diff = diff_total / len(data["diffs"]) if data["diffs"] else 0

            print(f"\n{phase.upper()}:")
            print(f"  Helius Total Δ:  {helius_total} credits")
            print(f"  Local Total Δ:   {local_total} credits")
            print(f"  Total Diff:      {diff_total} credits")
            print(f"  Avg Diff/Run:    {avg_diff:.1f} credits")

            # Source file breakdown
            if data["source_files"]:
                print(f"\n  Source File Breakdown:")
                for source_file, stats in sorted(
                    data["source_files"].items(),
                    key=lambda x: x[1]["credits"],
                    reverse=True
                ):
                    print(f"    {source_file}: {stats['credits']} credits ({stats['calls']} calls)")

            # Raw calls sample
            if data["raw_calls_sample"]:
                print(f"\n  Sample RPC Calls (most recent):")
                seen = set()
                for call in data["raw_calls_sample"][:5]:
                    # Show unique method calls
                    key = f"{call['source_file']}:{call['method']}"
                    if key not in seen:
                        seen.add(key)
                        print(
                            f"    {call['method']} ({call['provider']}) "
                            f"- {call['credits']} credits "
                            f"from {call['source_file']}"
                        )

            if abs(diff_total) < 10:
                print(f"\n  Status: ✅ GOOD MATCH (within 10 credits)")
            elif diff_total > 0:
                print(f"\n  Status: ⚠️  HELIUS HIGHER - {diff_total} untracked credits")
            else:
                print(f"\n  Status: ⚠️  LOCAL HIGHER - {abs(diff_total)} over-estimated credits")

        # Overall
        overall_helius = sum(r["helius_delta"] for r in self.results)
        overall_local = sum(r["local_credits_delta"] for r in self.results)
        overall_diff = overall_helius - overall_local

        print(f"\nOVERALL:")
        print(f"  Total Helius Δ:  {overall_helius} credits")
        print(f"  Total Local Δ:   {overall_local} credits")
        print(f"  Net Difference:  {overall_diff} credits")
        print(f"  Accuracy:        {(overall_local / overall_helius * 100):.1f}%" if overall_helius > 0 else "  Accuracy:        N/A")

    def run_audit(self):
        """Execute the full audit"""
        print("=" * 80)
        print("HELIUS vs LOCAL RPC METRICS AUDIT")
        print("=" * 80)
        print(f"Started: {self.start_time.isoformat()}\n")

        # Step 1: Fetch initial Helius usage
        print("📊 Fetching initial Helius usage...")
        helius_initial = self.get_helius_usage()
        if helius_initial is None:
            print("❌ Cannot proceed without Helius data")
            return

        print(f"   Helius credits today: {helius_initial}")

        # Step 2: Wait 60 seconds
        print(f"\n⏳ Waiting {WAIT_TIME_SECONDS} seconds for baseline...")
        for i in range(WAIT_TIME_SECONDS):
            print(f"  {i+1}/{WAIT_TIME_SECONDS}", end="\r")
            time.sleep(1)
        print(f"✅ Baseline wait complete{' ' * 20}\n")

        # Step 3: Select random creator
        print("🎲 Selecting random creator from database...")
        self.selected_creator = self.select_random_creator()
        if not self.selected_creator:
            print("❌ Could not find a valid creator")
            return

        print(f"   Selected creator: {self.selected_creator}\n")

        # Step 4: Run creator extraction iterations
        print(f"🔄 Running {CREATOR_ITERATIONS} creator extraction iterations...")
        for i in range(CREATOR_ITERATIONS):
            helius_before = self.get_helius_usage()
            local_before = self.get_local_metrics_summary()

            returncode, output, duration = self.run_creator_extraction(self.selected_creator)

            # Wait for Helius to update (60 seconds for config file sync)
            print(f"  ⏳ Waiting 60 seconds for Helius to update...")
            time.sleep(60)

            helius_after = self.get_helius_usage()
            local_after = self.get_local_metrics_summary()  # Get full total again

            self.record_audit_phase(
                phase="creator",
                iteration=i + 1,
                helius_before=helius_before,
                helius_after=helius_after,
                local_before=local_before,
                local_after=local_after,
                duration=duration,
                returncode=returncode,
                output=output,
            )

        # Step 5: Run funder extraction iterations
        print(f"\n🔄 Running {FUNDER_ITERATIONS} funder extraction iterations...")
        for i in range(FUNDER_ITERATIONS):
            helius_before = self.get_helius_usage()
            local_before = self.get_local_metrics_summary()

            returncode, output, duration = self.run_funder_extraction(self.selected_creator)

            # Wait for Helius to update (60 seconds for config file sync)
            print(f"  ⏳ Waiting 60 seconds for Helius to update...")
            time.sleep(60)

            helius_after = self.get_helius_usage()
            local_after = self.get_local_metrics_summary()  # Get full total again

            self.record_audit_phase(
                phase="funder",
                iteration=i + 1,
                helius_before=helius_before,
                helius_after=helius_after,
                local_before=local_before,
                local_after=local_after,
                duration=duration,
                returncode=returncode,
                output=output,
            )

        # Step 6: Write results
        self.write_results()
        self.print_summary()

        print("\n" + "=" * 80)
        print(f"Audit completed: {datetime.now().isoformat()}")
        print("=" * 80)


if __name__ == "__main__":
    audit = HeliusAudit()
    try:
        audit.run_audit()
    except KeyboardInterrupt:
        print("\n\n⚠️  Audit interrupted by user")
        audit.write_results()
        audit.print_summary()
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Audit failed: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)
