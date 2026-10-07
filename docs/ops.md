# Operations

Repo and CI setup, moved out of `AGENTS.md` unchanged.

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
