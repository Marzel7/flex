import json
from pathlib import Path

from src.ops.byzantine_birth_signal import ByzantineBirthSignalEmitter, SIGNAL_TYPE, load_proven_creators


def test_authoritative_loader_and_idempotent_signal():
    creators = load_proven_creators("database/wt_ops_v2.db")
    assert len(creators) == 36
    emitter = ByzantineBirthSignalEmitter(creators, enabled=True)
    creator = "EiwpFhN3LmoSTEbygYnpweUJDaHNfXX3Uho2PGRVPmr3"
    signal = emitter.observe(mint="mint", creator=creator, signature="sig", receive_utc_ns=1)
    assert signal["signal_type"] == SIGNAL_TYPE
    assert signal["canonical_membership"] is False
    assert signal["prior_strict_proof_creator"] == creator
    assert emitter.observe(mint="mint", creator=creator, signature="sig", receive_utc_ns=1) is None
    assert emitter.observe(mint="other", creator="unrelated", signature="sig", receive_utc_ns=1) is None


def test_disabled_emitter_does_not_signal():
    assert ByzantineBirthSignalEmitter({}, enabled=False).observe(mint="m", creator="c", signature="s", receive_utc_ns=1) is None


def test_latest_five_retained_births_emit_and_negative_controls_do_not():
    creators = load_proven_creators("database/wt_ops_v2.db")
    rows = json.loads(Path("/private/tmp/byzantine_rich_birth_scan/progress.json").read_text())["rows"]
    wanted = {"4ZXXiMMyN3wotCEW2QfmRbesHg3pS7S3rovnrKcYpump", "zRoYAdLYogsS491qazGakt2BDvgjMMiE8xQWpSVpump", "FqXipMyKJJUrU9MASnpWqTESVcdZjkK83ttFmWdVpump", "3jW73wn4skHyLMEDTZ5dzGzcF9C1YEbYuk9y61pUpump", "G5vBr81KJZEuFvD8Nf2oB5XRa6Ess7yHJefoUCjJpump"}
    emitter = ByzantineBirthSignalEmitter(creators, enabled=True, capacity=8)
    signals = [emitter.observe(mint=r["mint"], creator=r["creator"], signature=r["signature"], receive_utc_ns=1) for r in rows if r["mint"] in wanted]
    assert len(signals) == 5 and all(s and s["prior_strict_proof_creator"] == s["creator"] for s in signals)
    controls = [r for r in rows if r["creator"] not in creators][:10]
    assert len(controls) == 10
    assert all(emitter.observe(mint=r["mint"], creator=r["creator"], signature=r["signature"], receive_utc_ns=1) is None for r in controls)


def test_queue_overflow_is_nonblocking_and_counted():
    emitter = ByzantineBirthSignalEmitter({"creator": {"mint": "proof", "signature": "proof", "creator": "creator"}}, enabled=True, capacity=1)
    assert emitter.observe(mint="one", creator="creator", signature="one", receive_utc_ns=1)
    assert emitter.observe(mint="two", creator="creator", signature="two", receive_utc_ns=1) is None
    assert emitter.dropped == 1
