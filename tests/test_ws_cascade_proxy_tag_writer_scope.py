import sqlite3
import threading

from src.core import ws_cascade_store as store
from src.utils.db_locking import db_connect


def _schema(conn):
    conn.executescript("""
        CREATE TABLE wt_active_subprov_sessions (
            id INTEGER PRIMARY KEY, subprov_wallet TEXT, treasury_wallet TEXT,
            funding_amount REAL, funding_mechanism TEXT, state TEXT,
            session_tag TEXT
        );
        CREATE TABLE wt_subprov_evidence (subprov TEXT);
        CREATE TABLE wt_webhook_hits (
            wallet_address TEXT, counterparty TEXT, direction TEXT,
            tx_signature TEXT
        );
        CREATE TABLE wt_capital_reloads (subprov TEXT, treasury TEXT);
        CREATE TABLE wt_confirmed_treasuries (treasury TEXT);
    """)


def test_proxy_scan_does_not_hold_writer_lane(tmp_path):
    """A paused classification scan must not block an independent writer."""
    path = tmp_path / "ops.db"
    with db_connect(str(path)) as conn:
        _schema(conn)
        conn.execute(
            "INSERT INTO wt_active_subprov_sessions VALUES "
            "(1,'sub','treasury',10.0,'PLAIN_TRANSFER','EXPIRED',NULL)"
        )
        conn.commit()

    scanning = threading.Event()
    release = threading.Event()

    class PausedConnection:
        def __init__(self, conn):
            self._conn = conn
        @property
        def row_factory(self):
            return self._conn.row_factory
        @row_factory.setter
        def row_factory(self, value):
            self._conn.row_factory = value
        def execute(self, sql, params=()):
            if "SELECT s.id, s.subprov_wallet, s.treasury_wallet, s.funding_amount" in sql:
                scanning.set()
                assert release.wait(2)
            return self._conn.execute(sql, params)

    def classify():
        with db_connect(str(path)) as raw:
            tags = store.find_operational_spend_proxy_tags(PausedConnection(raw))
            store.apply_operational_spend_proxy_tags(raw, tags)
            raw.commit()

    thread = threading.Thread(target=classify)
    thread.start()
    assert scanning.wait(1)
    with db_connect(str(path), timeout=1) as writer:
        writer.execute("INSERT INTO wt_confirmed_treasuries VALUES ('other')")
        writer.commit()
    release.set()
    thread.join(2)
    assert not thread.is_alive()
