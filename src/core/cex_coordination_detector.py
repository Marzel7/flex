#!/usr/bin/env python3
"""
CEX-Masked Coordination Detector

Standard coordination detection is blind to operators who fund through CEX hot
wallets, because those wallets are excluded from the shared-funder graph.

This detector finds coordination by looking at behavioural patterns across
creators who share the same CEX funder:
  - Identical or near-identical funding amounts (within 5%)
  - Strict timing cadence (funded on different days, but regular intervals)
  - High G7 rate across the group
  - Creators activated within a short window of each other

Results are written to cex_coordinated_groups and edges into
coordinated_creator_edges with bridge_funder = CEX address and a
cex_masked=1 flag.
"""

import logging
import sqlite3
import time
from collections import defaultdict
from itertools import combinations
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Tuning parameters
MIN_GROUP_SIZE          = 3       # min creators sharing a CEX funder to analyse
MIN_AMOUNT_SIMILARITY   = 0.90    # amounts must be within 10% of each other
MAX_DISTINCT_AMOUNTS    = 4       # if funder uses >4 distinct round amounts → not scripted
CADENCE_MAX_STD_DAYS    = 3.0     # std deviation of inter-funding gaps (days)
MIN_CONFIDENCE          = 0.30    # minimum edge confidence to write
HIGH_G7_THRESHOLD       = 60.0    # g7_percentage column is 0-100 scale


