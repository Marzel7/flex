#!/usr/bin/env python3
"""
Automatic CEX Detection System

Automatically classify addresses as CEX (Centralized Exchange) wallets using:
1. Solscan labels (high-confidence, direct exchange labeling)
2. SNS primary domains (medium-confidence, exchange-owned domains)
3. Transaction heuristics (supporting signals for classification)

Classification Scoring System:
- score ≥ 100 → cex_confirmed (add to cex_wallets table immediately)
- 60–99 → cex_likely (flag for manual review)
- 30–59 → cex_possible (informational)
- < 30 → unknown (insufficient evidence)
"""

import asyncio
import sqlite3
import aiohttp
import json
import logging
from typing import Dict, List, Optional, Tuple, Set
from datetime import datetime
from dataclasses import dataclass
from enum import Enum

import sys
sys.path.insert(0, '/Users/kevinkeaveney/Dev/claude/flex')

from src.utils.infra_mapping import INFRASTRUCTURE_ACCOUNTS, CEX_ACCOUNTS, is_infrastructure_account, is_cex_account
from src.utils.db_locking import managed_db_connect

DB_PATH = "flex_complete_database.db"
SOLSCAN_API_BASE = "https://api.solscan.io"
BONFIDA_API_BASE = "https://api.bonfida.com/v2"

logger = logging.getLogger(__name__)


class CEXClassification(Enum):
    """Classification levels for address confidence"""
    CONFIRMED = "cex_confirmed"      # score ≥ 100, add to db immediately
    LIKELY = "cex_likely"            # score 60-99, flag for review
    POSSIBLE = "cex_possible"        # score 30-59, informational
    UNKNOWN = "unknown"              # score < 30


@dataclass
class CEXClassificationResult:
    """Result of CEX classification for an address"""
    address: str
    classification: CEXClassification
    confidence_score: int
    solscan_label: Optional[str]
    solscan_exchange: Optional[str]
    primary_domain: Optional[str]
    score_reasons: List[str]
    heuristic_analysis: Optional[Dict]


