"""Prepare proxy/CA deployment using only the bundled Python standard library."""
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit

from trust import corporate_pem
import configuration as base


ROOT = Path(__file__).resolve().parents[1]
NETWORK_KEYS = {"HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "PIP_INDEX_URL", "PIP_EXTRA_INDEX_URL"}


def preserve_workspace_owner(path):
    """Root bootstrap must leave 0600 settings editable by the Linux repo owner."""
    if os.geteuid() != 0:
        return
    owner = Path.cwd().stat()
    path = Path(path)
    while path != Path(".") and path.parts and path.parts[0] == "runtime":
        if path.exists():
            current = path.lstat()
            if (current.st_uid, current.st_gid) != (owner.st_uid, owner.st_gid):
                os.chown(path, owner.st_uid, owner.st_gid, follow_symlinks=False)
        path = path.parent


def runtime_path(value):
    path = Path(value)
    if (not re.fullmatch(r"runtime/[A-Za-z0-9_./-]+", path.as_posix()) or ".." in path.parts
            or not path.resolve().is_relative_to(Path("runtime").resolve())):
        raise ValueError("Deployment paths must be inside runtime/ using letters, digits, dots, underscores or hyphens")
    return path


def write_private(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w") as file:
        file.write(content)
    os.chmod(temporary, 0o600)
    temporary.replace(path)
    preserve_workspace_owner(path)


def compose_environment(values):
    """Serialize one dotenv file accepted by both Compose generations."""
    lines = []
    for key, value in values.items():
        # Single quotes keep dollars literal; dotenv supports these two escapes.
        escaped = value.replace("\\", "\\\\").replace("'", "\\'")
        lines.append(f"{key}='{escaped}'\n")
    return "".join(lines)


def normalize_network(values):
    if set(values) - NETWORK_KEYS:
        raise ValueError("Network file supports only HTTP_PROXY, HTTPS_PROXY, NO_PROXY, PIP_INDEX_URL and PIP_EXTRA_INDEX_URL")
    normalized = {}
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "PIP_INDEX_URL", "PIP_EXTRA_INDEX_URL"):
        value = values.get(name, "")
        if value:
            try:
                url = urlsplit(value)
                valid = url.scheme in ("http", "https") and bool(url.hostname) and not url.fragment
                if url.port is not None and not 1 <= url.port <= 65535:
                    valid = False
            except ValueError:
                valid = False
            if not valid or any(char.isspace() for char in value) or "'" in value or '"' in value:
                raise ValueError(f"Invalid {name}; use an HTTP(S) URL with URL-encoded credentials")
        normalized[name] = value
    bypass = [value.strip() for value in values.get("NO_PROXY", "").split(",") if value.strip()]
    bypass += ["localhost", "127.0.0.1", "::1", "web"]
    normalized["NO_PROXY"] = ",".join(dict.fromkeys(bypass))
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY"):
        normalized[name.lower()] = normalized[name]
    # Avoid a Docker client's implicit all-proxy setting routing personal traffic.
    normalized.update(ALL_PROXY="", all_proxy="")
    return normalized


def argument_parser():
    parser = base.argument_parser()
    parser.prog = "bash deploy/deploy.sh"
    parser.description = "Deploy one SQLite application container with optional outbound proxy and corporate CA"
    parser.epilog = "Manage services: bash deploy/deploy.sh compose --env-file runtime/local.env ps (or logs, stop, etc.)"
    parser.add_argument("--init", action="store_true", help="prepare configuration only, without building or starting services")
    parser.add_argument("--network-file", help="literal proxy/index env file inside runtime/")
    parser.add_argument("--ca-dir", help="directory inside runtime/ containing PEM .crt/.pem certificates")
    parser.add_argument("--bind-address", help="published IPv4 address; default 127.0.0.1, use a LAN address for direct access")
    return parser


def prepare(args):
    if args.migrate_sqlite:
        raise ValueError("Use the original deploy --migrate-sqlite workflow before this SQLite-only deployment")
    target, created = base.prepare(args)
    preserve_workspace_owner(target)
    if target == Path("runtime/local.env"):
        preserve_workspace_owner(Path("runtime/demo-access.json"))
    os.umask(0o077)
    control = target.with_suffix(".deploy.env")
    previous = base.read_env(control) if control.exists() else {}
    network = runtime_path(args.network_file or previous.get("GEV_NETWORK_FILE") or target.with_suffix(".network.env").as_posix())
    ca_dir = runtime_path(args.ca_dir or previous.get("GEV_CA_DIR") or "runtime/ca")
    generated = runtime_path(str(target.with_suffix("")) + ".deploy")
    if network in (target, control) or network.is_relative_to(generated):
        raise ValueError("Network settings must use a separate file from application/generated configuration")
    if ca_dir == generated or ca_dir.is_relative_to(generated) or generated.is_relative_to(ca_dir):
        raise ValueError("CA directory must be separate from generated deployment files")
    bind_address = args.bind_address or previous.get("GEV_BIND_ADDRESS", "127.0.0.1")
    try:
        ipaddress.IPv4Address(bind_address)
    except ipaddress.AddressValueError:
        raise ValueError("--bind-address must be an IPv4 address") from None
    ca_dir.mkdir(parents=True, exist_ok=True)
    preserve_workspace_owner(ca_dir)
    if not network.exists():
        write_private(network, (ROOT / "deploy/network.env.example").read_text())
    os.chmod(network, 0o600)
    preserve_workspace_owner(network)
    normalized = normalize_network(base.read_env(network))
    certificates = corporate_pem(ca_dir)
    build_network = generated / "build-network.json"
    runtime_network = generated / "runtime-network.env"
    build_ca = generated / "company-ca.pem"
    # Compose raw env files preserve literal percent/dollar signs in proxy passwords.
    runtime_values = {key: value for key, value in normalized.items() if not key.startswith("PIP_")}
    build_values = {key: value for key, value in normalized.items() if value or not key.startswith("PIP_")}
    write_private(build_network, json.dumps(build_values))
    write_private(runtime_network, "".join(f"{key}={value}\n" for key, value in runtime_values.items()))
    write_private(build_ca, certificates)
    application = base.read_env(target)
    base_image = application.get("GEV_APP_IMAGE", "git-easy-view:local")
    if "@" in base_image:
        raise ValueError("GEV_APP_IMAGE must be a buildable image name/tag, not a digest")
    image = base_image + ("-intranet" if ":" in base_image.rsplit("/", 1)[-1] else ":intranet")
    controls = {
        "GEV_NETWORK_FILE": network.as_posix(), "GEV_CA_DIR": ca_dir.as_posix(),
        "GEV_BUILD_NETWORK": build_network.as_posix(), "GEV_RUNTIME_NETWORK": runtime_network.as_posix(),
        "GEV_BUILD_CA": build_ca.as_posix(), "GEV_CA_FINGERPRINT": hashlib.sha256(certificates.encode()).hexdigest(),
        "GEV_BIND_ADDRESS": bind_address,
        "GEV_INTRANET_IMAGE": image,
    }
    write_private(control, "".join(f"{key}={value}\n" for key, value in controls.items()))
    # Compose v1 accepts only one --env-file. Preserve application/control
    # precedence without evaluating a credentials file as shell code.
    write_private(generated / "compose.env", compose_environment({**application, **controls}))
    print(f"{'Created' if created else 'Preserved'} {target}; prepared {network} and CA mapping {ca_dir}.")
    print("Proxy values and credentials are not printed. Deployment remains a single application container.")
    if args.init:
        print("Configuration only. Edit the network file and CA directory, then repeat without --init.")
    return controls


def main():
    parser = argument_parser()
    args = parser.parse_args()
    try:
        prepare(args)
    except (ValueError, OSError) as error:
        # Filesystem/SSL errors must not disclose proxy URLs or private content.
        parser.exit(1, f"Deployment configuration error: {error}\n")


if __name__ == "__main__":
    main()
