"""Birdeye credential authority. Importing this module performs no I/O."""
from __future__ import annotations

import os
from pathlib import Path


class BirdeyeConfigurationError(RuntimeError):
    """Configuration failure whose message never contains credential material."""


def _normalize(value: str) -> str:
    return value.strip().strip('\'"').strip()


def birdeye_credential() -> str:
    """Use BIRDEYE only, retaining the existing BIRDEYE dotenv source.

    An explicitly empty environment setting fails closed. Legacy aliases are
    never considered. File reads happen only when BIRDEYE is absent from env.
    """
    if "BIRDEYE" in os.environ:
        value = _normalize(os.environ["BIRDEYE"])
    else:
        value = ""
        env_file = Path(__file__).resolve().parents[2] / ".env"
        try:
            lines = env_file.read_text(encoding="utf-8").splitlines()
        except OSError:
            lines = ()
        for line in lines:
            line = line.strip()
            if line.startswith("export "):
                line = line[7:].lstrip()
            name, separator, configured = line.partition("=")
            if separator and name.strip() == "BIRDEYE":
                value = _normalize(configured.split("#", 1)[0])
    if not value:
        raise BirdeyeConfigurationError("BIRDEYE_CONFIGURATION_REQUIRED")
    return value


def validated_birdeye_credential(explicit_key: str | None = None) -> str:
    """Keep constructor signatures; reject overrides of the approved value."""
    approved = birdeye_credential()
    if explicit_key is not None and explicit_key != approved:
        raise BirdeyeConfigurationError("BIRDEYE_CREDENTIAL_OVERRIDE_FORBIDDEN")
    return approved
