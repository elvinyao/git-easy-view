"""Run isolated source tests from a tools container without deployment settings.

Integration/scale/E2E suites intentionally use their own environment and should
continue to run with python -m pytest in the corresponding verification service.
"""

import os
import subprocess
import sys


def main() -> int:
    if any(os.environ.get(flag) == "1" for flag in ("GEV_INTEGRATION", "GEV_SCALE", "GEV_E2E")):
        print("Use python -m pytest in the verification service for opt-in suites.", file=sys.stderr)
        return 2
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GEV_")}
    print("Running source tests without deployment GEV_* settings.", flush=True)
    return subprocess.call([sys.executable, "-m", "pytest", *sys.argv[1:]], env=environment)


if __name__ == "__main__":
    raise SystemExit(main())
