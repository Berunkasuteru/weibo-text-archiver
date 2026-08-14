# Weibo Text Archiver

A local Windows tool for exporting Weibo posts into trustworthy Markdown archives.

## What it does

- Exports Weibo posts to Markdown.
- Generates any combination of Full Archive, AI 分析版, and Custom outputs from one fetch.
- Supports full snapshots, recent-post limits, date-based ranges, and a 20-post test export.
- Retrieves long-text content through explicitly supported paths.
- Marks narrowly proven unavailable content as incomplete instead of presenting a timeline preview as full text.
- Reports archive integrity separately from pagination completion.
- Processes archive data locally.
- Uses no browser automation and downloads no runtime dependencies.

## Why archive integrity matters

The application never treats a visible timeline preview as verified full text. If approved full-text paths independently prove that content is currently unavailable, the archive marks that record as incomplete and keeps any preview explicitly unverified. Unknown, mixed, authentication, network, schema, and rate-limit failures remain fail-closed.

## Windows

The Windows package targets Windows 10/11 x64 and does not require a Python installation.

### Download from GitHub Releases

Download `WeiboTextArchiver_Windows.zip` from the latest GitHub Release, extract it, and run:

```text
WeiboTextArchiver.exe
```

### Download from Command Prompt

To save the latest regular release in the current Command Prompt directory:

```cmd
curl.exe -fL -o WeiboTextArchiver_Windows.zip https://github.com/Berunkasuteru/weibo-text-archiver/releases/latest/download/WeiboTextArchiver_Windows.zip
```

The downloaded ZIP is saved in the directory currently open in Command Prompt.

### Run from source

To run from source with Python 3:

```text
python -m weibo_archive.app
```

## Export modes

- **Full Archive** preserves the complete supported Markdown record.
- **AI 分析版** preserves the same verified body facts as Full Archive while strengthening structure, deduplication, and semantic labels for AI analysis.
- **Custom** lets users choose selected metadata fields and date precision, then locally filter the fetched records by original/repost type, keyword, and an optional inclusive date slice.

Custom keywords are case-insensitive for ASCII and use substring matching. Multiple keywords use OR; type, keyword, and date categories combine with AND. Searchable text includes the target account's available text and available repost-source text. An incomplete preview may help select a record, but remains explicitly unverified and never becomes complete.

The model has no reliable structured hashtag field, so this beta does not provide a separate hashtag filter. A literal `#topic#` can still be entered as a keyword. Records with unknown timestamps remain eligible when no Custom date filter is active; with a date filter they are excluded from the confirmed slice and reported separately.

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

The current suite contains 50 offline regression checks.

## Build

Run `BUILD_WINDOWS.bat` on Windows. It uses the isolated `.venv-build` environment and creates a PyInstaller `onedir` / `windowed` ZIP plus `SHA256.txt` under `release/`.

## License

Weibo Text Archiver is released under the MIT License.

Third-party components remain subject to their respective licenses. See `THIRD_PARTY_NOTICES.txt` and `THIRD_PARTY_LICENSES/`.
