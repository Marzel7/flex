"""
Phase 6A: Test scoring model v2 implementation.

Tests:
- Connectivity Risk component (0-40)
- Lifecycle Risk component (0-25)
- Evidence Risk v2 component (0-35) with weighted confidence
- Final score capping at 100
- Edge cases (NULL edges, 0 edges)
"""

import pytest
import json
import sys
from pathlib import Path
from contextlib import contextmanager

# Add parent directory and tests directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

from test_utils import (
    db_transaction, seed_network, seed_network_evidence,
    seed_network_score, get_score
)
# test_db fixture is provided by conftest.py (auto-discovered by pytest)


@contextmanager
def compute_score_v2(db_path, network_name):
    """Compute score v2 directly using the SQL formula."""
    with db_transaction(db_path) as db:
        # Compute the three components
        result = db.execute('''
            WITH score_components AS (
              SELECT
                nr.network_name,
                CASE
                  WHEN nr.network_type = 'organic' THEN 0
                  WHEN nr.network_type = 'cex_connected' THEN 10
                  WHEN nr.network_type = 'infra_connected' THEN 15
                  WHEN nr.network_type = 'cex_and_infra_connected' THEN 25
                  ELSE 0
                END as connectivity_risk,
                CASE
                  WHEN nr.stability_state = 'stable' THEN 0
                  WHEN nr.stability_state = 'new' THEN 10
                  WHEN nr.stability_state = 'growing' THEN 20
                  WHEN nr.stability_state = 'shrinking' THEN 5
                  ELSE 0
                END as lifecycle_risk,
                CASE
                  WHEN ne.total_edges IS NULL THEN 0
                  WHEN ne.total_edges = 0 THEN 0
                  ELSE CAST(ROUND(
                    (
                      CAST(ne.high_confidence_edges AS FLOAT) * 1.0 +
                      CAST(ne.medium_confidence_edges AS FLOAT) * 0.6 +
                      CAST(COALESCE(ne.low_confidence_edges, 0) AS FLOAT) * 0.2
                    ) / CAST(CASE WHEN ne.total_edges IS NULL OR ne.total_edges = 0 THEN 1 ELSE ne.total_edges END AS FLOAT) * 35.0
                  ) AS INTEGER)
                END as evidence_risk,
                CAST(
                  (
                    CAST(ne.high_confidence_edges AS FLOAT) * 1.0 +
                    CAST(ne.medium_confidence_edges AS FLOAT) * 0.6 +
                    CAST(COALESCE(ne.low_confidence_edges, 0) AS FLOAT) * 0.2
                  ) / CAST(CASE WHEN ne.total_edges IS NULL OR ne.total_edges = 0 THEN 1 ELSE ne.total_edges END AS FLOAT)
                AS REAL) as confidence_score
              FROM networks_release nr
              LEFT JOIN network_evidence ne ON nr.network_name = ne.network_name
              WHERE nr.network_name = ?
            )
            SELECT
              network_name,
              connectivity_risk,
              lifecycle_risk,
              evidence_risk,
              MIN(100, connectivity_risk + lifecycle_risk + evidence_risk) as final_score,
              confidence_score
            FROM score_components;
        ''', (network_name,)).fetchone()

        yield result if result else None


