from __future__ import annotations

import ctypes
import os
import tempfile
from ctypes import wintypes
from pathlib import Path
from typing import Callable

from .paths import COOKIE_FILE, CREDENTIAL_FILE


_MAGIC = b"WTA-CREDENTIAL\x00\x01"
_CRYPTPROTECT_UI_FORBIDDEN = 0x1


class CredentialError(RuntimeError):
    pass


class CredentialUnavailable(CredentialError):
    pass


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
    ]


def _input_blob(data: bytes) -> tuple[_DATA_BLOB, object]:
    buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    return _DATA_BLOB(len(data), buffer), buffer


def _windows_libraries():
    if os.name != "nt":
        raise CredentialError("Windows DPAPI 仅可在 Windows 上使用。")
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DATA_BLOB),
        wintypes.LPCWSTR,
        ctypes.POINTER(_DATA_BLOB),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_DATA_BLOB),
    ]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DATA_BLOB),
        ctypes.c_void_p,
        ctypes.POINTER(_DATA_BLOB),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_DATA_BLOB),
    ]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    return crypt32, kernel32


def _dpapi_protect(data: bytes) -> bytes:
    crypt32, kernel32 = _windows_libraries()
    input_blob, input_buffer = _input_blob(data)
    output_blob = _DATA_BLOB()
    if not crypt32.CryptProtectData(
        ctypes.byref(input_blob),
        "Weibo Text Archiver",
        None,
        None,
        None,
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(output_blob),
    ):
        error = ctypes.get_last_error()
        raise CredentialError(
            f"Windows 无法保护本机登录状态（错误 {error}）。"
        ) from None
    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        kernel32.LocalFree(output_blob.pbData)
        del input_buffer


def _dpapi_unprotect(data: bytes) -> bytes:
    crypt32, kernel32 = _windows_libraries()
    input_blob, input_buffer = _input_blob(data)
    output_blob = _DATA_BLOB()
    if not crypt32.CryptUnprotectData(
        ctypes.byref(input_blob),
        None,
        None,
        None,
        None,
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(output_blob),
    ):
        error = ctypes.get_last_error()
        raise CredentialError(
            f"Windows 无法读取本机登录状态（错误 {error}）。请重新扫码登录。"
        ) from None
    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        kernel32.LocalFree(output_blob.pbData)
        del input_buffer


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            fd = -1
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        temp_path.replace(path)
    finally:
        if fd >= 0:
            os.close(fd)
        temp_path.unlink(missing_ok=True)


def _validate_cookie_header(value: str) -> str:
    header = str(value or "").strip()
    if not header or any(character in header for character in "\r\n\x00"):
        raise CredentialError("保存的微博登录状态无效，请重新扫码登录。")
    pairs = {}
    for part in header.split(";"):
        if "=" not in part:
            continue
        key, item = part.split("=", 1)
        if key.strip() and item.strip():
            pairs[key.strip()] = item.strip()
    if not pairs.get("SUB"):
        raise CredentialError("保存的微博登录状态缺少 SUB，请重新扫码登录。")
    return header


def _serialize_cookies(cookies: dict[str, str]) -> str:
    if not isinstance(cookies, dict) or not str(cookies.get("SUB") or "").strip():
        raise CredentialError("拒绝保存缺少 SUB 的登录状态。")
    return _validate_cookie_header(
        "; ".join(
            f"{key}={value}"
            for key, value in cookies.items()
            if str(key).strip() and str(value).strip()
        )
    )


