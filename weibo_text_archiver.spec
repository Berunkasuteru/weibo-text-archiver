# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path


ROOT = Path(SPECPATH)
version_namespace = {}
exec(
    (ROOT / "weibo_archive" / "__init__.py").read_text(encoding="utf-8"),
    version_namespace,
)
VERSION = version_namespace["__version__"]
BUNDLE_NAME = f"WeiboTextArchiver_{VERSION}_Windows"

a = Analysis(
    [str(ROOT / "WeiboTextArchiver.pyw")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[(str(ROOT / "assets" / "app_icon.png"), "assets")],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "PIL",
        "qrcode.image.pil",
        "qrcode.image.styledpil",
        "qrcode.image.styles.colormasks",
        "qrcode.image.styles.moduledrawers.pil",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="WeiboTextArchiver",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    icon=str(ROOT / "assets" / "app_icon.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name=BUNDLE_NAME,
)
