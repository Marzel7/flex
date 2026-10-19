#!/usr/bin/env python3
"""
Clustering Task Queue

Prevents database lock errors by serializing clustering operations.
Multiple tokens triggering clustering in parallel would cause SQLite lock contention.

This queue ensures:
1. Only ONE clustering operation at a time
2. Operations processed in FIFO order
3. Automatic retry on database lock
4. Comprehensive logging
"""

import asyncio
import sqlite3
import time
from typing import Optional, Callable
from datetime import datetime

DB_PATH = "pumpswap_tokens.db"


class ClusteringTaskQueue:
    """Serialized task queue for clustering operations"""

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self.queue: asyncio.Queue = asyncio.Queue()
        self.processing = False
        self.stats = {
            "queued": 0,
            "processed": 0,
            "failed": 0,
            "retried": 0,
        }

    async def enqueue_clustering_task(self, task_fn: Callable, task_name: str = "clustering", retry_count: int = 3):
        """
        Add a clustering task to the queue

        Args:
            task_fn: Callable that performs the clustering operation
            task_name: Name of the task (for logging)
            retry_count: Number of retries on database lock
        """
        task = {
            "name": task_name,
            "fn": task_fn,
            "retries": retry_count,
            "enqueued_at": datetime.utcnow(),
        }

        await self.queue.put(task)
        self.stats["queued"] += 1

        print(
            f"[CLUSTER_QUEUE] 📋 Task '{task_name}' enqueued | Queue size: {self.queue.qsize()}",
            flush=True,
        )

        # Start processor if not already running
        if not self.processing:
            asyncio.create_task(self._process_queue())

    async def _process_queue(self):
        """Process queued clustering tasks sequentially"""
        if self.processing:
            return  # Already processing

        self.processing = True
        print(f"[CLUSTER_QUEUE] 🚀 Queue processor started", flush=True)

        try:
            while not self.queue.empty() or self.processing:
                try:
                    # Wait for next task with timeout
                    task = await asyncio.wait_for(self.queue.get(), timeout=5.0)
                except asyncio.TimeoutError:
                    # No tasks, stop processing
                    break

                task_name = task["name"]
                task_fn = task["fn"]
                retries_left = task["retries"]

                print(
                    f"[CLUSTER_QUEUE] ⏳ Processing task '{task_name}' ({self.queue.qsize()} in queue)",
                    flush=True,
                )

                success = False
                last_error = None

                # Retry logic for database locks
                while retries_left > 0 and not success:
                    try:
                        # Execute the clustering task
                        loop = asyncio.get_event_loop()
                        await loop.run_in_executor(None, task_fn)

                        success = True
                        self.stats["processed"] += 1

                        print(
                            f"[CLUSTER_QUEUE] ✅ Task '{task_name}' completed successfully",
                            flush=True,
                        )

                    except sqlite3.OperationalError as db_err:
                        if "database is locked" in str(db_err):
                            retries_left -= 1
                            self.stats["retried"] += 1

                            wait_time = (task["retries"] - retries_left) * 0.5  # Exponential backoff
                            print(
                                f"[CLUSTER_QUEUE] ⚠️ Database locked for '{task_name}' | "
                                f"Retrying in {wait_time:.1f}s ({retries_left} retries left)",
                                flush=True,
                            )

                            await asyncio.sleep(wait_time)
                        else:
                            # Not a lock error, fail immediately
                            last_error = db_err
                            break

                    except Exception as e:
                        last_error = e
                        break

                if not success:
                    self.stats["failed"] += 1
                    error_msg = str(last_error) if last_error else "Unknown error"
                    print(
                        f"[CLUSTER_QUEUE] ❌ Task '{task_name}' failed after {task['retries']} retries: {error_msg}",
                        flush=True,
                    )

                self.queue.task_done()

        finally:
            self.processing = False
            print(f"[CLUSTER_QUEUE] 🏁 Queue processor stopped", flush=True)
            self._print_stats()

    def _print_stats(self):
        """Print queue statistics"""
        print(
            f"[CLUSTER_QUEUE] 📊 Stats: Queued={self.stats['queued']}, "
            f"Processed={self.stats['processed']}, Failed={self.stats['failed']}, "
            f"Retried={self.stats['retried']}",
            flush=True,
        )

    async def wait_for_completion(self, timeout: Optional[float] = None):
        """Wait for all queued tasks to complete"""
        try:
            if timeout:
                await asyncio.wait_for(self.queue.join(), timeout=timeout)
            else:
                await self.queue.join()
            print(f"[CLUSTER_QUEUE] ✅ All tasks completed", flush=True)
        except asyncio.TimeoutError:
            print(
                f"[CLUSTER_QUEUE] ⚠️ Timeout waiting for tasks ({self.queue.qsize()} remaining)",
                flush=True,
            )


# Global singleton
_queue: Optional[ClusteringTaskQueue] = None


def get_clustering_queue() -> ClusteringTaskQueue:
    """Get or create the global clustering queue"""
    global _queue
    if _queue is None:
        _queue = ClusteringTaskQueue()
    return _queue


async def enqueue_clustering(task_fn: Callable, task_name: str = "clustering"):
    """Convenience function to enqueue a clustering task"""
    queue = get_clustering_queue()
    await queue.enqueue_clustering_task(task_fn, task_name)
