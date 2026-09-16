# Security Model

## Network

Weibo Text Archiver uses the Python standard-library HTTPS stack and the system default TLS certificate verification.

Project code must not use:

```python
verify=False
ssl._create_unverified_context()
```

Certificate verification failures stop the task rather than falling back to an insecure connection.

### Independent image backup

Image metadata discovery uses authenticated Weibo timeline requests to `m.weibo.cn`. Actual image GETs use a separate credential-free HTTPS client restricted to `wx<digits>.sinaimg.cn`, with the fixed Referer `https://m.weibo.cn/`. They receive no Weibo Cookie, saved credential or Authorization header; this client has no CookieJar and rejects redirects. TLS verification stays enabled. Downloads are sequential and validate bounded size, image signatures and MIME consistency before atomic local commit.

Images and the separate schema-1 image manifest remain local. The manifest records source identity, relative local files, integrity facts and download results, but does not persist remote image URLs, cookies, request headers or post text. These local files can still contain personal information and image metadata and should be protected accordingly. Valid existing files are checked before reuse; cancellation does not delete previously saved images. Image results do not alter text archive integrity.

The application uses no browser automation and sends no telemetry. Image backup does not broaden the existing public GitHub update check described below.

## Credentials and diagnostics

Diagnostic logs and `last_error.txt` redact known credential fields, including:

- `SUB`
- `SUBP`
- `SSOLoginState`
- CSRF headers and tokens
- alternate or token-like query values

On Windows, the application protects the saved Weibo login state with Windows DPAPI scoped to the current Windows user and stores the protected binary at:

```text
%LOCALAPPDATA%\WeiboTextExporter\credential.dat
```

Existing plaintext `cookie.txt` credentials are migrated only after the protected credential has been atomically written, decrypted again, and validated. Failed migration leaves the legacy credential intact. Source execution on non-Windows systems retains the local plaintext file as a compatibility fallback without adding keyring dependencies.

DPAPI protects against casual plaintext disclosure and access from other Windows user contexts. It does not protect credentials from malicious software already running as the same Windows user. Users should never share either protected or legacy credential files.

"Clear login information" removes saved login/session material only. It does not erase normalized archive caches or previously exported Markdown files.

## Local archive privacy

`v7_cache/<uid>/last_success.json` is a normalized local archive. Version 4 adds explicit `platform_tombstone` unavailable-content semantics. Version 3 adds fetch-time visibility state and intentionally retained `type`/`list_id`/valid-string `list_idstr` provenance, but must not be assumed to understand the version 4 tombstone reason. Version 2 stores source timestamp provenance, known UTC offsets, and optional author UIDs but has no visibility fact, so it must not be interpreted as a visibility-aware archive. Version 1 cannot recover the source offset or author UID. The application currently writes this cache but does not restore archives from it. It does not contain cookies, request headers, media URLs, full queries, response bodies, or long-text attempt diagnostics.

It does contain post text and any explicitly marked timeline preview, so it remains personal archive data and should be protected like the exported Markdown files. Unversioned legacy cache files must not be silently treated as a trusted archive source.

Visibility filtering operates only on records already returned to the current authenticated session. It does not enumerate inaccessible posts, infer hidden counts, or issue per-post visibility requests.

## Privileges

The application does not require administrator privileges, install a service, modify the firewall, or write to system directories.

## Telemetry and update check

The application includes no telemetry or usage-statistics upload. Once per launch, it performs one lightweight unauthenticated request to the public GitHub latest-release endpoint to compare strict stable version numbers. The request does not include a Weibo UID, Cookie, saved credential, archive text, exported content, or export settings. Failure is silent and does not affect normal use. The application does not automatically download, install, or self-update.
