from dataclasses import dataclass

from src.intelligence.staged_coverage_expansion import construct_manifest


@dataclass
class Row:
    mint: str
    reason: str
    creation_signature: str
    migration_signature: str


def test_manifest_is_migration_first_paired_and_deterministic():
    rows = [Row("a", "MISSING_MIGRATION_TRANSACTION", "ca", "ma"),
            Row("b", "MISSING_CREATION_TRANSACTION", "cb", "mb"),
            Row("c", "MISSING_CREATION_AND_MIGRATION_TRANSACTION", "cc", "mc"),
            Row("d", "MISSING_CREATION_AND_MIGRATION_TRANSACTION", "cd", "md")]
    first, summary = construct_manifest(rows, limit=5)
    second, _ = construct_manifest(list(reversed(rows)), limit=5)
    assert first == second
    assert [(row.dependency_type, row.expected_completion_effect) for row in first[:2]] == [
        ("MIGRATION", "COMPLETE_LAUNCH"), ("CREATION", "COMPLETE_LAUNCH")]
    assert first[2].dependency_type == "MIGRATION" and first[3].dependency_type == "CREATION"
    assert summary["completion_opportunities"] == 3


def test_manifest_never_exceeds_physical_target_limit():
    rows = [Row(str(index), "MISSING_CREATION_AND_MIGRATION_TRANSACTION", f"c{index}", f"m{index}")
            for index in range(1000)]
    targets, _ = construct_manifest(rows, limit=1000)
    assert len(targets) == 1000
    assert targets[-1].manifest_position == 1000
