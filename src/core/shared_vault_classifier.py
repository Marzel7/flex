"""
Shared Vault Classification

Detects and classifies program-level shared vaults (like ADyA...) vs token-specific accounts.

This enables:
- Correct vault identification (not pool_address fallback)
- Infrastructure clustering (group tokens by shared vault)
- Launch pattern detection (coordinated deployments)
- Risk assessment (shared vault concentration)

Design:
- Deterministic (no ML)
- Simple thresholds based on reuse count
- SQL-driven for efficiency
- Cache-friendly
"""

import logging
import sqlite3
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Classification thresholds
SHARED_VAULT_MIN_REUSE = 5  # Account used by 5+ tokens = shared vault
SHARED_VAULT_SIGNATURE_MIN = 10  # 10+ tokens = definitive shared vault


class VaultClassifier:
    """Classify accounts as shared vaults or token-specific."""

    def __init__(self, db_path: str = 'database/flex_complete_database.db'):
        self.db_path = db_path

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_account_usage_cache(self) -> None:
        """Build or refresh cache of account reuse counts."""
        conn = self._get_conn()
        cursor = conn.cursor()

        try:
            # Create cache table if not exists
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS account_usage_cache (
                    account_address TEXT PRIMARY KEY,
                    token_count INTEGER NOT NULL,
                    last_updated INTEGER NOT NULL
                )
            """)

            # Refresh: count each pool_address usage (shared vaults)
            cursor.execute("""
                DELETE FROM account_usage_cache
            """)

            cursor.execute("""
                INSERT INTO account_usage_cache (account_address, token_count, last_updated)
                SELECT
                    pool_address,
                    COUNT(DISTINCT mint) as token_count,
                    CAST(strftime('%s', 'now') AS INTEGER) as last_updated
                FROM token_pool_accounts
                WHERE pool_address IS NOT NULL
                GROUP BY pool_address
            """)

            conn.commit()
            logger.info("Account usage cache refreshed")

        except Exception as e:
            logger.error(f"Error building account cache: {e}")
        finally:
            conn.close()

    def classify_account(self, account_address: str) -> str:
        """
        Classify a single account.

        Returns:
            'shared_program_vault' - Used by 5+ tokens
            'shared_vault_signature' - Used by 10+ tokens (definitive pump.fun style)
            'token_vault' - Used by 1 token
            'unknown' - Not found or error
        """
        if not account_address:
            return 'unknown'

        conn = self._get_conn()
        cursor = conn.cursor()

        try:
            cursor.execute("""
                SELECT token_count FROM account_usage_cache
                WHERE account_address = ?
            """, (account_address,))

            row = cursor.fetchone()
            if not row:
                return 'unknown'

            count = row['token_count']

            if count >= SHARED_VAULT_SIGNATURE_MIN:
                return 'shared_vault_signature'
            elif count >= SHARED_VAULT_MIN_REUSE:
                return 'shared_program_vault'
            elif count == 1:
                return 'token_vault'
            else:
                return 'unknown'

        except Exception as e:
            logger.error(f"Error classifying account {account_address}: {e}")
            return 'unknown'
        finally:
            conn.close()

    def get_account_type_label(self, classification: str) -> str:
        """
        Get human-readable label for classification.

        Examples:
            'shared_vault_signature' → 'Shared Vault (pump.fun)'
            'shared_program_vault' → 'Shared Vault (Program)'
            'token_vault' → 'Token Vault'
            'unknown' → 'Unknown'
        """
        labels = {
            'shared_vault_signature': 'Shared Vault (pump.fun)',
            'shared_program_vault': 'Shared Vault (Program)',
            'token_vault': 'Token Vault',
            'amm_pool': 'AMM Pool',
            'unknown': 'Unknown',
        }
        return labels.get(classification, 'Unknown')

    def get_shared_vaults(self, min_reuse: int = SHARED_VAULT_MIN_REUSE) -> List[Dict]:
        """
        Get list of all shared vault accounts.

        Args:
            min_reuse: Minimum reuse count to be considered shared

        Returns:
            List of dicts with account_address, token_count, classification
        """
        conn = self._get_conn()
        cursor = conn.cursor()

        try:
            cursor.execute("""
                SELECT
                    account_address,
                    token_count,
                    CASE
                        WHEN token_count >= ? THEN 'shared_vault_signature'
                        WHEN token_count >= ? THEN 'shared_program_vault'
                        ELSE 'token_vault'
                    END AS classification
                FROM account_usage_cache
                WHERE token_count >= ?
                ORDER BY token_count DESC
            """, (SHARED_VAULT_SIGNATURE_MIN, SHARED_VAULT_MIN_REUSE, min_reuse))

            results = []
            for row in cursor.fetchall():
                results.append({
                    'account_address': row['account_address'],
                    'token_count': row['token_count'],
                    'classification': row['classification'],
                    'label': self.get_account_type_label(row['classification']),
                })
            return results

        except Exception as e:
            logger.error(f"Error getting shared vaults: {e}")
            return []
        finally:
            conn.close()

    def get_tokens_by_shared_vault(
        self, vault_address: str
    ) -> List[Dict]:
        """
        Get all tokens using a specific shared vault.

        Returns:
            List of dicts with mint, discovery_method, created_at
        """
        conn = self._get_conn()
        cursor = conn.cursor()

        try:
            cursor.execute("""
                SELECT
                    mint,
                    discovery_method,
                    created_at
                FROM token_pool_accounts
                WHERE pool_address = ?
                ORDER BY created_at ASC
            """, (vault_address,))

            results = []
            for row in cursor.fetchall():
                results.append({
                    'mint': row['mint'],
                    'discovery_method': row['discovery_method'],
                    'created_at': row['created_at'],
                })
            return results

        except Exception as e:
            logger.error(f"Error getting tokens for vault {vault_address}: {e}")
            return []
        finally:
            conn.close()

    def detect_launch_clusters(self) -> List[Dict]:
        """
        Detect token clusters (coordinated launches via shared vault).

        Returns:
            List of clusters with vault_address, token_count, time_window, tokens
        """
        shared_vaults = self.get_shared_vaults(min_reuse=5)
        clusters = []

        for vault_info in shared_vaults:
            tokens = self.get_tokens_by_shared_vault(vault_info['account_address'])

            if len(tokens) < 5:
                continue

            # Calculate time window (first to last token)
            created_times = [t['created_at'] for t in tokens if t['created_at']]
            if not created_times:
                continue

            time_window = max(created_times) - min(created_times)
            time_window_minutes = time_window // 60 if time_window else 0

            clusters.append({
                'vault_address': vault_info['account_address'],
                'vault_label': vault_info['label'],
                'token_count': len(tokens),
                'time_window_minutes': time_window_minutes,
                'first_token_created_at': min(created_times),
                'last_token_created_at': max(created_times),
                'tokens': tokens,
            })

        return sorted(clusters, key=lambda x: x['token_count'], reverse=True)


# Global instance
_classifier = None


def get_classifier() -> VaultClassifier:
    """Get or create global classifier instance."""
    global _classifier
    if _classifier is None:
        _classifier = VaultClassifier()
        _classifier._ensure_account_usage_cache()
    return _classifier


def classify_account(account_address: str) -> str:
    """Convenience function to classify an account."""
    return get_classifier().classify_account(account_address)


def get_account_type_label(classification: str) -> str:
    """Convenience function to get label."""
    return get_classifier().get_account_type_label(classification)


if __name__ == '__main__':
    import json

    logging.basicConfig(level=logging.INFO)

    classifier = VaultClassifier()
    classifier._ensure_account_usage_cache()

    print("\n=== Shared Vaults ===")
    shared = classifier.get_shared_vaults(min_reuse=5)
    print(json.dumps(shared, indent=2))

    print("\n=== Launch Clusters ===")
    clusters = classifier.detect_launch_clusters()
    for cluster in clusters:
        print(f"\nVault: {cluster['vault_address']}")
        print(f"Tokens: {cluster['token_count']}")
        print(f"Time window: {cluster['time_window_minutes']} minutes")
        print(f"First: {cluster['first_token_created_at']}")
        print(f"Last: {cluster['last_token_created_at']}")
