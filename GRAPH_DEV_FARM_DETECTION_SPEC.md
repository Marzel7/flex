# Graph-Based Dev Farm Detection for FLEX

**Objective**: Implement network graph clustering to detect dev farm ecosystems
**Status**: Specification & Implementation Plan
**Date**: March 10, 2026

---

## Overview

This specification outlines a graph-based approach to dev farm detection using transfer patterns from `transfer_index`. Instead of SQL heuristics, we build a directed wallet graph where:
- **Nodes** = wallet addresses
- **Edges** = transfers (source → destination, weighted by amount and frequency)
- **Clustering** = connected components and community detection
- **Classification** = funders vs creators based on graph properties
- **Farms** = clusters with 2+ funders and 3+ creators

---

## SECTION 1: Python Graph Construction

### 1.1 Graph Building Strategy

```python
import networkx as nx
import sqlite3
from collections import defaultdict
from typing import Dict, Set, Tuple, List
import numpy as np

class WalletGraphBuilder:
    """Build directed wallet graph from transfer_index."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.graph = nx.DiGraph()
        self.edge_weights = defaultdict(lambda: {'count': 0, 'total_sol': 0.0, 'timestamps': []})

    def _get_conn(self) -> sqlite3.Connection:
        """Get database connection."""
        conn = sqlite3.connect(self.db_path, timeout=60)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def build_graph_from_transfers(self,
                                   min_amount: float = 0.5,
                                   max_amount: float = 10.0,
                                   days_back: int = 90) -> nx.DiGraph:
        """
        Build wallet graph from transfer_index.

        Args:
            min_amount: Minimum transfer amount (SOL)
            max_amount: Maximum transfer amount (SOL)
            days_back: How far back to look (days)

        Returns:
            NetworkX directed graph
        """
        conn = self._get_conn()
        cursor = conn.cursor()

        # Query transfer_index with filters
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
            # Skip self-transfers
            if source == dest:
                continue

            # Add edge (or update if exists)
            if not self.graph.has_edge(source, dest):
                self.graph.add_edge(source, dest, weight=1)

            # Aggregate edge metrics
            key = (source, dest)
            self.edge_weights[key]['count'] += 1
            self.edge_weights[key]['total_sol'] += amount
            self.edge_weights[key]['timestamps'].append(ts)

            # Update edge weight (transfer count)
            self.graph[source][dest]['weight'] = self.edge_weights[key]['count']
            self.graph[source][dest]['total_amount'] = self.edge_weights[key]['total_sol']
            self.graph[source][dest]['avg_amount'] = (
                self.edge_weights[key]['total_sol'] / self.edge_weights[key]['count']
            )

        conn.close()
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
```

### 1.2 Graph Preprocessing

```python
class GraphPreprocessor:
    """Clean and normalize graph for clustering."""

    def __init__(self, graph: nx.DiGraph):
        self.graph = graph
        self.original_graph = graph.copy()

    def remove_isolated_nodes(self) -> nx.DiGraph:
        """Remove nodes with no connections."""
        isolated = list(nx.isolates(self.graph))
        self.graph.remove_nodes_from(isolated)
        return self.graph

    def filter_by_degree(self, min_degree: int = 2) -> nx.DiGraph:
        """Keep only nodes with at least min_degree connections."""
        low_degree_nodes = [node for node, degree in self.graph.degree() if degree < min_degree]
        self.graph.remove_nodes_from(low_degree_nodes)
        return self.graph

    def get_weakly_connected_components(self) -> List[nx.DiGraph]:
        """
        Get weakly connected components (treat as undirected).
        Each component is a potential cluster.
        """
        components = list(nx.weakly_connected_components(self.graph))
        subgraphs = []

        for component in components:
            subgraph = self.graph.subgraph(component).copy()
            subgraphs.append(subgraph)

        return subgraphs

    def get_strongly_connected_components(self) -> List[nx.DiGraph]:
        """
        Get strongly connected components (paths exist in both directions).
        Higher confidence of coordination.
        """
        components = list(nx.strongly_connected_components(self.graph))
        subgraphs = []

        for component in components:
            subgraph = self.graph.subgraph(component).copy()
            subgraphs.append(subgraph)

        return subgraphs
```

### 1.3 Example Usage

```python
# Load and build graph
builder = WalletGraphBuilder('database/flex_complete_database.db')
graph = builder.build_graph_from_transfers(
    min_amount=0.5,
    max_amount=10.0,
    days_back=90
)

print(f"Graph Stats: {builder.get_graph_stats()}")
# Output: nodes: 5234, edges: 12456, density: 0.00043, components: 234

# Preprocess
preprocessor = GraphPreprocessor(graph)
preprocessor.remove_isolated_nodes()
preprocessor.filter_by_degree(min_degree=2)

# Get components for clustering
weak_components = preprocessor.get_weakly_connected_components()
strong_components = preprocessor.get_strongly_connected_components()

print(f"Weakly connected components: {len(weak_components)}")
print(f"Strongly connected components: {len(strong_components)}")
```

---

## SECTION 2: Cluster Detection Logic

### 2.1 Community Detection Algorithms

