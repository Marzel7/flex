"""Default-off, fail-open PumpPortal migration receive census."""
from __future__ import annotations
import hashlib, json, os, queue, threading
from pathlib import Path
from typing import Optional

class PumpPortalMigrationCensus:
    def __init__(self, *, enabled: bool, path: str, max_pending: int = 2048) -> None:
        self.enabled, self.path = bool(enabled), Path(path)
        self._pending: queue.Queue[dict] = queue.Queue(maxsize=max(1, int(max_pending)))
        self._seen, self._dropped, self._started = set(), 0, False
        self._valid_total, self._duplicate_total, self._storage_failures = 0, 0, 0
        self._invalid_total, self._invalid_reasons = 0, {}
        self._last_invalid_at, self._last_invalid_reason = None, None
        self._lock = threading.Lock()
    def record(self, *, receive_utc_ns: int, receive_monotonic_ns: int, signature: Optional[str], mint: Optional[str], creator: Optional[str] = None) -> None:
        if not self.enabled: return
        if not isinstance(signature, str) or not signature.strip() or not isinstance(mint, str) or not mint.strip():
            missing_signature = not isinstance(signature, str) or not signature.strip()
            missing_mint = not isinstance(mint, str) or not mint.strip()
            reason = "MISSING_SIGNATURE_AND_MINT" if missing_signature and missing_mint else ("MISSING_SIGNATURE" if missing_signature else "MISSING_MINT")
            import time
            with self._lock:
                self._invalid_total += 1
                self._invalid_reasons[reason] = self._invalid_reasons.get(reason, 0) + 1
                self._last_invalid_at, self._last_invalid_reason = time.time_ns(), reason
            return
        identity = hashlib.sha256(f"{signature}:{mint}".encode()).hexdigest()
        with self._lock:
            if identity in self._seen:
                self._duplicate_total += 1
                return
            self._seen.add(identity)
            self._valid_total += 1
        row = {"schema_version": 1, "event_id": identity, "event_type": "MIGRATION", "receive_utc_ns": int(receive_utc_ns), "receive_monotonic_ns": int(receive_monotonic_ns), "signature": signature, "mint": mint, "creator": creator or None, "source": "pumpportal", "subscription": "subscribeMigration"}
        try: self._pending.put_nowait(row)
        except queue.Full:
            with self._lock: self._dropped += 1
            return
        if not self._started:
            with self._lock:
                if not self._started:
                    self._started = True; threading.Thread(target=self._drain, name="pumpportal-migration-census", daemon=True).start()
    def _drain(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        while True:
            row = self._pending.get()
            try:
                with self.path.open("a", encoding="utf-8") as out: out.write(json.dumps(row, sort_keys=True, separators=(",", ":"))+"\n")
            except OSError:
                with self._lock: self._storage_failures += 1
            finally: self._pending.task_done()
    def health(self) -> dict:
        with self._lock:
            return {"enabled":self.enabled,"pending":self._pending.qsize(),"dropped":self._dropped,"valid_total":self._valid_total,"duplicate_total":self._duplicate_total,"storage_failures":self._storage_failures,
                    "migration_census_invalid_total":self._invalid_total,
                    "migration_census_invalid_reasons":dict(self._invalid_reasons),
                    "last_invalid_at":self._last_invalid_at,"last_invalid_reason":self._last_invalid_reason}

    def events(self) -> list[dict]:
        if not self.path.exists(): return []
        return [json.loads(line) for line in self.path.read_text().splitlines() if line]

def configured_migration_census() -> PumpPortalMigrationCensus:
    enabled=os.environ.get("MIGRATION_CENSUS_ENABLED","0").lower() in {"1","true","yes"}
    path=os.environ.get("MIGRATION_CENSUS_PATH",str(Path(__file__).resolve().parents[2]/"logs"/"oip_migration_census.jsonl"))
    return PumpPortalMigrationCensus(enabled=enabled,path=path)
