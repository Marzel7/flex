#!/usr/bin/env python3
"""
Check RPC configuration and endpoint capabilities.
Helps diagnose rate limiting and performance issues.
"""

import os
import requests
import time
from dotenv import load_dotenv

load_dotenv()

HELIUS_API_KEY = os.getenv("HELIUS_API_KEY", "")
RPC_URL = os.getenv("RPC_URL", "")

print("\n" + "="*70)
print("RPC CONFIGURATION CHECK")
print("="*70)

print("\n📋 Environment Variables:")
print(f"  HELIUS_API_KEY: {'✓ SET' if HELIUS_API_KEY else '✗ NOT SET'}")
print(f"  RPC_URL: {'✓ SET' if RPC_URL else '✗ NOT SET'}")

# Determine which RPC is being used
if RPC_URL:
    active_rpc = RPC_URL
    rpc_type = "QuickNode (or custom)"
elif HELIUS_API_KEY:
    active_rpc = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"
    rpc_type = "Helius"
else:
    active_rpc = "https://api.mainnet-beta.solana.com"
    rpc_type = "Public Solana"

print(f"\n🔗 Active RPC Endpoint: {rpc_type}")
print(f"  URL: {active_rpc[:80]}{'...' if len(active_rpc) > 80 else ''}")

# Test rate limiting
print("\n⚡ Rate Limiting Test:")
print("  Sending 15 sequential requests to measure rate limits...")

times = []
failed = 0
successful = 0

for i in range(15):
    try:
        payload = {
            "jsonrpc": "2.0",
            "id": i,
            "method": "getSlot",
            "params": []
        }

        start = time.time()
        resp = requests.post(active_rpc, json=payload, timeout=10)
        elapsed = time.time() - start
        times.append(elapsed)

        if resp.status_code == 200:
            successful += 1
            status = "✓"
        elif resp.status_code == 429:
            failed += 1
            status = "⚠ 429 RATE LIMITED"
        else:
            failed += 1
            status = f"✗ HTTP {resp.status_code}"

        print(f"    Request {i+1:2d}: {status} ({elapsed:.3f}s)")
    except Exception as e:
        failed += 1
        print(f"    Request {i+1:2d}: ✗ ERROR ({str(e)[:40]})")

print(f"\n  Results: {successful}/15 successful, {failed}/15 failed")

if failed > 0:
    print(f"\n  ⚠️  Rate limiting detected!")
    if rpc_type == "Public Solana":
        print(f"  → Public Solana RPC has ~40 req/sec limit")
        print(f"  → Use BATCH_SIZE=3 or less")
        print(f"  → Or configure QuickNode with RPC_URL environment variable")
    elif rpc_type == "Helius":
        print(f"  → Helius may have rate limits on free tier")
        print(f"  → Try setting RPC_URL to QuickNode endpoint")
else:
    print(f"\n  ✓ No rate limiting detected")
    if rpc_type == "QuickNode (or custom)":
        print(f"  → Can safely use BATCH_SIZE=10 or higher")
    elif rpc_type == "Helius":
        print(f"  → Helius is responding well, BATCH_SIZE=5-10 safe")
    else:
        print(f"  → Unexpected - public Solana usually rate limits faster")

print("\n" + "="*70)
print("CONFIGURATION RECOMMENDATIONS")
print("="*70)

print("\n1️⃣  To use QuickNode Premium (recommended for >80% coverage):")
print("   a. Create .env file with:")
print("      RPC_URL=https://your-quicknode-endpoint-here")
print("   b. Restart the listener")
print("   c. Should achieve 80-95%+ coverage")

print("\n2️⃣  To use public Solana safely:")
print("   a. Change BATCH_SIZE to 3 in pump_fun_post_migration_analyzer.py")
print("   b. Change BATCH_DELAY to 1.0")
print("   c. Coverage will be lower (~40-60%) but stable")

print("\n3️⃣  To use Helius (if available):")
print("   a. Set HELIUS_API_KEY in .env")
print("   b. Set BATCH_SIZE=5 for safe operation")
print("   c. Coverage: 60-75% typically")

print("\n" + "="*70 + "\n")
