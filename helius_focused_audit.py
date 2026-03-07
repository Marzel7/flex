#!/usr/bin/env python3
"""
Focused Helius audit - forces fresh RPC calls with a new creator
"""
import sqlite3
import subprocess
import time
import json
from datetime import datetime
from pathlib import Path

def get_helius_credits():
    """Get current Helius daily credits"""
    try:
        import importlib
        import rpc_metrics_config
        importlib.reload(rpc_metrics_config)
        return rpc_metrics_config.PlanConfig.CURRENT_USAGE['credits_used_today']
    except Exception as e:
        print(f"Error reading Helius: {e}")
        return None

def get_unanalyzed_creator():
    """Get a creator that hasn't been fully analyzed"""
    conn = sqlite3.connect('flex_complete_database.db')
    cursor = conn.cursor()
    cursor.execute("""
        SELECT creator_address, COUNT(*) as funder_count 
        FROM creator_funders 
        WHERE fully_analyzed = 0 
        ORDER BY funder_count DESC
        LIMIT 1
    """)
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else None

def reset_creator_analyzed_flag(creator_address):
    """Reset fully_analyzed flag to force re-extraction"""
    conn = sqlite3.connect('flex_complete_database.db')
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE creator_funders SET fully_analyzed = 0 WHERE creator_address = ?",
        (creator_address,)
    )
    conn.commit()
    conn.close()
    print(f"✅ Reset fully_analyzed flag for {creator_address}")

def run_extraction(creator_address):
    """Run creator extraction and return status"""
    cmd = f"python3 realtime_creator_funding_extractor.py '{creator_address}' 2>&1"
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
    return result.returncode, result.stdout + result.stderr

def main():
    print("=" * 80)
    print("FOCUSED HELIUS AUDIT - Force Fresh RPC Calls")
    print("=" * 80)
    
    # Step 1: Get baseline
    print("\n📊 Step 1: Get baseline Helius credits...")
    helius_before = get_helius_credits()
    print(f"  Helius credits: {helius_before}")
    
    # Step 2: Find unanalyzed creator
    print("\n📊 Step 2: Find unanalyzed creator...")
    creator = get_unanalyzed_creator()
    if not creator:
        print("  ❌ No unanalyzed creators found. Trying to reset one...")
        conn = sqlite3.connect('flex_complete_database.db')
        cursor = conn.cursor()
        cursor.execute("SELECT creator_address FROM creator_funders LIMIT 1")
        creator = cursor.fetchone()[0] if cursor.fetchone() else None
        conn.close()
        if creator:
            reset_creator_analyzed_flag(creator)
        else:
            print("  ❌ No creators in database")
            return
    
    print(f"  Creator: {creator}")
    
    # Step 3: Run extraction
    print(f"\n🔄 Step 3: Running extraction for {creator}...")
    print("  (This should make fresh RPC calls)")
    returncode, output = run_extraction(creator)
    print(f"  Return code: {returncode}")
    print(f"  Output length: {len(output)} chars")
    
    # Step 4: Wait for Helius sync
    print("\n⏳ Step 4: Waiting 60 seconds for Helius sync...")
    for i in range(60, 0, -10):
        print(f"  {i}s remaining...", flush=True)
        time.sleep(10)
    
    # Step 5: Get after credits
    print("\n📊 Step 5: Get after Helius credits...")
    helius_after = get_helius_credits()
    print(f"  Helius credits: {helius_after}")
    
    # Step 6: Report
    delta = helius_after - helius_before if (helius_after and helius_before) else None
    print("\n" + "=" * 80)
    print("RESULTS")
    print("=" * 80)
    print(f"Before: {helius_before}")
    print(f"After:  {helius_after}")
    print(f"Delta:  {delta} credits" if delta is not None else "Delta: Could not calculate")
    
    if delta and delta > 0:
        print(f"✅ Extracted {delta} credits from Helius during extraction")
    elif delta == 0:
        print(f"⚠️  No Helius credits consumed (data may be cached)")
    else:
        print(f"❓ Unexpected delta: {delta}")

if __name__ == "__main__":
    main()
