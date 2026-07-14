"""
Sprint X7 — Analyst Inbox tests.

Covers:
  - InboxItem validation (priority/status/subject_type constraints)
  - InboxStore upsert semantics (deduplication, status preservation)
  - InboxStore set_status + fetch_summary
  - inbox_adapters.refresh_inbox (happy path + adapter error isolation)
  - API route responses (summary, all items, per-op items, status update, refresh)
  - Template markup (inbox.html, Mission Control attention panel, op page attention section)
"""

from __future__ import annotations

import sqlite3
import time
import types
import json
import pytest


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_item(**overrides):
    from src.ops.inbox import InboxItem, HIGH, SUBJECT_LIFECYCLE
    defaults = dict(
        operation_id="watchtower",
        subject_type=SUBJECT_LIFECYCLE,
        priority=HIGH,
        headline="Test headline",
        summary="Test summary",
        reason="Test reason",
        recommended_action="Do something",
        dedup_key="test:dedup_key",
    )
    defaults.update(overrides)
    return InboxItem(**defaults)


def _in_memory_store(tmp_path=None):
    import tempfile, os
    from src.ops.inbox import InboxStore
    if tmp_path is None:
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
    else:
        path = str(tmp_path / "inbox_test.db")
    return InboxStore(path)


# ── InboxItem validation ──────────────────────────────────────────────────────

class TestInboxItemValidation:

    def test_valid_item_creates_ok(self):
        item = _make_item()
        assert item.headline == "Test headline"
        assert item.priority == "HIGH"
        assert item.status == "NEW"

    def test_invalid_priority_raises(self):
        from src.ops.inbox import InboxItem, SUBJECT_LIFECYCLE
        with pytest.raises(ValueError, match="priority"):
            InboxItem(
                operation_id="watchtower",
                subject_type=SUBJECT_LIFECYCLE,
                priority="URGENT",           # invalid
                headline="h",
                summary="s",
                reason="r",
                recommended_action="a",
                dedup_key="k",
            )

    def test_invalid_status_raises(self):
        from src.ops.inbox import InboxItem, HIGH, SUBJECT_LIFECYCLE
        with pytest.raises(ValueError, match="status"):
            InboxItem(
                operation_id="watchtower",
                subject_type=SUBJECT_LIFECYCLE,
                priority=HIGH,
                headline="h",
                summary="s",
                reason="r",
                recommended_action="a",
                dedup_key="k",
                status="DISCARDED",          # invalid
            )

    def test_invalid_subject_type_raises(self):
        from src.ops.inbox import InboxItem, HIGH
        with pytest.raises(ValueError, match="subject_type"):
            InboxItem(
                operation_id="watchtower",
                subject_type="UNKNOWN_TYPE", # invalid
                priority=HIGH,
                headline="h",
                summary="s",
                reason="r",
                recommended_action="a",
                dedup_key="k",
            )

    def test_to_dict_includes_priority_rank(self):
        item = _make_item()
        d = item.to_dict()
        assert "priority_rank" in d
        assert isinstance(d["priority_rank"], int)

    def test_all_valid_priorities_accepted(self):
        from src.ops.inbox import CRITICAL, HIGH, MEDIUM, LOW, INFO, SUBJECT_LIFECYCLE
        for p in (CRITICAL, HIGH, MEDIUM, LOW, INFO):
            item = _make_item(priority=p, dedup_key=f"test:{p}")
            assert item.priority == p

    def test_all_valid_statuses_accepted(self):
        from src.ops.inbox import NEW, ACKNOWLEDGED, IN_PROGRESS, RESOLVED, EXPIRED, SUBJECT_LIFECYCLE
        for s in (NEW, ACKNOWLEDGED, IN_PROGRESS, RESOLVED, EXPIRED):
            item = _make_item(status=s, dedup_key=f"test:{s}")
            assert item.status == s


# ── InboxStore upsert & deduplication ────────────────────────────────────────

