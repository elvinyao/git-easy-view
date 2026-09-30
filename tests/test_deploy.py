"""Regression checks for deployment preservation, isolation and failure ordering."""

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

from cryptography.fernet import Fernet
import pytest

from app.config import Settings


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("deployment_setup", ROOT / ".agent/setup-local.py")
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


@pytest.fixture
def deployment_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    old_mask = os.umask(0o022)
    yield tmp_path
    os.umask(old_mask)


def options(*arguments):
    return setup.argument_parser().parse_args(arguments)


def test_first_deployment_is_valid_and_redeployment_preserves_secrets(deployment_directory, capsys):
    assert setup.main([]) == 0
    target = Path("runtime/local.env")
    original = target.read_bytes()
    access = Path("runtime/demo-access.json").read_bytes()
    values = setup.read_env(target)
    settings = Settings(**{key.removeprefix("GEV_").lower(): value for key, value in values.items() if key.startswith("GEV_") and key != "GEV_ENV_FILE"})
    assert not settings.production
    assert settings.decrypt_token(settings.encrypt_token("example-token")) == "example-token"
    assert values["COMPOSE_PROJECT_NAME"] == "git-easy-view"
    assert values["GEV_APP_IMAGE"] == "git-easy-view:local"
    assert target.stat().st_mode & 0o777 == 0o600
    assert Path("runtime/demo-access.json").stat().st_mode & 0o777 == 0o600
    assert setup.main([]) == 0
    assert target.read_bytes() == original
    assert Path("runtime/demo-access.json").read_bytes() == access
    output = capsys.readouterr().out
    assert "Preserved runtime/local.env" in output
    for key in ("POSTGRES_PASSWORD", "GEV_CREDENTIAL_KEY", "GEV_SESSION_SECRET"):
        assert values[key] not in output


def test_legacy_local_configuration_is_preserved(deployment_directory):
    target, _ = setup.prepare(options())
    values = setup.read_env(target)
    del values["GEV_ENV_FILE"]
    del values["COMPOSE_PROJECT_NAME"]
    target.write_text("".join(f"{key}={value}\n" for key, value in values.items()))
    original = target.read_bytes()
    assert setup.prepare(options()) == (target, False)
    assert target.read_bytes() == original


def test_production_has_independent_secrets_and_project(deployment_directory):
    setup.prepare(options())
    target, created = setup.prepare(options("--production"))
    assert created
    production = setup.read_env(target)
    local = setup.read_env(Path("runtime/local.env"))
    assert production["COMPOSE_PROJECT_NAME"] != local["COMPOSE_PROJECT_NAME"]
    assert production["GEV_APP_IMAGE"] != local["GEV_APP_IMAGE"]
    assert production["GEV_PORT"] == "8767"
    assert production["GEV_PRODUCTION"] == production["GEV_COOKIE_SECURE"] == "true"
    Fernet(production["GEV_CREDENTIAL_KEY"].encode())
    for key in ("POSTGRES_PASSWORD", "GEV_CREDENTIAL_KEY", "GEV_SESSION_SECRET"):
        assert production[key] != local[key]
    original = target.read_bytes()
    assert setup.prepare(options("--production")) == (target, False)
    assert target.read_bytes() == original


@pytest.mark.parametrize("arguments,match", [
    (("--production", "--project-name", "git-easy-view"), "different COMPOSE_PROJECT_NAME"),
    (("--env-file", "runtime/staging.env"), "Specify --project-name"),
    (("--env-file", "../outside.env"), "inside runtime"),
    (("--port", "70000"), "between 1 and 65535"),
])
def test_invalid_new_configuration_does_not_create_env(deployment_directory, arguments, match):
    with pytest.raises(ValueError, match=match):
        setup.prepare(options(*arguments))
    assert not list(deployment_directory.rglob("*.env"))


@pytest.mark.parametrize("arguments,match", [
    (("--production", "--env-file", "runtime/local.env"), "deployment mode differs"),
    (("--port", "9000"), "cannot change an existing"),
    (("--project-name", "another-project"), "cannot change an existing"),
])
def test_existing_configuration_cannot_be_switched_by_flags(deployment_directory, arguments, match):
    target, _ = setup.prepare(options())
    original = target.read_bytes()
    with pytest.raises(ValueError, match=match):
        setup.prepare(options(*arguments))
    assert target.read_bytes() == original


