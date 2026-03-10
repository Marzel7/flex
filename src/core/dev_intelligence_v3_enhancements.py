"""
FLEX Dev Intelligence V3 Enhancements — Behavioral Modeling Signals

Adds three powerful signals to v3 launch predictions:
1. Organization Momentum Score — activity acceleration trends
2. Launch Cadence Model — launch interval prediction
3. Organization Expansion Detection — new creator tracking

These are fully rules-based, extracted from snapshots and token_analysis.
Compatible with existing v3 (additive only, no breaking changes).
"""

import sqlite3
import json
import logging
from typing import Dict, List, Tuple
from statistics import mean, stdev
from collections import defaultdict

logger = logging.getLogger(__name__)


class OrganizationMomentumTracker:
    """Tracks activity acceleration and momentum trends."""

    def __init__(self, db_path: str):
        self.db_path = db_path

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=60)
        conn.execute('PRAGMA journal_mode=WAL')
        conn.row_factory = sqlite3.Row
        return conn

    def compute_momentum(self, org_id: int, cursor: sqlite3.Cursor) -> Dict:
        """
        Compute momentum score from activity trends.

        Returns:
        {
            'activity_24h': int,
            'activity_7d_avg': float,
            'momentum': float (-1 to 1, negative=decay, positive=acceleration),
            'momentum_signal': float (0-100 normalized),
            'trend': str ('accelerating'|'stable'|'decelerating')
        }
        """
        # Get last 7 days of snapshots
        cursor.execute("""
            SELECT snapshot_date, active_funders, burst_count, weighted_volume
            FROM org_snapshots
            WHERE organization_id = ?
            ORDER BY snapshot_date DESC
            LIMIT 7
        """, (org_id,))

        snapshots = [dict(row) for row in cursor.fetchall()]
        if len(snapshots) < 2:
            return {
                'activity_24h': 0,
                'activity_7d_avg': 0,
                'momentum': 0,
                'momentum_signal': 0,
                'trend': 'stable'
            }

        # Most recent (24h)
        activity_24h = snapshots[0]['active_funders'] + snapshots[0]['burst_count']

        # Average of previous 6 days
        if len(snapshots) > 1:
            prev_activities = [
                s['active_funders'] + s['burst_count']
                for s in snapshots[1:7]
            ]
            activity_7d_avg = mean(prev_activities) if prev_activities else activity_24h
        else:
            activity_7d_avg = activity_24h

        # Momentum: (today - avg) / avg
        if activity_7d_avg > 0:
            momentum = (activity_24h - activity_7d_avg) / activity_7d_avg
        else:
            momentum = 0

        # Normalize to 0-100 signal
        # momentum of +0.5 (50% increase) = 50 signal
        # momentum of -0.5 (50% decrease) = -50 signal
        momentum_signal = min(100, max(-100, momentum * 100))

        # Trend classification
        if momentum > 0.2:
            trend = 'accelerating'
        elif momentum < -0.2:
            trend = 'decelerating'
        else:
            trend = 'stable'

        return {
            'activity_24h': int(activity_24h),
            'activity_7d_avg': float(activity_7d_avg),
            'momentum': float(momentum),
            'momentum_signal': float(momentum_signal),
            'trend': trend,
        }