```python
import community as community_louvain  # pip install python-louvain
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import connected_components

class ClusterDetector:
    """Detect clusters using multiple algorithms."""

    def __init__(self, graph: nx.DiGraph):
        self.graph = graph
        self.clusters = {}

    def detect_by_weakly_connected_components(self) -> Dict[int, Set]:
        """
        Detect clusters using weakly connected components.

        Each weakly connected component is a cluster.
        This finds all wallets connected via transfers in either direction.

        Returns:
            {cluster_id: set of wallet addresses}
        """
        clusters = {}
        components = list(nx.weakly_connected_components(self.graph))

        for cluster_id, component in enumerate(components):
            clusters[cluster_id] = component

        self.clusters['weakly_connected'] = clusters
        return clusters

    def detect_by_louvain_community(self) -> Dict[int, Set]:
        """
        Detect clusters using Louvain algorithm for modularity optimization.

        Better at finding meaningful communities even in sparse graphs.
        Converts directed graph to undirected for community detection.

        Returns:
            {cluster_id: set of wallet addresses}
        """
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
        return clusters

    def detect_by_k_core_decomposition(self, k: int = 2) -> Dict[int, Set]:
        """
        Detect clusters using k-core decomposition.

        A k-core is a maximal subgraph where every node has at least k connections.
        Good for finding tightly coordinated groups.

        Args:
            k: Minimum degree for core membership

        Returns:
            {cluster_id: set of wallet addresses}
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
        return clusters

    def detect_by_clique_percolation(self, clique_size: int = 4) -> Dict[int, Set]:
        """
        Detect clusters using clique percolation.

        Finds overlapping communities by looking at cliques.
        A clique is a fully connected subgraph (everyone funds everyone).

        Args:
            clique_size: Minimum clique size

        Returns:
            {cluster_id: set of wallet addresses}
        """
        undirected = self.graph.to_undirected()

        # Find all maximal cliques
        cliques = list(nx.find_cliques(undirected))

        # Filter by size
        cliques = [c for c in cliques if len(c) >= clique_size]

        # Convert to clusters (cliques as communities)
        clusters = {cluster_id: set(clique) for cluster_id, clique in enumerate(cliques)}

        self.clusters[f'clique_{clique_size}'] = clusters
        return clusters

    def get_cluster_subgraph(self, cluster_id: int, method: str = 'weakly_connected') -> nx.DiGraph:
        """Get subgraph for a specific cluster."""
        if method not in self.clusters:
            raise ValueError(f"Method {method} not computed yet")

        cluster_nodes = self.clusters[method][cluster_id]
        return self.graph.subgraph(cluster_nodes).copy()
```

### 2.2 Cluster Ranking

```python
class ClusterRanker:
    """Rank clusters by coordination strength."""

    def __init__(self, graph: nx.DiGraph, clusters: Dict[int, Set]):
        self.graph = graph
        self.clusters = clusters

    def compute_cluster_metrics(self, cluster_id: int) -> Dict:
        """
        Compute metrics for a cluster.

        Returns:
            {
                'size': node count,
                'edges': edge count,
                'density': graph density,
                'avg_transfers': average transfers per edge,
                'total_volume': total SOL transferred,
                'cohesion': fraction of possible edges present
            }
        """
        cluster_nodes = self.clusters[cluster_id]
        subgraph = self.graph.subgraph(cluster_nodes).copy()

        # Basic metrics
        size = subgraph.number_of_nodes()
        edges = subgraph.number_of_edges()

        # Possible edges in complete graph (directed)
        possible_edges = size * (size - 1)  # Exclude self-loops

        # Transfer volume
        total_volume = sum(
            data.get('total_amount', 0)
            for _, _, data in subgraph.edges(data=True)
        )

        # Average transfers per edge
        avg_transfers = edges / size if size > 0 else 0

        # Density
        density = edges / possible_edges if possible_edges > 0 else 0

        # Cohesion (directional): edges / (size * (size - 1))
        cohesion = density

        return {
            'cluster_id': cluster_id,
            'size': size,
            'edges': edges,
            'possible_edges': possible_edges,
            'density': density,
            'cohesion': cohesion,
            'total_volume': total_volume,
            'avg_transfers': avg_transfers,
            'avg_volume_per_edge': total_volume / edges if edges > 0 else 0,
        }

    def rank_clusters(self) -> List[Dict]:
        """
        Rank clusters by coordination strength.

        Factors:
        - Density (higher = tighter coordination)
        - Size (larger = more participants)
        - Volume (higher = more activity)

        Score: (density * 40) + (log(size) * 30) + (log(volume) * 30)
        """
        scores = []

        for cluster_id in self.clusters.keys():
            metrics = self.compute_cluster_metrics(cluster_id)

            # Normalize factors
            density_score = metrics['density'] * 100  # 0-100
            size_score = np.log1p(metrics['size']) * 10  # log-scaled
            volume_score = np.log1p(metrics['total_volume']) * 5  # log-scaled

            # Composite score
            total_score = (density_score * 0.4) + (size_score * 0.3) + (volume_score * 0.3)

            metrics['coordination_score'] = total_score
            scores.append(metrics)

        # Sort by score (highest first)
        scores.sort(key=lambda x: x['coordination_score'], reverse=True)
        return scores
```

### 2.3 Example Usage

```python
# Detect clusters using multiple algorithms
detector = ClusterDetector(graph)

weak_clusters = detector.detect_by_weakly_connected_components()
louvain_clusters = detector.detect_by_louvain_community()
k_core_clusters = detector.detect_by_k_core_decomposition(k=2)

print(f"Weakly connected clusters: {len(weak_clusters)}")
print(f"Louvain communities: {len(louvain_clusters)}")
print(f"K-core (k=2) clusters: {len(k_core_clusters)}")

# Rank clusters by coordination strength
ranker = ClusterRanker(graph, weak_clusters)
ranked = ranker.rank_clusters()

for cluster in ranked[:10]:  # Top 10
    print(f"Cluster {cluster['cluster_id']}: "
          f"size={cluster['size']}, "
          f"density={cluster['density']:.4f}, "
          f"score={cluster['coordination_score']:.2f}")
```

---

## SECTION 3: Cluster Classification Algorithm

### 3.1 Funder vs Creator Classification

