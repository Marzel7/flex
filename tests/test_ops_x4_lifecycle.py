"""
Sprint X4 — Operational Lifecycle Framework tests.

Tests cover:
- LifecycleSnapshot validation (valid/invalid states)
- PlatformLifecycleSummary aggregation
- Adapter error resilience (returns IDLE snapshot, never raises)
- Lifecycle route responses (via Flask test client)
- Template smoke-tests: lifecycle panel markup present
"""

import sys
import os
import time
import importlib
import sqlite3
import tempfile
import re

import pytest

# ── Allow importing from project root ────────────────────────────────────────
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


# ════════════════════════════════════════════════════════════════════════════
# 1. LifecycleSnapshot
# ════════════════════════════════════════════════════════════════════════════

class TestLifecycleSnapshot:
    def _make(self, state="IDLE", **kw):
        from src.ops.lifecycle import LifecycleSnapshot
        defaults = dict(
            operation_id="test-op",
            display_name="Test Op",
            lifecycle_state=state,
            state_reason="test reason",
            counts={},
            confidence=None,
            last_transition_at=None,
            next_expected_state=None,
            generated_at=int(time.time()),
        )
        defaults.update(kw)
        return LifecycleSnapshot(**defaults)

    def test_valid_states_accepted(self):
        from src.ops.lifecycle import STATES_ORDERED
        for s in STATES_ORDERED:
            snap = self._make(state=s)
            assert snap.lifecycle_state == s

    def test_invalid_state_raises(self):
        from src.ops.lifecycle import LifecycleSnapshot
        with pytest.raises(ValueError, match="Invalid lifecycle state"):
            self._make(state="NONSENSE")

    def test_to_dict_contains_state_display(self):
        snap = self._make(state="ARMED")
        d = snap.to_dict()
        assert d["lifecycle_state"] == "ARMED"
        assert d["state_display"]["colour"] == "amber"
        assert d["state_display"]["label"] == "ARMED"

    def test_to_dict_includes_next_expected(self):
        snap = self._make(state="OBSERVING", next_expected_state="ARMED")
        d = snap.to_dict()
        assert d["next_expected_state"] == "ARMED"
        assert d["next_expected_display"] == "ARMED"

    def test_to_dict_meta_default_empty(self):
        snap = self._make(state="IDLE")
        assert snap.to_dict()["meta"] == {}

    def test_confidence_optional(self):
        snap = self._make(state="IDLE", confidence=None)
        assert snap.to_dict()["confidence"] is None

    def test_confidence_range(self):
        snap = self._make(state="ARMED", confidence=0.75)
        assert snap.to_dict()["confidence"] == 0.75


# ════════════════════════════════════════════════════════════════════════════
# 2. PlatformLifecycleSummary
# ════════════════════════════════════════════════════════════════════════════

class TestPlatformLifecycleSummary:
    def _snap(self, state, observing=0, armed=0, active=0, completed=0, op_id="wt"):
        from src.ops.lifecycle import LifecycleSnapshot, OBSERVING, ARMED, ACTIVE, COMPLETED
        now = int(time.time())
        return LifecycleSnapshot(
            operation_id=op_id,
            display_name=op_id,
            lifecycle_state=state,
            state_reason="",
            counts={OBSERVING: observing, ARMED: armed, ACTIVE: active, COMPLETED: completed},
            confidence=None,
            last_transition_at=None,
            next_expected_state=None,
            generated_at=now,
        )

    def _build(self, snaps):
        from src.ops.lifecycle import PlatformLifecycleSummary, OBSERVING, ARMED, ACTIVE, COMPLETED
        now = int(time.time())
        online = {"OBSERVING", "ARMED", "ACTIVE"}
        return PlatformLifecycleSummary(
            operations_total=len(snaps),
            operations_online=sum(1 for s in snaps if s.lifecycle_state in online),
            observing_total=sum(s.counts.get(OBSERVING, 0) for s in snaps),
            armed_total=sum(s.counts.get(ARMED, 0) for s in snaps),
            active_total=sum(s.counts.get(ACTIVE, 0) for s in snaps),
            completed_today=sum(s.counts.get(COMPLETED, 0) for s in snaps),
            snapshots=snaps,
            generated_at=now,
        )

    def test_aggregate_counts(self):
        snaps = [
            self._snap("OBSERVING", observing=5, armed=0, op_id="a"),
            self._snap("ARMED",     observing=2, armed=3, op_id="b"),
        ]
        summary = self._build(snaps)
        assert summary.observing_total == 7
        assert summary.armed_total     == 3
        assert summary.operations_online == 2

    def test_idle_not_counted_as_online(self):
        snaps = [
            self._snap("IDLE",      op_id="a"),
            self._snap("OBSERVING", observing=1, op_id="b"),
        ]
        summary = self._build(snaps)
        assert summary.operations_online == 1

    def test_to_dict_contains_snapshots(self):
        snaps = [self._snap("IDLE", op_id="x")]
        d = self._build(snaps).to_dict()
        assert len(d["snapshots"]) == 1
        assert d["snapshots"][0]["operation_id"] == "x"


