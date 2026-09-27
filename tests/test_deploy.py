"""Deploy templates are portable: placeholders only, every variable defined.

(REQ_PORTABLE_CONFIG)
"""

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
DEPLOY = ROOT / "deploy"
EXAMPLES = ROOT / "examples"
# A user's real deploy/.env is git-ignored and must not be scanned as a template.
TEMPLATE_FILES = sorted(
    p for base in (DEPLOY, EXAMPLES) for p in base.rglob("*") if p.is_file() and p.name != ".env"
)

_IPV4 = re.compile(r"\b(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\b")
_COORD = re.compile(r"-?\d{1,3}\.\d{5,}")
# Credential-like: 32+ chars mixing lower case, upper case and a digit. The
# mix requirement keeps UPPER_SNAKE variable names such as
# DOCKER_INFLUXDB_INIT_ADMIN_TOKEN (exactly 32 chars) from matching.
_LONG_SECRET = re.compile(
    r"(?=[A-Za-z0-9+/_-]*[a-z])(?=[A-Za-z0-9+/_-]*[A-Z])(?=[A-Za-z0-9+/_-]*\d)"
    r"\b[A-Za-z0-9+/_-]{32,}={0,2}"
)


def _env_keys():
    keys = {}
    for line in (DEPLOY / "example.env").read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            key, _, value = line.partition("=")
            keys[key] = value
    return keys


def test_template_files_exist():
    names = {p.relative_to(ROOT).as_posix() for p in TEMPLATE_FILES}
    for required in (
        "deploy/docker-compose.yml",
        "deploy/example.env",
        "deploy/ingest/Dockerfile",
        "deploy/grafana-provisioning/datasources/trackiwi.yaml",
        "deploy/grafana-provisioning/dashboards/trackiwi.yaml",
        "deploy/systemd/trackiwi-ingest.service",
        "deploy/systemd/trackiwi-ingest.timer",
        "examples/influx.example.toml",
    ):
        assert required in names


def test_every_compose_variable_is_in_example_env():
    compose = (DEPLOY / "docker-compose.yml").read_text()
    used = {m.group(1) for m in re.finditer(r"\$\{([A-Z_][A-Z0-9_]*)(?::-[^}]*)?\}", compose)}
    defaulted = {m.group(1) for m in re.finditer(r"\$\{([A-Z_][A-Z0-9_]*):-[^}]*\}", compose)}
    missing = used - defaulted - set(_env_keys())
    assert not missing, f"compose uses variables absent from example.env: {sorted(missing)}"


def test_provisioning_variables_are_passed_to_grafana():
    compose = (DEPLOY / "docker-compose.yml").read_text()
    for path in (DEPLOY / "grafana-provisioning").rglob("*.yaml"):
        for var in re.findall(r"\$([A-Z_][A-Z0-9_]*)", path.read_text()):
            assert f"{var}:" in compose, f"{path.name} uses ${var} but grafana is not given it"


def test_example_env_values_are_placeholders():
    allowed_literals = {"admin", "home", "trackiwi", "600"}
    for key, value in _env_keys().items():
        assert value.startswith("changeme") or value in allowed_literals, f"{key}={value}"


def test_no_real_values_in_templates():
    """REQ_PORTABLE_CONFIG: no IPs, coordinates or credential-looking strings."""
    for path in TEMPLATE_FILES:
        text = path.read_text(encoding="utf-8")
        ips = {ip for ip in _IPV4.findall(text) if ip not in ("127.0.0.1", "0.0.0.0")}
        assert not ips, f"{path}: IP addresses {ips}"
        assert not _COORD.search(text), f"{path}: coordinate-like number"
        assert not _LONG_SECRET.search(text), f"{path}: credential-like string"


def test_systemd_units_have_no_absolute_user_paths():
    for path in (DEPLOY / "systemd").iterdir():
        text = path.read_text()
        assert "/home/" not in text and "/Users/" not in text, path


def test_dockerignore_keeps_private_data_out_of_the_image():
    text = (ROOT / ".dockerignore").read_text()
    for pattern in (".venv", ".git", "*.db", "config.json", ".env", "deploy/.env"):
        assert pattern in text.splitlines(), pattern


def _gitignore_private_patterns():
    """The patterns under `.gitignore`'s "Private data" heading, negations excluded."""
    lines = (ROOT / ".gitignore").read_text().splitlines()
    start = next(i for i, line in enumerate(lines) if "Private data" in line)
    end = next(i for i, line in enumerate(lines) if i > start and line.startswith("# ---"))
    return [
        line.strip()
        for line in lines[start + 1 : end]
        if line.strip() and not line.startswith("#") and not line.startswith("!")
    ]