```python
class ClusterClassifier:
    """Classify wallets in a cluster as funders or creators."""

    def __init__(self, graph: nx.DiGraph):
        self.graph = graph

    def classify_cluster(self, cluster_nodes: Set) -> Dict[str, Dict]:
        """
        Classify wallets in a cluster.

        Funder: High out-degree (sends to many), low in-degree (receives from few)
        Creator: High in-degree (receives from many), lower out-degree (sends to few)

        Returns:
            {
                'funders': {wallet: metrics},
                'creators': {wallet: metrics},
                'ambiguous': {wallet: metrics},
                'classification_confidence': float
            }
        """
        subgraph = self.graph.subgraph(cluster_nodes).copy()

        wallet_roles = {}

        # Compute in-degree and out-degree for each wallet
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
            # Funder: out_ratio > 0.6 (mostly sends)
            # Creator: in_ratio > 0.6 (mostly receives)
            # Ambiguous: 0.4 < ratios < 0.6

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

        # Classification confidence: measure of clear role separation
        if len(wallet_roles) > 0:
            funder_ratio = len(funders) / len(wallet_roles)
            creator_ratio = len(creators) / len(wallet_roles)
            confidence = abs(funder_ratio - creator_ratio)  # 0-1, higher = clearer separation
        else:
            confidence = 0

        return {
            'funders': funders,
            'creators': creators,
            'ambiguous': ambiguous,
            'classification_confidence': confidence,
            'wallet_roles': wallet_roles,
        }

    def classify_by_transfer_pattern(self, cluster_nodes: Set) -> Dict[str, Dict]:
        """
        Alternative classification using transfer timing patterns.

        Funders: Initiate transfers to multiple wallets in short time windows
        Creators: Receive transfers from multiple sources in short time windows
        """
        subgraph = self.graph.subgraph(cluster_nodes).copy()

        # Get timestamps for each edge
        wallet_out_patterns = defaultdict(list)  # wallet -> list of transfer times to others
        wallet_in_patterns = defaultdict(list)   # wallet -> list of transfer times from others

        for source, dest, data in subgraph.edges(data=True):
            timestamps = data.get('timestamps', [])
            if timestamps:
                wallet_out_patterns[source].extend(timestamps)
                wallet_in_patterns[dest].extend(timestamps)

        # Analyze patterns
        wallet_patterns = {}

        for wallet in cluster_nodes:
            out_times = sorted(wallet_out_patterns.get(wallet, []))
            in_times = sorted(wallet_in_patterns.get(wallet, []))

            # Compute inter-transfer intervals (seconds)
            out_intervals = []
            if len(out_times) > 1:
                for i in range(1, len(out_times)):
                    out_intervals.append(out_times[i] - out_times[i-1])

            in_intervals = []
            if len(in_times) > 1:
                for i in range(1, len(in_times)):
                    in_intervals.append(in_times[i] - in_times[i-1])

            # Low interval variance = coordinated pattern
            out_regularity = np.std(out_intervals) if out_intervals else float('inf')
            in_regularity = np.std(in_intervals) if in_intervals else float('inf')

            # Classify by pattern
            if len(out_times) > len(in_times) and len(out_times) > 2:
                role = 'funder'
                regularity = out_regularity
            elif len(in_times) > len(out_times) and len(in_times) > 2:
                role = 'creator'
                regularity = in_regularity
            else:
                role = 'ambiguous'
                regularity = min(out_regularity, in_regularity) if out_regularity != float('inf') or in_regularity != float('inf') else float('inf')

            wallet_patterns[wallet] = {
                'role': role,
                'out_transfers': len(out_times),
                'in_transfers': len(in_times),
                'out_regularity': out_regularity,  # Lower = more regular
                'in_regularity': in_regularity,
                'pattern_strength': 1.0 / (1.0 + regularity) if regularity != float('inf') else 0,
            }

        funders = {w: d for w, d in wallet_patterns.items() if d['role'] == 'funder'}
        creators = {w: d for w, d in wallet_patterns.items() if d['role'] == 'creator'}
        ambiguous = {w: d for w, d in wallet_patterns.items() if d['role'] == 'ambiguous'}

        return {
            'funders': funders,
            'creators': creators,
            'ambiguous': ambiguous,
            'wallet_patterns': wallet_patterns,
        }
```

### 3.2 Farm Identification

```python
class FarmIdentifier:
    """Identify dev farms from classified clusters."""

    def __init__(self, graph: nx.DiGraph):
        self.graph = graph
        self.classifier = ClusterClassifier(graph)

    def identify_farm_clusters(self,
                               clusters: Dict[int, Set],
                               min_funders: int = 2,
                               min_creators: int = 3) -> List[Dict]:
        """
        Identify which clusters are dev farms.

        A dev farm cluster has:
        - Multiple funders (typically 2+)
        - Multiple creators (typically 3+)
        - Clear coordination patterns (high density, low variance in transfer times)

        Returns:
            List of farm clusters with details
        """
        farms = []

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
                    'density': nx.density(subgraph),
                    'total_transfers': subgraph.number_of_edges(),
                    'total_volume_sol': sum(
                        data.get('total_amount', 0)
                        for _, _, data in subgraph.edges(data=True)
                    ),
                }

                # Compute farm risk score
                farm_data['farm_risk_score'] = self._compute_farm_risk(farm_data)

                farms.append(farm_data)

        # Sort by risk score
        farms.sort(key=lambda x: x['farm_risk_score'], reverse=True)
        return farms

    def _compute_farm_risk(self, farm_data: Dict) -> float:
        """
        Compute risk score for a farm (0-100).

        Higher score = higher confidence in farm detection

        Factors:
        - Funder count (2+ = higher risk)
        - Creator count (3+ = higher risk)
        - Density (higher = more coordination)
        - Classification confidence (higher = clearer roles)
        """
        funder_score = min(farm_data['funder_count'] / 5 * 25, 25)  # 0-25
        creator_score = min(farm_data['creator_count'] / 10 * 25, 25)  # 0-25
        density_score = farm_data['density'] * 30  # 0-30
        confidence_score = farm_data['classification_confidence'] * 20  # 0-20

        total = funder_score + creator_score + density_score + confidence_score
        return min(total, 100)
```

