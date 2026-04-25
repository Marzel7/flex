"""
Graph-Based Dev Farm Detection Engine for FLEX

Implements network graph clustering to detect dev farm ecosystems from transfer patterns.
Uses transfer_index to build a directed wallet graph, then applies multiple clustering
algorithms to identify farms with 2+ funders and 3+ creators.

Key Components:
1. WalletGraphBuilder — Construct directed graph from transfer_index
2. GraphPreprocessor — Clean and normalize graph
3. ClusterDetector — Multiple clustering algorithms (weakly connected, Louvain, k-core, clique)
4. ClusterClassifier — Classify wallets as funders vs creators
5. FarmIdentifier — Identify dev farm clusters
6. GraphDevFarmDetectionEngine — Complete pipeline integration

Production-ready with SQLite WAL, error handling, and logging.
"""

import time
import json
import logging
import sqlite3
from collections import defaultdict
from typing import Dict, Set, List, Tuple, Optional
from pathlib import Path

import numpy as np
import networkx as nx

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

from src.utils.infra_mapping import build_excluded_set, classify_wallet


# ============================================================================
# SECTION 1: GRAPH CONSTRUCTION
# ============================================================================

class WalletGraphBuilder:
    """Build directed wallet graph from transfer_index."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.graph = nx.DiGraph()
        self.edge_weights = defaultdict(lambda: {
            'count': 0,
            'total_sol': 0.0,
            'timestamps': []
        })

    def _get_conn(self) -> sqlite3.Connection:
        """Get database connection with WAL."""
        conn = sqlite3.connect(self.db_path, timeout=60)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def build_graph_from_transfers(self,
                                   min_amount: float = 0.5,
                                   max_amount: float = 10.0,
                                   days_back: int = 90,
                                   excluded: frozenset = frozenset()) -> nx.DiGraph:
        """
        Build wallet graph from transfer_index with enhanced edge weighting.
        CEX/infra wallets are excluded from graph construction so they don't
        inflate cluster sizes or create false coordination signals.

        Edge Weights Include:
        - weight: transfer_count (number of transfers on this edge)
        - total_amount: total SOL transferred
        - avg_amount: average per transfer
        - time_concentration: 0-1 (how clustered transfers are in time)
        - composite_weight: 0-100 (combined metric for cluster strength)

        Args:
            min_amount: Minimum transfer amount (SOL)
            max_amount: Maximum transfer amount (SOL)
            days_back: How far back to look (days)
            excluded: frozenset of CEX/infra addresses to skip

        Returns:
            NetworkX directed graph with comprehensive edge attributes
        """
        conn = self._get_conn()
        cursor = conn.cursor()

        query = """
            SELECT source, destination, amount_sol, block_time
            FROM transfer_index
            WHERE amount_sol BETWEEN ? AND ?
              AND block_time >= (strftime('%s', 'now') - ? * 86400)
              AND is_valid = 1
            ORDER BY source, destination, block_time
        """

        cursor.execute(query, (min_amount, max_amount, days_back))

        # Build graph with aggregated edge weights
        for source, dest, amount, ts in cursor.fetchall():
            # Skip self-transfers and CEX/infra wallets
            if source == dest:
                continue
            if source in excluded or dest in excluded:
                continue

            # Add edge to graph
            if not self.graph.has_edge(source, dest):
                self.graph.add_edge(source, dest, weight=1)

            # Aggregate edge metrics
            key = (source, dest)
            self.edge_weights[key]['count'] += 1
            self.edge_weights[key]['total_sol'] += amount
            self.edge_weights[key]['timestamps'].append(ts)

            # Update edge attributes
            self.graph[source][dest]['weight'] = self.edge_weights[key]['count']
            self.graph[source][dest]['total_amount'] = self.edge_weights[key]['total_sol']
            self.graph[source][dest]['avg_amount'] = (
                self.edge_weights[key]['total_sol'] / self.edge_weights[key]['count']
            )
            
            # Compute time_concentration: how clustered transfers are in time
            timestamps = sorted(self.edge_weights[key]['timestamps'])
            if len(timestamps) > 1:
                # Time span in seconds
                time_span = timestamps[-1] - timestamps[0]
                # If span is 0 (all same second), concentration = 1.0
                if time_span == 0:
                    time_concentration = 1.0
                else:
                    # Measure variance from uniform distribution
                    # Lower variance = higher concentration (transfers bunched together)
                    time_diffs = [timestamps[i+1] - timestamps[i] for i in range(len(timestamps)-1)]
                    if time_diffs:
                        mean_diff = sum(time_diffs) / len(time_diffs)
                        variance = sum((d - mean_diff) ** 2 for d in time_diffs) / len(time_diffs)
                        # Normalize: high variance = low concentration, low variance = high concentration
                        # Use exponential decay to map to 0-1 range
                        time_concentration = max(0, 1.0 - (variance / (time_span + 1)))
                    else:
                        time_concentration = 1.0
            else:
                time_concentration = 1.0
            
            self.graph[source][dest]['time_concentration'] = time_concentration
            
            # Composite weight: 0-100 scale combining all factors
            # transfer_count (0-30): heavily weighted, indicates active relationship
            # total_amount (0-30): heavily weighted, indicates significant coordination
            # time_concentration (0-40): indicates bursts/coordinated timing
            count_score = min(self.edge_weights[key]['count'] / 10 * 30, 30)
            amount_score = min(self.edge_weights[key]['total_sol'] / 50 * 30, 30)  # ~50 SOL = max
            time_score = time_concentration * 40
            
            composite_weight = count_score + amount_score + time_score
            self.graph[source][dest]['composite_weight'] = composite_weight

        conn.close()
        logger.info(f"Graph built: {self.graph.number_of_nodes()} nodes, {self.graph.number_of_edges()} edges")
        return self.graph

    def get_graph_stats(self) -> Dict:
        """Get basic graph statistics."""
        return {
            'nodes': self.graph.number_of_nodes(),
            'edges': self.graph.number_of_edges(),
            'density': nx.density(self.graph),
            'strongly_connected_components': nx.number_strongly_connected_components(self.graph),
            'weakly_connected_components': nx.number_weakly_connected_components(self.graph),
        }


# ============================================================================
# SECTION 1B: GRAPH PREPROCESSING
# ============================================================================

class GraphPreprocessor:
    """Clean and normalize graph for clustering."""

    def __init__(self, graph: nx.DiGraph):
        self.graph = graph
        self.original_graph = graph.copy()

    def remove_isolated_nodes(self) -> nx.DiGraph:
        """Remove nodes with no connections."""
        isolated = list(nx.isolates(self.graph))
        self.graph.remove_nodes_from(isolated)
        logger.info(f"Removed {len(isolated)} isolated nodes")
        return self.graph

    def filter_by_degree(self, min_degree: int = 2) -> nx.DiGraph:
        """Keep only nodes with at least min_degree connections."""
        low_degree_nodes = [node for node, degree in self.graph.degree() if degree < min_degree]
        self.graph.remove_nodes_from(low_degree_nodes)
        logger.info(f"Removed {len(low_degree_nodes)} low-degree nodes (threshold: {min_degree})")
        return self.graph

    def get_weakly_connected_components(self) -> List[nx.DiGraph]:
        """Get weakly connected components (treat as undirected)."""
        components = list(nx.weakly_connected_components(self.graph))
        subgraphs = [self.graph.subgraph(component).copy() for component in components]
        return subgraphs

    def get_strongly_connected_components(self) -> List[nx.DiGraph]:
        """Get strongly connected components (paths exist in both directions)."""
        components = list(nx.strongly_connected_components(self.graph))
        subgraphs = [self.graph.subgraph(component).copy() for component in components]
        return subgraphs


# ============================================================================
# SECTION 2: CLUSTER DETECTION
# ============================================================================

class ClusterDetector:
    """Detect clusters using multiple algorithms."""

    def __init__(self, graph: nx.DiGraph):
        self.graph = graph
        self.clusters = {}

    def detect_by_weakly_connected_components(self) -> Dict[int, Set]:
        """
        Detect clusters using weakly connected components.
        Each weakly connected component is a cluster.
        """
        clusters = {}
        components = list(nx.weakly_connected_components(self.graph))

        for cluster_id, component in enumerate(components):
            clusters[cluster_id] = component

        self.clusters['weakly_connected'] = clusters
        logger.info(f"Weakly connected components: {len(clusters)}")
        return clusters

    def detect_by_louvain_community(self) -> Dict[int, Set]:
        """
        Detect clusters using Louvain algorithm.
        Converts directed graph to undirected for community detection.
        """
        try:
            import community as community_louvain
        except ImportError:
            logger.warning("python-louvain not installed, skipping Louvain detection")
            return {}

        # Convert to undirected for Louvain
        undirected = self.graph.to_undirected()

        # Run Louvain
        partition = community_louvain.best_partition(undirected)

        # Reformat as clusters
        clusters = {}
        for wallet, cluster_id in partition.items():
            if cluster_id not in clusters:
                clusters[cluster_id] = set()
            clusters[cluster_id].add(wallet)

        self.clusters['louvain'] = clusters
        logger.info(f"Louvain communities: {len(clusters)}")
        return clusters

    def detect_by_k_core_decomposition(self, k: int = 2) -> Dict[int, Set]:
        """
        Detect clusters using k-core decomposition.
        A k-core is where every node has at least k connections.
        """
        # Convert to undirected
        undirected = self.graph.to_undirected()

        # Compute k-core
        k_core = nx.k_core(undirected, k=k)

        # Get connected components of k-core
        clusters = {}
        components = list(nx.connected_components(k_core))

        for cluster_id, component in enumerate(components):
            clusters[cluster_id] = component

        self.clusters[f'k_core_{k}'] = clusters
        logger.info(f"K-core (k={k}) clusters: {len(clusters)}")
        return clusters

    def detect_by_clique_percolation(self, clique_size: int = 4) -> Dict[int, Set]:
        """Detect clusters using clique percolation."""
        undirected = self.graph.to_undirected()

        # Find all maximal cliques
        cliques = list(nx.find_cliques(undirected))

        # Filter by size
        cliques = [c for c in cliques if len(c) >= clique_size]

        # Convert to clusters
        clusters = {cluster_id: set(clique) for cluster_id, clique in enumerate(cliques)}

        self.clusters[f'clique_{clique_size}'] = clusters
        logger.info(f"Clique (size={clique_size}) clusters: {len(clusters)}")
        return clusters

    def get_cluster_subgraph(self, cluster_id: int, method: str = 'weakly_connected') -> nx.DiGraph:
        """Get subgraph for a specific cluster."""
        if method not in self.clusters:
            raise ValueError(f"Method {method} not computed yet")

        cluster_nodes = self.clusters[method][cluster_id]
        return self.graph.subgraph(cluster_nodes).copy()


# ============================================================================
# SECTION 2B: CLUSTER RANKING
# ============================================================================

class ClusterRanker:
    """Rank clusters by coordination strength."""

    def __init__(self, graph: nx.DiGraph, clusters: Dict[int, Set]):
        self.graph = graph
        self.clusters = clusters

    def compute_cluster_metrics(self, cluster_id: int) -> Dict:
        """Compute metrics for a cluster including weighted edge strength."""
        cluster_nodes = self.clusters[cluster_id]
        subgraph = self.graph.subgraph(cluster_nodes).copy()

        size = subgraph.number_of_nodes()
        edges = subgraph.number_of_edges()

        # Possible edges in complete graph (directed)
        possible_edges = size * (size - 1)

        # Transfer volume
        total_volume = sum(
            data.get('total_amount', 0)
            for _, _, data in subgraph.edges(data=True)
        )

        # Average transfers per edge
        avg_transfers = edges / size if size > 0 else 0

        # Density
        density = edges / possible_edges if possible_edges > 0 else 0
        
        # NEW: Cluster Strength from Weighted Edges
        # Combines transfer count + amount + time concentration
        edge_weights = []
        for _, _, data in subgraph.edges(data=True):
            edge_weights.append(data.get('weight', 1))  # transfer count
        
        avg_edge_weight = np.mean(edge_weights) if edge_weights else 0
        max_edge_weight = np.max(edge_weights) if edge_weights else 0
        
        # Composite edge strength (0-100 scale)
        composite_weights = []
        for _, _, data in subgraph.edges(data=True):
            composite_weights.append(data.get('composite_weight', 0))
        
        avg_composite_weight = np.mean(composite_weights) if composite_weights else 0
        max_composite_weight = np.max(composite_weights) if composite_weights else 0
        
        # Time concentration analysis
        time_concentrations = []
        for _, _, data in subgraph.edges(data=True):
            time_concentrations.append(data.get('time_concentration', 0.5))
        
        avg_time_concentration = np.mean(time_concentrations) if time_concentrations else 0.5
        
        # Cluster strength: how tightly coordinated are the edges?
        # Higher avg_composite_weight = stronger cluster
        # Higher avg_time_concentration = more burst-like (coordinated timing)
        cluster_strength = (avg_composite_weight / 100 * 0.6) + (avg_time_concentration * 0.4)
        cluster_strength = min(cluster_strength * 100, 100)  # Normalize to 0-100

        return {
            'cluster_id': cluster_id,
            'size': size,
            'edges': edges,
            'possible_edges': possible_edges,
            'density': density,
            'cohesion': density,
            'total_volume': total_volume,
            'avg_transfers': avg_transfers,
            'avg_volume_per_edge': total_volume / edges if edges > 0 else 0,
            # NEW: Weighted edge metrics
            'avg_edge_weight': avg_edge_weight,
            'max_edge_weight': max_edge_weight,
            'avg_composite_weight': avg_composite_weight,
            'max_composite_weight': max_composite_weight,
            'avg_time_concentration': avg_time_concentration,
            'cluster_strength': cluster_strength,
        }

    def rank_clusters(self) -> List[Dict]:
        """Rank clusters by coordination strength using weighted edges."""
        scores = []

        for cluster_id in self.clusters.keys():
            metrics = self.compute_cluster_metrics(cluster_id)

            # Normalize factors
            density_score = metrics['density'] * 100  # 0-100
            size_score = np.log1p(metrics['size']) * 10
            volume_score = np.log1p(metrics['total_volume']) * 5
            
            # NEW: Cluster strength from weighted edges (0-100 scale)
            strength_score = metrics['cluster_strength']  # Already 0-100

            # Composite score: weighted by different factors
            # - Density (structural coordination): 30%
            # - Size (network scale): 20%
            # - Volume (capital commitment): 20%
            # - Strength (edge weight + timing): 30%
            total_score = (
                (density_score * 0.30) +
                (size_score * 0.20) +
                (volume_score * 0.20) +
                (strength_score * 0.30)
            )

            metrics['coordination_score'] = total_score
            metrics['strength_score'] = strength_score
            scores.append(metrics)

        # Sort by score (highest first)
        scores.sort(key=lambda x: x['coordination_score'], reverse=True)
        return scores


# ============================================================================
# SECTION 3: CLUSTER CLASSIFICATION
# ============================================================================

class ClusterClassifier:
    """Classify wallets in a cluster as funders or creators."""

    def __init__(self, graph: nx.DiGraph):
        self.graph = graph

    def classify_cluster(self, cluster_nodes: Set) -> Dict:
        """
        Classify wallets in a cluster.

        Funder: High out-degree (sends to many), low in-degree (receives from few)
        Creator: High in-degree (receives from many), lower out-degree (sends to few)
        """
        subgraph = self.graph.subgraph(cluster_nodes).copy()

        wallet_roles = {}

        # Compute in-degree and out-degree
        for wallet in cluster_nodes:
            in_degree = subgraph.in_degree(wallet)
            out_degree = subgraph.out_degree(wallet)

            # Normalize degrees
            total_degree = in_degree + out_degree

            if total_degree == 0:
                in_ratio = 0
                out_ratio = 0
            else:
                in_ratio = in_degree / total_degree
                out_ratio = out_degree / total_degree

            # Classification logic
            if out_ratio > 0.6:
                role = 'funder'
            elif in_ratio > 0.6:
                role = 'creator'
            else:
                role = 'ambiguous'

            wallet_roles[wallet] = {
                'role': role,
                'in_degree': in_degree,
                'out_degree': out_degree,
                'in_ratio': in_ratio,
                'out_ratio': out_ratio,
                'total_degree': total_degree,
            }

        # Separate by role
        funders = {w: d for w, d in wallet_roles.items() if d['role'] == 'funder'}
        creators = {w: d for w, d in wallet_roles.items() if d['role'] == 'creator'}
        ambiguous = {w: d for w, d in wallet_roles.items() if d['role'] == 'ambiguous'}

        # Classification confidence
        if len(wallet_roles) > 0:
            funder_ratio = len(funders) / len(wallet_roles)
            creator_ratio = len(creators) / len(wallet_roles)
            confidence = abs(funder_ratio - creator_ratio)
        else:
            confidence = 0

        return {
            'funders': funders,
            'creators': creators,
            'ambiguous': ambiguous,
            'classification_confidence': confidence,
            'wallet_roles': wallet_roles,
        }


# ============================================================================
# SECTION 3B: FARM IDENTIFICATION
# ============================================================================

class FarmIdentifier:
    """Identify dev farms from classified clusters."""

    def __init__(self, graph: nx.DiGraph):
        self.graph = graph
        self.classifier = ClusterClassifier(graph)

    def identify_farm_clusters(self,
                               clusters: Dict[int, Set],
                               cluster_metrics: Dict = None,
                               min_funders: int = 2,
                               min_creators: int = 3) -> List[Dict]:
        """
        Identify which clusters are dev farms.

        A dev farm has:
        - Multiple funders (2+)
        - Multiple creators (3+)
        - Clear coordination patterns

        Args:
            cluster_metrics: Optional dict of cluster_id -> metrics (from ClusterRanker)
        """
        farms = []
        cluster_metrics = cluster_metrics or {}

        for cluster_id, cluster_nodes in clusters.items():
            # Skip small clusters
            if len(cluster_nodes) < min_funders + min_creators:
                continue

            # Classify wallets
            classification = self.classifier.classify_cluster(cluster_nodes)
            funders = classification['funders']
            creators = classification['creators']

            # Check if it's a farm
            if len(funders) >= min_funders and len(creators) >= min_creators:
                subgraph = self.graph.subgraph(cluster_nodes).copy()

                # Get metrics from ranker if available
                metrics = cluster_metrics.get(cluster_id, {})

                # Compute farm-specific metrics
                farm_data = {
                    'cluster_id': cluster_id,
                    'funder_count': len(funders),
                    'creator_count': len(creators),
                    'ambiguous_count': len(classification['ambiguous']),
                    'total_wallets': len(cluster_nodes),
                    'classification_confidence': classification['classification_confidence'],
                    'funders': list(funders.keys()),
                    'creators': list(creators.keys()),
                    'ambiguous': list(classification['ambiguous'].keys()),
                    'all_wallets': list(cluster_nodes),
                    'cluster_nodes': cluster_nodes,
                    'subgraph': subgraph,
                    'classification': classification,
                    'density': nx.density(subgraph),
                    'total_transfers': subgraph.number_of_edges(),
                    'total_volume_sol': sum(
                        data.get('total_amount', 0)
                        for _, _, data in subgraph.edges(data=True)
                    ),
                    # NEW: Weighted edge metrics from ClusterRanker
                    'avg_edge_weight': metrics.get('avg_edge_weight', 0),
                    'max_edge_weight': metrics.get('max_edge_weight', 0),
                    'avg_composite_weight': metrics.get('avg_composite_weight', 0),
                    'max_composite_weight': metrics.get('max_composite_weight', 0),
                    'avg_time_concentration': metrics.get('avg_time_concentration', 0.5),
                    'cluster_strength': metrics.get('cluster_strength', 0),
                    'strength_score': metrics.get('strength_score', 0),
                }

                # Compute farm risk score
                farm_data['farm_risk_score'] = self._compute_farm_risk(farm_data)
                farm_data['risk_level'] = self._classify_risk_level(farm_data['farm_risk_score'])

                farms.append(farm_data)

        # Sort by strength_score (weighted), then by risk score
        farms.sort(key=lambda x: (x.get('strength_score', 0), x['farm_risk_score']), reverse=True)
        logger.info(f"Dev farms identified: {len(farms)}")
        return farms

    def _compute_farm_risk(self, farm_data: Dict) -> float:
        """Compute risk score for a farm (0-100)."""
        funder_score = min(farm_data['funder_count'] / 5 * 25, 25)
        creator_score = min(farm_data['creator_count'] / 10 * 25, 25)
        density_score = farm_data['density'] * 30
        confidence_score = farm_data['classification_confidence'] * 20

        total = funder_score + creator_score + density_score + confidence_score
        return min(total, 100)

    def _classify_risk_level(self, score: float) -> str:
        """Classify risk level from score."""
        if score >= 80:
            return 'CRITICAL'
        elif score >= 60:
            return 'HIGH'
        elif score >= 40:
            return 'MEDIUM'
        else:
            return 'LOW'


# ============================================================================
# SECTION 5: COMPLETE DETECTION ENGINE
# ============================================================================

class GraphDevFarmDetectionEngine:
    """Complete graph-based dev farm detection pipeline."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.start_time = time.time()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=60)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_tables(self) -> None:
        """Create tables if missing."""
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.executescript("""
            CREATE TABLE IF NOT EXISTS farm_clusters (
                cluster_id INTEGER PRIMARY KEY AUTOINCREMENT,
                graph_cluster_id INTEGER NOT NULL,
                funder_count INTEGER NOT NULL,
                creator_count INTEGER NOT NULL,
                ambiguous_count INTEGER DEFAULT 0,
                total_wallets INTEGER NOT NULL,
                funder_list TEXT NOT NULL,
                creator_list TEXT NOT NULL,
                ambiguous_list TEXT,
                all_wallets TEXT NOT NULL,
                cluster_density REAL DEFAULT 0,
                cluster_size INTEGER DEFAULT 0,
                avg_transfers_per_edge REAL DEFAULT 0,
                total_transfers INTEGER DEFAULT 0,
                total_volume_sol REAL DEFAULT 0,
                avg_edge_weight REAL DEFAULT 0,
                max_edge_weight REAL DEFAULT 0,
                avg_composite_weight REAL DEFAULT 0,
                max_composite_weight REAL DEFAULT 0,
                avg_time_concentration REAL DEFAULT 0.5,
                cluster_strength REAL DEFAULT 0,
                strength_score REAL DEFAULT 0,
                cex_wallet_count INTEGER DEFAULT 0,
                infra_wallet_count INTEGER DEFAULT 0,
                unknown_wallet_count INTEGER DEFAULT 0,
                classification_confidence REAL DEFAULT 0,
                pattern_regularity REAL DEFAULT 0,
                farm_risk_score REAL DEFAULT 0,
                risk_level TEXT DEFAULT 'LOW',
                detection_method TEXT DEFAULT 'graph_clustering',
                first_activity_ts INTEGER,
                last_activity_ts INTEGER,
                active_days REAL DEFAULT 0,
                detected_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                UNIQUE(graph_cluster_id)
            );

            CREATE TABLE IF NOT EXISTS farm_cluster_members (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cluster_id INTEGER NOT NULL,
                wallet_address TEXT NOT NULL,
                wallet_role TEXT NOT NULL,
                in_degree INTEGER DEFAULT 0,
                out_degree INTEGER DEFAULT 0,
                in_ratio REAL DEFAULT 0,
                out_ratio REAL DEFAULT 0,
                total_degree INTEGER DEFAULT 0,
                transfers_sent INTEGER DEFAULT 0,
                transfers_received INTEGER DEFAULT 0,
                total_sent_sol REAL DEFAULT 0,
                total_received_sol REAL DEFAULT 0,
                role_confidence REAL DEFAULT 0,
                pattern_regularity REAL DEFAULT 0,
                first_activity_ts INTEGER,
                last_activity_ts INTEGER,
                detected_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                FOREIGN KEY(cluster_id) REFERENCES farm_clusters(cluster_id),
                UNIQUE(cluster_id, wallet_address)
            );

            CREATE TABLE IF NOT EXISTS farm_cluster_edges (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cluster_id INTEGER NOT NULL,
                source_wallet TEXT NOT NULL,
                dest_wallet TEXT NOT NULL,
                transfer_count INTEGER DEFAULT 0,
                total_amount_sol REAL DEFAULT 0,
                avg_amount_sol REAL DEFAULT 0,
                time_concentration REAL DEFAULT 0.5,
                composite_weight REAL DEFAULT 0,
                first_transfer_ts INTEGER,
                last_transfer_ts INTEGER,
                detected_at REAL NOT NULL,
                FOREIGN KEY(cluster_id) REFERENCES farm_clusters(cluster_id),
                UNIQUE(cluster_id, source_wallet, dest_wallet)
            );
        """)

        # Create indexes
        cursor.executescript("""
            CREATE INDEX IF NOT EXISTS idx_farm_clusters_risk_score
                ON farm_clusters(farm_risk_score DESC);
            CREATE INDEX IF NOT EXISTS idx_farm_clusters_detected_at
                ON farm_clusters(detected_at DESC);
            CREATE INDEX IF NOT EXISTS idx_farm_members_cluster
                ON farm_cluster_members(cluster_id);
            CREATE INDEX IF NOT EXISTS idx_farm_members_role
                ON farm_cluster_members(wallet_role);
            CREATE INDEX IF NOT EXISTS idx_farm_edges_cluster
                ON farm_cluster_edges(cluster_id);
        """)

        conn.commit()
        conn.close()
        logger.info("Tables verified/created")

    def detect_and_store(self) -> Dict:
        """
        Main entry point: run complete detection pipeline.

        Returns:
            {status, message, clusters_detected, farms_identified, ...}
        """
        try:
            logger.info("Starting graph-based dev farm detection")

            # Step 1: Create tables
            self._ensure_tables()

            # Build unified CEX/infra exclusion set once for this run
            conn = self._get_conn()
            self._excluded = build_excluded_set(conn)
            conn.close()
            logger.info(f"Exclusion set: {len(self._excluded)} CEX/infra addresses")

            # Step 2: Build graph (CEX/infra excluded at edge level)
            logger.info("Building wallet graph from transfer_index")
            graph = self._build_wallet_graph()
            logger.info(f"Graph built: {graph.number_of_nodes()} nodes, {graph.number_of_edges()} edges")

            # Step 3: Preprocess
            graph = self._preprocess_graph(graph)
            logger.info(f"After preprocessing: {graph.number_of_nodes()} nodes, {graph.number_of_edges()} edges")

            # Step 4: Detect clusters
            clusters = self._detect_clusters(graph)
            logger.info(f"Clusters detected: {len(clusters)}")

            # Step 5: Identify farms
            farms = self._identify_farms(graph, clusters)
            logger.info(f"Dev farms identified: {len(farms)}")

            # Annotate each farm with CEX/infra composition counts
            excluded = getattr(self, '_excluded', frozenset())
            for farm in farms:
                all_wallets = farm.get('all_wallets', [])
                cex_count   = sum(1 for w in all_wallets if classify_wallet(w)['wallet_type'] == 'cex')
                infra_count = sum(1 for w in all_wallets if classify_wallet(w)['wallet_type'] == 'infra')
                farm['cex_wallet_count']   = cex_count
                farm['infra_wallet_count'] = infra_count
                farm['unknown_wallet_count'] = len(all_wallets) - cex_count - infra_count

            # Step 6: Store results
            farm_count, member_count, edge_count = self._store_results(graph, farms)
            logger.info(f"Stored: {farm_count} farms, {member_count} members, {edge_count} edges")

            duration_ms = (time.time() - self.start_time) * 1000

            return {
                'status': 'success',
                'message': f'Graph detection: {len(clusters)} clusters, {len(farms)} farms',
                'clusters_detected': len(clusters),
                'farms_identified': len(farms),
                'farm_members_stored': member_count,
                'farm_edges_stored': edge_count,
                'duration_ms': duration_ms,
            }

        except Exception as e:
            logger.error(f"Detection failed: {e}", exc_info=True)
            duration_ms = (time.time() - self.start_time) * 1000
            return {
                'status': 'error',
                'message': str(e),
                'clusters_detected': 0,
                'farms_identified': 0,
                'farm_members_stored': 0,
                'farm_edges_stored': 0,
                'duration_ms': duration_ms,
            }

    def _build_wallet_graph(self) -> nx.DiGraph:
        """Build graph from transfer_index, excluding CEX/infra nodes."""
        builder = WalletGraphBuilder(self.db_path)
        excluded = getattr(self, '_excluded', frozenset())
        return builder.build_graph_from_transfers(
            min_amount=0.5,
            max_amount=10.0,
            days_back=90,
            excluded=excluded,
        )

    def _preprocess_graph(self, graph: nx.DiGraph) -> nx.DiGraph:
        """Clean graph."""
        preprocessor = GraphPreprocessor(graph)
        preprocessor.remove_isolated_nodes()
        preprocessor.filter_by_degree(min_degree=2)
        return preprocessor.graph

    def _detect_clusters(self, graph: nx.DiGraph) -> Dict[int, Set]:
        """Detect clusters using weakly connected components."""
        detector = ClusterDetector(graph)
        return detector.detect_by_weakly_connected_components()

    def _identify_farms(self, graph: nx.DiGraph, clusters: Dict[int, Set]) -> List[Dict]:
        """Identify dev farms from clusters using weighted edge metrics."""
        # Step 1: Rank clusters by coordination strength
        ranker = ClusterRanker(graph, clusters)
        ranked_clusters = ranker.rank_clusters()
        
        # Convert to dict for lookup: cluster_id -> metrics
        cluster_metrics = {m['cluster_id']: m for m in ranked_clusters}
        
        # Step 2: Identify farms with cluster metrics
        farm_id = FarmIdentifier(graph)
        return farm_id.identify_farm_clusters(
            clusters,
            cluster_metrics=cluster_metrics,
            min_funders=2,
            min_creators=3
        )

    def _store_results(self, graph: nx.DiGraph, farms: List[Dict]) -> Tuple[int, int, int]:
        """Store farms, members, and edges to database with weighted metrics."""
        conn = self._get_conn()
        cursor = conn.cursor()
        now = time.time()

        farm_count = 0
        member_count = 0
        edge_count = 0

        for farm in farms:
            # Store farm cluster with weighted edge metrics + CEX/infra composition
            cursor.execute("""
                INSERT OR REPLACE INTO farm_clusters (
                    graph_cluster_id, funder_count, creator_count, ambiguous_count,
                    total_wallets, funder_list, creator_list, ambiguous_list, all_wallets,
                    cluster_density, total_transfers, total_volume_sol,
                    avg_edge_weight, max_edge_weight, avg_composite_weight, max_composite_weight,
                    avg_time_concentration, cluster_strength,
                    cex_wallet_count, infra_wallet_count, unknown_wallet_count,
                    classification_confidence, farm_risk_score, risk_level, strength_score,
                    detected_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                farm['cluster_id'],
                farm['funder_count'],
                farm['creator_count'],
                farm['ambiguous_count'],
                farm['total_wallets'],
                json.dumps(farm['funders']),
                json.dumps(farm['creators']),
                json.dumps(farm['ambiguous']),
                json.dumps(farm['all_wallets']),
                farm['density'],
                farm['total_transfers'],
                farm['total_volume_sol'],
                farm.get('avg_edge_weight', 0),
                farm.get('max_edge_weight', 0),
                farm.get('avg_composite_weight', 0),
                farm.get('max_composite_weight', 0),
                farm.get('avg_time_concentration', 0.5),
                farm.get('cluster_strength', 0),
                farm.get('cex_wallet_count', 0),
                farm.get('infra_wallet_count', 0),
                farm.get('unknown_wallet_count', farm['total_wallets']),
                farm['classification_confidence'],
                farm['farm_risk_score'],
                farm['risk_level'],
                farm.get('strength_score', 0),
                now,
                now
            ))

            cluster_id = cursor.lastrowid
            farm_count += 1

            # Store members
            for wallet, role_data in farm['classification']['wallet_roles'].items():
                cursor.execute("""
                    INSERT OR REPLACE INTO farm_cluster_members (
                        cluster_id, wallet_address, wallet_role,
                        in_degree, out_degree, in_ratio, out_ratio, total_degree,
                        role_confidence, detected_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    cluster_id,
                    wallet,
                    role_data['role'],
                    role_data['in_degree'],
                    role_data['out_degree'],
                    role_data['in_ratio'],
                    role_data['out_ratio'],
                    role_data['total_degree'],
                    0.9 if role_data['role'] != 'ambiguous' else 0.5,
                    now,
                    now
                ))
                member_count += 1

            # Store edges with NEW: time_concentration and composite_weight
            subgraph = farm['subgraph']
            for source, dest, data in subgraph.edges(data=True):
                cursor.execute("""
                    INSERT OR REPLACE INTO farm_cluster_edges (
                        cluster_id, source_wallet, dest_wallet,
                        transfer_count, total_amount_sol, avg_amount_sol,
                        time_concentration, composite_weight,
                        detected_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    cluster_id,
                    source,
                    dest,
                    data.get('weight', 0),
                    data.get('total_amount', 0.0),
                    data.get('total_amount', 0.0) / max(data.get('weight', 1), 1),
                    data.get('time_concentration', 0.5),
                    data.get('composite_weight', 0),
                    now
                ))
                edge_count += 1

        conn.commit()
        conn.close()

        return farm_count, member_count, edge_count
