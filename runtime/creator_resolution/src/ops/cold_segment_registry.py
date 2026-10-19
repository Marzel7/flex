"""STORAGE-LIFECYCLE-P5B-R2 Part 2/3: production COLD segment registry +
transfer-reader factory.

This module is the production integration point that closes the gap found
during P5B Stage 2: UnifiedTransferReader (src/ops/unified_transfer_reader.py)
is fully qualified but had ZERO production call sites -- nothing in
src/core/ actually discovered COLD segments or constructed a reader. This
module provides that discovery + construction, safely, for real use.

Design points (see docs/audits/storage_lifecycle_p5b_r2_production_reader_integration.json
for the full qualification writeup):

- Read-only everywhere. Every COLD connection is opened with
  `file:...?mode=ro` URIs. This module never writes to a COLD segment.
- Deterministic ordering: segments are discovered via glob + sorted by
  filename, which sorts chronologically given the
  transfer_index_cold_<YYYY>_<MM>[_suffix].sqlite naming convention.
- Qualification gate: a segment is only included if it has a
  segment_manifest row with closed_at IS NOT NULL. A segment file that is
  missing entirely, missing a manifest row, or has closed_at NULL is
  treated as malformed/unqualified -- it is skipped and reported, not
  fatal to the rest of the registry.
- Bounded resource usage: connections are opened ONCE (lazily, on first
  build()/refresh() call) and reused for the lifetime of the registry
  object. There is no per-query connection open/close. See close() for
  explicit teardown.
- No continuous polling. COLD segments are immutable/append-only-as-new-
  months, so this is a startup scan + an explicit refresh() method the
  caller can invoke (e.g. on a known monthly-rollover schedule), not a
  background poller.
"""
from __future__ import annotations

import glob
import logging
import os
import sqlite3
import time
from dataclasses import dataclass, field

from src.ops.unified_transfer_reader import UnifiedTransferReader

logger = logging.getLogger(__name__)

# Same env-var-with-default convention as src/core/main.py's DB_PATH, so a
# future Stage 3 atomic switch (renaming replacement_main.db into
# flex_complete_database.db's place) needs no code change here.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_DEFAULT_HOT_DB_PATH = os.path.join(_REPO_ROOT, "database", "flex_complete_database.db")
# Current cold storage is deliberately version-neutral.  Historical P5A
# material may be inspected only through an explicit override; it is never a
# normal-runtime fallback.
CURRENT_COLD_STORAGE_ROOT = os.path.join(_REPO_ROOT, "database", "current_cold_storage")
CURRENT_COLD_SEGMENTS_ROOT = os.path.join(CURRENT_COLD_STORAGE_ROOT, "segments")
CURRENT_COLD_SUMMARIES_ROOT = os.path.join(CURRENT_COLD_STORAGE_ROOT, "summaries")
_DEFAULT_COLD_ROOT = CURRENT_COLD_SEGMENTS_ROOT

HOT_DB_PATH_ENV = "DB_PATH"          # reuse main.py's existing env var name


def resolve_hot_db_path(override: str | None = None) -> str:
    """Resolves the production HOT DB path using the SAME env-var/default
    contract as src/core/main.py's DB_PATH constant (env var 'DB_PATH',
    falling back to database/flex_complete_database.db). `override` is for
    R2's own qualification runs against the candidate build -- callers
    doing historical P5A qualification should pass an explicit override;
    production code
    should never pass override and rely on the env/default resolution so
    that a future Stage 3 rename-in-place takes effect automatically."""
    if override is not None:
        return override
    return os.path.abspath(os.environ.get(HOT_DB_PATH_ENV, _DEFAULT_HOT_DB_PATH))


def resolve_cold_root(override: str | None = None) -> str:
    """Resolve the one production COLD segment root directory.

    ``override`` remains available for explicitly-scoped tests and historical
    qualification.  Normal runtime deliberately has no environment fallback
    or legacy-P5A fallback, so readers and the rollover writer share one
    current authority.
    """
    if override is not None:
        return override
    return os.path.abspath(_DEFAULT_COLD_ROOT)


def resolve_summary_root() -> str:
    """Return the single current rollover-summary location.

    Summary storage intentionally has no environment fallback: a writer and
    its paired reader must use the same current namespace.
    """
    return os.path.abspath(CURRENT_COLD_SUMMARIES_ROOT)