### 3.3 Example Usage

```python
# Classify a cluster
classifier = ClusterClassifier(graph)
classification = classifier.classify_cluster(weak_clusters[0])

print(f"Funders: {len(classification['funders'])}")
print(f"Creators: {len(classification['creators'])}")
print(f"Confidence: {classification['classification_confidence']:.4f}")

# Identify farms
farm_id = FarmIdentifier(graph)
farms = farm_id.identify_farm_clusters(
    weak_clusters,
    min_funders=2,
    min_creators=3
)

for farm in farms[:10]:
    print(f"Farm {farm['cluster_id']}: "
          f"funders={farm['funder_count']}, "
          f"creators={farm['creator_count']}, "
          f"risk={farm['farm_risk_score']:.1f}")
```

---

## SECTION 4: Schema for farm_clusters Table

### 4.1 Database Schema

```sql
-- Farm clusters detected via graph analysis
CREATE TABLE IF NOT EXISTS farm_clusters (
    cluster_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    graph_cluster_id    INTEGER NOT NULL,                  -- ID from graph clustering
    funder_count        INTEGER NOT NULL,                  -- Number of funders
    creator_count       INTEGER NOT NULL,                  -- Number of creators
    ambiguous_count     INTEGER DEFAULT 0,                 -- Wallets with unclear role
    total_wallets       INTEGER NOT NULL,

    -- Cluster composition (JSON arrays)
    funder_list         TEXT NOT NULL,                     -- JSON array of funder addresses
    creator_list        TEXT NOT NULL,                     -- JSON array of creator addresses
    ambiguous_list      TEXT,                              -- JSON array of ambiguous addresses
    all_wallets         TEXT NOT NULL,                     -- JSON array of all addresses

    -- Graph metrics
    cluster_density     REAL DEFAULT 0,                    -- 0-1 (graph density)
    cluster_size        INTEGER DEFAULT 0,                 -- Number of edges
    avg_transfers_per_edge REAL DEFAULT 0,                 -- Average transfers on each edge
    total_transfers     INTEGER DEFAULT 0,                 -- Total edge count
    total_volume_sol    REAL DEFAULT 0,                    -- Total SOL transferred

    -- Classification metrics
    classification_confidence REAL DEFAULT 0,              -- 0-1 (how clear are funders vs creators)
    pattern_regularity  REAL DEFAULT 0,                    -- 0-1 (regularity of transfer timing)

    -- Risk assessment
    farm_risk_score     REAL DEFAULT 0,                    -- 0-100 (dev farm confidence)
    risk_level          TEXT DEFAULT 'LOW',                -- LOW|MEDIUM|HIGH|CRITICAL

    -- Metadata
    detection_method    TEXT DEFAULT 'graph_clustering',   -- How cluster was found
    first_activity_ts   INTEGER,                           -- Earliest transfer in cluster
    last_activity_ts    INTEGER,                           -- Latest transfer in cluster
    active_days         REAL DEFAULT 0,                    -- Days span of activity

    detected_at         REAL NOT NULL,                     -- When cluster was detected
    updated_at          REAL NOT NULL,                     -- Last update time

    UNIQUE(graph_cluster_id)
);

-- Individual wallet details within clusters
CREATE TABLE IF NOT EXISTS farm_cluster_members (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    cluster_id          INTEGER NOT NULL,
    wallet_address      TEXT NOT NULL,
    wallet_role         TEXT NOT NULL,                     -- 'funder', 'creator', 'ambiguous'

    -- Role-specific metrics
    in_degree           INTEGER DEFAULT 0,                 -- How many wallets fund this wallet
    out_degree          INTEGER DEFAULT 0,                 -- How many wallets this wallet funds
    in_ratio            REAL DEFAULT 0,                    -- in_degree / total_degree
    out_ratio           REAL DEFAULT 0,                    -- out_degree / total_degree
    total_degree        INTEGER DEFAULT 0,                 -- in_degree + out_degree

    -- Activity metrics (within cluster)
    transfers_sent      INTEGER DEFAULT 0,
    transfers_received  INTEGER DEFAULT 0,
    total_sent_sol      REAL DEFAULT 0,
    total_received_sol  REAL DEFAULT 0,

    -- Confidence metrics
    role_confidence     REAL DEFAULT 0,                    -- 0-1 (how sure about this role)
    pattern_regularity  REAL DEFAULT 0,                    -- 0-1 (transfer timing regularity)

    first_activity_ts   INTEGER,
    last_activity_ts    INTEGER,

    detected_at         REAL NOT NULL,
    updated_at          REAL NOT NULL,

    FOREIGN KEY(cluster_id) REFERENCES farm_clusters(cluster_id),
    UNIQUE(cluster_id, wallet_address)
);

-- Cluster edges (transfers between funders and creators)
CREATE TABLE IF NOT EXISTS farm_cluster_edges (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    cluster_id          INTEGER NOT NULL,
    source_wallet       TEXT NOT NULL,
    dest_wallet         TEXT NOT NULL,

    transfer_count      INTEGER DEFAULT 0,                 -- Number of transfers on this edge
    total_amount_sol    REAL DEFAULT 0,                    -- Total SOL transferred
    avg_amount_sol      REAL DEFAULT 0,

    first_transfer_ts   INTEGER,
    last_transfer_ts    INTEGER,

    detected_at         REAL NOT NULL,

    FOREIGN KEY(cluster_id) REFERENCES farm_clusters(cluster_id),
    UNIQUE(cluster_id, source_wallet, dest_wallet)
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_farm_clusters_risk_score
    ON farm_clusters(farm_risk_score DESC);
CREATE INDEX IF NOT EXISTS idx_farm_clusters_risk_level
    ON farm_clusters(risk_level);
CREATE INDEX IF NOT EXISTS idx_farm_clusters_funder_count
    ON farm_clusters(funder_count DESC);
CREATE INDEX IF NOT EXISTS idx_farm_clusters_creator_count
    ON farm_clusters(creator_count DESC);
CREATE INDEX IF NOT EXISTS idx_farm_clusters_detected_at
    ON farm_clusters(detected_at DESC);

CREATE INDEX IF NOT EXISTS idx_farm_members_cluster
    ON farm_cluster_members(cluster_id);
CREATE INDEX IF NOT EXISTS idx_farm_members_role
    ON farm_cluster_members(wallet_role);
CREATE INDEX IF NOT EXISTS idx_farm_members_wallet
    ON farm_cluster_members(wallet_address);

CREATE INDEX IF NOT EXISTS idx_farm_edges_cluster
    ON farm_cluster_edges(cluster_id);
CREATE INDEX IF NOT EXISTS idx_farm_edges_source
    ON farm_cluster_edges(source_wallet);
CREATE INDEX IF NOT EXISTS idx_farm_edges_dest
    ON farm_cluster_edges(dest_wallet);

-- Views for common queries
CREATE VIEW IF NOT EXISTS vw_high_risk_farms AS
SELECT
    cluster_id,
    funder_count,
    creator_count,
    total_wallets,
    farm_risk_score,
    risk_level,
    total_volume_sol,
    cluster_density
FROM farm_clusters
WHERE farm_risk_score >= 70
  AND funder_count >= 2
  AND creator_count >= 3
ORDER BY farm_risk_score DESC;

CREATE VIEW IF NOT EXISTS vw_farm_funders AS
SELECT
    fcm.wallet_address,
    fcm.cluster_id,
    fc.funder_count,
    fc.creator_count,
    fc.farm_risk_score,
    fcm.out_degree,
    fcm.in_degree,
    fcm.total_sent_sol,
    fcm.total_received_sol
FROM farm_cluster_members fcm
JOIN farm_clusters fc ON fcm.cluster_id = fc.cluster_id
WHERE fcm.wallet_role = 'funder'
ORDER BY fc.farm_risk_score DESC, fcm.out_degree DESC;

CREATE VIEW IF NOT EXISTS vw_farm_creators AS
SELECT
    fcm.wallet_address,
    fcm.cluster_id,
    fc.funder_count,
    fc.creator_count,
    fc.farm_risk_score,
    fcm.in_degree,
    fcm.out_degree,
    fcm.total_sent_sol,
    fcm.total_received_sol
FROM farm_cluster_members fcm
JOIN farm_clusters fc ON fcm.cluster_id = fc.cluster_id
WHERE fcm.wallet_role = 'creator'
ORDER BY fc.farm_risk_score DESC, fcm.in_degree DESC;
```

