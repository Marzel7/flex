from pathlib import Path


def test_registry_uses_shared_columns_without_visible_stable_ids():
    template = (Path(__file__).resolve().parents[1] / "templates/operators_index.html").read_text()
    for label in (
        "Operator",
        "Activity 24h / 7d / 30d",
        "Established launches",
        "Last launch",
        "Evolution Watch",
        "Action",
    ):
        assert label in template
    assert "row.operation_family||row.operator_id" not in template
    assert "row.live_launches_7d" in template
    assert "row.live_launches_30d" in template
    assert "c357Subtype" not in template
