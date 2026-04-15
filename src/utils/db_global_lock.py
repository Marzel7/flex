"""
Shared cross-process database locking for Flex.

All processes writing to pumpswap_tokens.db (listener, extractor, clustering)
must use this lock to prevent "database is locked" errors.

Usage:
    from src.utils.db_global_lock import db_write_lock_global
    
    with db_write_lock_global():
        conn = sqlite3.connect(DB_PATH, timeout=15)
        # ... write operations ...
        conn.commit()
"""

import os
import time
import contextlib
import fcntl

DB_PATH = os.getenv("DB_PATH", "pumpswap_tokens.db")
DB_LOCK_FILE = f"{DB_PATH}.write.lock"


@contextlib.contextmanager
def db_write_lock_global(timeout: float = 30.0):
    """
    Cross-process exclusive lock using fcntl with timeout.
    Blocks up to `timeout` seconds then raises TimeoutError.
    Prevents "database is locked" errors across listener + extractor + clustering.
    """
    start = time.time()
    lock_file = open(DB_LOCK_FILE, "a+")
    lock_file.flush()
    
    try:
        while True:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.time() - start > timeout:
                    raise TimeoutError(f"Timed out waiting for DB lock: {DB_LOCK_FILE}")
                time.sleep(0.05)
        yield
    finally:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        finally:
            lock_file.close()