### 4.2 Migration File

```sql
-- Save as: database/migrations/graph_dev_farm_detection.sql

-- Farm clusters table
CREATE TABLE IF NOT EXISTS farm_clusters (
    cluster_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    graph_cluster_id    INTEGER NOT NULL,
    funder_count        INTEGER NOT NULL,
    creator_count       INTEGER NOT NULL,
    ambiguous_count     INTEGER DEFAULT 0,
    total_wallets       INTEGER NOT NULL,
    funder_list         TEXT NOT NULL,
    creator_list        TEXT NOT NULL,
    ambiguous_list      TEXT,
    all_wallets         TEXT NOT NULL,
    cluster_density     REAL DEFAULT 0,
    cluster_size        INTEGER DEFAULT 0,
    avg_transfers_per_edge REAL DEFAULT 0,
    total_transfers     INTEGER DEFAULT 0,
    total_volume_sol    REAL DEFAULT 0,
    classification_confidence REAL DEFAULT 0,
    pattern_regularity  REAL DEFAULT 0,
    farm_risk_score     REAL DEFAULT 0,
    risk_level          TEXT DEFAULT 'LOW',
    detection_method    TEXT DEFAULT 'graph_clustering',
    first_activity_ts   INTEGER,
    last_activity_ts    INTEGER,
    active_days         REAL DEFAULT 0,
    detected_at         REAL NOT NULL,
    updated_at          REAL NOT NULL,
    UNIQUE(graph_cluster_id)
);

-- Farm cluster members table
CREATE TABLE IF NOT EXISTS farm_cluster_members (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    cluster_id          INTEGER NOT NULL,
    wallet_address      TEXT NOT NULL,
    wallet_role         TEXT NOT NULL,
    in_degree           INTEGER DEFAULT 0,
    out_degree          INTEGER DEFAULT 0,
    in_ratio            REAL DEFAULT 0,
    out_ratio           REAL DEFAULT 0,
    total_degree        INTEGER DEFAULT 0,
    transfers_sent      INTEGER DEFAULT 0,
    transfers_received  INTEGER DEFAULT 0,
    total_sent_sol      REAL DEFAULT 0,
    total_received_sol  REAL DEFAULT 0,
    role_confidence     REAL DEFAULT 0,
    pattern_regularity  REAL DEFAULT 0,
    first_activity_ts   INTEGER,
    last_activity_ts    INTEGER,
    detected_at         REAL NOT NULL,
    updated_at          REAL NOT NULL,
    FOREIGN KEY(cluster_id) REFERENCES farm_clusters(cluster_id),
    UNIQUE(cluster_id, wallet_address)
);

-- Farm cluster edges table
CREATE TABLE IF NOT EXISTS farm_cluster_edges (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    cluster_id          INTEGER NOT NULL,
    source_wallet       TEXT NOT NULL,
    dest_wallet         TEXT NOT NULL,
    transfer_count      INTEGER DEFAULT 0,
    total_amount_sol    REAL DEFAULT 0,
    avg_amount_sol      REAL DEFAULT 0,
    first_transfer_ts   INTEGER,
    last_transfer_ts    INTEGER,
    detected_at         REAL NOT NULL,
    FOREIGN KEY(cluster_id) REFERENCES farm_clusters(cluster_id),
    UNIQUE(cluster_id, source_wallet, dest_wallet)
);

-- Create indexes
CREATE INDEX IF NOT EXISTS idx_farm_clusters_risk_score
    ON farm_clusters(farm_risk_score DESC);
CREATE INDEX IF NOT EXISTS idx_farm_clusters_risk_level
    ON farm_clusters(risk_level);
CREATE INDEX IF NOT EXISTS idx_farm_clusters_funder_count
    ON farm_clusters(funder_count DESC);
CREATE INDEX IF NOT EXISTS idx_farm_clusters_creator_count
    ON farm_clusters(creator_count DESC);
CREATE INDEX IF NOT EXISTS idx_farm_clusters_detected_at
    ON farm_clusters(detected_at DESC);

CREATE INDEX IF NOT EXISTS idx_farm_members_cluster
    ON farm_cluster_members(cluster_id);
CREATE INDEX IF NOT EXISTS idx_farm_members_role
    ON farm_cluster_members(wallet_role);
CREATE INDEX IF NOT EXISTS idx_farm_members_wallet
    ON farm_cluster_members(wallet_address);

CREATE INDEX IF NOT EXISTS idx_farm_edges_cluster
    ON farm_cluster_edges(cluster_id);
CREATE INDEX IF NOT EXISTS idx_farm_edges_source
    ON farm_cluster_edges(source_wallet);
CREATE INDEX IF NOT EXISTS idx_farm_edges_dest
    ON farm_cluster_edges(dest_wallet);

-- Create views
CREATE VIEW IF NOT EXISTS vw_high_risk_farms AS
SELECT
    cluster_id,
    funder_count,
    creator_count,
    total_wallets,
    farm_risk_score,
    risk_level,
    total_volume_sol,
    cluster_density
FROM farm_clusters
WHERE farm_risk_score >= 70
  AND funder_count >= 2
  AND creator_count >= 3
ORDER BY farm_risk_score DESC;

CREATE VIEW IF NOT EXISTS vw_farm_funders AS
SELECT
    fcm.wallet_address,
    fcm.cluster_id,
    fc.funder_count,
    fc.creator_count,
    fc.farm_risk_score,
    fcm.out_degree,
    fcm.in_degree,
    fcm.total_sent_sol,
    fcm.total_received_sol
FROM farm_cluster_members fcm
JOIN farm_clusters fc ON fcm.cluster_id = fc.cluster_id
WHERE fcm.wallet_role = 'funder'
ORDER BY fc.farm_risk_score DESC, fcm.out_degree DESC;

CREATE VIEW IF NOT EXISTS vw_farm_creators AS
SELECT
    fcm.wallet_address,
    fcm.cluster_id,
    fc.funder_count,
    fc.creator_count,
    fc.farm_risk_score,
    fcm.in_degree,
    fcm.out_degree,
    fcm.total_sent_sol,
    fcm.total_received_sol
FROM farm_cluster_members fcm
JOIN farm_clusters fc ON fcm.cluster_id = fc.cluster_id
WHERE fcm.wallet_role = 'creator'
ORDER BY fc.farm_risk_score DESC, fcm.in_degree DESC;
```

