from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_potential_detail_mints_keep_solscan_and_add_muted_gmgn_chart_links():
    for name in ("potential_operation_detail.html", "potential_operation_legacy_child_detail.html"):
        page = (ROOT / "templates" / name).read_text()
        assert "https://solscan.io/token/{{ member.mint }}" in page
        assert "https://gmgn.ai/sol/token/{{ member.mint }}" in page
        assert "Open token chart on GMGN" in page
        assert "rel=\"noopener noreferrer\"" in page
