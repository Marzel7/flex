"""
Token Behaviour Monitoring - Forward-looking classification

Classifies tokens as they accumulate historical price snapshots.
Designed to run periodically (e.g., every 5-10 minutes) to classify
recently updated tokens without re-scanning the entire dataset.

The classification tracks behaviour from the moment we start monitoring.
No backfill — only forward-looking from this point onward.
"""

import logging
import sqlite3
import time
from typing import Optional

logger = logging.getLogger(__name__)


def classify_recent_tokens(
    db_path: str,
    min_age_secs: int = 300,  # Token must be at least 5 minutes old
    batch_size: int = 50,
    dry_run: bool = False,
) -> dict:
    """
    Classify tokens that have been updated since last classification run.

    Only classifies tokens with:
    - At least 8 price snapshots
    - At least 5 minutes (300 secs) of history
    - Not yet classified OR classified more than 10 minutes ago

    Args:
        db_path: Path to database
        min_age_secs: Minimum age of token before first classification (default 300 = 5 min)
        batch_size: How many to process per run
        dry_run: If True, don't write to database

    Returns:
        {"classified": N, "by_category": {...}, "errors": [...]}
    """
    from src.core.token_behavior import classify_mint

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    try:
        now = int(time.time())

        # Find candidates: tokens with enough snapshots and age
        # that haven't been classified recently
        query = """
            SELECT DISTINCT p.mint
            FROM token_price_snapshots p
            WHERE (
                -- Either not yet classified
                NOT EXISTS (
                    SELECT 1 FROM token_behavior b WHERE b.mint = p.mint
                )
                -- Or classified more than 10 minutes ago
                OR EXISTS (
                    SELECT 1 FROM token_behavior b
                    WHERE b.mint = p.mint
                    AND b.classified_at < ?
                )
            )
            -- Token must have minimum age
            AND (? - MIN(p.captured_at)) >= ?
            -- Token must have enough snapshots
            AND COUNT(*) >= 8
            GROUP BY p.mint
            LIMIT ?
        """

        candidates = cursor.execute(
            query,
            (now - 600, now, min_age_secs, batch_size)  # 10 min = 600 secs
        ).fetchall()

        logger.info(f"[TOKEN_BEHAVIOUR] Found {len(candidates)} candidates for classification")

        results = {
            'classified': 0,
            'by_category': {},
            'errors': [],
        }

        for idx, row in enumerate(candidates, 1):
            mint = row['mint']
            try:
                # Classify the token (skips upsert if dry_run)
                category, confidence = classify_mint(
                    mint, db_path, skip_upsert=dry_run
                )

                if category not in results['by_category']:
                    results['by_category'][category] = 0
                results['by_category'][category] += 1
                results['classified'] += 1

                if idx % 10 == 0:
                    logger.info(f"[TOKEN_BEHAVIOUR] Classified {idx}/{len(candidates)} tokens")

            except Exception as e:
                error_msg = f"{mint}: {str(e)}"
                results['errors'].append(error_msg)
                logger.error(f"[TOKEN_BEHAVIOUR] Error classifying {mint}: {e}")

        return results

    finally:
        conn.close()


def get_behaviour_summary(db_path: str) -> dict:
    """
    Get summary of classified tokens by category.

    Returns: {
        "total_classified": N,
        "by_category": {
            "immediate_rug": {"count": N, "avg_confidence": X},
            ...
        },
        "last_update": timestamp
    }
    """
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("""
            SELECT
                category,
                COUNT(*) as count,
                ROUND(AVG(confidence), 3) as avg_confidence,
                MAX(classified_at) as last_updated
            FROM token_behavior
            GROUP BY category
            ORDER BY count DESC
        """).fetchall()

        total = sum(row[1] for row in rows)

        summary = {
            'total_classified': total,
            'by_category': {},
            'last_updated': int(time.time()),
        }

        for row in rows:
            cat, count, avg_conf, last_ts = row
            summary['by_category'][cat] = {
                'count': count,
                'avg_confidence': avg_conf,
                'pct': round(100.0 * count / total, 1) if total > 0 else 0,
                'last_updated': last_ts,
            }

        return summary

    finally:
        conn.close()


def get_category_tokens(
    db_path: str,
    category: str,
    min_confidence: float = 0.0,
    limit: int = 50,
) -> list:
    """
    Get all tokens in a category, sorted by confidence.

    Args:
        db_path: Path to database
        category: Filter category (e.g., 'immediate_rug')
        min_confidence: Minimum confidence threshold
        limit: Max results to return

    Returns:
        List of token dicts with classification details
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("""
            SELECT
                mint,
                category,
                confidence,
                max_return_multiple,
                drawdown_from_peak,
                snapshot_count,
                lifetime_secs,
                classified_at
            FROM token_behavior
            WHERE category = ? AND confidence >= ?
            ORDER BY confidence DESC
            LIMIT ?
        """, (category, min_confidence, limit)).fetchall()

        return [
            {
                'mint': row['mint'],
                'category': row['category'],
                'confidence': round(row['confidence'], 3),
                'max_return_multiple': row['max_return_multiple'],
                'drawdown_from_peak': round(row['drawdown_from_peak'], 3) if row['drawdown_from_peak'] else None,
                'snapshot_count': row['snapshot_count'],
                'lifetime_secs': row['lifetime_secs'],
                'classified_at': row['classified_at'],
            }
            for row in rows
        ]

    finally:
        conn.close()


def run_periodic_monitor(db_path: str, interval_secs: int = 600):
    """
    Run token behaviour classification periodically.

    Designed to be called from a scheduler or background worker.

    Args:
        db_path: Path to database
        interval_secs: How long to wait between runs (default 10 min)
    """
    logger.info(f"[TOKEN_BEHAVIOUR] Starting periodic monitor (interval: {interval_secs}s)")

    while True:
        try:
            start_time = time.time()
            results = classify_recent_tokens(db_path)
            elapsed = time.time() - start_time

            logger.info(
                f"[TOKEN_BEHAVIOUR] Classification cycle complete: "
                f"{results['classified']} classified in {elapsed:.1f}s"
            )

            if results['by_category']:
                summary = ", ".join(
                    f"{cat}={count}"
                    for cat, count in sorted(results['by_category'].items())
                )
                logger.info(f"[TOKEN_BEHAVIOUR] Distribution: {summary}")

            if results['errors']:
                logger.warning(f"[TOKEN_BEHAVIOUR] {len(results['errors'])} errors during classification")

        except Exception as e:
            logger.error(f"[TOKEN_BEHAVIOUR] Monitor error: {e}", exc_info=True)

        # Sleep until next interval
        time.sleep(interval_secs)


if __name__ == '__main__':
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format='[%(asctime)s] %(name)s: %(message)s'
    )

    db_path = sys.argv[1] if len(sys.argv) > 1 else 'database/flex_complete_database.db'

    # Run once then exit
    logger.info(f"Running one-time classification from {db_path}")
    results = classify_recent_tokens(db_path)
    logger.info(f"Results: {results}")
