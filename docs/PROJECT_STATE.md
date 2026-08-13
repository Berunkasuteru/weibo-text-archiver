# PROJECT_STATE

Last updated: 2026-08-13

## Current project

- Product: 微博文字导出器
- Platform: Windows
- Current development stage: 0.4.0 completed / accepted (pre-1.0)
- Current branch: `main`
- Current accepted tag: `v0.4.0`
- Current accepted commit: `Initial public release`
- Known Alpha1 baseline commit: `6327d3a V7 Alpha1 baseline`
- Alpha2 core code commit: `d0b75e3 V7 Alpha2 diagnostics and range fixes`
- Working tree before Alpha4 implementation: clean

## Product goal

A trustworthy, text-first personal Weibo archive/export tool for Windows.

Primary goals:

- Export Weibo text reliably to Markdown.
- Preserve archive correctness over convenience.
- Support AI-friendly compact output without silently losing text.
- Avoid runtime GitHub downloads, runtime pip installs, browser automation, admin rights, TLS bypasses, and unnecessary frameworks.
- Eventually ship as a normal Windows app, likely PyInstaller `onedir`.

## Current architecture

Primary data boundary:

`raw JSON -> parser -> frozen dataclass models -> exporter`

Important modules include:

- `weibo_archive/network.py`
- `weibo_archive/client.py`
- `weibo_archive/parser.py`
- `weibo_archive/models.py`
- `weibo_archive/exporter.py`
- `weibo_archive/app.py`
- `weibo_archive/tasking.py`

The exporter must not receive arbitrary raw response dictionaries.

## Alpha2 completed and accepted

### Long-text diagnostics

- Keep long-text handling fail-closed.
- `extend` and `detail` keep separate sanitized diagnostics.
- HTTP 200 HTML permission pages are not accepted as valid long text.
- Embedded status ID mismatch is rejected.
- Response body snippets are not included in diagnostic exceptions.
- JSON parsing failures must not retain complete response bodies through exception cause/context.
- No third long-text fallback was added.
- Truncated list text is not accepted as complete content.

Known inaccessible long-text case:

- Weibo ID: `9000000000000001`
- The post is currently inaccessible to the authenticated session, so its full text cannot be verified.
- `extend` and `detail` cannot provide verifiable full text; no more specific cause is asserted.
- V6 silently exported the truncated list text; V7 must not do that.

### Try-50 / recent-N timeline semantics

Windows testing confirmed that old pinned posts (`mblogtype == 2`) were contaminating progress reporting.

Fix direction:

- Pinned posts do not define the normal timeline frontier.
- Try-50 / recent-N count non-pinned, target-UID, deduplicated, successfully parsed timeline candidates.
- Stop processing once 50 valid timeline candidates are obtained.
- Do not request an unnecessary fourth page after the target is already satisfied.
- Final result is sorted and strictly limited to the requested count.
- Progress text uses wording such as `时间线推进到` rather than a misleading minimum date over all accepted posts.

### GUI

Windows acceptance confirmed:

- Expanding `运行详情` grows the window so the log is visible.
- Expanded height respects the current Windows work area and does not overlap the taskbar.
- Collapsing restores the compact size/position.
- Multi-monitor negative-coordinate geometry is covered by offline geometry tests; full multi-monitor Windows acceptance remains future compatibility work.
- Default compact layout shows the complete `等待任务` status text without clipping.

## Tests

- `environment_check.py`: PASS
- `tests/run_tests.py`: 46/46 PASS
- `git diff --check`: PASS
- exporter golden: unchanged and passing
- zero-runtime-dependency audit: passing

## Final Windows acceptance checklist

Confirm all of the following:

