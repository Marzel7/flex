"""
WATCHTOWER table creation and static seed data.

Extracted from src/core/main.py so that standalone scripts can initialise
the WATCHTOWER schema without importing Flask or starting background threads.

main.py imports ensure_watchtower_tables and seed_wallet_tiers from here.
scripts/ensure_watchtower_tables.py does too.

The _WT_INFRA_ROLES dict lives in main.py (used by ~50 other functions).
Pass it explicitly to seed_wallet_tiers().
"""
import os
import sqlite3

from src.utils.db_locking import db_connect

_REPO_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "../.."))
_DEFAULT_DB_PATH = os.path.join(_REPO_ROOT, "database", "flex_complete_database.db")

DB_PATH = os.path.abspath(os.environ.get("DB_PATH", _DEFAULT_DB_PATH))

# Sentinel table: if this exists the main table-creation block has run.
_SENTINEL_TABLE = "wt_identity_proposals"


def _tables_already_exist(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (_SENTINEL_TABLE,)
    ).fetchone()
    return row is not None


def ensure_watchtower_tables(conn: sqlite3.Connection) -> None:
    """Create all WATCHTOWER tables and indexes using individual execute calls.

    Deliberately avoids executescript() — that method issues an implicit COMMIT
    and holds a broader write lock for the duration of the entire script, causing
    lock contention when called on every webhook hit. Individual CREATE IF NOT EXISTS
    statements acquire the lock briefly per statement and release it immediately.
    """
    stmts = [
        """CREATE TABLE IF NOT EXISTS watchtower_fee_payers (
            address          TEXT    NOT NULL PRIMARY KEY,
            first_seen_at    INTEGER NOT NULL,
            last_seen_at     INTEGER NOT NULL,
            tx_count         INTEGER NOT NULL DEFAULT 1,
            total_sol_sent   REAL    NOT NULL DEFAULT 0,
            first_sig        TEXT,
            last_sig         TEXT,
            detection_run    INTEGER NOT NULL DEFAULT 0
        )""",
        """CREATE TABLE IF NOT EXISTS watchtower_sweep_events (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            signature     TEXT    NOT NULL UNIQUE,
            block_time    INTEGER,
            payer_count   INTEGER NOT NULL DEFAULT 0,
            total_sol     REAL    NOT NULL DEFAULT 0,
            raw_payload   TEXT,
            received_at   INTEGER NOT NULL DEFAULT (strftime('%s','now'))
        )""",
        "CREATE INDEX IF NOT EXISTS idx_wt_fee_payers_unseen ON watchtower_fee_payers(detection_run) WHERE detection_run = 0",
        """CREATE TABLE IF NOT EXISTS watchtower_dormant_seen (
            creator  TEXT NOT NULL,
            mint     TEXT NOT NULL,
            seen_at  INTEGER NOT NULL DEFAULT (strftime('%s','now')),
            PRIMARY KEY (creator, mint)
        )""",
        """CREATE TABLE IF NOT EXISTS watchtower_wallet_state (
            address          TEXT    NOT NULL PRIMARY KEY,
            state            TEXT    NOT NULL DEFAULT 'provisioned',
            state_changed_at INTEGER,
            provisioned_at   INTEGER,
            activated_at     INTEGER,
            first_fee_at     INTEGER,
            last_fee_at      INTEGER,
            launch_count     INTEGER NOT NULL DEFAULT 0,
            evidence_json    TEXT,
            updated_at       INTEGER NOT NULL DEFAULT (strftime('%s','now'))
        )""",
        """CREATE TABLE IF NOT EXISTS watchtower_operator_graph (
            operator_address TEXT NOT NULL,
            child_address    TEXT NOT NULL,
            relationship     TEXT NOT NULL,
            amount_sol       REAL,
            first_seen_at    INTEGER,
            last_seen_at     INTEGER,
            tx_signature     TEXT,
            hop              INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY (operator_address, child_address, relationship)
        )""",
        """CREATE TABLE IF NOT EXISTS watchtower_launch_candidates (
            address          TEXT    NOT NULL PRIMARY KEY,
            source_operator  TEXT,
            candidate_reason TEXT,
            confidence       TEXT    NOT NULL DEFAULT 'medium',
            first_signal_at  INTEGER NOT NULL DEFAULT (strftime('%s','now')),
            last_signal_at   INTEGER NOT NULL DEFAULT (strftime('%s','now')),
            launched_mint    TEXT,
            evidence_json    TEXT
        )""",
        """CREATE TABLE IF NOT EXISTS watchtower_raydium_launches (
            pool_address          TEXT NOT NULL PRIMARY KEY,
            mint                  TEXT NOT NULL,
            creator_address       TEXT,
            pool_program          TEXT,
            initial_liquidity_sol REAL,
            detected_at           INTEGER NOT NULL DEFAULT (strftime('%s','now')),
            block_time            INTEGER,
            tx_signature          TEXT,
            operator_link         TEXT,
            link_type             TEXT,
            evidence_json         TEXT
        )""",
        "CREATE INDEX IF NOT EXISTS idx_wt_wallet_state ON watchtower_wallet_state(state)",
        "CREATE INDEX IF NOT EXISTS idx_wt_op_graph_operator ON watchtower_operator_graph(operator_address)",
        "CREATE INDEX IF NOT EXISTS idx_wt_op_graph_child ON watchtower_operator_graph(child_address)",
        "CREATE INDEX IF NOT EXISTS idx_wt_candidates_operator ON watchtower_launch_candidates(source_operator)",
        "CREATE INDEX IF NOT EXISTS idx_wt_raydium_mint ON watchtower_raydium_launches(mint)",
        """CREATE TABLE IF NOT EXISTS watchtower_events (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            event_sequence   INTEGER NOT NULL DEFAULT 0,
            event_type       TEXT    NOT NULL,
            wallet_address   TEXT,
            related_wallet   TEXT,
            token_mint       TEXT,
            payload_json     TEXT,
            source           TEXT,
            created_at       INTEGER NOT NULL DEFAULT (strftime('%s','now'))
        )""",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_wt_events_sequence ON watchtower_events(event_sequence) WHERE event_sequence > 0",
        "CREATE INDEX IF NOT EXISTS idx_wt_events_wallet ON watchtower_events(wallet_address, event_sequence ASC)",
        "CREATE INDEX IF NOT EXISTS idx_wt_events_type ON watchtower_events(event_type, event_sequence ASC)",
        "CREATE INDEX IF NOT EXISTS idx_wt_events_created ON watchtower_events(created_at DESC)",
        """CREATE TABLE IF NOT EXISTS watchtower_infra_events (
            signature        TEXT NOT NULL,
            block_time       INTEGER,
            infra_address    TEXT NOT NULL,
            infra_role       TEXT NOT NULL,
            direction        TEXT NOT NULL,
            counterparty     TEXT NOT NULL,
            amount_sol       REAL NOT NULL DEFAULT 0,
            raw_payload      TEXT,
            received_at      INTEGER NOT NULL DEFAULT (strftime('%s','now')),
            PRIMARY KEY (signature, infra_address, direction)
        )""",
        "CREATE INDEX IF NOT EXISTS idx_wt_infra_events_infra ON watchtower_infra_events(infra_address, block_time DESC)",
        "CREATE INDEX IF NOT EXISTS idx_wt_infra_events_counterparty ON watchtower_infra_events(counterparty)",
        """CREATE TABLE IF NOT EXISTS wt_discovery_log (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            discovered_at  INTEGER NOT NULL DEFAULT (strftime('%s','now')),
            discovery_type TEXT    NOT NULL,
            address        TEXT,
            detail_json    TEXT
        )""",
        "CREATE INDEX IF NOT EXISTS idx_wt_discovery_log_at ON wt_discovery_log(discovered_at DESC)",
        """CREATE TABLE IF NOT EXISTS wt_sub_provisioners (
            address        TEXT NOT NULL PRIMARY KEY,
            first_seen_at  INTEGER NOT NULL DEFAULT (strftime('%s','now')),
            funding_tx     TEXT,
            funding_amount REAL,
            funded_by      TEXT,
            fanout_count   INTEGER NOT NULL DEFAULT 0,
            fanout_amount  REAL,
            fanout_fingerprint TEXT,
            last_scanned_at INTEGER,
            scan_status    TEXT NOT NULL DEFAULT 'pending'
        )""",
        """CREATE TABLE IF NOT EXISTS wt_creator_launches (
            creator_wallet   TEXT NOT NULL,
            mint_address     TEXT NOT NULL,
            launch_tx        TEXT,
            launched_at      INTEGER,
            launch_platform  TEXT NOT NULL DEFAULT 'pump_fun',
            evidence_grade   TEXT NOT NULL DEFAULT 'STRONG',
            evidence_basis   TEXT,
            launch_success_state TEXT NOT NULL DEFAULT 'launched_only',
            migrated_at      INTEGER,
            migration_tx     TEXT,
            PRIMARY KEY (creator_wallet, mint_address)
        )""",
        "CREATE INDEX IF NOT EXISTS idx_wt_creator_launches_mint ON wt_creator_launches(mint_address)",
        "CREATE INDEX IF NOT EXISTS idx_wt_creator_launches_creator ON wt_creator_launches(creator_wallet)",
        "CREATE INDEX IF NOT EXISTS idx_wt_creator_launches_state ON wt_creator_launches(launch_success_state)",
        """CREATE TABLE IF NOT EXISTS wt_staged_wallets (
            wallet_address    TEXT PRIMARY KEY,
            provisioned_at    INTEGER,
            provisioner_address TEXT,
            last_sig          TEXT,
            first_move_sig    TEXT,
            first_move_type   TEXT,
            first_move_at     INTEGER,
            state             TEXT DEFAULT 'DORMANT_FUNDED',
            evidence_grade    TEXT,
            evidence_basis    TEXT
        )""",
        "CREATE INDEX IF NOT EXISTS idx_wt_staged_state ON wt_staged_wallets(state)",
        """CREATE TABLE IF NOT EXISTS wt_graph_nodes (
            address        TEXT NOT NULL PRIMARY KEY,
            node_type      TEXT NOT NULL DEFAULT 'UNKNOWN',
            campaign_id    TEXT,
            first_seen_at  INTEGER NOT NULL DEFAULT (strftime('%s','now')),
            last_active_at INTEGER,
            state          TEXT,
            score          INTEGER,
            evidence_json  TEXT
        )""",
        "CREATE INDEX IF NOT EXISTS idx_wt_graph_nodes_type     ON wt_graph_nodes(node_type)",
        "CREATE INDEX IF NOT EXISTS idx_wt_graph_nodes_campaign ON wt_graph_nodes(campaign_id) WHERE campaign_id IS NOT NULL",
        """CREATE TABLE IF NOT EXISTS wt_graph_edges (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            from_address   TEXT NOT NULL,
            to_address     TEXT NOT NULL,
            edge_type      TEXT NOT NULL,
            amount_sol     REAL,
            tx_signature   TEXT,
            block_time     INTEGER NOT NULL DEFAULT (strftime('%s','now')),
            campaign_id    TEXT,
            UNIQUE(from_address, to_address, edge_type, tx_signature)
        )""",
        "CREATE INDEX IF NOT EXISTS idx_wt_edges_from     ON wt_graph_edges(from_address, block_time DESC)",
        "CREATE INDEX IF NOT EXISTS idx_wt_edges_to       ON wt_graph_edges(to_address,   block_time DESC)",
        "CREATE INDEX IF NOT EXISTS idx_wt_edges_campaign ON wt_graph_edges(campaign_id) WHERE campaign_id IS NOT NULL",
        """CREATE TABLE IF NOT EXISTS wt_campaigns (
            campaign_id        TEXT NOT NULL PRIMARY KEY,
            sub_provisioner    TEXT NOT NULL,
            started_at         INTEGER NOT NULL,
            ended_at           INTEGER,
            state              TEXT NOT NULL DEFAULT 'PROVISIONING',
            creator_count      INTEGER NOT NULL DEFAULT 0,
            trader_count       INTEGER NOT NULL DEFAULT 0,
            token_mints_json   TEXT,
            total_sol_deployed REAL NOT NULL DEFAULT 0,
            total_sol_swept    REAL NOT NULL DEFAULT 0,
            evidence_json      TEXT,
            created_at         INTEGER NOT NULL DEFAULT (strftime('%s','now')),
            updated_at         INTEGER NOT NULL DEFAULT (strftime('%s','now'))
        )""",
        "CREATE INDEX IF NOT EXISTS idx_wt_campaigns_provisioner ON wt_campaigns(sub_provisioner)",
        "CREATE INDEX IF NOT EXISTS idx_wt_campaigns_state       ON wt_campaigns(state)",
        """CREATE TABLE IF NOT EXISTS wt_trader_wallets (
            wallet_address      TEXT NOT NULL PRIMARY KEY,
            provisioner_address TEXT NOT NULL,
            campaign_id         TEXT,
            funded_at           INTEGER,
            funded_amount_sol   REAL,
            state               TEXT NOT NULL DEFAULT 'FUNDED',
            state_changed_at    INTEGER,
            total_buys          INTEGER NOT NULL DEFAULT 0,
            total_sells         INTEGER NOT NULL DEFAULT 0,
            total_bought_sol    REAL NOT NULL DEFAULT 0,
            total_sold_sol      REAL NOT NULL DEFAULT 0,
            net_pnl_sol         REAL,
            last_pamm_at        INTEGER,
            last_sweep_at       INTEGER,
            sweep_destination   TEXT,
            evidence_json       TEXT,
            created_at          INTEGER NOT NULL DEFAULT (strftime('%s','now')),
            updated_at          INTEGER NOT NULL DEFAULT (strftime('%s','now'))
        )""",
        "CREATE INDEX IF NOT EXISTS idx_wt_traders_provisioner ON wt_trader_wallets(provisioner_address)",
        "CREATE INDEX IF NOT EXISTS idx_wt_traders_campaign    ON wt_trader_wallets(campaign_id) WHERE campaign_id IS NOT NULL",
        "CREATE INDEX IF NOT EXISTS idx_wt_traders_state       ON wt_trader_wallets(state)",
        """CREATE TABLE IF NOT EXISTS wt_pamm_interactions (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            signature      TEXT NOT NULL UNIQUE,
            block_time     INTEGER NOT NULL,
            wallet_address TEXT NOT NULL,
            token_mint     TEXT NOT NULL,
            direction      TEXT NOT NULL,
            sol_amount     REAL NOT NULL,
            token_amount   REAL,
            campaign_id    TEXT,
            created_at     INTEGER NOT NULL DEFAULT (strftime('%s','now'))
        )""",
        "CREATE INDEX IF NOT EXISTS idx_wt_pamm_wallet   ON wt_pamm_interactions(wallet_address, block_time DESC)",
        "CREATE INDEX IF NOT EXISTS idx_wt_pamm_mint     ON wt_pamm_interactions(token_mint, block_time DESC)",
        "CREATE INDEX IF NOT EXISTS idx_wt_pamm_campaign ON wt_pamm_interactions(campaign_id, block_time DESC) WHERE campaign_id IS NOT NULL",
        "CREATE INDEX IF NOT EXISTS idx_wt_pamm_time     ON wt_pamm_interactions(block_time DESC)",
        """CREATE TABLE IF NOT EXISTS wt_candidate_scores (
            wallet_address   TEXT NOT NULL PRIMARY KEY,
            score            INTEGER NOT NULL DEFAULT 0,
            score_breakdown  TEXT,
            lineage_path     TEXT,
            reaches_treasury INTEGER NOT NULL DEFAULT 0,
            enrolled_at      INTEGER,
            scored_at        INTEGER NOT NULL DEFAULT (strftime('%s','now')),
            rescored_at      INTEGER
        )""",
        "CREATE INDEX IF NOT EXISTS idx_wt_scores_score ON wt_candidate_scores(score DESC)",
        """CREATE TABLE IF NOT EXISTS wt_webhook_enrollments (
            wallet_address TEXT NOT NULL PRIMARY KEY,
            webhook_id     TEXT NOT NULL,
            enrolled_at    INTEGER NOT NULL DEFAULT (strftime('%s','now')),
            de_enrolled_at INTEGER,
            is_active      INTEGER NOT NULL DEFAULT 1
        )""",
        "CREATE INDEX IF NOT EXISTS idx_wt_enrollment_active ON wt_webhook_enrollments(is_active, webhook_id)",
        """CREATE TABLE IF NOT EXISTS wt_webhook_hits (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            webhook_id       TEXT    NOT NULL,
            wallet_address   TEXT    NOT NULL,
            tx_signature     TEXT,
            tx_type          TEXT,
            source           TEXT,
            counterparty     TEXT,
            slot             INTEGER,
            block_time       INTEGER,
            amount_sol       REAL,
            is_fee_touch     INTEGER NOT NULL DEFAULT 0,
            is_pamm_interaction INTEGER NOT NULL DEFAULT 0,
            created_at       INTEGER NOT NULL DEFAULT (strftime('%s','now'))
        )""",
        "CREATE INDEX IF NOT EXISTS idx_wh_wallet_time  ON wt_webhook_hits(wallet_address, created_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_wh_created      ON wt_webhook_hits(created_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_wh_fee_touch    ON wt_webhook_hits(is_fee_touch) WHERE is_fee_touch=1",
        "CREATE INDEX IF NOT EXISTS idx_wh_source       ON wt_webhook_hits(source, created_at DESC)",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_wh_sig_wallet ON wt_webhook_hits(tx_signature, wallet_address)",
        """CREATE TABLE IF NOT EXISTS wt_infra_telemetry_buckets (
            wallet_address        TEXT    NOT NULL,
            minute_bucket         INTEGER NOT NULL,
            role                  TEXT    NOT NULL,
            hit_count             INTEGER NOT NULL DEFAULT 0,
            unique_counterparties INTEGER NOT NULL DEFAULT 0,
            total_sol             REAL    NOT NULL DEFAULT 0,
            max_tx_sol            REAL    NOT NULL DEFAULT 0,
            burst_score           INTEGER NOT NULL DEFAULT 0,
            created_at            INTEGER NOT NULL DEFAULT (strftime('%s','now')),
            PRIMARY KEY (wallet_address, minute_bucket)
        )""",
        "CREATE INDEX IF NOT EXISTS idx_wt_buckets_time   ON wt_infra_telemetry_buckets(minute_bucket DESC)",
        "CREATE INDEX IF NOT EXISTS idx_wt_buckets_wallet ON wt_infra_telemetry_buckets(wallet_address, minute_bucket DESC)",
        """CREATE TABLE IF NOT EXISTS wt_relay_counterparties (
            sender_address   TEXT    NOT NULL,
            relay_address    TEXT    NOT NULL,
            first_sweep_at   INTEGER,
            last_sweep_at    INTEGER,
            sweep_count      INTEGER NOT NULL DEFAULT 1,
            total_sol        REAL    NOT NULL DEFAULT 0,
            discovery_state  TEXT    NOT NULL DEFAULT 'NEW',
            linked_mint      TEXT,
            linked_campaign  TEXT,
            backtrace_at     INTEGER,
            notes            TEXT,
            PRIMARY KEY (sender_address, relay_address)
        )""",
        "CREATE INDEX IF NOT EXISTS idx_wt_relay_cp_state    ON wt_relay_counterparties(discovery_state, last_sweep_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_wt_relay_cp_sender   ON wt_relay_counterparties(sender_address)",
        "CREATE INDEX IF NOT EXISTS idx_wt_relay_cp_priority ON wt_relay_counterparties(priority_score DESC)",
        """CREATE TABLE IF NOT EXISTS wt_relay_sweep_epochs (
            epoch_id          INTEGER PRIMARY KEY AUTOINCREMENT,
            relay_address     TEXT    NOT NULL,
            collector_address TEXT,
            sweep_count       INTEGER NOT NULL DEFAULT 0,
            unique_senders    INTEGER NOT NULL DEFAULT 0,
            total_sol         REAL    NOT NULL DEFAULT 0,
            started_at        INTEGER NOT NULL,
            ended_at          INTEGER,
            epoch_state       TEXT    NOT NULL DEFAULT 'OPEN',
            created_at        INTEGER NOT NULL DEFAULT (strftime('%s','now'))
        )""",
        "CREATE INDEX IF NOT EXISTS idx_wt_epochs_relay ON wt_relay_sweep_epochs(relay_address, started_at DESC)",
        """CREATE TABLE IF NOT EXISTS wt_extraction_clusters (
            cluster_id        INTEGER PRIMARY KEY AUTOINCREMENT,
            relay_wallet      TEXT    NOT NULL,
            collector_wallet  TEXT,
            token_count       INTEGER NOT NULL DEFAULT 0,
            creator_count     INTEGER NOT NULL DEFAULT 0,
            total_sol_swept   REAL    NOT NULL DEFAULT 0,
            first_seen        INTEGER,
            last_seen         INTEGER,
            confidence_score  REAL    NOT NULL DEFAULT 0,
            cluster_state     TEXT    NOT NULL DEFAULT 'FORMING',
            evidence_json     TEXT,
            created_at        INTEGER NOT NULL DEFAULT (strftime('%s','now'))
        )""",
        "CREATE INDEX IF NOT EXISTS idx_wt_clusters_relay ON wt_extraction_clusters(relay_wallet, confidence_score DESC)",
        """CREATE TABLE IF NOT EXISTS wt_cluster_members (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            cluster_id        INTEGER NOT NULL,
            token_mint        TEXT,
            creator_wallet    TEXT,
            relay_wallet      TEXT    NOT NULL,
            sweep_sig         TEXT,
            confidence        REAL    NOT NULL DEFAULT 0,
            attribution_reason TEXT,
            assigned_at       INTEGER NOT NULL DEFAULT (strftime('%s','now')),
            UNIQUE(cluster_id, token_mint)
        )""",
        "CREATE INDEX IF NOT EXISTS idx_wt_members_cluster  ON wt_cluster_members(cluster_id)",
        "CREATE INDEX IF NOT EXISTS idx_wt_members_mint     ON wt_cluster_members(token_mint)",
        "CREATE INDEX IF NOT EXISTS idx_wt_members_creator  ON wt_cluster_members(creator_wallet)",
        """CREATE TABLE IF NOT EXISTS wt_wallet_tier (
            wallet_address  TEXT    PRIMARY KEY,
            tier            INTEGER NOT NULL,
            role            TEXT    NOT NULL,
            classified_at   INTEGER NOT NULL DEFAULT (strftime('%s','now')),
            auto_classified INTEGER NOT NULL DEFAULT 1,
            notes           TEXT
        )""",
        """CREATE TABLE IF NOT EXISTS wt_launch_corridors (
            wallet_address      TEXT    NOT NULL PRIMARY KEY,
            treasury_sig        TEXT,
            treasury_ts         INTEGER NOT NULL,
            treasury_sol        REAL    NOT NULL,
            signaller_sig       TEXT,
            signaller_ts        INTEGER,
            signaller_lag_s     INTEGER,
            state               TEXT    NOT NULL DEFAULT 'AWAITING_SIGNALLER',
            first_tx_sig        TEXT,
            first_tx_ts         INTEGER,
            first_tx_dest       TEXT,
            first_tx_program    TEXT,
            corridor_resolved_at INTEGER,
            mint_address        TEXT,
            enrolled_at         INTEGER,
            f5m_expires_at      INTEGER,
            created_at          INTEGER NOT NULL DEFAULT (strftime('%s','now')),
            updated_at          INTEGER NOT NULL DEFAULT (strftime('%s','now'))
        )""",
        "CREATE INDEX IF NOT EXISTS idx_wt_corridors_state   ON wt_launch_corridors(state, treasury_ts DESC)",
        "CREATE INDEX IF NOT EXISTS idx_wt_corridors_created ON wt_launch_corridors(created_at DESC)",
        """CREATE TABLE IF NOT EXISTS wt_swarm_corridors (
            id                      INTEGER PRIMARY KEY AUTOINCREMENT,
            provisioner_wallet      TEXT    NOT NULL UNIQUE,
            treasury_sig            TEXT,
            treasury_ts             INTEGER NOT NULL,
            treasury_sol            REAL    NOT NULL,
            wallet_count            INTEGER NOT NULL DEFAULT 0,
            median_fanout_sol       REAL,
            min_fanout_sol          REAL,
            max_fanout_sol          REAL,
            fanout_duration_s       INTEGER,
            fanout_started_at       INTEGER,
            fanout_completed_at     INTEGER,
            target_token_count      INTEGER NOT NULL DEFAULT 0,
            primary_token_mint      TEXT,
            state                   TEXT    NOT NULL DEFAULT 'SWARM_DEPLOYMENT_ACTIVE',
            coordinated_exit_detected INTEGER NOT NULL DEFAULT 0,
            sweepback_detected      INTEGER NOT NULL DEFAULT 0,
            treasury_recycle_detected INTEGER NOT NULL DEFAULT 0,
            recycle_sig             TEXT,
            recycle_ts              INTEGER,
            recycle_sol             REAL,
            created_at              INTEGER NOT NULL DEFAULT (strftime('%s','now')),
            updated_at              INTEGER NOT NULL DEFAULT (strftime('%s','now'))
        )""",
        "CREATE INDEX IF NOT EXISTS idx_wt_swarm_state     ON wt_swarm_corridors(state, treasury_ts DESC)",
        "CREATE INDEX IF NOT EXISTS idx_wt_swarm_provisioner ON wt_swarm_corridors(provisioner_wallet)",
        "CREATE INDEX IF NOT EXISTS idx_wt_swarm_token     ON wt_swarm_corridors(primary_token_mint)",
        """CREATE TABLE IF NOT EXISTS wt_swarm_corridors_samples (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            swarm_id         INTEGER NOT NULL,
            recipient_wallet TEXT    NOT NULL,
            sample_ts        INTEGER NOT NULL,
            fanout_sol       REAL,
            sequence         INTEGER,
            first_buy_mint   TEXT,
            confirmed_at     INTEGER,
            UNIQUE(swarm_id, recipient_wallet)
        )""",
        "CREATE INDEX IF NOT EXISTS idx_wt_swarm_samples_swarm ON wt_swarm_corridors_samples(swarm_id)",
        """CREATE TABLE IF NOT EXISTS wt_operator_launches (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            operator_wallet     TEXT    NOT NULL,
            deployer_wallet     TEXT    NOT NULL,
            mint                TEXT,
            create_tx           TEXT,
            first_seen_ts       INTEGER NOT NULL,
            confidence          TEXT    NOT NULL DEFAULT 'DEPLOYER_IDENTIFIED',
            swarm_provisioner   TEXT,
            swarm_confirmed_at  INTEGER,
            swarm_sample_json   TEXT,
            treasury_sol        REAL,
            treasury_sig        TEXT,
            created_at          INTEGER NOT NULL DEFAULT (strftime('%s','now')),
            updated_at          INTEGER NOT NULL DEFAULT (strftime('%s','now')),
            UNIQUE(deployer_wallet)
        )""",
        "CREATE INDEX IF NOT EXISTS idx_wt_op_launches_operator ON wt_operator_launches(operator_wallet, first_seen_ts DESC)",
        "CREATE INDEX IF NOT EXISTS idx_wt_op_launches_mint     ON wt_operator_launches(mint)",
        "CREATE INDEX IF NOT EXISTS idx_wt_op_launches_conf     ON wt_operator_launches(confidence)",
        """CREATE TABLE IF NOT EXISTS wt_operator_clusters (
            cluster_id          INTEGER PRIMARY KEY AUTOINCREMENT,
            state               TEXT    NOT NULL DEFAULT 'FORMING',
            confidence          REAL    NOT NULL DEFAULT 0,
            origin              TEXT    NOT NULL DEFAULT 'discovered',
            label               TEXT,
            treasury_wallet     TEXT,
            token_count         INTEGER NOT NULL DEFAULT 0,
            provisioner_count   INTEGER NOT NULL DEFAULT 0,
            total_sol_deployed  REAL    NOT NULL DEFAULT 0,
            total_sol_recycled  REAL    NOT NULL DEFAULT 0,
            first_seen          INTEGER,
            last_seen           INTEGER,
            deployment_fingerprint_id INTEGER,
            notes               TEXT,
            created_at          INTEGER NOT NULL DEFAULT (strftime('%s','now')),
            updated_at          INTEGER NOT NULL DEFAULT (strftime('%s','now'))
        )""",
        "CREATE INDEX IF NOT EXISTS idx_wt_clusters_conf ON wt_operator_clusters(confidence DESC)",
        "CREATE INDEX IF NOT EXISTS idx_wt_clusters_treasury ON wt_operator_clusters(treasury_wallet)",
        """CREATE TABLE IF NOT EXISTS wt_operator_treasuries (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            wallet              TEXT    NOT NULL UNIQUE,
            cluster_id          INTEGER,
            state               TEXT    NOT NULL DEFAULT 'CANDIDATE',
            confidence          REAL    NOT NULL DEFAULT 0,
            total_deployed_sol  REAL    NOT NULL DEFAULT 0,
            total_recycled_sol  REAL    NOT NULL DEFAULT 0,
            deployment_count    INTEGER NOT NULL DEFAULT 0,
            first_deployment    INTEGER,
            last_deployment     INTEGER,
            typical_deploy_sol  REAL,
            deploy_cadence_h    REAL,
            evidence_json       TEXT,
            created_at          INTEGER NOT NULL DEFAULT (strftime('%s','now')),
            updated_at          INTEGER NOT NULL DEFAULT (strftime('%s','now'))
        )""",
        "CREATE INDEX IF NOT EXISTS idx_wt_treasuries_cluster ON wt_operator_treasuries(cluster_id)",
        "CREATE INDEX IF NOT EXISTS idx_wt_treasuries_state   ON wt_operator_treasuries(state, confidence DESC)",
        """CREATE TABLE IF NOT EXISTS wt_swarm_provisioners (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            wallet              TEXT    NOT NULL UNIQUE,
            cluster_id          INTEGER,
            treasury_wallet     TEXT,
            state               TEXT    NOT NULL DEFAULT 'CANDIDATE',
            funded_at           INTEGER,
            funding_sol         REAL,
            wallet_count        INTEGER NOT NULL DEFAULT 0,
            median_fanout_sol   REAL,
            stddev_fanout_sol   REAL,
            fanout_window_s     INTEGER,
            primary_token_mint  TEXT,
            swept_at            INTEGER,
            recycled_sol        REAL,
            evidence_json       TEXT,
            created_at          INTEGER NOT NULL DEFAULT (strftime('%s','now')),
            updated_at          INTEGER NOT NULL DEFAULT (strftime('%s','now'))
        )""",
        "CREATE INDEX IF NOT EXISTS idx_wt_swarm_provs_cluster  ON wt_swarm_provisioners(cluster_id)",
        "CREATE INDEX IF NOT EXISTS idx_wt_swarm_provs_treasury ON wt_swarm_provisioners(treasury_wallet)",
        "CREATE INDEX IF NOT EXISTS idx_wt_swarm_provs_token    ON wt_swarm_provisioners(primary_token_mint)",
        """CREATE TABLE IF NOT EXISTS wt_operator_fingerprints (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            cluster_id          INTEGER,
            archetype           TEXT    NOT NULL DEFAULT 'SWARM',
            typical_deploy_sol  REAL,
            deploy_sol_stddev   REAL,
            median_fanout_sol   REAL,
            fanout_sol_stddev   REAL,
            typical_wallet_count INTEGER,
            fanout_window_s     INTEGER,
            buy_window_s        INTEGER,
            exit_window_s       INTEGER,
            one_token_concentration REAL,
            recycle_rate        REAL,
            sample_count        INTEGER NOT NULL DEFAULT 0,
            created_at          INTEGER NOT NULL DEFAULT (strftime('%s','now')),
            updated_at          INTEGER NOT NULL DEFAULT (strftime('%s','now'))
        )""",
        "CREATE INDEX IF NOT EXISTS idx_wt_fingerprints_cluster ON wt_operator_fingerprints(cluster_id)",
        """CREATE TABLE IF NOT EXISTS wt_swarm_candidates (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            token_mint          TEXT    NOT NULL UNIQUE,
            migrated_at         INTEGER,
            scanned_at          INTEGER,
            state               TEXT    NOT NULL DEFAULT 'PENDING',
            cluster_id          INTEGER,
            provisioner_wallet  TEXT,
            treasury_wallet     TEXT,
            unique_buyers       INTEGER,
            buy_window_s        INTEGER,
            median_funding_sol  REAL,
            stddev_funding_sol  REAL,
            one_token_pct       REAL,
            confidence          REAL    NOT NULL DEFAULT 0,
            operator_class      TEXT,
            evidence_json       TEXT,
            created_at          INTEGER NOT NULL DEFAULT (strftime('%s','now')),
            updated_at          INTEGER NOT NULL DEFAULT (strftime('%s','now'))
        )""",
        "CREATE INDEX IF NOT EXISTS idx_wt_swarm_cands_state   ON wt_swarm_candidates(state, migrated_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_wt_swarm_cands_cluster ON wt_swarm_candidates(cluster_id)",
        "CREATE INDEX IF NOT EXISTS idx_wt_swarm_cands_prov    ON wt_swarm_candidates(provisioner_wallet)",
        """CREATE TABLE IF NOT EXISTS wt_worker_heartbeat (
            worker_name   TEXT    PRIMARY KEY,
            last_seen     INTEGER NOT NULL,
            status        TEXT    NOT NULL DEFAULT 'unknown',
            meta_json     TEXT
        )""",
        """CREATE TABLE IF NOT EXISTS wt_worker_failures (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            worker_name   TEXT    NOT NULL,
            failed_at     INTEGER NOT NULL DEFAULT (strftime('%s','now')),
            restart_count INTEGER NOT NULL DEFAULT 0,
            error         TEXT
        )""",
        "CREATE INDEX IF NOT EXISTS idx_wt_hb_worker ON wt_worker_heartbeat(worker_name)",
        "CREATE INDEX IF NOT EXISTS idx_wt_failures_worker ON wt_worker_failures(worker_name, failed_at DESC)",
        """CREATE TABLE IF NOT EXISTS watch_candidate_tokens (
            mint                  TEXT    PRIMARY KEY,
            creator_address       TEXT    NOT NULL,
            prediction_score      INTEGER,
            has_sol_flows         INTEGER NOT NULL DEFAULT 0,
            classified_as         TEXT    NOT NULL DEFAULT 'UNKNOWN',
            classification_conf   REAL    NOT NULL DEFAULT 0.0,
            classification_reason TEXT,
            cluster_id            INTEGER,
            added_at              INTEGER NOT NULL DEFAULT (strftime('%s','now')),
            updated_at            INTEGER NOT NULL DEFAULT (strftime('%s','now'))
        )""",
        "CREATE INDEX IF NOT EXISTS idx_wct_creator   ON watch_candidate_tokens(creator_address)",
        "CREATE INDEX IF NOT EXISTS idx_wct_class     ON watch_candidate_tokens(classified_as, classification_conf DESC)",
        "CREATE INDEX IF NOT EXISTS idx_wct_cluster   ON watch_candidate_tokens(cluster_id)",
        """CREATE TABLE IF NOT EXISTS wt_operations (
            operation_id        INTEGER PRIMARY KEY AUTOINCREMENT,
            auto_name           TEXT,
            human_name          TEXT,
            operator_identity   TEXT    NOT NULL DEFAULT 'UNKNOWN',
            identity_confidence TEXT    NOT NULL DEFAULT 'UNKNOWN',
            identity_validated_at INTEGER,
            state               TEXT    NOT NULL DEFAULT 'DISCOVERED',
            token_count         INTEGER NOT NULL DEFAULT 0,
            creator_count       INTEGER NOT NULL DEFAULT 0,
            confidence          REAL    NOT NULL DEFAULT 0.0,
            corridor_amount     TEXT,
            window_start        INTEGER,
            window_end          INTEGER,
            discovery_signals   TEXT,
            discovered_at       INTEGER NOT NULL DEFAULT (strftime('%s','now')),
            updated_at          INTEGER NOT NULL DEFAULT (strftime('%s','now'))
        )""",
        "CREATE INDEX IF NOT EXISTS idx_wt_ops_corridor ON wt_operations(corridor_amount, window_start)",
        "CREATE INDEX IF NOT EXISTS idx_wt_ops_identity ON wt_operations(operator_identity)",
        """CREATE TABLE IF NOT EXISTS wt_operation_members (
            operation_id        INTEGER NOT NULL,
            token_mint          TEXT    NOT NULL,
            creator_wallet      TEXT,
            funding_amount      REAL,
            migrated_at         INTEGER,
            join_signal         TEXT,
            PRIMARY KEY (operation_id, token_mint)
        )""",
        "CREATE INDEX IF NOT EXISTS idx_wt_opmem_mint ON wt_operation_members(token_mint)",
        """CREATE TABLE IF NOT EXISTS wt_operation_transitions (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            operation_id   INTEGER NOT NULL,
            from_state     TEXT,
            to_state       TEXT    NOT NULL,
            actor          TEXT,
            detail         TEXT,
            at             INTEGER NOT NULL DEFAULT (strftime('%s','now'))
        )""",
        "CREATE INDEX IF NOT EXISTS idx_wt_optrans_op ON wt_operation_transitions(operation_id, at)",
        """CREATE TABLE IF NOT EXISTS wt_hub_backfill_queue (
            funder_address   TEXT PRIMARY KEY,
            seed_creator     TEXT,
            corridor_amount  TEXT,
            status           TEXT NOT NULL DEFAULT 'pending',
            hops_written     INTEGER DEFAULT 0,
            reached_hub      TEXT,
            attempts         INTEGER DEFAULT 0,
            last_error       TEXT,
            enqueued_at      INTEGER DEFAULT (strftime('%s','now')),
            processed_at     INTEGER
        )""",
        "CREATE INDEX IF NOT EXISTS idx_wt_hbq_status ON wt_hub_backfill_queue(status)",
        """CREATE TABLE IF NOT EXISTS wt_identity_proposals (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            operation_id      INTEGER,
            corridor_amount   TEXT,
            current_identity  TEXT,
            proposed_identity TEXT,
            evidence_hub      TEXT,
            evidence_role     TEXT,
            token_count       INTEGER,
            proposed_at       INTEGER DEFAULT (strftime('%s','now')),
            applied           INTEGER DEFAULT 0
        )""",
        "CREATE INDEX IF NOT EXISTS idx_wt_idprop_op ON wt_identity_proposals(operation_id, applied)",
    ]
    for stmt in stmts:
        try:
            conn.execute(stmt)
        except Exception:
            pass  # table/index already exists — non-fatal

    # ── Seed WATCH as Operator Cluster #1 (reference fingerprint) ─────────────
    try:
        existing = conn.execute(
            "SELECT cluster_id FROM wt_operator_clusters WHERE label='WATCH'"
        ).fetchone()
        if not existing:
            conn.execute("""
                INSERT OR IGNORE INTO wt_operator_clusters
                    (state, confidence, origin, label, treasury_wallet,
                     token_count, provisioner_count, total_sol_deployed,
                     first_seen, last_seen, notes, created_at, updated_at)
                VALUES ('ACTIVE', 1.0, 'seed', 'WATCH',
                        '44orWS68MqXG198M3YXyZoNrYtsNhgnNhtUT5SavqJFM',
                        35, 13, 2450.0,
                        1745791200, strftime('%s','now'),
                        'Seed cluster: known WATCH operator. 35+ SWARM ops Apr-May 2026. Fingerprint: 60-80 SOL deploy, 0.014 SOL fanout, 800-5000 wallets, 1 token.',
                        strftime('%s','now'), strftime('%s','now'))
            """)
            conn.execute("""
                INSERT OR IGNORE INTO wt_operator_treasuries
                    (wallet, state, confidence, deployment_count,
                     typical_deploy_sol, first_deployment, last_deployment, created_at, updated_at)
                VALUES ('44orWS68MqXG198M3YXyZoNrYtsNhgnNhtUT5SavqJFM',
                        'CONFIRMED', 1.0, 35, 70.0,
                        1745791200, strftime('%s','now'),
                        strftime('%s','now'), strftime('%s','now'))
            """)
            cluster_id = conn.execute(
                "SELECT cluster_id FROM wt_operator_clusters WHERE label='WATCH'"
            ).fetchone()
            if cluster_id:
                cid = cluster_id[0]
                conn.execute(
                    "UPDATE wt_operator_treasuries SET cluster_id=? WHERE wallet=?",
                    (cid, "44orWS68MqXG198M3YXyZoNrYtsNhgnNhtUT5SavqJFM")
                )
                conn.execute("""
                    INSERT OR IGNORE INTO wt_operator_fingerprints
                        (cluster_id, archetype, typical_deploy_sol, deploy_sol_stddev,
                         median_fanout_sol, fanout_sol_stddev, typical_wallet_count,
                         fanout_window_s, one_token_concentration, recycle_rate,
                         sample_count, created_at, updated_at)
                    VALUES (?, 'SWARM', 70.0, 5.0, 0.014, 0.001, 2000,
                            3600, 0.98, 0.88, 35,
                            strftime('%s','now'), strftime('%s','now'))
                """, (cid,))
    except Exception as e:
        print(f"[WT_SEED] WATCH cluster seed error: {e}", flush=True)

    conn.commit()

    # ── Column migrations ──────────────────────────────────────────────────────
    _migrations = [
        "ALTER TABLE wt_launch_corridors ADD COLUMN corridor_type TEXT DEFAULT 'CREATOR'",
        "ALTER TABLE wt_relay_counterparties ADD COLUMN downstream_collector TEXT",
        "ALTER TABLE wt_relay_counterparties ADD COLUMN tx_sig TEXT",
        "ALTER TABLE wt_relay_counterparties ADD COLUMN block_time INTEGER",
        "ALTER TABLE wt_relay_counterparties ADD COLUMN inferred_mint TEXT",
        "ALTER TABLE wt_relay_counterparties ADD COLUMN inferred_creator TEXT",
        "ALTER TABLE wt_relay_counterparties ADD COLUMN sweep_epoch_id INTEGER",
        "ALTER TABLE wt_relay_counterparties ADD COLUMN treasury_recycle_detected INTEGER DEFAULT 0",
        "ALTER TABLE wt_relay_counterparties ADD COLUMN cluster_id INTEGER",
        "ALTER TABLE wt_relay_counterparties ADD COLUMN backtrace_depth INTEGER DEFAULT 0",
        "ALTER TABLE wt_relay_counterparties ADD COLUMN backtrace_error TEXT",
        "ALTER TABLE wt_relay_counterparties ADD COLUMN priority_score REAL DEFAULT 0",
        "ALTER TABLE wt_swarm_candidates ADD COLUMN operator_class TEXT",
        "ALTER TABLE wt_swarm_candidates ADD COLUMN operation TEXT",
        "ALTER TABLE wt_swarm_candidates ADD COLUMN wt_link TEXT",
        "ALTER TABLE wt_operator_clusters ADD COLUMN operator_classification TEXT DEFAULT 'UNKNOWN'",
        "ALTER TABLE wt_operator_clusters ADD COLUMN classification_confidence REAL DEFAULT 0.0",
        "ALTER TABLE wt_operator_clusters ADD COLUMN classification_reason TEXT",
        "ALTER TABLE wt_operator_clusters ADD COLUMN cluster_evidence_json TEXT",
        "ALTER TABLE wt_operator_clusters ADD COLUMN watch_token_count INTEGER DEFAULT 0",
        "ALTER TABLE wt_operations ADD COLUMN coherence_score REAL DEFAULT 0.0",
        "ALTER TABLE wt_operations ADD COLUMN coherence_flag TEXT",
        "ALTER TABLE wt_operations ADD COLUMN merged_into INTEGER",
        "ALTER TABLE wt_operations ADD COLUMN noise_reason TEXT",
        "ALTER TABLE wt_operations ADD COLUMN first_discovered_at INTEGER",
        "ALTER TABLE wt_operations ADD COLUMN identity_confidence TEXT NOT NULL DEFAULT 'UNKNOWN'",
        "ALTER TABLE wt_operations ADD COLUMN identity_validated_at INTEGER",
        "ALTER TABLE wt_sub_provisioners ADD COLUMN token_mint TEXT",
        "ALTER TABLE wt_sub_provisioners ADD COLUMN token_symbol TEXT",
        "ALTER TABLE wt_sub_provisioners ADD COLUMN traded_amount REAL",
        "ALTER TABLE wt_sub_provisioners ADD COLUMN last_trade_tx TEXT",
        "ALTER TABLE wt_sub_provisioners ADD COLUMN last_trade_at INTEGER",
        "ALTER TABLE watchtower_infra_events ADD COLUMN token_mint TEXT",
        "ALTER TABLE watchtower_infra_events ADD COLUMN token_symbol TEXT",
        "ALTER TABLE watchtower_infra_events ADD COLUMN traded_amount REAL",
    ]
    for m in _migrations:
        try:
            conn.execute(m)
        except Exception:
            pass  # column already exists
    conn.commit()


def seed_wallet_tiers(conn: sqlite3.Connection, infra_roles: dict) -> int:
    """Insert static wt_wallet_tier rows from the _WT_INFRA_ROLES mapping.

    Uses INSERT OR IGNORE — existing rows are untouched.
    Returns the number of rows attempted.
    """
    count = 0
    for addr, role in infra_roles.items():
        tier = 1 if role in ("SIGNALLER", "SUB_PROV") else 2
        conn.execute("""
            INSERT OR IGNORE INTO wt_wallet_tier
                (wallet_address, tier, role, auto_classified)
            VALUES (?, ?, ?, 1)
        """, (addr, tier, role))
        count += 1
    conn.commit()
    return count


def verify_sentinel(db_path: str = None) -> bool:
    """Read-only check: returns True if WATCHTOWER tables have been initialised."""
    path = db_path or DB_PATH
    try:
        conn = db_connect(path, timeout=5, read_only=True)
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (_SENTINEL_TABLE,)
        ).fetchone()
        conn.close()
        return row is not None
    except Exception:
        return False
