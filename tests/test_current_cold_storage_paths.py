"""Current cold-storage namespace contract.

Historical P5A paths are valid only when an operator supplies an explicit
override.  Normal reader and rollover defaults must share one current root.
"""
from pathlib import Path

from src.ops.cold_segment_registry import (
    CURRENT_COLD_SEGMENTS_ROOT,
    CURRENT_COLD_SUMMARIES_ROOT,
    ColdSegmentRegistry,
    TransferReaderFactory,
    resolve_cold_root,
    resolve_summary_root,
)
from src.ops.hot_cold_rollover_runner import DEFAULT_COLD_ROOT, summary_root_for_cold_root
from src.ops.transfer_cold_store import close_segment, create_cold_segment


def test_current_defaults_share_one_version_neutral_storage_root(monkeypatch):
    monkeypatch.setenv("TRANSFER_COLD_ROOT", "/legacy/path/must-not-be-used")
    assert DEFAULT_COLD_ROOT == CURRENT_COLD_SEGMENTS_ROOT
    assert resolve_cold_root() == str(Path(CURRENT_COLD_SEGMENTS_ROOT).resolve())
    assert resolve_summary_root() == str(Path(CURRENT_COLD_SUMMARIES_ROOT).resolve())
    assert "_p5a_migration_build" not in DEFAULT_COLD_ROOT
    assert "_p5a_migration_build" not in resolve_summary_root()


def test_registry_uses_only_the_configured_current_root(tmp_path):
    current = tmp_path / "current" / "segments"
    historical = tmp_path / "_p5a_migration_build" / "cold_segments"
    current.mkdir(parents=True)
    historical.mkdir(parents=True)
    current_segment = current / "transfer_index_cold_2026_01.sqlite"
    historical_segment = historical / "transfer_index_cold_stage3a_delta.sqlite"
    create_cold_segment(str(current_segment), month_covered="2026_01")
    close_segment(str(current_segment), source_run_id="test")
    create_cold_segment(str(historical_segment), month_covered="unspecified")
    close_segment(str(historical_segment), source_run_id="historical")

    registry = ColdSegmentRegistry(str(current)).build()
    try:
        assert [Path(s.path).name for s in registry.segments] == [current_segment.name]
        assert all("_p5a_migration_build" not in s.path for s in registry.segments)
    finally:
        registry.close()


def test_missing_current_root_fails_closed(tmp_path):
    factory = TransferReaderFactory(
        hot_db_path=str(tmp_path / "missing-hot.db"),
        cold_root=str(tmp_path / "missing"),
    )
    try:
        factory.get_transfer_reader()
    except Exception as exc:  # precise type is intentionally internal
        assert "found 0 qualified" in str(exc)
    else:
        raise AssertionError("missing current cold root did not fail closed")


def test_explicit_historical_root_cannot_write_current_summaries(tmp_path):
    historical = tmp_path / "_p5a_migration_build" / "cold_segments"
    assert summary_root_for_cold_root(str(historical)) == str(
        historical.parent / "summaries"
    )
    assert summary_root_for_cold_root(str(historical)) != resolve_summary_root()
    assert summary_root_for_cold_root(CURRENT_COLD_SEGMENTS_ROOT) == resolve_summary_root()
