"""Proxy/CA deployment preserves data, validates trust, and fails safely."""

from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import ssl
import subprocess
import sys
import tempfile

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
import pytest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("intranet_setup", ROOT / "deploy/setup.py")
setup = importlib.util.module_from_spec(spec)
sys.path.insert(0, str(ROOT / "deploy"))
try:
    spec.loader.exec_module(setup)
    import trust
    import entrypoint
finally:
    sys.path.pop(0)


@pytest.fixture
def deployment_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    original_mask = os.umask(0o022)
    yield tmp_path
    os.umask(original_mask)


@pytest.fixture
def certificate():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Deployment test corporate CA")])
    now = datetime.now(timezone.utc)
    value = (
        x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    return value.public_bytes(serialization.Encoding.PEM).decode()


def options(*arguments):
    return setup.argument_parser().parse_args(arguments)


def literal_values(path):
    return dict(line.split("=", 1) for line in Path(path).read_text().splitlines() if line and not line.startswith("#"))


def test_personal_initialization_and_repeat_preserve_existing_app_configuration(deployment_directory, capsys):
    controls = setup.prepare(options("--init"))
    application = Path("runtime/local.env")
    original = application.read_bytes()
    reserved_accounts = Path("runtime/demo-access.json").read_bytes()
    assert setup.base.read_env(application)["GEV_DATABASE_URL"] == "sqlite:////data/db/gev.sqlite3"
    runtime = literal_values(controls["GEV_RUNTIME_NETWORK"])
    assert all(runtime[key] == "" for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"))
    assert runtime["NO_PROXY"] == runtime["no_proxy"] == "localhost,127.0.0.1,::1,web"
    assert controls["GEV_BIND_ADDRESS"] == "127.0.0.1"
    assert controls["GEV_INTRANET_IMAGE"] != setup.base.read_env(application)["GEV_APP_IMAGE"]
    assert Path(controls["GEV_BUILD_CA"]).read_text() == ""
    assert not any(key.startswith("PIP_") for key in json.loads(Path(controls["GEV_BUILD_NETWORK"]).read_text()))
    assert setup.prepare(options()) == controls
    assert application.read_bytes() == original
    assert Path("runtime/demo-access.json").read_bytes() == reserved_accounts
    for path in [application, Path("runtime/local.deploy.env"), *Path("runtime/local.deploy").iterdir(), Path(controls["GEV_NETWORK_FILE"])]:
        assert path.stat().st_mode & 0o777 == 0o600
    output = capsys.readouterr().out
    assert "Configuration only" in output and "Preserved runtime/local.env" in output
    for key in ("GEV_SESSION_SECRET", "GEV_CREDENTIAL_KEY"):
        assert setup.base.read_env(application)[key] not in output


@pytest.mark.skipif(sys.platform != "linux" or os.geteuid() != 0, reason="requires the Linux root Docker bootstrap")
def test_root_bootstrap_leaves_private_configuration_editable_by_workspace_owner(deployment_directory):
    original_owner = deployment_directory.stat()
    expected_owner = (12345, 12346)
    os.chown(deployment_directory, *expected_owner)
    try:
        setup.prepare(options("--init"))
        runtime = Path("runtime")
        paths = [runtime, *runtime.rglob("*")]
        originals = {path: path.read_bytes() for path in paths if path.is_file()}
        for path in paths:
            metadata = path.stat()
            assert (metadata.st_uid, metadata.st_gid) == expected_owner
            assert metadata.st_mode & 0o777 == (0o700 if path.is_dir() else 0o600)
        # Also repair settings left root-owned by an earlier Docker invocation.
        for path in paths:
            os.chown(path, 0, 0)
        setup.prepare(options())
        for path in paths:
            metadata = path.stat()
            assert (metadata.st_uid, metadata.st_gid) == expected_owner
            assert metadata.st_mode & 0o777 == (0o700 if path.is_dir() else 0o600)
        assert all(path.read_bytes() == content for path, content in originals.items())
    finally:
        os.chown(deployment_directory, original_owner.st_uid, original_owner.st_gid)


def test_proxy_passwords_remain_literal_and_python_indexes_only_reach_build(deployment_directory, capsys):
    controls = setup.prepare(options("--init"))
    proxy = "http://person:p%40ss$literal%25@proxy.internal:8080"
    index = "https://person:p%40ss$literal%25@packages.internal/simple"
    Path(controls["GEV_NETWORK_FILE"]).write_text(
        f"HTTP_PROXY={proxy}\nHTTPS_PROXY={proxy}\nNO_PROXY=.internal, localhost,.internal\n"
        f"PIP_INDEX_URL={index}\nPIP_EXTRA_INDEX_URL=https://extra.internal/simple\n"
    )
    setup.prepare(options())
    runtime = literal_values(controls["GEV_RUNTIME_NETWORK"])
    build = json.loads(Path(controls["GEV_BUILD_NETWORK"]).read_text())
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        assert runtime[key] == build[key] == proxy
    assert runtime["NO_PROXY"] == runtime["no_proxy"] == ".internal,localhost,127.0.0.1,::1,web"
    assert build["PIP_INDEX_URL"] == index
    assert build["PIP_EXTRA_INDEX_URL"] == "https://extra.internal/simple"
    assert not any(key.startswith("PIP_") for key in runtime)
    assert proxy not in capsys.readouterr().out


def test_merged_compose_environment_preserves_application_values_and_control_precedence(deployment_directory):
    from dotenv import dotenv_values

    controls = setup.prepare(options("--init"))
    application = Path("runtime/local.env")
    secret = "a-long-session-secret-with-$literal-backslash\\and'quote"
    values = setup.base.read_env(application)
    values["GEV_SESSION_SECRET"] = secret
    values["GEV_BIND_ADDRESS"] = "192.0.2.10"
    application.write_text("".join(f"{key}={value}\n" for key, value in values.items()))
    original = application.read_bytes()
    controls = setup.prepare(options())

    merged = Path("runtime/local.deploy/compose.env")
    parsed = dotenv_values(merged)
    assert parsed == {**values, **controls}
    assert parsed["GEV_SESSION_SECRET"] == secret
    assert parsed["GEV_BIND_ADDRESS"] == "127.0.0.1"
    assert merged.stat().st_mode & 0o777 == 0o600
    assert application.read_bytes() == original


def test_legacy_entrypoint_reads_literal_runtime_proxy_without_build_indexes(deployment_directory, monkeypatch):
    controls = setup.prepare(options("--init"))
    proxy = "http://person:p%40ss$literal%25@proxy.internal:8080"
    Path(controls["GEV_NETWORK_FILE"]).write_text(
        f"HTTP_PROXY={proxy}\nHTTPS_PROXY={proxy}\nPIP_INDEX_URL=https://packages.internal/simple\n"
    )
    setup.prepare(options())
    monkeypatch.setenv("GEV_RUNTIME_NETWORK_FILE", controls["GEV_RUNTIME_NETWORK"])
    monkeypatch.setenv("HTTP_PROXY", "http://old-proxy.invalid")
    assert entrypoint.runtime_environment() == literal_values(controls["GEV_RUNTIME_NETWORK"])
    assert entrypoint.runtime_environment()["HTTP_PROXY"] == proxy
    assert "PIP_INDEX_URL" not in entrypoint.runtime_environment()


def test_modern_entrypoint_does_not_load_a_runtime_network_file(monkeypatch):
    monkeypatch.delenv("GEV_RUNTIME_NETWORK_FILE", raising=False)
    assert entrypoint.runtime_environment() == {}


@pytest.mark.parametrize("content", [
    "PIP_INDEX_URL=https://SECRET_PROBE@packages.internal/simple\n",
    "GEV_DATABASE_URL=SECRET_PROBE\n",
    "HTTP_PROXY=http://SECRET_PROBE@proxy\nHTTP_PROXY=http://duplicate\n",
    "export HTTP_PROXY=http://SECRET_PROBE@proxy\n",
    "SECRET_PROBE\n",
])
def test_legacy_entrypoint_rejects_invalid_network_files_without_echoing_secrets(tmp_path, monkeypatch, content):
    network = tmp_path / "network.env"
    network.write_text(content)
    monkeypatch.setenv("GEV_RUNTIME_NETWORK_FILE", str(network))
    with pytest.raises(ValueError, match="runtime network") as error:
        entrypoint.runtime_environment()
    assert "SECRET_PROBE" not in str(error.value)


def test_legacy_entrypoint_applies_proxy_and_trust_before_exec(tmp_path, monkeypatch):
    network = tmp_path / "network.env"
    proxy = "http://person:p$literal@proxy.internal"
    network.write_text(f"HTTP_PROXY={proxy}\nALL_PROXY=\n")
    monkeypatch.setenv("GEV_RUNTIME_NETWORK_FILE", str(network))
    monkeypatch.setenv("HTTP_PROXY", "http://old-proxy.invalid")
    monkeypatch.setattr(entrypoint, "corporate_pem", lambda path: "test CA")
    monkeypatch.setattr(entrypoint, "combined_bundle", lambda contents: "/tmp/test-ca.pem")
    monkeypatch.setattr(sys, "argv", ["entrypoint.py", "python", "-m", "app.server"])
    calls = []
    monkeypatch.setattr(os, "execvpe", lambda *args: calls.append(args))
    entrypoint.main()
    command, arguments, environment = calls[0]
    assert command == "python" and arguments == ["python", "-m", "app.server"]
    assert environment["HTTP_PROXY"] == proxy and environment["ALL_PROXY"] == ""
    assert environment["SSL_CERT_FILE"] == environment["GIT_PROXY_SSL_CAINFO"] == "/tmp/test-ca.pem"


def test_custom_ca_mapping_and_bind_address_survive_redeployment(deployment_directory, certificate):
    controls = setup.prepare(options("--init", "--ca-dir", "runtime/company-ca", "--bind-address", "0.0.0.0"))
    application = Path("runtime/local.env").read_bytes()
    ca_file = Path("runtime/company-ca/root.crt")
    ca_file.write_text(certificate)
    updated = setup.prepare(options())
    assert updated["GEV_CA_DIR"] == "runtime/company-ca"
    assert updated["GEV_BIND_ADDRESS"] == "0.0.0.0"
    assert updated["GEV_CA_FINGERPRINT"] != controls["GEV_CA_FINGERPRINT"]
    assert updated["GEV_CA_FINGERPRINT"] == hashlib.sha256(certificate.encode()).hexdigest()
    assert Path(updated["GEV_BUILD_CA"]).read_text() == certificate
    assert setup.prepare(options())["GEV_CA_FINGERPRINT"] == updated["GEV_CA_FINGERPRINT"]
    ca_file.unlink()
    assert setup.prepare(options())["GEV_CA_FINGERPRINT"] == hashlib.sha256(b"").hexdigest()
    assert Path("runtime/local.env").read_bytes() == application


@pytest.mark.parametrize("values,match", [
    ({"ALL_PROXY": "http://proxy:8080"}, "supports only"),
    ({"PIP_TRUSTED_HOST": "packages.internal"}, "supports only"),
    ({"HTTPS_PROXY": "socks5://proxy:1080"}, "Invalid HTTPS_PROXY"),
    ({"HTTP_PROXY": "http://proxy:70000"}, "Invalid HTTP_PROXY"),
    ({"HTTPS_PROXY": "http://proxy/#fragment"}, "Invalid HTTPS_PROXY"),
    ({"HTTP_PROXY": "http://user:raw password@proxy:8080"}, "Invalid HTTP_PROXY"),
    ({"PIP_INDEX_URL": "file:///packages"}, "Invalid PIP_INDEX_URL"),
])
def test_invalid_network_settings_are_rejected_without_disclosing_values(values, match):
    with pytest.raises(ValueError, match=match) as failure:
        setup.normalize_network(values)
    assert all(value not in str(failure.value) for value in values.values())


@pytest.mark.parametrize("arguments,match", [
    (("--network-file", "runtime/local.env"), "separate file"),
    (("--network-file", "runtime/local.deploy.env"), "separate file"),
    (("--network-file", "runtime/local.deploy/runtime-network.env"), "separate file"),
    (("--network-file", "../outside.env"), "inside runtime"),
    (("--ca-dir", "/etc/ssl/certs"), "inside runtime"),
    (("--ca-dir", "runtime/../outside"), "inside runtime"),
    (("--ca-dir", "runtime/local.deploy"), "CA"),
    (("--ca-dir", "runtime/local.deploy/ca"), "CA"),
    (("--ca-dir", "runtime"), "inside runtime"),
    (("--env-file", "runtime/team/app.env", "--project-name", "test-team", "--ca-dir", "runtime/team"), "CA"),
    (("--bind-address", "proxy.internal"), "IPv4"),
    (("--migrate-sqlite",), "original deploy"),
])
def test_invalid_paths_and_collisions_preserve_application_configuration(deployment_directory, arguments, match):
    setup.prepare(options("--init"))
    original = Path("runtime/local.env").read_bytes()
    with pytest.raises(ValueError, match=match):
        setup.prepare(options(*arguments))
    assert Path("runtime/local.env").read_bytes() == original


def test_bad_certificate_is_rejected_before_generated_configuration_changes(deployment_directory):
    controls = setup.prepare(options("--init"))
    original = {path: path.read_bytes() for path in Path("runtime/local.deploy").iterdir()}
    Path(controls["GEV_CA_DIR"], "broken.crt").write_text("-----BEGIN CERTIFICATE-----\nnot-a-certificate\n-----END CERTIFICATE-----\n")
    with pytest.raises(ValueError, match="Invalid CA certificate"):
        setup.prepare(options())
    assert all(path.read_bytes() == content for path, content in original.items())


def test_read_only_ca_mapping_builds_separate_bundle_without_changing_public_trust(tmp_path, certificate):
    source = tmp_path / "mapped-ca"
    source.mkdir()
    root = source / "company.pem"
    root.write_text(certificate)
    root.chmod(0o444)
    source.chmod(0o555)
    public = Path("/etc/ssl/certs/ca-certificates.crt")
    original = public.read_bytes()
    target = tmp_path / "combined.pem"
    try:
        assert trust.combined_bundle(trust.corporate_pem(source), target) == str(target)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.load_verify_locations(cafile=target)
        assert context.cert_store_stats()["x509_ca"] > 1
        assert target.read_bytes().startswith(original.rstrip())
        assert target.read_text().endswith(certificate)
        assert root.read_text() == certificate and public.read_bytes() == original
        assert list(source.iterdir()) == [root]
        environment = trust.trust_environment(str(target))
        assert environment["GIT_SSL_CAINFO"] == environment["GIT_PROXY_SSL_CAINFO"] == environment["SSL_CERT_FILE"] == str(target)
        assert "GIT_SSL_NO_VERIFY" not in environment and "PIP_TRUSTED_HOST" not in environment
    finally:
        source.chmod(0o755)
        root.chmod(0o644)


@pytest.mark.parametrize("kind", ["private-key", "symlink", "directory"])
def test_ca_mapping_rejects_non_certificate_entries(tmp_path, certificate, kind):
    directory = tmp_path / "ca"
    directory.mkdir()
    path = directory / "root.pem"
    if kind == "private-key":
        path.write_text(certificate + "-----BEGIN PRIVATE KEY-----\nsecret\n")
    elif kind == "symlink":
        outside = tmp_path / "outside.pem"
        outside.write_text(certificate)
        path.symlink_to(outside)
    else:
        path.mkdir()
    with pytest.raises(ValueError, match="CA"):
        trust.corporate_pem(directory)


@pytest.fixture
def fake_docker(tmp_path):
    binary = tmp_path / "docker"
    binary.write_text(f"#!{sys.executable}\n" + """
import json, os, sys
args = sys.argv[1:]
with open(os.environ['DEPLOY_CALLS'], 'a') as file:
    file.write(json.dumps(args) + '\\n')
if args == ['compose', 'version', '--short']:
    print('2.35.1')
    sys.exit(0)
if args[0] == 'compose':
    command = args[9:]
    failure = os.environ.get('DEPLOY_FAIL')
    if failure == 'build' and command[0] == 'build':
        sys.exit(11)
    if failure == 'configuration' and command[:1] == ['run'] and 'tools' in command:
        sys.exit(12)
    if failure == 'migration' and command[:1] == ['run'] and command[-1] == 'migrate':
        sys.exit(13)
    if failure == 'health' and command[:1] == ['up']:
        sys.exit(14)
    if command[:1] == ['port']:
        print('127.0.0.1:8766')
""")
    binary.chmod(0o755)
    calls = tmp_path / "calls.jsonl"
    # compose.sh deliberately resolves config through /workspace. Keep an empty,
    # uniquely named test config there, without touching a real deployment.
    (ROOT / "runtime").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".test-intranet-", dir=ROOT / "runtime") as temporary:
        control = Path(temporary) / "test.deploy.env"
        control.write_text("")
        env_file = (Path(temporary) / "test.env").relative_to(ROOT).as_posix()

        def run(*arguments, failure=""):
            environment = {
                **os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}",
                "GEV_PROJECT_ROOT": "/example/project with spaces", "DEPLOY_CALLS": str(calls), "DEPLOY_FAIL": failure,
            }
            result = subprocess.run(
                ["sh", str(ROOT / "deploy/internal-deploy.sh"), "--env-file", env_file, *arguments],
                cwd=ROOT, env=environment, capture_output=True, text=True,
            )
            raw = [json.loads(line) for line in calls.read_text().splitlines()] if calls.exists() else []
            rows = [row for row in raw if row[:2] == ["compose", "--project-directory"]]
            assert all(row[1:9] == [
                "--project-directory", "/example/project with spaces", "-f", "/example/project with spaces/deploy/compose.yaml",
                "--env-file", f"/workspace/{env_file}", "--env-file", f"/workspace/{control.relative_to(ROOT).as_posix()}",
            ] for row in rows)
            return result, [row[9:] for row in rows]

        yield run


