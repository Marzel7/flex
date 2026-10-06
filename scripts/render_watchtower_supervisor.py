"""Pure renderer for the proposed-live two-program Supervisor migration."""
import hashlib
from pathlib import Path

ALLOWED = ("operation_monitor_worker", "watchtower_api")
TEMPLATE = Path(__file__).resolve().parents[1] / "config/supervisor/watchtower_final_runtime.conf"
LEDGER_SHA256 = "ad75e5e910662d0259ca095df9e7ae20501136dc0a8d09ccfec62d1e9273b5b8"

def stanza(text, name):
    start = text.index(f"[program:{name}]")
    end = text.find("\n[", start + 1)
    return text[start:] if end < 0 else text[start:end]

def remove_stanza(text, name):
    return text.replace(stanza(text, name), "", 1)

def render_fragment(root: Path, sha: str) -> str:
    root = root.resolve(); ledger = root / "docs/audits/watchtower-opening-offset-audit.v2.json"
    if hashlib.sha256(ledger.read_bytes()).hexdigest() != LEDGER_SHA256: raise ValueError("LEDGER_IDENTITY_INVALID")
    out = TEMPLATE.read_text().replace("__WATCHTOWER_ROOT__", str(root)).replace("__WATCHTOWER_SHA__", sha)
    if "__WATCHTOWER_" in out or any(f"[program:{name}]" not in out for name in ALLOWED): raise ValueError("RENDER_IDENTITY_INVALID")
    return out

def render_proposed_live(source: str, root: Path, sha: str, include_dir: Path):
    if any(source.count(f"[program:{name}]") != 1 for name in ALLOWED): raise ValueError("EMBEDDED_SCOPE_INVALID")
    candidate = remove_stanza(remove_stanza(source, "operation_monitor_worker"), "watchtower_api").rstrip()
    return candidate + "\n\n[include]\nfiles = " + str(include_dir / "*.conf") + "\n", render_fragment(root, sha)
