"""Temporary-only transformer.  Never writes the proposed-live artifacts."""
import re
from pathlib import Path

def isolate(config: str, fragment: str, root: Path):
    logs = root / "logs"; logs.mkdir(parents=True, exist_ok=True)
    changes = []
    def sub(text, pattern, value, section):
        nonlocal changes
        def one(match):
            changes.append({"section": section, "field": match.group(1), "isolated": value, "reason": "zero-start isolated validation"})
            return match.group(1) + "=" + value
        return re.sub(pattern, one, text, flags=re.M)
    config = sub(config, r"^(file)\s*=.*$", str(root / "supervisor.sock"), "unix_http_server")
    config = sub(config, r"^(pidfile)\s*=.*$", str(root / "supervisord.pid"), "supervisord")
    config = sub(config, r"^(logfile)\s*=.*$", str(logs / "supervisord.log"), "supervisord")
    config = sub(config, r"^(childlogdir)\s*=.*$", str(logs), "supervisord")
    config = sub(config, r"^(serverurl)\s*=.*$", "unix://" + str(root / "supervisor.sock"), "supervisorctl")
    for field in ("autostart", "autorestart"):
        config = sub(config, rf"^({field})\s*=.*$", "false", "all-programs")
        fragment = sub(fragment, rf"^({field})\s*=.*$", "false", "managed-programs")
    for field in ("stdout_logfile", "stderr_logfile"):
        config = sub(config, rf"^({field})\s*=.*$", str(logs / (field + ".log")), "all-programs")
        fragment = sub(fragment, rf"^({field})\s*=.*$", str(logs / (field + ".log")), "managed-programs")
    # Inherited runtime log destinations are also unsafe even though no child
    # starts; make the copy self-contained without changing proposed-live text.
    live_logs = "/Users/kevinkeaveney/Dev/claude/flex/logs"
    if live_logs in config:
        config = config.replace(live_logs, str(logs / "runtime"))
        changes.append({"section": "supervisord/program-environment", "field": "runtime-log-paths", "isolated": str(logs / "runtime"), "reason": "no live log reference"})
    return config, fragment, changes
