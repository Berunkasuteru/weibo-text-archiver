from __future__ import annotations

import argparse
import ctypes
import hashlib
import io
import shutil
import struct
import sys
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BUILD_DIR = ROOT / "build"
DIST_DIR = ROOT / "dist"
RELEASE_DIR = ROOT / "release"

sys.path.insert(0, str(ROOT))
from weibo_archive import __version__  # noqa: E402


BUNDLE_NAME = f"WeiboTextArchiver_{__version__}_Windows"
BUNDLE_DIR = DIST_DIR / BUNDLE_NAME
EXE_PATH = BUNDLE_DIR / "WeiboTextArchiver.exe"
ZIP_NAME = "WeiboTextArchiver_Windows.zip"
ZIP_PATH = RELEASE_DIR / ZIP_NAME
ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)


def _zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, ZIP_EPOCH)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = (0o40755 << 16 | 0x10) if name.endswith("/") else 0o100644 << 16
    return info


def _normalize_zip(data: bytes) -> bytes:
    """Rebuild nested runtime ZIPs without filesystem or extra-field timestamps."""
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(
        output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as target:
        for entry in sorted(source.infolist(), key=lambda item: item.filename):
            payload = source.read(entry)
            if zipfile.is_zipfile(io.BytesIO(payload)):
                payload = _normalize_zip(payload)
            target.writestr(_zip_info(entry.filename), payload)
    return output.getvalue()


def _verify_zip_metadata(data: bytes, label: str) -> None:
    """Check raw local headers, central headers and all nested ZIPs, not just contents."""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        if archive.testzip() is not None:
            raise RuntimeError(f"ZIP integrity check failed: {label}")
        central = archive.start_dir
        for entry in archive.infolist():
            local = entry.header_offset
            if data[local:local + 4] != b"PK\x03\x04" or data[central:central + 4] != b"PK\x01\x02":
                raise RuntimeError(f"invalid ZIP header: {label}/{entry.filename}")
            local_time = struct.unpack_from("<HH", data, local + 10)
            central_time = struct.unpack_from("<HH", data, central + 12)
            local_extra = struct.unpack_from("<H", data, local + 28)[0]
            name_size, extra_size, comment_size = struct.unpack_from("<HHH", data, central + 28)
            if (entry.date_time != ZIP_EPOCH or local_time != (0, 0x21)
                    or central_time != (0, 0x21) or entry.extra or local_extra or extra_size):
                raise RuntimeError(f"non-normalized ZIP timestamp or extra field: {label}/{entry.filename}")
            central += 46 + name_size + extra_size + comment_size
            payload = archive.read(entry)
            if zipfile.is_zipfile(io.BytesIO(payload)):
                _verify_zip_metadata(payload, f"{label}/{entry.filename}")


def _safe_remove(path: Path) -> None:
    resolved_root = ROOT.resolve()
    resolved = path.resolve()
    if resolved.parent != resolved_root:
        raise RuntimeError(f"refusing to remove unexpected path: {resolved}")
    if resolved.exists():
        shutil.rmtree(resolved)


def clean() -> None:
    for path in (BUILD_DIR, DIST_DIR, RELEASE_DIR):
        _safe_remove(path)


def _copy_release_docs() -> None:
    shutil.copy2(ROOT / "LICENSE", BUNDLE_DIR / "LICENSE")
    shutil.copy2(ROOT / "README.md", BUNDLE_DIR / "README.md")
    shutil.copy2(
        ROOT / "THIRD_PARTY_NOTICES.txt",
        BUNDLE_DIR / "THIRD_PARTY_NOTICES.txt",
    )
    shutil.copytree(
        ROOT / "THIRD_PARTY_LICENSES",
        BUNDLE_DIR / "THIRD_PARTY_LICENSES",
        dirs_exist_ok=True,
    )


def _audit_bundle() -> None:
    if not EXE_PATH.is_file():
        raise RuntimeError(f"missing packaged executable: {EXE_PATH}")
    forbidden_names = {
        ".git",
        ".claude",
        "__pycache__",
        "tests",
        "cookie.txt",
        "credential.dat",
        "last_error.txt",
        "last_success.json",
        "manifest.json",
        "archives",
        "pil",
        "pillow",
    }
    forbidden_suffixes = {".pyc", ".log", ".tmp"}
    violations = []
    for path in BUNDLE_DIR.rglob("*"):
        if path.name.lower() in forbidden_names or path.suffix.lower() in forbidden_suffixes:
            violations.append(path.relative_to(BUNDLE_DIR).as_posix())
    if violations:
        raise RuntimeError(f"forbidden bundle entries: {violations}")

    sensitive_markers = (
        b"C:\\Users\\",
        "C:\\Users\\".encode("utf-16le"),
        b"C:/Users/",
        "C:/Users/".encode("utf-16le"),
        b"@outlook.com",
        "@outlook.com".encode("utf-16le"),
        b"Administrator",
        "Administrator".encode("utf-16le"),
    )
    def audit_data(data, relative):
        if any(marker.lower() in data.lower() for marker in sensitive_markers):
            raise RuntimeError(f"local path or private identity marker in bundle: {relative}")
        if zipfile.is_zipfile(io.BytesIO(data)):
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                for entry in archive.infolist():
                    audit_data(archive.read(entry), f"{relative}/{entry.filename}")

    for path in BUNDLE_DIR.rglob("*"):
        if path.is_file():
            audit_data(path.read_bytes(), path.relative_to(BUNDLE_DIR).as_posix())


def _verify_windows_executable() -> None:
    data = EXE_PATH.read_bytes()
    if data[:2] != b"MZ":
        raise RuntimeError("packaged executable has no MZ header")
    pe_offset = struct.unpack_from("<I", data, 0x3C)[0]
    if data[pe_offset:pe_offset + 4] != b"PE\0\0":
        raise RuntimeError("packaged executable has no PE header")
    optional_header = pe_offset + 4 + 20
    subsystem = struct.unpack_from("<H", data, optional_header + 68)[0]
    if subsystem != 2:
        raise RuntimeError(f"expected Windows GUI subsystem 2, got {subsystem}")

    python_dlls = list((BUNDLE_DIR / "_internal").glob("python*.dll"))
    if not python_dlls:
        raise RuntimeError("bundled Python DLL is missing")

    if sys.platform == "win32":
        large_icon = ctypes.c_void_p()
        small_icon = ctypes.c_void_p()
        count = ctypes.windll.shell32.ExtractIconExW(
            str(EXE_PATH),
            0,
            ctypes.byref(large_icon),
            ctypes.byref(small_icon),
            1,
        )
        try:
            if count < 1 or not (large_icon.value or small_icon.value):
                raise RuntimeError("packaged executable has no extractable icon")
        finally:
            if large_icon.value:
                ctypes.windll.user32.DestroyIcon(large_icon)
            if small_icon.value:
                ctypes.windll.user32.DestroyIcon(small_icon)


def _verify_zip() -> None:
    _verify_zip_metadata(ZIP_PATH.read_bytes(), ZIP_NAME)
    with zipfile.ZipFile(ZIP_PATH, "r") as archive:
        if archive.testzip() is not None:
            raise RuntimeError("release ZIP integrity check failed")
        names = set(archive.namelist())
    required = {
        f"{BUNDLE_NAME}/WeiboTextArchiver.exe",
        f"{BUNDLE_NAME}/README.md",
        f"{BUNDLE_NAME}/LICENSE",
        f"{BUNDLE_NAME}/THIRD_PARTY_NOTICES.txt",
    }
    missing = required - names
    if missing:
        raise RuntimeError(f"release ZIP is missing files: {sorted(missing)}")


def package() -> tuple[Path, str]:
    if not BUNDLE_DIR.is_dir():
        raise RuntimeError(f"missing PyInstaller onedir output: {BUNDLE_DIR}")
    RELEASE_DIR.mkdir(parents=True, exist_ok=True)
    if any(RELEASE_DIR.iterdir()):
        raise RuntimeError("release directory is not empty")
    _copy_release_docs()
    for path in BUNDLE_DIR.rglob("*"):
        if path.is_file() and zipfile.is_zipfile(path):
            path.write_bytes(_normalize_zip(path.read_bytes()))
    _audit_bundle()
    _verify_windows_executable()

    with zipfile.ZipFile(
        ZIP_PATH,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        for path in sorted(BUNDLE_DIR.rglob("*")):
            if path.is_file():
                arcname = Path(BUNDLE_NAME) / path.relative_to(BUNDLE_DIR)
                archive.writestr(_zip_info(arcname.as_posix()), path.read_bytes())

    _verify_zip()

    digest = hashlib.sha256(ZIP_PATH.read_bytes()).hexdigest()
    (RELEASE_DIR / "SHA256.txt").write_text(
        f"{digest}  {ZIP_NAME}\n",
        encoding="utf-8",
    )
    return ZIP_PATH, digest


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare the Windows preview release.")
    parser.add_argument(
        "--clean",
        action="store_true",
        help="remove build, dist and release outputs",
    )
    args = parser.parse_args()
    if args.clean:
        clean()
        print("Cleaned build, dist and release outputs.")
        return 0
    zip_path, digest = package()
    print(f"ZIP: {zip_path}")
    print(f"SHA256: {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
