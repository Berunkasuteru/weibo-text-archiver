from __future__ import annotations

import re
import time
import traceback
from pathlib import Path

from .credentials import peek_cookie_header
from .paths import COOKIE_FILE, CREDENTIAL_FILE, ERROR_FILE


_SECRET_KEYS = ("SUB", "SUBP", "SSOLoginState", "X-CSRF-TOKEN")

_QUERY_TOKEN_RE = re.compile(
    r"(?i)(?P<key>(?:alt|token|ticket|st|sso(?:loginstate)?|subp?|x-csrf-token))"
    r"(?P<sep>\s*=\s*)(?P<value>[^&\s\"'<>]+)"
)
_COOKIE_PAIR_RE = re.compile(
    r"(?i)(?P<key>SUBP?|SSOLoginState|X-CSRF-TOKEN)"
    r"(?P<sep>\s*=\s*)(?P<value>[^;\s,\"'<>]+)"
)


_COOKIE_CACHE: dict[Path, tuple[tuple[int, int] | None, list[str]]] = {}
_PROTECTED_CACHE: tuple[tuple[int, int] | None, list[str]] | None = None


def _extract_secret_values(header: str) -> list[str]:
    values: list[str] = []
    for part in header.split(";"):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        if key.strip() in _SECRET_KEYS and value.strip():
            values.append(value.strip())
    return values


def _read_cookie_values(cookie_file: Path = COOKIE_FILE) -> list[str]:
    try:
        st = cookie_file.stat()
        stamp: tuple[int, int] | None = (st.st_mtime_ns, st.st_size)
    except OSError:
        _COOKIE_CACHE[cookie_file] = (None, [])
        return []

    cached = _COOKIE_CACHE.get(cookie_file)
    if cached is not None and cached[0] == stamp:
        return list(cached[1])

    try:
        raw = cookie_file.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        _COOKIE_CACHE[cookie_file] = (None, [])
        return []

    result = sorted(set(_extract_secret_values(raw)), key=len, reverse=True)
    _COOKIE_CACHE[cookie_file] = (stamp, result)
    return list(result)


def _protected_cookie_values() -> list[str]:
    """Secrets from the DPAPI store, which outlives the legacy plaintext file."""
    global _PROTECTED_CACHE
    try:
        st = CREDENTIAL_FILE.stat()
        stamp: tuple[int, int] | None = (st.st_mtime_ns, st.st_size)
    except OSError:
        stamp = None

    if _PROTECTED_CACHE is not None and _PROTECTED_CACHE[0] == stamp:
        return list(_PROTECTED_CACHE[1])

    values = _extract_secret_values(peek_cookie_header()) if stamp else []
    result = sorted(set(values), key=len, reverse=True)
    _PROTECTED_CACHE = (stamp, result)
    return list(result)


def _secret_values(cookie_file: Path | None) -> list[str]:
    if cookie_file is not None:
        return _read_cookie_values(cookie_file)
    values = _read_cookie_values(COOKIE_FILE) + _protected_cookie_values()
    return sorted(set(values), key=len, reverse=True)


def redact_text(value: object, cookie_file: Path | None = None) -> str:
    out = str(value)
    for secret in _secret_values(cookie_file):
        out = out.replace(secret, "***")
    out = _COOKIE_PAIR_RE.sub(
        lambda m: f"{m.group('key')}{m.group('sep')}***", out
    )
    out = _QUERY_TOKEN_RE.sub(
        lambda m: f"{m.group('key')}{m.group('sep')}***", out
    )
    return out


def save_detailed_error(context: str, exc: BaseException) -> str:
    raw = (
        f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {context}\n"
        + "=" * 72
        + "\n"
        + "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    )
    safe = redact_text(raw)
    try:
        ERROR_FILE.parent.mkdir(parents=True, exist_ok=True)
        ERROR_FILE.write_text(safe, encoding="utf-8")
        return str(ERROR_FILE)
    except Exception:
        return "（详细错误日志写入失败）"