@dataclass
class SegmentInfo:
    path: str
    segment_id: str
    month_covered: str
    row_count: int
    closed_at: float


@dataclass
class SegmentRejection:
    path: str
    reason: str


class ColdSegmentRegistry:
    """Discovers, qualifies, and holds open read-only connections to COLD
    transfer_index segments under a directory. Bounded: connections are
    opened once per segment on build()/refresh(), never per-query.

    Lifecycle: construct -> build() (or first access triggers an implicit
    build) -> use .connections / .segments -> close() when done (worker
    shutdown, test teardown).
    """

    def __init__(self, cold_root: str | None = None):
        self.cold_root = resolve_cold_root(cold_root)
        self._conns: dict[str, sqlite3.Connection] = {}
        self.segments: list[SegmentInfo] = []
        self.rejections: list[SegmentRejection] = []
        self._built = False

    def _qualify_segment(self, path: str) -> SegmentInfo | SegmentRejection:
        if not os.path.isfile(path):
            return SegmentRejection(path=path, reason="FILE_NOT_FOUND")
        try:
            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        except sqlite3.OperationalError as exc:
            return SegmentRejection(path=path, reason=f"OPEN_FAILED:{exc}")
        try:
            try:
                row = conn.execute(
                    "SELECT segment_id, month_covered, row_count, closed_at "
                    "FROM segment_manifest LIMIT 1"
                ).fetchone()
            except sqlite3.OperationalError as exc:
                return SegmentRejection(path=path, reason=f"NO_MANIFEST_TABLE:{exc}")
            if row is None:
                return SegmentRejection(path=path, reason="NO_MANIFEST_ROW")
            segment_id, month_covered, row_count, closed_at = row
            if closed_at is None:
                return SegmentRejection(path=path, reason="MANIFEST_NOT_CLOSED")
            return SegmentInfo(
                path=path, segment_id=segment_id, month_covered=month_covered,
                row_count=row_count, closed_at=closed_at,
            )
        finally:
            conn.close()

    def build(self) -> "ColdSegmentRegistry":
        """One-time (or explicitly re-invoked via refresh()) scan of
        cold_root for *.sqlite segment files, qualifying each and opening
        a held read-only connection for every segment that passes. Safe to
        call multiple times -- each call fully re-scans and re-opens
        (existing connections are closed first) rather than incrementally
        appending, since a COLD directory should only ever grow by whole
        new segments, not require merge logic."""
        self.close()
        self.segments = []
        self.rejections = []

        pattern = os.path.join(self.cold_root, "transfer_index_cold_*.sqlite")
        paths = sorted(glob.glob(pattern))  # filename sort == chronological (YYYY_MM[_suffix])

        for path in paths:
            result = self._qualify_segment(path)
            if isinstance(result, SegmentRejection):
                self.rejections.append(result)
                logger.warning("cold_segment_registry: rejected malformed segment %s (%s)",
                                result.path, result.reason)
                continue
            self.segments.append(result)
            # Open the real held connection (separate from the qualification
            # probe connection above, which was closed already).
            self._conns[result.path] = sqlite3.connect(f"file:{result.path}?mode=ro", uri=True)

        self._built = True
        return self

    def refresh(self) -> "ColdSegmentRegistry":
        """Explicit re-scan. COLD segments are immutable/append-only-as-
        new-months, so this does not need to run on any polling schedule
        -- callers should invoke it only when they know a new month's
        segment has landed (e.g. an ops runbook step), or accept that a
        long-lived worker process picks up new segments only on its next
        restart. This is a deliberate simplicity choice, not an oversight."""
        return self.build()

    def ensure_built(self) -> None:
        if not self._built:
            self.build()

    @property
    def connections(self) -> list[sqlite3.Connection]:
        self.ensure_built()
        return [self._conns[s.path] for s in self.segments]

    @property
    def segment_count(self) -> int:
        self.ensure_built()
        return len(self.segments)

    def close(self) -> None:
        """Closes all held COLD connections. Safe to call repeatedly (e.g.
        graceful shutdown, test teardown) and safe to call before build()
        has ever run."""
        for conn in self._conns.values():
            try:
                conn.close()
            except sqlite3.Error:
                pass
        self._conns = {}
        self._built = False


