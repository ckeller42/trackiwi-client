# AGENTS.md

Canonical rules for anyone — human or agent — working in this repository.
`CLAUDE.md` is a one-line `@AGENTS.md` import so Claude Code loads this file.

Unofficial read-only client for the trackiwi GPS API. Spec:
`docs/superpowers/specs/2026-09-17-trackiwi-client-design.md`.

## Rules

- **Zero runtime dependencies.** Standard library only. Dev tools go in
  `[project.optional-dependencies].dev`.
- **Read-only.** The only state-changing call allowed is `DELETE /api/v2/session`
  in `logout`. Never add writes to tours, markers, alarms or shares.
- **No private data in this repo, ever.** Fixtures are synthetic. There is no
  record-from-live mode, deliberately.
- **Never log or print the token.** Server-controlled text that can reach
  output goes through `client._redact` first, at two points. `_check` redacts
  the error body it interpolates into its message, because a proxy or gateway
  can echo the request's own `Authorization` header into that body. And
  `Client._api` scrubs the session token from the message of *every*
  `TrackiwiError` raised beneath it — transport failures included — before it
  propagates (belt and braces, whatever produced the message).
- **Never hardcode the API base** — it comes from the login response's `server`
  — and **require `https://`** (`client._require_https`), wherever it came
  from: login response, `--api-base`, or the config file.
- **Convert foreign exceptions at the module boundary, not in `main()`.**
  `main()` catches `AuthError`, `TrackiwiError`, `BrokenPipeError` and
  `KeyboardInterrupt`, and deliberately nothing else: a blanket
  `except (sqlite3.Error, json.JSONDecodeError, OSError, ValueError)` there
  would also swallow genuine bugs and present them to the user as their
  mistake. `Client.load`, `Store.__enter__`, `_decode_json` and `cmd_export`
  each raise `TrackiwiError` with a message naming the way out. `TrackiwiError`
  and `AuthError` live in `trackiwi/__init__.py` with the package's other
  shared contracts (`__version__`, `COLUMNS`), so neither `client.py` nor
  `store.py` imports the other to reach them.
- **Validate config *values*, not only the document.** `Client.load` checks
  `isinstance(data, dict)` *and* that `api_base` is a string or `None`: a
  non-string value reached `_require_https`'s `.lower()` and escaped `main()`
  as an `AttributeError`, one line past the check meant to prevent that.
- **A stored token must always be removable.** `logout` is the only revocation
  path (spec §7.3), so it must not be blockable by the config file's own
  contents. On *any* `TrackiwiError` from `Client.load()` (non-https
  `api_base`, truncated JSON, wrong type), `cmd_logout` falls back to
  `Client.forget_local_session()` and warns on stderr
  (`REQ_TOKEN_REMOVABLE`). It does *not* revoke over a non-https base:
  sending the token in cleartext is exactly what `_require_https` prevents.
  `Client.logout()` deletes through the same `forget_local_session()`.
- **`BrokenPipeError` means success only where the payload is written.** It is
  caught in `cmd_export` around `sys.stdout.write` (`export | head` is not an
  error); `main()` catches it too but exits **1**, because `cmd_sync` writes
  its progress to stderr and `sync 2>&1 | head -1` returning 0 made a
  wrapper's `sync && export` run on a partial sync. `_silence_stderr()` is the
  shared devnull redirect that keeps CPython's flush-on-exit quiet.
- **A malformed field means "skip and count the row", never "crash" and never
  "store it anyway".** Non-finite coordinates and timestamps outside
  `_MIN_FIX_AT.._MAX_FIX_AT` are malformed: `inf` produced schema-invalid GPX
  and invalid JSON, and an out-of-range `fix_at` crashed *every* later export.
  The exporters are the second layer, for rows already cached before those
  checks existed: `to_gpx` (`_finite`) and `to_geojson` (`allow_nan=False`)
  both raise, and `cmd_export` converts it. `to_csv` deliberately does not —
  it is a raw dump of the cache. A comment must not claim wider coverage than
  that. "Never crash" includes `sync`: `parse_positions` returns the highest
  id on the page, skipped rows included, and `sync` advances to it
  (`REQ_SYNC_PAST_MALFORMED`) — advancing only to the last *parsed* id made a
  malformed newest row fail every later run. It raises only when a page has
  skipped rows and no readable id at all.