def test_deploy_validates_before_stopping_then_migrates_and_waits_for_health(fake_docker):
    result, calls = fake_docker("--pull")
    assert result.returncode == 0, result.stderr
    validation = next(index for index, call in enumerate(calls) if call[0] == "run" and "tools" in call)
    stop = calls.index(["stop", "web"])
    migration = calls.index(["run", "--rm", "--no-deps", "-T", "migrate"])
    startup = calls.index(["up", "-d", "--no-deps", "--no-build", "--wait", "--wait-timeout", "180", "web"])
    assert calls[0] == ["config", "--quiet"]
    assert calls.index(["build", "--pull", "web"]) < validation < stop < migration < startup
    assert not any("down" in call or "demo" in call for call in calls)
    assert "Deployment complete" in result.stdout


def test_no_build_reuses_image_and_preserves_health_wait(fake_docker):
    result, calls = fake_docker("--no-build", "--wait-timeout", "30")
    assert result.returncode == 0, result.stderr
    assert not any(call[0] == "build" for call in calls)
    assert any(call[0] == "up" and "--wait" in call and "30" in call for call in calls)


def test_init_does_not_build_stop_or_start_services(fake_docker):
    result, calls = fake_docker("--init")
    assert result.returncode == 0, result.stderr
    assert calls == []


@pytest.mark.parametrize("failure,code", [("build", 11), ("configuration", 12), ("migration", 13), ("health", 14)])
def test_deploy_failure_never_deletes_data_or_claims_success(fake_docker, failure, code):
    result, calls = fake_docker(failure=failure)
    assert result.returncode == code
    assert "Configuration and volumes retained" in result.stderr
    assert "Deployment complete" not in result.stdout
    assert not any(call[0] in ("down", "rm") for call in calls)
    if failure in ("build", "configuration"):
        assert not any(call[0] == "stop" for call in calls)
    if failure == "migration":
        assert ["stop", "web"] in calls
        assert not any(call[0] == "up" for call in calls)
