import json
from pathlib import Path

import pytest

from src.evidence.contracts.birth_valuation_adapters import (
    adapt_launch_fact,
    adapt_market_observation,
    adapt_observed_migration,
    adapt_platform_receive,
)
from src.evidence.contracts.birth_valuation_corpus import (
    BirthValuationCorpusError,
    assemble_birth_valuation_corpora,
)


FIXTURE = Path(__file__).parent / "fixtures" / "eb0_1c_source_adapters.json"


def _records():
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    records = [
        adapt_launch_fact(fixture["launch_fact"]),
        adapt_platform_receive(fixture["platform_receive"]),
        adapt_observed_migration(fixture["migration_receive"]),
        *(adapt_market_observation(item) for item in fixture["market_conflicts"]),
    ]
    later = dict(records[-1])
    later["event_time_utc_ns"] += 10_000_000_000
    later["observed_at_utc_ns"] += 10_000_000_000
    later["source_record_digest"] = "sha256:frozen-market-later"
    records.append(later)
    return records


def test_earliest_boundary_is_selected_separately_per_event_kind():
    corpus = assemble_birth_valuation_corpora(_records())[0]

    assert corpus.manifest.event_counts == {
        "CHAIN_BIRTH": 1,
        "MARKET_FIRST_OBSERVED": 2,
        "MIGRATION": 1,
        "PLATFORM_FIRST_SEEN": 1,
    }
    assert len(corpus.excluded) == 1
    assert corpus.excluded[0].reason == "LATER_THAN_EARLIEST_EVENT_KIND_BOUNDARY"


def test_tied_conflicting_market_facts_are_both_preserved():
    corpus = assemble_birth_valuation_corpora(_records())[0]
    market = [item for item in corpus.manifest.observations if item.event_kind == "MARKET_FIRST_OBSERVED"]

    assert len(market) == 2
    assert {item.quality_state for item in market} == {"CONFLICTING"}
    assert corpus.manifest.conflicting_observation_count == 2


def test_missingness_is_retained_in_selected_manifest():
    corpus = assemble_birth_valuation_corpora(_records())[0]
    assert corpus.manifest.missing_valuation_count == 3


def test_assembly_is_input_order_independent_and_digest_stable():
    records = _records()
    forward = assemble_birth_valuation_corpora(records)
    reverse = assemble_birth_valuation_corpora(reversed(records))
    assert forward == reverse
    assert forward[0].corpus_digest == "6be5dc1c24cfbb61de983ee5d18c42abd3d7b48294af67672ceed0909f6188da"


def test_multiple_mints_are_emitted_in_deterministic_order():
    records = _records()
    second = dict(records[0])
    second["mint"] = "AnotherMint111111111111111111111111111"
    second["source_record_digest"] = "sha256:another-launch"
    corpora = assemble_birth_valuation_corpora(records + [second])
    assert [item.mint for item in corpora] == sorted(item.mint for item in corpora)


def test_empty_or_malformed_input_fails_closed():
    with pytest.raises(BirthValuationCorpusError, match="EMPTY_INPUT"):
        assemble_birth_valuation_corpora([])
    bad = _records()[0]
    bad["event_time_utc_ns"] = "mixed"
    with pytest.raises(BirthValuationCorpusError, match="INVALID_EVENT_TIME"):
        assemble_birth_valuation_corpora([bad])