- **Read-only commands must not create the cache.** `export` and `purge` touch
  the database only when it already exists; `purge` unlinks it (plus any
  `-journal`/`-wal`/`-shm`, via `store.cache_files`, which `Store.purge()`
  shares so the library makes the same promise) without opening it, so it
  still works on a corrupt file — which is exactly when a user wants the
  history gone.
- **Only a *corrupt* cache may be told to run `purge --yes`.** `purge` is
  unconditionally destructive, so advising it on an intact database destroys
  the one asset spec §7.1 protects. `Store._open_failure` splits the cases: a
  locked/busy `sqlite3.OperationalError` is transient (a concurrent or hung
  `trackiwi`) and gets a "wait and re-run" message that never says purge;
  everything else keeps the purge advice, because there it is the only remedy.
  `sqlite3.OperationalError` is a subclass of `sqlite3.DatabaseError`, so the
  lock check must come first. Both messages carry the sqlite text — without it
  the two cases are indistinguishable from CLI output.
- **An export covering several trackers must keep them apart** — one `<trk>`
  per tracker, one GeoJSON `Feature` per tracker with `tracker_id` in
  `properties`. A fused track renders as a plausible line, so the error cannot
  be spotted after the fact.
- **Ignore the `trackiwi-app-command` response header.** Never act on it.
- No retry logic: `sync` is offset-based and resumable, so re-running is the retry.
- **`xml.etree.ElementTree` in `trackiwi/export.py` is deliberate, not an
  oversight.** The exporter only *serialises* XML for GPX output; it never
  parses untrusted input, so the entity-expansion / XXE risks `defusedxml`
  guards against don't apply here — and pulling in `defusedxml` would break
  the zero-runtime-dependency rule for no benefit. Automated dependency/
  security scanners will flag `xml.etree` anyway; that flag is a false
  positive in this codebase and should not be "fixed" by adding the
  dependency.
- **`heading.py` is pure and offline.** It imports only the stdlib and
  `export` (for `Row` and `_by_tracker`); it never touches `client`, `store`
  or the network, and `cmd_heading` never creates the cache. Parked heading =
  initial bearing between the *last two moving fixes of the same tracker*,
  never the raw `course` at zero speed (it drifts) and never a fix from
  another device. `DEFAULT_STALE_AFTER` (3600 s) is a judgement, not a
  constant of nature, so it stays a parameter (`stale_after`,
  `--stale-after`). The two caveats (reversing cannot be detected; GPS alone
  cannot sense a stationary heading) live in the module docstring and the
  `--help` text; a test pins the latter.
- **`influx.py` is the only module that talks to InfluxDB**, `lineprotocol.py`
  is pure. `influx.py` and `client.py` never import each other.
- **Read-only means read-only against trackiwi.** InfluxDB writes are allowed
  only as `trackiwi_position` line protocol to the one configured target
  (`REQ_INFLUX_WRITE_SCOPE`). Never add deletes, bucket management or other
  measurements.
- **Mirror invariant:** `mirror_state` advances only after InfluxDB
  acknowledges a batch (`REQ_MIRROR_RESUME`) — same discipline as `sync`.
  Acknowledged = 2xx, or a 4xx "beyond retention policy" partial write
  (warned on stderr). A 3xx never is: `InfluxWriter`'s default opener refuses
  redirects (a followed 302 to a login page was a false ack and forwarded the
  token), so don't swap it back to plain `urlopen`. `Client` refuses them too:
  both take the opener from `_http.no_redirect_opener`, and `client._check`
  raises on any 3xx (`REQ_NO_REDIRECTS`).
