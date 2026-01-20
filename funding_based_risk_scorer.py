#!/usr/bin/env python3
"""
Funding-based risk scoring enhancement.

Integrates bidirectional SOL flow analysis into rug detection risk scores.

Signals:
1. INBOUND LEGITIMACY: Creators with external funding are slightly safer
2. NETWORK COORDINATION: Multiple creators → same treasury = CRITICAL risk
3. SELF-FUNDED CONSOLIDATION: No inbound, heavy outbound = suspicious pattern
4. DISTRIBUTED CONSOLIDATION: Outbound to 5+ addresses = money laundering pattern
"""

import sqlite3
from typing import Optional, Dict, List, Tuple

DB_PATH = "pumpswap_tokens.db"

class FundingRiskScorer:
    """Enhance risk scores using funding flow analysis"""

    def __init__(self, creator_address: str):
        self.creator_address = creator_address
        self.conn = sqlite3.connect(DB_PATH, timeout=10)
        self.conn.row_factory = sqlite3.Row
        self.cursor = self.conn.cursor()

    def get_funding_profile(self) -> Dict:
        """Get complete funding profile for creator"""
        profile = {
            'inbound_funders': 0,
            'inbound_total_sol': 0,
            'outbound_destinations': 0,
            'outbound_total_sol': 0,
            'is_coordinated': False,
            'network_id': None,
            'network_size': 0,
            'network_risk_level': None,
            'co_creators': 0,
            'treasury_addresses': []
        }

        try:
            # Get unified network data
            self.cursor.execute("""
                SELECT
                    inbound_funders_count,
                    inbound_total_sol,
                    outbound_destinations_count,
                    outbound_total_sol,
                    is_coordinated,
                    network_id,
                    co_creator_count
                FROM creator_unified_network
                WHERE creator_address = ?
            """, (self.creator_address,))

            row = self.cursor.fetchone()
            if row:
                profile['inbound_funders'] = row[0] or 0
                profile['inbound_total_sol'] = row[1] or 0
                profile['outbound_destinations'] = row[2] or 0
                profile['outbound_total_sol'] = row[3] or 0
                profile['is_coordinated'] = bool(row[4])
                profile['network_id'] = row[5]
                profile['co_creators'] = row[6] or 0

                # Get network info if coordinated
                if row[5]:  # network_id
                    self.cursor.execute("""
                        SELECT network_size, risk_level, treasury_addresses
                        FROM creator_network_group
                        WHERE network_id = ?
                    """, (row[5],))

                    net_row = self.cursor.fetchone()
                    if net_row:
                        profile['network_size'] = net_row[0]
                        profile['network_risk_level'] = net_row[1]

                        import json
                        try:
                            profile['treasury_addresses'] = json.loads(net_row[2])
                        except:
                            profile['treasury_addresses'] = []

            return profile

        except Exception as e:
            print(f"[FUNDING] Error getting funding profile: {e}", flush=True)
            return profile

    def calculate_funding_risk_adjustment(self, base_score: float) -> Tuple[float, Dict]:
        """
        Calculate risk adjustment based on funding signals.

        Returns:
            Adjusted score and signal details
        """
        profile = self.get_funding_profile()
        adjustment = 0.0
        signals = {
            'has_inbound': False,
            'has_outbound': False,
            'is_network_member': False,
            'network_risk': None,
            'pattern': 'unknown'
        }

        # CRITICAL: In coordinated network with malicious member
        if profile['is_coordinated'] and profile['network_id']:
            self.cursor.execute("""
                SELECT malicious_member_count FROM creator_network_group
                WHERE network_id = ?
            """, (profile['network_id'],))

            net_row = self.cursor.fetchone()
            if net_row and net_row[0] > 0:
                # CRITICAL RISK - part of malicious network
                adjustment = -0.25  # Subtract 25% from safety (add to rug probability)
                signals['is_network_member'] = True
                signals['network_risk'] = 'CRITICAL'
                signals['pattern'] = 'coordinated_malicious_network'

        # HIGH: Coordinated with suspicious members only
        elif profile['is_coordinated']:
            adjustment = -0.15  # Subtract 15% safety
            signals['is_network_member'] = True
            signals['network_risk'] = profile['network_risk_level']
            signals['pattern'] = 'coordinated_suspicious_network'

        # Pattern Analysis for Non-Coordinated Creators
        else:
            has_inbound = profile['inbound_funders'] > 0
            has_outbound = profile['outbound_destinations'] > 0

            signals['has_inbound'] = has_inbound
            signals['has_outbound'] = has_outbound

            # Pattern 1: Legitimate funding hub (high inbound, no outbound)
            if has_inbound and not has_outbound:
                if profile['inbound_funders'] >= 5:
                    # Multiple external funders = better legitimacy
                    adjustment = +0.08  # Add 8% safety
                    signals['pattern'] = 'external_funding_hub'
                else:
                    adjustment = +0.03  # Add 3% safety for any inbound
                    signals['pattern'] = 'externally_funded'

            # Pattern 2: Self-funded consolidation (no inbound, some outbound)
            elif not has_inbound and has_outbound:
                if profile['outbound_destinations'] >= 5:
                    # Distributed to 5+ addresses = possible money laundering
                    adjustment = -0.10  # Subtract 10% safety
                    signals['pattern'] = 'distributed_consolidation_suspicious'
                elif profile['outbound_destinations'] >= 2:
                    # Consolidation to 2+ addresses = minor concern
                    adjustment = -0.05  # Subtract 5% safety
                    signals['pattern'] = 'multi_address_consolidation'
                else:
                    adjustment = 0.0  # Neutral
                    signals['pattern'] = 'single_address_consolidation'

            # Pattern 3: Both inbound and outbound (normal operation)
            elif has_inbound and has_outbound:
                # Positive signal: legitimate funding + normal consolidation
                adjustment = +0.05
                signals['pattern'] = 'bidirectional_normal'

            # Pattern 4: Neither (no funding activity tracked)
            else:
                adjustment = 0.0
                signals['pattern'] = 'no_funding_activity'

        # Apply adjustment to base score
        # Since base_score is rug probability (0=safe, 1=rug):
        # - Positive adjustment reduces rug probability (increases safety)
        # - Negative adjustment increases rug probability (reduces safety)

        adjusted_score = max(0.0, min(1.0, base_score - adjustment))

        return adjusted_score, signals

    def format_funding_report(self) -> str:
        """Generate human-readable funding report"""
        profile = self.get_funding_profile()
        adjusted_score, signals = self.calculate_funding_risk_adjustment(0.5)  # Use 0.5 as neutral baseline

        report = "\n" + "="*80 + "\n"
        report += "FUNDING ANALYSIS REPORT\n"
        report += "="*80 + "\n"

        report += f"\nCreator: {self.creator_address[:40]}...\n"

        # Inbound
        report += f"\n📥 PRE-MIGRATION FUNDING (Inbound):\n"
        if profile['inbound_funders'] > 0:
            report += f"   Funders: {profile['inbound_funders']}\n"
            report += f"   Total SOL: {profile['inbound_total_sol']:.4f}\n"
        else:
            report += f"   No external funding detected\n"

        # Outbound
        report += f"\n📤 POST-MIGRATION CONSOLIDATION (Outbound):\n"
        if profile['outbound_destinations'] > 0:
            report += f"   Destinations: {profile['outbound_destinations']}\n"
            report += f"   Total SOL: {profile['outbound_total_sol']:.4f}\n"
            if profile['treasury_addresses']:
                report += f"   Treasury addresses:\n"
                for addr in profile['treasury_addresses'][:3]:
                    report += f"     • {addr}\n"
        else:
            report += f"   No consolidation activity detected\n"

        # Network
        report += f"\n🔗 NETWORK STATUS:\n"
        if profile['is_coordinated']:
            risk_emoji = "🚨" if profile['network_risk_level'] == 'CRITICAL' else "🟠"
            report += f"   {risk_emoji} Member of {profile['network_risk_level']} network\n"
            report += f"   Network ID: {profile['network_id']}\n"
            report += f"   Network size: {profile['network_size']} creators\n"
            report += f"   Co-creators: {profile['co_creators']}\n"
        else:
            report += f"   ✓ No network coordination detected\n"

        # Signals
        report += f"\n📊 FUNDING SIGNALS:\n"
        report += f"   Pattern: {signals['pattern']}\n"
        if signals['has_inbound']:
            report += f"   ✓ Has external funding sources\n"
        if signals['has_outbound']:
            report += f"   ⚠ Has post-migration consolidation\n"
        if signals['is_network_member']:
            report += f"   🚨 Member of {signals['network_risk']} network\n"

        report += "\n" + "="*80 + "\n"

        return report

    def __del__(self):
        """Clean up database connection"""
        try:
            self.conn.close()
        except:
            pass


def test_scoring():
    """Test the funding risk scorer"""
    # Test with known creators
    test_creators = [
        "2NuAgVk3hcb7s4YvP4GjV5fD8eDvZQv5wuN6ZC8igRfV",      # Malicious network member
        "FNkq7bdnsaqwKmu51PpSNZ7fmmMM8rY23scCJ45T53qR",     # Legitimate funding hub
        "npcP7WAHMXC5MzQbwN67pJtarFcsMqro5NUXZ1mn6DQ",      # Blocked with many funders
    ]

    for creator in test_creators:
        try:
            scorer = FundingRiskScorer(creator)
            print(scorer.format_funding_report())

            # Show adjustment
            base_scores = [0.3, 0.5, 0.7]
            print(f"\nRisk Score Adjustments:")
            for base in base_scores:
                adjusted, signals = scorer.calculate_funding_risk_adjustment(base)
                delta = adjusted - base
                sign = "+" if delta > 0 else ""
                print(f"  Base: {base:.1f} → Adjusted: {adjusted:.3f} ({sign}{delta:.3f})")

        except Exception as e:
            print(f"Error testing creator {creator}: {e}")


if __name__ == "__main__":
    test_scoring()
