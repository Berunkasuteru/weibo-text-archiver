# Long-Text Investigation — 9000000000000001

> Public repository uses a synthetic post ID; the original live investigation identifier was removed before publication.

## Incident

V7 Alpha 1 failed during “try 50 posts” while hydrating a long `retweeted_status`:

```text
IncompleteContent:
长微博 9000000000000001 的全文无法取得。
为避免静默保存截断正文，本次导出已停止。
```

Investigation was performed locally on Windows using the same logged-in Weibo session that had successfully run V6.6.2.

No authentication credentials are included in this document.

---

## 1. V6.6.2 baseline

The target ID appears inside a `retweeted_status` in the V6.6.2 upstream JSON.

Observed behavior:

- The list JSON contains the target post.
- The available list text is approximately 150 characters.
- The text ends with an “全文 / expand full text” style marker.
- Therefore the list response does not contain the complete body.
- The V6.6.2 Markdown output contains the same truncated body.

Conclusion:

**V6.6.2 did not successfully retrieve the complete text of this post.**

Its apparent successful export was caused by silently falling back to the truncated list text.

---

## 2. Upstream long-text behavior

The locally downloaded `dataabc/weibo-crawler` source was inspected.

For this long-post path, upstream attempts:

```text
https://m.weibo.cn/detail/<numeric-id>
```

If retrieval/parsing fails, upstream ultimately continues using the existing list response.

For this incident, that behavior resulted in truncated content being included in the final export.

Therefore V6.6.2 cannot be used as evidence that complete text was available for this specific post.

---

## 3. Target post probes

Target:

```text
9000000000000001
```

Using the current authenticated session:

### `m.weibo.cn/statuses/extend`

Result:

- HTTP 200
- Content-Type: `text/html`
- Body size: approximately 2471 bytes
- Response represents a “暂无查看权限” / no-view-permission page
- No usable `longTextContent`

### `m.weibo.cn/detail/<id>`

Result:

- HTTP 200
- Content-Type: `text/html`
- Body size: approximately 2471 bytes
- Response represents the same no-view-permission condition
- No usable embedded `status`

Therefore both Alpha 1 fallbacks fail for a real content-access reason, not merely because of a parser exception.

---

## 4. Control samples

Other long Weibo posts were tested using the same authenticated session.

For those control posts:

- `statuses/extend` succeeded.
- mobile detail succeeded.

Therefore:

- the login session is not globally invalid;
- the long-text endpoint is not globally broken;
- the Alpha 1 network path is capable of retrieving other long posts.

This isolates the failure to the target post / its current access state.

---

## 5. `weibo.com` AJAX probes

A read-only probe against the desktop AJAX status endpoint reported:

```text
ok = 0
error_code = 20112
```

for the target post.

This is consistent with the observed “暂无查看权限” responses.

A desktop long-text endpoint was also investigated.

It can return long text for other control posts, but it did not recover this target post.

Therefore it does not solve this incident.

---

## 6. Root-cause conclusion

The target post is currently not available as complete content to the authenticated session.

Alpha 1 therefore behaved correctly by refusing to treat the truncated list text as complete content.

The primary Alpha 1 defect exposed by this incident is diagnostic:

- `statuses/extend` failures are swallowed;
- detail fallback failures are swallowed;
- schema/content failures are not preserved;
- the final exception only states that full text could not be obtained.

The correct Alpha 2 repair is:

**Improve safe diagnostics while preserving fail-closed semantics.**

It must **not** restore V6.6.2's behavior of silently exporting truncated long-post content.

---

## 7. Third fallback assessment

A third desktop AJAX long-text fallback is not justified by this incident.

Reasons:

1. It does not recover this target.
2. Existing mobile fallbacks work for other control long posts.
3. No real sample currently proves:
   - `extend` fails;
   - `detail` fails;
   - desktop long-text succeeds for the same post.
4. Adding another internal endpoint increases compatibility and testing surface without solving the demonstrated failure.

A third fallback may be reconsidered only after obtaining such a differential-success sample and a sanitized response fixture.

---

## 8. Required Alpha 2 behavior

Each long-text attempt should retain only safe diagnostic metadata such as:

- fallback name;
- host/path;
- HTTP status;
- Content-Type;
- response byte count;
- response classification;
- safe JSON key names;
- presence/length of expected content fields;
- embedded-status ID match result.

Diagnostics must never retain:

- Cookie headers;
- SUB / SUBP;
- SSOLoginState;
- CSRF tokens;
- alt tokens;
- complete URL query parameters;
- raw response body;
- response-body excerpts.

If all allowed full-text paths fail, Alpha 2 must continue raising `IncompleteContent`.

No normalized archive or Markdown should be written for that task.

---

## 9. Evidence checklist

- [x] Target ID located in V6.6.2 `retweeted_status`
- [x] V6.6.2 exported truncated rather than complete text
- [x] Upstream long-text path inspected locally
- [x] Target `extend` response classified
- [x] Target `detail` response classified
- [x] Same-session control long posts tested successfully
- [x] Desktop AJAX target returned error code `20112`
- [x] Third fallback did not recover the target
- [x] Minimal repair is diagnostic hardening
- [x] Fail-closed behavior must remain

---

## Evidence status

The investigation supports implementation of a **diagnostic-only Alpha 2 repair**.

It does not support:

- adding a new long-text fallback;
- restoring truncated-content fallback behavior;
- modifying Markdown output semantics.
