from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "templates" / "discovery.html").read_text()


def _function(name: str, next_name: str) -> str:
    start = HTML.index(f"function {name}")
    return HTML[start:HTML.index(f"function {next_name}", start)]


# X65.47 (frontend half) — the candidate queue's Status column shows the
# already-identified exchange name ("CEX: Binance") when available, rather
# than the generic "Known CEX boundary" label, falling back to the
# existing generic label for every other case.


def test_status_prefers_cex_exchange_name_when_present():
    table_fn = _function("renderCandidateQueueTable", "renderWatchtowerProvisioningCandidates")
    assert "r.cex_exchange_name" in table_fn
    assert "'CEX: '" in table_fn


def test_status_falls_back_to_generic_label_when_no_exchange_name():
    table_fn = _function("renderCandidateQueueTable", "renderWatchtowerProvisioningCandidates")
    assert "x65_27CandidateStatus(r.mint)" in table_fn


def test_no_new_fetch_introduced_for_exchange_name():
    table_fn = _function("renderCandidateQueueTable", "renderWatchtowerProvisioningCandidates")
    assert "fetch(" not in table_fn
