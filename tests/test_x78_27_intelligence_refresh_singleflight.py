import threading
import time

from src.core import creator_funding_worker as worker


def test_post_extraction_refresh_is_single_flight(monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    calls = []

    def slow_once(creator):
        calls.append(creator)
        entered.set()
        assert release.wait(2)

    monkeypatch.setattr(worker, "_post_extraction_intelligence_refresh_once", slow_once)
    monkeypatch.setattr(worker, "_intel_refresh_singleflight_skips", 0)

    first = threading.Thread(
        target=worker._post_extraction_intelligence_refresh, args=("creator-one",)
    )
    first.start()
    assert entered.wait(1)

    started = time.monotonic()
    worker._post_extraction_intelligence_refresh("creator-two")
    elapsed = time.monotonic() - started

    assert elapsed < 0.25
    assert calls == ["creator-one"]
    assert worker._intel_refresh_singleflight_skips == 1

    release.set()
    first.join(2)
    assert not first.is_alive()

    worker._post_extraction_intelligence_refresh("creator-three")
    assert calls == ["creator-one", "creator-three"]


def test_network_release_is_not_built_twice_in_refresh_source():
    import inspect

    source = inspect.getsource(worker._post_extraction_intelligence_refresh_once)
    assert "build_networks_release(" not in source
    assert "rebuild_after_scan(" not in source
    assert "take_snapshot(" not in source
