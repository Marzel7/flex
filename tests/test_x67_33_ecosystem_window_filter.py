"""X67.33 -- Make Ecosystem Exchange Interaction window-aware.

X67.32 established the root cause: list_ecosystem_exchange_interactions()
had no window parameter, its query had no WHERE clause, the API accepted no
query parameters, and the frontend never sent (or re-sent) a window. This
verifies the fix: funding_time-based filtering, "All" omitting the WHERE
predicate entirely (not an artificial huge interval), API validation, and
summary-count equivalence with the filtered population.
"""
import sqlite3
import time

import pytest
from flask import Flask

from src.core import operation_dashboard_routes as odr
from src.ops import ecosystem_intelligence as eco


NOW = 1785065905


def _make_ops_db(tmp_path, rows):
    path = str(tmp_path / f"ops_{time.time_ns()}.db")
    conn = sqlite3.connect(path)
    conn.execute(eco.SCHEMA_SQL)
    for r in rows:
        conn.execute(
            "INSERT INTO wt_ecosystem_exchange_interactions "
            "(mint, treasury_wallet, exchange_wallet, exchange_name, exchange_category, "
            " creator_wallet, funding_mechanism, funding_signature, funding_amount, "
            " funding_time, walkback_confidence, walkback_completed_at, walkback_evidence_json, "
            " discovery_source, reclassified_at, reclassification_reason) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                r["mint"], r.get("treasury_wallet", "TreasuryA"), r.get("exchange_wallet", "ExchangeA"),
                r.get("exchange_name", "WhiteBIT"), r.get("exchange_category", "cex"),
                r.get("creator_wallet", "CreatorA"), r.get("funding_mechanism", "WSOL_WRAP_CLOSE"),
                r.get("funding_signature", "sig"), r.get("funding_amount", 1.5),
                r.get("funding_time"), r.get("walkback_confidence", "HIGH"),
                r.get("walkback_completed_at", NOW), r.get("walkback_evidence_json", "{}"),
                r.get("discovery_source", "SUBPROV_REACTIVATED"), r.get("reclassified_at", NOW),
                r.get("reclassification_reason", "KNOWN_INFRASTRUCTURE_REGISTRY_MATCH"),
            ),
        )
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def app_for(monkeypatch, tmp_path):
    def _build(rows):
        db_path = _make_ops_db(tmp_path, rows)
        # X67.33's route calls list_ecosystem_exchange_interactions() without
        # passing ops_db_path, so it reads ecosystem_intelligence's OWN
        # module-level OPS_DB_PATH default (independent of
        # operation_dashboard_routes.OPS_DB_PATH) -- both point at the same
        # real path in production via OPS_V2_DB_PATH, but tests must patch
        # the one actually consulted.
        monkeypatch.setattr(odr, "OPS_DB_PATH", db_path)
        monkeypatch.setattr(eco, "OPS_DB_PATH", db_path)
        app = Flask(__name__)
        app.register_blueprint(odr.ops_dashboard_bp)
        return app, db_path
    return _build


# ── 1. list_ecosystem_exchange_interactions() unit-level filtering ──────────

