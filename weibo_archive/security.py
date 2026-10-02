from __future__ import annotations

import re
import time
import traceback
from pathlib import Path

from .paths import COOKIE_FILE, ERROR_FILE


_SECRET_KEYS = ("SUB", "SUBP", "SSOLoginState", "X-CSRF-TOKEN")

_QUERY_TOKEN_RE = re.compile(
    r"(?i)(?P<key>(?:alt|token|ticket|st|sso(?:loginstate)?|subp?|x-csrf-token))"
    r"(?P<sep>\s*=\s*)(?P<value>[^&\s\"'<>]+)"
)
_COOKIE_PAIR_RE = re.compile(
    r"(?i)(?P<key>SUBP?|SSOLoginState|X-CSRF-TOKEN)"
    r"(?P<sep>\s*=\s*)(?P<value>[^;\s,\"'<>]+)"
)


def _read_cookie_values(cookie_file: Path = COOKIE_FILE) -> list[str]:
    try:
        raw = cookie_file.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []

    values: list[str] = []
    for part in raw.split(";"):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        if key.strip() in _SECRET_KEYS and value.strip():
            values.append(value.strip())
    return sorted(set(values), key=len, reverse=True)


def redact_text(value: object, cookie_file: Path = COOKIE_FILE) -> str:
    out = str(value)
    for secret in _read_cookie_values(cookie_file):
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
        ERROR_FILE.write_text(safe, encoding="utf-8")
        return str(ERROR_FILE)
    except Exception:
        return "（详细错误日志写入失败）"
