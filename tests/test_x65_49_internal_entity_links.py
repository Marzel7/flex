from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "templates" / "discovery.html").read_text()


def _function(name: str, next_name: str) -> str:
    start = HTML.index(f"function {name}")
    return HTML[start:HTML.index(f"function {next_name}", start)]


def _addr_chip_body():
    start = HTML.index("function addrChip")
    end = HTML.index("\n  }", start)
    return HTML[start:end + len("\n  }")]


# X65.49 — addrChip() links to the app's own entity page (href(), the same
# route every other Discovery chain-node link already uses) instead of
# Solscan (X65.48, superseded per explicit follow-up: keep the analyst
# in-context rather than navigating away). `kind` maps directly onto
# href()'s `type` param -- 'token' for mints, 'creator'/'treasury'/
# 'sub_provisioner' for wallets in that specific role, defaulting to
# 'wallet' when the role is unknown.


def test_addr_chip_links_via_the_shared_href_helper():
    body = _addr_chip_body()
    assert "href(full,entityType)" in body
    assert "solscan" not in body.lower()


def test_addr_chip_kind_defaults_to_wallet_when_unspecified():
    body = _addr_chip_body()
    assert "kind||'wallet'" in body.replace(" ", "")


def test_addr_chip_no_longer_opens_a_new_tab():
    # Internal navigation should stay in the same tab/context, unlike the
    # external Solscan link this supersedes.
    body = _addr_chip_body()
    assert 'target="_blank"' not in body


def test_copy_affordance_still_preserved():
    body = _addr_chip_body()
    assert "copyToClipboard(" in body
    assert "event.stopPropagation()" in body


def test_mint_links_use_token_type():
    canonical_table = _function("renderKnownWatchtowerAddressTable", "renderKnownWatchtowerTopology")
    assert "addrChip(r.mint,'token')" in canonical_table
    # X65.58 follow-up -- candidate rows now navigate directly via
    # href(r.mint,'token') (matching Canonical WATCHTOWER's row-click
    # behaviour) instead of an addrChip() inside an expandable detail row.
    candidate_table = _function("renderCandidateQueueTable", "renderWatchtowerProvisioningCandidates")
    assert "href(r.mint,'token')" in candidate_table


def test_creator_links_use_creator_type():
    canonical_table = _function("renderKnownWatchtowerAddressTable", "renderKnownWatchtowerTopology")
    assert "addrChip(r.creator,'creator')" in canonical_table


def test_treasury_links_use_treasury_type():
    canonical_table = _function("renderKnownWatchtowerAddressTable", "renderKnownWatchtowerTopology")
    assert "addrChip(r.treasury_wallet,'treasury')" in canonical_table


def test_subprov_detail_present_in_candidate_row_expandable_detail():
    # X65.58 follow-up removed the expandable detail row in favour of
    # row-click-navigates-to-entity-page. X65.60 restored the expandable
    # detail row (per the X65.59 audit's recommendation to move topology/
    # treasury-tier/address detail out of the primary columns rather than
    # off the candidate table entirely) -- ev.subprov_wallet is shown there
    # again, alongside a link through to the entity page.
    candidate_table = _function("renderCandidateQueueTable", "renderWatchtowerProvisioningCandidates")
    assert "addrChip(ev.subprov_wallet,'sub_provisioner')" in candidate_table


def test_href_helper_produces_the_expected_discovery_entity_url():
    href_fn_start = HTML.index("function href(id,type)")
    href_fn_end = HTML.index("}", href_fn_start)
    href_fn = HTML[href_fn_start:href_fn_end + 1]
    assert "/discovery?entity=" in href_fn
    assert "&type=" in href_fn
