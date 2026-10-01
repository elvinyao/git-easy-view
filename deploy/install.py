"""Use BuildKit-mounted proxy settings and CA only for dependency installation."""
import json
import os
from pathlib import Path
import subprocess
import sys

from trust import combined_bundle, trust_environment


def main():
    network = json.loads(Path("/run/secrets/build_network").read_text())
    bundle = combined_bundle(Path("/run/secrets/corporate_ca").read_text())
    env = dict(os.environ, **network, **trust_environment(bundle))
    try:
        subprocess.run([sys.executable, "-m", "pip", "install", "--no-cache-dir", *sys.argv[1:]],
                       env=env, check=True)
    finally:
        Path(bundle).unlink(missing_ok=True)


if __name__ == "__main__":
    main()
