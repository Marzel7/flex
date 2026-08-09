import json
import sqlite3
from pathlib import Path

import pytest

from scripts.oip_v2_2c3_authority_store import IndexedAuthorityStore, event_id


def build_fixture(tmp_path: Path, *, unknown_family: bool = False):
    canonical = tmp_path / "canonical.sqlite"
    projection = tmp_path / "projection.sqlite"
    db = sqlite3.connect(canonical)
    db.executescript("""
      CREATE TABLE primitive_observations(
        primitive_id TEXT PRIMARY KEY,primitive_type TEXT,primitive_version TEXT,
        subjects_json TEXT,parameters_json TEXT,window_start INTEGER,window_end INTEGER,
        output_payload_json TEXT,output_digest TEXT,quality_state TEXT,
        missing_inputs_json TEXT,failure_state TEXT,generated_at INTEGER);
      CREATE TABLE primitive_evidence_inputs(
        primitive_id TEXT,evidence_id TEXT,PRIMARY KEY(primitive_id,evidence_id));
    """)
    rows = [
      ("event", "UNKNOWN_FAMILY" if unknown_family else "SYSTEM_TRANSFER", ["a", "b"], 1),
      ("timing-old", "BEHAVIOURAL_TIMING", ["a"], 2),
      ("timing-current", "BEHAVIOURAL_TIMING", ["a"], 3),
      ("fresh-old", "WALLET_FRESH_AT_EVENT", ["c"], 2),
      ("fresh-current", "WALLET_FRESH_AT_EVENT", ["c"], 3),
    ]
    db.executemany("INSERT INTO primitive_observations VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", [
      (primitive_id, family, "1", json.dumps(subjects), "{}", generated, generated,
       "{}", primitive_id, "PROVEN", "[]", None, generated)
      for primitive_id, family, subjects, generated in rows])
    db.commit(); db.close()
    db = sqlite3.connect(projection)
    db.execute("""CREATE TABLE primitive_authority(
      primitive_id TEXT PRIMARY KEY,state TEXT,authority_group_json TEXT,
      superseded_by TEXT,reason TEXT,contract_version TEXT)""")
    db.executemany("INSERT INTO primitive_authority VALUES(?,?,?,?,?,?)", [
      ("event", "AUTHORITATIVE", '["event"]', None, "CURRENT", "1.0.0"),
      ("timing-old", "HISTORICAL_SNAPSHOT", '["timing"]', "timing-current", "OLD", "1.0.0"),
      ("timing-current", "AUTHORITATIVE", '["timing"]', None, "CURRENT", "1.0.0"),
      ("fresh-old", "LEGACY_VERSION", '["fresh"]', "fresh-current", "LEGACY", "1.0.0"),
      ("fresh-current", "AUTHORITATIVE", '["fresh"]', None, "CURRENT", "1.0.0"),
    ])
    db.commit(); db.close()
    return canonical, projection


def test_indexed_authority_preserves_history_and_subject_membership(tmp_path):
    canonical, projection = build_fixture(tmp_path)
    store = IndexedAuthorityStore(tmp_path / "authority.sqlite", canonical=canonical)
    try:
        assert store.import_projection(projection) == 5
        assert store.build_subject_index(batch_size=2) == 6
        assert store.ids("ALL_PERSISTED") == (
            "event", "fresh-current", "fresh-old", "timing-current", "timing-old")
        assert store.ids("CURRENT_AUTHORITATIVE") == ("event", "fresh-current", "timing-current")
        assert store.ids("HISTORICAL_SNAPSHOT") == ("timing-old",)
        assert store.ids("LEGACY_VERSION") == ("fresh-old",)
        assert store.ids("CURRENT_AUTHORITATIVE", subjects=("a",)) == ("event", "timing-current")
        assert store.ids("ALL_PERSISTED", subjects=("a",), minimum_subjects=2) == ("event",)
        assert len(store.history("a")) == 3
    finally:
        store.close()


def test_projection_recovers_after_interruption_and_replay_is_idempotent(tmp_path):
    canonical, projection = build_fixture(tmp_path)
    store = IndexedAuthorityStore(tmp_path / "authority.sqlite", canonical=canonical)
    try:
        store.import_projection(projection)
        before = store.connection.execute("SELECT COUNT(*) FROM primitive_authority_events").fetchone()[0]
        store.connection.execute("DELETE FROM current_primitive_authority")
        store.connection.commit()
        assert store.import_projection(projection) == before
        assert store.ids("CURRENT_AUTHORITATIVE") == ("event", "fresh-current", "timing-current")
        assert store.connection.execute("SELECT COUNT(*) FROM primitive_authority_events").fetchone()[0] == before
        with pytest.raises(sqlite3.IntegrityError, match="immutable authority event"):
            store.connection.execute("UPDATE primitive_authority_events SET reason='changed'")
    finally:
        store.close()


def test_recovered_authority_store_can_be_opened_strictly_read_only(tmp_path):
    canonical, projection = build_fixture(tmp_path)
    authority = tmp_path / "authority.sqlite"
    writer = IndexedAuthorityStore(authority, canonical=canonical)
    writer.import_projection(projection); writer.build_subject_index(); writer.close()
    reader = IndexedAuthorityStore(authority, canonical=canonical, read_only=True)
    try:
        assert reader.ids("CURRENT_AUTHORITATIVE") == ("event", "fresh-current", "timing-current")
        assert reader.ids("CURRENT_AUTHORITATIVE", subjects=("a",)) == ("event", "timing-current")
    finally:
        reader.close()


def test_unknown_family_fails_closed(tmp_path):
    canonical, projection = build_fixture(tmp_path, unknown_family=True)
    store = IndexedAuthorityStore(tmp_path / "authority.sqlite", canonical=canonical)
    try:
        with pytest.raises(ValueError, match="unregistered Primitive families"):
            store.import_projection(projection)
    finally:
        store.close()


def test_competing_snapshots_resolve_by_boundary_then_event_id(tmp_path):
    canonical, projection = build_fixture(tmp_path)
    store = IndexedAuthorityStore(tmp_path / "authority.sqlite", canonical=canonical)
    try:
        store.import_projection(projection)
        group_id = store.connection.execute("""SELECT authority_group_id
          FROM primitive_authority_events WHERE primitive_id='timing-current'""").fetchone()[0]
        body = ("timing-next", "BEHAVIOURAL_TIMING", group_id, '["timing"]',
                "AUTHORITATIVE", "timing-next", "NEXT", "1.0.0", "1", 4, 4)
        store.connection.execute(
            "INSERT INTO primitive_authority_events VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (event_id(body), *body))
        store.connection.commit()
        assert store.resolve_competing_events(group_id)["primitive_id"] == "timing-next"
    finally:
        store.close()
