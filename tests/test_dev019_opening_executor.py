from types import SimpleNamespace

from src.ops.watchtower_historical_executor import HistoricalExecutor


MINT = "mint-dev019"
T = 1_700_000_000


def outcome(items, status=200):
    return SimpleNamespace(status_code=status, payload={"data": {"items": items}}, error_state=None)


def candle(timestamp, opening, closing=None):
    closing = opening if closing is None else closing
    return {"unixTime": timestamp, "o": opening, "h": max(opening, closing), "l": min(opening, closing), "c": closing}


class Binding:
    def __init__(self, replies): self.replies, self.requests = list(replies), []
    def __call__(self, request): self.requests.append(request); return self.replies.pop(0)


def executor(tmp_path, replies):
    binding = Binding(replies)
    return HistoricalExecutor(str(tmp_path / "journal.db"), binding=binding), binding


def boundary(): return {"mint": MINT, "migration_timestamp": T}


def test_strict_success_never_requests_fallback(tmp_path):
    run, binding = executor(tmp_path, [outcome([candle(T + 1, 12)])])
    result = run.acquire_opening(boundary())
    assert result["entry_method"] == "FIRST_FULL_POST_MIGRATION_SECOND_MC"
    assert len(binding.requests) == 1 and binding.requests[0]["request_parameters"]["type"] == "1s"


def test_strict_miss_uses_one_minute_open_not_close(tmp_path):
    run, binding = executor(tmp_path, [outcome([]), outcome([candle(T + 5, 100, 150)])])
    result = run.acquire_opening(boundary())
    assert result["entry_method"] == "FIRST_AVAILABLE_POST_MIGRATION_1M_OPEN_MC"
    assert result["entry_mc_usd"] == 100 and result["market_cap_field"] == "open"
    assert [x["request_parameters"]["type"] for x in binding.requests] == ["1s", "1m"]


def test_both_stages_insufficient_are_terminal_and_idempotent(tmp_path):
    run, binding = executor(tmp_path, [outcome([]), outcome([])])
    assert run.acquire_opening(boundary())["state"] == "INSUFFICIENT_EVIDENCE"
    assert run.acquire_opening(boundary())["state"] == "INSUFFICIENT_EVIDENCE"
    assert len(binding.requests) == 2


def test_resume_after_persisted_strict_miss_does_not_repeat_strict(tmp_path):
    run, _ = executor(tmp_path, [])
    import sqlite3
    with sqlite3.connect(run.db_path) as conn:
        conn.execute("CREATE TABLE watchtower_historical_openings(mint TEXT PRIMARY KEY,state TEXT NOT NULL,result_json TEXT,created_at INTEGER NOT NULL,strict_state TEXT,fallback_state TEXT,updated_at TEXT)")
        conn.execute("INSERT INTO watchtower_historical_openings VALUES(?,?,?,0,?,?,0)", (MINT, "FALLBACK_PENDING", None, "INSUFFICIENT_EVIDENCE", "PENDING"))
    binding = Binding([outcome([candle(T + 5, 100)])]); run.binding = binding
    assert run.acquire_opening(boundary())["entry_method"] == "FIRST_AVAILABLE_POST_MIGRATION_1M_OPEN_MC"
    assert [x["request_parameters"]["type"] for x in binding.requests] == ["1m"]


def test_completed_token_is_skipped_and_two_request_cap_holds(tmp_path):
    run, binding = executor(tmp_path, [outcome([]), outcome([candle(T + 5, 3)])])
    assert run.acquire_opening(boundary())["state"] == "QUALIFIED"
    assert run.acquire_opening(boundary())["state"] == "QUALIFIED"
    assert len(binding.requests) == 2 and run.calls == 2
