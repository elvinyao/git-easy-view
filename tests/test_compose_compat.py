"""Exercise Compose selection and legacy deployment without touching real services."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def compose_cli(tmp_path):
    program = f"#!{sys.executable}\n" + r'''
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
name = Path(sys.argv[0]).name
with open(os.environ['COMPAT_CALLS'], 'a') as file:
    file.write(json.dumps([name, *args]) + '\n')
if args == ['compose', 'version', '--short']:
    if os.environ['COMPAT_PLUGIN'] == 'missing':
        sys.exit(1)
    print(os.environ['COMPAT_PLUGIN'])
    sys.exit(0)
if name == 'docker-compose' and args == ['version', '--short']:
    print(os.environ['COMPAT_STANDALONE'])
    sys.exit(1 if os.environ['COMPAT_STANDALONE'] == 'missing' else 0)
if args[:1] == ['inspect']:
    print('running ' + os.environ['COMPAT_HEALTH'])
    sys.exit(0)
if args[:1] == ['info']:
    print('20.10.24')
    sys.exit(0)
commands = {'config', 'build', 'run', 'stop', 'up', 'ps', 'port'}
command = next((args[i:] for i, item in enumerate(args) if item in commands), [])
if command[:1] == ['build'] and os.environ['COMPAT_FAIL'] == 'build':
    sys.exit(11)
if command[:1] == ['run'] and command[-1] == 'migrate' and os.environ['COMPAT_FAIL'] == 'migration':
    sys.exit(12)
if command[:2] == ['ps', '-q']:
    if os.environ['COMPAT_HEALTH'] != 'missing':
        print('fake-web-id')
if command[:1] == ['port']:
    print('127.0.0.1:8766')
'''
    for name in ('docker', 'docker-compose'):
        binary = tmp_path / name
        binary.write_text(program)
        binary.chmod(0o755)
    calls = tmp_path / 'calls.jsonl'

    def run(script, *arguments, plugin='missing', standalone='1.29.2', health='healthy', failure='', extra_env=None):
        calls.unlink(missing_ok=True)
        env = {
            **os.environ, 'PATH': f"{tmp_path}:{os.environ['PATH']}",
            'GEV_PROJECT_ROOT': str(ROOT), 'COMPAT_CALLS': str(calls),
            'COMPAT_PLUGIN': plugin, 'COMPAT_STANDALONE': standalone,
            'COMPAT_HEALTH': health, 'COMPAT_FAIL': failure,
            **(extra_env or {}),
        }
        result = subprocess.run(['bash' if script.endswith('run.sh') or script == 'deploy/deploy.sh' else 'sh', str(ROOT / script), *arguments], cwd=ROOT, env=env, capture_output=True, text=True, timeout=20)
        rows = [json.loads(line) for line in calls.read_text().splitlines()] if calls.exists() else []
        return result, rows

    return run


def compose_commands(rows):
    return [row for row in rows if '--project-directory' in row]


def test_default_deploy_falls_back_to_v1_and_waits_for_real_health(compose_cli):
    result, rows = compose_cli('.agent/deploy.sh', '--no-build')
    assert result.returncode == 0, result.stderr
    commands = compose_commands(rows)
    assert commands and all(row[0] == 'docker-compose' for row in commands)
    up = next(row for row in commands if 'up' in row)
    assert '--wait' not in up and '--wait-timeout' not in up
    assert any(row[:2] == ['docker', 'inspect'] for row in rows)
    assert 'Deployment complete' in result.stdout


def test_plugin_is_preferred_when_both_commands_exist(compose_cli):
    result, rows = compose_cli('.agent/deploy.sh', '--no-build', plugin='v2.35.1')
    assert result.returncode == 0, result.stderr
    assert all(row[:2] == ['docker', 'compose'] for row in compose_commands(rows))
    assert not any(row[0] == 'docker-compose' for row in rows)
    assert any('--wait' in row for row in rows)


def test_legacy_update_replaces_only_service_container_and_keeps_volumes(compose_cli):
    result, rows = compose_cli('.agent/deploy.sh', '--no-build')
    assert result.returncode == 0, result.stderr
    commands = compose_commands(rows)
    replace = next(row for row in commands if 'rm' in row)
    assert replace[-4:] == ['rm', '-f', '-s', 'web']
    migration = next(row for row in commands if 'run' in row and row[-1] == 'migrate')
    startup = next(row for row in commands if 'up' in row)
    assert commands.index(migration) < commands.index(replace) < commands.index(startup)
    assert not any('down' in row or '-v' in row or '--volumes' in row for row in commands)


@pytest.mark.parametrize('version', ['1.28.6', '1.29.1', 'missing'])
def test_unsupported_or_missing_compose_fails_before_stopping(compose_cli, version):
    result, rows = compose_cli('.agent/deploy.sh', '--no-build', standalone=version)
    assert result.returncode != 0
    assert not any('stop' in row or 'up' in row for row in rows)
    assert 'Compose' in result.stderr


@pytest.mark.parametrize('health', ['unhealthy', 'missing', 'starting'])
def test_legacy_unhealthy_missing_or_timeout_never_claims_success(compose_cli, health):
    result, _ = compose_cli('.agent/deploy.sh', '--no-build', '--wait-timeout', '1', health=health)
    assert result.returncode != 0
    assert 'Deployment complete' not in result.stdout
    assert 'retained' in result.stderr


def test_legacy_migration_failure_never_starts_web(compose_cli):
    result, rows = compose_cli('.agent/deploy.sh', '--no-build', failure='migration')
    assert result.returncode == 12
    assert not any('up' in row for row in rows)
    assert 'remain stopped' in result.stderr


@pytest.fixture
def intranet_env():
    with tempfile.TemporaryDirectory(dir=ROOT / 'runtime', prefix='compose-compat-') as directory:
        folder = Path(directory)
        env_file = folder / 'settings.env'
        env_file.write_text('COMPOSE_PROJECT_NAME=compat-test\nGEV_APP_IMAGE=git-easy-view:compat\n')
        generated = folder / 'settings.deploy'
        generated.mkdir()
        ca = folder / 'ca'
        ca.mkdir()
        values = {
            'GEV_INTRANET_IMAGE': 'git-easy-view:compat-intranet',
            'GEV_CA_FINGERPRINT': 'test-fingerprint',
            'GEV_CA_DIR': ca.relative_to(ROOT).as_posix(),
            'GEV_BUILD_NETWORK': (generated / 'build-network.json').relative_to(ROOT).as_posix(),
            'GEV_BUILD_CA': (generated / 'company-ca.pem').relative_to(ROOT).as_posix(),
            'GEV_RUNTIME_NETWORK': (generated / 'runtime-network.env').relative_to(ROOT).as_posix(),
        }
        control = ''.join(f'{key}={value}\n' for key, value in values.items())
        env_file.with_suffix('.deploy.env').write_text(control)
        (generated / 'compose.env').write_text(env_file.read_text() + control)
        (generated / 'build-network.json').write_text('{"HTTP_PROXY":"http://secret-proxy:3128"}')
        (generated / 'company-ca.pem').write_text('')
        (generated / 'runtime-network.env').write_text('HTTP_PROXY=http://secret-proxy:3128\n')
        yield env_file.relative_to(ROOT).as_posix(), generated


@pytest.mark.parametrize('plugin,standalone', [('missing', '1.29.2'), ('2.20.3', 'missing')])
def test_legacy_intranet_uses_one_env_file_secret_build_and_compatible_manifest(compose_cli, intranet_env, plugin, standalone):
    env_file, _ = intranet_env
    result, rows = compose_cli('deploy/internal-deploy.sh', '--env-file', env_file, '--pull', plugin=plugin, standalone=standalone)
    assert result.returncode == 0, result.stderr
    commands = compose_commands(rows)
    assert commands and all(row.count('--env-file') == 1 for row in commands)
    assert all(any(value.endswith('/deploy/compose.legacy.yaml') for value in row) for row in commands)
    build = next(row for row in rows if row[:2] == ['docker', 'build'])
    assert '--pull' in build and build.count('--secret') == 2
    assert any('GEV_CA_FINGERPRINT=test-fingerprint' == value for value in build)
    assert not any('secret-proxy' in str(row) for row in rows)
    assert not any('--wait' in row for row in commands)
    assert any(row[:2] == ['docker', 'inspect'] for row in rows)


def test_legacy_intranet_management_retains_arguments_and_rejects_missing_ca(compose_cli, intranet_env):
    env_file, generated = intranet_env
    arguments = ['exec', 'web', 'sh', '-c', "printf '%s' 'two words'"]
    result, rows = compose_cli('deploy/compose.sh', '--env-file', env_file, *arguments)
    assert result.returncode == 0, result.stderr
    assert compose_commands(rows)[-1][-len(arguments):] == arguments
    (generated.parent / 'ca').rmdir()
    result, rows = compose_cli('deploy/compose.sh', '--env-file', env_file, 'up', '-d', 'web')
    assert result.returncode != 0
    assert not compose_commands(rows)


def test_legacy_no_build_rejects_image_without_runtime_network_reader(compose_cli, intranet_env, monkeypatch):
    import app.config

    env_file, _ = intranet_env
    result, rows = compose_cli('deploy/internal-deploy.sh', '--env-file', env_file, '--no-build')
    assert result.returncode == 0, result.stderr
    validation = next(row[-1] for row in compose_commands(rows) if 'run' in row and 'tools' in row)
    monkeypatch.setattr(app.config, 'Settings', lambda: None)
    monkeypatch.setattr(Path, 'is_file', lambda path: True)
    monkeypatch.setattr(Path, 'read_text', lambda path: 'old entrypoint without proxy file support')
    monkeypatch.setenv('GEV_RUNTIME_NETWORK_FILE', '/etc/gev/runtime-network.env')
    with pytest.raises(AssertionError, match='Rebuild'):
        exec(validation, {})


def test_legacy_management_rejects_stale_merged_project_settings(compose_cli, intranet_env):
    env_file, generated = intranet_env
    merged = generated / 'compose.env'
    os.utime(merged, (1, 1))
    result, rows = compose_cli('deploy/compose.sh', '--env-file', env_file, 'stop', 'web')
    assert result.returncode != 0
    assert '--init' in result.stderr
    assert not compose_commands(rows)


@pytest.mark.parametrize('script,arguments', [('.agent/run.sh', ['compose', 'ps']), ('deploy/deploy.sh', ['compose', 'ps'])])
def test_public_launchers_accept_company_cli_image_without_host_compose(compose_cli, script, arguments):
    result, rows = compose_cli(script, *arguments, extra_env={'GEV_DOCKER_CLI_IMAGE': 'registry.example/docker-cli:legacy'})
    assert result.returncode == 0, result.stderr
    calls = [row for row in rows if row[:2] == ['docker', 'run']]
    assert len(calls) == 1 and 'registry.example/docker-cli:legacy' in calls[0]
    assert not any(row[0] == 'docker-compose' or row[:2] == ['docker', 'compose'] for row in rows)
