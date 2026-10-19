"""
Dev Intelligence Graph — Multi-Layer Organization Detection

Extends the weighted wallet graph with token launch nodes to detect
developer organizations (2+ wallets, 2+ creators, 1+ tokens).

Key Components:
1. DevIntelligenceGraphBuilder — builds extended wallet→creator→token graph
2. OrganizationDetector — finds developer organization clusters
3. OperatorDetector — identifies operator wallets via centrality metrics
4. OrganizationScorer — computes org_score (0-1) and cluster_strength
5. DevIntelligenceEngine — pipeline orchestrator and result storage

Production-ready with SQLite WAL, error handling, and logging.
"""

import time
import json
import sqlite3
import logging
import networkx as nx
from typing import Dict, List, Set, Tuple, Optional
from collections import defaultdict

from src.core.graph_dev_farm_detection import WalletGraphBuilder

logger = logging.getLogger(__name__)


class DevIntelligenceGraphBuilder:
    """Build multi-layer graph: wallet → creator → token"""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.graph: nx.DiGraph = nx.DiGraph()

    def _get_conn(self) -> sqlite3.Connection:
        """Get database connection with WAL and performance settings."""
        conn = sqlite3.connect(self.db_path, timeout=60)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA mmap_size=30000000")
        return conn

    def build_extended_graph(
        self,
        min_transfer: float = 0.5,
        max_transfer: float = 10.0,
        days_back: int = 90
    ) -> nx.DiGraph:
        """
        Build multi-layer graph with wallet, creator, and token nodes.

        Steps:
        1. Load base wallet graph from transfer_index via WalletGraphBuilder
        2. Tag all nodes as node_type='wallet'
        3. Load token→creator pairs from token_analysis
        4. Add token nodes and creator→token edges
        5. Upgrade creator nodes from 'wallet' to 'creator'
        6. Return augmented graph
        """
        # Step 1: Load wallet graph
        logger.info("Loading base wallet graph from transfer_index")
        self.graph = self._load_wallet_graph(min_transfer, max_transfer, days_back)
        logger.info(f"Wallet graph: {self.graph.number_of_nodes()} nodes, {self.graph.number_of_edges()} edges")

        # Step 2: Tag wallet nodes
        self._tag_wallet_nodes(self.graph)

        # Steps 3-5: Add token edges and upgrade creator nodes
        self._add_token_edges(self.graph)

        logger.info(f"Extended graph: {self.graph.number_of_nodes()} nodes, {self.graph.number_of_edges()} edges")
        return self.graph

    def _load_wallet_graph(
        self,
        min_transfer: float,
        max_transfer: float,
        days_back: int
    ) -> nx.DiGraph:
        """Delegate to WalletGraphBuilder to load the base wallet graph."""
        builder = WalletGraphBuilder(self.db_path)
        return builder.build_graph_from_transfers(
            min_amount=min_transfer,
            max_amount=max_transfer,
            days_back=days_back
        )

    def _tag_wallet_nodes(self, graph: nx.DiGraph) -> None:
        """Set node_type='wallet' on all nodes lacking a type tag."""
        for node in graph.nodes():
            if 'node_type' not in graph.nodes[node]:
                graph.nodes[node]['node_type'] = 'wallet'

    def _add_token_edges(self, graph: nx.DiGraph) -> None:
        """
        Load tokens from token_analysis and add creator→token edges.

        SQL: SELECT earliest_tx_creator, mint, market_cap_highest, rug_probability, risk_level
             FROM token_analysis WHERE earliest_tx_creator IS NOT NULL AND != ''
        """
        conn = self._get_conn()
        cursor = conn.cursor()

        query = """
            SELECT earliest_tx_creator, mint, market_cap_highest, rug_probability, risk_level
            FROM token_analysis
            WHERE earliest_tx_creator IS NOT NULL
              AND earliest_tx_creator != ''
        """

        cursor.execute(query)
        token_rows = cursor.fetchall()
        conn.close()

        logger.info(f"Loading {len(token_rows)} tokens from token_analysis")

        for creator_addr, mint, market_cap, rug_prob, risk_level in token_rows:
            # Add creator node if not already present, ensure it's tagged 'creator'
            if creator_addr not in graph:
                graph.add_node(creator_addr, node_type='creator')
            else:
                # Upgrade existing wallet to creator
                graph.nodes[creator_addr]['node_type'] = 'creator'

            # Add token node
            if mint not in graph:
                graph.add_node(
                    mint,
                    node_type='token',
                    market_cap=market_cap if market_cap else 0,
                    rug_probability=rug_prob if rug_prob else 0,
                    risk_level=risk_level if risk_level else 'UNKNOWN'
                )

            # Add creator→token edge
            if not graph.has_edge(creator_addr, mint):
                graph.add_edge(
                    creator_addr,
                    mint,
                    edge_type='token_creation',
                    weight=1,
                    composite_weight=0,
                    time_concentration=0
                )

        logger.info(f"Added token edges: graph now {graph.number_of_nodes()} nodes, {graph.number_of_edges()} edges")