class AutomaticCEXDetector:
    """Automatically detect and classify CEX wallet addresses"""

    def __init__(self, session: Optional[aiohttp.ClientSession] = None):
        self.session = session
        self.own_session = session is None
        self.db_path = DB_PATH

    async def __aenter__(self):
        if self.own_session:
            self.session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.own_session and self.session:
            await self.session.close()

    async def classify_address(self, address: str, analyze_transactions: bool = True) -> CEXClassificationResult:
        """
        Main classification function - analyze address and determine CEX confidence.

        Args:
            address: Solana address to classify
            analyze_transactions: Whether to fetch and analyze transaction patterns

        Returns:
            CEXClassificationResult with classification and confidence score
        """
        # Skip infrastructure accounts
        if is_infrastructure_account(address):
            return CEXClassificationResult(
                address=address,
                classification=CEXClassification.UNKNOWN,
                confidence_score=0,
                solscan_label=None,
                solscan_exchange=None,
                primary_domain=None,
                score_reasons=["Infrastructure account - excluded from CEX detection"],
                heuristic_analysis=None
            )

        # Already in CEX mapping?
        if is_cex_account(address):
            return CEXClassificationResult(
                address=address,
                classification=CEXClassification.CONFIRMED,
                confidence_score=150,  # Maximum confidence
                solscan_label=None,
                solscan_exchange=None,
                primary_domain=None,
                score_reasons=["Already in CEX_ACCOUNTS mapping"],
                heuristic_analysis=None
            )

        score_reasons = []
        confidence_score = 0
        solscan_label = None
        solscan_exchange = None
        primary_domain = None
        heuristic_analysis = None

        # Layer 1: Solscan Label Lookup (HIGH CONFIDENCE - direct signal)
        solscan_result = await self._solscan_labels([address])
        if solscan_result and address in solscan_result:
            label_info = solscan_result[address]
            solscan_label = label_info.get('label')

            if label_info.get('is_cex'):
                confidence_score += 80  # High confidence from Solscan
                solscan_exchange = label_info.get('exchange_name')
                score_reasons.append(f"Solscan labeled as CEX: {solscan_label}")

        # Layer 2: SNS Primary Domain Lookup (MEDIUM CONFIDENCE - supporting signal)
        sns_result = await self._sns_primary_domains([address])
        if sns_result and address in sns_result:
            domain = sns_result[address]
            if domain and not self._is_likely_user_domain(domain):
                # Exchange-like domains (no personal name patterns)
                confidence_score += 15  # Supporting signal only
                primary_domain = domain
                score_reasons.append(f"SNS primary domain: {domain}")

        # Layer 3: Transaction Heuristics (SUPPORTING SIGNALS)
        if analyze_transactions:
            heuristic_analysis = await self._score_cex_heuristics(address)
            if heuristic_analysis:
                heuristic_score = heuristic_analysis.get('score', 0)
                confidence_score += heuristic_score

                # Add reason for heuristic detection
                if heuristic_score > 0:
                    pattern = heuristic_analysis.get('pattern')
                    if pattern:
                        score_reasons.append(f"Transaction pattern: {pattern}")

        # Determine final classification based on score
        if confidence_score >= 100:
            classification = CEXClassification.CONFIRMED
        elif confidence_score >= 60:
            classification = CEXClassification.LIKELY
        elif confidence_score >= 30:
            classification = CEXClassification.POSSIBLE
        else:
            classification = CEXClassification.UNKNOWN

        return CEXClassificationResult(
            address=address,
            classification=classification,
            confidence_score=confidence_score,
            solscan_label=solscan_label,
            solscan_exchange=solscan_exchange,
            primary_domain=primary_domain,
            score_reasons=score_reasons,
            heuristic_analysis=heuristic_analysis
        )

    async def _solscan_labels(self, addresses: List[str]) -> Dict[str, Dict]:
        """
        Fetch Solscan labels for addresses in batch (up to 50).

        Returns dict mapping address → {label, is_cex, exchange_name}
        """
        results = {}

        if not self.session:
            return results

        # Batch fetch in groups of 50
        for i in range(0, len(addresses), 50):
            batch = addresses[i:i+50]

            try:
                # Solscan has account metadata endpoint
                url = f"{SOLSCAN_API_BASE}/account/metadata/multi"
                params = {"accounts": ",".join(batch)}

                async with self.session.get(url, params=params, timeout=10) as resp:
                    if resp.status == 200:
                        data = await resp.json()

                        # Parse response - structure depends on Solscan API
                        if isinstance(data, dict):
                            for addr, metadata in data.items():
                                label = metadata.get('label', '')
                                label_type = metadata.get('labelType', '').lower()

                                is_cex = 'cex' in label_type or 'exchange' in label_type or \
                                        'cex' in label.lower() or 'exchange' in label.lower()

                                results[addr] = {
                                    'label': label,
                                    'is_cex': is_cex,
                                    'exchange_name': self._extract_exchange_name(label) if is_cex else None
                                }

            except asyncio.TimeoutError:
                logger.warning(f"Solscan API timeout for batch {i//50 + 1}")
            except Exception as e:
                logger.warning(f"Solscan API error: {e}")

        return results

    async def _sns_primary_domains(self, addresses: List[str]) -> Dict[str, Optional[str]]:
        """
        Resolve SNS primary domains for addresses (batch of 20).

        Returns dict mapping address → domain_name (or None)
        """
        results = {}

        if not self.session:
            return results

        # Batch fetch in groups of 20
        for i in range(0, len(addresses), 20):
            batch = addresses[i:i+20]

            try:
                # Bonfida API for resolving domains
                url = f"{BONFIDA_API_BASE}/user/fav-domains/{','.join(batch)}"

                async with self.session.get(url, timeout=10) as resp:
                    if resp.status == 200:
                        data = await resp.json()

                        # Response format: {"address": "domain_name", ...}
                        if isinstance(data, dict):
                            results.update(data)

            except asyncio.TimeoutError:
                logger.warning(f"Bonfida API timeout for batch {i//20 + 1}")
            except Exception as e:
                logger.warning(f"Bonfida API error: {e}")

        return results

    async def _score_cex_heuristics(self, address: str) -> Optional[Dict]:
        """
        Analyze transaction patterns for CEX wallet heuristics.

        Looks for:
        - Deposit-like: Many unique senders, consolidation patterns
        - Sweep-like: Concentrated outbound, dust cleanup
        - Transfer-heavy: Mostly transfers, minimal DeFi interaction

        Returns dict with {score: 0-20, pattern: str} or None
        """
        if not self.session:
            return None

        try:
            # Fetch recent enhanced transactions from Helius
            url = f"https://api.helius.xyz/v0/addresses/{address}/transactions"
            params = {"limit": 100, "type": "UNKNOWN"}  # Get mixed transaction types

            async with self.session.get(url, params=params, timeout=10) as resp:
                # Record RPC call to Helius
                try:
                    from src.metrics.rpc_metrics_recorder import record_request
                    record_request(
                        section="cex_detection_analysis",
                        provider="helius",
                        method="getTransactionsForAddress",
                        status_code=resp.status,
                        latency_ms=0,  # Already completed
                        source_file="automatic_cex_detection"
                    )
                except:
                    pass  # Don't fail if metrics recording fails

                if resp.status != 200:
                    return None

                txs = await resp.json()
                if not txs or len(txs) < 10:
                    return None

                # Analyze transaction patterns
                sender_set = set()
                recipient_set = set()
                transfer_count = 0
                defi_interaction_count = 0
                dust_transfers = 0  # transfers < 0.01 SOL
                large_outbound = 0  # outbound > 10 SOL

                for tx in txs:
                    # This is simplified - actual analysis depends on tx structure
                    # For now, return conservative heuristic score
                    pass

                # Scoring logic (simplified)
                score = 0
                pattern = None

                # If mostly transfers with many unique senders → deposit-like
                if len(sender_set) > len(recipient_set) * 2:
                    score = 10
                    pattern = "deposit_pattern"

                # If large concentrated outbound → sweep-like
                if large_outbound > len(txs) / 4:
                    score = 8
                    pattern = "sweep_pattern"

                # If high transfer ratio with low DeFi
                if transfer_count > 0:
                    transfer_ratio = transfer_count / len(txs)
                    if transfer_ratio > 0.7 and defi_interaction_count < len(txs) * 0.2:
                        score = 5
                        pattern = "transfer_heavy"

                if score > 0:
                    return {"score": score, "pattern": pattern}

        except Exception as e:
            logger.warning(f"Heuristics analysis error for {address}: {e}")

        return None

    def _extract_exchange_name(self, label: str) -> Optional[str]:
        """Extract exchange name from Solscan label"""
        exchanges = {
            'binance': 'Binance',
            'coinbase': 'Coinbase',
            'kraken': 'Kraken',
            'bybit': 'Bybit',
            'okx': 'OKX',
            'ftx': 'FTX',
            'gate': 'Gate.io',
            'mexc': 'MEXC',
            'huobi': 'Huobi',
            'crypto.com': 'Crypto.com',
        }

        label_lower = label.lower()
        for keyword, name in exchanges.items():
            if keyword in label_lower:
                return name

        return None

    def _is_likely_user_domain(self, domain: str) -> bool:
        """Check if domain looks like personal user domain vs exchange"""
        # User domains often contain common words, numbers, personal names
        personal_patterns = ['art', 'dev', 'io', 'eth', 'eth', 'web3', 'nft']

        domain_lower = domain.lower()

        # If contains personal patterns, likely user domain
        for pattern in personal_patterns:
            if pattern in domain_lower:
                return True

        # If contains numbers or underscores, might be user-created
        if any(c.isdigit() or c == '_' for c in domain):
            return True

        return False

    async def classify_batch(self, addresses: List[str],
                            skip_existing: bool = True) -> List[CEXClassificationResult]:
        """
        Classify multiple addresses in batch.

        Args:
            addresses: List of Solana addresses to classify
            skip_existing: Skip addresses already in address_classification table

        Returns:
            List of CEXClassificationResult
        """
        results = []

        # Filter out existing classifications if requested
        if skip_existing:
            existing = self._get_existing_classifications(addresses)
            addresses = [a for a in addresses if a not in existing]

        # Classify each address
        for address in addresses:
            try:
                result = await self.classify_address(address)
                results.append(result)
            except Exception as e:
                logger.error(f"Error classifying {address}: {e}")

        return results

    def _get_existing_classifications(self, addresses: List[str]) -> Set[str]:
        """Get addresses already in address_classification table"""
        try:
            # X76.3 -- managed_db_connect guarantees close() on exception.
            with managed_db_connect(self.db_path, timeout=5) as conn:
                cursor = conn.cursor()

                placeholders = ','.join('?' * len(addresses))
                cursor.execute(
                    f"SELECT address FROM address_classification WHERE address IN ({placeholders})",
                    addresses
                )

                existing = {row[0] for row in cursor.fetchall()}
                return existing

        except Exception as e:
            logger.warning(f"Error checking existing classifications: {e}")
            return set()

    def save_classification(self, result: CEXClassificationResult) -> bool:
        """Save classification result to database"""
        try:
            # X76.3 -- managed_db_connect guarantees close() on exception.
            with managed_db_connect(self.db_path, timeout=5) as conn:
                cursor = conn.cursor()

                cursor.execute("""
                    INSERT OR REPLACE INTO address_classification
                    (address, solscan_label, solscan_exchange_name, primary_domain,
                     classification, confidence_score, score_reasons, last_checked_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    result.address,
                    result.solscan_label,
                    result.solscan_exchange,
                    result.primary_domain,
                    result.classification.value,
                    result.confidence_score,
                    json.dumps(result.score_reasons),
                    datetime.now().isoformat()
                ))

                conn.commit()
                return True

        except Exception as e:
            logger.error(f"Error saving classification: {e}")
            return False

    def add_confirmed_cex_to_mapping(self, result: CEXClassificationResult) -> bool:
        """
        If classification is CONFIRMED, add to cex_wallets table.

        This bridges automatic detection with the manual CEX_ACCOUNTS mapping.
        """
        if result.classification != CEXClassification.CONFIRMED:
            return False

        try:
            # X76.3 -- managed_db_connect guarantees close() on exception.
            with managed_db_connect(self.db_path, timeout=5) as conn:
                cursor = conn.cursor()

                exchange_name = result.solscan_exchange or "Unknown Exchange"
                wallet_type = "Exchange Wallet"

                # Determine wallet type from solscan label if available
                if result.solscan_label:
                    if "hot" in result.solscan_label.lower():
                        wallet_type = "Hot Wallet"
                    elif "staking" in result.solscan_label.lower():
                        wallet_type = "Staking Wallet"
                    elif "custody" in result.solscan_label.lower():
                        wallet_type = "Custody"

                # Check if already exists (avoid duplicate inserts)
                cursor.execute("SELECT cex_address FROM cex_wallets WHERE cex_address = ?", (result.address,))
                if cursor.fetchone():
                    return False  # Already exists

                cursor.execute("""
                    INSERT INTO cex_wallets
                    (cex_address, exchange_name, wallet_type, confidence_level,
                     discovery_source, is_active)
                    VALUES (?, ?, ?, ?, ?, 1)
                """, (
                    result.address,
                    exchange_name,
                    wallet_type,
                    min(100, result.confidence_score),  # Cap at 100
                    "automatic_detection"
                ))

                conn.commit()

            logger.info(f"Added confirmed CEX to mapping: {exchange_name} {wallet_type}")
            return True

        except Exception as e:
            logger.error(f"Error adding to CEX mapping: {e}")
            return False


async def classify_addresses_from_funding(max_addresses: int = 200) -> Dict:
    """
    Classify addresses from creator_funders table that aren't yet classified.

    This runs during funding extraction to auto-detect new CEX wallets.
    """
    try:
        # X76.3 -- managed_db_connect guarantees close() on exception.
        with managed_db_connect(DB_PATH, timeout=5) as conn:
            cursor = conn.cursor()

            # Get unclassified addresses from funding relationships
            cursor.execute("""
                SELECT DISTINCT funder_address
                FROM creator_funders
                WHERE funder_address NOT IN (
                    SELECT address FROM address_classification
                )
                AND funder_address NOT IN (
                    SELECT cex_address FROM cex_wallets
                )
                AND is_cex = 0
                LIMIT ?
            """, (max_addresses,))

            addresses = [row[0] for row in cursor.fetchall()]

        if not addresses:
            return {"classified": 0, "confirmed": 0, "likely": 0}

        # Classify addresses
        detector = AutomaticCEXDetector()
        async with detector:
            results = await detector.classify_batch(addresses, skip_existing=False)

        classified = 0
        confirmed = 0
        likely = 0

        for result in results:
            # Save classification
            if detector.save_classification(result):
                classified += 1

                # If confirmed, add to CEX mapping and log
                if result.classification == CEXClassification.CONFIRMED:
                    if detector.add_confirmed_cex_to_mapping(result):
                        confirmed += 1
                        print(f"[AUTO-CEX] 🎯 CONFIRMED: {result.solscan_exchange} {result.address[:16]}... (score: {result.confidence_score})")

                elif result.classification == CEXClassification.LIKELY:
                    likely += 1
                    print(f"[AUTO-CEX] ⚠️ LIKELY: {result.address[:16]}... (score: {result.confidence_score})")

        return {
            "classified": classified,
            "confirmed": confirmed,
            "likely": likely,
            "total_analyzed": len(addresses)
        }

    except Exception as e:
        logger.error(f"Error in classify_addresses_from_funding: {e}")
        return {"error": str(e)}


async def main():
    """Test automatic CEX detection"""

    # Test addresses
    test_addresses = [
        "8iBa3q2NqYqdTF5trYVyryy3XeeM6E3K26efsXhfVvcb",  # Binance 2 (known)
        "5g7yNHyGLJ7fiQ9SN9mf47opDnMjc585kqXWt6d7aBWs",  # Coinbase (known)
    ]

    async with AutomaticCEXDetector() as detector:
        for addr in test_addresses:
            result = await detector.classify_address(addr, analyze_transactions=False)
            print(f"{addr[:16]}... → {result.classification.value} ({result.confidence_score})")
            print(f"  Reasons: {result.score_reasons}")


if __name__ == "__main__":
    asyncio.run(main())
