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


def test_systemd_service_reads_the_optional_influx_env_file():
    """A user unit sees no shell-rc variables; the token must reach it another way."""
    lines = (DEPLOY / "systemd" / "trackiwi-ingest.service").read_text().splitlines()
    assert "EnvironmentFile=-%h/.config/trackiwi/influx.env" in lines
    assert "ExecStart=%h/.local/bin/trackiwi ingest" in lines
