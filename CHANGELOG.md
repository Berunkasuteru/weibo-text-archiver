# Changelog

## 0.5.0-beta.1

- Added fetch-once multi-output selection for Full Archive, AI Compact, and Custom Markdown.
- Added local Custom filters for original/repost type, OR keywords, and an inclusive date slice.
- Reports Custom matched/fetched counts and timestamps that cannot be evaluated by an active date filter.
- Keeps Full and AI outputs independent from Custom filtering and preserves existing archive-integrity semantics.
- Deferred a separate hashtag filter because the normalized model has no reliable structured hashtag field.

## 0.4.3

- Standardized the Windows Release asset as `WeiboTextArchiver_Windows.zip` while retaining a versioned directory inside the archive.
- Added a direct Command Prompt download example for the latest regular GitHub Release.

## 0.4.2

- Improved AI Compact attribution between account text and repost sources.
- Normalized compact location metadata and strengthened media/incomplete-context rules.
- Renamed GUI actions around the export workflow.
- Reduced the test export from 50 posts to 20 posts.
- Removed stale user-visible V7 wording.

## 0.4.1

### Windows packaging
- Added the first packaged Windows preview and standalone Windows 10/11 x64 build.
- Added an original application icon with Tk and PyInstaller integration.
- Standardized the public product and artifact name as Weibo Text Archiver.
- Cleaned up the public repository presentation and release-facing documentation.
- Preserved existing archive-integrity behavior without adding runtime dependencies.

## 0.4.0 (accepted)

### Incomplete archive semantics
- A long-text node becomes `INCOMPLETE` only when both approved `extend` and `detail` paths independently classify as `no_view_permission_html`.
- Top-level posts and retweeted originals keep independent completeness state, so a complete user comment is retained when only its retweeted original is unavailable.
- Timeline previews are stored separately from complete text and are always rendered as `PREVIEW_ONLY` / unverified content.
- Unknown, mixed, network, rate-limit, authentication, schema, and ID-mismatch failures remain fail-closed.

### Safety and integrity
- Challenge/login markers now take precedence over permission markers during non-JSON classification.
- Confirmed unavailable IDs use a fetch-lifetime negative cache for deterministic repeated references.
- Added a conservative global fail-closed fuse: three consecutive unique unavailable IDs, or more than 20% after five unique long-text acquisitions.
- Archive integrity is calculated from frozen Post nodes through one shared function and remains independent of pagination termination.
- Normalized cache schema is now version 1 and stores stable completeness state without request diagnostics.

### Output and UX
- FULL and AI Markdown explicitly mark incomplete top-level and retweeted content; complete-only output remains byte-for-byte compatible with accepted goldens.
- Successful archives containing incomplete records finish as DONE with an integrity warning in the completion window.
- Added a native `.pyw` GUI entry point without a persistent console; console launch remains available for diagnostics, and GUI version text derives from one version source.

## 7.0.0-alpha3 (accepted)

### Export choices
- Added full archive, AI compact, and custom content presets.
- Resolve each GUI selection to an immutable `ExportOptions` snapshot before the worker starts.
- Custom output can hide post source/device, post location, and engagement counts, and can use minute or date-only timestamps.
- Dates and post body text remain mandatory; the same policy applies to top-level and retweeted posts.
- Each task now produces one selected primary Markdown file while the normalized cache remains complete.

### UX
- Added a compact secondary settings window instead of exposing many low-level checkboxes in the main window.
- Replaced the passive completion message with explicit Open File, Open Export Folder, and Close actions.
- Windows launching uses `os.startfile` only after an explicit click and is isolated behind a testable helper.

### Compatibility and safety
- The accepted Alpha2/V5 full and AI renderer defaults remain byte-for-byte covered by their original golden files.
- Added separate Alpha3 full, AI, and custom-minimal golden files.
- Export writes use a unique task-owned temporary file; cancellation or failure never deletes an existing final path.
- No runtime dependency, TLS, network, parser, pagination, or long-text behavior changed.

## 7.0.0-alpha2

### Long-Weibo diagnostics
- Preserve a separate structured, redacted outcome for the `statuses/extend` and mobile-detail full-text attempts.
- Report only host/path, HTTP status, Content-Type, byte count, safe JSON keys and expected-field metadata.
- Removed response-body excerpts from non-JSON exceptions.
- Added regression coverage for the real HTTP 200 no-view-permission response, schema failures and embedded-status ID mismatches.
- Full-text failure remains fail-closed; truncated timeline text is never used as a fallback.

### Windows acceptance fixes
- Trial/recent ranges stop after the requested number of non-pinned timeline candidates instead of fetching an extra page-sized cushion.
- Progress distinguishes accepted timeline candidates from final selection and reports the non-pinned crawl frontier.
- Expanding runtime details grows the Tk window within the current monitor work area; collapsing restores the compact geometry.

## 7.0.0-alpha1

### Architecture
- Removed runtime `weibo-crawler` download/install/execute chain.
- Added standard-library `HttpClient`.
- Added `raw JSON -> parser -> frozen dataclass -> exporter` boundary.
- Added normalized local archive cache.
- Added tiny task state machine plus generation guard.

### Reliability
- Long text fails closed if full content cannot be proven.
- Unknown non-empty timeline cards fail closed instead of masquerading as end-of-history.
- Missing engagement values stay `None` in the model rather than becoming zero.
- Output files are written through temporary files before replacement.
- Cancellation invalidates stale worker events immediately.

### UX
- New Precision Utility GUI.
- Collapsed technical log by default.
- Trial 50 / recent N / since-date / full snapshot.
- Range and snapshot semantics embedded in filenames and file headers.
- Progress includes post count, pages and oldest reached date.

### Security
- No runtime GitHub code download.
- No runtime pip install.
- No browser automation.
- Standard TLS verification stays enabled.
- Credential redaction retained.

### Intentionally deferred
- DPAPI credential storage.
- resumable full crawl.
- incremental update.
- final AI token-format revision.
- PyInstaller onedir / Authenticode release build.


### Alpha 1 发布前 fail-closed 加固
- 未知 `ok=0` JSON 不再因缺少 challenge URL 而误判为自然结束。
- `since_date` 的提前终止要求整页时间均可解析；未知日期会保留并继续抓取。
- 回归测试增至 15 项。
