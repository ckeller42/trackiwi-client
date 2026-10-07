# Operations

Repo and CI setup, moved out of `AGENTS.md`. Anything this page cannot know from the
repository's own files is marked **verify in repo settings**.

## Repo / CI setup

- GitHub: `ckeller42/trackiwi-client`, **public**, MIT-licensed (`LICENSE` at the
  repo root, wired into `pyproject.toml` via `license = "MIT"` / `license-files`,
  with the badge in the README).
- **Docs hosting is live.** `pages.yml` builds the Sphinx site
  (`sphinx-build -b html -W docs docs/_build/html`) on every push to `main` (and on
  manual dispatch) and deploys it with `actions/deploy-pages`; the README links
  the result at <https://ckeller42.github.io/trackiwi-client/>. It needs Pages
  set to "GitHub Actions" as the source (**verify in repo settings**). The same
  `-W` build also gates every PR through the `docs` job in `ci.yml`.
- **Branch ruleset on `main` is active** (AGENTS.md, "Workflow"): a PR is required,
  conversations must be resolved, and these checks must pass with the branch up
  to date. The names are load-bearing; do not rename the workflow (`CI`) or its jobs:
  `pre-commit`, `test (3.11)`, `test (3.12)`, `test (3.13)`, `typecheck`, `docs`.
  Never add a `name:` to the matrix job (it would change `test (3.x)`) and keep the
  matrix values strings. The `build` job (packaging smoke test) and the Claude jobs
  are **not** required.
- The repo's reference ruleset payload is kept below so it can be re-applied or
  compared. Whether the live ruleset still matches it exactly is **verify in repo
  settings** (Settings, Rules, Rulesets).

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
- GitHub-native secret scanning and push protection: **verify in repo settings**
  (they complement the three scanners below).
- **Workflow conventions** (every workflow, enforced by review and by `zizmor`):
  top-level `permissions` of `contents: read` or `{}` with wider scopes only on the
  job that needs them; `persist-credentials: false` on every checkout; a
  `timeout-minutes` on every job; every `uses:` pinned to a full commit SHA with a
  `# vX.Y.Z` comment. `ci.yml` cancels superseded runs on pull requests only, so a
  push to `main` always runs to completion.
- **Workflow linting** runs inside the existing `pre-commit` job: `actionlint` and
  `zizmor` (offline audits only; the hook has no token). The one deliberate
  `zizmor` exception is the `workflow_run` trigger in `dependabot-auto-merge.yml`,
  annotated in place with its reason.
- **Dependabot** (`.github/dependabot.yml`): weekly, with a 7-day cooldown for
  version updates (security updates ignore it). github-actions, pip and pre-commit
  updates are grouped by minor/patch; majors stay one PR each. `ruff` is pinned in
  both `pyproject.toml` and `.pre-commit-config.yaml` and the two must match, so
  both bumps share one cross-ecosystem `ruff` group (a single PR). That grouping
  uses Dependabot's `multi-ecosystem-groups`; check the first such PR actually
  carries both files.
- **Packaging smoke test:** the `build` job in `ci.yml` runs `python -m build`,
  `twine check --strict dist/*`, installs the wheel into a clean venv and runs
  `trackiwi --help`. Releases themselves are manual (tag the merge commit, create
  a GitHub release; see AGENTS.md).
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
  are left for manual review (it reads the `update-type` trailer of every
  dependency in the PR's commits, so a grouped PR is merged only if none of its
  members is a major). It deliberately does not rely on the repo's
  "Allow auto-merge" setting — do not enable that toggle expecting it to help.
- **CI's `pre-commit` job runs `pre-commit run --all-files`, never a bare `ruff`
  call.** Two reasons, both learned the hard way: the `ruff-format` hook is
  scoped to Python on purpose, and a bare `ruff format` also rewrites Python
  code fences inside `docs/` (it silently restructured a fenced snippet in the
  plan document once, changing its meaning); and `pre-commit` in CI is the
  third enforcement layer for the no-private-data guard, which a local
  `git commit --no-verify` can bypass.
