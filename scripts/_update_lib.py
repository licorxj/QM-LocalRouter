"""Shared helpers for the update scripts (stdlib only — no third-party deps,
so they run with any Python, inside or outside the backend venv).

Used by scripts/check_update.py and scripts/update.py. The in-app updater
(backend/app/services/app_update.py) mirrors this logic with httpx.
"""
from __future__ import annotations

import json
import os
import re
import ssl
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

DEFAULT_REPO = "licorxj/QM-LocalRouter"
GITHUB_API = "https://api.github.com"
CACHE_TTL = 300

SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS_DIR.parent

_cache: dict[str, tuple[float, dict]] = {}


def get_repo() -> str:
    return os.environ.get("LOCALROUTER_UPDATE_REPO", DEFAULT_REPO)


def get_mode(repo_root: Path | None = None) -> str:
    """release (frozen / no .git) vs git (source checkout)."""
    forced = os.environ.get("LOCALROUTER_UPDATE_MODE", "").strip().lower()
    if forced in ("release", "git"):
        return forced
    root = repo_root or REPO_ROOT
    if getattr(sys, "frozen", False):
        return "release"
    if (root / ".git").is_dir():
        return "git"
    return "release"


# ---------------------------------------------------------------------------
# versions
# ---------------------------------------------------------------------------

def parse_version(value: str | None) -> tuple[int, ...]:
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


def local_version(repo_root: Path | None = None) -> dict:
    """Local version from (1) version.json, (2) git describe, (3) unknown."""
    root = repo_root or REPO_ROOT
    meta = {}
    vj = root / "version.json"
    if vj.is_file():
        try:
            meta = json.loads(vj.read_text(encoding="utf-8"))
        except Exception:
            meta = {}
    if not meta:
        try:
            out = subprocess.run(
                ["git", "describe", "--tags", "--always", "--dirty"],
                cwd=str(root), capture_output=True, text=True, timeout=10,
            )
            if out.returncode == 0:
                desc = out.stdout.strip()
                dirty = desc.endswith("-dirty")
                tag = None
                commit = desc
                if "-g" in desc:
                    commit = desc.split("-g")[-1]
                    if desc.split("-")[0].lstrip("vV").replace(".", "").isdigit():
                        tag = desc.split("-")[0]
                meta = {"git_tag": tag, "git_commit": commit, "git_dirty": dirty}
        except Exception:
            meta = {}
    version = (meta.get("git_tag") or "").lstrip("vV") or None
    return {
        "version": version or "unknown",
        "git_tag": meta.get("git_tag"),
        "git_commit": meta.get("git_commit"),
        "git_dirty": bool(meta.get("git_dirty")),
    }


# ---------------------------------------------------------------------------
# GitHub releases
# ---------------------------------------------------------------------------

def _ssl_context() -> "ssl.SSLContext":
    """Prefer certifi's CA bundle (conda/system Pythons often ship an
    incomplete default CA chain), fall back to the default context."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def _opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=_ssl_context()))


def fetch_latest_release(repo: str | None = None, force: bool = False) -> dict:
    repo = repo or get_repo()
    cached = _cache.get(repo)
    if cached and not force and time.time() - cached[0] < CACHE_TTL:
        return cached[1]

    req = urllib.request.Request(
        f"{GITHUB_API}/repos/{repo}/releases",
        headers={"Accept": "application/vnd.github+json",
                 "User-Agent": "LocalRouter-update-script"},
    )
    with urllib.request.urlopen(req, timeout=20, context=_ssl_context()) as resp:
        releases = json.loads(resp.read().decode("utf-8"))

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
    _cache[repo] = (time.time(), data)
    return data


def platform_key() -> str:
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def install_kind(repo_root: Path | None = None) -> str:
    if getattr(sys, "frozen", False):
        return "portable" if not sys.platform.startswith("win") else "portable"
    return "source"


def pick_asset(release: dict, asset_name: str | None = None,
               repo_root: Path | None = None) -> dict | None:
    """Pick the release asset for this platform. Source checkouts on Windows
    prefer the installer; frozen/portable prefer the zip."""
    assets = release.get("assets", [])
    if asset_name:
        for a in assets:
            if a["name"] == asset_name:
                return a
        return None
    plat = platform_key()
    mode = get_mode(repo_root)
    wanted = None
    if plat == "windows":
        if mode == "git":
            wanted = r"LocalRouter-Setup-.*-x64\.exe$"
        else:
            wanted = r"apirouter-windows\.zip$"
    elif plat == "macos":
        wanted = r"LocalRouter-Setup-.*-macos\.pkg$"
    else:
        wanted = r"apirouter-linux\.zip$"
    for a in assets:
        if re.search(wanted, a["name"], re.IGNORECASE):
            return a
    return None


def download(url: str, dest: Path, progress=None) -> Path:
    """Stream-download `url` to `dest` with progress(done, total) callback."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "LocalRouter-update-script"})
    with _opener().open(req, timeout=60) as resp, open(dest, "wb") as fh:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = resp.read(256 * 1024)
            if not chunk:
                break
            fh.write(chunk)
            done += len(chunk)
            if progress:
                progress(done, total)
    return dest