class TestInboxStoreUpsert:

    def test_upsert_new_item_stored(self):
        store = _in_memory_store()
        item = _make_item()
        store.upsert(item)
        rows = store.fetch_active()
        assert len(rows) == 1
        assert rows[0]["headline"] == "Test headline"

    def test_upsert_same_dedup_key_updates_content(self):
        store = _in_memory_store()
        store.upsert(_make_item(headline="Original"))
        store.upsert(_make_item(headline="Updated"))
        rows = store.fetch_active()
        assert len(rows) == 1
        assert rows[0]["headline"] == "Updated"

    def test_upsert_same_dedup_key_preserves_analyst_status(self):
        store = _in_memory_store()
        store.upsert(_make_item(headline="First"))
        rows = store.fetch_active()
        item_id = rows[0]["item_id"]
        store.set_status(item_id, "ACKNOWLEDGED")
        # Re-upsert with same dedup_key
        store.upsert(_make_item(headline="Second"))
        rows2 = store.fetch_active()
        assert len(rows2) == 1
        assert rows2[0]["status"] == "ACKNOWLEDGED"
        assert rows2[0]["headline"] == "Second"

    def test_upsert_same_dedup_key_preserves_created_at(self):
        store = _in_memory_store()
        store.upsert(_make_item())
        rows1 = store.fetch_active()
        created_1 = rows1[0]["created_at"]
        time.sleep(0.05)
        store.upsert(_make_item(summary="New summary"))
        rows2 = store.fetch_active()
        assert rows2[0]["created_at"] == created_1

    def test_different_dedup_keys_create_separate_items(self):
        store = _in_memory_store()
        store.upsert(_make_item(dedup_key="k1"))
        store.upsert(_make_item(dedup_key="k2"))
        assert len(store.fetch_active()) == 2

    def test_resolved_items_excluded_from_fetch_active(self):
        store = _in_memory_store()
        store.upsert(_make_item())
        rows = store.fetch_active()
        store.set_status(rows[0]["item_id"], "RESOLVED")
        assert store.fetch_active() == []

    def test_fetch_active_filters_by_operation(self):
        store = _in_memory_store()
        store.upsert(_make_item(operation_id="watchtower",           dedup_key="a"))
        store.upsert(_make_item(operation_id="launcher-observatory", dedup_key="b"))
        wt_items = store.fetch_active(operation_id="watchtower")
        assert len(wt_items) == 1
        assert wt_items[0]["operation_id"] == "watchtower"


# ── InboxStore set_status & summary ──────────────────────────────────────────

class TestInboxStoreStatus:

    def test_set_status_returns_true_on_success(self):
        store = _in_memory_store()
        store.upsert(_make_item())
        item_id = store.fetch_active()[0]["item_id"]
        assert store.set_status(item_id, "ACKNOWLEDGED") is True

    def test_set_status_returns_false_for_unknown_id(self):
        store = _in_memory_store()
        assert store.set_status("nonexistent-id", "RESOLVED") is False

    def test_fetch_summary_counts_correctly(self):
        from src.ops.inbox import ACKNOWLEDGED
        store = _in_memory_store()
        store.upsert(_make_item(dedup_key="k1"))
        store.upsert(_make_item(dedup_key="k2"))
        rows = store.fetch_active()
        store.set_status(rows[0]["item_id"], ACKNOWLEDGED)
        summary = store.fetch_summary()
        assert summary["new"]          == 1
        assert summary["acknowledged"] == 1
        assert summary["total_active"] == 2

    def test_fetch_summary_includes_top_item(self):
        store = _in_memory_store()
        store.upsert(_make_item(dedup_key="only"))
        summary = store.fetch_summary()
        assert summary["top_item"] is not None
        assert summary["top_item"]["headline"] == "Test headline"

    def test_fetch_summary_top_item_none_when_empty(self):
        store = _in_memory_store()
        summary = store.fetch_summary()
        assert summary["top_item"] is None


# ── inbox_adapters ────────────────────────────────────────────────────────────

