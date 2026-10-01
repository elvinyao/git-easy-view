"""Real HTTPS Git transport through an isolated local corporate-style proxy."""

from datetime import datetime, timedelta, timezone
from functools import partial
from http.server import BaseHTTPRequestHandler, SimpleHTTPRequestHandler, ThreadingHTTPServer
import select
import socket
import ssl
import subprocess
import threading
from types import SimpleNamespace

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
import pytest

from app.git_service import GitError, TRANSPORT_ENVIRONMENT, _environment, clone_bare
from app.providers.base import Provider


@pytest.fixture
def transport(monkeypatch, tmp_path):
    for key in (*TRANSPORT_ENVIRONMENT, "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(key, raising=False)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Local corporate CA")])
    now = datetime.now(timezone.utc)
    certificate = (
        x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
        .sign(key, hashes.SHA256())
    )
    bundle = tmp_path / "corporate.pem"
    bundle.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    private_key = tmp_path / "server.key"
    private_key.write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption(),
    ))
    repository = tmp_path / "repo.git"
    subprocess.run(["git", "init", "--bare", "--initial-branch=main", str(repository)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repository), "update-server-info"], check=True, capture_output=True)
    api_directory = tmp_path / "api" / "v3"
    api_directory.mkdir(parents=True)
    (api_directory / "status").write_text("ready")

    class Origin(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    origin = ThreadingHTTPServer(("127.0.0.1", 0), partial(Origin, directory=str(tmp_path)))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(bundle, private_key)
    origin.socket = context.wrap_socket(origin.socket, server_side=True)
    origin_port = origin.server_port
    connections = []

    class Proxy(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_CONNECT(self):
            connections.append(self.path)
            assert self.path == f"localhost:{origin_port}"
            with socket.create_connection(("127.0.0.1", origin_port), timeout=5) as upstream:
                self.send_response(200, "Connection established")
                self.end_headers()
                sockets = [self.connection, upstream]
                while True:
                    ready, _, _ = select.select(sockets, [], [], 5)
                    if not ready:
                        return
                    for source in ready:
                        chunk = source.recv(65536)
                        if not chunk:
                            return
                        destination = upstream if source is self.connection else self.connection
                        destination.sendall(chunk)

    proxy = ThreadingHTTPServer(("127.0.0.1", 0), Proxy)
    threads = [threading.Thread(target=server.serve_forever, daemon=True) for server in (origin, proxy)]
    for thread in threads:
        thread.start()
    try:
        yield {
            "url": f"https://localhost:{origin_port}/repo.git",
            "proxy": f"http://127.0.0.1:{proxy.server_port}",
            "bundle": str(bundle), "connections": connections,
        }
    finally:
        for server in (origin, proxy):
            server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join(timeout=5)


@pytest.mark.parametrize("proxy_key", ["HTTPS_PROXY", "https_proxy"])
def test_real_clone_uses_proxy_and_corporate_ca(transport, monkeypatch, tmp_path, proxy_key):
    monkeypatch.setenv(proxy_key, transport["proxy"])
    monkeypatch.setenv("GIT_SSL_CAINFO", transport["bundle"])
    destination = tmp_path / "cloned.git"
    clone_bare(transport["url"], destination)
    assert (destination / "HEAD").exists()
    assert transport["connections"]


@pytest.mark.parametrize("bypass_key", ["NO_PROXY", "no_proxy"])
def test_real_clone_bypasses_proxy_for_internal_host(transport, monkeypatch, tmp_path, bypass_key):
    monkeypatch.setenv("HTTPS_PROXY", transport["proxy"])
    monkeypatch.setenv(bypass_key, "localhost")
    monkeypatch.setenv("GIT_SSL_CAINFO", transport["bundle"])
    clone_bare(transport["url"], tmp_path / "cloned.git")
    assert transport["connections"] == []


def test_connection_ca_overrides_global_bundle_without_disabling_verification(transport, monkeypatch, tmp_path):
    monkeypatch.setenv("GIT_SSL_CAINFO", str(tmp_path / "missing-global.pem"))
    monkeypatch.setenv("GIT_SSL_NO_VERIFY", "1")
    with pytest.raises(GitError, match="Git operation failed"):
        clone_bare(transport["url"], tmp_path / "untrusted.git")
    clone_bare(transport["url"], tmp_path / "trusted.git", env={"GIT_SSL_CAINFO": transport["bundle"]})
    assert (tmp_path / "trusted.git" / "HEAD").exists()


def test_provider_api_honors_proxy_and_global_ca(transport, monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", transport["proxy"])
    monkeypatch.setenv("SSL_CERT_FILE", transport["bundle"])
    connection = SimpleNamespace(kind="github", base_url=transport["url"].removesuffix("/repo.git"), ca_bundle_path=None)
    with Provider(connection, None) as provider:
        assert provider._request(provider.api_root + "/status").text == "ready"
    assert transport["connections"]


def test_network_allowlist_preserves_git_isolation(monkeypatch):
    approved = {
        "HTTP_PROXY": "http://proxy.example:8080", "http_proxy": "http://proxy.example:8080",
        "HTTPS_PROXY": "https://proxy.example:8443", "https_proxy": "https://proxy.example:8443",
        "NO_PROXY": "localhost,.internal", "no_proxy": "localhost,.internal",
        "GIT_SSL_CAINFO": "/etc/gev/roots.pem", "GIT_PROXY_SSL_CAINFO": "/etc/gev/proxy.pem",
        "SSL_CERT_FILE": "/etc/gev/roots.pem", "CURL_CA_BUNDLE": "/etc/gev/roots.pem",
    }
    rejected = {
        "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "http.sslVerify", "GIT_CONFIG_VALUE_0": "false",
        "GIT_SSL_NO_VERIFY": "1", "GIT_CONFIG_GLOBAL": "/tmp/untrusted-config",
        "GIT_ALLOW_PROTOCOL": "ext:file:https", "GIT_ASKPASS": "/tmp/untrusted-askpass",
        "GIT_EXEC_PATH": "/tmp/untrusted-helpers", "LD_PRELOAD": "/tmp/untrusted.so",
        "ALL_PROXY": "socks5://unapproved:1080", "GEV_CREDENTIAL_KEY": "secret",
    }
    for key, value in {**approved, **rejected}.items():
        monkeypatch.setenv(key, value)
    environment = _environment(None, allow_file_transport=False)
    assert all(environment[key] == value for key, value in approved.items())
    assert all(environment.get(key) != value for key, value in rejected.items())
    assert environment["GIT_CONFIG_GLOBAL"] == "/dev/null"
    assert environment["GIT_ALLOW_PROTOCOL"] == "https"
    assert environment["GIT_TERMINAL_PROMPT"] == "0"
    assert environment["HOME"] == "/nonexistent"
    for key in ("GIT_SSL_NO_VERIFY", "GIT_CONFIG_COUNT", "GIT_ALLOW_PROTOCOL", "PATH", "LD_PRELOAD"):
        with pytest.raises(ValueError, match="override is not allowed"):
            _environment({key: "unsafe"}, allow_file_transport=False)