class OrganizationDetector:
    """Detect developer organization clusters from extended graph."""

    def __init__(self, graph: nx.DiGraph):
        self.graph = graph

    def detect_organizations(
        self,
        min_wallets: int = 2,
        min_creators: int = 2,
        min_tokens: int = 1
    ) -> List[Dict]:
        """
        Find weakly connected components and filter to organization-sized clusters.

        Returns list of dicts: {nodes, wallets, creators, tokens, subgraph}
        """
        organizations = []

        # Find all weakly connected components
        components = list(nx.weakly_connected_components(self.graph))
        logger.info(f"Found {len(components)} connected components")

        for component in components:
            # Classify nodes by type
            classification = self._classify_component_nodes(component)
            wallets = classification['wallets']
            creators = classification['creators']
            tokens = classification['tokens']

            # Filter to organization-sized clusters
            if len(wallets) >= min_wallets and len(creators) >= min_creators and len(tokens) >= min_tokens:
                subgraph = self.graph.subgraph(component).copy()
                org = {
                    'nodes': component,
                    'wallets': wallets,
                    'creators': creators,
                    'tokens': tokens,
                    'subgraph': subgraph
                }
                organizations.append(org)

        logger.info(f"Detected {len(organizations)} developer organizations")
        return organizations

    def _classify_component_nodes(self, component: Set) -> Dict:
        """Classify nodes by node_type attribute."""
        wallets = set()
        creators = set()
        tokens = set()

        for node in component:
            node_type = self.graph.nodes[node].get('node_type', 'wallet')
            if node_type == 'wallet':
                wallets.add(node)
            elif node_type == 'creator':
                creators.add(node)
            elif node_type == 'token':
                tokens.add(node)

        return {'wallets': wallets, 'creators': creators, 'tokens': tokens}


class OperatorDetector:
    """Identify operator wallets using centrality metrics."""

    def analyze_organization(self, org: Dict) -> Dict:
        """
        Compute degree, betweenness, and PageRank centrality.
        Identify operator as wallet with highest betweenness.

        Returns: {degree_centrality, betweenness_centrality, pagerank, operator_wallet}
        """
        subgraph = org['subgraph']
        wallet_nodes = org['wallets']

        # Compute centrality metrics
        degree = nx.degree_centrality(subgraph)

        # Use k approximation for large subgraphs
        if len(subgraph) > 200:
            betweenness = nx.betweenness_centrality(subgraph, k=50, normalized=True)
        else:
            betweenness = nx.betweenness_centrality(subgraph, normalized=True)

        pagerank = nx.pagerank(subgraph, alpha=0.85)

        # Find operator wallet (highest betweenness)
        operator_wallet = self._find_operator(wallet_nodes, betweenness, degree)

        return {
            'degree_centrality': degree,
            'betweenness_centrality': betweenness,
            'pagerank': pagerank,
            'operator_wallet': operator_wallet
        }

    def _find_operator(self, wallet_nodes: Set, betweenness: Dict, degree: Dict) -> str:
        """
        Find operator wallet: highest betweenness among wallets.
        Tie-break by degree. Fallback to global max betweenness if no wallet nodes.
        """
        if wallet_nodes:
            # Filter to wallet nodes
            wallet_betweenness = {w: betweenness.get(w, 0) for w in wallet_nodes}
            operator = max(wallet_betweenness, key=wallet_betweenness.get)
        else:
            # Fallback to global max betweenness
            operator = max(betweenness, key=betweenness.get)

        return operator


