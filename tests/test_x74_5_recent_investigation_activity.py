from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "templates/operators_index.html").read_text(encoding="utf-8")


def test_recent_activity_is_at_bottom_of_registry_and_has_compact_empty_state():
    section = SOURCE.index('class="reg-recent"')
    registry = SOURCE.index('id="reg-sections"')
    script = SOURCE.index("<script>")
    assert registry < section < script
    assert "Recent Investigation Activity" in SOURCE
    assert "No recent investigation activity." in SOURCE
    assert 'class="reg-event-list"' in SOURCE


def test_activity_scope_is_reconciliation_disposition_only():
    assert "if(!['UNRESOLVED','REVIEW'].includes(disp))return[]" in SOURCE
    projection = SOURCE.split("function activityCandidates", 1)[1].split("const activityTime", 1)[0]
    assert "CONFIRMED_OPERATION" not in projection
    assert "INFRASTRUCTURE" not in projection


def test_exactly_five_diverse_events_are_selected_newest_first():
    assert "selected.length<5&&!seen.has(e.family_id)" in SOURCE
    assert "selected.slice(0,5).map" in SOURCE
    assert "sort((a,b)=>b.timestamp-a.timestamp" in SOURCE
    assert "seen.add(e.family_id)" in SOURCE


def test_internal_codes_have_analyst_readable_translations():
    for label in (
        "Population refreshed",
        "Funding mechanism discovered",
        "New launch added",
        "Contradictory evidence added",
        "Treasury relationship discovered",
        "Provisioning lineage updated",
        "Review status changed",
        "Promotion readiness changed",
        "Evidence package revised",
        "Population membership changed",
        "Creator reuse observed",
        "Settlement evidence added",
        "RPC evidence added",
    ):
        assert label in SOURCE


def test_repeated_events_are_grouped_and_link_to_relevant_tabs():
    assert "let grouped={}" in SOURCE
    assert "ACTIVITY_COLLAPSE_SECONDS=15*60" in SOURCE
    assert "sort((a,b)=>b.timestamp-a.timestamp)" in SOURCE
    assert "bucket.timestamp-e.timestamp<=ACTIVITY_COLLAPSE_SECONDS" in SOURCE
    assert "bucket.count++" in SOURCE
    assert "count+' related updates'" in SOURCE
    assert "?'members':type==='REVIEW_STATUS'?'summary':'evidence'" in SOURCE
    assert "'tab='+e.tab" in SOURCE


def test_activity_uses_existing_registry_payload_without_new_endpoint():
    assert "enrichActivity(populationRows.concat(reviewRows))" in SOURCE
    assert SOURCE.count("/api/ops/emerging-operators?limit=500") == 1
    assert "/api/ops/recent-investigation-activity" not in SOURCE
    assert ".slice(0,5)" in SOURCE
    assert "renderActivity(families);" in SOURCE


def test_compact_rows_match_registry_disposition_accents():
    assert ".reg-event.REVIEW{--row-color:#f97316}" in SOURCE
    assert ".reg-event.UNRESOLVED{--row-color:#64748b}" in SOURCE
    for field in (
        "reg-event-time",
        "reg-event-name",
        "reg-badge",
        "reg-event-title",
        "reg-event-detail",
        "reg-open",
    ):
        assert field in SOURCE
