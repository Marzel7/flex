from pathlib import Path

from src.ops.potential_operations import _c357_dutb_funder_map


ROOT = Path(__file__).resolve().parents[1]


def test_c357_dutb_audit_projects_verified_funders_only():
    links = _c357_dutb_funder_map()
    assert len(links) == 35
    assert links["GUBtBg8i5wn5ME3vYmgqMZFFuBnDp6i1oa8BPFwoURAP"]["delivery_count"] == 2
    assert links["GUBtBg8i5wn5ME3vYmgqMZFFuBnDp6i1oa8BPFwoURAP"]["funding_lamports"] == 104_861_941


def test_c357_detail_template_has_subtle_dutb_funding_marker():
    template = (ROOT / "templates/potential_operation_detail.html").read_text()
    assert "member.dutb_funding_link" in template
    assert "DuTb funding link" in template
    assert "Verified DuTb-owned temporary-WSOL close" in template
