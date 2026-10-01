"""Build/runtime trust without modifying the image's public trust store."""
from pathlib import Path
import ssl


def corporate_pem(directory):
    root = Path(directory)
    if not root.is_dir():
        raise ValueError("The CA directory must exist")
    contents = []
    for path in sorted(root.iterdir()):
        if path.suffix.lower() not in (".crt", ".pem"):
            continue
        if path.is_symlink() or not path.is_file():
            raise ValueError("CA entries must be regular files, not symlinks")
        data = path.read_text()
        if "PRIVATE KEY" in data or "-----BEGIN CERTIFICATE-----" not in data:
            raise ValueError("CA files must contain PEM certificates only")
        try:
            ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT).load_verify_locations(cadata=data)
        except ssl.SSLError:
            raise ValueError("Invalid CA certificate; use PEM encoded .crt or .pem files") from None
        contents.append(data.strip() + "\n")
    return "".join(contents)


def combined_bundle(corporate, target="/tmp/gev-ca-bundle.pem"):
    public = Path("/etc/ssl/certs/ca-certificates.crt").read_text()
    bundle = public.rstrip() + "\n" + corporate
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.load_verify_locations(cadata=bundle)
    Path(target).write_text(bundle)
    return str(target)


def trust_environment(bundle):
    return {key: bundle for key in (
        "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "PIP_CERT", "CURL_CA_BUNDLE",
        "GIT_SSL_CAINFO", "GIT_PROXY_SSL_CAINFO",
    )}
