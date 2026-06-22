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
from src.analysis.cross_funding_network_analyzer import CrossFundingClusterAnalyzer
from src.analysis.watchtower_detector import analyze_creator_from_conn, ensure_schema as wt_ensure_schema

import os as _os
DB_PATH = _os.environ.get(
    "DB_PATH",
    _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "../../database/flex_complete_database.db"),
)


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

        # Track what was updated for UI refresh
        updates = {
            "creator_tags": False,
            "networks": False,
            "metrics": False,
            "coordinated_funders": False,
            "clusters": False
        }

        try:
            # 0. TAG CREATOR based on funding patterns
            # DISABLED: Automatic funding-based tags (cex_funded, heavy_funded, etc.) should not be created
            # Only legitimate service tags (uses_jitotip, uses_axiom, etc.) should appear in creator_tags
            # tags_added = await self._tag_creator_from_funding_patterns(creator, mint, total_funders, total_sol)
            # if tags_added:
            #     updates["creator_tags"] = True

            # 1. Assign network to creator if not already assigned
            network_assigned = await self._assign_creator_network(creator)
            if network_assigned:
                updates["networks"] = True

            # 2. Update Top Funding Distribution Senders metrics
            await self._update_funding_distribution_metrics(creator)
            updates["metrics"] = True

            # 3. Detect coordinated funders
            coordinated_found = await self._detect_coordinated_funders(creator)
            if coordinated_found:
                updates["coordinated_funders"] = True

            # 3.5. Comprehensive network analysis using CrossFundingClusterAnalyzer
            # Gated: rebuilds 142k-creator graph synchronously on event loop — parked during recovery.
            # Re-enable via CROSS_FUNDING_CLUSTER_ANALYZER_ENABLED=1
            if _os.environ.get("CROSS_FUNDING_CLUSTER_ANALYZER_ENABLED", "0") == "1":
                await self._analyze_creator_network_using_cluster_analyzer(creator)

            # 4. Rebuild clusters
            await self._rebuild_clusters_for_creator(creator)
            updates["clusters"] = True

            # 5. WATCHTOWER detection — runs against creator_funders which is now populated
            await self._check_watchtower_linkage(creator, mint)

            # 6. Emit UI update with what was actually changed
            await self._emit_ui_update(creator, mint, total_funders, total_sol, updates)

            print(f"[POST_LAUNCH] ✅ Automation complete for {creator[:16]}...", flush=True)

        except Exception as e:
            print(f"[POST_LAUNCH] ❌ Error in post-launch automation: {e}", flush=True)
            import traceback
            traceback.print_exc()

    async def _tag_creator_from_funding_patterns(self, creator: str, mint: str, total_funders: int, total_sol: float):
        """
        PERMANENTLY DISABLED: Do not create automatic funding-based tags.

        These tags are invalid and must NEVER be created:
        - cex_funded
        - heavy_funded
        - infra_funded
        - network_member
        - multi_funder
        - coordinated_funding

        Database triggers prevent insertion of these tags.
        Only legitimate service tags appear in creator_tags:
        - uses_jitotip, uses_axiom, uses_debridge, uses_meteora
        - Multi-Funder (coordination indicator)

        Funding pattern analysis lives in separate tables:
        - coordinated_funders table
        - cex_wallets table
        - creator_networks table
        """
        return False  # PERMANENTLY DISABLED - Never executes code below

        # LEGACY CODE BELOW - UNREACHABLE
        try:
            conn = sqlite3.connect(self.db_path, timeout=60)
            cursor = conn.cursor()

            tags_to_add = []

            # 1. Check if funded by coordinated funders
            cursor.execute("""
                SELECT COUNT(DISTINCT cf.funder_address)
                FROM creator_funders cf
                WHERE cf.creator_address = ?
                AND cf.funder_address IN (
                    SELECT funder_address FROM cex_wallets WHERE is_active = 1
                )
            """, (creator,))
            cex_funders_count = cursor.fetchone()[0] or 0

            # 2. Check infrastructure funders
            cursor.execute("""
                SELECT COUNT(DISTINCT funder_address)
                FROM creator_funders
                WHERE creator_address = ?
                AND is_classified = 1
            """, (creator,))
            infra_funders_count = cursor.fetchone()[0] or 0

            # 3. Tag based on patterns
            if total_funders >= 5:
                tags_to_add.append(("multi_funder", f"Funded by {total_funders} unique addresses", total_sol))

            if total_sol >= 10:
                tags_to_add.append(("heavy_funded", f"Received {total_sol:.2f} SOL in funding", total_sol))

            if cex_funders_count > 0:
                tags_to_add.append(("cex_funded", f"Funded by {cex_funders_count} CEX account(s)", total_sol))

            if infra_funders_count > 0:
                tags_to_add.append(("infra_funded", f"Funded by {infra_funders_count} infrastructure account(s)", total_sol))

            # 4. Check if this creator funds other creators (network member)
            cursor.execute("""
                SELECT COUNT(DISTINCT creator_address)
                FROM creator_funders
                WHERE funder_address IN (
                    SELECT funder_address FROM creator_funders
                    WHERE creator_address = ?
                )
                AND creator_address != ?
            """, (creator, creator))
            co_funded_count = cursor.fetchone()[0] or 0

            if co_funded_count >= 1:
                tags_to_add.append(("network_member", f"Part of funding network with {co_funded_count} other creators", 0))

            # 5. Check for coordinated patterns (same funder as others)
            cursor.execute("""
                SELECT COUNT(DISTINCT cf.funder_address)
                FROM creator_funders cf
                WHERE cf.creator_address = ?
                AND cf.funder_address IN (
                    SELECT funder_address FROM creator_funders
                    WHERE creator_address != ?
                    GROUP BY funder_address
                    HAVING COUNT(DISTINCT creator_address) >= 2
                )
            """, (creator, creator))
            coordinated_funder_count = cursor.fetchone()[0] or 0

            if coordinated_funder_count >= 2:
                tags_to_add.append(("coordinated_funding", f"Funded by {coordinated_funder_count} coordinated funders", total_sol))

            # 6. Add all tags to creator_tags table
            for tag, description, amount in tags_to_add:
                cursor.execute("""
                    INSERT OR REPLACE INTO creator_tags
                    (creator_address, tag, description, amount_sol, added_at)
                    VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                """, (creator, tag, description, amount if amount > 0 else None))

            conn.commit()
            conn.close()

            if tags_to_add:
                tag_list = ", ".join([f"'{tag[0]}'" for tag in tags_to_add])
                print(f"[TAGS] ✅ Tagged creator {creator[:16]}... with {len(tags_to_add)} funding-based tags: {tag_list}", flush=True)
                return True
            else:
                print(f"[TAGS] ℹ No funding-based tags for creator {creator[:16]}...", flush=True)
                return False

        except Exception as e:
            print(f"[TAGS] ⚠ Error tagging creator: {e}", flush=True)
            return False

    async def _assign_creator_network(self, creator: str):
        """
        Assign network to creator based on funding relationships
        
        Priority order:
        1. Atomic network funders (from atomic_network_names)
        2. Coordinated funders (from coordinated_funders)
        3. Shared funders (2+ shared with another creator)
        """
        try:
            # Retry with exponential backoff if funders not yet available
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    conn = sqlite3.connect(self.db_path, timeout=60)
                    cursor = conn.cursor()

                    # Check if creator already has a network assigned
                    cursor.execute("""
                        SELECT id FROM creator_networks
                        WHERE creator_address = ?
                    """, (creator,))

                    existing = cursor.fetchone()
                    if existing:
                        print(f"[NETWORK] ℹ Creator {creator[:16]}... already assigned to network {existing[0]}", flush=True)
                        conn.close()
                        return True

                    # Find creators with funders
                    cursor.execute("""
                        SELECT COUNT(*) FROM creator_funders WHERE creator_address = ?
                    """, (creator,))
                    funders_count = cursor.fetchone()[0]

                    if not funders_count:
                        if attempt < max_retries - 1:
                            await asyncio.sleep(1)  # Wait before retry
                            continue
                        else:
                            print(f"[NETWORK] 🔹 Creator {creator[:16]}... has no funders after {max_retries} attempts", flush=True)
                            conn.close()
                            return False

                    # If we got here, we have funders, so break out of retry loop
                    break
                except Exception as e:
                    if attempt < max_retries - 1:
                        await asyncio.sleep(1)
                        continue
                    else:
                        raise

            # PRIORITY 1: Check for atomic network funders
            cursor.execute("""
                SELECT DISTINCT an.network_name
                FROM creator_funders cf
                JOIN atomic_network_names an ON cf.funder_address = an.funder_address
                WHERE cf.creator_address = ?
                LIMIT 1
            """, (creator,))
            
            atomic_network = cursor.fetchone()
            if atomic_network:
                network_name = atomic_network[0]
                print(f"[NETWORK] 🌐 Creator {creator[:16]}... assigned to atomic network: {network_name}", flush=True)
                
                # Find other creators in the same network
                cursor.execute("""
                    SELECT DISTINCT cf.creator_address
                    FROM creator_funders cf
                    JOIN atomic_network_names an ON cf.funder_address = an.funder_address
                    WHERE an.network_name = ?
                    AND cf.creator_address != ?
                    LIMIT 10
                """, (network_name, creator))
                
                connected = [row[0] for row in cursor.fetchall()]
                
                cursor.execute("""
                    INSERT OR REPLACE INTO creator_networks
                    (creator_address, connected_creators, shared_destinations, network_size, network_risk_level, network_name, detected_at)
                    VALUES (?, ?, ?, ?, 'MEDIUM', ?, CURRENT_TIMESTAMP)
                """, (creator, json.dumps(connected), json.dumps([]), len(connected) + 1, network_name))
                
                conn.commit()
                conn.close()
                return True

            # PRIORITY 2: Check for coordinated funders
            cursor.execute("""
                SELECT ccf.funder_address, ccf.creator_addresses, ccf.risk_level
                FROM creator_funders cf
                JOIN coordinated_funders ccf ON cf.funder_address = ccf.funder_address
                WHERE cf.creator_address = ?
                LIMIT 1
            """, (creator,))
            
            coordinated = cursor.fetchone()
            if coordinated:
                funder_addr = coordinated[0]
                other_creators_json = coordinated[1]
                risk_level = coordinated[2]
                
                # Parse the JSON array of creators
                other_creators = json.loads(other_creators_json) if isinstance(other_creators_json, str) else other_creators_json
                connected_list = [c for c in other_creators if c != creator]
                
                print(f"[NETWORK] 🔴 Creator {creator[:16]}... assigned to coordinated funder network ({len(connected_list)} connected creators)", flush=True)
                
                cursor.execute("""
                    INSERT OR REPLACE INTO creator_networks
                    (creator_address, connected_creators, shared_destinations, network_size, network_risk_level, detected_at)
                    VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """, (creator, json.dumps(connected_list), json.dumps([]), len(connected_list) + 1, risk_level or 'MEDIUM'))
                
                conn.commit()
                conn.close()
                return True

            # PRIORITY 3: Check for shared funders with other creators
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
                print(f"[NETWORK] 🔗 Creator {creator[:16]}... assigned to shared funder network ({len(connected_creators)} connected creators)", flush=True)

                # Store connected creators relationship
                connected_list = [addr for addr, count in connected_creators]

                cursor.execute("""
                    INSERT OR REPLACE INTO creator_networks
                    (creator_address, connected_creators, shared_destinations, network_size, network_risk_level, detected_at)
                    VALUES (?, ?, ?, ?, 'MEDIUM', CURRENT_TIMESTAMP)
                """, (creator, json.dumps(connected_list), json.dumps([]), len(connected_list) + 1))

                conn.commit()
                conn.close()
                return True
            else:
                print(f"[NETWORK] 🔹 Creator {creator[:16]}... appears independent (no shared funders)", flush=True)
                conn.close()
                return False

        except Exception as e:
            print(f"[NETWORK] ⚠ Error assigning network: {e}", flush=True)
            return False

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
                conn.close()
                return True
            else:
                print(f"[COORDINATION] ✅ Creator {creator[:16]}... has unique funders (no coordination detected)", flush=True)
                conn.close()
                return False

        except Exception as e:
            print(f"[COORDINATION] ⚠ Error detecting coordinated funders: {e}", flush=True)
            return False

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
            cluster_creators = [creator]
            cluster_funders = funders
            cluster_recipients = recipients
            cluster_destinations = list(set(funders + recipients))

            cursor.execute("""
                INSERT OR REPLACE INTO unified_creator_clusters
                (target_creator, cluster_creators, cluster_funders, cluster_recipients, cluster_destinations, risk_level)
                VALUES (?, ?, ?, ?, ?, 'MEDIUM')
            """, (creator, json.dumps(cluster_creators), json.dumps(cluster_funders),
                  json.dumps(cluster_recipients), json.dumps(cluster_destinations)))

            conn.commit()
            conn.close()

            print(f"[CLUSTERS] ✅ Rebuilt clusters for {creator[:16]}... (members: {len(cluster_destinations)} addresses)", flush=True)

        except Exception as e:
            print(f"[CLUSTERS] ⚠ Error rebuilding clusters: {e}", flush=True)

    async def _analyze_creator_network_using_cluster_analyzer(self, creator: str):
        """
        Use CrossFundingClusterAnalyzer to comprehensively analyze creator's network position
        
        Applies all network detection logic from cross_funding_network_analyzer:
        - Atomic funder networks
        - Funder clusters
        - Creator clusters
        - Network coordinators
        """
        try:
            print(f"[NETWORK_ANALYSIS] 🔍 Analyzing creator {creator[:16]}... with CrossFundingClusterAnalyzer", flush=True)
            
            analyzer = CrossFundingClusterAnalyzer(self.db_path)
            
            # Build all network structures
            print(f"[NETWORK_ANALYSIS] 📊 Building atomic funder networks...", flush=True)
            analyzer.build_atomic_funder_networks()
            
            print(f"[NETWORK_ANALYSIS] 🔗 Building funder clusters...", flush=True)
            analyzer.build_funder_clusters()
            
            print(f"[NETWORK_ANALYSIS] 👥 Building creator clusters...", flush=True)
            analyzer.build_creator_clusters()
            
            print(f"[NETWORK_ANALYSIS] 🌐 Detecting network coordinators...", flush=True)
            coordinators = analyzer.detect_network_coordinators()
            
            # Analyze this specific creator's unified cluster
            print(f"[NETWORK_ANALYSIS] 📈 Analyzing unified cluster for {creator[:16]}...", flush=True)
            report = analyzer.analyze_creator_unified_cluster(creator)
            
            print(f"[NETWORK_ANALYSIS] ✅ Network analysis complete for {creator[:16]}...", flush=True)
            
            return True
            
        except Exception as e:
            print(f"[NETWORK_ANALYSIS] ⚠ Error in network analysis: {e}", flush=True)
            import traceback
            traceback.print_exc()
            return False

    async def _check_watchtower_linkage(self, creator: str, mint: str) -> None:
        """Check whether this creator is operationally related to WATCHTOWER infrastructure."""
        try:
            conn = sqlite3.connect(self.db_path, timeout=15)
            conn.row_factory = sqlite3.Row
            try:
                wt_ensure_schema(conn)
                result = analyze_creator_from_conn(creator, conn, mint=mint)
                conn.commit()
                if result["is_related"]:
                    strong = [e for e in result["evidence"] if e["strength"] == "strong"]
                    print(
                        f"[WATCHTOWER] 🚨 RELATED: {creator[:20]}… "
                        f"label='{result['label']}' "
                        f"rules={[e['rule'] for e in strong]}",
                        flush=True,
                    )
            finally:
                conn.close()
        except Exception as exc:
            print(f"[WATCHTOWER] ⚠ detection error for {creator[:20]}…: {exc}", flush=True)

    async def _emit_ui_update(self, creator: str, mint: str, total_funders: int, total_sol: float, updates: dict = None):
        """
        Emit WebSocket update to UI for real-time dashboard refresh

        Signals UI to refresh:
        - Creator details
        - Funding distribution charts
        - Cluster visualizations
        - Network assignments
        - Creator tags (NEW!)

        Args:
            updates: Dict indicating what was actually updated (creator_tags, networks, etc)
        """
        if not self.websocket_manager:
            print(f"[UI] ℹ No WebSocket manager - skipping WebSocket broadcast", flush=True)
            # Still log to console even without WebSocket
            if updates and any(updates.values()):
                updated_items = [k for k, v in updates.items() if v]
                print(f"[UI] 📝 Updated: {', '.join(updated_items)}", flush=True)
            return

        try:
            # Prepare update payload with actual changes
            updates_dict = updates if updates else {
                "creator_details": True,
                "funding_distribution": True,
                "coordinated_funders": True,
                "clusters": True,
                "networks": True,
                "creator_tags": True
            }

            update_payload = {
                "type": "post_launch_complete",
                "creator": creator,
                "mint": mint,
                "total_funders": total_funders,
                "total_sol": total_sol,
                "timestamp": datetime.utcnow().isoformat(),
                "updates": updates_dict
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
