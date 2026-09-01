import json
import os
import sqlite3
from pathlib import Path
from typing import Callable


DB = Path("database/wt_ops_v2.db")
OUT = Path("/tmp/p3r_historical_features.jsonl")
CK = Path("/tmp/p3r_historical_features.checkpoint.json")
N = 2000


def _read_checkpoint(path: Path) -> dict:
    if not path.exists():
        return {"last_mint": "", "rows": 0, "chunk": 0}
    return json.loads(path.read_text())


def _atomic_write_json(path: Path, value: object) -> None:
    payload = (json.dumps(value, sort_keys=True) + "\n").encode("utf-8")
    tmp = path.with_suffix(f"{path.suffix}.tmp")
    with tmp.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)
    parent_fd = os.open(str(path.parent), os.O_RDONLY)
    try:
        os.fsync(parent_fd)
    finally:
        os.close(parent_fd)


def _write_checkpoint(path: Path, value: dict) -> None:
    _atomic_write_json(path, value)


def _write_inflight(path: Path, value: dict | None) -> None:
    if value is None:
        if path.exists():
            path.unlink()
        return
    _atomic_write_json(path, value)


def _payload_row(queue_row: tuple[str, str, str], edge_row: tuple[int, int, str | None, str | None]) -> dict:
    return {
        "mint": queue_row[0],
        "creator": queue_row[1],
        "direct_funder": queue_row[2],
        "edge_count": edge_row[0],
        "max_hop_depth": edge_row[1],
        "parents": sorted(edge_row[2].split(",")) if edge_row[2] else None,
        "mechanisms": sorted(edge_row[3].split(",")) if edge_row[3] else None,
    }


def _append_lines(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_CREAT | os.O_APPEND | os.O_WRONLY
    fd = os.open(str(path), flags, 0o644)
    try:
        os.write(fd, ("".join(lines)).encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)


def _recover_from_inflight(output: Path, checkpoint: Path) -> None:
    inflight_path = checkpoint.with_suffix(checkpoint.suffix + ".inflight")
    if not inflight_path.exists():
        return
    inflight = json.loads(inflight_path.read_text())
    rollback_rows = int(inflight.get("checkpoint_rows", 0))
    lines = output.read_text().splitlines() if output.exists() else []
    if len(lines) != rollback_rows:
        output.write_text("\n".join(lines[:rollback_rows]) + ("\n" if rollback_rows else ""))
    _write_inflight(inflight_path, None)


def run(*, database: Path = DB, output: Path = OUT, chunk_size: int = N,
        checkpoint_path: Path = CK, inject: Callable[[str], None] | None = None) -> dict:
    checkpoint = _read_checkpoint(checkpoint_path)
    _recover_from_inflight(output, checkpoint_path)

    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
        candidate_mints = [
            row[0] for row in connection.execute(
                "select mint from wt_walkback_queue where mint>? order by mint limit ?",
                (checkpoint["last_mint"], chunk_size),
            ).fetchall()
        ]
        if not candidate_mints:
            return checkpoint

        lines: list[str] = []
        for mint in candidate_mints:
            queue_row = connection.execute(
                "select mint, creator, funder_wallet from wt_walkback_queue where mint=?",
                (mint,),
            ).fetchone()
            edge_row = connection.execute(
                "select count(*), max(hop_depth), group_concat(distinct candidate_parent), group_concat(distinct mechanism)"
                " from wt_walkback_edge_candidates where mint=?",
                (mint,),
            ).fetchone()
            lines.append(json.dumps(_payload_row(queue_row, edge_row), sort_keys=True) + "\n")

        inflight_state = {
            "checkpoint_rows": checkpoint["rows"],
            "last_mint": candidate_mints[-1],
            "chunk": checkpoint["chunk"] + 1,
        }
        inflight_path = checkpoint_path.with_suffix(checkpoint_path.suffix + ".inflight")
        _write_inflight(inflight_path, inflight_state)
        if inject is not None:
            inject("checkpoint_inflight")

        _append_lines(output, lines)
        if inject is not None:
            inject("output_appended")

        _write_inflight(inflight_path, None)
        checkpoint["last_mint"] = candidate_mints[-1]
        checkpoint["rows"] += len(lines)
        checkpoint["chunk"] += 1
        _write_checkpoint(checkpoint_path, checkpoint)
        if inject is not None:
            inject("checkpoint_committed")
        return checkpoint


def main() -> int:
    print(json.dumps(run()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
