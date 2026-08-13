from __future__ import annotations

import http.cookiejar
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import qrcode

from .network import USER_AGENT, Cancelled


PASSPORT_BASE = "https://passport.weibo.com"
SSO_SIGNIN_URL = PASSPORT_BASE + "/sso/signin"
QR_IMAGE_URL = PASSPORT_BASE + "/sso/v2/qrcode/image"
QR_CHECK_URL = PASSPORT_BASE + "/sso/v2/qrcode/check"

QR_ENTRY = "miniblog"
QR_SOURCE = "miniblog"
QR_REDIRECT_URL = "https://weibo.com/"
QR_VERSION = "20250520"

RETCODE_SUCCESS = 20000000
RETCODE_NOT_SCANNED = 50114001
RETCODE_SCANNED = 50114002
RETCODE_EXPIRED = 50114004

PASSPORT_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": (
        "https://passport.weibo.com/sso/signin"
        "?entry=miniblog&source=miniblog&url=https://weibo.com/"
    ),
    "X-Requested-With": "XMLHttpRequest",
}


def _make_opener():
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    return opener, jar


def _request(opener, url, params=None, headers=None, timeout=30):
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    merged = dict(PASSPORT_HEADERS)
    if headers:
        merged.update(headers)
    return opener.open(
        urllib.request.Request(url, headers=merged, method="GET"),
        timeout=timeout,
    )


def _request_json(opener, url, params=None, headers=None, timeout=30):
    with _request(opener, url, params, headers, timeout) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    return json.loads(raw)


def _cookie_value(jar, name):
    for cookie in jar:
        if cookie.name == name:
            return cookie.value
    return ""


def _all_cookie_values(jar):
    return {
        cookie.name: cookie.value
        for cookie in jar
        if cookie.name and cookie.value
    }


def begin_qr_login():
    opener, jar = _make_opener()
    with _request(
        opener,
        SSO_SIGNIN_URL,
        {"entry": QR_ENTRY, "source": QR_SOURCE, "url": QR_REDIRECT_URL},
        timeout=30,
    ) as resp:
        resp.read(256)

    csrf = _cookie_value(jar, "X-CSRF-TOKEN")
    if not csrf:
        raise RuntimeError("微博登录页没有返回 CSRF Token。")

    csrf_headers = {"x-csrf-token": csrf}
    data = _request_json(
        opener,
        QR_IMAGE_URL,
        {"entry": QR_ENTRY, "size": "180"},
        headers=csrf_headers,
        timeout=30,
    )
    if data.get("retcode") != RETCODE_SUCCESS:
        raise RuntimeError(
            "获取二维码失败："
            + str(data.get("msg") or data.get("retcode") or "未知错误")
        )

    payload = data.get("data") or {}
    qrid = str(payload.get("qrid") or "")
    image_url = str(payload.get("image") or "")
    if not qrid:
        raise RuntimeError("微博返回的二维码数据缺少 qrid。")

    scan_url = ""
    if image_url:
        parsed = urllib.parse.urlparse(image_url)
        scan_url = (urllib.parse.parse_qs(parsed.query).get("data") or [""])[0]
    if not scan_url:
        scan_url = (
            "https://passport.weibo.cn/signin/qrcode/scan?"
            + urllib.parse.urlencode({"qr": qrid})
        )

    return opener, jar, qrid, scan_url, csrf_headers


def qr_matrix(scan_url: str):
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=1,
        border=4,
    )
    qr.add_data(scan_url)
    qr.make(fit=True)
    return qr.get_matrix()


def poll_qr_login(
    opener,
    jar,
    qrid: str,
    csrf_headers: dict,
    cancel_event: threading.Event,
    status_callback,
) -> dict[str, str]:
    deadline = time.time() + 240
    last_retcode = None

    while time.time() < deadline:
        if cancel_event.is_set():
            raise Cancelled("用户取消了扫码登录。")

        try:
            data = _request_json(
                opener,
                QR_CHECK_URL,
                {
                    "entry": QR_ENTRY,
                    "source": QR_SOURCE,
                    "url": QR_REDIRECT_URL,
                    "qrid": qrid,
                    "rid": "",
                    "ver": QR_VERSION,
                },
                headers=csrf_headers,
                timeout=15,
            )
        except (TimeoutError, urllib.error.URLError, json.JSONDecodeError):
            status_callback("网络波动，继续等待扫码结果…")
            if cancel_event.wait(2):
                raise Cancelled("用户取消了扫码登录。")
            continue

        retcode = data.get("retcode")
        msg = str(data.get("msg") or "")

        if retcode != last_retcode:
            last_retcode = retcode
            if retcode == RETCODE_NOT_SCANNED:
                status_callback("等待扫码…")
            elif retcode == RETCODE_SCANNED:
                status_callback("已扫码，请在手机微博中确认…")
            elif retcode == RETCODE_SUCCESS:
                status_callback("已确认，正在完成登录…")
            elif msg:
                status_callback(msg)

        if retcode == RETCODE_SUCCESS:
            payload = data.get("data") or {}
            cross_url = str(payload.get("url") or "")
            alt = str(payload.get("alt") or "")

            if cross_url:
                if cross_url.startswith("//"):
                    cross_url = "https:" + cross_url
                try:
                    with _request(opener, cross_url, headers={"User-Agent": USER_AGENT}) as resp:
                        resp.read(1024)
                except Exception:
                    pass

            if alt:
                alt_url = (
                    "https://login.sina.com.cn/sso/login.php?"
                    + urllib.parse.urlencode(
                        {"entry": "miniblog", "alt": alt, "returntype": "TEXT"}
                    )
                )
                try:
                    with _request(opener, alt_url, headers={"User-Agent": USER_AGENT}) as resp:
                        resp.read(1024)
                except Exception:
                    pass

            try:
                with _request(opener, "https://weibo.com/", headers={"User-Agent": USER_AGENT}) as resp:
                    resp.read(512)
            except Exception:
                pass

            cookies = _all_cookie_values(jar)
            if not cookies.get("SUB"):
                raise RuntimeError("手机端已确认，但电脑端没有取得 SUB Cookie。")
            return cookies

        if retcode == RETCODE_EXPIRED or "过期" in msg or "expired" in msg.lower():
            raise TimeoutError("二维码已过期，请重新获取。")

        if cancel_event.wait(2):
            raise Cancelled("用户取消了扫码登录。")

    raise TimeoutError("二维码等待超时，请重新获取。")


def save_cookies(cookie_file: Path, cookies: dict[str, str]):
    if not cookies.get("SUB"):
        raise RuntimeError("拒绝保存缺少 SUB 的登录 Cookie。")
    cookie_file.parent.mkdir(parents=True, exist_ok=True)
    cookie_file.write_text(
        "; ".join(f"{k}={v}" for k, v in cookies.items()),
        encoding="utf-8",
    )