def test_dockerignore_mirrors_every_private_gitignore_pattern_at_any_depth():
    """REQ_PORTABLE_CONFIG / REQ_NO_PRIVATE_DATA: an export in the clone never enters the build.

    A `.dockerignore` pattern without `**/` matches only at the context root,
    so each private `.gitignore` pattern must appear with the `**/` prefix.
    """
    patterns = _gitignore_private_patterns()
    assert {"*.gpx", "*.geojson", "*.csv", "*.json", "*.db", ".env.*"} <= set(patterns)
    lines = set((ROOT / ".dockerignore").read_text().splitlines())
    missing = [p for p in patterns if f"**/{p}" not in lines]
    assert not missing, f".dockerignore lacks **/ variants of: {missing}"


def test_dockerfile_copies_only_what_the_build_needs():
    """An allowlist: a COPY layer keeps whatever it copies, even after a later `rm`."""
    allowed = {"pyproject.toml", "LICENSE", "trackiwi/"}
    copies = [
        line.split()[1:-1]
        for line in (DEPLOY / "ingest" / "Dockerfile").read_text().splitlines()
        if line.startswith(("COPY", "ADD"))
    ]
    assert copies, "the Dockerfile must copy the package"
    for sources in copies:
        assert set(sources) <= allowed, f"COPY of {sources} (allowed: {sorted(allowed)})"


def _compose_service_blocks():
    """Map service name -> its indented block of docker-compose.yml (no YAML parser needed)."""
    text = (DEPLOY / "docker-compose.yml").read_text()
    services = text.split("\nservices:\n", 1)[1].split("\nvolumes:\n", 1)[0]
    blocks: dict[str, list[str]] = {}
    name = None
    for line in services.splitlines():
        if re.match(r"^  [a-z][a-z0-9_-]*:\s*$", line):
            name = line.strip().rstrip(":")
            blocks[name] = []
        elif name:
            blocks[name].append(line)
    return {k: "\n".join(v) for k, v in blocks.items()}


def test_published_ports_bind_to_loopback_by_default():
    """REQ_DEPLOY_LEAST_EXPOSURE: no port reaches the LAN unless deliberately configured.

    Each `ports:` entry must carry a host address that is either the literal
    loopback or a variable *defaulting* to it, so a bare `deploy/.env` (the
    example with placeholders filled in) publishes nothing beyond this machine.
    """
    compose = (DEPLOY / "docker-compose.yml").read_text()
    entries = re.findall(r'^\s+- "([^"]*:\d+:\d+)"\s*$', compose, flags=re.M)
    assert entries, "the compose file publishes at least InfluxDB and Grafana"
    for entry in entries:
        host, _, _ = entry.rpartition(":")
        host, _, _ = host.rpartition(":")
        assert host == "127.0.0.1" or re.fullmatch(r"\$\{[A-Z_][A-Z0-9_]*:-127\.0\.0\.1\}", host), (
            f"port mapping {entry!r} is not bound to 127.0.0.1 by default"
        )


def test_lan_exposure_is_opt_in_and_documented():
    """Bind-address variables: defaulted in compose, commented out in example.env, in the README."""
    compose = (DEPLOY / "docker-compose.yml").read_text()
    example = (DEPLOY / "example.env").read_text()
    readme = (ROOT / "README.md").read_text()
    for var in ("INFLUXDB_BIND_ADDRESS", "GRAFANA_BIND_ADDRESS"):
        assert f"${{{var}:-127.0.0.1}}" in compose, var
        assert var not in _env_keys(), f"{var} must stay commented out in example.env"
        assert f"# {var}=" in example, f"{var} is not shown in example.env"
        assert var in readme, f"README does not document {var}"


def test_ingest_never_receives_the_admin_token():
    """REQ_DEPLOY_LEAST_EXPOSURE: the ingest gets a bucket-scoped token, never the operator token.

    The operator token (`INFLUXDB_TOKEN`, InfluxDB's `DOCKER_INFLUXDB_INIT_ADMIN_TOKEN`)
    can read and delete every bucket; the ingest needs read + write on one.
    """
    blocks = _compose_service_blocks()
    assert "${INFLUXDB_INGEST_TOKEN}" in blocks["ingest"]
    assert "${INFLUXDB_TOKEN}" not in blocks["ingest"]
    assert "INFLUXDB_INGEST_TOKEN" in _env_keys()


def test_documented_ingest_token_scope_is_read_and_write_on_the_bucket():
    """`influx check` lists buckets (read) and `push` writes; both flags must appear together."""
    for path in (DEPLOY / "example.env", ROOT / "README.md"):
        text = path.read_text()
        for flag in ("influx auth create", "--write-bucket", "--read-bucket"):
            assert flag in text, f"{path.name} lacks {flag}"
        assert "--all-access" not in text and "--operator" not in text, path.name


def test_systemd_service_reads_the_optional_influx_env_file():
    """A user unit sees no shell-rc variables; the token must reach it another way."""
    lines = (DEPLOY / "systemd" / "trackiwi-ingest.service").read_text().splitlines()
    assert "EnvironmentFile=-%h/.config/trackiwi/influx.env" in lines
    assert "ExecStart=%h/.local/bin/trackiwi ingest" in lines
