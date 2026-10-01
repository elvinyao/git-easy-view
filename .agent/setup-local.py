"""Compatibility entry point for developer tooling; deployment lives in deploy/."""
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "deployment_configuration", Path(__file__).resolve().parents[1] / "deploy/configuration.py"
)
configuration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(configuration)

positive_seconds = configuration.positive_seconds
read_env = configuration.read_env
validate = configuration.validate
prepare = configuration.prepare


def argument_parser():
    return configuration.argument_parser(prog="bash .agent/run.sh deploy")


def main(argv=None):
    return configuration.main(argv, prog="bash .agent/run.sh deploy")


if __name__ == "__main__":
    raise SystemExit(main())
