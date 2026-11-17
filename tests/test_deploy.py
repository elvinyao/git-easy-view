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
spec = importlib.util.spec_from_file_location("deployment_setup", ROOT / "deploy/configuration.py")
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
    assert Path("runtime/local-repositories").is_dir()
    assert setup.main([]) == 0
    assert target.read_bytes() == original
    assert Path("runtime/demo-access.json").read_bytes() == access
    output = capsys.readouterr().out
    assert "Preserved runtime/local.env" in output
    assert values["GEV_DATABASE_URL"] == "sqlite:////data/db/gev.sqlite3"
    assert "GEV_JOB_BACKEND" not in values
    for key in ("GEV_CREDENTIAL_KEY", "GEV_SESSION_SECRET"):
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
    for key in ("GEV_CREDENTIAL_KEY", "GEV_SESSION_SECRET"):
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
    password = setup.read_env(target)["GEV_CREDENTIAL_KEY"]
    original = target.read_text().replace("sqlite:////data/db/gev.sqlite3", "postgresql://user:SECRET_PROBE@other-host/gev")
    target.write_text(original)
    with pytest.raises(SystemExit) as result:
        setup.main([])
    assert result.value.code == 1
    assert password not in capsys.readouterr().err
    assert target.read_text() == original
