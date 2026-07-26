import sqlite3

from src.ops.operational_intelligence import (
    QUICK_BIRTH_MIGRATION,
    classify_quick_birth_migration,
    query,
    select_creator_birth,
    summarise_quick_birth_diagnostics,
    _enrich_discovery_records,
)


def _record(*, watchtower=False, quick=False, topology="LINEAR"):
    return {
        "topology": topology,
        "behaviours": [],
        "mechanisms": [],
        "operation_id": "WATCHTOWER" if watchtower else None,
        "is_watchtower": watchtower,
        "is_quick_birth_migration": quick,
    }


def test_confirmed_watchtower_filter_excludes_non_watchtower():
    intelligence = {"records": {
        "WT": _record(watchtower=True),
        "OTHER": _record(),
    }}
    assert query(intelligence, operation="WATCHTOWER") == ["WT"]


def test_quick_creator_and_quick_migration_match():
    result = classify_quick_birth_migration(1_000, 1_005, 1_088)
    assert result["creator_age_at_create_seconds"] == 5
    assert result["create_to_migration_seconds"] == 83
    assert result["is_quick_birth_migration"] is True


def test_millisecond_timestamps_are_normalised():
    result = classify_quick_birth_migration(1_700_000_000_000, 1_700_000_005_000, 1_700_000_088_000)
    assert result["creator_age_at_create_seconds"] == 5
    assert result["create_to_migration_seconds"] == 83
    assert result["is_quick_birth_migration"] is True


def test_iso_create_timestamp_is_normalised():
    result = classify_quick_birth_migration(
        1_700_000_000, "2023-11-14 22:13:25", 1_700_000_088,
    )
    assert result["creator_age_at_create_seconds"] == 5
    assert result["create_to_migration_seconds"] == 83
    assert result["is_quick_birth_migration"] is True


def test_six_second_creator_birth_does_not_match():
    result = classify_quick_birth_migration(1_000, 1_006, 1_007)
    assert result["quick_birth_reason"] == "BIRTH_TOO_OLD"
    assert result["is_quick_birth_migration"] is False


def test_old_creator_does_not_match():
    assert not classify_quick_birth_migration(1_000, 87_401, 87_402)["is_quick_birth_migration"]


def test_slow_migration_does_not_match():
    assert not classify_quick_birth_migration(1_000, 1_001, 1_902)["is_quick_birth_migration"]


def test_missing_birth_or_migration_does_not_match():
    missing_birth = classify_quick_birth_migration(None, 1_001, 1_002)
    missing_migration = classify_quick_birth_migration(1_000, 1_001, None)
    assert missing_birth["quick_birth_reason"] == "MISSING_CREATOR_BIRTH"
    assert missing_migration["quick_birth_reason"] == "MISSING_MIGRATION"
    assert not missing_birth["quick_birth_evaluable"]
    assert not missing_migration["quick_birth_evaluable"]


def test_negative_intervals_do_not_match():
    first = classify_quick_birth_migration(1_002, 1_001, 1_003)
    second = classify_quick_birth_migration(1_000, 1_002, 1_001)
    assert first["quick_birth_reason"] == "NEGATIVE_INTERVAL"
    assert second["quick_birth_reason"] == "NEGATIVE_INTERVAL"
    assert not first["is_quick_birth_migration"]
    assert not second["is_quick_birth_migration"]


def test_creator_birth_precedence_prefers_confirmed_wallet_birth():
    value, source, quality = select_creator_birth(
        confirmed_wallet_birth=100,
        earliest_creator_signature=200,
        persisted_first_seen=300,
    )
    assert (value, source, quality) == (
        100, "confirmed_wallet_birth", "EXHAUSTIVE_HISTORY_CONFIRMED",
    )


def test_funding_proxy_is_used_only_when_enabled():
    disabled = select_creator_birth(
        funding_proxies=[(400, "funding_tx")], allow_funding_proxy=False,
    )
    enabled = select_creator_birth(
        funding_proxies=[(400, "funding_tx")], allow_funding_proxy=True,
    )
    assert disabled == (None, None, "UNKNOWN")
    assert enabled == (400, "funding_tx", "CREATOR_FUNDING_PROXY")


def test_diagnostics_reflect_exclusion_reasons():
    ok = classify_quick_birth_migration(195, 200, 300) | {
        "creator_birth_source": "confirmed_first_transaction",
    }
    missing = classify_quick_birth_migration(None, 200, 300) | {
        "creator_birth_source": None,
    }
    summary = summarise_quick_birth_diagnostics({"OK": ok, "MISSING": missing})
    assert summary["evaluable"] == 1
    assert summary["reasons"] == {"OK": 1, "MISSING_CREATOR_BIRTH": 1}
    assert summary["creator_birth_sources"] == {
        "confirmed_first_transaction": 1, "UNKNOWN": 1,
    }


def test_cross_dimension_filters_intersect_without_duplicates():
    intelligence = {"records": {
        "MATCH": _record(watchtower=True, quick=True, topology="LINEAR"),
        "WRONG_TOPOLOGY": _record(watchtower=True, quick=True, topology="FAN_OUT"),
        "NOT_QUICK": _record(watchtower=True, quick=False, topology="LINEAR"),
    }}
    result = query(
        intelligence,
        operation="WATCHTOWER",
        quick_birth_migration=True,
        topology="LINEAR",
    )
    assert result == ["MATCH"]
    assert len(result) == len(set(result))


def test_quick_machine_value_is_stable():
    assert QUICK_BIRTH_MIGRATION == "QUICK_BIRTH_MIGRATION"


def test_canonical_watchtower_registry_launch_cannot_disappear_in_join(tmp_path):
    ops_path = tmp_path / "ops.db"
    core_path = tmp_path / "core.db"
    ops = sqlite3.connect(ops_path)
    ops.execute("CREATE TABLE wt_confirmed_treasuries (treasury TEXT)")
    ops.execute(
        "CREATE TABLE wt_watchtower_launches ("
        "mint TEXT,creator_wallet TEXT,create_time INTEGER,treasury_wallet TEXT,"
        "birth_to_launch_seconds INTEGER,create_to_migration_secs INTEGER,recorded_at INTEGER,"
        "detection_source TEXT)"
    )
    ops.execute(
        "INSERT INTO wt_watchtower_launches VALUES "
        "('KNOWN','CREATOR',9900,'TREASURY',10,20,9900,NULL)"
    )
    ops.commit()
    ops.close()
    core = sqlite3.connect(core_path)
    core.execute("CREATE TABLE token_analysis (mint TEXT,created_at INTEGER,migrated_at INTEGER)")
    core.execute("INSERT INTO token_analysis VALUES ('KNOWN',9900,9920)")
    core.commit()
    core.close()
    records = {"KNOWN": {"creator": "CREATOR", "topology": "LINEAR", "behaviours": [], "mechanisms": []}}
    diagnostics = _enrich_discovery_records(str(ops_path), str(core_path), records, now=10_000)
    assert records["KNOWN"]["is_watchtower"] is True
    assert records["KNOWN"]["operation_source"] == "canonical_watchtower_launch_registry"
    assert diagnostics["watchtower"]["known_total"] == 1
    assert diagnostics["watchtower"]["inside_window"] == 1
    assert diagnostics["watchtower"]["matched"] == 1
    assert diagnostics["watchtower"]["excluded"] == 0