def test_custom_environment_has_its_own_env_file_and_project(deployment_directory):
    args = options("--env-file", "runtime/development.env", "--project-name", "gev-development", "--port", "8770")
    target, _ = setup.prepare(args)
    values = setup.read_env(target)
    assert values["GEV_ENV_FILE"] == "./runtime/development.env"
    assert values["GEV_PORT"] == "8770"
    assert not Path("runtime/demo-access.json").exists()
    assert not setup.prepare(args)[1]


def test_invalid_configuration_error_does_not_expose_password(deployment_directory, capsys):
    target, _ = setup.prepare(options())
    password = setup.read_env(target)["POSTGRES_PASSWORD"]
    original = target.read_text().replace("@postgres:5432/gev", "@other-host:5432/gev")
    target.write_text(original)
    with pytest.raises(SystemExit) as result:
        setup.main([])
    assert result.value.code == 1
    assert password not in capsys.readouterr().err
    assert target.read_text() == original


@pytest.fixture
def fake_docker(tmp_path):
    binary = tmp_path / "docker"
    binary.write_text(f"#!{sys.executable}\n" + """
import json, os, sys
args = sys.argv[1:]
with open(os.environ['DEPLOY_CALLS'], 'a') as file:
    file.write(json.dumps(args) + '\\n')
if args[0] == 'compose':
    command = args[7:]
    if os.environ.get('DEPLOY_FAIL') == 'build' and command[0] == 'build':
        sys.exit(11)
    if os.environ.get('DEPLOY_FAIL') == 'migration' and command[:1] == ['run'] and command[-1] == 'migrate':
        sys.exit(12)
    if os.environ.get('DEPLOY_FAIL') == 'health' and command[:1] == ['up'] and command[-1] == 'dispatcher':
        sys.exit(13)
    if command[:1] == ['port']:
        print('127.0.0.1:8766')
""")
    binary.chmod(0o755)
    calls = tmp_path / "calls.jsonl"

    def run(*arguments, failure=""):
        environment = {
            **os.environ,
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
            "GEV_PROJECT_ROOT": "/example/project with spaces",
            "DEPLOY_CALLS": str(calls),
            "DEPLOY_FAIL": failure,
        }
        result = subprocess.run(["sh", str(ROOT / ".agent/deploy.sh"), *arguments], env=environment, capture_output=True, text=True)
        raw = [json.loads(line) for line in calls.read_text().splitlines()]
        commands = [row[7:] for row in raw if row[0] == "compose"]
        assert all(row[1:6] == ["--project-directory", "/example/project with spaces", "-f", "/example/project with spaces/compose.yaml", "--env-file"] for row in raw if row[0] == "compose")
        return result, commands

    return run


def test_deployment_stops_writers_and_runs_migration_on_every_attempt(fake_docker):
    result, calls = fake_docker()
    assert result.returncode == 0, result.stderr
    stop = calls.index(["stop", "web", "worker", "dispatcher"])
    migrate = calls.index(["run", "--rm", "--no-deps", "-T", "migrate"])
    startup = calls.index(["up", "-d", "--no-deps", "--no-build", "--wait", "--wait-timeout", "180", "web", "worker", "dispatcher"])
    assert stop < migrate < startup
    assert calls[0] == ["config", "--quiet"]
    assert "No accounts" in result.stdout
    assert not any("down" in call or "demo" in call for call in calls)


def test_fast_deployment_reuses_image_and_waits_for_health(fake_docker):
    result, calls = fake_docker("--no-build", "--wait-timeout", "25")
    assert result.returncode == 0
    assert not any(call[0] == "build" for call in calls)
    assert any(call[:1] == ["up"] and "25" in call and "--wait" in call for call in calls)
    assert ["run", "--rm", "--no-deps", "-T", "migrate"] in calls


@pytest.mark.parametrize("failure,expected", [("build", 11), ("migration", 12), ("health", 13)])
def test_deployment_failure_is_nonzero_and_never_deletes_volumes(fake_docker, failure, expected):
    result, calls = fake_docker(failure=failure)
    assert result.returncode == expected
    assert "Deployment failed during" in result.stderr
    assert "Deployment complete" not in result.stdout
    assert not any(call[0] == "down" for call in calls)
    if failure == "build":
        assert not any(call[0] == "stop" for call in calls)
    if failure == "migration":
        assert "remain stopped" in result.stderr
        assert not any(call[0] == "up" and call[-1] == "dispatcher" for call in calls)