class ColdRegistryUnavailableError(RuntimeError):
    """Raised by get_transfer_reader() when a caller requires a working
    COLD registry (fail_closed=True) but the registry found zero qualified
    segments -- e.g. because cold_root doesn't exist, is empty, or every
    segment present is malformed. This is the fail-closed path: a
    HISTORICAL_HOT_COLD_REQUIRED consumer must never silently degrade to
    HOT-only and present partial history as if it were complete."""


@dataclass
class TransferReaderFactory:
    """Per-process (per-Gunicorn-worker) factory. Holds one HOT connection
    and one ColdSegmentRegistry, both opened lazily on first use and reused
    for the life of the factory instance. Construct ONE of these per
    worker process (see Part 4 / Part 15 for the worker-lifecycle design)
    -- never one per request.
    """
    hot_db_path: str | None = None
    cold_root: str | None = None
    _hot_conn: sqlite3.Connection | None = field(default=None, init=False, repr=False)
    _registry: ColdSegmentRegistry | None = field(default=None, init=False, repr=False)

    def _get_hot_conn(self) -> sqlite3.Connection:
        if self._hot_conn is None:
            path = resolve_hot_db_path(self.hot_db_path)
            self._hot_conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        return self._hot_conn

    def _get_registry(self) -> ColdSegmentRegistry:
        if self._registry is None:
            self._registry = ColdSegmentRegistry(self.cold_root)
            self._registry.build()
        return self._registry

    def get_transfer_reader(self, *, fail_closed: bool = True) -> UnifiedTransferReader:
        """Returns a working UnifiedTransferReader over the current HOT
        connection and all qualified COLD connections.

        fail_closed=True (the default, and the required mode for any
        HISTORICAL_HOT_COLD_REQUIRED consumer): raises
        ColdRegistryUnavailableError if the COLD registry has zero
        qualified segments, rather than silently returning a HOT-only
        reader. fail_closed=False is available for callers that have their
        own, explicitly-reasoned-about fallback contract (none exist in
        this codebase as of R2 -- every current HISTORICAL_HOT_COLD_REQUIRED
        adapter uses the default).
        """
        registry = self._get_registry()
        if fail_closed and registry.segment_count == 0:
            raise ColdRegistryUnavailableError(
                f"cold segment registry at {registry.cold_root!r} found 0 qualified "
                f"segments ({len(registry.rejections)} rejected) -- refusing to serve "
                "a HISTORICAL_HOT_COLD_REQUIRED query as HOT-only. See P5B rollback "
                "contract for recovery."
            )
        return UnifiedTransferReader(hot_conn=self._get_hot_conn(), cold_conns=registry.connections)

    def close(self) -> None:
        if self._hot_conn is not None:
            try:
                self._hot_conn.close()
            except sqlite3.Error:
                pass
            self._hot_conn = None
        if self._registry is not None:
            self._registry.close()
            self._registry = None


# --- Process-wide singleton -------------------------------------------------
# Each Gunicorn worker is a separate process, so this module-level singleton
# is naturally per-worker: a fresh worker process gets a fresh, unimported
# module state, so _factory is None until that worker's first call. See
# Part 4/15/17 in the R2 qualification artifact for the full worker-lifecycle
# proof (fresh-process re-import test).
_factory: TransferReaderFactory | None = None


def get_transfer_reader(*, hot_db_path: str | None = None, cold_root: str | None = None,
                         fail_closed: bool = True) -> UnifiedTransferReader:
    """Process-level convenience entry point. On first call in a process,
    constructs and caches a TransferReaderFactory (one HOT connection + one
    ColdSegmentRegistry, opened once); subsequent calls in the SAME process
    reuse those connections -- no new connections are opened per call.

    hot_db_path/cold_root overrides are for qualification/testing only.
    Production call sites should omit them and rely on the canonical current
    resolution (see resolve_hot_db_path/resolve_cold_root).
    """
    global _factory
    if _factory is None:
        _factory = TransferReaderFactory(hot_db_path=hot_db_path, cold_root=cold_root)
    return _factory.get_transfer_reader(fail_closed=fail_closed)


def reset_transfer_reader_factory() -> None:
    """Test/teardown helper: closes and clears the process-level singleton
    so a subsequent get_transfer_reader() call builds fresh state. Also
    useful to simulate a fresh-worker-process reload in tests without
    actually forking a new process."""
    global _factory
    if _factory is not None:
        _factory.close()
    _factory = None
