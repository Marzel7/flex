import json
from pathlib import Path
from types import SimpleNamespace

from src.ops.operation_monitor_worker import MonitorBirdeyeTransport
from src.ops.watchtower_shadow_capture import capture_already_acquired_opening
from src.ops.watchtower_shadow_evaluation import evaluate_retained_record


def _payload(items):
    return {"data": {"items": [
        {"unixTime": timestamp, "o": value, "h": value, "l": value, "c": value}
        for timestamp, value in items
    ]}}


def test_disabled_capture_has_no_file_or_authoritative_side_effect(tmp_path):
    target = tmp_path / "shadow.json"
    result = capture_already_acquired_opening(
        mint="natural", migration_timestamp=100, http_status=200,
        payload=_payload([(102, 12)]), environ={"WATCHTOWER_SHADOW_CAPTURE_ENABLED": "0", "WATCHTOWER_SHADOW_CAPTURE_LEDGER_PATH": str(target)},
    )
    assert result == {"state": "DISABLED", "provider_calls": 0, "database_writes": 0, "queue_writes": 0}
    assert not target.exists()


def test_enabled_capture_projects_only_compact_offsets_for_pure_shadow(tmp_path):
    target = tmp_path / "shadow.json"
    result = capture_already_acquired_opening(
        mint="natural", migration_timestamp=100, http_status=200,
        payload=_payload([(102, 12), (103, 13)]), environ={"WATCHTOWER_SHADOW_CAPTURE_ENABLED": "1", "WATCHTOWER_SHADOW_CAPTURE_LEDGER_PATH": str(target)},
    )
    assert result["state"] == "CAPTURED"
    assert result["record"]["offsets"] == {"2": 12.0, "3": 13.0}
    assert target.stat().st_size < 262_144
    shadow = evaluate_retained_record(result["record"])
    assert shadow["entry_offset_seconds"] == 2
    assert shadow["provider_calls"] == shadow["database_writes"] == shadow["queue_writes"] == 0


def test_capture_refuses_enabled_mode_without_a_separate_ledger_path():
    result = capture_already_acquired_opening(
        mint="natural", migration_timestamp=100, http_status=200,
        payload=_payload([(101, 11)]), environ={"WATCHTOWER_SHADOW_CAPTURE_ENABLED": "1"},
    )
    assert result["state"] == "REFUSED_LEDGER_PATH_REQUIRED"


def test_existing_strict_opening_response_is_captured_without_an_extra_provider_call(monkeypatch, tmp_path):
    calls = []
    def binding(_request):
        calls.append(1)
        return SimpleNamespace(status_code=200, payload=_payload([(102, 12)]), error_state=None)
    target = tmp_path / "shadow.json"
    monkeypatch.setenv("WATCHTOWER_SHADOW_CAPTURE_ENABLED", "1")
    monkeypatch.setenv("WATCHTOWER_SHADOW_CAPTURE_LEDGER_PATH", str(target))
    entry, _ = MonitorBirdeyeTransport(binding=binding).acquire_watchtower_entry(
        {"mint": "natural"}, {"migration_timestamp": 100, "time_from": 100, "time_to": 102},
    )
    assert entry["timestamp"] == 102
    assert calls == [1]
    assert evaluate_retained_record(json.loads(target.read_text())["records"][0])["entry_offset_seconds"] == 2
