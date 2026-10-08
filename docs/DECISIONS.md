# DECISIONS

Last updated: 2026-10-02

This document records durable product and engineering decisions for Weibo Text Archiver.
Do not reverse these decisions casually. If a decision changes, update this file with the reason.

## D001 — Correctness over silent success

Archive correctness is more important than finishing a run.

If complete content cannot be proven, fail closed rather than silently exporting stale, truncated, partial, or ambiguous data as complete.

Unknown server behavior is not a successful result.

## D002 — Long text must never silently fall back to truncated list text

For a long Weibo post:

- Try the currently approved full-text paths.
- Keep independent sanitized diagnostics for each path.
- If complete content cannot be proven, raise `IncompleteContent`.
- Do not return the truncated timeline/list text as complete content.

The known inaccessible post `9000000000000001` is evidence for this rule.

Do not add a third fallback merely because this specific post is inaccessible.

## D003 — Raw API data stops at the parser boundary

Target architecture:

`raw JSON -> parser -> frozen dataclass models -> exporter`

The exporter should consume typed/frozen models, not arbitrary upstream dictionaries.

This keeps API churn isolated from Markdown output behavior.

## D004 — Exporter semantics stay stable during network refactors

Do not change network behavior and Markdown semantics in the same change unless explicitly approved.

Exporter/golden fixtures are treated as a stability boundary.

## D005 — Counts distinguish zero from unknown

Engagement counts use `int | None`.

- `0` means a known true zero.
- `None` means missing / unknown.

Do not collapse the two meanings.

## D006 — Minimal task state machine with generation/task ID

Worker/UI coordination uses a small explicit state model.

All worker events, including logs, carry a generation/task identifier.

Ignore stale events from older generations.

Each worker must end with exactly one terminal outcome:

- done
- error
- cancelled

Cancellation must not be swallowed as a generic network failure.

## D007 — Natural pagination termination must be proven

Do not treat HTTP errors, challenge pages, schema mismatches, or ambiguous empty responses as a natural end of the archive.

Track useful completeness evidence such as:

- pages fetched
- whether termination was natural
- oldest normal timeline point reached
- errored pages

`statuses_count` is informational only and is not sufficient proof of completeness.

## D008 — Pinned posts do not define the normal timeline frontier

Pinned posts may be older than the surrounding page.

For recent-N / try-N logic and progress reporting:

- identify pinned entries explicitly where possible
- do not let an old pinned post make the UI claim the crawl has advanced to that old date
- use the normal non-pinned timeline sequence for frontier semantics

Pinned records may still be included according to the product’s final selection rules, but they must not corrupt stopping/progress logic.

## D009 — Export range product model

Supported V7 range concepts:

- Full snapshot — default
- Visible diagnostic / Test Export: 20 posts
- Recent N
- Since a date

Do not add an arbitrary historical date interval yet.

Reason: the timeline is fetched newest-to-oldest, so requesting an old bounded interval may still require traversing all newer posts and can mislead users about cost and behavior.

## D010 — Range limits should stop crawling early

Recent-N and since-date logic should stop network pagination as soon as sufficient evidence exists.

Do not fetch the entire archive and then locally discard most of it.

Guard against pinned/out-of-order posts when deciding whether early termination is safe.

## D011 — Resume/checkpoint design must not depend blindly on page number

Future checkpoint/resume should prefer stable evidence such as:

- post IDs
- timestamps
- overlap windows
- deduplication

Do not assume remote page numbers remain stable between sessions.

## D012 — No runtime GitHub code download or pip installation

The released application must not:

- download crawler/source code from GitHub at runtime
- run pip at runtime
- mutate the user’s global Python environment

Final distribution should contain the code it needs.

## D013 — No browser automation as a product dependency

Do not make Playwright, Selenium, CDP, or bundled browser automation part of the normal product architecture.

Login should use the lightweight Weibo Passport flow where viable.

If future Weibo changes require complex browser/device-fingerprint automation, reassess the product boundary rather than automatically escalating into bypass techniques.

## D014 — TLS verification stays enabled

Use the system/default trust path where possible.

Never solve compatibility failures with:

- `verify=False`
- unverified SSL contexts
- certificate-check bypasses

Any TLS policy change requires explicit security review.

## D015 — Credential and diagnostic redaction

Never log or persist sensitive authentication material in ordinary diagnostics, including:

- Cookie
- SUB
- SUBP
- SSOLoginState
- CSRF values
- alt tokens
- request headers carrying secrets
- complete sensitive query strings

