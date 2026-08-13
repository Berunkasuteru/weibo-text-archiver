# Security Model

## Network

Weibo Text Archiver uses the Python standard-library HTTPS stack and the system default TLS certificate verification.

Project code must not use:

```python
verify=False
ssl._create_unverified_context()
```

Certificate verification failures stop the task rather than falling back to an insecure connection.

## Credentials and diagnostics

Diagnostic logs and `last_error.txt` redact known credential fields, including:

- `SUB`
- `SUBP`
- `SSOLoginState`
- CSRF headers and tokens
- alternate or token-like query values

Version 0.4.1 stores the Weibo login cookie locally at:

```text
%LOCALAPPDATA%\WeiboTextExporter\cookie.txt
```

The cookie is currently stored as plaintext for compatibility with earlier local versions. Users should never share this file. A future credential-storage change must fail safely and require a new login if stored credentials cannot be read.

## Local archive privacy

`v7_cache/<uid>/last_success.json` is a normalized local archive with `schema_version: 1`. It does not contain cookies, request headers, media URLs, full queries, response bodies, or long-text attempt diagnostics.

It does contain post text and any explicitly marked timeline preview, so it remains personal archive data and should be protected like the exported Markdown files. Unversioned legacy cache files must not be silently treated as a trusted archive source.

## Privileges

The application does not require administrator privileges, install a service, modify the firewall, or write to system directories.

## Telemetry

Version 0.4.1 includes no telemetry, usage-statistics upload, or automatic update check.
