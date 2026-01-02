"""
Environment variable loader for .env file support
Loads environment variables from .env file for use across the application
"""

import os
import json
from pathlib import Path


def load_env():
    """
    Load environment variables from .env file in the project root.
    Supports both direct environment variables and JSON-encoded values.
    """
    env_file = Path(__file__).parent.parent / ".env"

    if not env_file.exists():
        print(f"⚠️  Warning: .env file not found at {env_file}")
        return False

    try:
        with open(env_file, "r") as f:
            for line in f:
                # Skip empty lines and comments
                line = line.strip()
                if not line or line.startswith("#"):
                    continue

                # Parse KEY=VALUE
                if "=" not in line:
                    continue

                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip()

                # Don't override existing environment variables
                if key not in os.environ:
                    # Handle JSON arrays (for TRADING_KEYPAIR)
                    if value.startswith("[") and value.endswith("]"):
                        # Store as string, will be parsed when needed
                        os.environ[key] = value
                    else:
                        os.environ[key] = value

        return True
    except Exception as e:
        print(f"❌ Error loading .env file: {e}")
        return False


# Auto-load on import
if __name__ != "__main__":
    load_env()
