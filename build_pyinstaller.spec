# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the LocalRouter release build.

Run with:  pyinstaller build_pyinstaller.spec --noconfirm

Cross-platform: the same spec works on Linux, macOS and Windows. litellm
loads its provider modules via importlib at runtime, so we pull the whole
package in with collect_all(); heavy optional ML deps (torch/tensorflow/...)
are not installed by backend/requirements.txt and are therefore skipped.

Two executables are produced on every platform, each with its own Analysis
(the standard PyInstaller multi-exe pattern; sharing one Analysis between
EXEs produced broken binaries):
  apirouter    console build, the server-mode entry (backend/launcher.py)
  LocalRouter  windowed desktop shell (backend/launcher_desktop.py); the
               frozen exe keeps all user state next to itself
"""
import os
import sys

from PyInstaller.utils.hooks import collect_all

block_cipher = None

# litellm is the routing kernel and imports providers lazily -> collect all.
litellm_datas, litellm_bins, litellm_hiddenimports = collect_all("litellm")

def _without_ucrt(entries):
    """Drop api-ms-* / ucrtbase UCRT forwarder stubs pulled in by dependency
    scanning (pythonnet/clr_loader). Bundled at the onefile root they break
    python312.dll's own dependency resolution; end-user Windows provides the
    real UCRT."""
    return [e for e in entries
            if not os.path.basename(e[0]).lower().startswith(("api-ms-", "ucrtbase"))]


litellm_bins = _without_ucrt(litellm_bins)

# The desktop shell opens a native webview window: WebView2 through
# pythonnet/clr on Windows, Cocoa through PyObjC on macOS (pywebview picks the
# backend per platform). Pull the packages in explicitly; each one is collected
# independently so a failing pythonnet (no usable runtime on macOS/Linux)
# must not drop the webview collection. Skipped entirely when pywebview is
# not installed (server-only build environments).
desktop_datas, desktop_bins, desktop_hidden = [], [], []
for _pkg in ("webview", "pythonnet", "clr_loader"):
    try:
        _d, _b, _h = collect_all(_pkg)
        desktop_datas += _d
        desktop_bins += _without_ucrt(_b)
        desktop_hidden += _h
    except Exception:
        pass
if sys.platform == "darwin":
    desktop_hidden += ["webview.platforms.cocoa"]
else:
    desktop_hidden += [
        "webview.platforms.winforms",
        "webview.platforms.edgechromium",
    ]

_icon = "build/app.ico" if os.path.exists("build/app.ico") else None

_common = dict(
    pathex=["backend"],
    binaries=_without_ucrt(list(litellm_bins)) + list(desktop_bins),
    # version.json is generated at release-build time (see build-release.yml) and
    # carries the git tag/commit so the frozen exe reports a real version.
    datas=list(litellm_datas) + list(desktop_datas)
    + ([("version.json", ".")] if os.path.exists("version.json") else []),
    hiddenimports=[
        "app",
        "app.routers",
        "app.services",
        "app.services.cline",
        "app.services.opencode",
        "app.services.workbuddy",
        "app.services.zcode",
        "app.services.zcode_system",
        # fastapi 在函数体内动态 import python_multipart（backup /restore 等
        # Form 接口），确保冻结包内一定可用
        "python_multipart",
        "multipart",
        # aiosqlite is imported by sqlalchemy's dialect registry through a
        # string at runtime, so static analysis never sees it.
        "aiosqlite",
    ] + list(litellm_hiddenimports) + list(desktop_hidden),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "torch",
        "tensorflow",
        "tensorflow-cpu",
        "jax",
        "mxnet",
    ],
    noarchive=False,
)

a_server = Analysis(["backend/launcher.py"], **_common)
a_desktop = Analysis(["backend/launcher_desktop.py"], **_common)

pyz_server = PYZ(a_server.pure, a_server.zipped_data, cipher=block_cipher)
pyz_desktop = PYZ(a_desktop.pure, a_desktop.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz_server,
    a_server.scripts,
    a_server.binaries,
    a_server.datas,
    [],
    name="apirouter",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

exe_desktop = EXE(
    pyz_desktop,
    a_desktop.scripts,
    a_desktop.binaries,
    a_desktop.datas,
    [],
    name="LocalRouter",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    icon=_icon,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
