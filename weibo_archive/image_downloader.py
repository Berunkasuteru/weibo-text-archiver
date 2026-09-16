"""Credential-free, non-redirecting CDN transport and bounded atomic download."""
from __future__ import annotations

import hashlib
import http.client
import os
import re
import shutil
import ssl
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .image_models import ImageError
from .network import Cancelled, USER_AGENT


MAX_IMAGE_BYTES = 50 * 1024 * 1024
SOCKET_TIMEOUT = 15.0
IMAGE_DEADLINE = 60.0
CDN_START_SPACING = 0.5
CHUNK_SIZE = 64 * 1024
DISK_RESERVE = 64 * 1024 * 1024
REFERER = "https://m.weibo.cn/"
MEDIA_EXTENSIONS = {"image/jpeg": ".jpg", "image/png": ".png",
                    "image/gif": ".gif", "image/webp": ".webp"}


def detect_type(prefix: bytes) -> str | None:
    if prefix.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if prefix.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if prefix[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if prefix[:4] == b"RIFF" and prefix[8:12] == b"WEBP":
        return "image/webp"
    return None


def validate_image_url(url: str):
    reason = None
    try:
        if not isinstance(url, str) or any(ord(c) <= 32 or ord(c) == 127 for c in url):
            reason = "invalid_url"
        else:
            p = urllib.parse.urlsplit(url)
            if p.scheme != "https":
                reason = "non_https"
            elif p.username is not None or p.password is not None or p.port not in (None, 443):
                reason = "invalid_url"
            elif not re.fullmatch(r"wx[0-9]+\.sinaimg\.cn", p.hostname or ""):
                reason = "unexpected_host"
            elif p.fragment:
                reason = "invalid_url"
    except (ValueError, TypeError):
        reason = "invalid_url"
    if reason:
        raise ImageError(reason)


class RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _network_reason(exc):
    if isinstance(exc, urllib.error.HTTPError):
        return "unexpected_redirect" if 300 <= exc.code < 400 else f"http_{exc.code}"
    reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
    if isinstance(reason, ssl.SSLError):
        return "tls_error"
    if isinstance(reason, TimeoutError):
        return "timeout"
    if isinstance(reason, http.client.IncompleteRead):
        return "length_mismatch"
    return "network_error"


def _network_call(fn):
    code = None
    try:
        return fn()
    except (urllib.error.URLError, OSError, http.client.HTTPException) as exc:
        code = _network_reason(exc)
        if isinstance(exc, urllib.error.HTTPError):
            exc.close()
    # Raise outside except: URL-bearing exceptions must not become __context__.
    raise ImageError(code)


@dataclass(frozen=True)
class FileFacts:
    media_type: str
    byte_size: int
    sha256: str
    content_type: str | None
    content_length: int | None


def inspect_file(path: Path, *, max_bytes=MAX_IMAGE_BYTES, check=lambda: None) -> FileFacts:
    """Stream local verification, not image decoding; no filename-based trust."""
    size = 0
    digest = hashlib.sha256()
    prefix = b""
    with path.open("rb") as handle:
        while True:
            check()
            chunk = handle.read(CHUNK_SIZE)
            if not chunk:
                break
            size += len(chunk)
            if size > max_bytes:
                raise ImageError("too_large")
            prefix = (prefix + chunk[:16])[:16]
            digest.update(chunk)
    media = detect_type(prefix)
    if not size or media is None or path.suffix != MEDIA_EXTENSIONS[media]:
        raise ImageError("signature_mismatch")
    if media == "image/webp" and int.from_bytes(prefix[4:8], "little") + 8 != size:
        raise ImageError("length_mismatch")
    return FileFacts(media, size, digest.hexdigest(), None, None)


class ImageDownloader:
    def __init__(self, *, cancel_event=None, max_bytes=MAX_IMAGE_BYTES,
                 socket_timeout=SOCKET_TIMEOUT, deadline=IMAGE_DEADLINE,
                 spacing=CDN_START_SPACING, reserve=DISK_RESERVE,
                 opener=None, clock=time.monotonic, disk_usage=shutil.disk_usage):
        if max_bytes <= 0 or socket_timeout <= 0 or deadline <= 0 or spacing < 0 or reserve < 0:
            raise ImageError("invalid_config")
        self.cancel_event = cancel_event or threading.Event()
        self.max_bytes, self.socket_timeout, self.deadline = max_bytes, socket_timeout, deadline
        self.spacing, self.reserve = spacing, reserve
        self.clock, self.disk_usage = clock, disk_usage
        self._last_start = None
        self.request_count = 0
        # No ambient proxy credentials, cookie processor, or authenticated client.
        self.opener = opener or urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            urllib.request.HTTPSHandler(context=ssl.create_default_context()), RejectRedirects())

    def check_cancelled(self):
        if self.cancel_event.is_set():
            raise Cancelled("image_cancelled")

    def _check(self, started):
        self.check_cancelled()
        if self.clock() - started >= self.deadline:
            raise ImageError("timeout")

    def download(self, url: str, *, allocate, prepared):
        """allocate(type)->safe Path checkpoints pending BEFORE temp creation.

        prepared(path, facts) persists size/hash while still pending BEFORE commit,
        making a final file left by a crash independently verifiable on resume.
        Both callbacks must be owned by an exclusively locked ManifestStore.
        """
        reason = None
        cancelled = False
        try:
            return self._download(url, allocate, prepared)
        except Cancelled:
            cancelled = True
        except ImageError as exc:
            reason = exc.reason
        except OSError:
            reason = "storage_error"
        if cancelled:
            raise Cancelled("image_cancelled")
        raise ImageError(reason)

    def _download(self, url, allocate, prepared):
        validate_image_url(url)
        self.check_cancelled()
        if self._last_start is not None:
            delay = max(0, self.spacing - (self.clock() - self._last_start))
            if self.cancel_event.wait(delay):
                raise Cancelled("image_cancelled")
        started = self.clock()
        self._last_start = started
        self.request_count += 1
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                    "Referer": REFERER, "Accept-Encoding": "identity"}, method="GET")
        response = _network_call(lambda: self.opener.open(request, timeout=min(self.socket_timeout, self.deadline)))
        temp = None
        reserved = None
        final = None
        try:
            with response:
                self._check(started)
                status = response.status
                if status != 200:
                    raise ImageError("unexpected_redirect" if 300 <= status < 400 else f"http_{status}")
                if response.geturl() != url:
                    raise ImageError("unexpected_redirect")
                mime = response.headers.get("Content-Type")
                mime = mime.split(";", 1)[0].strip().lower() if mime else None
                if mime is not None and mime not in MEDIA_EXTENSIONS:
                    raise ImageError("invalid_content_type")
                if response.headers.get("Content-Encoding", "identity").lower() not in ("", "identity"):
                    raise ImageError("invalid_content_encoding")
                length = response.headers.get("Content-Length")
                if length is not None and not re.fullmatch(r"[0-9]{1,20}", length.strip()):
                    raise ImageError("length_mismatch")
                length = int(length) if length is not None else None
                if length is not None and length > self.max_bytes:
                    raise ImageError("too_large")

                # Read only a small prefix before allocating the signature-derived
                # extension. read1 avoids a slow peer holding an entire chunk open.
                reader = getattr(response, "read1", response.read)
                prefix = b""
                while len(prefix) < 16:
                    self._check(started)
                    chunk = _network_call(lambda: reader(16 - len(prefix)))
                    self._check(started)
                    if not chunk:
                        break
                    prefix += chunk
                    if len(prefix) > self.max_bytes:
                        raise ImageError("too_large")
                media = detect_type(prefix)
                if media is None or (mime is not None and media != mime):
                    raise ImageError("signature_mismatch")
                final = Path(allocate(media))
                if self.disk_usage(final.parent).free < self.reserve + (length if length is not None else self.max_bytes):
                    raise ImageError("insufficient_disk_space")
                fd, name = tempfile.mkstemp(prefix=".image-", suffix=".tmp", dir=final.parent)
                temp = Path(name)
                digest = hashlib.sha256()
                size = 0
                with os.fdopen(fd, "wb") as handle:
                    chunk = prefix
                    while chunk:
                        self._check(started)
                        size += len(chunk)
                        if size > self.max_bytes:
                            raise ImageError("too_large")
                        if self.disk_usage(final.parent).free < self.reserve + len(chunk):
                            raise ImageError("insufficient_disk_space")
                        handle.write(chunk)
                        digest.update(chunk)
                        chunk = _network_call(lambda: reader(CHUNK_SIZE))
                        self._check(started)
                    if size == 0 or (length is not None and size != length):
                        raise ImageError("length_mismatch")
                    if media == "image/webp" and int.from_bytes(prefix[4:8], "little") + 8 != size:
                        raise ImageError("length_mismatch")
                    handle.flush()
                    os.fsync(handle.fileno())
                facts = FileFacts(media, size, digest.hexdigest(), mime, length)
                prepared(final, facts)
                self._check(started)
                # O_EXCL prevents overwriting a file that appeared after allocation.
                # This zero-byte reservation belongs to this worker, not to a caller.
                fd = os.open(final, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                try:
                    reserved = os.fstat(fd)
                finally:
                    os.close(fd)
                os.replace(temp, final)
                temp = None
                reserved = None
                return final, facts
        finally:
            if temp is not None:
                temp.unlink(missing_ok=True)
            if reserved is not None and final is not None:
                current = final.stat(follow_symlinks=False)
                if (current.st_dev, current.st_ino, current.st_size) == (reserved.st_dev, reserved.st_ino, 0):
                    final.unlink()