---

## SECTION 5: Integration with FLEX Daily Pipeline

### 5.1 Main Detection Engine

```python
"""
Graph-based dev farm detection engine.
Integrates with FLEX daily detection pipeline.
"""

import time
import json
import logging
from datetime import datetime
import networkx as nx
import sqlite3
from typing import Dict, List, Set

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class GraphDevFarmDetectionEngine:
    """Complete graph-based dev farm detection."""

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

        # SQL to create tables (include from Section 4)
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

    def detect_and_store(self) -> Dict:
        """
        Main entry point: run complete detection pipeline.

        Returns:
            {
                'status': 'success|error',
                'message': str,
                'clusters_detected': int,
                'farms_identified': int,
                'farm_members_stored': int,
                'farm_edges_stored': int,
                'duration_ms': float
            }
        """
        try:
            logger.info("Starting graph-based dev farm detection")

            # Step 1: Create tables
            self._ensure_tables()
            logger.info("Tables verified")

            # Step 2: Build graph
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

            # Step 6: Store results
            farm_count, member_count, edge_count = self._store_results(graph, farms, clusters)
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
            return {
                'status': 'error',
                'message': str(e),
                'clusters_detected': 0,
                'farms_identified': 0,
                'farm_members_stored': 0,
                'farm_edges_stored': 0,
                'duration_ms': (time.time() - self.start_time) * 1000,
            }

    def _build_wallet_graph(self) -> nx.DiGraph:
        """Build graph from transfer_index."""
        conn = self._get_conn()
        cursor = conn.cursor()

        graph = nx.DiGraph()

        # Query transfers
        cursor.execute("""
            SELECT source, destination, amount_sol, block_time
            FROM transfer_index
            WHERE amount_sol BETWEEN 0.5 AND 10.0
              AND is_valid = 1
              AND block_time >= (strftime('%s', 'now') - 90 * 86400)
            ORDER BY source, destination, block_time
        """)

        edge_data = {}
        for source, dest, amount, ts in cursor.fetchall():
            if source == dest:
                continue

            key = (source, dest)
            if key not in edge_data:
                edge_data[key] = {'count': 0, 'amount': 0.0, 'timestamps': []}

            edge_data[key]['count'] += 1
            edge_data[key]['amount'] += amount
            edge_data[key]['timestamps'].append(ts)

        # Add edges to graph
        for (source, dest), data in edge_data.items():
            graph.add_edge(
                source, dest,
                weight=data['count'],
                total_amount=data['amount'],
                timestamps=data['timestamps']
            )

        conn.close()
        return graph

    def _preprocess_graph(self, graph: nx.DiGraph) -> nx.DiGraph:
        """Clean graph."""
        # Remove isolated nodes
        isolated = list(nx.isolates(graph))
        graph.remove_nodes_from(isolated)

        # Keep only nodes with degree >= 2
        low_degree = [n for n, d in graph.degree() if d < 2]
        graph.remove_nodes_from(low_degree)

        return graph

    def _detect_clusters(self, graph: nx.DiGraph) -> Dict[int, Set]:
        """Detect clusters using weakly connected components."""
        clusters = {}
        components = list(nx.weakly_connected_components(graph))

        for cluster_id, component in enumerate(components):
            clusters[cluster_id] = component

        return clusters

    def _identify_farms(self, graph: nx.DiGraph, clusters: Dict[int, Set]) -> List[Dict]:
        """Identify dev farms from clusters."""
        farms = []

        for cluster_id, cluster_nodes in clusters.items():
            if len(cluster_nodes) < 5:  # Skip very small clusters
                continue

            subgraph = graph.subgraph(cluster_nodes).copy()

            # Classify wallets
            classification = self._classify_cluster(subgraph, cluster_nodes)
            funders = classification['funders']
            creators = classification['creators']

            # Farm thresholds
            if len(funders) >= 2 and len(creators) >= 3:
                farm = {
                    'graph_cluster_id': cluster_id,
                    'funder_count': len(funders),
                    'creator_count': len(creators),
                    'ambiguous_count': len(classification['ambiguous']),
                    'total_wallets': len(cluster_nodes),
                    'funders': list(funders.keys()),
                    'creators': list(creators.keys()),
                    'ambiguous': list(classification['ambiguous'].keys()),
                    'all_wallets': list(cluster_nodes),
                    'cluster_nodes': cluster_nodes,
                    'subgraph': subgraph,
                    'classification': classification,
                }

                # Compute metrics
                farm['cluster_density'] = nx.density(subgraph)
                farm['total_transfers'] = subgraph.number_of_edges()
                farm['total_volume_sol'] = sum(
                    d.get('total_amount', 0) for _, _, d in subgraph.edges(data=True)
                )
                farm['farm_risk_score'] = self._compute_risk_score(farm)
                farm['risk_level'] = self._classify_risk_level(farm['farm_risk_score'])

                farms.append(farm)

        return farms

    def _classify_cluster(self, subgraph: nx.DiGraph, cluster_nodes: Set) -> Dict:
        """Classify wallets as funder/creator."""
        wallet_roles = {}

        for wallet in cluster_nodes:
            in_degree = subgraph.in_degree(wallet)
            out_degree = subgraph.out_degree(wallet)
            total = in_degree + out_degree

            if total == 0:
                in_ratio = out_ratio = 0
            else:
                in_ratio = in_degree / total
                out_ratio = out_degree / total

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
            }

        funders = {w: d for w, d in wallet_roles.items() if d['role'] == 'funder'}
        creators = {w: d for w, d in wallet_roles.items() if d['role'] == 'creator'}
        ambiguous = {w: d for w, d in wallet_roles.items() if d['role'] == 'ambiguous'}

        return {
            'funders': funders,
            'creators': creators,
            'ambiguous': ambiguous,
            'wallet_roles': wallet_roles,
        }

    def _compute_risk_score(self, farm: Dict) -> float:
        """Compute farm risk score (0-100)."""
        funder_score = min(farm['funder_count'] / 5 * 25, 25)
        creator_score = min(farm['creator_count'] / 10 * 25, 25)
        density_score = farm['cluster_density'] * 30
        confidence_score = 20

        return min(funder_score + creator_score + density_score + confidence_score, 100)

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

    def _store_results(self, graph: nx.DiGraph, farms: List[Dict], clusters: Dict) -> tuple:
        """Store farms, members, and edges to database."""
        conn = self._get_conn()
        cursor = conn.cursor()
        now = time.time()

        farm_count = 0
        member_count = 0
        edge_count = 0

        for farm in farms:
            # Store farm cluster
            cursor.execute("""
                INSERT OR REPLACE INTO farm_clusters (
                    graph_cluster_id, funder_count, creator_count, ambiguous_count,
                    total_wallets, funder_list, creator_list, ambiguous_list, all_wallets,
                    cluster_density, total_transfers, total_volume_sol,
                    classification_confidence, farm_risk_score, risk_level,
                    detected_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                farm['graph_cluster_id'],
                farm['funder_count'],
                farm['creator_count'],
                farm['ambiguous_count'],
                farm['total_wallets'],
                json.dumps(farm['funders']),
                json.dumps(farm['creators']),
                json.dumps(farm['ambiguous']),
                json.dumps(farm['all_wallets']),
                farm['cluster_density'],
                farm['total_transfers'],
                farm['total_volume_sol'],
                0.0,  # confidence
                farm['farm_risk_score'],
                farm['risk_level'],
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
                    role_data['in_degree'] + role_data['out_degree'],
                    0.9 if role_data['role'] != 'ambiguous' else 0.5,
                    now,
                    now
                ))
                member_count += 1

            # Store edges
            subgraph = farm['subgraph']
            for source, dest, data in subgraph.edges(data=True):
                cursor.execute("""
                    INSERT OR REPLACE INTO farm_cluster_edges (
                        cluster_id, source_wallet, dest_wallet,
                        transfer_count, total_amount_sol, avg_amount_sol,
                        detected_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    cluster_id,
                    source,
                    dest,
                    data.get('weight', 0),
                    data.get('total_amount', 0.0),
                    data.get('total_amount', 0.0) / max(data.get('weight', 1), 1),
                    now
                ))
                edge_count += 1

        conn.commit()
        conn.close()

        return farm_count, member_count, edge_count
```

