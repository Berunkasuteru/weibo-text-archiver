# Weibo Text Archiver

A local-first Windows desktop utility for exporting personal or accessible Weibo timelines into verifiable Markdown archives, with an optional independent image backup tool.

It runs entirely on your machine without browser automation, cloud relays, or built-in LLMs.

## Highlights

- **Single-Fetch Multi-Output** — Generate a **Full Archive**, **AI Analysis Edition (`WEIBO_AI_2`)**, and **Custom Export** in a single network pass from the same normalized snapshot.
- **Account Detection & Vanity Profile Links** — One-click **填入我的账号** detects the signed-in UID and nickname, and exports also accept `/n/<nickname>`, `/p/100505<uid>`, and custom Weibo profile URLs while keeping single-post links guarded.
- **Offline Cache & Incremental Sync (`离线缓存…`)** — Re-export Markdown or budget-split AI volumes directly from saved Schema-5 `last_success.json` caches without re-fetching, or run overlapping incremental updates merged by post ID.
- **Portable JSON & Image-Linked HTML Viewer** — Export structured JSON backups and offline HTML archives (`*_图文浏览版.html`) with local search, filtering, lightbox zoom, and read-only matching against downloaded `WeiboImages_<UID>/manifest.json` images.
- **Fail-Closed Text Integrity** — Hydrates truncated long posts (`isLongText`) and nested reposts. When full text cannot be obtained due to confirmed permission boundaries or platform tombstones, records are explicitly marked (`PREVIEW_ONLY`, `INCOMPLETE`, `PLATFORM_TOMBSTONE`) rather than silently passed off as complete text.
- **Reader-Verifiable AI Format (`WEIBO_AI_2`)** — Separates top-level commentary from `//@` repost chains and source bodies, deduplicates repeated texts (`REF=W<n>.RT` / `REF=W<n>.OWN`), and can generate budget-limited or multi-part copies from memory after export without re-fetching.
- **Independent Image Backup** — Downloads timeline images (standard photos, GIFs, and Live Photo still frames) over credential-free HTTPS with SHA-256 verification, resumable local manifests (`manifest.json`), and explicit enumeration gap reporting.

## Quick Start

### Standalone Windows Release (Recommended)

1. Download `WeiboTextArchiver_Windows.zip` from [GitHub Releases](https://github.com/Berunkasuteru/weibo-text-archiver/releases) and verify its SHA-256 checksum (against `SHA256.txt` when attached, or the checksum in the release notes).
2. Extract the full archive (keep `WeiboTextArchiver.exe` alongside `_internal\`) and launch `WeiboTextArchiver.exe`.

### Run from Source

Requires Windows 10/11 and Python 3.10+ with `tkinter` (`qrcode` 8.2 is vendored under `vendor/qrcode/`, so no external runtime dependency installation is required):

```powershell
python -m weibo_archive.app
```

### Typical Workflow

The desktop GUI uses Simplified Chinese labels tailored for Weibo archiving:

1. **Sign In** — Click **扫码登录 / 更新** (Scan QR / Update) to sign in via Weibo Passport QR code.
2. **Target** — Use the signed-in account, or enter a target UID / Weibo profile URL.
3. **Range** — Start with **测试导出** (Trial Export — Latest 20 posts) to verify access, or select **全量快照** (Full Archive), **最近 N 条** (Latest N), or **从 YYYY-MM-DD 起** (Since Date).
4. **Outputs** — Check one or more of **AI 分析版** (AI Analysis Edition), **完整归档** (Full Archive), or **自定义导出** (Custom Export), choose an output folder, and click **开始导出 →**.

## Output Modes

| Mode | Purpose | Default Output Naming |
| :--- | :--- | :--- |
| **Full Archive** | Human-readable archival record with complete metadata, engagement counts, timestamps, and visibility labels. | `<Name>_<UID>_<Range>_完整.md` |
| **AI Analysis Edition** | Compact, structured `WEIBO_AI_2` Markdown with separated authorship layers and body deduplication. Supports on-demand smaller/split copies after export. | `<Name>_<UID>_<Range>_AI分析版.md` |
| **Custom Export** | User-selected metadata fields and content filters (original posts vs. reposts, visibility, keyword filters). | `<Name>_<UID>_<Range>_自定义_完整.md` or `*_自定义_AI分析.md` |
| **Image Backup** | Separate window (**图片备份…**) that saves timeline images and `manifest.json` without coupling to text export. | `WeiboImages_<UID>/` |

## Privacy & Security

- **Local-First & No Built-In LLM** — The application never uploads your archive to third-party servers. Generating an **AI Analysis Edition** only writes a local Markdown file; if you later upload that file to an external AI service, that disclosure is initiated solely by you.
- **DPAPI Credential Storage** — Login cookies are encrypted with Windows DPAPI (`CryptProtectData`) under `%LOCALAPPDATA%\WeiboTextExporter\credential.dat` and can be cleared from the UI at any time.
- **Strict Redirect & Host Policy** — Saved cookies are sent only to HTTPS `weibo.cn`, `weibo.com`, and `sina.com.cn` hosts on the default port. Image CDN requests use a separate client that sends no cookies or credentials.
- **Diagnostics Redaction** — Local error logs (`last_error.txt`) automatically scrub cookies, tokens, and raw response bodies.
- **Silent Update Check** — On startup, the app makes a single unauthenticated HTTPS request to `api.github.com` to check for a newer release and fails silently if offline.

## Development & Verification

```powershell
# Check runtime and build dependencies
python tools/build/environment_check.py

# Run test suites
python tests/run_tests.py
python tests/test_image_core.py
python tests/test_image_ui.py

# Build reproducible Windows release bundle (onedir + normalized ZIP + SHA256)
tools\windows\BUILD_WINDOWS.bat
```

## Documentation & License

- [Architecture](docs/ARCHITECTURE.md) · [Engineering Decisions](docs/DECISIONS.md) · [Source Notes](docs/SOURCE_NOTES.md)
- [Security Policy](docs/SECURITY.md) · [Changelog](CHANGELOG.md) · [Third-Party Notices](vendor/THIRD_PARTY_NOTICES.txt)
- Licensed under the [MIT License](LICENSE).
