# Weibo Text Archiver

A local-first Windows desktop utility for exporting personal or accessible Weibo timelines into verifiable Markdown archives, with an optional independent image backup tool.

It runs entirely on your machine without browser automation, cloud relays, or built-in LLMs.

## Highlights

- **Single-Fetch Multi-Output** — Generate a **Full Archive**, **AI Analysis Edition (`WEIBO_AI_2`)**, and **Custom Export** in a single network pass from the same normalized snapshot.
- **Fail-Closed Text Integrity** — Hydrates truncated long posts (`isLongText`) and nested reposts. When full text cannot be obtained due to confirmed permission boundaries or platform tombstones, records are explicitly marked (`PREVIEW_ONLY`, `INCOMPLETE`, `PLATFORM_TOMBSTONE`) rather than silently passed off as complete text.
- **Reader-Verifiable AI Format (`WEIBO_AI_2`)** — Separates top-level commentary from `//@` repost chains and source bodies, deduplicates repeated texts (`REF=W<n>.RT` / `REF=W<n>.OWN`), and can generate budget-limited or multi-part copies from memory after export without re-fetching.
- **Independent Image Backup** — Downloads timeline images (standard photos, GIFs, and Live Photo still frames) over credential-free HTTPS with SHA-256 verification, resumable local manifests (`image_manifest.json`), and explicit enumeration gap reporting.

## Quick Start

### Standalone Windows Release (Recommended)

1. Download `WeiboTextArchiver_Windows.zip` and `SHA256.txt` from [GitHub Releases](https://github.com/Berunkasuteru/weibo-text-archiver/releases).
2. Extract the full archive (keep `WeiboTextArchiver.exe` alongside `_internal\`) and launch `WeiboTextArchiver.exe`.

### Run from Source

Requires Windows 10/11 and Python 3.10+ with `tkinter` and `qrcode`:

```powershell
pip install qrcode==8.2
python -m weibo_archive.app
```

### Typical Workflow

1. **Sign In** — Click **Scan QR / Update** to sign in via Weibo Passport QR code.
2. **Target** — Use the signed-in account, or enter a target UID / Weibo profile URL.
3. **Range** — Start with **Trial Export (Latest 20 posts)** to verify access, or select **Full Archive**, **Latest N**, or **Since Date (`YYYY-MM-DD`)**.
4. **Outputs** — Check one or more of **Full Archive**, **AI Analysis Edition**, or **Custom Export**, choose an output folder, and start the export.

## Output Modes

| Mode | Purpose | Default Filename Suffix |
| :--- | :--- | :--- |
| **Full Archive** | Human-readable archival record with complete metadata, engagement counts, timestamps, and visibility labels. | `.md` |
| **AI Analysis Edition** | Compact, structured `WEIBO_AI_2` Markdown with separated authorship layers and body deduplication. Supports on-demand smaller/split copies after export. | `_AI.md` |
| **Custom Export** | User-selected metadata fields and content filters (original posts vs. reposts, visibility, keyword filters). | `_custom.md` |
| **Image Backup** | Separate window (`Image Backup…`) that saves timeline images and `image_manifest.json` without coupling to text export. | `<Nickname>_<UID>_images/` |

## Privacy & Security

- **Local-First & No Built-In LLM** — The application never uploads your archive to third-party servers. Generating an **AI Analysis Edition** only writes a local Markdown file; if you later upload that file to an external AI service, that disclosure is initiated solely by you.
- **DPAPI Credential Storage** — Login cookies are encrypted with Windows DPAPI (`CryptProtectData`) under `%LOCALAPPDATA%\WeiboTextArchiver\credential.dat` and can be cleared from the UI at any time.
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
- [Security Policy](SECURITY.md) · [Changelog](CHANGELOG.md) · [Third-Party Notices](THIRD_PARTY_NOTICES.txt)
- Licensed under the [MIT License](LICENSE).
