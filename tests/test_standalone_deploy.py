"""The public deploy launcher works independently of developer tooling."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
PYTHON_IMAGE = "python:3.13-bookworm"
CLI_IMAGE = "docker:28-cli"


@pytest.fixture
def standalone(tmp_path):
    checkout = tmp_path / "checkout with spaces"
    shutil.copytree(ROOT / "deploy", checkout / "deploy")
    outside = tmp_path / "outside checkout"
    outside.mkdir()
    binaries = tmp_path / "fake binaries"
    binaries.mkdir()
    docker = binaries / "docker"
    docker.write_text(f"#!{sys.executable}\n" + """
import json, os, subprocess, sys
args = sys.argv[1:]
with open(os.environ['DEPLOY_CALLS'], 'a') as file:
    file.write(json.dumps(args) + '\\n')
image = next(value for value in args if value in ('python:3.13-bookworm', 'docker:28-cli'))
status = int(os.environ['SETUP_STATUS' if image.startswith('python:') else 'DEPLOY_STATUS'])
if status:
    sys.exit(status)
if image.startswith('python:') and os.environ['RUN_SETUP'] == '1':
    workspace = next(value.removesuffix(':/workspace') for value in args if value.endswith(':/workspace'))
    command = args[args.index(image) + 1:]
    # Exercise only stdlib setup, inside the surrounding Docker pytest process.
    result = subprocess.run([sys.executable, '-S', *command[1:]], cwd=workspace)
    sys.exit(result.returncode)
""")
    docker.chmod(0o755)
    calls_file = tmp_path / "calls.jsonl"

    def run(*arguments, setup_status=0, deploy_status=0, real_setup=False):
        calls_file.unlink(missing_ok=True)
        environment = {
            **os.environ,
            "PATH": f"{binaries}:{os.environ['PATH']}",
            "DEPLOY_CALLS": str(calls_file),
            "SETUP_STATUS": str(setup_status),
            "DEPLOY_STATUS": str(deploy_status),
            "RUN_SETUP": str(int(real_setup)),
        }
        result = subprocess.run(
            ["bash", str(checkout / "deploy/deploy.sh"), *arguments],
            cwd=outside, env=environment, capture_output=True, text=True,
        )
        calls = [json.loads(line) for line in calls_file.read_text().splitlines()] if calls_file.exists() else []
        for call in calls:
            assert call[:4] == ["run", "--rm", "-i", "--init"]
            assert ["-v", f"{checkout}:/workspace"] in [call[index:index + 2] for index in range(len(call) - 1)]
            assert ["-w", "/workspace"] in [call[index:index + 2] for index in range(len(call) - 1)]
        assert not (checkout / ".agent").exists()
        assert not (outside / "runtime").exists()
        return result, calls

    return checkout, run


def command(call, image):
    return call[call.index(image) + 1:]


@pytest.mark.parametrize("flag", ["--init", "--help", "-h"])
def test_init_and_help_only_launch_configuration_container(standalone, flag):
    _, run = standalone
    result, calls = run(flag)
    assert result.returncode == 0, result.stderr
    assert len(calls) == 1
    assert command(calls[0], PYTHON_IMAGE) == ["python", "deploy/setup.py", flag]
    assert not any("docker.sock" in value for value in calls[0])


def test_deployment_prepares_before_orchestration_and_preserves_arguments(standalone):
    checkout, run = standalone
    # The wrapper forwards opaque arguments; setup validates allowable paths.
    arguments = ["--env-file", "runtime/settings with spaces.env", "--wait-timeout", "42", "--no-build"]
    result, calls = run(*arguments)
    assert result.returncode == 0, result.stderr
    assert len(calls) == 2
    assert command(calls[0], PYTHON_IMAGE) == ["python", "deploy/setup.py", *arguments]
    assert command(calls[1], CLI_IMAGE) == ["sh", "deploy/internal-deploy.sh", *arguments]
    assert f"GEV_PROJECT_ROOT={checkout}" in calls[1]
    assert f"{checkout}:{checkout}" in calls[1]
    assert "/var/run/docker.sock:/var/run/docker.sock" in calls[1]


def test_compose_forwards_management_command_without_preparing_configuration(standalone):
    _, run = standalone
    arguments = ["--env-file", "runtime/local.env", "exec", "web", "sh", "-c", "printf '%s' 'two words'"]
    result, calls = run("compose", *arguments)
    assert result.returncode == 0, result.stderr
    assert len(calls) == 1
    assert command(calls[0], CLI_IMAGE) == ["sh", "deploy/compose.sh", *arguments]


def test_configuration_failure_prevents_deployment_and_preserves_exit_status(standalone):
    _, run = standalone
    result, calls = run("--no-build", setup_status=17)
    assert result.returncode == 17
    assert len(calls) == 1
    assert PYTHON_IMAGE in calls[0]


@pytest.mark.parametrize("arguments,expected_calls", [([], 2), (["compose", "ps"], 1)])
def test_deployment_or_management_failure_preserves_exit_status(standalone, arguments, expected_calls):
    _, run = standalone
    result, calls = run(*arguments, deploy_status=23)
    assert result.returncode == 23
    assert len(calls) == expected_calls


def test_actual_initialization_needs_only_deploy_directory_and_preserves_secrets(standalone):
    checkout, run = standalone
    result, calls = run("--init", real_setup=True)
    assert result.returncode == 0, result.stderr
    assert len(calls) == 1
    application = checkout / "runtime/local.env"
    original = application.read_bytes()
    assert b"GEV_DATABASE_URL=sqlite:////data/db/gev.sqlite3" in original
    assert (checkout / "runtime/local.deploy.env").is_file()
    assert (checkout / "runtime/local.network.env").is_file()
    result, calls = run("--init", real_setup=True)
    assert result.returncode == 0, result.stderr
    assert len(calls) == 1
    assert application.read_bytes() == original
    assert "Preserved runtime/local.env" in result.stdout
