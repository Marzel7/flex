#!/usr/bin/env python3
"""
Creator Blocklist Checker

Provides utilities to check if a token's creator is in the blocklist.
Used for pre-buy filtering in trading bot.

All blocklist data is stored in the database for persistence and querying.
"""

import json
import sqlite3
from typing import Optional, Dict, Tuple


class CreatorBlocklistChecker:
    """Check tokens against creator blocklist stored in database"""

    def __init__(self, db_path: str = "pumpswap_tokens.db"):
        self.db_path = db_path

    def _get_token_creator(self, token_mint: str) -> Optional[str]:
        """Get creator for token from database"""
        try:
            conn = sqlite3.connect(self.db_path, timeout=60)
            cursor = conn.cursor()

            cursor.execute("SELECT earliest_tx_creator FROM token_analysis WHERE mint = ?", (token_mint,))
            row = cursor.fetchone()
            conn.close()

            if row and row[0]:
                return row[0]
            return None
        except Exception as e:
            print(f"[DB_ERROR] Failed to get creator: {e}")
            return None

    def _get_creator_blocklist_entry(self, creator_address: str) -> Optional[Dict]:
        """Get blocklist entry for creator from database"""
        try:
            conn = sqlite3.connect(self.db_path, timeout=60)
            cursor = conn.cursor()

            cursor.execute(
                "SELECT creator_address, rug_count, reputation, rugged_tokens, first_rug_detected_at, last_rug_detected_at FROM creator_blocklist WHERE creator_address = ?",
                (creator_address,)
            )
            row = cursor.fetchone()
            conn.close()

            if not row:
                return None

            creator, rug_count, reputation, rugged_tokens_json, first_detected, last_detected = row

            try:
                rugged_tokens = json.loads(rugged_tokens_json) if rugged_tokens_json else []
            except:
                rugged_tokens = []

            return {
                "creator": creator,
                "rug_count": rug_count,
                "reputation": reputation,
                "rugged_tokens": rugged_tokens,
                "first_rug_detected_at": first_detected,
                "last_rug_detected_at": last_detected,
                "status": "blocked"
            }
        except Exception as e:
            print(f"[DB_ERROR] Failed to get creator blocklist entry: {e}")
            return None

    def _check_network_risk(self, creator_address: str) -> Optional[Dict]:
        """Check if creator is connected to malicious creators"""
        try:
            conn = sqlite3.connect(self.db_path, timeout=60)
            cursor = conn.cursor()

            cursor.execute("""
                SELECT connected_to_malicious, network_members
                FROM creator_blocklist
                WHERE creator_address = ?
            """, (creator_address,))
            row = cursor.fetchone()
            conn.close()

            if not row:
                return None

            connected, network_json = row

            if connected:
                try:
                    network_members = json.loads(network_json) if network_json else []
                except:
                    network_members = []

                return {
                    "is_connected": True,
                    "connected_members": network_members
                }
            return None
        except Exception as e:
            print(f"[DB_ERROR] Failed to check network: {e}")
            return None

    def check_token(self, token_mint: str) -> Tuple[bool, str]:
        """
        Check if token is safe to buy.

        Returns:
            (is_safe: bool, reason: str)
            - (True, "OK") if token is safe
            - (False, reason) if token is blocked
        """
        # Get creator from database
        creator = self._get_token_creator(token_mint)

        if not creator:
            return True, "Creator unknown (not checked against blocklist)"

        # Check if creator is in blocklist
        blocklist_entry = self._get_creator_blocklist_entry(creator)

        if not blocklist_entry:
            # Check if creator is connected to malicious creators (network risk)
            network_risk = self._check_network_risk(creator)
            if network_risk:
                return False, f"🔗 NETWORK RISK - Connected to {len(network_risk['connected_members'])} malicious creator(s)"
            return True, "Creator not in blocklist"

        # Creator is in blocklist - check their history
        rug_count = blocklist_entry.get("rug_count", 0)

        if rug_count >= 2:
            return False, f"🚨 SERIAL RUGGER - Creator has {rug_count} confirmed rugs"
        else:
            return False, f"📝 WATCH LIST - Creator has {rug_count} rug, use caution"

    def get_creator_info(self, token_mint: str) -> Optional[Dict]:
        """Get detailed creator information"""
        creator = self._get_token_creator(token_mint)

        if not creator:
            return None

        return self._get_creator_blocklist_entry(creator)

    def get_all_blocked_creators(self) -> list:
        """Get all creators in blocklist"""
        try:
            conn = sqlite3.connect(self.db_path, timeout=60)
            cursor = conn.cursor()

            cursor.execute("SELECT creator_address, rug_count, reputation FROM creator_blocklist ORDER BY rug_count DESC")
            rows = cursor.fetchall()
            conn.close()

            return [
                {
                    "creator": row[0],
                    "rug_count": row[1],
                    "reputation": row[2]
                }
                for row in rows
            ]
        except Exception as e:
            print(f"[DB_ERROR] Failed to get blocked creators: {e}")
            return []

    def get_blocklist_stats(self) -> Dict:
        """Get blocklist statistics"""
        try:
            conn = sqlite3.connect(self.db_path, timeout=60)
            cursor = conn.cursor()

            # Total blocked creators
            cursor.execute("SELECT COUNT(*) FROM creator_blocklist")
            total = cursor.fetchone()[0]

            # Malicious (2+ rugs)
            cursor.execute("SELECT COUNT(*) FROM creator_blocklist WHERE rug_count >= 2")
            malicious = cursor.fetchone()[0]

            # Suspicious (1 rug)
            cursor.execute("SELECT COUNT(*) FROM creator_blocklist WHERE rug_count = 1")
            suspicious = cursor.fetchone()[0]

            # Total rugs across all creators
            cursor.execute("SELECT SUM(rug_count) FROM creator_blocklist")
            total_rugs = cursor.fetchone()[0] or 0

            conn.close()

            return {
                "total_blocked_creators": total,
                "malicious": malicious,
                "suspicious": suspicious,
                "total_rugs_detected": total_rugs
            }
        except Exception as e:
            print(f"[DB_ERROR] Failed to get blocklist stats: {e}")
            return {}


# Singleton instance
_checker = None


def get_checker(db_path: str = "pumpswap_tokens.db") -> CreatorBlocklistChecker:
    """Get or create the blocklist checker instance"""
    global _checker
    if _checker is None:
        _checker = CreatorBlocklistChecker(db_path)
    return _checker


def check_token_safety(token_mint: str) -> Tuple[bool, str]:
    """
    Quick check if token is safe to buy.

    Usage:
        is_safe, reason = check_token_safety(mint)
        if not is_safe:
            print(f"Skipping: {reason}")
            return
    """
    checker = get_checker()
    return checker.check_token(token_mint)


def get_token_creator_info(token_mint: str) -> Optional[Dict]:
    """Get creator info for a token"""
    checker = get_checker()
    return checker.get_creator_info(token_mint)


def get_all_blocked_creators() -> list:
    """Get all creators in blocklist"""
    checker = get_checker()
    return checker.get_all_blocked_creators()


def get_blocklist_stats() -> Dict:
    """Get blocklist statistics"""
    checker = get_checker()
    return checker.get_blocklist_stats()
