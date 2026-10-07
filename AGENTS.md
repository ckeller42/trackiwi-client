# AGENTS.md

Canonical rules for anyone — human or agent — working in this repository.
`CLAUDE.md` is a one-line `@AGENTS.md` import so Claude Code loads this file.

Unofficial read-only client for the trackiwi GPS API. Design spec:
`docs/superpowers/specs/2026-09-17-trackiwi-client-design.md` (local-only, gitignored). Each rule below
names the code or `REQ_*` (in `docs/requirements.rst`) that owns the reason.

## Rules

- **Zero runtime dependencies.** Standard library only; dev tools go in
  `[project.optional-dependencies].dev`.
- **Read-only.** The only state-changing trackiwi call is `DELETE /api/v2/session`
  in `logout`. Never add writes to tours, markers, alarms or shares.
- **No private data in this repo, ever.** Fixtures are synthetic; there is no
  record-from-live mode, deliberately.
- **Never log or print the token.** Server-controlled text that can reach
  output goes through `_http.redact`: `client._check` redacts the error body,
  and `Client._api` scrubs the token from every `TrackiwiError` beneath it
  (`REQ_TOKEN_NEVER_LOGGED`).
- **Never hardcode the API base** (it comes from the login response's `server`)
  and **require `https://`** via `client._require_https`, wherever it came from.
- **Convert foreign exceptions at the module boundary, not in `main()`.**
  `main()` catches only `AuthError`, `TrackiwiError`, `BrokenPipeError` and
  `KeyboardInterrupt`; a blanket catch would present bugs as user mistakes.
  `Client.load`, `Store.__enter__`, `_decode_json` and `cmd_export` raise
  `TrackiwiError` naming the way out. Both exceptions live in
  `trackiwi/__init__.py` so `client` and `store` never import each other.
- **Validate config *values*, not only the document** (`Client.load`).
- **A stored token must always be removable.** On any `TrackiwiError` from
  `Client.load()`, `cmd_logout` falls back to `Client.forget_local_session()`
  and warns on stderr; it never revokes over a non-https base
  (`REQ_TOKEN_REMOVABLE`).
- **`BrokenPipeError` is success only in `cmd_export`**, where the payload is
  written; `main()` exits 1 so `sync 2>&1 | head -1 && export` cannot run on a
  partial sync. `_silence_stderr()` is the shared devnull redirect.
- **A malformed field means "skip and count the row"**, never crash, never
  store it: non-finite coordinates and `fix_at` outside `_MIN_FIX_AT.._MAX_FIX_AT`.
  `to_gpx` and `to_geojson` raise on such cached rows and `cmd_export` converts
  it; `to_csv` is a raw dump and deliberately does not. `parse_positions`
  returns the highest id on the page, skipped rows included, and `sync`
  advances to it (`REQ_SYNC_PAST_MALFORMED`).
- **Read-only commands must not create the cache.** `export` and `purge` touch
  the database only if it exists; `purge` unlinks via `store.cache_files`
  without opening it, so it works on a corrupt file.
- **Only a *corrupt* cache may be told to run `purge --yes`.** A locked/busy
  `sqlite3.OperationalError` gets "wait and re-run" (see `Store._open_failure`;
  check the lock case first, it subclasses `DatabaseError`).
- **An export covering several trackers keeps them apart:** one `<trk>` per
  tracker, one GeoJSON `Feature` each with `tracker_id` in `properties`.
- **Ignore the `trackiwi-app-command` response header.** Never act on it.
- **`xml.etree.ElementTree` in `export.py` is deliberate**: it only serialises
  GPX, never parses untrusted input. Do not add `defusedxml`; scanner flags are
  false positives.
- **`heading.py` is pure and offline** (imports only stdlib, `Row`, `by_tracker`;
  `cmd_heading` never creates the cache). Parked heading is the bearing between
  the last two moving fixes of the *same* tracker, never raw `course` at zero
  speed. `stale_after` stays a parameter. Caveats live in its docstring and
  `--help`; a test pins the latter.
- **`influx.py` is the only module that talks to InfluxDB**; `lineprotocol.py`
  is pure; `influx.py` and `client.py` never import each other.
- **InfluxDB writes** are only `trackiwi_position` line protocol to the one
  configured target (`REQ_INFLUX_WRITE_SCOPE`). No deletes, bucket management or
  other measurements.
- **`mirror_state` advances only after InfluxDB acknowledges a batch**
  (`REQ_MIRROR_RESUME`): 2xx, or a 4xx "beyond retention policy" partial write.
  A 3xx never is; both transports use `_http.no_redirect_opener` and
  `client._check` raises on any 3xx (`REQ_NO_REDIRECTS`).
- **`tracker_name` is only the *stored* name.** `mirror()` stops before a row
  without one; names are stored only by `ingest`, with the fallback
  `tracker <id>` from `Store.name_unnamed_trackers` (`REQ_MIRROR_IDEMPOTENT`,
  `REQ_MIRROR_FALLBACK_NAME`).
- **Transports never leak raw `OSError`:** `client._request` and
  `influx._request` both go through `_http.send`.
- **The ingest Dockerfile COPYs an allowlist**, never `.`; `.dockerignore`
  mirrors every private `.gitignore` pattern with `**/`. Add both together.
- **Deployment specifics never enter this repo.** `deploy/` and `examples/`
  hold placeholders only (`REQ_PORTABLE_CONFIG`); the env template is
  `deploy/example.env` because the guard rejects `.env.*` names.
- **No retry logic:** `sync` is offset-based and resumable; re-running is the retry.

## Docs contract

- The site under `docs/` has four groups, in this order: Getting started, How-to guides, Reference,
  Explanation (plus Contributing). A page belongs to exactly one; `docs/index.rst` holds the toctrees.
- One architecture page, `docs/architecture.md`: arc42 sections 1 to 12, drawn with C4-styled
  Mermaid flowcharts and sequence diagrams (person `#08427b`, system `#1168bd`, container
  `#438dd5`, external `#999`). No `;`, bare `&`, bare `<`/`>`, `<-->` or `:` in a loop/opt label.
  Diagram text must match the code. Behaviour claims link to a `REQ_*` (`{need}`) or a module.
- One source per fact: a how-to or reference page links to the README or requirements, it does
  not copy them. A new requirement-like statement stays prose unless code implements it and a
  test verifies it (then it gets a `REQ_*`, see Rules above).
- `sphinx-build -b html -W docs docs/_build/html` must pass (the `docs` job); `myst-parser` and
  `sphinxcontrib-mermaid` are pinned in the `dev` extra and are docs tooling, never runtime deps.
- The concept (shared by all ckeller42 repos) is `DOCUMENTATION.md` in `ckeller42/buspi-config`.

## Workflow

- Branch, then PR into `main`. A repo ruleset enforces it: PR required,
  conversations resolved, and `pre-commit` plus `test (3.11/3.12/3.13)` green,
  with the branch up to date (`gh pr update-branch <n>` when it is not).
  Commit prefixes: `feat:`, `fix:`, `docs:`, `test:`, `chore:`.
- Local gate: `./tools/ci.sh` (pre-commit incl. gitleaks working-tree scan,
  strict mypy, pytest with coverage, doctests, `sphinx-build -W`, `interrogate`).
  Repo/CI setup, branch-protection payload and review-job notes: `docs/ops.md`.
- Run `.venv/bin/pre-commit install` once per clone. `.pre-commit-config.yaml`
  is the source of truth for lint/scan versions; keep the `ruff==` pin in the
  `[dev]` extra in step with it. Markdown lint config: `.markdownlint-cli2.jsonc`.
- **Every requirement is a `.. req::` with a `REQ_*` id** in
  `docs/requirements.rst`; the implementing docstring says
  ``Implements :need:`REQ_…`.``; `docs/traceability.rst` needs a `.. test::`
  that `:verifies:` it with real test node ids. New code adds both or the docs
  build fails. Build: `.venv/bin/sphinx-build -b html -W docs docs/_build/html`.
- **Doctests only on pure functions** (`export.*`, `heading.*`, `normalize_epoch`,
  `epoch_from_iso`, `parse_positions`), synthetic values only.
- **Gates:** `interrogate` fail-under 100 on the public surface (shorten a
  docstring, never delete it); coverage `--cov-fail-under=95`.
- **Three secret scanners, deliberately:** gitleaks (`.gitleaks.toml`),
  `detect-secrets` (`.secrets.baseline`, regenerate and audit before committing)
  and `SECRET_RE` in `tools/check_no_private_data.py`. Keep all three.
- `python -m trackiwi …` runs straight from a clone.
- **Releasing:** the version has one source, `trackiwi/__init__.py`
  (`pyproject.toml` reads it). Bump it in a PR; after merge tag the merge
  commit `vX.Y.Z` and create a GitHub release. Deployment steps for a specific
  machine live in the repo that deploys it, never here
  (`REQ_PORTABLE_CONFIG`).
- **Agents installing it for a user:** `pipx install
  git+https://github.com/ckeller42/trackiwi-client@vX.Y.Z`, then `trackiwi login`
  (the user types the password; never ask for it) and `trackiwi sync`.

## Gotchas

- System `python3` is 3.9; this project needs 3.11+. Use
  `/opt/local/bin/python3.13 -m venv .venv && .venv/bin/pip install -e ".[dev]"`.
  `tools/check_no_private_data.py` must stay 3.9-compatible (the hook runs it
  with system `python3`).
- `pre-commit run --all-files` only checks git-tracked files: `git add` new
  files before running the gate.
- `*.json` and `*.xml` are gitignored as track exports; a legitimate one needs
  `git add -f` (the dashboard is re-included). Docs cannot quote a full 40-char
  SHA (use short SHAs or `# allow-secret`); SHA-pinned actions in
  `.github/workflows/` are exempt. Resolve a pin with
  `git ls-remote https://github.com/<owner>/<repo> refs/tags/<tag>` (use `^{}`).
- Grafana Flux queries use `"${bucket}"` / `"${tracker}"`; only built-ins are `v.*`.
- Live tests are skipped by default: `TRACKIWI_LIVE=1 .venv/bin/pytest -m live -s -v`
  (never in CI). Reading the result:
  `.claude/skills/check-trackiwi-api/SKILL.md`. A `fix_at` in an unknown unit is
  *skipped* at parse time, so a plain `sync` exits 0 with `skipped N malformed
  rows`; read that line.
- `fix_at` is epoch seconds after parsing. Two functions, deliberately:
  `normalize_epoch` takes the sync CSV int, `epoch_from_iso` the ISO string of
  `GET /api/v2/trackers`. Do not merge them (a digit string is ambiguous, and
  their error handling differs). A zone-less ISO value is read as UTC.
- **Units (verified live, spec §3.6):** `speed` km/h, `altitude` m, `course`
  degrees clockwise from true north, `distance` **cm** and a per-fix delta (not
  an odometer; it may disagree with haversine, do not "fix" that), `voltage`
  **centivolts** (`1303` = 13.03 V), `fix_timezone` an integer (never a zone name).
- **`GET /api/v2/trackers` and `/alarms` return location data** (geofence
  `lat`/`long`/`radius`, alarm `event` coordinates). `alarms` prints neither; a
  test pins that. Treat both responses like the cache (spec §7.1).
- **Never call or implement `POST /api/v2/session/test_alarm`** (fires a real
  alarm). It appears only in a `client.py` comment; `grep -r test_alarm` finding
  anything else is a regression. Same for the trailing-slash item routes
  (`/api/v2/tours/`, `markers/`, `marker_categories/`, `shares/`, `trackers/`)
  and `PUT /api/v2/session/push_token`.
- The read-only list endpoints share `Client._get_list` and are not cached.

## Repo / CI setup

Moved to `docs/ops.md` (branch ruleset, Pages, workflow conventions, review-job
and CodeRabbit/Dependabot notes).

## Offset semantics in `sync`

`offset` is **exclusive**, confirmed against a live account (spec §10 item 7).
If a sync fails with `"trackiwi returned no new records past offset N"`, suspect
that first: inclusive semantics would make an ordinary no-news sync print
`0 new positions` and exit 1. Run the live contract test to settle it.