class TestInboxAdapters:

    def _make_snap(self, op_id, state, counts=None, confidence=0.75, last_ts=None):
        from src.ops.lifecycle import LifecycleSnapshot
        return LifecycleSnapshot(
            operation_id=op_id,
            display_name=op_id,
            lifecycle_state=state,
            counts=counts or {},
            state_reason="test",
            last_transition_at=last_ts or int(time.time()) - 60,
            confidence=confidence,
            next_expected_state=None,
            generated_at=int(time.time()),
        )

    def test_watchtower_active_launches_emits_high_item(self):
        from src.ops.inbox_adapters import _watchtower_items
        from src.ops.lifecycle import ACTIVE
        snap = self._make_snap("watchtower", ACTIVE, counts={"ACTIVE": 2})
        items = _watchtower_items(snap)
        assert any(i.priority == "HIGH" and "launch" in i.headline.lower() for i in items)

    def test_watchtower_armed_emits_item(self):
        from src.ops.inbox_adapters import _watchtower_items
        from src.ops.lifecycle import ARMED
        snap = self._make_snap("watchtower", ARMED, counts={"ARMED": 3}, confidence=0.8)
        items = _watchtower_items(snap)
        assert any("armed" in i.headline.lower() for i in items)

    def test_watchtower_armed_high_confidence_is_high_priority(self):
        from src.ops.inbox_adapters import _watchtower_items
        from src.ops.lifecycle import ARMED
        snap = self._make_snap("watchtower", ARMED, counts={"ARMED": 1}, confidence=0.9)
        items = _watchtower_items(snap)
        armed_item = next((i for i in items if "armed" in i.headline.lower()), None)
        assert armed_item is not None
        assert armed_item.priority == "HIGH"

    def test_watchtower_armed_low_confidence_is_medium_priority(self):
        from src.ops.inbox_adapters import _watchtower_items
        from src.ops.lifecycle import ARMED
        snap = self._make_snap("watchtower", ARMED, counts={"ARMED": 1}, confidence=0.4)
        items = _watchtower_items(snap)
        armed_item = next((i for i in items if "armed" in i.headline.lower()), None)
        assert armed_item is not None
        assert armed_item.priority == "MEDIUM"

    def test_refresh_inbox_returns_count(self):
        from src.ops.inbox_adapters import refresh_inbox
        store = _in_memory_store()

        # Patch get_all_lifecycles to return a simple IDLE snap
        from src.ops.lifecycle import LifecycleSnapshot, IDLE
        import src.ops.inbox_adapters as ia
        original = ia.get_all_lifecycles
        try:
            ia.get_all_lifecycles = lambda: [
                LifecycleSnapshot("watchtower", "WATCHTOWER", IDLE, "idle", {}, None, None, None, int(time.time()))
            ]
            count = refresh_inbox(store)
        finally:
            ia.get_all_lifecycles = original

        assert isinstance(count, int)
        assert count >= 0

    def test_refresh_inbox_adapter_error_does_not_raise(self):
        from src.ops.inbox_adapters import refresh_inbox
        import src.ops.inbox_adapters as ia
        from src.ops.lifecycle import LifecycleSnapshot, IDLE
        store = _in_memory_store()
        # Inject a broken builder that raises
        original_builders = dict(ia._ITEM_BUILDERS)
        try:
            ia._ITEM_BUILDERS = {"watchtower": lambda snap: 1 / 0}  # ZeroDivisionError
            ia.get_all_lifecycles = lambda: [
                LifecycleSnapshot("watchtower", "WT", IDLE, "", {}, None, None, None, int(time.time()))
            ]
            count = refresh_inbox(store)  # must not raise
            assert count == 0
        finally:
            ia._ITEM_BUILDERS = original_builders
            import importlib, src.ops.inbox_adapters
            importlib.reload(src.ops.inbox_adapters)


# ── API routes ────────────────────────────────────────────────────────────────

