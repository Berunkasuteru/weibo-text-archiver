from __future__ import annotations

import json
import os
import re
import urllib.request


GITHUB_LATEST_RELEASE_API = (
    "https://api.github.com/repos/Berunkasuteru/"
    "weibo-text-archiver/releases/latest"
)
GITHUB_LATEST_RELEASE_PAGE = (
    "https://github.com/Berunkasuteru/weibo-text-archiver/releases/latest"
)
UPDATE_CHECK_TIMEOUT = 3.0
_STABLE_VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


_UPDATE_OPENER = urllib.request.build_opener(_NoRedirectHandler())


def _stable_version_tuple(value: str) -> tuple[int, int, int] | None:
    match = _STABLE_VERSION_RE.fullmatch(str(value or "").strip())
    if match is None:
        return None
    major, minor, patch = match.groups()
    return int(major), int(minor), int(patch)


def find_newer_github_version(
    current_version: str,
    *,
    opener=None,
) -> str | None:
    """Return a newer strict stable version, or silently decline the check."""
    current = _stable_version_tuple(current_version)
    if current is None:
        return None
    request = urllib.request.Request(
        GITHUB_LATEST_RELEASE_API,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "WeiboTextArchiver-UpdateCheck",
        },
        method="GET",
    )
    selected_opener = opener if opener is not None else _UPDATE_OPENER.open
    try:
        with selected_opener(request, timeout=UPDATE_CHECK_TIMEOUT) as response:
            if int(getattr(response, "status", 200)) != 200:
                return None
            body = response.read(65537)
        if len(body) > 65536:
            return None
        payload = json.loads(body.decode("utf-8"))
        if not isinstance(payload, dict):
            return None
        latest = _stable_version_tuple(payload.get("tag_name"))
    except Exception:
        return None
    if latest is None or latest <= current:
        return None
    return ".".join(str(part) for part in latest)


def launch_official_release_page(launcher=None) -> bool:
    selected = launcher if launcher is not None else getattr(os, "startfile", None)
    if selected is None:
        return False
    try:
        selected(GITHUB_LATEST_RELEASE_PAGE)
    except OSError:
        return False
    return True
