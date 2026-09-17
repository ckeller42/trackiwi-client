# CLAUDE.md

Unofficial read-only client for the trackiwi GPS API. Spec:
`docs/superpowers/specs/2026-09-17-trackiwi-client-design.md`.

## Rules

- **Zero runtime dependencies.** Standard library only. Dev tools go in
  `[project.optional-dependencies].dev`.
- **Read-only.** The only state-changing call allowed is `DELETE /api/v2/session`
  in `logout`. Never add writes to tours, markers, alarms or shares.
- **No private data in this repo, ever.** Fixtures are synthetic. There is no
  record-from-live mode, deliberately.
- **Never log or print the token.**
- **Never hardcode the API base** — it comes from the login response's `server`.
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

## Workflow

- Branch, then PR into `main` — **by convention, not enforced.** Branch
  protection cannot be enabled while the repo is private on the free plan
  (the API returns 403 "Upgrade to GitHub Pro or make this repository
  public"), so nothing mechanically stops a direct push to `main`. Treat the
  PR workflow as binding anyway; see "Repo / CI setup" for the payload to
  apply the moment the repo goes public or the plan changes.
- Do not self-merge past unresolved CodeRabbit threads.
- Commit prefixes: `feat:`, `fix:`, `docs:`, `test:`, `chore:`.
- Local gate: `./tools/ci.sh` — runs `pre-commit run --all-files` and `pytest`,
  mirroring CI exactly.

## Repo / CI setup

- GitHub: `ckeller42/trackiwi-client`, **private**. Sphinx docs, GitHub Pages
  and a licence are deliberately deferred until the repo is made public
  (Pages on a private repo needs a paid plan).
- Branch protection on `main`: **NOT active, and cannot be.** Both APIs that
  could enforce it were tried and both return the same thing on a private
  repo on the free plan:

  ```
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
            { "context": "test (3.11)" }, { "context": "test (3.13)" } ] } },
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
- **The CodeRabbit app install is a manual browser step** and cannot be
  automated: <https://github.com/apps/coderabbitai>. The committed
  `.coderabbit.yaml` only configures it once installed.
- Dependabot auto-merge is `workflow_run`-triggered, so it only runs after CI
  already passed for that exact commit. It merges **minor/patch only**; majors
  are left for manual review. It deliberately does not rely on the repo's
  "Allow auto-merge" setting — do not enable that toggle expecting it to help.
- **CI's gate stage runs `pre-commit run --all-files`, never a bare `ruff`
  call.** Two reasons, both learned the hard way: the `ruff-format` hook is
  scoped to Python on purpose, and a bare `ruff format` also rewrites Python
  code fences inside `docs/` (it silently restructured a fenced snippet in the
  plan document once, changing its meaning); and `pre-commit` in CI is the
  third enforcement layer for the no-private-data guard, which a local
  `git commit --no-verify` can bypass.

## Gotchas

- System `/usr/bin/python3` is 3.9.6 (Xcode); this project needs 3.11+. Use
  `/opt/local/bin/python3.13` (MacPorts).
- `tools/check_no_private_data.py` must stay Python 3.9-compatible: the
  pre-commit hook runs it via `language: system` → `python3`, which on this
  machine is 3.9.6, not the venv.
- `pytest` skips live tests by default (`addopts = -m 'not live'`). Run them
  with:

  ```bash
  TRACKIWI_LIVE=1 .venv/bin/pytest -m live -s -v
  ```

  This test exists to answer open questions about the real API that can't be
  settled from the spec alone — the unit of `fix_at` (seconds vs.
  milliseconds), real field magnitudes, and the shape of the tracker response
  envelope (bare list vs. `{"data": [...]}`). It never runs in CI and never
  needs credentials there; it requires a real account and is opt-in only.
- `fix_at` is normalised to epoch seconds at parse time. Nothing downstream
  should handle milliseconds.

## Known unknown: offset semantics in `sync`

`Client.sync()` assumes trackiwi's `offset` parameter is **exclusive** — that
asking for positions "after id 5" does not return id 5 again. The loop sets
`offset = max(id already seen)` on each page and expects the next page to
start strictly after that.

This is *not confirmed* against the real API; it is inferred from the
vendor's own web app, which uses the same `offset = highest id seen` loop and
stops only on an empty page. Under inclusive semantics that loop could never
terminate (it would keep re-fetching the same last row forever), so exclusive
offsets are the only interpretation consistent with the vendor's own client
behaving correctly. The live contract test (see above) is what will actually
confirm or refute this against a real account.

If a real multi-page sync ever fails with `"trackiwi returned no new records
past offset N"`, this assumption is the first suspect — check whether the
server is in fact returning the row at `offset` itself (inclusive), which
would make `offset <= requested_offset` trip on legitimate, non-stalled
pages.