class CredentialStore:
    def __init__(
        self,
        *,
        protected_file: Path,
        legacy_file: Path,
        use_dpapi: bool,
        protect: Callable[[bytes], bytes] = _dpapi_protect,
        unprotect: Callable[[bytes], bytes] = _dpapi_unprotect,
        atomic_write: Callable[[Path, bytes], None] = _atomic_write,
    ):
        self.protected_file = Path(protected_file)
        self.legacy_file = Path(legacy_file)
        self.use_dpapi = bool(use_dpapi)
        self.protect = protect
        self.unprotect = unprotect
        self.atomic_write = atomic_write

    def _decode_payload(self, payload: bytes) -> str:
        if not payload.startswith(_MAGIC) or len(payload) <= len(_MAGIC):
            raise CredentialError("本机保存的登录状态已损坏，请重新扫码登录。")
        try:
            plaintext = self.unprotect(payload[len(_MAGIC) :])
            header = plaintext.decode("utf-8", errors="strict")
        except CredentialError:
            raise
        except Exception:
            raise CredentialError(
                "本机保存的登录状态无法读取，请重新扫码登录。"
            ) from None
        return _validate_cookie_header(header)

    def _read_protected(self) -> str:
        try:
            payload = self.protected_file.read_bytes()
        except OSError:
            raise CredentialUnavailable("未找到可用的微博登录状态。") from None
        return self._decode_payload(payload)

    def _read_legacy(self) -> str:
        try:
            header = self.legacy_file.read_text(encoding="utf-8", errors="strict")
        except OSError:
            raise CredentialUnavailable("未找到可用的微博登录状态。") from None
        except UnicodeError:
            raise CredentialError("旧版微博登录状态已损坏，请重新扫码登录。") from None
        return _validate_cookie_header(header)

    def _verified_protected_payload(self, header: str) -> bytes:
        plaintext = header.encode("utf-8")
        try:
            protected = self.protect(plaintext)
            verified = self.unprotect(protected)
        except CredentialError:
            raise
        except Exception:
            raise CredentialError("Windows 无法保护本机登录状态。") from None
        if verified != plaintext:
            raise CredentialError("Windows 登录状态保护验证失败。")
        return _MAGIC + protected

    def _write_protected(self, header: str) -> None:
        payload = self._verified_protected_payload(header)
        old_payload = None
        if self.protected_file.exists():
            try:
                old_payload = self.protected_file.read_bytes()
            except OSError:
                raise CredentialError(
                    "现有本机登录状态无法安全替换。"
                ) from None
        try:
            self.atomic_write(self.protected_file, payload)
            if self._read_protected() != header:
                raise CredentialError("Windows 登录状态写入验证失败。")
        except CredentialError:
            self._restore_previous(old_payload)
            raise
        except Exception:
            self._restore_previous(old_payload)
            raise CredentialError("本机登录状态保存失败。") from None

    def _restore_previous(self, old_payload: bytes | None) -> None:
        try:
            if old_payload is None:
                self.protected_file.unlink(missing_ok=True)
            else:
                self.atomic_write(self.protected_file, old_payload)
        except Exception:
            pass

    def _remove_legacy_after_verified_protected(self) -> None:
        try:
            self.legacy_file.unlink(missing_ok=True)
        except OSError:
            pass

    def _migrate_legacy(self) -> str:
        header = self._read_legacy()
        self._write_protected(header)
        if self._read_protected() != header:
            raise CredentialError("Windows 登录状态迁移验证失败。")
        self._remove_legacy_after_verified_protected()
        return header

    def has_saved_login(self) -> bool:
        try:
            self.load_cookie_header()
            return True
        except CredentialError:
            return False

    def load_cookie_header(self) -> str:
        if not self.use_dpapi:
            return self._read_legacy()

        if self.protected_file.exists():
            try:
                header = self._read_protected()
            except CredentialError:
                if self.legacy_file.exists():
                    return self._migrate_legacy()
                raise
            self._remove_legacy_after_verified_protected()
            return header

        if self.legacy_file.exists():
            return self._migrate_legacy()
        raise CredentialUnavailable(
            "未找到可用的微博登录状态，请重新扫码登录。"
        )

    def save_cookies(self, cookies: dict[str, str]) -> None:
        header = _serialize_cookies(cookies)
        if self.use_dpapi:
            self._write_protected(header)
            self._remove_legacy_after_verified_protected()
        else:
            try:
                self.atomic_write(self.legacy_file, header.encode("utf-8"))
            except Exception:
                raise CredentialError("本机登录状态保存失败。") from None

    def clear_saved_login(self) -> None:
        failed = False
        for path in (self.protected_file, self.legacy_file):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                failed = True
        if failed:
            raise CredentialError("部分本机登录状态无法删除。")


def _default_store() -> CredentialStore:
    return CredentialStore(
        protected_file=CREDENTIAL_FILE,
        legacy_file=COOKIE_FILE,
        use_dpapi=os.name == "nt",
    )


def has_saved_login() -> bool:
    return _default_store().has_saved_login()


def load_cookie_header() -> str:
    return _default_store().load_cookie_header()


def save_cookies(cookies: dict[str, str]) -> None:
    _default_store().save_cookies(cookies)


def clear_saved_login() -> None:
    _default_store().clear_saved_login()