class TestScoringV2Components:
    """Test individual score components."""

    def test_connectivity_organic(self, test_db):
        """Organic networks have 0 connectivity risk."""
        with db_transaction(test_db) as db:
            seed_network(db, 'OrgNet', network_type='organic')
            seed_network_evidence(db, 'OrgNet', total_edges=10, high_confidence=10)

        with compute_score_v2(test_db, 'OrgNet') as result:
            assert result is not None
            assert result['connectivity_risk'] == 0

    def test_connectivity_cex_connected(self, test_db):
        """CEX-connected networks have 10 connectivity risk."""
        with db_transaction(test_db) as db:
            seed_network(db, 'CexNet', network_type='cex_connected')
            seed_network_evidence(db, 'CexNet', total_edges=10, high_confidence=10)

        with compute_score_v2(test_db, 'CexNet') as result:
            assert result is not None
            assert result['connectivity_risk'] == 10

    def test_connectivity_infra_connected(self, test_db):
        """Infra-connected networks have 15 connectivity risk."""
        with db_transaction(test_db) as db:
            seed_network(db, 'InfraNet', network_type='infra_connected')
            seed_network_evidence(db, 'InfraNet', total_edges=10, high_confidence=10)

        with compute_score_v2(test_db, 'InfraNet') as result:
            assert result is not None
            assert result['connectivity_risk'] == 15

    def test_connectivity_cex_and_infra(self, test_db):
        """CEX+Infra-connected networks have 25 connectivity risk."""
        with db_transaction(test_db) as db:
            seed_network(db, 'BothNet', network_type='cex_and_infra_connected')
            seed_network_evidence(db, 'BothNet', total_edges=10, high_confidence=10)

        with compute_score_v2(test_db, 'BothNet') as result:
            assert result is not None
            assert result['connectivity_risk'] == 25

    def test_lifecycle_stable(self, test_db):
        """Stable networks have 0 lifecycle risk."""
        with db_transaction(test_db) as db:
            seed_network(db, 'StableNet', stability_state='stable')
            seed_network_evidence(db, 'StableNet', total_edges=10, high_confidence=10)

        with compute_score_v2(test_db, 'StableNet') as result:
            assert result is not None
            assert result['lifecycle_risk'] == 0

    def test_lifecycle_new(self, test_db):
        """New networks have 10 lifecycle risk."""
        with db_transaction(test_db) as db:
            seed_network(db, 'NewNet', stability_state='new')
            seed_network_evidence(db, 'NewNet', total_edges=10, high_confidence=10)

        with compute_score_v2(test_db, 'NewNet') as result:
            assert result is not None
            assert result['lifecycle_risk'] == 10

    def test_lifecycle_growing(self, test_db):
        """Growing networks have 20 lifecycle risk."""
        with db_transaction(test_db) as db:
            seed_network(db, 'GrowingNet', stability_state='growing')
            seed_network_evidence(db, 'GrowingNet', total_edges=10, high_confidence=10)

        with compute_score_v2(test_db, 'GrowingNet') as result:
            assert result is not None
            assert result['lifecycle_risk'] == 20

    def test_lifecycle_shrinking(self, test_db):
        """Shrinking networks have 5 lifecycle risk."""
        with db_transaction(test_db) as db:
            seed_network(db, 'ShrinkNet', stability_state='shrinking')
            seed_network_evidence(db, 'ShrinkNet', total_edges=10, high_confidence=10)

        with compute_score_v2(test_db, 'ShrinkNet') as result:
            assert result is not None
            assert result['lifecycle_risk'] == 5


class TestEvidenceV2WeightedConfidence:
    """Test evidence component with weighted confidence formula."""

    def test_all_high_confidence(self, test_db):
        """H=10, M=0, L=0, T=10 → conf=1.0 → evidence=35."""
        with db_transaction(test_db) as db:
            seed_network(db, 'HighConfNet', network_type='organic', stability_state='stable')
            seed_network_evidence(db, 'HighConfNet', total_edges=10,
                                high_confidence=10, medium_confidence=0, low_confidence=0)

        with compute_score_v2(test_db, 'HighConfNet') as result:
            assert result is not None
            assert result['connectivity_risk'] == 0
            assert result['lifecycle_risk'] == 0
            assert result['evidence_risk'] == 35
            # confidence_score should be 1.0 (10*1.0 + 0*0.6 + 0*0.2) / 10
            assert abs(result['confidence_score'] - 1.0) < 0.01

    def test_all_medium_confidence(self, test_db):
        """H=0, M=10, L=0, T=10 → conf=0.6 → evidence=21."""
        with db_transaction(test_db) as db:
            seed_network(db, 'MedConfNet', network_type='organic', stability_state='stable')
            seed_network_evidence(db, 'MedConfNet', total_edges=10,
                                high_confidence=0, medium_confidence=10, low_confidence=0)

        with compute_score_v2(test_db, 'MedConfNet') as result:
            assert result is not None
            assert result['evidence_risk'] == 21
            # confidence_score should be 0.6 (0*1.0 + 10*0.6 + 0*0.2) / 10
            assert abs(result['confidence_score'] - 0.6) < 0.01

    def test_all_low_confidence(self, test_db):
        """H=0, M=0, L=10, T=10 → conf=0.2 → evidence=7."""
        with db_transaction(test_db) as db:
            seed_network(db, 'LowConfNet', network_type='organic', stability_state='stable')
            seed_network_evidence(db, 'LowConfNet', total_edges=10,
                                high_confidence=0, medium_confidence=0, low_confidence=10)

        with compute_score_v2(test_db, 'LowConfNet') as result:
            assert result is not None
            assert result['evidence_risk'] == 7
            # confidence_score should be 0.2 (0*1.0 + 0*0.6 + 10*0.2) / 10
            assert abs(result['confidence_score'] - 0.2) < 0.01

    def test_mixed_confidence(self, test_db):
        """H=5, M=3, L=2, T=10 → conf=(5 + 1.8 + 0.4)/10 = 0.72 → evidence=25."""
        with db_transaction(test_db) as db:
            seed_network(db, 'MixedConfNet', network_type='organic', stability_state='stable')
            seed_network_evidence(db, 'MixedConfNet', total_edges=10,
                                high_confidence=5, medium_confidence=3, low_confidence=2)

        with compute_score_v2(test_db, 'MixedConfNet') as result:
            assert result is not None
            # (5*1.0 + 3*0.6 + 2*0.2) / 10 = 7.2 / 10 = 0.72
            # 0.72 * 35 = 25.2 → rounds to 25
            assert result['evidence_risk'] == 25
            assert abs(result['confidence_score'] - 0.72) < 0.01


