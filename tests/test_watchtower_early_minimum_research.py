import hashlib
from pathlib import Path

from src.ops.watchtower_early_minimum_research import EVIDENCE_CLASS, projection, read_pilot, statistics


def test_retained_pilot_is_read_only_research_with_no_fabricated_identities():
    before = Path('docs/audits/dev014_watchtower_early_minimum_mcap_pilot_20261009.v1.json').read_bytes()
    pilot = read_pilot()
    assert pilot['sample_size'] == 10
    assert all(row['evidence_class'] == EVIDENCE_CLASS for row in pilot['rows'])
    observations = [item for row in pilot['rows'] for item in row['observations'].values()]
    assert all(item['request_identity'] is None and item['evidence_identity'] is None for item in observations)
    assert all(item['chronological_recovery'] is None for item in observations)
    assert Path('docs/audits/dev014_watchtower_early_minimum_mcap_pilot_20261009.v1.json').read_bytes() == before
    assert pilot['source_artifact_sha256'] == hashlib.sha256(before).hexdigest()


def test_pilot_statistics_keep_measured_sample_denominators_and_partial_coverage():
    data = projection()
    stats = data['statistics']
    assert set(stats) == {'300', '900', '3600'}
    assert stats['300']['measured_count'] == stats['900']['measured_count'] == stats['3600']['measured_count'] == 10
    assert stats['3600']['complete_returned_buckets'] == 7
    assert stats['3600']['partial_coverage'] == 3
    assert stats['900']['declines']['20']['denominator'] == 10
    assert data['thirty_minute_availability'] == 'UNAVAILABLE_NOT_RETAINED'
    assert data['recovery_metric_availability'] == 'UNAVAILABLE_NOT_RETAINED'


def test_research_statistics_never_merge_authoritative_store_population():
    pilot = read_pilot()
    values = statistics(pilot)
    assert all(window['sample_size'] == 10 and window['missing_count'] == 0 for window in values.values())
