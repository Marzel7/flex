import hashlib
from pathlib import Path
from scripts.render_watchtower_supervisor import absolutize_supervisord_log_paths, render_proposed_live, render_fragment, ALLOWED, stanza
from scripts.isolated_watchtower_supervisor_validation import isolate
from scripts.render_watchtower_supervisor import _ranges, render_in_place

ROOT = Path(__file__).resolve().parents[1]
SHA = "288be3388642e28ad487e8c353769a64916f9ed2"

def source():
    return """[unix_http_server]\nfile=/tmp/live.sock\n[supervisord]\npidfile=/tmp/live.pid\nlogfile=/live.log\nchildlogdir=/live-logs\n[supervisorctl]\nserverurl=unix:///tmp/live.sock\n[program:watchtower_api]\ncommand=old-api\ndirectory=/old\nautostart=true\nautorestart=true\n\n[program:watchtower_listener]\ncommand=listener\ndirectory=/listener\nautostart=true\nautorestart=true\n\n[program:unrelated]\ncommand=other\nautostart=true\nautorestart=true\n\n[program:operation_monitor_worker]\ncommand=old-worker\nautostart=false\nautorestart=true\n"""

def programs(text): return [line for line in text.splitlines() if line.startswith("[program:")]

def test_proposed_live_has_exact_names_root_sha_and_only_two_program_changes(tmp_path):
    candidate, fragment = render_proposed_live(source(), ROOT, SHA, tmp_path / "include")
    effective = candidate + "\n" + fragment
    assert effective.count("[program:operation_monitor_worker]") == 1
    assert effective.count("[program:watchtower_api]") == 1
    assert "watchtower_final_monitor" not in effective and "watchtower_final_ui" not in effective
    assert str(ROOT) in fragment and SHA in fragment
    assert stanza(source(), "watchtower_listener") == stanza(candidate, "watchtower_listener")
    assert programs(source()) == ["[program:watchtower_api]", "[program:watchtower_listener]", "[program:unrelated]", "[program:operation_monitor_worker]"]
    assert "[program:watchtower_listener]" in candidate and "[program:unrelated]" in candidate

def test_rendered_ledger_and_guard_contract_are_explicit(tmp_path):
    fragment = render_fragment(ROOT, SHA)
    ledger = ROOT / "docs/audits/watchtower-opening-offset-audit.v2.json"
    assert str(ledger) in fragment and hashlib.sha256(ledger.read_bytes()).hexdigest() == "ad75e5e910662d0259ca095df9e7ae20501136dc0a8d09ccfec62d1e9273b5b8"
    assert "launch_watchtower_final.sh worker" in fragment and "launch_watchtower_final.sh api" in fragment
    assert "src.core.main:app" in (ROOT / "scripts/launch_watchtower_final.sh").read_text()
    assert all(token not in fragment for token in ("HELIUS_API_KEY=", "BIRDEYE_API_KEY=", "SECRET=", "TOKEN="))

def test_isolation_is_a_copy_and_disables_every_program(tmp_path):
    proposed, fragment = render_proposed_live(source(), ROOT, SHA, tmp_path / "include")
    isolated, isolated_fragment, inventory = isolate(proposed, fragment, tmp_path / "isolated")
    assert proposed != isolated and fragment != isolated_fragment
    assert "autostart=true" not in isolated + isolated_fragment
    assert "autorestart=true" not in isolated + isolated_fragment
    assert "/tmp/live.sock" not in isolated and "/tmp/live.pid" not in isolated
    assert "/Users/kevinkeaveney/Dev/claude/flex/logs" not in isolated + isolated_fragment
    assert len(inventory) > 0

def test_fail_closed_for_duplicate_or_missing_identity(tmp_path):
    for broken in (source().replace("[program:watchtower_api]", "[program:other_api]"), source()+"[program:watchtower_api]\ncommand=x\n"):
        try: render_proposed_live(broken, ROOT, SHA, tmp_path / "include")
        except ValueError as exc: assert str(exc) == "EMBEDDED_SCOPE_INVALID"
        else: raise AssertionError("must fail closed")

def test_global_supervisor_logs_are_absolute_and_preserve_main_config_destination(tmp_path):
    config = tmp_path / "config/supervisor/supervisord.conf"; config.parent.mkdir(parents=True)
    config.write_text("x")
    candidate = "[supervisord]\nlogfile=%(here)s/../../logs/supervisor/supervisord.log\nchildlogdir=%(here)s/../../logs/supervisor\n"
    rendered = absolutize_supervisord_log_paths(candidate, config)
    expected = str((config.parent / "../../logs/supervisor").resolve())
    assert f"logfile={expected}/supervisord.log" in rendered
    assert f"childlogdir={expected}" in rendered
    assert "%(here)s" not in rendered

def test_in_place_replacement_preserves_every_non_target_byte_and_is_idempotent():
    original = source()
    first, _ = render_in_place(original, ROOT, SHA)
    second, _ = render_in_place(first, ROOT, SHA)
    def masked(text):
        for start, end, _ in sorted(_ranges(text, ALLOWED), reverse=True): text = text[:start] + "<TARGET>" + text[end:]
        return text
    assert masked(original) == masked(first)
    assert first == second
    assert stanza(original, "watchtower_listener") == stanza(first, "watchtower_listener")
    assert "[include]" not in first

def test_in_place_rejects_include_and_each_missing_or_duplicate_target():
    cases = [source()+"[include]\nfiles=x\n", source().replace("[program:watchtower_api]", "[program:no_api]"), source()+"[program:operation_monitor_worker]\ncommand=x\n"]
    for broken in cases:
        try: render_in_place(broken, ROOT, SHA)
        except ValueError: pass
        else: raise AssertionError("must fail closed")