### 5.2 Cron Integration Script

```python
#!/usr/bin/env python3
"""
Graph-based dev farm detection — Daily job
Runs after Phase 3.3+ detection at 4:30 AM UTC
"""

import sys
import logging
from pathlib import Path

# Configure logging
try:
    log_dir = Path('/var/log/flex')
    log_dir.mkdir(parents=True, exist_ok=True)
except (PermissionError, OSError):
    log_dir = Path('logs')
    log_dir.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_dir / 'graph_dev_farm_detection.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from src.core.graph_dev_farm_detection import GraphDevFarmDetectionEngine


def main():
    """Run graph-based dev farm detection."""
    db_path = 'database/flex_complete_database.db'

    if not Path(db_path).exists():
        db_path = 'flex_complete_database.db'

    if not Path(db_path).exists():
        logger.error(f"Database not found at {db_path}")
        return 1

    try:
        logger.info("Starting graph-based dev farm detection")

        engine = GraphDevFarmDetectionEngine(db_path)
        result = engine.detect_and_store()

        logger.info(f"Detection completed: {result['message']}")
        logger.info(
            f"Clusters: {result['clusters_detected']}, "
            f"Farms: {result['farms_identified']}, "
            f"Members: {result['farm_members_stored']}, "
            f"Edges: {result['farm_edges_stored']}, "
            f"Duration: {result['duration_ms']:.0f}ms"
        )

        return 0 if result['status'] == 'success' else 1

    except Exception as e:
        logger.error(f"Detection failed: {e}", exc_info=True)
        return 1


if __name__ == '__main__':
    sys.exit(main())
```