class LaunchCadenceDetector:
    """Detects launch patterns and predicts next launch timing."""

    def __init__(self, db_path: str):
        self.db_path = db_path

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=60)
        conn.execute('PRAGMA journal_mode=WAL')
        conn.row_factory = sqlite3.Row
        return conn

    def analyze_cadence(self, org_id: int, cursor: sqlite3.Cursor) -> Dict:
        """
        Analyze org's launch cadence and predict next launch.

        Returns:
        {
            'launches_detected': int,
            'launch_dates': [list of dates],
            'intervals': [list of days between launches],
            'average_interval': float,
            'interval_variability': float (0-1, higher=less predictable),
            'days_since_last_launch': int,
            'cadence_score': float (0-100),
            'due_for_launch': bool,
            'prediction_confidence': float (0-1)
        }
        """
        # Get all launches by org (from token_analysis where org creators = earliest_tx_creator)
        cursor.execute("""
            SELECT ta.created_at, COUNT(*) as count
            FROM token_analysis ta
            JOIN dev_organization_members dom ON ta.earliest_tx_creator = dom.member_address
            WHERE dom.organization_id = ?
              AND dom.member_type = 'creator'
              AND ta.created_at IS NOT NULL
            GROUP BY DATE(ta.created_at)
            ORDER BY DATE(ta.created_at) DESC
            LIMIT 20
        """, (org_id,))

        launch_rows = cursor.fetchall()
        if not launch_rows or len(launch_rows) < 2:
            return {
                'launches_detected': 0,
                'launch_dates': [],
                'intervals': [],
                'average_interval': 0,
                'interval_variability': 0,
                'days_since_last_launch': 999,
                'cadence_score': 0,
                'due_for_launch': False,
                'prediction_confidence': 0,
            }

        # Extract dates
        import datetime
        launch_dates = []
        for row in launch_rows:
            try:
                date_str = row[0].split(' ')[0] if isinstance(row[0], str) else str(row[0])
                launch_dates.append(date_str)
            except:
                continue

        if len(launch_dates) < 2:
            return {
                'launches_detected': len(launch_dates),
                'launch_dates': launch_dates,
                'intervals': [],
                'average_interval': 0,
                'interval_variability': 0,
                'days_since_last_launch': 999,
                'cadence_score': 0,
                'due_for_launch': False,
                'prediction_confidence': 0,
            }

        # Compute intervals between launches
        intervals = []
        for i in range(len(launch_dates) - 1):
            try:
                date1 = datetime.datetime.strptime(launch_dates[i], '%Y-%m-%d')
                date2 = datetime.datetime.strptime(launch_dates[i+1], '%Y-%m-%d')
                interval_days = (date1 - date2).days
                if interval_days > 0:
                    intervals.append(interval_days)
            except:
                continue

        if not intervals:
            return {
                'launches_detected': len(launch_dates),
                'launch_dates': launch_dates,
                'intervals': [],
                'average_interval': 0,
                'interval_variability': 0,
                'days_since_last_launch': 999,
                'cadence_score': 0,
                'due_for_launch': False,
                'prediction_confidence': 0,
            }

        # Statistics
        avg_interval = mean(intervals)
        if len(intervals) > 1:
            interval_std = stdev(intervals)
            variability = min(1.0, interval_std / max(avg_interval, 1))
        else:
            variability = 0.5

        # Days since last launch
        try:
            import datetime
            last_launch = datetime.datetime.strptime(launch_dates[0], '%Y-%m-%d')
            now = datetime.datetime.now()
            days_since = (now - last_launch).days
        except:
            days_since = 999

        # Cadence score: 0-100 based on how "due" the org is
        # At average_interval days, cadence_score = 50
        # At 1.5x average, cadence_score = 75
        # At 2x average, cadence_score = 90
        if avg_interval > 0:
            due_ratio = days_since / avg_interval
            cadence_score = min(100, 50 + (due_ratio - 1) * 25)
        else:
            cadence_score = 0

        # Due for launch: if past average interval or high variability suggests imminent
        due_for_launch = days_since >= avg_interval and avg_interval > 0

        # Confidence: lower variability = higher confidence
        prediction_confidence = 1.0 - variability

        return {
            'launches_detected': len(launch_dates),
            'launch_dates': launch_dates,
            'intervals': intervals,
            'average_interval': float(avg_interval),
            'interval_variability': float(variability),
            'days_since_last_launch': int(days_since),
            'cadence_score': float(cadence_score),
            'due_for_launch': bool(due_for_launch),
            'prediction_confidence': float(prediction_confidence),
        }