# ════════════════════════════════════════════════════════════════════════════
# 3. Adapter error resilience
# ════════════════════════════════════════════════════════════════════════════

class TestAdapterResilience:
    """
    Adapters must return a valid IDLE snapshot (never raise) even when
    the database is missing or tables don't exist.
    """

    def test_watchtower_adapter_nonexistent_db(self, monkeypatch):
        monkeypatch.setenv("FLEX_OPS_DB_PATH", "/tmp/no_such_file_x4_test.db")
        # Force the path helper to use a bad path
        import src.ops.lifecycle_adapters as la
        monkeypatch.setattr(la, "_ops_db_path", lambda: "/tmp/no_such_file_x4_test.db")
        snap = la.watchtower_lifecycle()
        assert snap.lifecycle_state == "IDLE"
        assert "error" in snap.state_reason.lower() or snap.lifecycle_state == "IDLE"

    def test_lo_adapter_missing_table(self, monkeypatch, tmp_path):
        db_path = str(tmp_path / "empty.db")
        conn = sqlite3.connect(db_path)
        conn.close()
        import src.ops.lifecycle_adapters as la
        monkeypatch.setattr(la, "_ops_db_path", lambda: db_path)
        snap = la.launcher_observatory_lifecycle()
        assert snap.lifecycle_state == "IDLE"

    def test_bso_adapter_missing_table(self, monkeypatch, tmp_path):
        db_path = str(tmp_path / "empty.db")
        conn = sqlite3.connect(db_path)
        conn.close()
        import src.ops.lifecycle_adapters as la
        monkeypatch.setattr(la, "_ops_db_path", lambda: db_path)
        snap = la.buy_swarm_observatory_lifecycle()
        assert snap.lifecycle_state == "IDLE"

    def test_get_lifecycle_unknown_op(self):
        from src.ops.lifecycle_adapters import get_lifecycle
        assert get_lifecycle("nonexistent-op") is None

    def test_get_all_lifecycles_returns_three(self, monkeypatch, tmp_path):
        db_path = str(tmp_path / "empty.db")
        conn = sqlite3.connect(db_path)
        conn.close()
        import src.ops.lifecycle_adapters as la
        monkeypatch.setattr(la, "_ops_db_path", lambda: db_path)
        snaps = la.get_all_lifecycles()
        assert len(snaps) == 3
        op_ids = {s.operation_id for s in snaps}
        assert "watchtower" in op_ids
        assert "launcher-observatory" in op_ids
        assert "buy-swarm-observatory" in op_ids


# ════════════════════════════════════════════════════════════════════════════
# 4. Adapters with real data (SQLite fixtures)
# ════════════════════════════════════════════════════════════════════════════

