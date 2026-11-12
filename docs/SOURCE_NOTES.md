# Source and Origin Notes

## Weibo Text Archiver Implementation

The `weibo_archive/` codebase, tests (`tests/`), documentation (`docs/`), packaging scripts (`tools/`), and GUI assets (`weibo_archive/assets/`) are original to this project and are released under the root [MIT License](../LICENSE).

## Historical Background

Early pre-release iterations of this project experimented with launching external crawler scripts at runtime. That architecture was completely removed:

- No runtime code is downloaded from GitHub or any other remote source.
- No runtime `pip install` or environment mutation occurs.
- No code from `dataabc/weibo-crawler` is included in this repository or its release bundles.

All Weibo HTTPS requests, Passport QR authentication, timeline pagination, long-text hydration, normalized data modeling, Markdown rendering, and independent CDN image backup are implemented directly inside `weibo_archive/`.

## Bundled Third-Party Components

The repository vendors `python-qrcode` (under `vendor/qrcode/`) for offline QR matrix generation without runtime `pip install` or network downloads. Its license and attribution are documented in [vendor/THIRD_PARTY_NOTICES.txt](../vendor/THIRD_PARTY_NOTICES.txt) and [vendor/THIRD_PARTY_LICENSES/python-qrcode_LICENSE.txt](../vendor/THIRD_PARTY_LICENSES/python-qrcode_LICENSE.txt).

`Pillow` is a build-time dependency only (`tools/build/requirements-build.txt`, used to generate Windows icons in `tools/generate_icon.py`); it is excluded from the packaged runtime bundle (`WeiboTextArchiver_Windows.zip`), which contains the CPython runtime, Tcl/Tk, `weibo_archive/`, and vendored `python-qrcode`.