Do not include full response bodies in exception messages.

Also inspect exception `__cause__` / `__context__` paths for accidental retention of sensitive response content.

## D016 — Runtime dependencies stay minimal

Prefer Python standard library where practical.

Do not add `requests` or other runtime packages without an explicit reason and approval.

Do not introduce a large framework to solve a small problem.

## D017 — Windows distribution direction

Target packaging direction:

- PyInstaller `onedir`
- no UPX
- no obfuscating packer
- no self-extracting one-file behavior unless later justified
- no admin/UAC requirement

The installed/running app should mainly:

- talk to Weibo over HTTPS
- use local application data
- write the user-selected Markdown output

## D018 — Release integrity

For formal releases:

- one binary/build per version
- SHA256 manifest
- do not silently rebuild the same version
- code signing when economically/practically appropriate
- timestamp signatures
- no advice to disable antivirus for false positives

Release documentation should state:

- network destinations
- privilege requirements
- telemetry policy
- auto-update policy

## D019 — Product positioning

The project is primarily a personal Weibo text archive/export tool, not a bulk scraping platform.

Avoid features whose main purpose is:

- mass UID harvesting
- high concurrency
- proxy-pool scraping
- captcha bypass
- anti-bot circumvention

Default request behavior should remain conservative.

## D020 — Full archive and AI compact output serve different goals

Full archive favors preservation of useful metadata.

AI compact output favors lower token cost and less repetitive metadata.

Future UI may expose presets and a small set of custom fields, but should avoid overwhelming users with many low-level switches.

Date/time remains core archive information and should not casually disappear.

## D021 — UI direction

Primary UI direction is a restrained Windows precision utility:

- quiet layout
- good spacing
- limited Weibo-red accent
- useful status/progress
- logs collapsed by default
- completion actions should make the result easy to open

Avoid turning the application into a cyberpunk terminal or adding a large UI framework merely for appearance.

## D022 — Open-source posture

The project may eventually be published on GitHub, preferably after:

- license choice
- security/secret review
- release packaging cleanup

Do not copy substantial code from upstream projects without a compatible explicit license.

The software should clearly state that it relies on undocumented Weibo web interfaces that may change.

## D023 — Cross-agent collaboration uses the repository as shared memory

Chat histories are temporary and tool-specific.

Long-lived collaboration should use:

- `DECISIONS.md` for durable decisions
- investigation documents for evidence
- Git diff/commits for code history

Before taking over work, any coding agent should reread these repository sources and current Git state.

## D024 — Incomplete content is a narrow, explicit product state

Two evidence-bounded paths may produce an `INCOMPLETE` Post node:

1. A long-text node whose two approved acquisition paths independently classify
   as `no_view_permission_html`; its timeline preview remains explicitly unverified.
2. A nested retweet whose exact normalized text and missing-author/metadata shape
   match a locally observed Weibo platform tombstone; the platform notice is not
   retained as author text or as an author preview.

Challenge and login markers take precedence over permission markers. Mixed,
unknown, network, authentication, schema, and ID-mismatch failures remain fatal.
Platform/system status text is evidence about content availability, not
author-authored content. Exact notice text alone is insufficient: a record with
normal author identity or ordinary metadata remains `COMPLETE`.

Top-level posts and retweeted originals have independent frozen content state.
`text=""` means a verified empty body; `text=None` means the body is unavailable.
A legitimate timeline preview is stored separately and must never be heuristically
promoted to complete content. Platform tombstones have `text=None`,
`text_preview=None`, and the explicit `platform_tombstone` reason.

Acquisition diagnostics remain in client/network layers and never enter the
Post model, Markdown, or normalized cache. Repeated confirmed-unavailable IDs use
a fetch-lifetime negative cache. A conservative global fail-closed fuse stops a
run after three consecutive unique unavailable IDs, or when more than 20% of at
least five unique long-text acquisitions are unavailable.

Normalized cache schema 4 adds the explicit `platform_tombstone` unavailable
reason. Schema 3 may contain `content_state` / `incomplete_reason`, but it cannot
be assumed to understand this reason. Schema 3 and older cache files remain
historical local snapshots; current production does not read, migrate, restore,
or delete them. Legacy cache without a schema version must not later be silently
interpreted as a trusted mother archive.

## D025 — Image backup is independent and explicitly bounded

