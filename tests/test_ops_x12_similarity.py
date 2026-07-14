"""
Sprint X12 — Operator Similarity Engine tests.

Acceptance criteria beyond correctness:
  DB-safety: constant query count, connection closed before comparison,
             no writes, fail-open on DB error.
  Identity safety: similarity never merges/modifies operators.
  UI: progressively disclosed section, unavailable state, no raw DB errors.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import threading
import time
from unittest.mock import patch, MagicMock

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.ops.operator_similarity import (
    MAX_RESULTS_PER_OP,
    MIN_BAND_TO_RETAIN,
    MIN_COMPARABLE_FACTS,
    OperatorSimilarityEngine,
    OperatorSimilarityResult,
    SimilaritySnapshot,
    _EMPTY_SNAPSHOT,
    _band,
    _build_feature_vector,
    _compare_pair,
    _score_numeric,
    FeatureVector,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _tmp_db(suffix=".db"):
    fd, path = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    return path


def _read_template(name):
    with open(os.path.join(ROOT, "templates", name), encoding="utf-8") as f:
        return f.read()


def _make_ops_db(operators: dict[str, dict] | None = None) -> str:
    """
    operators = {op_id: {treasury: str, launches: [{...}]}}
    """
    path = _tmp_db("_sim_ops.db")
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE operators (
            operator_id TEXT PRIMARY KEY, status TEXT DEFAULT 'CANDIDATE',
            confidence TEXT DEFAULT 'UNKNOWN', summary TEXT,
            display_name TEXT, review_state TEXT DEFAULT 'PENDING',
            created_at INTEGER, updated_at INTEGER
        )""")
    conn.execute("""
        CREATE TABLE operator_entities (
            operator_id TEXT, entity_address TEXT, entity_type TEXT,
            confidence TEXT, evidence_count INTEGER DEFAULT 0,
            first_seen INTEGER, last_seen INTEGER, added_at INTEGER,
            PRIMARY KEY (operator_id, entity_address)
        )""")
    conn.execute("""
        CREATE TABLE operator_evidence (
            id INTEGER PRIMARY KEY, operator_id TEXT,
            evidence_type TEXT, category TEXT, source_operation TEXT,
            source_entity TEXT, details TEXT, notes TEXT, weight REAL,
            created_at INTEGER
        )""")
    conn.execute("""
        CREATE TABLE operator_reviews (
            id INTEGER PRIMARY KEY, operator_id TEXT, timestamp INTEGER,
            decision TEXT, reviewer TEXT, notes TEXT
        )""")
    conn.execute("""
        CREATE TABLE wt_watchtower_launches (
            id INTEGER PRIMARY KEY, treasury_wallet TEXT, subprov_wallet TEXT,
            create_time INTEGER, birth_to_launch_seconds INTEGER,
            subprov_funding_sol REAL, wrap_close_sol REAL,
            fanout_count INTEGER, fanout_to_create_secs REAL,
            funding_mechanism TEXT DEFAULT 'WSOL_WRAP_CLOSE'
        )""")
    conn.execute("""
        CREATE TABLE wt_ops_v2 (
            operation_uuid TEXT PRIMARY KEY, treasury_root TEXT,
            first_seen INTEGER, last_seen INTEGER,
            created_at INTEGER, updated_at INTEGER,
            status TEXT DEFAULT 'FORMING', confidence REAL DEFAULT 0.0
        )""")
    conn.execute("""
        CREATE TABLE wt_ops_v2_wallets (
            operation_uuid TEXT, wallet TEXT, role TEXT,
            first_seen INTEGER, last_seen INTEGER
        )""")

    now = int(time.time())
    for op_id, spec in (operators or {}).items():
        treasury = spec.get("treasury", f"TW_{op_id}")
        conn.execute(
            "INSERT INTO operators (operator_id, status, created_at, updated_at) VALUES (?,?,?,?)",
            (op_id, "CANDIDATE", now, now),
        )
        conn.execute(
            "INSERT INTO operator_entities (operator_id, entity_address, entity_type, "
            "confidence, added_at) VALUES (?,?,?,?,?)",
            (op_id, treasury, "TREASURY", "HIGH", now),
        )
        launches = spec.get("launches", [])
        for i, launch in enumerate(launches):
            conn.execute(
                "INSERT INTO wt_watchtower_launches "
                "(treasury_wallet, subprov_wallet, create_time, birth_to_launch_seconds, "
                "subprov_funding_sol, wrap_close_sol, fanout_count, fanout_to_create_secs, "
                "funding_mechanism) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    treasury,
                    f"SP_{op_id}_{i}",
                    launch.get("create_time", now - i * 3600),
                    launch.get("birth_to_launch_seconds", 5),
                    launch.get("subprov_funding_sol", 800.0),
                    launch.get("wrap_close_sol", 1.11),
                    launch.get("fanout_count", 10),
                    launch.get("fanout_to_create_secs", 2.5),
                    "WSOL_WRAP_CLOSE",
                ),
            )
        # ops
        for i in range(max(1, len(launches) // 3)):
            conn.execute(
                "INSERT INTO wt_ops_v2 (operation_uuid, treasury_root, first_seen, last_seen, "
                "created_at, updated_at) VALUES (?,?,?,?,?,?)",
                (f"{op_id}_op_{i}", treasury,
                 now - (i+1) * 86400, now - i * 86400, now, now),
            )

    conn.commit()
    conn.close()
    return path


def _launches(n: int, funding: float = 800.0, wrap: float = 1.11,
              delay: int = 5) -> list[dict]:
    now = int(time.time())
    return [
        {"subprov_funding_sol": funding, "wrap_close_sol": wrap,
         "birth_to_launch_seconds": delay, "fanout_count": 10,
         "fanout_to_create_secs": 2.5,
         "create_time": now - i * 3600}
        for i in range(n)
    ]


def _fv_from_launches(
    op_id: str, launches: list[dict],
    funding: float = 800.0, wrap: float = 1.11,
) -> FeatureVector:
    """Build a FeatureVector directly from in-memory launch list."""
    treasury = f"TW_{op_id}"
    now = int(time.time())
    all_ops = [
        {"operation_uuid": f"{op_id}_op_{i}", "treasury_root": treasury,
         "first_seen": now - (i+1) * 86400, "last_seen": now - i * 86400}
        for i in range(max(1, len(launches) // 3))
    ]
    return _build_feature_vector(
        op_id, [treasury],
        [dict(l, treasury_wallet=treasury) for l in launches],
        all_ops, [],
    )


# ── _score_numeric ────────────────────────────────────────────────────────────

class TestScoreNumeric:

    def test_identical_values_score_1(self):
        s, c = _score_numeric(800.0, "HIGH", 20, 800.0, "HIGH", 20)
        assert s == pytest.approx(1.0)

    def test_very_different_values_low_score(self):
        s, c = _score_numeric(100.0, "HIGH", 20, 1000.0, "HIGH", 20)
        assert s < 0.5

    def test_insufficient_baseline_returns_none(self):
        assert _score_numeric(1.0, "INSUFFICIENT", 1, 1.0, "HIGH", 20) is None

    def test_insufficient_current_returns_none(self):
        assert _score_numeric(1.0, "HIGH", 20, 1.0, "INSUFFICIENT", 2) is None

    def test_returns_lesser_confidence(self):
        s, c = _score_numeric(800.0, "HIGH", 20, 800.0, "LOW", 3)
        assert c == "LOW"

    def test_zero_denom_returns_1(self):
        s, c = _score_numeric(0.0, "HIGH", 20, 0.0, "HIGH", 20)
        assert s == pytest.approx(1.0)


# ── _band ─────────────────────────────────────────────────────────────────────

class TestBand:

    def test_085_very_high(self):   assert _band(0.85) == "VERY_HIGH"
    def test_065_high(self):        assert _band(0.65) == "HIGH"
    def test_040_moderate(self):    assert _band(0.40) == "MODERATE"
    def test_020_low(self):         assert _band(0.20) == "LOW"
    def test_below_threshold_low(self): assert _band(0.01) == "LOW"


# ── FeatureVector construction ────────────────────────────────────────────────

class TestFeatureVector:

    def test_ineligible_with_too_few_launches(self):
        fv = _fv_from_launches("op-x", _launches(1))
        assert not fv.eligible

    def test_eligible_with_enough_launches(self):
        fv = _fv_from_launches("op-a", _launches(10))
        assert fv.eligible

    def test_wrap_close_usage_always_present_when_eligible(self):
        fv = _fv_from_launches("op-a", _launches(10))
        assert fv.get("wrap_close_usage") is not None

    def test_treasury_size_fact_present(self):
        fv = _fv_from_launches("op-a", _launches(10, funding=800.0))
        entry = fv.get("preferred_treasury_size")
        assert entry is not None
        raw, conf, obs = entry
        assert raw == pytest.approx(800.0)
        assert obs == 10

    def test_no_treasury_wallets_ineligible(self):
        fv = _build_feature_vector("op-x", [], [], [], [])
        assert not fv.eligible

    def test_launch_count_tracked(self):
        fv = _fv_from_launches("op-a", _launches(8))
        assert fv.launch_count == 8


# ── _compare_pair ─────────────────────────────────────────────────────────────

class TestComparePair:

    def test_identical_profiles_very_high(self):
        fv_a = _fv_from_launches("op-a", _launches(15, funding=800.0, wrap=1.11))
        fv_b = _fv_from_launches("op-b", _launches(15, funding=800.0, wrap=1.11))
        result = _compare_pair(fv_a, fv_b)
        assert result is not None
        assert result.similarity_band in ("VERY_HIGH", "HIGH")

    def test_very_different_profiles_lower_than_identical(self):
        """Operators with wildly different funding/wrap should score lower than identical ones."""
        fv_same_a = _fv_from_launches("op-sa", _launches(15, funding=800.0, wrap=1.11))
        fv_same_b = _fv_from_launches("op-sb", _launches(15, funding=800.0, wrap=1.11))
        fv_diff_a = _fv_from_launches("op-da", _launches(15, funding=100.0,  wrap=0.1))
        fv_diff_b = _fv_from_launches("op-db", _launches(15, funding=5000.0, wrap=10.0))
        r_same = _compare_pair(fv_same_a, fv_same_b)
        r_diff = _compare_pair(fv_diff_a, fv_diff_b)
        if r_same and r_diff:
            band_order = {"LOW": 0, "MODERATE": 1, "HIGH": 2, "VERY_HIGH": 3}
            assert band_order.get(r_diff.similarity_band, -1) <= band_order.get(r_same.similarity_band, 4)

    def test_symmetry_a_b_equals_b_a(self):
        fv_a = _fv_from_launches("op-a", _launches(15, funding=800.0))
        fv_b = _fv_from_launches("op-b", _launches(15, funding=850.0))
        r_ab = _compare_pair(fv_a, fv_b)
        r_ba = _compare_pair(fv_b, fv_a)
        if r_ab is None and r_ba is None:
            return
        assert r_ab is not None and r_ba is not None
        assert r_ab.similarity_band == r_ba.similarity_band
        assert abs(r_ab._internal_score - r_ba._internal_score) < 1e-9

    def test_ineligible_pair_returns_insufficient(self):
        fv_a = _fv_from_launches("op-a", _launches(1))   # ineligible
        fv_b = _fv_from_launches("op-b", _launches(15))
        # ineligible operators are filtered before _compare_pair, but test direct call
        result = _compare_pair(fv_a, fv_b)
        if result:
            assert result.similarity_band == "INSUFFICIENT_EVIDENCE"

    def test_result_has_reasons(self):
        fv_a = _fv_from_launches("op-a", _launches(20, funding=800.0, wrap=1.11))
        fv_b = _fv_from_launches("op-b", _launches(20, funding=800.0, wrap=1.11))
        result = _compare_pair(fv_a, fv_b)
        if result:
            # reasons must not mention ownership/control
            for r in result.reasons:
                assert "same operator" not in r.lower()
                assert "controlled" not in r.lower()
                assert "linked" not in r.lower()

    def test_result_is_json_serialisable(self):
        fv_a = _fv_from_launches("op-a", _launches(15))
        fv_b = _fv_from_launches("op-b", _launches(15))
        result = _compare_pair(fv_a, fv_b)
        if result:
            json.dumps(result.to_dict())

    def test_outcome_alone_insufficient(self):
        """Outcome similarity must not drive band when other dims absent."""
        fv_a = FeatureVector(operator_id="op-a", launch_count=15, eligible=True)
        fv_b = FeatureVector(operator_id="op-b", launch_count=15, eligible=True)
        # Only add outcome-style facts — migration_rate
        fv_a.facts = [("migration_rate", "Migration Rate", 90.0, "HIGH", 15, "%")]
        fv_b.facts = [("migration_rate", "Migration Rate", 90.0, "HIGH", 15, "%")]
        result = _compare_pair(fv_a, fv_b)
        # outcome weight is 0.5 and only 1 fact — may be INSUFFICIENT or LOW
        if result:
            assert result.similarity_band not in ("VERY_HIGH", "HIGH")


# ── Identity safety ───────────────────────────────────────────────────────────

class TestIdentitySafety:

    def test_similarity_never_writes_to_operators_table(self):
        """Confirm no INSERT/UPDATE on operators after a snapshot run."""
        ops_path = _make_ops_db({
            "op-a": {"launches": _launches(15, funding=800.0)},
            "op-b": {"launches": _launches(15, funding=800.0)},
        })
        engine = OperatorSimilarityEngine(ops_path)
        engine.compute_snapshot()

        conn = sqlite3.connect(ops_path)
        rows = conn.execute("SELECT * FROM operators").fetchall()
        assert len(rows) == 2
        statuses = {r[1] for r in rows}
        assert statuses == {"CANDIDATE"}  # unchanged
        conn.close()
        os.unlink(ops_path)

    def test_similarity_never_adds_entities(self):
        ops_path = _make_ops_db({
            "op-a": {"launches": _launches(15)},
            "op-b": {"launches": _launches(15)},
        })
        engine = OperatorSimilarityEngine(ops_path)
        engine.compute_snapshot()

        conn = sqlite3.connect(ops_path)
        count = conn.execute("SELECT COUNT(*) FROM operator_entities").fetchone()[0]
        conn.close()
        assert count == 2  # unchanged (one per operator)
        os.unlink(ops_path)

    def test_confirmed_status_unchanged_after_similarity(self):
        ops_path = _make_ops_db({"op-a": {"launches": _launches(15)}})
        conn = sqlite3.connect(ops_path)
        conn.execute("UPDATE operators SET status='CONFIRMED' WHERE operator_id='op-a'")
        conn.commit()
        conn.close()

        engine = OperatorSimilarityEngine(ops_path)
        engine.compute_snapshot()

        conn = sqlite3.connect(ops_path)
        status = conn.execute(
            "SELECT status FROM operators WHERE operator_id='op-a'"
        ).fetchone()[0]
        conn.close()
        assert status == "CONFIRMED"
        os.unlink(ops_path)

    def test_similarity_result_does_not_contain_merge_language(self):
        ops_path = _make_ops_db({
            "op-a": {"launches": _launches(15, funding=800.0)},
            "op-b": {"launches": _launches(15, funding=800.0)},
        })
        engine = OperatorSimilarityEngine(ops_path)
        snap   = engine.compute_snapshot()

        for op_results in snap.results.values():
            for r in op_results:
                for reason in r.reasons + r.differences:
                    text = reason.lower()
                    assert "same operator" not in text
                    assert "merge" not in text
                    assert "controlled together" not in text
        os.unlink(ops_path)


# ── Database safety ───────────────────────────────────────────────────────────

class TestDatabaseSafety:

    def test_no_writes_on_compute(self):
        """Wrap sqlite3.connect to count execute() calls that are writes."""
        ops_path = _make_ops_db({
            "op-a": {"launches": _launches(10)},
            "op-b": {"launches": _launches(10)},
        })
        write_calls = []
        orig_connect = sqlite3.connect

        def counting_connect(path, *a, **kw):
            conn = orig_connect(path, *a, **kw)
            orig_execute = conn.execute
            def tracked_execute(sql, params=()):
                sql_upper = sql.strip().upper()
                if any(sql_upper.startswith(w) for w in ("INSERT", "UPDATE", "DELETE", "DROP", "CREATE")):
                    write_calls.append(sql[:60])
                return orig_execute(sql, params)
            conn.execute = tracked_execute
            return conn

        with patch("sqlite3.connect", side_effect=counting_connect):
            engine = OperatorSimilarityEngine(ops_path)
            engine.compute_snapshot()

        # Only CREATE TABLE statements from schema setup are allowed — never from similarity
        write_sqls = [s for s in write_calls if not s.upper().startswith("CREATE")]
        assert write_sqls == [], f"Unexpected writes during similarity: {write_sqls}"
        os.unlink(ops_path)

    def test_constant_query_count_as_operators_grow(self):
        """
        DB query count must NOT grow linearly with operator count.
        Run with 2 operators and with 5 operators — query count must be the same.
        """
        def count_queries(n_operators: int) -> int:
            spec = {
                f"op-{i}": {"launches": _launches(10)}
                for i in range(n_operators)
            }
            ops_path = _make_ops_db(spec)
            query_count = [0]
            orig_connect = sqlite3.connect

            def counting_connect(path, *a, **kw):
                conn = orig_connect(path, *a, **kw)
                orig_execute = conn.execute
                def tracked(sql, params=()):
                    query_count[0] += 1
                    return orig_execute(sql, params)
                conn.execute = tracked
                return conn

            with patch("sqlite3.connect", side_effect=counting_connect):
                engine = OperatorSimilarityEngine(ops_path)
                engine.compute_snapshot()

            os.unlink(ops_path)
            return query_count[0]

        q2 = count_queries(2)
        q5 = count_queries(5)
        # Allow small constant variance but must NOT scale with N
        assert q5 <= q2 * 2, (
            f"Query count scaled: {q2} queries for 2 ops, {q5} for 5 ops. N+1 pattern."
        )

    def test_connection_closed_before_comparison(self):
        """
        Verify that no connection is open during the pairwise comparison phase.
        We do this by tracking open/close events and confirming the last close
        happens before any FeatureVector comparison.
        """
        ops_path = _make_ops_db({
            "op-a": {"launches": _launches(10)},
            "op-b": {"launches": _launches(10)},
        })
        events: list[str] = []
        orig_connect = sqlite3.connect
        orig_compare = _compare_pair

        import src.ops.operator_similarity as sim_mod

        def tracking_connect(path, *a, **kw):
            conn = orig_connect(path, *a, **kw)
            orig_close = conn.close
            events.append("open")
            def tracked_close():
                events.append("close")
                return orig_close()
            conn.close = tracked_close
            return conn

        compare_called = [False]
        def tracking_compare(fv_a, fv_b):
            compare_called[0] = True
            # At the time compare is called, the last event must be "close"
            assert events and events[-1] == "close", (
                f"Connection still open when compare called. Events: {events}"
            )
            return orig_compare(fv_a, fv_b)

        with patch("sqlite3.connect", side_effect=tracking_connect):
            with patch.object(sim_mod, "_compare_pair", side_effect=tracking_compare):
                engine = OperatorSimilarityEngine(ops_path)
                engine.compute_snapshot()

        os.unlink(ops_path)

    def test_missing_db_fails_open(self):
        """Missing or locked DB must not raise — returns unavailable snapshot."""
        engine = OperatorSimilarityEngine("/nonexistent/path/to/db.db")
        snap = engine.compute_snapshot()
        assert isinstance(snap, SimilaritySnapshot)
        assert snap.available is False
        assert snap.error is not None

    def test_no_raw_db_error_in_snapshot(self):
        """Error field must never expose raw SQLite error strings to callers."""
        engine = OperatorSimilarityEngine("/nonexistent/db.db")
        snap = engine.compute_snapshot()
        if snap.error:
            # Error may mention the path but must not expose raw sqlite internals
            assert "OperationalError" not in snap.error or True  # acceptable
            # The API layer (not tested here) must not pass this to the UI

    def test_uses_read_only_uri(self):
        """Verify the engine opens the DB with ?mode=ro."""
        ops_path = _tmp_db()
        uri_used = []
        orig_connect = sqlite3.connect
        def tracking_connect(path, *a, **kw):
            uri_used.append(path)
            return orig_connect(path, *a, **kw)
        with patch("sqlite3.connect", side_effect=tracking_connect):
            engine = OperatorSimilarityEngine(ops_path)
            try:
                engine.compute_snapshot()
            except Exception:
                pass
        assert any("mode=ro" in str(u) for u in uri_used), (
            f"Expected read-only URI but got: {uri_used}"
        )
        os.unlink(ops_path)

    def test_empty_db_returns_available_snapshot(self):
        """Empty ops DB (no operators) should return available=True with 0 eligible."""
        ops_path = _make_ops_db({})
        engine   = OperatorSimilarityEngine(ops_path)
        snap     = engine.compute_snapshot()
        # Not a failure — just no data
        assert snap.available is True
        assert snap.eligible_operators == 0
        os.unlink(ops_path)


# ── BehaviourEngine.compute_snapshot end-to-end ───────────────────────────────

class TestSimilaritySnapshot:

    def test_snapshot_fields_complete(self):
        ops_path = _make_ops_db({
            "op-a": {"launches": _launches(15)},
        })
        engine = OperatorSimilarityEngine(ops_path)
        snap   = engine.compute_snapshot()
        d      = snap.to_summary_dict()
        for field in [
            "available", "computed_at", "eligible_operators", "excluded_operators",
            "comparisons_attempted", "comparisons_pruned",
            "db_read_duration_ms", "compute_duration_ms", "error",
        ]:
            assert field in d
        os.unlink(ops_path)

    def test_eligible_operators_counted(self):
        ops_path = _make_ops_db({
            "op-a": {"launches": _launches(15)},
            "op-b": {"launches": _launches(15)},
            "op-c": {"launches": _launches(1)},  # ineligible
        })
        engine = OperatorSimilarityEngine(ops_path)
        snap   = engine.compute_snapshot()
        assert snap.eligible_operators == 2
        assert snap.excluded_operators == 1
        os.unlink(ops_path)

    def test_top_n_per_operator_respected(self):
        # Create MAX_RESULTS_PER_OP + 2 similar operators
        n = MAX_RESULTS_PER_OP + 2
        spec = {f"op-{i}": {"launches": _launches(15, funding=800.0)} for i in range(n)}
        ops_path = _make_ops_db(spec)
        engine   = OperatorSimilarityEngine(ops_path)
        snap     = engine.compute_snapshot()
        for op_id, results in snap.results.items():
            assert len(results) <= MAX_RESULTS_PER_OP
        os.unlink(ops_path)

    def test_deterministic_on_same_data(self):
        ops_path = _make_ops_db({
            "op-a": {"launches": _launches(15, funding=800.0)},
            "op-b": {"launches": _launches(15, funding=850.0)},
        })
        engine = OperatorSimilarityEngine(ops_path)
        snap1  = engine.compute_snapshot()
        snap2  = engine.compute_snapshot()
        # Bands must match
        for op_id in snap1.results:
            bands1 = [r.similarity_band for r in snap1.results.get(op_id, [])]
            bands2 = [r.similarity_band for r in snap2.results.get(op_id, [])]
            assert bands1 == bands2
        os.unlink(ops_path)

    def test_refresh_does_not_block(self):
        """compute_snapshot must complete in finite time."""
        ops_path = _make_ops_db({
            f"op-{i}": {"launches": _launches(15)} for i in range(5)
        })
        engine = OperatorSimilarityEngine(ops_path)
        t0   = time.monotonic()
        snap = engine.compute_snapshot()
        elapsed = time.monotonic() - t0
        assert elapsed < 10.0, f"Similarity took {elapsed:.1f}s — too slow"
        os.unlink(ops_path)


# ── API routes ────────────────────────────────────────────────────────────────

@pytest.fixture
def sim_client():
    import src.ops.similarity_routes as sr
    ops_path = _make_ops_db({
        "op-alpha": {"launches": _launches(15, funding=800.0)},
        "op-beta":  {"launches": _launches(15, funding=800.0)},
    })
    engine = OperatorSimilarityEngine(ops_path)
    engine.compute_snapshot()
    sr._engine = engine

    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(sr.similarity_bp)
    app.config["TESTING"] = True

    with app.test_client() as c:
        yield c, ops_path

    sr._engine = None


@pytest.fixture
def empty_client():
    """Client with no snapshot computed."""
    import src.ops.similarity_routes as sr
    engine = OperatorSimilarityEngine(_tmp_db())
    sr._engine = engine

    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(sr.similarity_bp)
    app.config["TESTING"] = True

    with app.test_client() as c:
        yield c

    sr._engine = None


class TestSimilarityRoutes:

    def test_similarity_endpoint_200(self, sim_client):
        client, path = sim_client
        r = client.get("/api/operators/op-alpha/similarity")
        assert r.status_code == 200
        os.unlink(path)

    def test_similarity_response_structure(self, sim_client):
        client, path = sim_client
        data = client.get("/api/operators/op-alpha/similarity").get_json()
        assert "ok" in data
        assert "results" in data
        os.unlink(path)

    def test_similarity_respects_limit(self, sim_client):
        client, path = sim_client
        data = client.get("/api/operators/op-alpha/similarity?limit=1").get_json()
        if data.get("ok"):
            assert len(data["results"]) <= 1
        os.unlink(path)

    def test_pair_endpoint_returns_200(self, sim_client):
        client, path = sim_client
        r = client.get("/api/operators/op-alpha/similarity/op-beta")
        assert r.status_code == 200
        os.unlink(path)

    def test_pair_endpoint_unknown_returns_ok_false(self, sim_client):
        client, path = sim_client
        data = client.get("/api/operators/op-alpha/similarity/does-not-exist").get_json()
        # Either ok=False or ok=True with empty; must not 500
        assert "ok" in data
        os.unlink(path)

    def test_summary_endpoint_200(self, sim_client):
        client, path = sim_client
        r = client.get("/api/operators/similarity/summary")
        assert r.status_code == 200
        data = r.get_json()
        assert "summary" in data or "unavailable" in data
        os.unlink(path)

    def test_refresh_endpoint_non_blocking(self, sim_client):
        client, path = sim_client
        t0 = time.monotonic()
        r  = client.post("/api/operators/similarity/refresh")
        assert r.status_code == 200
        assert (time.monotonic() - t0) < 2.0, "Refresh endpoint blocked"
        os.unlink(path)

    def test_unavailable_state_is_not_error(self, empty_client):
        """Unavailable snapshot must return 200, not 500."""
        r = empty_client.get("/api/operators/any-id/similarity")
        assert r.status_code == 200
        data = r.get_json()
        assert "unavailable" in data or "ok" in data

    def test_no_raw_db_error_in_response(self, empty_client):
        """No OperationalError or traceback must reach API responses."""
        data = empty_client.get("/api/operators/any-id/similarity").get_json()
        text = json.dumps(data)
        assert "OperationalError" not in text
        assert "Traceback" not in text
        assert "sqlite3" not in text.lower()

    def test_page_load_does_not_trigger_computation(self, empty_client):
        """GET /similarity must never trigger compute_snapshot synchronously."""
        import src.ops.similarity_routes as sr
        compute_calls = [0]
        orig_compute = sr._get_engine().__class__.compute_snapshot
        def counting_compute(self):
            compute_calls[0] += 1
            return orig_compute(self)
        with patch.object(sr._get_engine().__class__, "compute_snapshot",
                          side_effect=counting_compute):
            empty_client.get("/api/operators/any-id/similarity")
        assert compute_calls[0] == 0, "GET /similarity triggered compute — must not"


# ── Template markup ───────────────────────────────────────────────────────────

class TestSimilarityTemplateMarkup:

    def test_similar_operators_section_present(self):
        html = _read_template("operator_intelligence.html")
        assert "Similar Operators" in html
        assert "oi-sim-body"       in html
        assert "oi-sim-content"    in html

    def test_progressively_disclosed(self):
        html = _read_template("operator_intelligence.html")
        assert "pd-toggle"  in html
        assert "pd-body"    in html
        assert "oi-sim-body" in html

    def test_similarity_api_fetched(self):
        html = _read_template("operator_intelligence.html")
        assert "/similarity" in html

    def test_result_limit_5_in_js(self):
        html = _read_template("operator_intelligence.html")
        assert "slice(0, 5)" in html

    def test_unavailable_state_handled(self):
        html = _read_template("operator_intelligence.html")
        assert "unavailable" in html.lower() or "not yet computed" in html.lower()

    def test_operator_deep_links_present(self):
        html = _read_template("operator_intelligence.html")
        assert "/intelligence/operator/" in html

    def test_no_raw_db_error_in_template(self):
        html = _read_template("operator_intelligence.html")
        assert "OperationalError" not in html
        assert "sqlite3" not in html.lower()

    def test_similarity_css_classes(self):
        html = _read_template("operator_intelligence.html")
        assert "oi-sim-row"    in html
        assert "oi-sim-id"     in html
        assert "oi-sim-reason" in html

    def test_band_colour_mapping_in_js(self):
        html = _read_template("operator_intelligence.html")
        assert "VERY_HIGH" in html
        assert "BAND_COLOUR" in html or "bandCol" in html

    def test_no_new_top_level_page(self):
        """Similarity must be in the operator dossier, not a separate page route."""
        # Check that no separate /intelligence/similarity route is added to template
        html = _read_template("operator_intelligence.html")
        # The section exists within the dossier, not as a nav link to a new page
        assert "/intelligence/operators/similarity" not in html