class TestListEcosystemExchangeInteractionsFiltering:
    def test_24h_filter_returns_recent_row(self, tmp_path, monkeypatch):
        monkeypatch.setattr(eco.time, "time", lambda: NOW)
        db_path = _make_ops_db(tmp_path, [
            {"mint": "Recent", "funding_time": NOW - 100},
        ])
        rows = eco.list_ecosystem_exchange_interactions(db_path, window_seconds=86400)
        assert [r["mint"] for r in rows] == ["Recent"]

    def test_24h_filter_excludes_older_row(self, tmp_path, monkeypatch):
        monkeypatch.setattr(eco.time, "time", lambda: NOW)
        db_path = _make_ops_db(tmp_path, [
            {"mint": "Recent", "funding_time": NOW - 100},
            {"mint": "EightDaysOld", "funding_time": NOW - 8 * 86400},
        ])
        rows = eco.list_ecosystem_exchange_interactions(db_path, window_seconds=86400)
        mints = {r["mint"] for r in rows}
        assert mints == {"Recent"}

    def test_all_view_returns_both_recent_and_historical(self, tmp_path, monkeypatch):
        monkeypatch.setattr(eco.time, "time", lambda: NOW)
        db_path = _make_ops_db(tmp_path, [
            {"mint": "Recent", "funding_time": NOW - 100},
            {"mint": "EightDaysOld", "funding_time": NOW - 8 * 86400},
            {"mint": "TwelveDaysOld", "funding_time": NOW - 12 * 86400},
        ])
        rows = eco.list_ecosystem_exchange_interactions(db_path, window_seconds=None)
        mints = {r["mint"] for r in rows}
        assert mints == {"Recent", "EightDaysOld", "TwelveDaysOld"}

    def test_zero_or_negative_window_seconds_means_all(self, tmp_path, monkeypatch):
        monkeypatch.setattr(eco.time, "time", lambda: NOW)
        db_path = _make_ops_db(tmp_path, [
            {"mint": "EightDaysOld", "funding_time": NOW - 8 * 86400},
        ])
        assert len(eco.list_ecosystem_exchange_interactions(db_path, window_seconds=0)) == 1
        assert len(eco.list_ecosystem_exchange_interactions(db_path, window_seconds=-5)) == 1

    def test_boundary_row_exactly_at_cutoff_is_included(self, tmp_path, monkeypatch):
        """funding_time >= cutoff (inclusive), matching the task's stated
        semantics ("funding_time >= current_time - 86400")."""
        monkeypatch.setattr(eco.time, "time", lambda: NOW)
        db_path = _make_ops_db(tmp_path, [
            {"mint": "ExactlyAtCutoff", "funding_time": NOW - 86400},
        ])
        rows = eco.list_ecosystem_exchange_interactions(db_path, window_seconds=86400)
        assert [r["mint"] for r in rows] == ["ExactlyAtCutoff"]

    def test_boundary_row_one_second_past_cutoff_is_excluded(self, tmp_path, monkeypatch):
        monkeypatch.setattr(eco.time, "time", lambda: NOW)
        db_path = _make_ops_db(tmp_path, [
            {"mint": "JustPastCutoff", "funding_time": NOW - 86400 - 1},
        ])
        rows = eco.list_ecosystem_exchange_interactions(db_path, window_seconds=86400)
        assert rows == []

    def test_default_window_seconds_none_is_unfiltered(self, tmp_path, monkeypatch):
        """No window_seconds argument at all -- default behaviour must match
        explicit None (both mean 'return everything'), not 24h."""
        monkeypatch.setattr(eco.time, "time", lambda: NOW)
        db_path = _make_ops_db(tmp_path, [
            {"mint": "EightDaysOld", "funding_time": NOW - 8 * 86400},
        ])
        assert len(eco.list_ecosystem_exchange_interactions(db_path)) == 1


# ── 2. API route: parameter handling + validation ───────────────────────────

