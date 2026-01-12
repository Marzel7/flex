#!/usr/bin/env python3
from pump_fun_pre_migration_analyzer_v2 import PumpFunPreMigrationAnalyzerV2
import os
from dotenv import load_dotenv

load_dotenv()

helius_key = os.getenv('HELIUS_API_KEY', '')
rpc_url = f"https://mainnet.helius-rpc.com/?api-key={helius_key}" if helius_key else "https://api.mainnet-beta.solana.com"

mint = "82P9MvicWYr2R1yeYZLJrbPZB236uMeMBKJ6bLgpBAGS"

analyzer = PumpFunPreMigrationAnalyzerV2(mint, rpc_url=rpc_url)
analyzer.fetch_curve_activity()

print(f"\nDetailed Metrics for {mint[:20]}...")
print(f"{'='*70}")
print(f"Raw Data:")
print(f"  Events: {len(analyzer.events)}")
print(f"  Buys: {sum(1 for e in analyzer.events if e['type'] == 'buy')}")
print(f"  Sells: {sum(1 for e in analyzer.events if e['type'] == 'sell')}")
print(f"  Unique wallets: {len(set(e['wallet'] for e in analyzer.events))}")

print(f"\nRug Score Components:")
print(f"  1. Mint concentration: {analyzer.mint_concentration():.3f}")
print(f"     → Top 5 wallets hold {analyzer.mint_concentration()*100:.1f}% of buys")
print(f"     → Score impact: +0.25 if >0.7, +0.15 if >0.5")

print(f"  2. Unique minters ratio: {analyzer.unique_minters_ratio():.3f}")
print(f"     → {analyzer.unique_minters_ratio()*100:.1f}% unique wallets to total buys")
print(f"     → Score impact: +0.20 if <0.15, +0.10 if <0.25")

print(f"  3. Sell suppression: {analyzer.sell_suppression_ratio():.3f}")
print(f"     → {analyzer.sell_suppression_ratio()*100:.1f}% of all events are sells")
print(f"     → Score impact: +0.20 if <0.05, +0.10 if <0.10")

print(f"  4. Mint velocity: {analyzer.mint_velocity():.1f}s")
print(f"     → Avg time between buys")
print(f"     → Score impact: +0.15 if <5s, +0.08 if <10s")

print(f"  5. Buy size variance: {analyzer.buy_size_variance():.0f}")
print(f"     → Variance in buy amounts")
print(f"     → Score impact: +0.15 if <1e6, +0.08 if <1e7")

print(f"  6. Sell volume concentration: {analyzer.sell_volume_concentration():.3f}")
print(f"     → Top 3 sellers hold {analyzer.sell_volume_concentration()*100:.1f}% of sell volume")
print(f"     → Score impact: +0.05 if >0.5")

print(f"\nFinal Risk Score: {analyzer.compute_rug_score():.3f} ({analyzer.summary()['risk_level']})")
print(f"{'='*70}\n")