class CexCoordinationDetector:

    def __init__(self, db_path: str):
        self.db_path = db_path

    def detect_and_store(self) -> dict:
        started = time.time()
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        conn.row_factory = sqlite3.Row

        try:
            self._ensure_tables(conn)
            groups = self._find_candidate_groups(conn)
            logger.info(f"[CEX-COORD] {len(groups)} CEX funders with {MIN_GROUP_SIZE}+ creators")

            edges_written = 0
            groups_flagged = 0

            for funder_address, creators in groups.items():
                result = self._analyse_group(conn, funder_address, creators)
                if result:
                    edges_written += self._write_edges(conn, funder_address, result)
                    groups_flagged += 1

            conn.commit()
            duration = round(time.time() - started, 2)
            logger.info(f"[CEX-COORD] Done: {groups_flagged} groups, {edges_written} edges, {duration}s")
            return {
                'status': 'success',
                'groups_flagged': groups_flagged,
                'edges_written': edges_written,
                'duration_seconds': duration,
            }

        except Exception as e:
            logger.exception("[CEX-COORD] Failed")
            return {'status': 'failed', 'error': str(e)}
        finally:
            conn.close()

    # ── Table setup ────────────────────────────────────────────────────────────

    def _ensure_tables(self, conn: sqlite3.Connection) -> None:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS cex_coordinated_groups (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                cex_funder       TEXT NOT NULL,
                cex_label        TEXT,
                creator_count    INTEGER NOT NULL,
                confidence       REAL NOT NULL,
                signals          TEXT,
                detected_at      REAL NOT NULL DEFAULT (unixepoch()),
                UNIQUE(cex_funder)
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_cex_coord_funder
            ON cex_coordinated_groups(cex_funder)
        """)
        # Add cex_masked column to coordinated_creator_edges if missing
        try:
            conn.execute("ALTER TABLE coordinated_creator_edges ADD COLUMN cex_masked INTEGER DEFAULT 0")
        except Exception:
            pass  # already exists
        conn.commit()

    # ── Candidate groups ───────────────────────────────────────────────────────

    def _find_candidate_groups(self, conn: sqlite3.Connection) -> Dict[str, List[dict]]:
        """Return CEX funders with MIN_GROUP_SIZE+ non-trivial creator relationships."""
        rows = conn.execute("""
            SELECT cf.funder_address, iw.label as cex_label,
                   cf.creator_address, cf.amount_sol, cf.first_detected_at
            FROM creator_funders cf
            JOIN infra_wallets iw ON iw.address = cf.funder_address AND iw.type = 'cex'
            WHERE cf.is_cex = 1
              AND cf.amount_sol >= 1.0
        """).fetchall()

        groups: Dict[str, List[dict]] = defaultdict(list)
        for row in rows:
            groups[row['funder_address']].append({
                'creator': row['creator_address'],
                'amount_sol': row['amount_sol'],
                'first_detected_at': row['first_detected_at'],
                'cex_label': row['cex_label'],
            })

        return {
            funder: creators
            for funder, creators in groups.items()
            if len(creators) >= MIN_GROUP_SIZE
        }

    # ── Group analysis ─────────────────────────────────────────────────────────

    def _analyse_group(self, conn: sqlite3.Connection, funder: str,
                       creators: List[dict]) -> Optional[dict]:
        """
        Score a group of CEX-funded creators for coordination signals.
        Returns a result dict if confidence >= MIN_CONFIDENCE, else None.
        """
        signals = []
        confidence = 0.0

        amounts = [c['amount_sol'] for c in creators]

        # ── Signal 1: Amount clustering ────────────────────────────────────────
        amount_score, amount_signal = self._score_amounts(amounts)
        if amount_score > 0:
            confidence += amount_score
            signals.append(amount_signal)

        # ── Signal 2: Timing cadence ───────────────────────────────────────────
        cadence_score, cadence_signal = self._score_cadence(creators)
        if cadence_score > 0:
            confidence += cadence_score
            signals.append(cadence_signal)

        # ── Signal 3: Creator risk profile ────────────────────────────────────
        risk_score, risk_signal = self._score_risk_profile(conn, creators)
        if risk_score > 0:
            confidence += risk_score
            signals.append(risk_signal)

        # ── Signal 4: G7 rate across group ────────────────────────────────────
        g7_score, g7_signal = self._score_g7_rate(conn, creators)
        if g7_score > 0:
            confidence += g7_score
            signals.append(g7_signal)

        confidence = min(1.0, confidence)

        if confidence < MIN_CONFIDENCE:
            return None

        return {
            'creators': [c['creator'] for c in creators],
            'confidence': round(confidence, 3),
            'signals': signals,
            'cex_label': creators[0].get('cex_label', ''),
        }

    def _score_amounts(self, amounts: List[float]) -> Tuple[float, str]:
        if len(amounts) < 2:
            return 0.0, ''

        # Check how many distinct round amounts exist
        round_amounts = set(round(a / 5) * 5 for a in amounts)  # round to nearest 5 SOL
        distinct = len(round_amounts)

        if distinct > MAX_DISTINCT_AMOUNTS:
            return 0.0, ''

        # Check if amounts cluster tightly
        avg = sum(amounts) / len(amounts)
        if avg == 0:
            return 0.0, ''

        within_10pct = sum(1 for a in amounts if abs(a - avg) / avg <= 0.10)
        similarity = within_10pct / len(amounts)

        if similarity >= MIN_AMOUNT_SIMILARITY:
            score = 0.25 + (0.15 * (1 - distinct / MAX_DISTINCT_AMOUNTS))
            return score, f"{within_10pct}/{len(amounts)} creators funded {avg:.0f}±10% SOL ({distinct} distinct amounts)"

        # Exact repeated amounts (e.g. exactly 40 or 70)
        from collections import Counter
        amount_counts = Counter(round(a) for a in amounts)
        most_common_count = amount_counts.most_common(1)[0][1]
        if most_common_count >= 3:
            most_common_val = amount_counts.most_common(1)[0][0]
            return 0.20, f"{most_common_count} creators funded exactly {most_common_val} SOL"

        return 0.0, ''

    def _score_cadence(self, creators: List[dict]) -> Tuple[float, str]:
        """Detect strict daily/weekly rotation pattern."""
        timestamps = []
        for c in creators:
            ts = c.get('first_detected_at')
            if not ts:
                continue
            try:
                if isinstance(ts, str):
                    import datetime
                    dt = datetime.datetime.fromisoformat(ts.replace('Z', '+00:00'))
                    timestamps.append(dt.timestamp())
                else:
                    timestamps.append(float(ts))
            except Exception:
                continue

        if len(timestamps) < 3:
            return 0.0, ''

        timestamps.sort()
        gaps_days = [(timestamps[i+1] - timestamps[i]) / 86400
                     for i in range(len(timestamps) - 1)]

        avg_gap = sum(gaps_days) / len(gaps_days)
        if avg_gap == 0:
            return 0.0, ''

        variance = sum((g - avg_gap) ** 2 for g in gaps_days) / len(gaps_days)
        std_days = variance ** 0.5

        if std_days <= CADENCE_MAX_STD_DAYS and 0.5 <= avg_gap <= 14:
            score = 0.25 * max(0, 1 - std_days / CADENCE_MAX_STD_DAYS)
            return score, f"Strict {avg_gap:.1f}d rotation cadence (σ={std_days:.1f}d)"

        return 0.0, ''

    def _score_risk_profile(self, conn: sqlite3.Connection,
                            creators: List[dict]) -> Tuple[float, str]:
        creator_addrs = [c['creator'] for c in creators]
        placeholders = ','.join('?' * len(creator_addrs))
        rows = conn.execute(f"""
            SELECT risk_level, COUNT(*) as cnt
            FROM creator_risk_scores
            WHERE creator_address IN ({placeholders})
            GROUP BY risk_level
        """, creator_addrs).fetchall()

        total = sum(r['cnt'] for r in rows)
        if total == 0:
            return 0.0, ''

        high_critical = sum(r['cnt'] for r in rows if r['risk_level'] in ('HIGH', 'CRITICAL'))
        frac = high_critical / total

        if frac >= 0.5:
            return 0.20, f"{high_critical}/{total} creators HIGH/CRITICAL risk"
        if frac >= 0.25:
            return 0.10, f"{high_critical}/{total} creators HIGH/CRITICAL risk"

        return 0.0, ''

    def _score_g7_rate(self, conn: sqlite3.Connection,
                       creators: List[dict]) -> Tuple[float, str]:
        creator_addrs = [c['creator'] for c in creators]
        placeholders = ','.join('?' * len(creator_addrs))
        rows = conn.execute(f"""
            SELECT AVG(g7_percentage) as avg_g7, COUNT(*) as cnt
            FROM creator_risk_scores
            WHERE creator_address IN ({placeholders})
              AND g7_percentage IS NOT NULL
        """, creator_addrs).fetchone()

        if not rows or not rows['avg_g7']:
            return 0.0, ''

        avg_g7 = rows['avg_g7']
        if avg_g7 >= HIGH_G7_THRESHOLD:
            return 0.15, f"Avg G7 rate {avg_g7:.0f}% across group"

        return 0.0, ''

    # ── Write edges ────────────────────────────────────────────────────────────

    def _write_edges(self, conn: sqlite3.Connection, funder: str, result: dict) -> int:
        creators = result['creators']
        confidence = result['confidence']
        signals_str = '; '.join(result['signals'])
        cex_label = result['cex_label']

        # Upsert group record
        conn.execute("""
            INSERT INTO cex_coordinated_groups
                (cex_funder, cex_label, creator_count, confidence, signals, detected_at)
            VALUES (?, ?, ?, ?, ?, unixepoch())
            ON CONFLICT(cex_funder) DO UPDATE SET
                cex_label=excluded.cex_label,
                creator_count=excluded.creator_count,
                confidence=excluded.confidence,
                signals=excluded.signals,
                detected_at=excluded.detected_at
        """, (funder, cex_label, len(creators), confidence, signals_str))

        # Write pairwise edges
        edges = 0
        for a, b in combinations(sorted(creators), 2):
            conn.execute("""
                INSERT OR REPLACE INTO coordinated_creator_edges
                    (creator_a, creator_b, bridge_funder, confidence, cex_masked)
                VALUES (?, ?, ?, ?, 1)
            """, (a, b, funder, confidence))
            edges += 1

        logger.info(
            f"[CEX-COORD] {cex_label or funder[:12]} → "
            f"{len(creators)} creators, conf={confidence:.2f}, {edges} edges | {signals_str}"
        )
        return edges


def run(db_path: str) -> dict:
    return CexCoordinationDetector(db_path).detect_and_store()