class TestApiWindowSecondsValidation:
    def test_valid_24h_request_filters(self, app_for, monkeypatch):
        monkeypatch.setattr(eco.time, "time", lambda: NOW)
        app, db_path = app_for([
            {"mint": "Recent", "funding_time": NOW - 100},
            {"mint": "Old", "funding_time": NOW - 9 * 86400},
        ])
        with app.test_client() as c:
            resp = c.get("/api/ops-v2/ecosystem-exchange-interactions?window_seconds=86400")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        assert body["total"] == 1
        assert body["interactions"][0]["mint"] == "Recent"

    def test_all_request_no_param_returns_everything(self, app_for, monkeypatch):
        monkeypatch.setattr(eco.time, "time", lambda: NOW)
        app, db_path = app_for([
            {"mint": "Recent", "funding_time": NOW - 100},
            {"mint": "Old", "funding_time": NOW - 9 * 86400},
        ])
        with app.test_client() as c:
            resp = c.get("/api/ops-v2/ecosystem-exchange-interactions")
        body = resp.get_json()
        assert body["total"] == 2

    def test_explicit_zero_window_seconds_returns_everything(self, app_for, monkeypatch):
        monkeypatch.setattr(eco.time, "time", lambda: NOW)
        app, db_path = app_for([
            {"mint": "Recent", "funding_time": NOW - 100},
            {"mint": "Old", "funding_time": NOW - 9 * 86400},
        ])
        with app.test_client() as c:
            resp = c.get("/api/ops-v2/ecosystem-exchange-interactions?window_seconds=0")
        assert resp.get_json()["total"] == 2

    def test_invalid_window_seconds_returns_400(self, app_for):
        app, db_path = app_for([{"mint": "Recent", "funding_time": NOW}])
        with app.test_client() as c:
            resp = c.get("/api/ops-v2/ecosystem-exchange-interactions?window_seconds=not-a-number")
        assert resp.status_code == 400
        body = resp.get_json()
        assert body["ok"] is False


# ── 3. Summary-count equivalence with the filtered population ───────────────

class TestSummaryCountEquivalence:
    def test_total_and_interactions_length_match_filtered_rows(self, app_for, monkeypatch):
        monkeypatch.setattr(eco.time, "time", lambda: NOW)
        app, db_path = app_for([
            {"mint": "Recent1", "funding_time": NOW - 50, "exchange_name": "WhiteBIT"},
            {"mint": "Recent2", "funding_time": NOW - 60, "exchange_name": "Binance"},
            {"mint": "Old1", "funding_time": NOW - 10 * 86400, "exchange_name": "WhiteBIT"},
        ])
        with app.test_client() as c:
            resp = c.get("/api/ops-v2/ecosystem-exchange-interactions?window_seconds=86400")
        body = resp.get_json()
        assert body["total"] == len(body["interactions"]) == 2
        exchange_names = {r["exchange_name"] for r in body["interactions"]}
        assert exchange_names == {"WhiteBIT", "Binance"}
        assert "Old1" not in {r["mint"] for r in body["interactions"]}

    def test_all_view_totals_reflect_full_population(self, app_for, monkeypatch):
        monkeypatch.setattr(eco.time, "time", lambda: NOW)
        app, db_path = app_for([
            {"mint": "Recent1", "funding_time": NOW - 50},
            {"mint": "Old1", "funding_time": NOW - 10 * 86400},
            {"mint": "Old2", "funding_time": NOW - 20 * 86400},
        ])
        with app.test_client() as c:
            resp = c.get("/api/ops-v2/ecosystem-exchange-interactions")
        body = resp.get_json()
        assert body["total"] == 3


# ── 4. Empty-state behaviour (no fallback to historical rows) ───────────────

class TestEmptyStateBehaviour:
    def test_zero_recent_interactions_does_not_fall_back_to_historical(self, app_for, monkeypatch):
        monkeypatch.setattr(eco.time, "time", lambda: NOW)
        app, db_path = app_for([
            {"mint": "Old1", "funding_time": NOW - 10 * 86400},
            {"mint": "Old2", "funding_time": NOW - 20 * 86400},
        ])
        with app.test_client() as c:
            resp = c.get("/api/ops-v2/ecosystem-exchange-interactions?window_seconds=86400")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        assert body["total"] == 0
        assert body["interactions"] == []

    def test_null_funding_time_row_excluded_from_24h_but_present_in_all(self, app_for, monkeypatch):
        """A row with funding_time=NULL can never satisfy a >= comparison in
        SQLite (NULL comparisons are never true), so it's correctly absent
        from any windowed view but must still appear in All -- confirms the
        WHERE predicate is truly omitted for All rather than coerced."""
        monkeypatch.setattr(eco.time, "time", lambda: NOW)
        app, db_path = app_for([
            {"mint": "NoFundingTime", "funding_time": None},
        ])
        with app.test_client() as c:
            resp_24h = c.get("/api/ops-v2/ecosystem-exchange-interactions?window_seconds=86400")
            resp_all = c.get("/api/ops-v2/ecosystem-exchange-interactions")
        assert resp_24h.get_json()["total"] == 0
        assert resp_all.get_json()["total"] == 1


