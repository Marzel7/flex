#!/usr/bin/env python3
"""
Post-Launch Automation Coordinator

After token creation and funding extraction, automatically:
1. Assign networks to creators based on funding patterns
2. Update Top Funding Distribution Senders metrics
3. Detect and update Coordinated Funders
4. Rebuild clusters and super-clusters
5. Emit WebSocket updates to UI for real-time dashboard refresh
"""

import sqlite3
import asyncio
import aiohttp
from typing import Dict, List, Optional
from datetime import datetime
import json

DB_PATH = "pumpswap_tokens.db"


class PostLaunchAutomationCoordinator:
    """Coordinates all post-launch analytics and clustering tasks"""

    def __init__(self, websocket_manager=None):
        """
        Initialize post-launch automation coordinator

        Args:
            websocket_manager: Optional WebSocket manager for real-time UI updates
        """
        self.db_path = DB_PATH
        self.websocket_manager = websocket_manager
        self.metrics = {}

    async def run_post_launch_automation(self, creator: str, mint: str, total_funders: int, total_sol: float):
        """
        Run complete post-launch automation workflow

        Called after creator funding extraction is complete.
        Triggers all downstream tasks needed to update analytics and clustering.

        Args:
            creator: Creator address
            mint: Token mint address
            total_funders: Total number of unique funders for this creator
            total_sol: Total SOL funding received
        """
        print(f"[POST_LAUNCH] 🚀 Starting automation for creator {creator[:16]}...", flush=True)

        try:
            # 1. Assign network to creator if not already assigned
            await self._assign_creator_network(creator)

            # 2. Update Top Funding Distribution Senders metrics
            await self._update_funding_distribution_metrics(creator)

            # 3. Detect coordinated funders
            await self._detect_coordinated_funders(creator)

            # 4. Rebuild clusters
            await self._rebuild_clusters_for_creator(creator)

            # 5. Emit WebSocket update to UI
            await self._emit_ui_update(creator, mint, total_funders, total_sol)

            print(f"[POST_LAUNCH] ✅ Automation complete for {creator[:16]}...", flush=True)

        except Exception as e:
            print(f"[POST_LAUNCH] ❌ Error in post-launch automation: {e}", flush=True)
            import traceback
            traceback.print_exc()

    async def _assign_creator_network(self, creator: str):
        """
        Assign network to creator based on funding relationships

        Analyzes shared funders with existing creators to find network membership.
        """
        try:
            conn = sqlite3.connect(self.db_path, timeout=60)
            cursor = conn.cursor()

            # Check if creator already has a network assigned
            cursor.execute("""
                SELECT network_id FROM creator_networks
                WHERE creator_address = ?
            """, (creator,))

            existing = cursor.fetchone()
            if existing:
                print(f"[NETWORK] ℹ Creator {creator[:16]}... already assigned to network {existing[0]}", flush=True)
                conn.close()
                return

            # Find creators with shared funders
            cursor.execute("""
                SELECT DISTINCT cf2.creator_address, COUNT(DISTINCT cf1.funder_address) as shared_funder_count
                FROM creator_funders cf1
                JOIN creator_funders cf2 ON cf1.funder_address = cf2.funder_address
                WHERE cf1.creator_address = ?
                AND cf2.creator_address != ?
                GROUP BY cf2.creator_address
                HAVING shared_funder_count >= 2
                ORDER BY shared_funder_count DESC
                LIMIT 10
            """, (creator, creator))

            connected_creators = cursor.fetchall()

            if connected_creators:
                print(f"[NETWORK] 🔗 Found {len(connected_creators)} connected creators for {creator[:16]}...", flush=True)

                # Store connected creators relationship
                connected_list = [addr for addr, count in connected_creators]

                cursor.execute("""
                    INSERT OR REPLACE INTO creator_networks
                    (creator_address, connected_creators, network_size, network_risk_level, detected_at)
                    VALUES (?, ?, ?, 'MEDIUM', CURRENT_TIMESTAMP)
                """, (creator, json.dumps(connected_list), len(connected_list) + 1))

                conn.commit()
                print(f"[NETWORK] ✅ Network assigned to creator {creator[:16]}...", flush=True)
            else:
                print(f"[NETWORK] 🔹 Creator {creator[:16]}... appears independent (no shared funders)", flush=True)

            conn.close()

        except Exception as e:
            print(f"[NETWORK] ⚠ Error assigning network: {e}", flush=True)

    async def _update_funding_distribution_metrics(self, creator: str):
        """
        Update Top Funding Distribution Senders metrics

        For each funder of this creator, update their participation metrics.
        """
        try:
            conn = sqlite3.connect(self.db_path, timeout=60)
            cursor = conn.cursor()

            # Get all funders for this creator
            cursor.execute("""
                SELECT funder_address, amount_sol
                FROM creator_funders
                WHERE creator_address = ?
            """, (creator,))

            funders = cursor.fetchall()
            print(f"[METRICS] 📊 Updating metrics for {len(funders)} funders", flush=True)

            for funder_addr, amount_sol in funders:
                # Count how many creators this funder funds
                cursor.execute("""
                    SELECT COUNT(DISTINCT creator_address)
                    FROM creator_funders
                    WHERE funder_address = ?
                """, (funder_addr,))

                creator_count = cursor.fetchone()[0]

                # Count how many tokens these creators have launched
                cursor.execute("""
                    SELECT COUNT(DISTINCT ta.mint)
                    FROM token_analysis ta
                    WHERE ta.earliest_tx_creator IN (
                        SELECT DISTINCT creator_address
                        FROM creator_funders
                        WHERE funder_address = ?
                    )
                """, (funder_addr,))

                token_count = cursor.fetchone()[0]

                # Update or create funder distribution record
                cursor.execute("""
                    INSERT OR REPLACE INTO funder_distribution_metrics
                    (funder_address, creators_funded, tokens_created, last_updated)
                    VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                """, (funder_addr, creator_count, token_count))

            conn.commit()
            conn.close()

            print(f"[METRICS] ✅ Updated metrics for {len(funders)} funders", flush=True)

        except Exception as e:
            print(f"[METRICS] ⚠ Error updating metrics: {e}", flush=True)

    async def _detect_coordinated_funders(self, creator: str):
        """
        Detect if this creator is part of a coordinated funding pattern

        Identifies funders that fund multiple creators (coordination indicator).
        """
        try:
            conn = sqlite3.connect(self.db_path, timeout=60)
            cursor = conn.cursor()

            # Get funders for this creator
            cursor.execute("""
                SELECT funder_address FROM creator_funders
                WHERE creator_address = ?
            """, (creator,))

            creator_funders = [row[0] for row in cursor.fetchall()]

            if not creator_funders:
                print(f"[COORDINATION] ℹ No funders for creator {creator[:16]}...", flush=True)
                conn.close()
                return

            # Find which of these funders also fund other creators
            placeholders = ','.join(['?' for _ in creator_funders])
            query = f"""
                SELECT funder_address, COUNT(DISTINCT creator_address) as creator_count
                FROM creator_funders
                WHERE funder_address IN ({placeholders})
                GROUP BY funder_address
                HAVING creator_count > 1
            """

            cursor.execute(query, creator_funders)
            coordinated_funders = cursor.fetchall()

            if coordinated_funders:
                print(f"[COORDINATION] 🔴 Found {len(coordinated_funders)} coordinated funders", flush=True)

                for funder_addr, creator_count in coordinated_funders:
                    # Get list of all creators funded by this funder
                    cursor.execute("""
                        SELECT DISTINCT creator_address
                        FROM creator_funders
                        WHERE funder_address = ?
                    """, (funder_addr,))

                    funded_creators = [row[0] for row in cursor.fetchall()]

                    # Store in coordinated_funders table
                    cursor.execute("""
                        INSERT OR REPLACE INTO coordinated_funders
                        (funder_address, creator_count, creator_addresses, risk_level, detected_at)
                        VALUES (?, ?, ?, 'HIGH', CURRENT_TIMESTAMP)
                    """, (funder_addr, len(funded_creators), json.dumps(funded_creators)))

                conn.commit()
                print(f"[COORDINATION] ✅ Registered {len(coordinated_funders)} coordinated funders", flush=True)
            else:
                print(f"[COORDINATION] ✅ Creator {creator[:16]}... has unique funders (no coordination detected)", flush=True)

            conn.close()

        except Exception as e:
            print(f"[COORDINATION] ⚠ Error detecting coordinated funders: {e}", flush=True)

    async def _rebuild_clusters_for_creator(self, creator: str):
        """
        Rebuild clusters to include this new creator

        Updates unified_creator_clusters based on funding relationships.
        """
        try:
            conn = sqlite3.connect(self.db_path, timeout=60)
            cursor = conn.cursor()

            # Get all funders and recipients for this creator
            cursor.execute("""
                SELECT DISTINCT funder_address FROM creator_funders
                WHERE creator_address = ?
            """, (creator,))

            funders = [row[0] for row in cursor.fetchall()]

            cursor.execute("""
                SELECT DISTINCT receiver_address FROM creator_receivers
                WHERE creator_address = ?
            """, (creator,))

            recipients = [row[0] for row in cursor.fetchall()]

            # Create cluster entry
            cluster_members = [creator] + funders + recipients
            cluster_id = hash(tuple(sorted(cluster_members))) % (10 ** 8)

            cursor.execute("""
                INSERT OR REPLACE INTO unified_creator_clusters
                (cluster_id, member_addresses, cluster_size, risk_level, created_at)
                VALUES (?, ?, ?, 'MEDIUM', CURRENT_TIMESTAMP)
            """, (cluster_id, json.dumps(cluster_members), len(cluster_members)))

            conn.commit()
            conn.close()

            print(f"[CLUSTERS] ✅ Rebuilt clusters for {creator[:16]}... (cluster_id: {cluster_id})", flush=True)

        except Exception as e:
            print(f"[CLUSTERS] ⚠ Error rebuilding clusters: {e}", flush=True)

    async def _emit_ui_update(self, creator: str, mint: str, total_funders: int, total_sol: float):
        """
        Emit WebSocket update to UI for real-time dashboard refresh

        Signals UI to refresh:
        - Creator details
        - Funding distribution charts
        - Cluster visualizations
        - Network assignments
        """
        if not self.websocket_manager:
            print(f"[UI] ℹ No WebSocket manager - skipping UI update", flush=True)
            return

        try:
            # Prepare update payload
            update_payload = {
                "type": "post_launch_complete",
                "creator": creator,
                "mint": mint,
                "total_funders": total_funders,
                "total_sol": total_sol,
                "timestamp": datetime.utcnow().isoformat(),
                "updates": {
                    "creator_details": True,
                    "funding_distribution": True,
                    "coordinated_funders": True,
                    "clusters": True,
                    "networks": True
                }
            }

            # Broadcast to all connected clients
            await self.websocket_manager.broadcast({
                "event": "post_launch_automation_complete",
                "data": update_payload
            })

            print(f"[UI] ✅ WebSocket update emitted for {creator[:16]}...", flush=True)

        except Exception as e:
            print(f"[UI] ⚠ Error emitting WebSocket update: {e}", flush=True)


# Global singleton
_coordinator = None


def get_post_launch_coordinator(websocket_manager=None):
    """Get or create post-launch automation coordinator"""
    global _coordinator
    if _coordinator is None:
        _coordinator = PostLaunchAutomationCoordinator(websocket_manager)
    return _coordinator


async def run_post_launch_automation(creator: str, mint: str, total_funders: int, total_sol: float, websocket_manager=None):
    """
    Convenience function to run post-launch automation

    Args:
        creator: Creator address
        mint: Token mint
        total_funders: Total unique funders
        total_sol: Total SOL received
        websocket_manager: Optional WebSocket manager for UI updates
    """
    coordinator = get_post_launch_coordinator(websocket_manager)
    await coordinator.run_post_launch_automation(creator, mint, total_funders, total_sol)
