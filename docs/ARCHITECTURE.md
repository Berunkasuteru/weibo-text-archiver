# Weibo Text Archiver Architecture

The architecture establishes strict boundaries between upstream Weibo API payloads, normalized domain models, local persistence, and output renderers.

## Repository Layout

```text
.
├── WeiboTextArchiver.pyw      Windows no-console GUI launcher
├── weibo_archive/             Application package and bundled icon assets (`assets/`)
│   ├── app.py / tasking.py / update_check.py
│   ├── auth.py / credentials.py / security.py / paths.py
│   ├── network.py / client.py / htmltext.py / parser.py / models.py / storage.py / performance.py
│   ├── export_options.py / exporter.py / ai_format.py / markdown_v5.py
│   └── image_models.py / image_parser.py / image_client.py / image_downloader.py / image_manifest.py / image_backup.py / image_ui.py
├── vendor/                    Third-party vendored python-qrcode, notices, and licenses
├── tests/                     Offline suites (run_tests.py, test_image_core.py, test_image_ui.py), fixtures, goldens
├── tools/                     Build spec, env check, icon generator, Windows launchers & ZIP packager
└── docs/                      Architecture, durable decisions, source provenance, and security policy
```

## Data Flow

```text
Weibo HTTPS
    │
    ▼
network.py
    │  bytes / JSON
    ▼
client.py
    │  raw API objects
    ▼
parser.py
    │
    ▼
models.py                ← frozen dataclasses / stable product vocabulary
    │
    ├──────────────► storage.py       normalized local cache (schema 5)
    │
    ▼
exporter.py ◄──────── frozen ExportOptions snapshot ◄──────── GUI preset/custom
    │
    ▼
markdown_v5.py           Full & Custom layouts (plus retained WEIBO_AI_1 baseline)
ai_format.py             AI Analysis layout (WEIBO_AI_2), rendered from frozen models
    │
    └──────────────► selected Full / AI Analysis / Custom Markdown outputs
```

Authentication uses a dedicated flow:

```text
auth.py → Weibo Passport QR SSO → local DPAPI-protected credential
```

The GUI (`app.py`) is responsible only for:

```text
input/preset → resolve immutable options → task orchestration → progress → result
```

Presentation options only enter `exporter.py` and the renderers. They never enter `parser.py`, `network.py`, or `models.py`, and never prune the normalized archive written by `storage.py`.

## Core Invariants

### 1. Exporters never inspect raw Weibo JSON

`Post` and `UserProfile` act as the firewall between upstream API payloads and Markdown outputs. Renderers consume frozen dataclasses only; if Weibo changes field names or nesting, changes stay inside `parser.py` and `client.py`.

### 2. Missing data is distinct from zero

In `models.py`:

- `0` means the upstream response explicitly reported zero.
- `None` means the field was omitted or could not be reliably parsed.

### 3. Stale background tasks cannot mutate active UI state

Every worker event carries a `generation` counter. The UI discards any event whose `generation` does not match the active generation. Cancelling a task immediately increments `generation`, preventing background threads from overwriting current UI state.

### 4. Long text is either proven complete or explicitly marked incomplete

When a post is flagged `isLongText`, the client must hydrate its full text. Two evidence-bounded paths may produce an `INCOMPLETE` `Post` node:

1. **Confirmed Permission Boundary** — Both approved full-text endpoints (`extend` and `detail`) for the same post node independently classify as `no_view_permission_html`. The node records `text = None`, preserves the visible timeline snippet in `text_preview`, and sets `content_state = INCOMPLETE`.
2. **Confirmed Platform Tombstone** — A nested repost matches both a controlled platform unavailability notice and the structural signature of a deleted/unavailable post (missing author identity, source, and engagement metadata). It records `text = None`, `text_preview = None`, `incomplete_reason = PLATFORM_TOMBSTONE`, and `content_state = INCOMPLETE`.

Top-level posts and reposted originals maintain independent completeness states. All other network, authentication, challenge, schema, or ID-mismatch errors fail closed rather than falling back to truncated timeline previews.

## Visibility and Normalized Cache Schema

Visibility provenance flows in one direction:

```text
timeline response → parser normalization → frozen Post.visibility → Archive → local Full / AI / Custom filtering
```

- Top-level posts map known visibility codes to `PUBLIC`, `FOLLOWERS`, `FRIENDS`, `PRIVATE`, or `UNKNOWN`.
- Full Archive outputs all fetched records without visibility filtering; AI Analysis Edition (public-only by default) and Custom Export filter locally from the same frozen `Archive`.
- `storage.py` currently writes normalized cache **schema version 5** (adding `Post.checkin` alongside IP `location`, `platform_tombstone` semantics from schema 4, visibility provenance from schema 3, and timestamp/UID provenance from schema 2).

## Independent Image Backup

```text
timeline raw
├─ text client/parser  → Post / Archive → Markdown
└─ image client/parser → ImageRecord / ImageAsset → CDN downloader / manifest.json
```

The Image Backup tool (`image_backup.py`, `image_client.py`, `image_downloader.py`, `image_manifest.py`, `image_models.py`, `image_parser.py`, `image_ui.py`) runs as an independent workflow with its own timeline traversal, frozen request state, window, and `manifest.json` (schema 1):

- It saves only images explicitly returned by the timeline response and downloadable from Sina CDN (standard photos, GIFs, and Live Photo still frames; videos, covers, and avatars are excluded).
- It preserves declared counts, returned slot counts, and enumeration gaps separately.
- Image CDN requests use a dedicated, credential-free HTTPS client with sequential pacing, signature/size/SHA-256 verification, and atomic file + manifest commits.

## Runtime Dependency Policy

At runtime, `weibo_archive/` relies on the Python standard library, `tkinter`, and vendored `qrcode` (for QR matrix generation). It never imports `requests`, `playwright`, `selenium`, or `PIL`, never spawns subprocesses or runs `pip`, never downloads remote code, and never disables TLS certificate verification.