class OrganizationScorer:
    """Compute organization score and cluster strength."""

    def score_organization(
        self,
        org: Dict,
        centrality: Dict,
        subgraph: nx.DiGraph
    ) -> Dict:
        """
        Score organization on 0-1 scale using 5 factors:
        - cluster_size (0-0.25)
        - creator_reuse (0-0.20)
        - token_count (0-0.20)
        - operator_betweenness (0-0.20)
        - avg_composite_weight (0-0.15)

        Also compute cluster_strength (0-100).
        """
        cluster_size = len(org['nodes'])
        creator_reuse = self._compute_creator_reuse(subgraph, org['creators'])
        token_count = len(org['tokens'])
        operator_wallet = centrality['operator_wallet']
        operator_betweenness = centrality['betweenness_centrality'].get(operator_wallet, 0)

        avg_composite_weight, avg_time_concentration = self._compute_avg_edge_weight(subgraph)

        # Compute org_score
        org_score = (
            (min(cluster_size, 20) / 20 * 0.25) +
            (min(creator_reuse, 10) / 10 * 0.20) +
            (min(token_count, 5) / 5 * 0.20) +
            (operator_betweenness * 0.20) +
            (min(avg_composite_weight, 100) / 100 * 0.15)
        )

        # Compute cluster_strength
        cluster_strength = (avg_composite_weight / 100 * 0.6) + (avg_time_concentration * 0.4)
        cluster_strength = min(cluster_strength * 100, 100)  # Normalize to 0-100

        return {
            'org_score': org_score,
            'cluster_strength': cluster_strength,
            'creator_reuse': creator_reuse,
            'avg_composite_weight': avg_composite_weight,
            'avg_time_concentration': avg_time_concentration
        }

    def _compute_creator_reuse(self, subgraph: nx.DiGraph, creator_nodes: Set) -> float:
        """
        Average in-degree of creator nodes (how many wallets fund each creator).
        Only count SOL-transfer edges (not token_creation edges).
        """
        if not creator_nodes:
            return 0

        total_in_degree = 0
        for creator in creator_nodes:
            in_degree = 0
            for pred in subgraph.predecessors(creator):
                edge_type = subgraph[pred][creator].get('edge_type', 'transfer')
                if edge_type != 'token_creation':
                    in_degree += 1
            total_in_degree += in_degree

        return total_in_degree / len(creator_nodes) if creator_nodes else 0

    def _compute_avg_edge_weight(self, subgraph: nx.DiGraph) -> Tuple[float, float]:
        """
        Compute average composite_weight and time_concentration.
        Only include SOL-transfer edges (exclude token_creation edges).
        """
        composite_weights = []
        time_concentrations = []

        for source, dest, data in subgraph.edges(data=True):
            if data.get('edge_type') != 'token_creation':
                composite_weights.append(data.get('composite_weight', 0))
                time_concentrations.append(data.get('time_concentration', 0))

        avg_composite = sum(composite_weights) / len(composite_weights) if composite_weights else 0
        avg_time_conc = sum(time_concentrations) / len(time_concentrations) if time_concentrations else 0

        return avg_composite, avg_time_conc