class TestAdapterWithFixtures:
    def _empty_db(self, tmp_path):
        db_path = str(tmp_path / "ops.db")
        conn = sqlite3.connect(db_path)
        conn.close()
        return db_path

    def test_watchtower_observing_state(self, monkeypatch, tmp_path):
        db_path = self._empty_db(tmp_path)
        conn = sqlite3.connect(db_path)
        now = int(time.time())
        conn.execute("""CREATE TABLE wt_active_subprov_sessions
            (id INTEGER PRIMARY KEY, state TEXT, monitoring_state TEXT,
             detected_at INTEGER, expires_at INTEGER)""")
        conn.execute("INSERT INTO wt_active_subprov_sessions VALUES (1,'ACTIVE',NULL,?,?)",
                     (now - 100, now + 3600))
        conn.execute("""CREATE TABLE wt_candidate_websocket_watches
            (id INTEGER PRIMARY KEY, state TEXT, expires_at INTEGER)""")
        conn.execute("""CREATE TABLE wt_ops_v2_armed
            (id INTEGER PRIMARY KEY, state TEXT, armed_at INTEGER, migration_time INTEGER)""")
        conn.execute("""CREATE TABLE wt_watchtower_launches
            (id INTEGER PRIMARY KEY, create_time INTEGER, migrated_at INTEGER)""")
        conn.commit()
        conn.close()

        import src.ops.lifecycle_adapters as la
        monkeypatch.setattr(la, "_ops_db_path", lambda: db_path)
        snap = la.watchtower_lifecycle()
        assert snap.lifecycle_state == "OBSERVING"
        from src.ops.lifecycle import OBSERVING
        assert snap.counts[OBSERVING] == 1

    def test_watchtower_armed_state(self, monkeypatch, tmp_path):
        db_path = self._empty_db(tmp_path)
        conn = sqlite3.connect(db_path)
        now = int(time.time())
        conn.execute("""CREATE TABLE wt_active_subprov_sessions
            (id INTEGER PRIMARY KEY, state TEXT, monitoring_state TEXT,
             detected_at INTEGER, expires_at INTEGER)""")
        conn.execute("""CREATE TABLE wt_candidate_websocket_watches
            (id INTEGER PRIMARY KEY, state TEXT, expires_at INTEGER)""")
        conn.execute("""CREATE TABLE wt_ops_v2_armed
            (id INTEGER PRIMARY KEY, state TEXT, armed_at INTEGER, migration_time INTEGER)""")
        conn.execute("INSERT INTO wt_ops_v2_armed VALUES (1,'ARMED',?,NULL)", (now - 10,))
        conn.execute("""CREATE TABLE wt_watchtower_launches
            (id INTEGER PRIMARY KEY, create_time INTEGER, migrated_at INTEGER)""")
        conn.commit()
        conn.close()

        import src.ops.lifecycle_adapters as la
        monkeypatch.setattr(la, "_ops_db_path", lambda: db_path)
        snap = la.watchtower_lifecycle()
        assert snap.lifecycle_state == "ARMED"
        from src.ops.lifecycle import ARMED
        assert snap.counts[ARMED] == 1

    def test_watchtower_active_state(self, monkeypatch, tmp_path):
        db_path = self._empty_db(tmp_path)
        conn = sqlite3.connect(db_path)
        now = int(time.time())
        conn.execute("""CREATE TABLE wt_active_subprov_sessions
            (id INTEGER PRIMARY KEY, state TEXT, monitoring_state TEXT,
             detected_at INTEGER, expires_at INTEGER)""")
        conn.execute("""CREATE TABLE wt_candidate_websocket_watches
            (id INTEGER PRIMARY KEY, state TEXT, expires_at INTEGER)""")
        conn.execute("""CREATE TABLE wt_ops_v2_armed
            (id INTEGER PRIMARY KEY, state TEXT, armed_at INTEGER, migration_time INTEGER)""")
        conn.execute("""CREATE TABLE wt_watchtower_launches
            (id INTEGER PRIMARY KEY, create_time INTEGER, migrated_at INTEGER)""")
        conn.execute("INSERT INTO wt_watchtower_launches VALUES (1,?,NULL)", (now - 300,))
        conn.commit()
        conn.close()

        import src.ops.lifecycle_adapters as la
        monkeypatch.setattr(la, "_ops_db_path", lambda: db_path)
        snap = la.watchtower_lifecycle()
        assert snap.lifecycle_state == "ACTIVE"

    def test_watchtower_idle_when_empty(self, monkeypatch, tmp_path):
        db_path = self._empty_db(tmp_path)
        conn = sqlite3.connect(db_path)
        conn.execute("""CREATE TABLE wt_active_subprov_sessions
            (id INTEGER PRIMARY KEY, state TEXT, monitoring_state TEXT,
             detected_at INTEGER, expires_at INTEGER)""")
        conn.execute("""CREATE TABLE wt_candidate_websocket_watches
            (id INTEGER PRIMARY KEY, state TEXT, expires_at INTEGER)""")
        conn.execute("""CREATE TABLE wt_ops_v2_armed
            (id INTEGER PRIMARY KEY, state TEXT, armed_at INTEGER, migration_time INTEGER)""")
        conn.execute("""CREATE TABLE wt_watchtower_launches
            (id INTEGER PRIMARY KEY, create_time INTEGER, migrated_at INTEGER)""")
        conn.commit()
        conn.close()

        import src.ops.lifecycle_adapters as la
        monkeypatch.setattr(la, "_ops_db_path", lambda: db_path)
        snap = la.watchtower_lifecycle()
        assert snap.lifecycle_state == "IDLE"
        assert snap.next_expected_state == "OBSERVING"