- [x] `试抓50条` stops after 50 normal timeline candidates and does not request page 4 unnecessarily.
- [x] Progress date is not polluted by old pinned posts.
- [x] Final try-50 output contains exactly 50 selected posts.
- [x] `运行详情` expands visibly, respects the work area, and collapses correctly.
- [x] Default compact window fully shows `等待任务`.
- [x] A range that reaches Weibo `9000000000000001` fails closed because its full text cannot be verified.
- [x] Offline regression tests confirm separate sanitized `extend`/`detail` diagnostics.
- [x] Automated security tests confirm diagnostics do not expose Cookie, token, response body, full query, or credentials.
- [x] Full offline tests pass.
- [x] No exporter / Markdown golden change.
- [x] No runtime dependency or TLS policy regression.

## Git workflow

Do not commit automatically from coding agents.

Preferred flow:

1. Agent modifies current working tree.
2. Run environment check and tests.
3. Review `git status`, `git diff --stat`, and `git diff`.
4. Windows human acceptance test.
5. Commit only after acceptance.

## Current stage

0.4.0 · Incomplete Archive Semantics — completed / accepted

Implemented 0.4.0 scope:

- Only `extend=no_view_permission_html` plus `detail=no_view_permission_html`
  can become a single-node `INCOMPLETE` result.
- Top-level and retweeted Post nodes retain independent completeness state.
- `text=None` means unavailable; `text=""` remains a verified empty body.
- Timeline preview is stored and rendered separately as unverified content.
- A fetch-lifetime unavailable cache makes repeated retweet IDs deterministic.
- A conservative global fuse keeps widespread permission-like failures fail-closed.
- Archive integrity is separate from pagination termination.
- Normalized cache uses schema version 1.
- Complete-only Alpha2/Alpha3 Markdown goldens remain byte-for-byte unchanged.

### 0.4.0 acceptance status

- [x] Offline environment check passes.
- [x] All 46 offline regression tests pass.
- [x] GUI title and launcher use the single 0.4.0 version source.
- [x] Windows `.pyw` launch shows the GUI without a persistent console window.
- [x] Existing complete-only goldens remain unchanged.
- [x] New FULL and AI incomplete goldens pass.
- [x] Security, dependency, cancellation, pagination, and geometry regressions pass.
- [x] Human diff review completed.
- [x] Windows target `9000000000000001` continues as an incomplete retweet.
- [x] Windows FULL and AI output reviewed.
- [x] Windows completion integrity warning accepted.

## Accepted Alpha3 baseline

V7 Alpha3 · UX Polish — completed / accepted

Accepted Alpha3 scope:

- Open exported file and export folder after an explicit completion-window click.
- Full archive, AI compact, and custom presets.
- Optional post source/device, post location, and repost/comment/like counts.
- Required date output in minute or date-only form.
- One selected primary Markdown output per task.
- Immutable per-task export-options snapshot.
- Complete normalized cache independent of Markdown display options.

Alpha3 must not simultaneously introduce API, network, or long-Weibo core refactors.

### Alpha3 acceptance status

- [x] Offline environment check passes.
- [x] All 30 offline regression tests pass.
- [x] Legacy full and AI golden files remain byte-for-byte unchanged.
- [x] New full, AI, and custom-minimal Alpha3 goldens pass.
- [x] Existing final output survives simulated cancellation/failure before atomic commit.
- [x] Human diff review completed.
- [x] Windows preset/custom export acceptance completed.
- [x] Windows completion-window and `os.startfile` behavior accepted.
- [x] Windows main-window geometry accepted.
- [x] Custom settings, QR login, and completion-window centering/work-area clamp accepted on Windows.

## Cross-tool handoff rule

Treat these as sources of truth:

- Git working tree: current code truth.
- `PROJECT_STATE.md`: current project/status truth.
- `DECISIONS.md`: long-lived architecture/product decisions.
- Investigation documents: evidence truth.
- Git commits: history truth.

When switching between Codex, Claude Code, or another coding agent, instruct it to reread:

1. `AGENTS.md`
2. `docs/PROJECT_STATE.md`
3. `docs/DECISIONS.md`
4. current `git status`
5. current `git diff`

Do not rely on stale chat history as the source of truth.
