#!/usr/bin/env python3
"""
Establish Baseline Token Price

This script establishes the initial on-chain price for a newly launched token,
stores it as a baseline, and detects when significant changes occur.

The baseline price serves as a reference point for detecting:
1. Liquidity removal events (price drops significantly)
2. Buy/sell pressure (price increases)
3. Pool health status

Usage:
  python establish_baseline_price.py <POOL_ADDRESS>

This will:
1. Fetch the current price from on-chain vaults
2. Store it as the baseline
3. Display price confidence/reliability metrics
4. Continue monitoring to track deviations from baseline
"""

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, List, Tuple

from meteora_price_fetcher_v2 import fetch_price


class BaselinePriceManager:
    def __init__(self, pool_address: str):
        self.pool_address = pool_address
        self.baseline_file = f"baseline_price_{pool_address[:8]}.json"
        self.baseline_data = None
        self.load_baseline()

    def load_baseline(self):
        """Load existing baseline if available"""
        if Path(self.baseline_file).exists():
            try:
                with open(self.baseline_file) as f:
                    self.baseline_data = json.load(f)
                    print(f"✓ Loaded existing baseline from {self.baseline_file}")
            except Exception as e:
                print(f"⚠️  Could not load baseline: {e}")

    def estimate_liquidity_removal(self, price_ratio: float) -> float:
        """
        Estimate percentage of liquidity removed based on price ratio change

        For a constant-product AMM (x * y = k):
        - If one side (e.g., SOL) is removed, the other side's price decreases
        - Price ratio = current_price / baseline_price
        - Liquidity removal affects vault balances proportionally

        For simple pools:
          liquidity_removed ≈ 1 - (1 / price_ratio) if price_ratio < 1
          liquidity_removed ≈ 1 - price_ratio if price_ratio > 1

        For Meteora DLMM (approximation):
          The relationship is more complex due to multiple bins,
          but we can approximate based on vault balance changes.

        Returns: Estimated percentage of liquidity removed (0-100)
        """
        if price_ratio <= 0:
            return 100.0  # Complete removal

        if price_ratio == 1.0:
            return 0.0  # No removal

        # For a price drop (ratio < 1): token side was drained
        # For a price increase (ratio > 1): SOL side was drained

        if price_ratio < 1:
            # Price dropped - token side liquidity removed
            # liquidity_removed = 1 - (1 / ratio)
            # Examples:
            #   ratio = 0.5 -> 1 - 2.0 = -1.0 (not used, use different formula)
            #   ratio = 0.5 -> removed = 50%
            #   ratio = 0.25 -> removed = 75%
            #   ratio = 0.1 -> removed = 90%
            liquidity_removed = (1 - price_ratio) * 100
        else:
            # Price increased - SOL side liquidity removed
            # liquidity_removed = (ratio - 1) / ratio * 100
            # Examples:
            #   ratio = 2.0 -> removed = 50%
            #   ratio = 4.0 -> removed = 75%
            #   ratio = 10.0 -> removed = 90%
            liquidity_removed = (1 - (1 / price_ratio)) * 100

        # Cap at 100% (can't remove more than 100%)
        return min(liquidity_removed, 100.0)

    def establish_baseline(self, num_samples: int = 5) -> Optional[Dict]:
        """
        Establish baseline price by taking multiple samples and averaging

        Args:
            num_samples: Number of price samples to average (recommended: 3-10)

        Returns:
            Baseline data dict with average price, stability metrics, etc.
        """
        print(f"\n{'='*80}")
        print(f"ESTABLISHING BASELINE PRICE FOR POOL: {self.pool_address}")
        print(f"{'='*80}\n")

        import time

        prices = []
        print(f"Taking {num_samples} price samples (1 per check)...\n")

        for i in range(num_samples):
            print(f"Sample {i+1}/{num_samples}: ", end='', flush=True)

            try:
                result = fetch_price(self.pool_address, verbose=False)
                price = result.get('on_chain_price')

                if price:
                    prices.append(price)
                    print(f"${price:.18f}")

                    if i < num_samples - 1:
                        time.sleep(1)  # 1 second between samples
                else:
                    print("FAILED TO FETCH")
                    return None

            except Exception as e:
                print(f"ERROR: {e}")
                return None

        # Calculate statistics
        avg_price = sum(prices) / len(prices)
        min_price = min(prices)
        max_price = max(prices)
        price_variance = max_price - min_price
        variance_pct = (price_variance / avg_price * 100) if avg_price > 0 else 0

        # Assess price stability
        if variance_pct < 1:
            stability = "VERY HIGH (Stable pool)"
        elif variance_pct < 5:
            stability = "HIGH (Mostly stable)"
        elif variance_pct < 10:
            stability = "MODERATE (Some fluctuation)"
        elif variance_pct < 20:
            stability = "LOW (Volatile)"
        else:
            stability = "VERY LOW (Highly volatile)"

        baseline_data = {
            'pool_address': self.pool_address,
            'established_at': datetime.now().isoformat(),
            'baseline_price': avg_price,
            'min_observed': min_price,
            'max_observed': max_price,
            'variance': variance_pct,
            'stability': stability,
            'samples': num_samples,
            'sample_prices': prices,
            'notes': 'This baseline will be used to detect liquidity removal events'
        }

        return baseline_data

    def save_baseline(self, baseline_data: Dict) -> bool:
        """Save baseline to file"""
        try:
            with open(self.baseline_file, 'w') as f:
                json.dump(baseline_data, f, indent=2)
            self.baseline_data = baseline_data
            print(f"\n✓ Baseline saved to {self.baseline_file}")
            return True
        except Exception as e:
            print(f"✗ Error saving baseline: {e}")
            return False

    def print_baseline_summary(self, baseline_data: Dict) -> None:
        """Print comprehensive baseline summary"""
        print(f"\n{'='*80}")
        print("BASELINE PRICE SUMMARY")
        print(f"{'='*80}\n")

        print(f"Pool Address:    {baseline_data['pool_address']}")
        print(f"Established:     {baseline_data['established_at']}")
        print(f"Baseline Price:  ${baseline_data['baseline_price']:.18f} SOL/token")
        print(f"\nObserved Range:")
        print(f"  Minimum:       ${baseline_data['min_observed']:.18f}")
        print(f"  Maximum:       ${baseline_data['max_observed']:.18f}")
        print(f"  Variance:      {baseline_data['variance']:.4f}%")
        print(f"  Stability:     {baseline_data['stability']}")

        print(f"\nDetection Thresholds:")
        print(f"  50% drop alert:  ${baseline_data['baseline_price'] * 0.5:.18f}")
        print(f"  25% drop alert:  ${baseline_data['baseline_price'] * 0.75:.18f}")
        print(f"  2x increase:     ${baseline_data['baseline_price'] * 2:.18f}")
        print(f"  10x increase:    ${baseline_data['baseline_price'] * 10:.18f}")

        print(f"\nWhat This Means:")
        if baseline_data['variance'] < 5:
            print("✓ Pool is STABLE - Good for detecting real events")
            print("  Baseline is reliable for comparison")
        else:
            print("⚠️  Pool is VOLATILE - Baseline should be used carefully")
            print("  May need larger deviation threshold to avoid false positives")

    def check_against_baseline(self) -> Optional[Dict]:
        """Check current price against established baseline"""
        if not self.baseline_data:
            print("No baseline established. Run establish_baseline() first.")
            return None

        print(f"\n{'='*80}")
        print("CHECKING CURRENT PRICE AGAINST BASELINE")
        print(f"{'='*80}\n")

        result = fetch_price(self.pool_address, verbose=False)
        current_price = result.get('on_chain_price')

        if not current_price:
            print("Failed to fetch current price")
            return None

        baseline_price = self.baseline_data['baseline_price']
        ratio = current_price / baseline_price if baseline_price > 0 else 0
        change_pct = (ratio - 1) * 100

        # Calculate liquidity removal percentage
        # When price drops, it means vault balances changed
        # Lower price = vault was drained
        liquidity_removed_pct = self.estimate_liquidity_removal(ratio)

        print(f"Baseline Price:  ${baseline_price:.18f}")
        print(f"Current Price:   ${current_price:.18f}")
        print(f"Change:          {change_pct:+.2f}%")
        print(f"Ratio:           {ratio:.6f}x")
        print(f"\nLiquidity Removal Estimate:")
        print(f"  Price ratio: {ratio:.6f}x")
        print(f"  Estimated liquidity removed: {liquidity_removed_pct:.1f}%")

        # Detect anomalies
        alerts = []

        if ratio < 0.5:
            alerts.append({
                'type': 'CRITICAL',
                'message': f'Price dropped >50% ({change_pct:.1f}%)',
                'cause': 'Likely liquidity removal event'
            })
        elif ratio < 0.75:
            alerts.append({
                'type': 'HIGH',
                'message': f'Price dropped 25-50% ({change_pct:.1f}%)',
                'cause': 'Significant liquidity change'
            })
        elif ratio > 10:
            alerts.append({
                'type': 'HIGH',
                'message': f'Price spiked >10x ({change_pct:.1f}%)',
                'cause': 'Major market movement or pool imbalance'
            })
        elif ratio > 2:
            alerts.append({
                'type': 'MEDIUM',
                'message': f'Price doubled ({change_pct:.1f}%)',
                'cause': 'Significant buy pressure'
            })

        if alerts:
            print(f"\n{'='*80}")
            print("⚠️  ALERTS DETECTED")
            print(f"{'='*80}")
            for alert in alerts:
                print(f"\n[{alert['type']}] {alert['message']}")
                print(f"  Possible cause: {alert['cause']}")
        else:
            print(f"\n✓ Price is stable relative to baseline")
            print(f"  No significant deviations detected")

        return {
            'baseline_price': baseline_price,
            'current_price': current_price,
            'change_pct': change_pct,
            'ratio': ratio,
            'liquidity_removed_pct': liquidity_removed_pct,
            'alerts': alerts,
            'check_time': datetime.now().isoformat()
        }


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    pool_address = sys.argv[1]
    manager = BaselinePriceManager(pool_address)

    # Check if baseline already exists
    if manager.baseline_data:
        print(f"\n✓ Baseline already established at {manager.baseline_data['established_at']}")
        manager.print_baseline_summary(manager.baseline_data)

        response = input("\nCheck current price against baseline? (y/n): ").lower()
        if response == 'y':
            manager.check_against_baseline()
    else:
        # Establish new baseline
        num_samples = input("\nHow many price samples to collect? (default: 5): ").strip()
        num_samples = int(num_samples) if num_samples.isdigit() else 5

        baseline_data = manager.establish_baseline(num_samples=num_samples)

        if baseline_data:
            manager.save_baseline(baseline_data)
            manager.print_baseline_summary(baseline_data)

            # Offer to check immediately
            response = input("\nMonitor this pool for 10 minutes? (y/n): ").lower()
            if response == 'y':
                import subprocess
                subprocess.run([
                    'python', 'test_liquidity_monitoring.py',
                    pool_address, '30', '20'  # 30s interval, 20 checks = 10 min
                ])


if __name__ == "__main__":
    main()