@pytest.fixture
def client():
    import sys, os, tempfile
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if root not in sys.path:
        sys.path.insert(0, root)

    from flask import Flask
    import src.ops.inbox_routes as ir

    # Point inbox store at a temp DB
    fd, db_path = tempfile.mkstemp(suffix="_inbox_test.db")
    os.close(fd)

    from src.ops.inbox import InboxStore
    ir._store = InboxStore(db_path)

    app = Flask(__name__, template_folder=os.path.join(root, "templates"),
                static_folder=os.path.join(root, "static"))
    app.register_blueprint(ir.inbox_bp)
    app.config["TESTING"] = True

    with app.test_client() as c:
        yield c

    try:
        os.unlink(db_path)
    except OSError:
        pass


class TestInboxRoutes:

    def test_inbox_summary_returns_ok(self, client):
        r = client.get("/api/ops/inbox/summary")
        assert r.status_code == 200
        data = r.get_json()
        assert data.get("ok") is True
        assert "new" in data
        assert "total_active" in data

    def test_inbox_all_returns_items_list(self, client):
        r = client.get("/api/ops/inbox")
        assert r.status_code == 200
        data = r.get_json()
        assert data.get("ok") is True
        assert isinstance(data.get("items"), list)

    def test_inbox_per_op_returns_ok(self, client):
        r = client.get("/api/ops/inbox/watchtower")
        assert r.status_code == 200
        data = r.get_json()
        assert data.get("ok") is True
        assert data.get("operation") == "watchtower"

    def test_inbox_refresh_returns_ok(self, client):
        r = client.post("/api/ops/inbox/refresh")
        assert r.status_code == 200
        data = r.get_json()
        assert data.get("ok") is True
        assert "items_written" in data

    def test_inbox_status_invalid_returns_400(self, client):
        r = client.post(
            "/api/ops/inbox/fake-id/status",
            data=json.dumps({"status": "INVALID_STATUS"}),
            content_type="application/json",
        )
        assert r.status_code == 400
        data = r.get_json()
        assert data.get("ok") is False

    def test_inbox_page_renders(self, client):
        r = client.get("/intelligence/inbox")
        assert r.status_code == 200
        html = r.data.decode()
        assert "Analyst Inbox" in html


# ── Template markup ───────────────────────────────────────────────────────────

def _read_template(name):
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "templates", name), encoding="utf-8") as f:
        return f.read()


class TestInboxTemplateMarkup:

    def test_inbox_page_has_strip_ids(self):
        html = _read_template("inbox.html")
        assert "strip-new"  in html
        assert "strip-ack"  in html
        assert "strip-ip"   in html
        assert "strip-res"  in html

    def test_inbox_page_has_filter_buttons(self):
        html = _read_template("inbox.html")
        assert "CRITICAL"         in html
        assert "MEDIUM"           in html
        assert "inbox-filter-btn" in html

    def test_inbox_page_has_inbox_list(self):
        html = _read_template("inbox.html")
        assert "inbox-list"  in html

    def test_mission_control_has_attention_section(self):
        html = _read_template("ops_shell_index.html")
        assert "mc-attn"             in html
        assert "Open Inbox"          in html
        assert "/intelligence/inbox" in html

    def test_mission_control_fetches_inbox_summary(self):
        html = _read_template("ops_shell_index.html")
        assert "/api/ops/inbox/summary" in html

    def test_op_page_has_inbox_section(self):
        html = _read_template("ops_shell_operation.html")
        assert "op-inbox-section"  in html
        assert "/api/ops/inbox/"   in html

    def test_inbox_page_has_api_calls(self):
        html = _read_template("inbox.html")
        assert "/api/ops/inbox"         in html
        assert "/api/ops/inbox/summary" in html
        assert "/api/ops/inbox/refresh" in html

    def test_inbox_page_renders_via_client(self, client):
        r = client.get("/intelligence/inbox")
        assert r.status_code == 200
        assert b"Analyst Inbox" in r.data