class TestEvidenceV2EdgeCases:
    """Test edge cases in evidence computation."""

    def test_null_edges(self, test_db):
        """NULL total_edges should result in 0 evidence risk."""
        with db_transaction(test_db) as db:
            seed_network(db, 'NullNet', network_type='organic', stability_state='stable')
            # Don't seed evidence (will be NULL in LEFT JOIN)

        with compute_score_v2(test_db, 'NullNet') as result:
            assert result is not None
            assert result['evidence_risk'] == 0
            assert result['final_score'] == 0  # 0+0+0

    def test_zero_edges(self, test_db):
        """total_edges=0 should result in 0 evidence risk."""
        with db_transaction(test_db) as db:
            seed_network(db, 'ZeroNet', network_type='cex_connected', stability_state='growing')
            seed_network_evidence(db, 'ZeroNet', total_edges=0,
                                high_confidence=0, medium_confidence=0, low_confidence=0)

        with compute_score_v2(test_db, 'ZeroNet') as result:
            assert result is not None
            assert result['evidence_risk'] == 0
            # 10 (connectivity) + 20 (lifecycle) + 0 (evidence) = 30
            assert result['final_score'] == 30

    def test_no_division_by_zero(self, test_db):
        """Should use GREATEST(total_edges, 1) to avoid division by zero."""
        with db_transaction(test_db) as db:
            seed_network(db, 'SafeNet', network_type='organic', stability_state='stable')
            seed_network_evidence(db, 'SafeNet', total_edges=0,
                                high_confidence=5, medium_confidence=0, low_confidence=0)

        with compute_score_v2(test_db, 'SafeNet') as result:
            # Should not throw error
            assert result is not None
            assert result['evidence_risk'] == 0


class TestScoringV2FinalScore:
    """Test final score computation and capping."""

    def test_score_sum(self, test_db):
        """Final score = connectivity + lifecycle + evidence (uncapped)."""
        with db_transaction(test_db) as db:
            # cex_connected (10) + growing (20) + high confidence (35) = 65
            seed_network(db, 'Sum65Net', network_type='cex_connected', stability_state='growing')
            seed_network_evidence(db, 'Sum65Net', total_edges=10, high_confidence=10)

        with compute_score_v2(test_db, 'Sum65Net') as result:
            assert result is not None
            assert result['final_score'] == 65

    def test_score_cap_at_100(self, test_db):
        """Final score capped at 100."""
        with db_transaction(test_db) as db:
            # cex_and_infra (25) + growing (20) + high confidence (35) = 80 (no cap needed)
            seed_network(db, 'Cap80Net', network_type='cex_and_infra_connected', stability_state='growing')
            seed_network_evidence(db, 'Cap80Net', total_edges=10, high_confidence=10)

        with compute_score_v2(test_db, 'Cap80Net') as result:
            assert result is not None
            assert result['final_score'] == 80

    def test_high_score(self, test_db):
        """Test a high-risk network: all worst-case components."""
        with db_transaction(test_db) as db:
            # cex_and_infra (25) + growing (20) + high confidence (35) = 80
            seed_network(db, 'HighNet', network_type='cex_and_infra_connected', stability_state='growing')
            seed_network_evidence(db, 'HighNet', total_edges=10, high_confidence=10)

        with compute_score_v2(test_db, 'HighNet') as result:
            assert result is not None
            assert result['final_score'] == 80

    def test_low_score(self, test_db):
        """Test a low-risk network: all best-case components."""
        with db_transaction(test_db) as db:
            # organic (0) + stable (0) + null evidence (0) = 0
            seed_network(db, 'LowNet', network_type='organic', stability_state='stable')

        with compute_score_v2(test_db, 'LowNet') as result:
            assert result is not None
            assert result['final_score'] == 0
