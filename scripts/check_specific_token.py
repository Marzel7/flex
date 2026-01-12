#!/usr/bin/env python3
"""
Check a specific token's risk score breakdown
"""

import asyncio
import os
from pump_fun_pre_migration_analyzer_v2 import PumpFunPreMigrationAnalyzerV2
from dotenv import load_dotenv

load_dotenv()

async def analyze_token(mint):
    """Analyze a token and show detailed risk scoring"""
    print(f"\n{'='*70}")
    print(f"Risk Score Analysis: {mint}")
    print(f"{'='*70}\n")
    
    helius_key = os.getenv('HELIUS_API_KEY')
    rpc_url = f"https://mainnet.helius-rpc.com/?api-key={helius_key}" if helius_key else "https://api.mainnet-beta.solana.com"
    
    analyzer = PumpFunPreMigrationAnalyzerV2(mint, rpc_url=rpc_url)
    await analyzer.fetch_curve_activity_async()
    summary = analyzer.summary()
    
    print("Individual Metric Scores:")
    print("-" * 70)
    
    # Check each metric against thresholds
    mint_conc = summary['mint_concentration']
    unique_ratio = summary['unique_minters_ratio']
    sell_ratio = summary['sell_suppression_ratio']
    velocity = summary['mint_velocity_sec']
    variance = summary['buy_size_variance']
    sell_conc = summary['sell_volume_concentration']
    
    score = 0.0
    
    # Mint concentration
    print(f"Mint Concentration: {mint_conc:.1%}")
    if mint_conc > 0.7:
        print(f"  ⚠️  > 0.7: +0.25 points (HIGH concentration in top 5)")
        score += 0.25
    elif mint_conc > 0.5:
        print(f"  ⚠️  > 0.5: +0.15 points (MEDIUM concentration in top 5)")
        score += 0.15
    else:
        print(f"  ✓  <= 0.5: 0 points (OK - distributed holdings)")
    
    # Unique minters ratio
    print(f"\nUnique Minters Ratio: {unique_ratio:.1%}")
    if unique_ratio < 0.15:
        print(f"  ⚠️  < 0.15: +0.20 points (VERY LOW unique wallets)")
        score += 0.20
    elif unique_ratio < 0.25:
        print(f"  ⚠️  < 0.25: +0.10 points (LOW unique wallets)")
        score += 0.10
    else:
        print(f"  ✓  >= 0.25: 0 points (OK - good participation)")
    
    # Sell suppression
    print(f"\nSell Suppression Ratio: {sell_ratio:.1%}")
    if sell_ratio < 0.05:
        print(f"  ⚠️  < 0.05: +0.20 points (VERY LOW sell activity)")
        score += 0.20
    elif sell_ratio < 0.10:
        print(f"  ⚠️  < 0.10: +0.10 points (LOW sell activity)")
        score += 0.10
    else:
        print(f"  ✓  >= 0.10: 0 points (OK - healthy selling)")
    
    # Mint velocity
    print(f"\nMint Velocity (seconds): {velocity:.1f}s")
    if velocity < 5:
        print(f"  ⚠️  < 5s: +0.15 points (EXTREMELY FAST buying)")
        score += 0.15
    elif velocity < 10:
        print(f"  ⚠️  < 10s: +0.08 points (FAST buying)")
        score += 0.08
    else:
        print(f"  ✓  >= 10s: 0 points (OK - normal pace)")
    
    # Buy size variance
    print(f"\nBuy Size Variance: {variance:.0f}")
    if variance < 1e6:
        print(f"  ⚠️  < 1M: +0.15 points (UNIFORM buy sizes - bot-like)")
        score += 0.15
    elif variance < 1e7:
        print(f"  ⚠️  < 10M: +0.08 points (LOW variance)")
        score += 0.08
    else:
        print(f"  ✓  >= 10M: 0 points (OK - diverse buy sizes)")
    
    # Sell volume concentration
    print(f"\nSell Volume Concentration: {sell_conc:.1%}")
    if sell_conc > 0.5:
        print(f"  ⚠️  > 0.5: +0.05 points (CONCENTRATED selling from few wallets)")
        score += 0.05
    else:
        print(f"  ✓  <= 0.5: 0 points (OK - distributed selling)")
    
    final_score = min(score, 1.0)
    
    print(f"\n{'='*70}")
    print(f"Total Score: {final_score:.2f} ({final_score:.0%})")
    print(f"{'='*70}\n")
    
    # Show thresholds
    print("Risk Level Thresholds:")
    print("  🔴 HIGH RISK:   >= 0.70")
    print("  🟡 MEDIUM RISK: >= 0.40 and < 0.70")
    print("  🟢 LOW RISK:    < 0.40")
    
    if final_score >= 0.7:
        risk_level = "🔴 HIGH RISK"
    elif final_score >= 0.4:
        risk_level = "🟡 MEDIUM RISK"
    else:
        risk_level = "🟢 LOW RISK"
    
    print(f"\nClassification: {risk_level}")
    print(f"\nSummary Metrics:")
    print(f"  Events: {summary['events_parsed']}")
    print(f"  Coverage: {summary['pre_migration_coverage']}%")
    print(f"  Transactions: {summary['transactions_fetched']}/{summary['signatures_requested']}")
    print(f"\n{'='*70}\n")

if __name__ == "__main__":
    test_mint = "8XzSqqNevScuiqJwDuKMgmDLCMsJPuay2GtKM2fupump"
    asyncio.run(analyze_token(test_mint))