# ════════════════════════════════════════════════════════════════════════════
# 5. Template smoke-tests
# ════════════════════════════════════════════════════════════════════════════

class TestTemplateLifecycleMarkup:
    def _read(self, name):
        path = os.path.join(ROOT, "templates", name)
        with open(path) as f:
            return f.read()

    def test_mission_control_has_situation_strip_ids(self):
        html = self._read("ops_shell_index.html")
        for el_id in ["sit-observing", "sit-armed", "sit-active", "sit-completed"]:
            assert el_id in html, f"Missing #{el_id} in ops_shell_index.html"

    def test_mission_control_fetches_platform_lifecycle(self):
        html = self._read("ops_shell_index.html")
        assert "/api/ops/lifecycle/platform" in html

    def test_op_page_has_lifecycle_banner(self):
        html = self._read("ops_shell_operation.html")
        # lifecycle inline block (renamed from op-lifecycle-banner in X6)
        assert "op-lc-badge" in html
        assert "OP_ID + '/lifecycle'" in html

    def test_entity_intelligence_has_lifecycle_card(self):
        html = self._read("entity_intelligence.html")
        assert "ei-lifecycle-card" in html
        assert "ei-lifecycle-body" in html
        assert "Current Lifecycle" in html

    def test_css_has_lifecycle_badge_classes(self):
        css_path = os.path.join(ROOT, "static", "css", "intel-platform.css")
        with open(css_path) as f:
            css = f.read()
        for state in ["IDLE", "OBSERVING", "ARMED", "ACTIVE", "COMPLETED", "ARCHIVED"]:
            assert ".ip-lc." + state in css, f"Missing .ip-lc.{state} in intel-platform.css"

    def test_css_has_lc_animations(self):
        css_path = os.path.join(ROOT, "static", "css", "intel-platform.css")
        with open(css_path) as f:
            css = f.read()
        assert "lc-armed-pulse" in css
        assert "lc-active-blink" in css


# ════════════════════════════════════════════════════════════════════════════
# 6. Lifecycle routes (Flask test client)
# ════════════════════════════════════════════════════════════════════════════

class TestLifecycleRoutes:
    @pytest.fixture(autouse=True)
    def _patch_adapters(self, monkeypatch, tmp_path):
        """Patch all adapters to return deterministic IDLE snapshots."""
        import src.ops.lifecycle_adapters as la
        db_path = str(tmp_path / "empty.db")
        sqlite3.connect(db_path).close()
        monkeypatch.setattr(la, "_ops_db_path", lambda: db_path)

    @pytest.fixture
    def client(self):
        from flask import Flask
        from src.ops.lifecycle_routes import lifecycle_bp
        app = Flask(__name__)
        app.register_blueprint(lifecycle_bp)
        app.config["TESTING"] = True
        with app.test_client() as c:
            yield c

    def test_single_op_returns_200(self, client):
        r = client.get("/api/ops/watchtower/lifecycle")
        assert r.status_code == 200
        data = r.get_json()
        assert "lifecycle_state" in data
        assert "state_reason" in data
        assert "counts" in data
        assert "generated_at" in data

    def test_unknown_op_returns_404(self, client):
        r = client.get("/api/ops/unknown-op-xyz/lifecycle")
        assert r.status_code == 404

    def test_platform_summary_returns_200(self, client):
        r = client.get("/api/ops/lifecycle/platform")
        assert r.status_code == 200
        data = r.get_json()
        assert "operations_total" in data
        assert data["operations_total"] == 3
        assert "observing_total" in data
        assert "armed_total" in data
        assert "active_total" in data
        assert "completed_today" in data
        assert "snapshots" in data
        assert len(data["snapshots"]) == 3

    def test_lo_lifecycle_route(self, client):
        r = client.get("/api/ops/launcher-observatory/lifecycle")
        assert r.status_code == 200
        data = r.get_json()
        assert data["operation_id"] == "launcher-observatory"

    def test_bso_lifecycle_route(self, client):
        r = client.get("/api/ops/buy-swarm-observatory/lifecycle")
        assert r.status_code == 200
        data = r.get_json()
        assert data["operation_id"] == "buy-swarm-observatory"

    def test_platform_summary_state_display_present(self, client):
        r = client.get("/api/ops/lifecycle/platform")
        data = r.get_json()
        for snap in data["snapshots"]:
            assert "state_display" in snap
            assert "colour" in snap["state_display"]
            assert "label" in snap["state_display"]
