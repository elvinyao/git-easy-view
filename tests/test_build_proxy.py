"""Exercise dependency installation through a real local proxy and private CA."""

import base64
from datetime import datetime, timedelta, timezone
from functools import partial
import hashlib
from http.server import BaseHTTPRequestHandler, SimpleHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import select
import socket
import ssl
import subprocess
import sys
import threading
import zipfile

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
import pytest


def _wheel(path):
    """Produce a valid dependency-free wheel without running a package builder."""
    metadata = "proxy_probe-1.0.dist-info"
    files = {
        "proxy_probe.py": b'VALUE = "installed through the proxy"\n',
        f"{metadata}/METADATA": b"Metadata-Version: 2.1\nName: proxy-probe\nVersion: 1.0\n",
        f"{metadata}/WHEEL": b"Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
    }
    records = []
    for name, content in files.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).rstrip(b"=").decode()
        records.append(f"{name},sha256={digest},{len(content)}\n")
    files[f"{metadata}/RECORD"] = ("".join(records) + f"{metadata}/RECORD,,\n").encode()
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)


@pytest.fixture
def package_transport(tmp_path):
    """The deliberately unresolvable index hostname can only work via the proxy."""
    hostname = "packages.invalid"
    now = datetime.now(timezone.utc)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Dependency test corporate CA")])
    ca = (
        x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name)
        .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(x509.KeyUsage(
            digital_signature=True, content_commitment=False, key_encipherment=False,
            data_encipherment=False, key_agreement=False, key_cert_sign=True,
            crl_sign=True, encipher_only=None, decipher_only=None,
        ), critical=True)
        .sign(ca_key, hashes.SHA256())
    )
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    leaf = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hostname)]))
        .issuer_name(ca_name).public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(hostname)]), critical=False)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    certificate = tmp_path / "server.pem"
    certificate.write_bytes(leaf.public_bytes(serialization.Encoding.PEM))
    private_key = tmp_path / "server.key"
    private_key.write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption(),
    ))
    packages = tmp_path / "packages"
    packages.mkdir()
    wheel_name = "proxy_probe-1.0-py3-none-any.whl"
    _wheel(packages / wheel_name)
    index = tmp_path / "simple" / "proxy-probe"
    index.mkdir(parents=True)
    (index / "index.html").write_text(f'<a href="/packages/{wheel_name}">{wheel_name}</a>')
    requests = []
    connections = []

    class Origin(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            requests.append(self.path)
            super().do_GET()

    origin = ThreadingHTTPServer(("127.0.0.1", 0), partial(Origin, directory=str(tmp_path)))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certificate, private_key)
    origin.socket = context.wrap_socket(origin.socket, server_side=True)
    authority = f"{hostname}:{origin.server_port}"

    class Proxy(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_CONNECT(self):
            connections.append(self.path)
            if self.path != authority:
                self.send_error(403)
                return
            with socket.create_connection(("127.0.0.1", origin.server_port), timeout=3) as upstream:
                self.send_response(200, "Connection established")
                self.end_headers()
                sockets = [self.connection, upstream]
                while True:
                    ready, _, _ = select.select(sockets, [], [], 3)
                    if not ready:
                        return
                    for source in ready:
                        try:
                            chunk = source.recv(65536)
                            if not chunk:
                                return
                            destination = upstream if source is self.connection else self.connection
                            destination.sendall(chunk)
                        except (ConnectionError, TimeoutError):
                            return

    proxy = ThreadingHTTPServer(("127.0.0.1", 0), Proxy)
    threads = [threading.Thread(target=server.serve_forever, daemon=True) for server in (origin, proxy)]
    for thread in threads:
        thread.start()
    try:
        yield {
            "ca": ca.public_bytes(serialization.Encoding.PEM).decode(),
            "index": f"https://{authority}/simple", "authority": authority,
            "proxy": f"http://127.0.0.1:{proxy.server_port}",
            "requests": requests, "connections": connections, "wheel": wheel_name,
        }
    finally:
        for server in (origin, proxy):
            server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join(timeout=5)


def test_build_installer_uses_secret_proxy_and_verifies_corporate_ca(package_transport, monkeypatch, tmp_path, capfd):
    deploy = Path(__file__).resolve().parents[1] / "deploy"
    monkeypatch.syspath_prepend(str(deploy))
    spec = importlib.util.spec_from_file_location("build_proxy_install", deploy / "install.py")
    installer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installer)

    # Map only the BuildKit mounts and output path; execute the real pip subprocess.
    network = tmp_path / "build-network.json"
    corporate = tmp_path / "corporate-ca.pem"
    network.write_text(json.dumps({
        "HTTPS_PROXY": package_transport["proxy"],
        "https_proxy": package_transport["proxy"],
        "NO_PROXY": "localhost,127.0.0.1", "no_proxy": "localhost,127.0.0.1",
        "PIP_INDEX_URL": package_transport["index"],
    }))
    corporate.write_text("")
    mounts = {"/run/secrets/build_network": network, "/run/secrets/corporate_ca": corporate}
    monkeypatch.setattr(installer, "Path", lambda value: mounts.get(str(value), Path(value)))
    bundle = tmp_path / "combined.pem"
    combine = installer.combined_bundle
    monkeypatch.setattr(installer, "combined_bundle", lambda contents: combine(contents, target=bundle))
    for name in tuple(os.environ):
        if name.upper().endswith("_PROXY") or name.startswith("PIP_") or name in (
            "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE",
        ):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PIP_CONFIG_FILE", "/dev/null")
    monkeypatch.setenv("PIP_DISABLE_PIP_VERSION_CHECK", "1")
    monkeypatch.setenv("PIP_DEFAULT_TIMEOUT", "3")
    monkeypatch.setenv("PIP_RETRIES", "0")
    target = tmp_path / "installed"
    monkeypatch.setattr(sys, "argv", [
        "install.py", "--target", str(target), "--no-deps", "--no-compile", "proxy-probe==1.0",
    ])

    with pytest.raises(subprocess.CalledProcessError):
        installer.main()
    failure = capfd.readouterr()
    assert "CERTIFICATE_VERIFY_FAILED" in failure.out + failure.err
    assert not (target / "proxy_probe.py").exists()
    assert package_transport["connections"] == [package_transport["authority"]]
    assert package_transport["requests"] == []

    corporate.write_text(package_transport["ca"])
    installer.main()
    assert (target / "proxy_probe.py").read_text() == 'VALUE = "installed through the proxy"\n'
    assert (target / "proxy_probe-1.0.dist-info" / "METADATA").is_file()
    assert package_transport["connections"].count(package_transport["authority"]) >= 3
    assert "/simple/proxy-probe/" in package_transport["requests"]
    assert f'/packages/{package_transport["wheel"]}' in package_transport["requests"]
    assert not bundle.exists()