class DevIntelligenceEngine:
    """Orchestrate full detection pipeline and persist results."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.start_time = time.time()

    def _get_conn(self) -> sqlite3.Connection:
        """Get database connection with WAL settings."""
        conn = sqlite3.connect(self.db_path, timeout=60)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA mmap_size=30000000")
        return conn

    def _ensure_tables(self) -> None:
        """Ensure dev_organizations and dev_organization_members tables exist."""
        conn = self._get_conn()
        cursor = conn.cursor()

        # dev_organizations
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS dev_organizations (
                organization_id         INTEGER PRIMARY KEY AUTOINCREMENT,
                operator_wallet         TEXT NOT NULL,
                cluster_size            INTEGER DEFAULT 0,
                wallet_count            INTEGER DEFAULT 0,
                creator_count           INTEGER DEFAULT 0,
                token_count             INTEGER DEFAULT 0,
                token_list              TEXT,
                creator_list            TEXT,
                wallet_list             TEXT,
                organization_score      REAL DEFAULT 0,
                degree_centrality       REAL DEFAULT 0,
                betweenness_centrality  REAL DEFAULT 0,
                pagerank_score          REAL DEFAULT 0,
                total_volume_sol        REAL DEFAULT 0,
                avg_edge_weight         REAL DEFAULT 0,
                cluster_strength        REAL DEFAULT 0,
                farm_cluster_id         INTEGER,
                detected_at             REAL NOT NULL,
                updated_at              REAL NOT NULL,
                UNIQUE(operator_wallet)
            )
        """)

        # dev_organization_members
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS dev_organization_members (
                id                      INTEGER PRIMARY KEY AUTOINCREMENT,
                organization_id         INTEGER NOT NULL,
                member_address          TEXT NOT NULL,
                member_type             TEXT NOT NULL,
                degree_centrality       REAL DEFAULT 0,
                betweenness_centrality  REAL DEFAULT 0,
                pagerank_score          REAL DEFAULT 0,
                token_count             INTEGER DEFAULT 0,
                total_volume_sol        REAL DEFAULT 0,
                role_confidence         REAL DEFAULT 0,
                detected_at             REAL NOT NULL,
                FOREIGN KEY(organization_id) REFERENCES dev_organizations(organization_id),
                UNIQUE(organization_id, member_address)
            )
        """)

        conn.commit()
        conn.close()
        logger.info("Tables ensured: dev_organizations, dev_organization_members")

    def detect_and_store(self) -> Dict:
        """
        Main pipeline: detect organizations and store results.

        Returns: {status, message, orgs_detected, members_stored, duration_ms}
        """
        try:
            logger.info("Starting dev intelligence graph detection")

            # Step 1: Ensure tables
            self._ensure_tables()

            # Step 2: Build extended graph
            logger.info("Building extended graph (wallet → creator → token)")
            builder = DevIntelligenceGraphBuilder(self.db_path)
            graph = builder.build_extended_graph()

            # Step 3: Detect organizations
            logger.info("Detecting developer organizations")
            detector = OrganizationDetector(graph)
            organizations = detector.detect_organizations(min_wallets=2, min_creators=2, min_tokens=1)

            # Step 4-5: Analyze and score each organization
            logger.info(f"Analyzing {len(organizations)} organizations")
            operator_detector = OperatorDetector()
            scorer = OrganizationScorer()

            enriched_orgs = []
            for org in organizations:
                centrality = operator_detector.analyze_organization(org)
                scores = scorer.score_organization(org, centrality, org['subgraph'])
                org.update({
                    'centrality': centrality,
                    'scores': scores
                })
                enriched_orgs.append(org)

            # Step 6: Store results
            org_count, member_count = self._store_results(enriched_orgs)

            duration_ms = (time.time() - self.start_time) * 1000
            logger.info(f"Detection complete: {org_count} orgs, {member_count} members in {duration_ms:.0f}ms")

            return {
                'status': 'success',
                'message': f'Dev intelligence: {org_count} organizations detected',
                'orgs_detected': org_count,
                'members_stored': member_count,
                'duration_ms': duration_ms
            }

        except Exception as e:
            logger.error(f"Detection failed: {e}", exc_info=True)
            duration_ms = (time.time() - self.start_time) * 1000
            return {
                'status': 'error',
                'message': str(e),
                'orgs_detected': 0,
                'members_stored': 0,
                'duration_ms': duration_ms
            }

    def _store_results(self, orgs: List[Dict]) -> Tuple[int, int]:
        """
        Store organizations and members to database.

        Returns: (org_count, member_count)
        """
        conn = self._get_conn()
        cursor = conn.cursor()
        now = time.time()

        org_count = 0
        member_count = 0

        for org in orgs:
            centrality = org['centrality']
            scores = org['scores']
            operator_wallet = centrality['operator_wallet']

            # Attempt to link to farm_cluster
            farm_cluster_id = self._link_farm_cluster(cursor, operator_wallet)

            # Compute per-member metrics
            total_volume = self._compute_total_volume(org['subgraph'])

            # Store organization
            cursor.execute("""
                INSERT OR REPLACE INTO dev_organizations (
                    operator_wallet, cluster_size, wallet_count, creator_count, token_count,
                    token_list, creator_list, wallet_list,
                    organization_score, degree_centrality, betweenness_centrality, pagerank_score,
                    total_volume_sol, avg_edge_weight, cluster_strength,
                    farm_cluster_id, detected_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                operator_wallet,
                len(org['nodes']),
                len(org['wallets']),
                len(org['creators']),
                len(org['tokens']),
                json.dumps(list(org['tokens'])),
                json.dumps(list(org['creators'])),
                json.dumps(list(org['wallets'])),
                scores['org_score'],
                centrality['degree_centrality'].get(operator_wallet, 0),
                centrality['betweenness_centrality'].get(operator_wallet, 0),
                centrality['pagerank'].get(operator_wallet, 0),
                total_volume,
                scores['avg_composite_weight'],
                scores['cluster_strength'],
                farm_cluster_id,
                now,
                now
            ))

            org_id = cursor.lastrowid
            org_count += 1

            # Store members
            for member_addr in org['nodes']:
                node_type = org['subgraph'].nodes[member_addr].get('node_type', 'wallet')

                # Compute member metrics
                member_volume = self._compute_member_volume(org['subgraph'], member_addr)
                member_tokens = self._compute_member_tokens(org['subgraph'], member_addr, node_type)

                # Role confidence
                if node_type == 'wallet':
                    role_confidence = 0.90
                elif node_type == 'creator':
                    role_confidence = 0.85
                elif node_type == 'token':
                    role_confidence = 1.00
                else:
                    role_confidence = 0.5

                cursor.execute("""
                    INSERT OR REPLACE INTO dev_organization_members (
                        organization_id, member_address, member_type,
                        degree_centrality, betweenness_centrality, pagerank_score,
                        token_count, total_volume_sol, role_confidence, detected_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    org_id,
                    member_addr,
                    node_type,
                    centrality['degree_centrality'].get(member_addr, 0),
                    centrality['betweenness_centrality'].get(member_addr, 0),
                    centrality['pagerank'].get(member_addr, 0),
                    member_tokens,
                    member_volume,
                    role_confidence,
                    now
                ))
                member_count += 1

        conn.commit()
        conn.close()

        return org_count, member_count

    def _link_farm_cluster(self, cursor: sqlite3.Cursor, operator_wallet: str) -> Optional[int]:
        """
        Attempt to link organization to existing farm_cluster.

        SQL: SELECT cluster_id FROM farm_cluster_members
             WHERE wallet_address = ? LIMIT 1
        """
        cursor.execute("""
            SELECT cluster_id FROM farm_cluster_members
            WHERE wallet_address = ?
            LIMIT 1
        """, (operator_wallet,))

        row = cursor.fetchone()
        return row[0] if row else None

    def _compute_total_volume(self, subgraph: nx.DiGraph) -> float:
        """Sum total_amount on all SOL-transfer edges."""
        total = 0
        for source, dest, data in subgraph.edges(data=True):
            if data.get('edge_type') != 'token_creation':
                total += data.get('total_amount', 0)
        return total

    def _compute_member_volume(self, subgraph: nx.DiGraph, member_addr: str) -> float:
        """Sum total_amount on outgoing SOL-transfer edges for member."""
        total = 0
        for dest in subgraph.successors(member_addr):
            edge_type = subgraph[member_addr][dest].get('edge_type', 'transfer')
            if edge_type != 'token_creation':
                total += subgraph[member_addr][dest].get('total_amount', 0)
        return total

    def _compute_member_tokens(self, subgraph: nx.DiGraph, member_addr: str, node_type: str) -> int:
        """
        Count tokens launched by member.

        For 'wallet' and 'creator': count token nodes reachable via edges.
        For 'token': 0.
        """
        if node_type == 'token':
            return 0

        token_count = 0
        for succ in subgraph.successors(member_addr):
            if subgraph.nodes[succ].get('node_type') == 'token':
                token_count += 1

        return token_count
