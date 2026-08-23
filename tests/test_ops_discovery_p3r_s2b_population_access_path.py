from scripts.qualify_ops_discovery_p3r_s2b_population_access_path import fixture_equivalence


def test_mint_ordered_access_path_is_fixture_equivalent_without_temp_sort():
    result = fixture_equivalence()
    assert result["pass"]
