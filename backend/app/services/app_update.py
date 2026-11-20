"""GitHub-Releases based self-update for LocalRouter.

Modes (auto-detected, override with ``LOCALROUTER_UPDATE_MODE``):

* ``release`` — frozen installs (PyInstaller). Compares the bundled version
  against ``<repo>/releases/latest`` on GitHub, downloads the matching asset
  and applies it:
    * Windows installer build  → runs the new ``LocalRouter-Setup-*.exe``
      silently (Inno keeps ``{app}\\data`` and restarts the app);
    * portable zip builds      → extracts next to the exe into
      ``update_pending/`` and spawns a detached helper script that swaps
      files once this process exits (running executables cannot be
      overwritten on Windows, so the swap happens post-exit);
    * macOS                    → downloads the ``.pkg`` and opens Installer.
* ``git`` — source checkouts: delegates to the git flow in
  ``routers.settings`` (``git pull`` + optional frontend build).

Env knobs:
  ``LOCALROUTER_UPDATE_REPO``  override ``owner/name`` (testing)
  ``LOCALROUTER_UPDATE_MODE``  force ``release`` | ``git``
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from app.config import BASE_DIR, DATA_DIR
from app.version import get_version_info

DEFAULT_REPO = "licorxj/QM-LocalRouter"
CACHE_TTL = 300  # seconds
DOWNLOAD_DIR = DATA_DIR / "updates"
PROTECTED = {"data", ".env"}  # never overwrite user data on portable swaps

_WINDOWS_INSTALLER_FLAGS = [
    "/VERYSILENT",
    "/SUPPRESSMSGBOXES",
    "/CLOSEAPPLICATIONS",
    "/FORCECLOSEAPPLICATIONS",
    "/RESTARTAPPLICATIONS",
]

# ---------------------------------------------------------------------------
# state shared with the /api/update/status endpoint
# ---------------------------------------------------------------------------

@dataclass
class UpdateState:
    phase: str = "idle"  # idle|checking|downloading|applying|installer_launched|exiting|error
    progress: int = 0
    downloaded: int = 0
    total: int = 0
    message: str = ""
    error: str | None = None
    asset_name: str | None = None
    result: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "progress": self.progress,
            "downloaded": self.downloaded,
            "total": self.total,
            "message": self.message,
            "error": self.error,
            "asset_name": self.asset_name,
            "result": self.result,
        }


_state = UpdateState()
_download_task: asyncio.Task | None = None

# ---------------------------------------------------------------------------
# helpers: version parsing / platform / mode
# ---------------------------------------------------------------------------

def parse_version(value: str | None) -> tuple[int, ...]:
    """'v3.0.0' / '3.0.0-1-gabc' / '3.0' -> (3, 0, 0)."""
    if not value:
        return (0,)
    text = value.strip().lstrip("vV").split("-")[0]
    parts = []
    for chunk in text.split("."):
        digits = ""
        for ch in chunk.strip():
            if ch.isdigit():
                digits += ch
            else:
                break
        parts.append(int(digits or 0))
    return tuple(parts) or (0,)


def is_newer(latest: str | None, current: str | None) -> bool:
    return parse_version(latest) > parse_version(current)


def get_repo() -> str:
    return os.environ.get("LOCALROUTER_UPDATE_REPO", DEFAULT_REPO)


def get_mode() -> str:
    forced = os.environ.get("LOCALROUTER_UPDATE_MODE", "").strip().lower()
    if forced in ("release", "git"):
        return forced
    if getattr(sys, "frozen", False):
        return "release"
    if (BASE_DIR.parent / ".git").is_dir():
        return "git"
    return "release"


def platform_key() -> str:
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def _is_windows() -> bool:
    return sys.platform.startswith("win")


def install_kind() -> str:
    """installer (Inno per-user dir) vs portable zip layout. Windows only care."""
    if not getattr(sys, "frozen", False):
        return "source"
    if not _is_windows():
        return "portable"
    exe_dir = Path(sys.executable).resolve().parent
    local_app = os.environ.get("LOCALAPPDATA")
    if local_app:
        inno_dir = (Path(local_app) / "Programs" / "LocalRouter").resolve()
        if exe_dir == inno_dir:
            return "installer"
    return "portable"


# ---------------------------------------------------------------------------
# GitHub client
# ---------------------------------------------------------------------------

_release_cache: dict[str, Any] = {"at": 0.0, "data": None}


async def fetch_latest_release(force: bool = False) -> dict[str, Any]:
    """Return the latest GitHub release (drafts excluded; falls back to the
    newest prerelease when no stable release exists)."""
    import time as now_mod

    global _release_cache
    if not force and _release_cache["data"] and now_mod.time() - _release_cache["at"] < CACHE_TTL:
        return _release_cache["data"]

    repo = get_repo()
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        resp = await client.get(
            f"https://api.github.com/repos/{repo}/releases",
            headers={"Accept": "application/vnd.github+json"},
        )
        resp.raise_for_status()
        releases = resp.json()

    stable = next((r for r in releases if not r.get("draft") and not r.get("prerelease")), None)
    fallback = next((r for r in releases if not r.get("draft")), None)
    release = stable or fallback
    if not release:
        raise RuntimeError(f"No releases published on {repo}")

    data = {
        "tag": release.get("tag_name") or "",
        "version": (release.get("tag_name") or "").lstrip("vV"),
        "url": release.get("html_url") or f"https://github.com/{repo}/releases/latest",
        "notes": release.get("body") or "",
        "published_at": release.get("published_at") or "",
        "prerelease": bool(release.get("prerelease")),
        "assets": [
            {"name": a["name"], "url": a["browser_download_url"], "size": a.get("size", 0)}
            for a in release.get("assets", [])
        ],
    }
    _release_cache = {"at": now_mod.time(), "data": data}
    return data


def pick_asset(release: dict[str, Any], asset_name: str | None = None) -> dict[str, Any] | None:
    """Select the release asset matching this platform / install kind."""
    assets = release.get("assets", [])
    if asset_name:
        for a in assets:
            if a["name"] == asset_name:
                return a
        return None

    plat = platform_key()
    wanted: str | None = None
    if plat == "windows":
        wanted = ("LocalRouter-Setup-.*-x64\\.exe$"
                  if install_kind() == "installer" else "apirouter-windows\\.zip$")
    elif plat == "macos":
        wanted = r"LocalRouter-Setup-.*-macos\.pkg$"
    else:
        wanted = r"apirouter-linux\.zip$"

    import re as _re
    for a in assets:
        if _re.search(wanted, a["name"], _re.IGNORECASE):
            return a
    return None


def current_version() -> str:
    return get_version_info().get("version") or ""


# ---------------------------------------------------------------------------
# download + apply
# ---------------------------------------------------------------------------

def _reset_state(asset_name: str | None = None) -> None:
    global _state
    _state = UpdateState(phase="checking", asset_name=asset_name)


async def _download(asset: dict[str, Any]) -> Path:
    import time as now_mod

    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    target = DOWNLOAD_DIR / asset["name"]
    _state.phase = "downloading"
    _state.total = asset.get("size", 0)
    _state.downloaded = 0
    _state.progress = 0
    _state.message = f"正在下载 {asset['name']}"

    async with httpx.AsyncClient(timeout=None, follow_redirects=True) as client:
        async with client.stream("GET", asset["url"]) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("content-length") or asset.get("size") or 0)
            _state.total = total
            done = 0
            with open(target, "wb") as fh:
                last_tick = 0.0
                async for chunk in resp.aiter_bytes(256 * 1024):
                    fh.write(chunk)
                    done += len(chunk)
                    _state.downloaded = done
                    if total:
                        _state.progress = min(100, int(done * 100 / total))
                    now = now_mod.time()
                    if now - last_tick > 0.5:
                        last_tick = now
                        _state.message = f"已下载 {done // 1024} / {total // 1024} KB"
            if total:
                _state.progress = 100
    return target


def _spawn_detached(args: list[str], cwd: Path | None = None,
                    no_window: bool = False) -> None:
    if _is_windows():
        if no_window:
            # .bat / 控制台辅助脚本：隐藏窗口，避免运行时闪出黑色 cmd 窗口
            flags = 0x08000000 | 0x00000200  # CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
        else:
            # GUI 安装器保留界面（Inno Setup 需要显示进度），但仍脱离当前控制台
            flags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        subprocess.Popen(args, cwd=str(cwd) if cwd else None,
                         creationflags=flags, close_fds=True)
    else:
        subprocess.Popen(args, cwd=str(cwd) if cwd else None,
                         start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _exit_soon(delay: float = 2.0) -> None:
    """Let the HTTP response flush, then exit so the helper/installer can
    replace files. The detached helper or Inno restarts the app."""

    def _go() -> None:
        import time
        time.sleep(delay)
        os._exit(0)

    import threading
    threading.Thread(target=_go, daemon=True).start()


def _apply_windows_installer(installer: Path) -> dict[str, Any]:
    _spawn_detached([str(installer), *_WINDOWS_INSTALLER_FLAGS], cwd=installer.parent)
    _state.phase = "installer_launched"
    _state.message = "安装器已启动，正在静默安装并将自动重启应用"
    _exit_soon(delay=3.0)
    return {"phase": _state.phase, "installer": str(installer)}


def _apply_macos_pkg(pkg: Path) -> dict[str, Any]:
    _spawn_detached(["open", str(pkg)])
    _state.phase = "installer_launched"
    _state.message = "已打开安装器，请在弹出的窗口中完成安装（保留个人数据）"
    return {"phase": _state.phase, "installer": str(pkg)}


def _write_portable_helpers(app_dir: Path, pending: Path, pid: int) -> tuple[Path, Path]:
    """Write the detached post-exit swap scripts (Windows + POSIX variants;
    only the matching one gets executed)."""
    exe_name = Path(sys.executable).name
    app_s = str(app_dir)
    pending_s = str(pending)

    bat = app_dir / "apply_update.bat"
    bat.write_text(
        "@echo off\r\n"
        "rem LocalRouter portable update helper (auto-generated)\r\n"
        f"set \"PID={pid}\"\r\n"
        ":waitloop\r\n"
        "tasklist /FI \"PID eq %PID%\" 2>NUL | find /I \"%PID%\" >NUL\r\n"
        "if not errorlevel 1 (\r\n"
        "  timeout /t 1 /nobreak >NUL\r\n"
        "  goto waitloop\r\n"
        ")\r\n"
        f"if exist \"{app_s}\\{exe_name}\" move /y \"{app_s}\\{exe_name}\" \"{app_s}\\{exe_name}.old\" >NUL\r\n"
        f"xcopy /E /I /Y \"{pending_s}\\*\" \"{app_s}\\\" >NUL\r\n"
        f"rd /s /q \"{pending_s}\"\r\n"
        f"start \"\" \"{app_s}\\{exe_name}\"\r\n"
        "del \"%~f0\"\r\n",
        encoding="gbk", errors="replace",
    )

    sh = app_dir / "apply_update.sh"
    sh.write_text(
        "#!/bin/sh\n"
        "# LocalRouter portable update helper (auto-generated)\n"
        f"PID={pid}\n"
        "while kill -0 \"$PID\" 2>/dev/null; do sleep 1; done\n"
        f"cd \"{app_dir}\" || exit 1\n"
        f"[ -f \"{exe_name}\" ] && mv -f \"{exe_name}\" \"{exe_name}.old\" 2>/dev/null\n"
        f"cp -rf \"{pending}/.\" \"{app_dir}/\"\n"
        f"rm -rf \"{pending}\"\n"
        f"chmod +x \"{exe_name}\" 2>/dev/null\n"
        f"nohup \"./{exe_name}\" >/dev/null 2>&1 &\n"
        "rm -f \"$0\"\n",
        encoding="utf-8",
    )
    try:
        sh.chmod(0o755)
    except Exception:
        pass
    return bat, sh


def _apply_portable_zip(zip_path: Path) -> dict[str, Any]:
    if not getattr(sys, "frozen", False):
        raise RuntimeError("Portable zip swap requires a frozen build")
    app_dir = Path(sys.executable).resolve().parent
    pending = app_dir / "update_pending"
    if pending.exists():
        import shutil
        shutil.rmtree(pending, ignore_errors=True)
    pending.mkdir(parents=True)

    with zipfile.ZipFile(zip_path) as zf:
        for member in zf.infolist():
            if member.is_dir():
                continue
            rel = Path(member.filename)
            # Skip anything that would clobber user data or local config.
            if rel.parts and rel.parts[0].lower() in PROTECTED:
                continue
            if any(p.lower() in PROTECTED for p in rel.parts):
                continue
            zf.extract(member, pending)

    _state.phase = "applying"
    _state.message = "已解压更新文件，等待应用退出后自动换装"
    pid = os.getpid()
    bat, sh = _write_portable_helpers(app_dir, pending, pid)
    helper = bat if _is_windows() else sh
    _spawn_detached([str(helper)], cwd=app_dir, no_window=True)
    _state.phase = "exiting"
    _state.message = "应用即将退出并自动完成换装重启"
    _exit_soon(delay=2.0)
    return {"phase": _state.phase, "helper": str(helper)}


def _plan(asset: dict[str, Any]) -> dict[str, Any]:
    """Human-readable plan used by dry-run."""
    kind = install_kind()
    plat = platform_key()
    if plat == "windows" and asset["name"].lower().endswith(".exe"):
        command = [asset["name"], *_WINDOWS_INSTALLER_FLAGS]
        strategy = "windows-installer"
    elif plat == "macos":
        command = ["open", asset["name"]]
        strategy = "macos-pkg"
    else:
        command = ["extract-zip", "apply_update.helper", "restart"]
        strategy = "portable-swap"
    return {
        "strategy": strategy,
        "install_kind": kind,
        "platform": plat,
        "asset": asset,
        "download_dir": str(DOWNLOAD_DIR),
        "command": command,
    }


async def start_apply(asset_name: str | None = None, dry_run: bool = False) -> dict[str, Any]:
    """Entry point used by the router. Returns a plan (dry-run) or starts the
    background download+apply pipeline."""
    global _download_task
    release = await fetch_latest_release()
    asset = pick_asset(release, asset_name)
    if not asset:
        raise LookupError(
            f"No matching asset in release {release.get('tag')} for "
            f"{platform_key()}/{install_kind()}. Available: "
            + ", ".join(a["name"] for a in release.get("assets", []))
        )

    if dry_run:
        return {"dry_run": True, "current_version": current_version(),
                "latest_version": release.get("version"), **_plan(asset)}

    if _state.phase in ("checking", "downloading", "applying"):
        raise RuntimeError("An update is already in progress")

    _reset_state(asset["name"])
    _state.phase = "downloading"

    async def _pipeline() -> None:
        try:
            zip_or_exe = await _download(asset)
            name = asset["name"].lower()
            if _is_windows() and name.endswith(".exe"):
                result = _apply_windows_installer(zip_or_exe)
            elif platform_key() == "macos" and name.endswith(".pkg"):
                result = _apply_macos_pkg(zip_or_exe)
            else:
                result = _apply_portable_zip(zip_or_exe)
            _state.result = result
            if _state.phase not in ("installer_launched", "exiting"):
                _state.phase = "ready"
        except Exception as exc:  # noqa: BLE001 - surface any failure to the UI
            _state.phase = "error"
            _state.error = str(exc)
            _state.message = f"更新失败: {exc}"

    _download_task = asyncio.create_task(_pipeline())
    return {"started": True, "asset": asset, "mode": "release"}
