"""Rebuild trust from the read-only CA mapping on each container start."""
import os
from pathlib import Path
import sys

from trust import combined_bundle, corporate_pem, trust_environment


def runtime_environment():
    """Read the optional legacy Compose mount without dotenv interpolation."""
    source = os.environ.get("GEV_RUNTIME_NETWORK_FILE")
    if not source:
        return {}
    allowed = {"HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "ALL_PROXY"}
    allowed |= {key.lower() for key in allowed}
    values = {}
    for line in Path(source).read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or key not in allowed or key in values or "\x00" in value:
            raise ValueError("Invalid runtime network configuration")
        values[key] = value
    return values


def main():
    bundle = combined_bundle(corporate_pem("/etc/gev/ca"))
    environment = {**os.environ, **runtime_environment(), **trust_environment(bundle)}
    command = sys.argv[1:] or [sys.executable, "-m", "app.server"]
    os.execvpe(command[0], command, environment)


if __name__ == "__main__":
    main()
