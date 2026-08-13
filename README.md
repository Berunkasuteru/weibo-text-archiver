# Weibo Text Archiver

A local Windows tool for exporting Weibo posts into trustworthy Markdown archives.

## What it does

- Exports Weibo posts to Markdown.
- Provides Full Archive, AI Compact, and Custom export modes.
- Supports full snapshots, recent-post limits, date-based ranges, and a 20-post test export.
- Retrieves long-text content through explicitly supported paths.
- Marks narrowly proven unavailable content as incomplete instead of presenting a timeline preview as full text.
- Reports archive integrity separately from pagination completion.
- Processes archive data locally.
- Uses no browser automation and downloads no runtime dependencies.

## Why archive integrity matters

The application never treats a visible timeline preview as verified full text. If approved full-text paths independently prove that content is currently unavailable, the archive marks that record as incomplete and keeps any preview explicitly unverified. Unknown, mixed, authentication, network, schema, and rate-limit failures remain fail-closed.

## Windows

The Windows preview targets Windows 10/11 x64.

For the packaged Windows preview, download the Windows ZIP from Releases, extract it, and run:

```text
WeiboTextArchiver.exe
```

No Python installation is required for the packaged application.

To run from source with Python 3:

```text
python -m weibo_archive.app
```

## Export modes

- **Full Archive** preserves the complete supported Markdown record.
- **AI Compact** uses explicit attribution markers and compact metadata intended for LLM analysis.
- **Custom** lets users choose selected metadata fields and date precision while retaining required archive text and dates.

## Privacy and security

- Login credentials remain in local application storage and are used for direct requests to Weibo; the project operates no credential-upload service.
- The application does not use browser automation.
- TLS certificate verification is not bypassed.
- Markdown exports and normalized archive data are written locally.
- Diagnostic output is redacted, but users should still never share their cookie file.

## Limitations

- Weibo may change undocumented endpoints, response structures, or access behavior.
- Unavailable historical content may be explicitly marked incomplete when the narrow evidence requirements are met.
- Login, challenge, rate-limit, widespread access, and unknown failures stop the task rather than producing a misleading complete archive.
- Current packaging is focused on Windows 10/11 x64.

## Development

```text
python environment_check.py
python tests/run_tests.py
```

The current suite contains 48 offline regression checks.

## Build

Run `BUILD_WINDOWS.bat` on Windows. It uses the isolated `.venv-build` environment and creates a PyInstaller `onedir` / `windowed` ZIP plus `SHA256.txt` under `release/`.

## License

Weibo Text Archiver is released under the MIT License.

Third-party components remain subject to their respective licenses. See `THIRD_PARTY_NOTICES.txt` and `THIRD_PARTY_LICENSES/`.