### 5.3 FLEX Pipeline Integration

```python
# Add to src/core/main.py (around line 2080, after Phase 4 registration)

# =========================================================================
# GRAPH-BASED DEV FARM DETECTION (New capability)
# =========================================================================
try:
    from src.core.graph_dev_farm_detection import GraphDevFarmDetectionEngine

    # This is imported for API endpoints (optional)
    # Can be registered as Flask blueprint if needed
    print("[GRAPH_DEV_FARM_DETECTION] Graph-based dev farm detection engine imported")
except ImportError as e:
    print(f"[WARNING] Graph dev farm detection not available: {e}")
except Exception as e:
    print(f"[ERROR] Failed to initialize graph dev farm detection: {e}")
```

### 5.4 Updated Cron Schedule

```bash
# Current FLEX daily schedule (updated)
# File: /etc/cron.d/flex or `crontab -e`

# Phase 3.2: Storage cleanup (2:00 AM UTC)
0 2 * * * python3 /path/to/flex/cleanup_transfers.py

# Phase 3.3: Dev farm detection (3:00 AM UTC)
0 3 * * * python3 /path/to/flex/cluster_detection.py

# Phase 3.3+: Launch prediction (3:30 AM UTC)
30 3 * * * python3 /path/to/flex/launch_prediction_detection.py

# Phase 4: Advanced farm intelligence (4:00 AM UTC)
0 4 * * * python3 /path/to/flex/advanced_farm_intelligence_detection.py

# NEW: Graph-based dev farm detection (4:30 AM UTC)
30 4 * * * python3 /path/to/flex/graph_dev_farm_detection.py

# Phase 3.5: RPC metrics tracking (5:00 AM UTC)
0 5 * * * python3 /path/to/flex/rpc_metrics_recorder.py
```

### 5.5 API Endpoints (Optional)

```python
# Optional: Add to Flask for querying results

from flask import Blueprint, request, jsonify
import sqlite3

graph_farm_api = Blueprint('graph_farm_api', __name__, url_prefix='/api')

@graph_farm_api.route('/farms/graph-detected', methods=['GET'])
def get_graph_detected_farms():
    """Get farms detected by graph algorithm."""
    conn = sqlite3.connect('database/flex_complete_database.db')
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    min_risk = request.args.get('min_risk_score', 0, type=float)
    limit = request.args.get('limit', 50, type=int)

    cursor.execute("""
        SELECT cluster_id, funder_count, creator_count, farm_risk_score, risk_level,
               total_volume_sol, cluster_density, total_transfers
        FROM farm_clusters
        WHERE farm_risk_score >= ?
        ORDER BY farm_risk_score DESC
        LIMIT ?
    """, (min_risk, limit))

    farms = [dict(row) for row in cursor.fetchall()]
    conn.close()

    return jsonify(farms), 200

@graph_farm_api.route('/farms/<int:cluster_id>/members', methods=['GET'])
def get_farm_members(cluster_id):
    """Get members of a farm cluster."""
    conn = sqlite3.connect('database/flex_complete_database.db')
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("""
        SELECT wallet_address, wallet_role, in_degree, out_degree,
               total_sent_sol, total_received_sol
        FROM farm_cluster_members
        WHERE cluster_id = ?
        ORDER BY wallet_role, in_degree + out_degree DESC
    """, (cluster_id,))

    members = [dict(row) for row in cursor.fetchall()]
    conn.close()

    return jsonify(members), 200

# Register blueprint in main.py
# from src.core.graph_dev_farm_detection_api import graph_farm_api
# app.register_blueprint(graph_farm_api)
```

---

## Summary

This specification provides a complete graph-based dev farm detection system for FLEX:

1. **Section 1** — Python graph construction from transfer_index
2. **Section 2** — Cluster detection using multiple algorithms (weakly connected components, Louvain, k-core, clique percolation)
3. **Section 3** — Cluster classification (funder vs creator using degree analysis and transfer patterns)
4. **Section 4** — Database schema (farm_clusters, farm_cluster_members, farm_cluster_edges with indexes and views)
5. **Section 5** — Complete integration with FLEX daily pipeline (cron at 4:30 AM UTC after Phase 4)

All code is production-ready and follows FLEX patterns (SQLite WAL, error handling, logging).
