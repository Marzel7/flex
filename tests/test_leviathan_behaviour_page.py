from pathlib import Path


def test_behaviour_page_uses_plain_leviathan_language():
    page = Path("templates/operator_subtype_detail.html").read_text()
    for text in ("Leviathan", "Qualified behaviour", "C357 lineage", "Leviathan Infrastructure Evidence", "Supported Launches", "Monitoring: Shadow only"):
        assert text in page
    assert "P3R-owned" not in page
    assert "Supported subtype projection" not in page
