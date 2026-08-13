from __future__ import annotations

import http.cookiejar
import json
import ssl
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Optional


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/145.0.0.0 Safari/537.36"
)


class Cancelled(RuntimeError):
    pass


@dataclass(frozen=True)
class SafeRequestDiagnostic:
    """Response/request metadata that is safe to include in local diagnostics."""

    classification: str
    host: str
    path: str
    status: Optional[int] = None
    content_type: str = ""
    body_bytes: Optional[int] = None


class NetworkError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        diagnostic: SafeRequestDiagnostic | None = None,
    ):
        super().__init__(message)
        self.diagnostic = diagnostic


class RateLimited(NetworkError):
    pass


class InvalidResponse(NetworkError):
    pass


@dataclass
class ResponseData:
    body: bytes
    url: str
    status: int
    content_type: str = ""

    def safe_diagnostic(self, classification: str) -> SafeRequestDiagnostic:
        host, path = _safe_endpoint(self.url)
        return SafeRequestDiagnostic(
            classification=classification,
            host=host,
            path=path,
            status=self.status,
            content_type=self.content_type,
            body_bytes=len(self.body),
        )


def _safe_endpoint(url: str) -> tuple[str, str]:
    """Return host/path only; query and fragment values are intentionally discarded."""
    parsed = urllib.parse.urlsplit(url)
    return parsed.hostname or "", parsed.path or "/"


def _normalized_content_type(value: str | None) -> str:
    # Parameters are unnecessary for diagnostics and can contain arbitrary text.
    return str(value or "").split(";", 1)[0].strip().lower()


def classify_non_json_response(raw: str, content_type: str = "") -> str:
    """Classify transient response text without retaining or quoting its body."""
    lowered = raw.lower()
    if any(marker in raw for marker in ("验证码", "访问频次", "访问频繁", "安全验证")):
        return "challenge_html"
    if "登录" in raw and ("passport" in lowered or "login" in lowered):
        return "login_html"
    if "暂无查看权限" in raw:
        return "no_view_permission_html"
    if content_type == "text/html" or "<html" in lowered or "<!doctype html" in lowered:
        return "unexpected_html"
    return "invalid_json"


class HttpClient:
    """Small HTTPS client with cancellation, retries and Windows system trust."""

    def __init__(self, cookie_header: str = "", cancel_event: threading.Event | None = None):
        self.cookie_header = cookie_header.strip()
        self.cancel_event = cancel_event or threading.Event()
        self.jar = http.cookiejar.CookieJar()
        context = ssl.create_default_context()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=context),
            urllib.request.HTTPCookieProcessor(self.jar),
        )
        self.request_count = 0

    def _combined_cookie_header(self) -> str:
        pairs: dict[str, str] = {}
        for part in self.cookie_header.split(";"):
            if "=" not in part:
                continue
            key, value = part.split("=", 1)
            if key.strip() and value.strip():
                pairs[key.strip()] = value.strip()

        # Cookies learned during preheat/requests should supplement the saved
        # login cookie rather than being silently suppressed by an explicit header.
        for cookie in self.jar:
            if cookie.name and cookie.value:
                pairs[cookie.name] = cookie.value

        return "; ".join(f"{k}={v}" for k, v in pairs.items())

    def _check_cancelled(self):
        if self.cancel_event.is_set():
            raise Cancelled("任务已取消。")

    def wait(self, seconds: float):
        if seconds <= 0:
            self._check_cancelled()
            return
        if self.cancel_event.wait(seconds):
            raise Cancelled("任务已取消。")

    def request(
        self,
        url: str,
        *,
        params: dict | None = None,
        headers: dict | None = None,
        timeout: float = 15,
        retries: int = 3,
    ) -> ResponseData:
        if params:
            query = urllib.parse.urlencode(params)
            url = url + ("&" if "?" in url else "?") + query

        base_headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Referer": "https://m.weibo.cn/",
        }
        combined_cookie = self._combined_cookie_header()
        if combined_cookie:
            base_headers["Cookie"] = combined_cookie
        if headers:
            base_headers.update(headers)

        last_error: Exception | None = None

        for attempt in range(retries):
            self._check_cancelled()
            req = urllib.request.Request(url, headers=base_headers, method="GET")
            self.request_count += 1
            try:
                with self.opener.open(req, timeout=timeout) as resp:
                    body = resp.read()
                    return ResponseData(
                        body=body,
                        url=resp.geturl(),
                        status=getattr(resp, "status", 200),
                        content_type=_normalized_content_type(
                            resp.headers.get("Content-Type")
                        ),
                    )
            except urllib.error.HTTPError as exc:
                host, path = _safe_endpoint(exc.geturl() or url)
                diagnostic = SafeRequestDiagnostic(
                    classification=f"http_{exc.code}",
                    host=host,
                    path=path,
                    status=exc.code,
                    content_type=_normalized_content_type(
                        exc.headers.get("Content-Type") if exc.headers else ""
                    ),
                )
                if exc.code in (403, 414, 429, 432):
                    last_error = RateLimited(
                        f"微博限制了当前请求（HTTP {exc.code}）。请稍后再试。",
                        diagnostic=diagnostic,
                    )
                else:
                    last_error = NetworkError(
                        f"微博请求失败（HTTP {exc.code}）。",
                        diagnostic=diagnostic,
                    )
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                host, path = _safe_endpoint(url)
                reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
                classification = "timeout" if isinstance(reason, TimeoutError) else "network_error"
                last_error = NetworkError(
                    "微博网络请求超时。"
                    if classification == "timeout"
                    else "微博网络请求失败。",
                    diagnostic=SafeRequestDiagnostic(
                        classification=classification,
                        host=host,
                        path=path,
                    ),
                )

            if attempt + 1 < retries:
                self.wait(2 ** attempt)

        assert last_error is not None
        raise last_error

    def json_response(self, url: str, **kwargs) -> tuple[dict, ResponseData]:
        response = self.request(url, **kwargs)
        raw = response.body.decode("utf-8", errors="replace")
        parse_diagnostic = None
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            # JSONDecodeError retains the complete source document in .doc.
            # Raise only after leaving the except block so it is not attached to
            # InvalidResponse as __cause__ or __context__.
            parse_diagnostic = response.safe_diagnostic(
                classify_non_json_response(raw, response.content_type)
            )

        if parse_diagnostic is not None:
            raise InvalidResponse(
                "微博返回的不是可用 JSON。可能遇到登录失效、风控或临时网页错误。",
                diagnostic=parse_diagnostic,
            )
        if not isinstance(data, dict):
            raise InvalidResponse(
                "微博 JSON 根节点不是对象。",
                diagnostic=response.safe_diagnostic("json_root_not_object"),
            )
        return data, response

    def json(self, url: str, **kwargs) -> dict:
        return self.json_response(url, **kwargs)[0]

    def text_response(self, url: str, **kwargs) -> tuple[str, ResponseData]:
        response = self.request(url, **kwargs)
        return response.body.decode("utf-8", errors="replace"), response

    def text(self, url: str, **kwargs) -> str:
        return self.text_response(url, **kwargs)[0]
