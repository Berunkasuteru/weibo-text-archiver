# Source and Origin Notes

## Weibo Text Archiver Implementation

The `weibo_archive/` codebase, tests (`tests/`), documentation (`docs/`), packaging scripts (`tools/`), and GUI assets (`assets/`) are original to this project and are released under the root [MIT License](../LICENSE).

## Historical Background

Early pre-release iterations of this project experimented with launching external crawler scripts at runtime. That architecture was completely removed:

- No runtime code is downloaded from GitHub or any other remote source.
- No runtime `pip install` or environment mutation occurs.
- No code from `dataabc/weibo-crawler` is included in this repository or its release bundles.

All Weibo HTTPS requests, Passport QR authentication, timeline pagination, long-text hydration, normalized data modeling, Markdown rendering, and independent CDN image backup are implemented directly inside `weibo_archive/`.

## Bundled Third-Party Components

The source tree does not vendor third-party source code.

When building the standalone Windows release bundle (`WeiboTextArchiver_Windows.zip`), the build environment bundles the CPython runtime, Tcl/Tk, Pillow, and `python-qrcode`. Their licenses and attributions are documented in [THIRD_PARTY_NOTICES.txt](../THIRD_PARTY_NOTICES.txt) and `THIRD_PARTY_LICENSES/`.