# ── 5. Frontend source assertions (no JS execution harness exists in this
#      codebase -- matches the established static-source-inspection pattern
#      used elsewhere, e.g. tests/test_intelligence_ui.py's
#      TestFrozenSystemsUnmodified) ─────────────────────────────────────────

def _discovery_html_source() -> str:
    import os
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "templates", "discovery.html")
    with open(path, encoding="utf-8") as f:
        return f.read()


def _ecosystem_loader_block(src: str) -> str:
    start = src.index("function loadEcosystemExchangeInteractions(){")
    end = src.index("function renderEcosystemExchangeInteractions(){")
    return src[start:end]


class TestFrontendRequestIncludesWindow:
    def test_fetch_sends_window_seconds_for_24h(self):
        block = _ecosystem_loader_block(_discovery_html_source())
        assert "window_seconds=" in block
        assert "dwWindowSeconds()" in block

    def test_all_view_omits_window_seconds_rather_than_sending_huge_sentinel(self):
        """The task explicitly rejects reusing dwWindowSeconds()'s 'all'
        value (3153600000) as an artificial-huge-interval substitute for a
        true unbounded query -- the loader must special-case DW_WINDOW==='all'
        to send no window_seconds parameter at all."""
        block = _ecosystem_loader_block(_discovery_html_source())
        assert "DW_WINDOW==='all'" in block or 'DW_WINDOW==="all"' in block

    def test_no_longer_uses_permanent_fetch_once_guard(self):
        """The pre-X67.33 defect: X65_69_ECOSYSTEM_EXCHANGE_FETCH_STARTED was
        set true once and never reset, permanently blocking re-fetch on
        window toggle. That exact variable must not reappear."""
        src = _discovery_html_source()
        assert "X65_69_ECOSYSTEM_EXCHANGE_FETCH_STARTED" not in src


class TestFrontendWindowToggleBehaviour:
    def test_loader_tracks_which_window_data_was_fetched_for(self):
        block = _ecosystem_loader_block(_discovery_html_source())
        assert "X65_69_ECOSYSTEM_EXCHANGE_LOADED_FOR_WINDOW" in block
        # re-fetch guard must compare against the CURRENT window, not skip
        # unconditionally
        assert "DW_WINDOW" in block

    def test_request_generation_guard_present_for_race_safety(self):
        """Handles rapid toggling: a slower stale request must not overwrite
        a newer window's response."""
        src = _discovery_html_source()
        assert "X65_69_ECOSYSTEM_EXCHANGE_GENERATION" in src
        block = _ecosystem_loader_block(src)
        assert "myGeneration" in block


class TestUiWordingChanges:
    def test_column_renamed_to_funding_observed(self):
        src = _discovery_html_source()
        assert "Funding Observed" in src
        # the old bare "Observed" column header for this specific table must
        # be gone (other tables may legitimately use "Observed" elsewhere,
        # so this only checks the ecosystem exchange table's own header row)
        render_start = src.index("function renderEcosystemExchangeInteractions(){")
        render_end = src.index("function ", render_start + 10)
        render_block = src[render_start:render_end]
        assert "<th>Funding Observed</th>" in render_block

    def test_copy_no_longer_implies_all_time_when_window_selected(self):
        render_start = _discovery_html_source().index(
            "function renderEcosystemExchangeInteractions(){")
        render_block = _discovery_html_source()[render_start:render_start + 4000]
        assert "Historical launches whose funding path" not in render_block
        assert "selected Discovery window" in render_block

    def test_empty_state_does_not_read_as_error(self):
        render_start = _discovery_html_source().index(
            "function renderEcosystemExchangeInteractions(){")
        render_block = _discovery_html_source()[render_start:render_start + 4000]
        assert "No known-exchange funding interactions were observed" in render_block