- **`tracker_name` is only ever the *stored* name.** `mirror()` stops before
  a batch holding a row with no stored name; never make a name up on the push
  path (it splits a tracker's series, `REQ_MIRROR_IDEMPOTENT`). Names are
  stored only by `ingest`; `influx push` stays offline. After `trackers()`
  answered, `ingest` stores the real names and then gives every cached tracker
  still without one the stable fallback `tracker <id>`
  (`Store.name_unnamed_trackers`, `REQ_MIRROR_FALLBACK_NAME`) — an unnamed
  tracker, or rows of one gone from the account, stalled the mirror for every
  tracker. A real name may replace a fallback; a fallback never replaces a
  stored name (`INSERT OR IGNORE`).
- **Transports never leak raw OSError.** Both `client._request` and
  `influx._request` go through `_http.send`, the one place that converts
  `OSError` / `http.client.HTTPException` (timeouts, resets while *reading*,
  which urllib does not wrap in `URLError`) to `TrackiwiError`; `ingest`
  relies on that to always reach the push.
- **The ingest Dockerfile COPYs an allowlist** (`pyproject.toml`, `LICENSE`,
  `trackiwi/`), never `.` — a COPY layer keeps what it copies. `.dockerignore`
  mirrors every private `.gitignore` pattern with `**/`; add both together.
- **Deployment specifics never enter this repo.** `deploy/` and `examples/`
  hold placeholders only (`REQ_PORTABLE_CONFIG`); a real deployment (e.g.
  buspi) is configured from its own repo, which consumes this package. The env
  template is `deploy/example.env` because the guard rejects `.env.*` names.

## Workflow

- Branch, then PR into `main` — **by convention, not enforced.** Branch
  protection cannot be enabled while the repo is private on the free plan
  (the API returns 403 "Upgrade to GitHub Pro or make this repository
  public"), so nothing mechanically stops a direct push to `main`. Treat the
  PR workflow as binding anyway; see "Repo / CI setup" for the payload to
  apply the moment the repo goes public or the plan changes.
- Do not self-merge past unresolved CodeRabbit threads.
- Commit prefixes: `feat:`, `fix:`, `docs:`, `test:`, `chore:`.
- Local gate: `./tools/ci.sh` — runs `pre-commit run --all-files` (plus the
  manual-stage `gitleaks-dir` working-tree scan), strict `mypy` (config in
  `[tool.mypy]`), `pytest` with coverage, the pure-function doctests, the
  `sphinx-build -W` traceability build and `interrogate`, mirroring CI exactly.
- **Hooks:** run `.venv/bin/pre-commit install` once per clone. It installs
  both the `pre-commit` stage (whitespace/EOF/YAML checks, ruff + ruff-format,
  gitleaks on the staged diff, detect-secrets, markdownlint-cli2, actionlint,
  the private-data guard) and the `pre-push` stage (strict mypy and the test
  suite with its coverage gate, via `.venv/bin`). `.pre-commit-config.yaml` is
  the single source of truth for lint/scan tool versions; the `ruff==` pin in
  the `[dev]` extra is kept in step with the ruff-pre-commit `rev` by hand.
  Markdown lint config is `.markdownlint-cli2.jsonc` (the only one); it skips
  the dated plans/specs under `docs/superpowers/`.
- **Requirements live as `sphinx-needs` objects in `docs/`.** Each requirement
  is a `.. req::` with a stable `REQ_*` id in `docs/requirements.rst`; the
  implementing function's docstring references it the sphinx way
  (``Implements :need:`REQ_…`.``); and `docs/traceability.rst` has a `.. test::`
  need that `:verifies:` it, naming the real test node id(s). **New code must
  add a requirement and a verifying `.. test::` — the docs build FAILS
  otherwise**: `conf.py`'s `req_without_test` rule flags any `req` with no
  incoming `verifies` link, and `sphinx-build -b html -W` (the `-W` is
  load-bearing) turns that, and any unresolved `:need:`, into a non-zero exit.
  Use `needs_links` (not the deprecated `needs_extra_links`) for the pinned
  `sphinx-needs==8.5.0`. Build locally:
  `.venv/bin/sphinx-build -b html -W docs docs/_build/html` (output is
  gitignored; only Pages *hosting* is deferred until public).
- **Doctests run on the pure functions only** (`export.*`, `heading.*`,
  `normalize_epoch`, `epoch_from_iso`, `parse_positions`), via `pytest --doctest-modules trackiwi`
  in `ci.sh`. Never add a doctest to network/filesystem/non-deterministic code,
  and use synthetic values only.
- **Docstring coverage** is gated by `interrogate` (`[tool.interrogate]` in
  `pyproject.toml`, `fail-under = 100` on the public surface with dunder/init/
  nested/private ignored). Cover new public API with a docstring or the gate
  fails.
- **Coverage gate:** `pytest --cov=trackiwi --cov-fail-under=95` in both CI and
  `ci.sh`. Measured coverage is ~97%; the threshold is 95 (headroom for matrix
  variance). `[tool.coverage.*]` config lives in `pyproject.toml`.
- **Secret scanners (three, deliberately):** `gitleaks` (pinned `v8.30.1`,
  config `.gitleaks.toml`), the `detect-secrets` pre-commit hook (pinned
  `v1.5.0`, baseline `.secrets.baseline`) and the home-rolled `SECRET_RE`
  guard in `tools/check_no_private_data.py`. They are **defence-in-depth**,
  not replacements for each other — keep all three. gitleaks' stock hook
  scans only the staged diff, so CI and `ci.sh` also run its manual-stage twin
  `pre-commit run --hook-stage manual gitleaks-dir --all-files` over the
  working tree. `.gitleaks.toml` allowlists exactly one thing: the
  `"hashed_secret"` lines of `.secrets.baseline` (path AND line must
  match). The baseline holds only hashed synthetic/test fakes; regenerate with
  `detect-secrets scan > .secrets.baseline` and audit before committing.
- **Run with no install:** `python -m trackiwi …` (`trackiwi/__main__.py`) runs
  straight from a clone — the payoff of zero runtime deps; identical to the
  `trackiwi` console script.

## Repo / CI setup

- GitHub: `ckeller42/trackiwi-client`, **private**. The GitHub *Pages hosting*
  of the docs is deferred until the repo is made public (Pages on a private
  repo needs a paid plan). **Sphinx itself is not deferred** — the
  `sphinx-needs` requirements-traceability build runs in CI now; only the
  public hosting of its output waits. The project is **MIT-licensed**
  (`LICENSE` at the repo root, wired into `pyproject.toml` via
  `license = "MIT"` / `license-files`, with the badge in the README).
- **Public-flip checklist** (do these the moment the repo goes public or the
  plan changes — one place so it is a checklist, not a rediscovery):
  1. Enable the branch-protection ruleset (payload already below in this
     section — apply it verbatim).
  2. Enable GitHub Pages to **publish** the Sphinx docs (the build already runs
     in CI; only hosting was blocked).
  3. Enable GitHub-native secret scanning + push protection if the plan allows
     — it complements the `detect-secrets` hook and the local guard.
  (The MIT `LICENSE` and its badge are already in place, so they are no longer
  on this checklist.)
- Branch protection on `main`: **NOT active, and cannot be.** Both APIs that
  could enforce it were tried and both return the same thing on a private
  repo on the free plan:

  ```text
  Upgrade to GitHub Pro or make this repository public to enable this
  feature. (HTTP 403)
  ```

  That is `PUT /repos/{o}/{r}/branches/main/protection` (legacy branch
  protection) **and** `POST /repos/{o}/{r}/rulesets` (the newer rulesets
  API). **Do not spend time retrying either** — the gate is the plan, not the
  payload. The owner's decision (2026-09-17) is to keep the repo private and
  treat the workflow as convention.
- **User requirement, currently unenforceable: a PR must not be merged while
  any comment thread is unresolved.** Honour this by hand on every PR. It is
  `required_review_thread_resolution` below, and it switches on by itself the
  moment the repo goes public or the plan changes — at which point run:

  ```bash
  gh api repos/ckeller42/trackiwi-client/rulesets -X POST --input - <<'JSON'
  {
    "name": "main: PR required, conversations resolved, CI green",
    "target": "branch",
    "enforcement": "active",
    "bypass_actors": [],
    "conditions": { "ref_name": { "include": ["~DEFAULT_BRANCH"], "exclude": [] } },
    "rules": [
      { "type": "pull_request", "parameters": {
          "required_approving_review_count": 0,
          "dismiss_stale_reviews_on_push": false,
          "require_code_owner_review": false,
          "require_last_push_approval": false,
          "required_review_thread_resolution": true,
          "automatic_copilot_code_review_enabled": false,
          "allowed_merge_methods": ["merge", "squash", "rebase"] } },
      { "type": "required_status_checks", "parameters": {
          "strict_required_status_checks_policy": true,
          "do_not_enforce_on_create": false,
          "required_status_checks": [
            { "context": "pre-commit" },
            { "context": "test (3.11)" }, { "context": "test (3.12)" },
            { "context": "test (3.13)" },
            { "context": "typecheck" }, { "context": "docs" } ] } },
    { "type": "deletion" },
    { "type": "non_fast_forward" }
    ]
  }
  JSON
  ```

  Rulesets are preferred over legacy branch protection here. Two parameters
  are deliberate and should not be "corrected": `required_approving_review_count:
  0`, because a solo owner cannot approve their own PR and `1` would lock them
  out of merging entirely; and `bypass_actors: []`, which is the rulesets
  equivalent of `enforce_admins: true` — without it the owner can push straight
  past the rule, which defeats the point.
- **The Claude review job (`claude-code-review.yml`) fails on a silent
  non-review, by design.** Its last step reads the action's `execution_file`
  and exits 1 when the result carries any `permission_denials` (printing the
  denied tool and input; the sanitised log shows only a count), when Claude
  produced no `result` message or a non-`success` one, or when Claude never
  ran. Three things learned fixing #8 that the action does not tell you:
  1. **A PR that edits that workflow is never reviewed by it.** The action's
     OIDC token exchange refuses a workflow file that differs from `main`
     ("Workflow validation failed … identical content to the version on the
     repository's default branch") and the job used to pass anyway. Now the
     gate step turns it red with a message naming this cause. To test a
     workflow change on its own PR, pass `github_token: ${{ github.token }}`
     temporarily (it skips the exchange; comments then come from
     `github-actions[bot]`) and remove it before merge.
  2. **The prompt is a slash command, so the CLI runs it through the `Skill`
     tool**, which must be in `--allowedTools` as
     `Skill(code-review:code-review *)`. This was the one denial left after
     #6 and #7.
  3. **Sub-agents must run in the foreground.** In a headless (SDK) session a
     sub-agent runs in the background unless Claude asks otherwise, and
     nothing ever wakes the session up again, so the review ended after six
     turns with zero denials and nothing posted.
     `CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1` on the action step forces the
     foreground. `gh run rerun --debug` does **not** reveal the transcript
     (the action keys on the `ACTIONS_STEP_DEBUG` env var, which a debug
     re-run does not set); `show_full_output: true` does, temporarily.
  `gh api` is deliberately not allowed: it can write with `-X`, and the review
  gets the same data from `gh pr view --json` / `gh pr diff`. A sub-agent
  that improvises one still trips the gate, which is the intended trade-off.
- **The CodeRabbit app install is a manual browser step** and cannot be
  automated: <https://github.com/apps/coderabbitai>. The committed
  `.coderabbit.yaml` only configures it once installed.
- Dependabot auto-merge is `workflow_run`-triggered, so it only runs after CI
  already passed for that exact commit. It merges **minor/patch only**; majors
  are left for manual review. It deliberately does not rely on the repo's
  "Allow auto-merge" setting — do not enable that toggle expecting it to help.
- **CI's `pre-commit` job runs `pre-commit run --all-files`, never a bare `ruff`
  call.** Two reasons, both learned the hard way: the `ruff-format` hook is
  scoped to Python on purpose, and a bare `ruff format` also rewrites Python
  code fences inside `docs/` (it silently restructured a fenced snippet in the
  plan document once, changing its meaning); and `pre-commit` in CI is the
  third enforcement layer for the no-private-data guard, which a local
  `git commit --no-verify` can bypass.

## Gotchas

- System `/usr/bin/python3` is 3.9.6 (Xcode); this project needs 3.11+. Use
  `/opt/local/bin/python3.13` (MacPorts). Install into a venv:
  `/opt/local/bin/python3.13 -m venv .venv && .venv/bin/pip install .` (or
  `-e ".[dev]"` for development). No install is needed just to run it — see the
  `python -m trackiwi` note in Workflow.
- `tools/check_no_private_data.py` must stay Python 3.9-compatible: the
  pre-commit hook runs it via `language: system` → `python3`, which on this
  machine is 3.9.6, not the venv.
- `pre-commit run --all-files` (and so `./tools/ci.sh`) only checks git-tracked
  files: a new file gets a false green until it is `git add`-ed. Stage new files
  before running the gate.
- A **new test module** must be added to the `[[tool.mypy.overrides]]` `module`
  list in `pyproject.toml`, or strict mypy rejects its unannotated test functions.
- `*.json` is git-ignored (a GeoJSON export named `.json` must never be
  committed); the dashboard is re-included by `!deploy/grafana-dashboards/*.json`.
- Grafana Flux queries use `"${bucket}"` / `"${tracker}"` for dashboard
  variables; only built-ins (`v.timeRangeStart`, `v.windowPeriod`) are `v.*`.
- `pytest` skips live tests by default (`addopts = -m 'not live'`). Run them
  with:

  ```bash
  TRACKIWI_LIVE=1 .venv/bin/pytest -m live -s -v
  ```

  How to read the result and what to do on drift:
  `.claude/skills/check-trackiwi-api/SKILL.md`.

  This test exists to answer open questions about the real API that can't be
  settled from the spec alone — the unit of `fix_at` (seconds vs.
  milliseconds), real field magnitudes, and the shape of the tracker response
  envelope (bare list vs. `{"data": [...]}`). It never runs in CI and never
  needs credentials there; it requires a real account and is opt-in only.

  Note the `fix_at` symptom changed twice: a unit that is neither seconds nor
  milliseconds (microseconds, say) is *skipped* at parse time, and since #18
  a page of skipped rows with readable ids is passed rather than raised on.
  So the live test fails on its `assert rows` (the message carries the skip
  count), and a plain `sync` exits 0 with `0 new positions` and
  `skipped N malformed rows` — read that line, it is the only signal. Read the
  raw CSV body through `client._api("POST", "/api/v2/trackers/sync", ...)` to
  see the actual value in that case.
- `fix_at` is normalised to epoch seconds at parse time. Nothing downstream
  should handle milliseconds.
- **Unit conventions, verified live 2026-09-17 (spec §3.6).** These are not
  guesses and they are not obvious from the field names:
  - **`distance` is centimetres, and a per-fix delta — not an odometer.**
    Measured at 100.00x the haversine distance between consecutive fixes over
    12 samples. It comes from the *device's* own consecutive readings, not from
    the stored coordinates, so it can legitimately disagree with a two-point
    calculation when a fix drops or GPS jitters. Do not "fix" that disagreement.
  - **`voltage` is centivolts** — `1303` is 13.03 V. Same hundredths
    convention.
  - **`fix_timezone` is an integer** (observed `1`), never a zone name like
    `"UTC"`. The parser enforces it (`int()`), the schema declares `INTEGER`,
    and a test pins the chain parse → store → CSV. Its *semantics* are still
    open; nothing depends on them, because everything is handled in UTC.
  - **`speed` and `altitude` units are still unverified.** Do not document a
    unit for either.
- **`fix_at` has two types on two endpoints, and there are two functions for
  that reason.** The sync CSV sends an **epoch integer in seconds**;
  `GET /api/v2/trackers` sends an **ISO 8601 string** inside
  `latest_positionlog` (`received_at` likewise). `normalize_epoch` takes the
  int, `epoch_from_iso` takes the string. **Do not merge them into one
  `int | str` function**: `"1766663018"` is a digit string that is also a
  plausible epoch, so a union-typed normaliser has to guess which form the
  caller meant, and no caller needs the ambiguity — each knows which endpoint
  it read. Their error handling differs too, which is the harder reason: a bad
  CSV field raises `ValueError` so `parse_positions` can skip and count the
  row, whereas a bad JSON field has no row to skip and converts to
  `TrackiwiError` at the boundary. `epoch_from_iso` reads a zone-less value as
  **UTC** on purpose — naive `.timestamp()` applies the *machine's* zone, which
  would shift every exported track by the local offset.
- **`GET /api/v2/trackers` and `GET /api/v2/alarms` both return location
  data**, which their names do not suggest. A tracker record's
  `alarm_configuration` holds a geofence `lat`/`long`/`radius` (usually where
  the vehicle is kept); every alarm record's `event` embeds
  `latitude`/`longitude`, so an alarm list is a movement record of exactly the
  moments that mattered. The `alarms` command prints neither — a test pins
  that — and its `--help` carries the warning. Treat both responses with the
  care spec §7.1 demands of the cache.
- **`POST /api/v2/session/test_alarm` must never be called or implemented.**
  It fires a real alarm on a real vehicle. It is referenced in exactly one
  place, a comment in `client.py` saying why not, and `grep -r test_alarm`
  finding anything else is a regression. Same for the trailing-slash
  item/mutation routes (`/api/v2/tours/`, `/api/v2/markers/`,
  `/api/v2/marker_categories/`, `/api/v2/shares/`, `/api/v2/trackers/`) and
  `PUT /api/v2/session/push_token`.
- **The six read-only list endpoints share `Client._get_list`.** Do not
  reimplement the GET/`_check`/decode/insist-on-a-list sequence per method; six
  copies is how the envelope handling drifts. None of them is cached in SQLite,
  deliberately: they are small live reads and the cache exists for positions
  only.
- **`*.json` and `*.xml` are gitignored as track exports**, because
  `export --format geojson -o positions.json` is the natural filename and a
  GPX saved as `track.xml` is the same hole. A legitimate JSON/XML file needs
  `git add -f`; the pre-commit guard then parses it and only blocks it if its
  top-level `type` really is a GeoJSON discriminator.
- **This repo's own docs cannot quote a full 40-character commit SHA**:
  `SECRET_RE` in the guard matches it as a token-shaped string. Use short SHAs
  (what AGENTS.md and the ledger do) or append `# allow-secret`.
  **The one exemption is a SHA-pinned action**: in `.github/workflows/` only,
  the guard strips the `@<40-hex>` of a `uses: owner/repo@<sha>` line before
  scanning it (`ACTION_PIN_RE`), so pins need no marker and the rest of the
  line — including the `# vX.Y.Z` comment Dependabot rewrites — is still
  scanned. A 40-hex string anywhere else in a workflow still blocks.
- **Actions are pinned by full commit SHA** with a `# vX.Y.Z` comment
  (Dependabot's `github-actions` ecosystem bumps both). Resolve a new pin with
  `git ls-remote https://github.com/<owner>/<repo> refs/tags/<tag>` (use the
  peeled `^{}` SHA for an annotated tag).

## Offset semantics in `sync` (confirmed exclusive)

`Client.sync()` treats trackiwi's `offset` parameter as **exclusive** — asking
for positions "after id 5" does not return id 5 again. The loop sets
`offset = max(id already seen)` on each page and the next page starts strictly
after that.

**This is now confirmed, not inferred.** The offset was verified exclusive
against a live account by the controller (spec §10 item 7): requesting `offset`
equal to the highest id on a page returns rows strictly greater than it, with no
overlap between consecutive pages. It was previously only *inferred* from the
vendor's own web app, which uses the same `offset = highest id seen` loop and
stops only on an empty page — under inclusive semantics that loop could never
terminate, which was the strongest available argument before the live
confirmation. The failure-mode guidance below is kept as history in case the
undocumented contract ever changes.

If a sync ever fails with `"trackiwi returned no new records past offset N"`,
this assumption is the first suspect — check whether the server is in fact
returning the row at `offset` itself (inclusive), which would make
`offset <= requested_offset` trip on legitimate, non-stalled pages.

**The first symptom would be an ordinary sync with nothing new, not a
multi-page one.** Under inclusive semantics a routine `trackiwi sync` with no
new data returns exactly the boundary row, so `rows` is non-empty, the batch is
yielded, `max(row[0]) == requested_offset`, and the guard raises — on by far
the most frequently executed path. It would print `0 new positions` (the count
is honest since the M-1 fix) and then exit 1. A multi-page sync only trips the
guard when a page genuinely contains nothing newer. Run the live contract test
before the first real use, not after: it is the only thing that can settle
both this and the `fix_at` unit.
