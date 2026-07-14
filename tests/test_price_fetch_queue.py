import time

from src.core.price_fetch_queue import FetchTask, PriceFetchQueue


def test_queue_dedupes_same_priority_mint():
    queue = PriceFetchQueue()

    first = FetchTask(mint="mint-a", priority="LOW", enqueued_at=1.0)
    second = FetchTask(mint="mint-a", priority="LOW", enqueued_at=2.0)

    assert queue.enqueue(first) is True
    assert queue.enqueue(second) is False

    stats = queue.get_stats()
    assert stats["enqueued"] == 1
    assert stats["deduped"] == 1
    assert stats["promoted"] == 0


def test_queue_promotes_pending_mint_to_higher_priority():
    queue = PriceFetchQueue()
    fetched = []

    low = FetchTask(mint="mint-a", priority="LOW", enqueued_at=1.0)
    high = FetchTask(mint="mint-a", priority="HIGH", enqueued_at=2.0)
    other = FetchTask(mint="mint-b", priority="MEDIUM", enqueued_at=3.0)

    assert queue.enqueue(low) is True
    assert queue.enqueue(high) is True
    assert queue.enqueue(other) is True

    def fetch_fn(mint):
        fetched.append(mint)
        class Price:
            price_usd = 1.0
        return Price()

    queue.start(fetch_fn)
    try:
        assert queue.wait_until_empty(timeout_seconds=5) is True
    finally:
        queue.stop()

    assert fetched == ["mint-a", "mint-b"]
    stats = queue.get_stats()
    assert stats["enqueued"] == 2
    assert stats["promoted"] == 1
    assert stats["deduped"] == 0

