# Project Working Rules

Applies to coding agents (Codex, Claude Code, etc.) working in this repository. Reply in Chinese.

## Scope and Quality

- Make the smallest coherent change that resolves the request and follow existing conventions; do not add unrequested features, abstractions, configuration, or files.
- Run one-off scripts against authorized real inputs and verify their output directly without creating extra test suites, fixtures, or rebuild workflows.
- Add permanent tests only for reusable logic whose failure could be silent; prefer a single meaningful check.
- Do not repeat passing checks whose covered code and inputs have not changed.
- Do not generate extra reports, READMEs, manifests, hash records, or backup copies unless requested.
- Use the Python standard library and already-installed dependencies; ask before adding a new dependency.
- Ask one focused question when requirements have a material ambiguity; otherwise apply the obvious reasonable interpretation and state your assumptions.
- Confirm before deleting or overwriting content outside the requested scope; mention unrelated problems without fixing them incidentally.
- Preserve input validation, real failure-path handling, and guards around deletion, credentials, and financial/sensitive operations; fix root causes rather than deleting or relaxing checks to pass tests.
- Run appropriate checks and inspect their output before claiming readiness; report exact commands executed and actual results, and explicitly note any unrun or failing checks.

## Git Identity and UTC

- Author commits and annotated tags solely with the public identity: `Berunkasuteru` and `31027379+Berunkasuteru@users.noreply.github.com`.
- All commit author/committer timestamps and annotated tag timestamps must use UTC with raw Git offset `+0000`.
- Run Git commands with `TZ=UTC0` (`$env:TZ='UTC0'` in PowerShell; `TZ=UTC0` in Bash).
- Never assume an existing shell or another agent client inherits environment config; verify the execution environment whenever committing, tagging, or pushing.
- Before committing or tagging, run `git var GIT_AUTHOR_IDENT` and `git var GIT_COMMITTER_IDENT` to verify identity and trailing `+0000`. Investigate any environment override rather than faking a pass.
- Keep repository-level `user.useConfigOnly=true`; do not infer Git identity from the OS username or hostname.
- Never modify Windows system timezone, system environment variables, global Git identity, or global hook paths without explicit consent.

## Push and History Protection

- Work in this active official working copy; treat inactive copies as read-only and never restore their push targets.
- Do not pull in unrelated history via legacy commits, branches, or tags; migrate only required file changes and create fresh UTC commits.
- Never push, merge, publish, force-push, rewrite history, or delete remote repositories, branches, tags, or Releases without authorization for the current task.
- Before pushing, inspect all reachable commits and annotated tag metadata: block pushing on any non-UTC timestamp, private identity marker, or read failure.
- Preserve the local `pre-push` hook protection; never bypass failures using `--no-verify` or by disabling hooks.
- Hooks and repository-level Git config are not copied by `git clone`; new clones must configure identity and install compliant hooks before pushing (and must never overwrite another project's hooks).

## Release Bundles and Public Metadata

- Release files and notes must not contain private names, private emails, machine usernames, personal directory absolute paths, credentials, or unauthorized personal data.
- Normalize all ZIP entry timestamps to `1980-01-01 00:00:00`. Verify local file headers, central directory headers, and extra timestamp fields — including nested ZIPs — rather than checking unpacked contents alone.
- Recompute SHA-256 after every byte change to release assets, update release notes accordingly, and verify GitHub's returned asset digest after upload.
- Do not assume an existing packaging script normalizes all timestamps; verify before upload and make minimal packaging fixes if needed.
- Historical timestamps in test fixtures and third-party timezone databases are functional data; do not mechanically rewrite or remove them for privacy rules.

## Project Context

- Repository: `https://github.com/Berunkasuteru/weibo-text-archiver`.
- Default branch is `main`, with the latest published release tag currently `v0.9.0` (legacy `post-0.6.0-fixes` was merged and retired). Verify current branch and tags before starting work.
- Merging to `main`, pushing to remote, or publishing a new release requires authorization for the current task.
- Test entry points: `python tests/run_tests.py`, `python tests/test_image_core.py`, `python tests/test_image_ui.py`.
- UI checks require a working Tk desktop environment; troubleshoot initialization or sandbox issues instead of removing assertions or skipping checks.
- Do not rely on fixed historical test counts; actual counts vary across branches and versions — read the live test output.