class OrganizationExpansionDetector:
    """Detects new creator additions and team expansion."""

    def __init__(self, db_path: str):
        self.db_path = db_path

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=60)
        conn.execute('PRAGMA journal_mode=WAL')
        conn.row_factory = sqlite3.Row
        return conn

    def detect_expansion(self, org_id: int, cursor: sqlite3.Cursor) -> Dict:
        """
        Detect new creator additions and team expansion signals.

        Returns:
        {
            'current_creator_count': int,
            'creators_added_24h': int,
            'creators_added_7d': int,
            'expansion_rate': float (new creators / total),
            'expansion_score': float (0-100),
            'expansion_signal': str ('rapid'|'normal'|'stable'|'shrinking'),
            'new_creators': [list of new creator wallets],
            'first_activity_creators': [creators who got funded in last 7d],
            'team_size_change_7d': int
        }
        """
        # Get current creators from org
        cursor.execute("""
            SELECT creator_list FROM dev_organizations
            WHERE organization_id = ?
        """, (org_id,))

        org_row = cursor.fetchone()
        if not org_row:
            return {
                'current_creator_count': 0,
                'creators_added_24h': 0,
                'creators_added_7d': 0,
                'expansion_rate': 0,
                'expansion_score': 0,
                'expansion_signal': 'stable',
                'new_creators': [],
                'first_activity_creators': [],
                'team_size_change_7d': 0,
            }

        current_creators = set(json.loads(org_row[0]) if org_row[0] else [])
        current_count = len(current_creators)

        # Get creators who had first transfer in last 24h
        cursor.execute("""
            SELECT DISTINCT destination FROM transfer_index
            WHERE destination IN ({})
              AND block_time >= (strftime('%s', 'now') - 86400)
            ORDER BY block_time DESC
        """.format(','.join('?' * len(current_creators)) if current_creators else 'NULL'),
            list(current_creators) if current_creators else [])

        creators_24h = {row[0] for row in cursor.fetchall()}

        # Get creators who had activity in last 7d
        cursor.execute("""
            SELECT DISTINCT destination FROM transfer_index
            WHERE destination IN ({})
              AND block_time >= (strftime('%s', 'now') - 604800)
            ORDER BY block_time DESC
        """.format(','.join('?' * len(current_creators)) if current_creators else 'NULL'),
            list(current_creators) if current_creators else [])

        creators_7d = {row[0] for row in cursor.fetchall()}

        # Get creator count from 7 days ago (approximated from snapshots)
        cursor.execute("""
            SELECT creator_count FROM dev_organizations
            WHERE organization_id = ?
        """, (org_id,))

        org_current = cursor.fetchone()
        team_size_change_7d = 0
        if org_current:
            # This is current; we'd need historical snapshot for exact change
            # For now, estimate from activity spike
            team_size_change_7d = len(creators_7d) - (current_count - len(creators_7d))

        # Expansion rate
        if current_count > 0:
            new_to_total = len(creators_7d) / current_count
            expansion_rate = new_to_total
        else:
            expansion_rate = 0

        # Expansion score: 0-100
        # 5+ new creators in 7d = 80 score
        # 2-4 = 50 score
        # 1 = 25 score
        if len(creators_7d) >= 5:
            expansion_score = min(100, 80 + (len(creators_7d) - 5) * 5)
            signal = 'rapid'
        elif len(creators_7d) >= 2:
            expansion_score = 50
            signal = 'normal'
        elif len(creators_7d) == 1:
            expansion_score = 25
            signal = 'stable'
        else:
            expansion_score = 0
            signal = 'stable'

        # Detect shrinking
        if team_size_change_7d < 0:
            signal = 'shrinking'
            expansion_score = -20

        return {
            'current_creator_count': int(current_count),
            'creators_added_24h': int(len(creators_24h)),
            'creators_added_7d': int(len(creators_7d)),
            'expansion_rate': float(expansion_rate),
            'expansion_score': float(expansion_score),
            'expansion_signal': signal,
            'new_creators': list(creators_7d)[:10],  # Top 10
            'first_activity_creators': list(creators_24h)[:5],  # Top 5 from 24h
            'team_size_change_7d': int(team_size_change_7d),
        }


