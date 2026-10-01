"""Rebuild trust from the read-only CA mapping on each container start."""
import os
import sys

from trust import combined_bundle, corporate_pem, trust_environment


def main():
    bundle = combined_bundle(corporate_pem("/etc/gev/ca"))
    environment = dict(os.environ, **trust_environment(bundle))
    command = sys.argv[1:] or [sys.executable, "-m", "app.server"]
    os.execvpe(command[0], command, environment)


if __name__ == "__main__":
    main()
