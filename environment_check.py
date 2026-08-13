#!/usr/bin/env python3
from __future__ import annotations

import importlib
import py_compile
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

checks = []
errors = []


def ok(name):
    checks.append(name)
    print(f"[OK] {name}")


def fail(name, exc):
    errors.append(f"{name}: {exc}")
    print(f"[FAIL] {name}: {exc}")


try:
    import tkinter
    ok(f"tkinter {tkinter.TkVersion}")
except Exception as exc:
    fail("tkinter", exc)

try:
    import qrcode
    ok("vendored qrcode")
except Exception as exc:
    fail("vendored qrcode", exc)

for module in (
    "weibo_archive.models",
    "weibo_archive.parser",
    "weibo_archive.network",
    "weibo_archive.auth",
    "weibo_archive.client",
    "weibo_archive.exporter",
    "weibo_archive.tasking",
    "weibo_archive.app",
):
    try:
        importlib.import_module(module)
        ok(f"import {module}")
    except Exception as exc:
        fail(f"import {module}", exc)

for asset in (ROOT / "assets/app_icon.png", ROOT / "assets/app_icon.ico"):
    if asset.is_file():
        ok(f"asset {asset.name}")
    else:
        fail(f"asset {asset.name}", "missing")

try:
    for path in (ROOT / "weibo_archive").glob("*.py"):
        py_compile.compile(str(path), doraise=True)
    ok("all Python modules compile")
except Exception as exc:
    fail("compile", exc)

try:
    result = subprocess.run(
        [sys.executable, "-c", "import weibo_archive.app; print('IMPORT_OK')"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
    )
    if result.returncode != 0 or "IMPORT_OK" not in result.stdout:
        raise RuntimeError((result.stdout or "") + "\n" + (result.stderr or ""))
    ok("GUI top-level startup smoke")
except Exception as exc:
    fail("GUI startup smoke", exc)

log = Path.home() / ".weibo_v7_environment_check.txt"
text = "RESULT: PASS\n" if not errors else "RESULT: FAIL\n" + "\n".join(errors) + "\n"
try:
    log.write_text(text, encoding="utf-8")
except Exception:
    pass

if errors:
    raise SystemExit(1)

print("\nRESULT: PASS")