class EnhancedLaunchScoreCalculator:
    """Combines v3 base signals with behavioral enhancements."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.momentum_tracker = OrganizationMomentumTracker(db_path)
        self.cadence_detector = LaunchCadenceDetector(db_path)
        self.expansion_detector = OrganizationExpansionDetector(db_path)

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=60)
        conn.execute('PRAGMA journal_mode=WAL')
        conn.row_factory = sqlite3.Row
        return conn

    def compute_enhanced_score(self, org_id: int, base_launch_prob_24h: float,
                               cursor: sqlite3.Cursor) -> Dict:
        """
        Compute enhanced launch score combining v3 base signals with behaviors.

        Enhanced Score Formula:
        enhanced_24h =
            0.40 * base_launch_prob_24h (activity)
          + 0.20 * momentum_signal (acceleration)
          + 0.15 * cadence_signal (timing)
          + 0.15 * expansion_signal (team growth)
          + 0.10 * confidence_boost (data quality)
        """
        # Get behavioral signals
        momentum = self.momentum_tracker.compute_momentum(org_id, cursor)
        cadence = self.cadence_detector.analyze_cadence(org_id, cursor)
        expansion = self.expansion_detector.detect_expansion(org_id, cursor)

        # Normalize to 0-100 where applicable
        momentum_norm = min(100, max(0, momentum['momentum_signal'] + 50))  # -100 to 100 → 0 to 100
        cadence_norm = cadence['cadence_score']  # already 0-100
        expansion_norm = max(0, expansion['expansion_score'])  # 0-100

        # Confidence from data completeness
        has_snapshots = momentum['activity_7d_avg'] > 0
        has_cadence = cadence['launches_detected'] >= 2
        has_expansion = expansion['current_creator_count'] > 0
        data_quality_score = (has_snapshots + has_cadence + has_expansion) * 33.33

        # Combined score
        enhanced_score = (
            base_launch_prob_24h * 0.40 +
            momentum_norm * 0.20 +
            cadence_norm * 0.15 +
            expansion_norm * 0.15 +
            data_quality_score * 0.10
        )

        return {
            'base_launch_prob': float(base_launch_prob_24h),
            'enhanced_launch_prob': float(min(100, enhanced_score)),
            'components': {
                'activity': float(base_launch_prob_24h),
                'momentum': float(momentum_norm),
                'cadence': float(cadence_norm),
                'expansion': float(expansion_norm),
                'data_quality': float(data_quality_score),
            },
            'momentum': momentum,
            'cadence': cadence,
            'expansion': expansion,
            'enhancement_factor': float(enhanced_score / max(base_launch_prob_24h, 1)),
        }


# Integration function for v3 engine
def enhance_org_launch_window(org_id: int, window_data: Dict, cursor: sqlite3.Cursor,
                              db_path: str) -> Dict:
    """
    Enhances existing org_launch_windows record with behavioral signals.
    Call after storing base launch_windows to add behavior data.

    Usage in DevIntelligenceV3Engine.detect_and_store():
        window_data = self._store_launch_windows(...)
        enhanced = enhance_org_launch_window(org_id, window_data, cursor, db_path)
    """
    calculator = EnhancedLaunchScoreCalculator(db_path)
    enhanced = calculator.compute_enhanced_score(org_id, window_data.get('prob_launch_24h', 0), cursor)

    # Store enhancement data in a new table or columns (v3.1 upgrade)
    # For now, return enriched dict
    return {
        **window_data,
        'enhanced_prob_launch_24h': enhanced['enhanced_launch_prob'],
        'momentum_signal': enhanced['momentum']['momentum_signal'],
        'cadence_score': enhanced['cadence']['cadence_score'],
        'expansion_score': enhanced['expansion']['expansion_score'],
        'enhancement_data': enhanced,
    }