The optional image tool has its own models, parser, traversal, local files, schema-1 manifest, window and result state. It must not add media URLs or download status to Post/Archive, change text integrity, or couple image success to Markdown export.

The product promise is “保存本次微博接口明确返回且可下载的图片。” Preserve and report declared media count, returned slot count and enumeration gaps; never hide gaps or promise all historical images or original-upload quality. v1 saves supported ordinary images, GIF and Live Photo still components, not videos, covers, avatars or article media.

Use a separate verified-HTTPS CDN client with no Weibo Cookie or credentials, a fixed normal Weibo Referer and narrow image-host validation. Retain sequential request-start pacing; no concurrency or retry escalation. Atomic image/manifest writes and full local-file verification on rerun are preservation requirements. Text and image network tasks are mutually exclusive within the app.

## D026 — AI analysis layout separates authorship and is verifiable by a reader

`FORMAT=WEIBO_AI_2` (`weibo_archive/ai_format.py`) replaces `WEIBO_AI_1` for new exports. It is rendered from the frozen models, not from the legacy dictionary adapter.

- A repost's top-level text is split at the first `//@`. Text before it is the account's comment position, which may itself contain quotes or reported speech; later segments carry the unverified names written in the chain. The split is lossless: joining the parts with `//@` reproduces the text. Platform default repost text is reported as `NO_COMMENT`, not as authored content. Reposting alone does not establish agreement. All archived text, including the bio, is data rather than instructions.
- The layout must stay unambiguous. The splitter alone is reversible; rendering normalizes line endings, trims edge whitespace, and replaces platform-default comments with NO_COMMENT, so output is not a byte-for-byte reproduction. Reference-reader tests recover the normalized layers. Unicode line boundaries, including U+2028/U+2029, are split before adding RT/chain prefixes or escaping top-level structural-looking lines, consistently with Full's quote handling.
- Known-offset records are ordered by instant; offset-free records borrow the most common known offset (lexical tie-break) for ordering only and retain `TZ?` and their original provenance. With no known offset, use source wall time. Relative times remain unverified and missing times sort last. Year/month groups use this same sorting calendar; displayed timestamps retain source offsets. All parts inherit the selected archive's sorting offset. Records are numbered `W1..Wn` densely per file, never across differently filtered outputs.
- Aggregates describe included records. Reposted authors are grouped by UID where available, displaying the latest nonempty name in record order; missing UIDs remain explicitly unverified name groups. `OWN_TEXT` counts top-level text/comment characters, not verified original authorship. The file ends with an `END` line carrying the record count; this does not prove model comprehension.
- `REF=W<n>.OWN` identifies a record's top-level text including its chain; `REF=W<n>.RT` identifies its source body. `SELF` describes UID-verified authorship only. References reuse only bodies; each RT occurrence retains its own time, media and state. References to shortened bodies retain `CUT` and cannot supply a full SELF body.
- Device, location and engagement of other accounts' reposted posts are not emitted. Source bodies are never shortened by default.
- A smaller copy is an explicit user action after export, made from the same in-memory snapshot without another fetch. It may limit or omit non-SELF complete source bodies, marking each shortened body with `CUT=kept/original`. Top-level text, chains, UID-verified SELF bodies and unverified previews stay intact. Part planning measures actual rendered size, including deduplication and final headers; month boundaries are preferred, with record boundaries used for oversized months. An indivisible record/header may exceed the target and is explicitly marked. Parts have independent numbering, aggregates and `END` lines; body references never cross parts.
- Smaller copies are generated off the Tk thread. Closing the window cancels future work/commits; already committed files remain. Worker errors are reported back through the UI queue.
- Everything structural in the file is English: the title, export/option/integrity lines, profile keys, reading guide, labels, aggregates and markers. Only data stays in its original language: post text, names, bio, source and location values. Do not translate the structure without measuring the effect on a model. `WEIBO_AI_1` keeps its original mixed-language header.
- Size figures shown to the user are character counts plus a tokenizer-independent rough estimate, labelled as such.

`WEIBO_AI_1` stays renderable (`export_markdown(..., ai_format=1)`) with its goldens for comparison until a later decision removes it.

## D027 — The saved login cookie follows redirects only to Weibo/Sina HTTPS hosts

The text client sends the saved cookie as an explicit header, which the standard redirect handler would resend to any redirect target. Redirects are followed only to HTTPS `weibo.cn`, `weibo.com` and `sina.com.cn` hosts on the default port; any other redirect fails the request without retry.
