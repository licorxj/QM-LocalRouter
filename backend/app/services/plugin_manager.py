"""echobird-plugin 插件管理：索引拉取 / 安装更新 / 与上游同步。

移植 EchoBird src-tauri/src/services/plugin_manager/（mod.rs + packages.rs）：

- 索引：echobird-plugin 仓库根的 index.json（插件注册表），多镜像回退
  （GitCode 中国镜像 → CNB → 国际 raw + gh-proxy 兜底），TTL 缓存
- 分发：插件 zip 直接提交在仓库 packages/ 目录，下载 URL 以固定 commit
  前缀保证不可变；条目含 per-platform sha256 与版本（粒度 = 版本 × 平台）
- 安装：下载（20MiB 上限）→ SHA256 校验 → 安全解压（防 zip-slip/symlink）
  → staging → packages/{plugin_id}/{version}/ 原子落位 → installed.json 原子写
- 更新 = 重新安装最新版本并保留 enabled 状态（EchoBird 无 semver 比对，
  这里复用 app_update.parse_version 给出「可更新」徽章）

存储布局（DATA_DIR/plugins/）：
    installed.json                      # 已装状态（原子写）
    packages/{plugin_id}/{version}/     # 解压后的插件包（manifest + bin/...）
    staging/{token}/                    # 下载解压临时区
"""
import asyncio
import hashlib
import json
import os
import platform as _platform
import re
import shutil
import time
import uuid
import zipfile

import httpx

from app.config import DATA_DIR
from app.services.app_update import parse_version

INDEX_URLS = {
    # 与 EchoBird plugin_manager/mod.rs 常量对齐
    "international": "https://raw.githubusercontent.com/edison7009/echobird-plugin/main/index.json",
    "china": "https://api.gitcode.com/api/v5/repos/edison7009/echobird-plugin/raw/index.json",
    "cnb": "https://cnb.cool/echobird/echobird-plugin/-/git/raw/main/index.json",
}
# 国际源的网络兜底（本仓库更新器同款思路）：gh-proxy 反代 raw.githubusercontent
_INTERNATIONAL_MIRRORS = [INDEX_URLS["international"],
                          "https://gh-proxy.com/" + INDEX_URLS["international"]]
RAW_PREFIXES = {
    "international": "https://raw.githubusercontent.com/edison7009/echobird-plugin/",
    "china": "https://api.gitcode.com/api/v5/repos/edison7009/echobird-plugin/raw/",
    "cnb": "https://cnb.cool/echobird/echobird-plugin/-/git/raw/main/",
}
DOWNLOAD_UA = "LocalRouter-Plugin-Hub"
MAX_PACKAGE_BYTES = 20 * 1024 * 1024
MAX_ENTRIES = 2000
MAX_PATH_DEPTH = 32
INDEX_TTL = 600
SYNC_INTERVAL = 12 * 3600

PLUGINS_DIR = DATA_DIR / "plugins"
PACKAGES_DIR = PLUGINS_DIR / "packages"
STAGING_DIR = PLUGINS_DIR / "staging"
INSTALLED_PATH = PLUGINS_DIR / "installed.json"

_account_plugin_ids: set[str] = set()  # 索引里解析出的账号类插件 id

# ---------------------------------------------------------------------------
# 平台判定（index 条目版本后缀：windows.x64 / macos.arm64 / macos.x64 / linux.x64 / linux.arm64）

def current_platform() -> str:
    if os.name == "nt":
        return "windows.x64"
    system = _platform.system().lower()
    arch = (_platform.machine() or "").lower()
    is_arm = arch in ("arm64", "aarch64")
    if system == "darwin":
        return f"macos.{'arm64' if is_arm else 'x64'}"
    if system == "linux":
        return f"linux.{'arm64' if is_arm else 'x64'}"
    return "unknown"


def _version_platform(version: str) -> str:
    """'0.1.0-preview.5.windows.x64' -> 'windows.x64'（无后缀视为早期 windows 包）。"""
    parts = version.split(".")
    for i in range(len(parts) - 1):
        if parts[i] in ("windows", "macos", "linux"):
            return f"{parts[i]}.{parts[i + 1]}"
    return "windows.x64"


# ---------------------------------------------------------------------------
# 索引拉取

_index_cache: dict | None = None
_index_cache_at = 0.0
_last_sync_at = 0.0
_last_sync_error = ""


def _region_order() -> list[str]:
    from app.routers.settings import _get_settings
    region = (_get_settings().get("plugins_region") or "auto").strip().lower()
    if region in INDEX_URLS:
        return [region, *[r for r in ("china", "cnb", "international") if r != region]]
    return ["china", "cnb", "international"]


def _public_https(url: str) -> bool:
    return url.startswith("https://") and "://" in url


async def _fetch_url(url: str, *, timeout: float = 120.0, limit: int = 0) -> bytes:
    """流式下载并限制大小（EchoBird packages.rs 的下载语义）。"""
    headers = {"User-Agent": DOWNLOAD_UA, "Accept": "*/*"}
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=15.0),
                                 trust_env=False, follow_redirects=True) as client:
        async with client.stream("GET", url, headers=headers) as resp:
            resp.raise_for_status()
            chunks: list[bytes] = []
            total = 0
            async for chunk in resp.aiter_bytes():
                total += len(chunk)
                if limit and total > limit:
                    raise ValueError("Download exceeds limit")
                chunks.append(chunk)
            return b"".join(chunks)


async def fetch_index(force: bool = False) -> dict:
    """拉取插件索引；多镜像顺序回退。成功后记录同步时间。"""
    global _index_cache, _index_cache_at, _last_sync_at, _last_sync_error
    if not force and _index_cache and time.time() - _index_cache_at < INDEX_TTL:
        return _index_cache
    last_err: Exception | None = None
    for region in _region_order():
        urls = _INTERNATIONAL_MIRRORS if region == "international" else [INDEX_URLS[region]]
        for url in urls:
            try:
                raw = await _fetch_url(url, limit=8 * 1024 * 1024)
                data = json.loads(raw.decode("utf-8"))
                if not isinstance(data, dict) or not isinstance(data.get("plugins"), list):
                    raise ValueError("invalid index payload")
                _index_cache = data
                _index_cache_at = time.time()
                _last_sync_at = time.time()
                _last_sync_error = ""
                _remember_account_plugins(data)
                return data
            except Exception as e:
                last_err = e
    _last_sync_error = str(last_err)[:200] if last_err else "unknown error"
    raise last_err or RuntimeError("index fetch failed")


def _remember_account_plugins(data: dict) -> None:
    _account_plugin_ids.clear()
    for entry in data.get("plugins", []):
        release = latest_release(entry)
        manifest = ((release or {}).get("manifest") or {}).get("echobird") or {}
        contributes = manifest.get("contributes") or {}
        providers = contributes.get("providers") or []
        if manifest.get("worker", {}).get("runtime") == "native" and providers:
            _account_plugin_ids.add(entry.get("id", ""))


def account_plugin_ids() -> set[str]:
    return set(_account_plugin_ids)


def last_sync_info() -> dict:
    return {"checked_at": _last_sync_at, "error": _last_sync_error}


# ---------------------------------------------------------------------------
# 已装状态

def _load_installed() -> dict:
    try:
        with open(INSTALLED_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_installed(data: dict) -> None:
    PLUGINS_DIR.mkdir(parents=True, exist_ok=True)
    tmp = INSTALLED_PATH.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, INSTALLED_PATH)


def installed_plugins() -> dict:
    return _load_installed()


def get_installed(plugin_id: str) -> dict | None:
    return _load_installed().get(plugin_id)


def applications_of_installed(record: dict) -> list[str]:
    """已装插件覆盖的 application 列表（contributes.providers[].applications）。"""
    echobird = (record.get("manifest") or {}).get("echobird") or {}
    contributes = echobird.get("contributes") or {}
    providers = contributes.get("providers") or []
    apps: list[str] = []
    for p in providers:
        apps.extend(p.get("applications") or [])
    return sorted(set(apps))


def enabled_applications() -> set[str]:
    """所有已启用插件覆盖的 application 集合（账号插件才有 applications）。"""
    apps: set[str] = set()
    for _pid, record in _load_installed().items():
        if record.get("enabled"):
            apps.update(applications_of_installed(record))
    return apps


def engine_binary_candidates() -> list[str]:
    """所有已装账号插件包内的引擎二进制路径（同一构建，任一可用）。"""
    out: list[str] = []
    exe = "echobird-account-engine.exe" if os.name == "nt" else "echobird-account-engine"
    for record in _load_installed().values():
        root = record.get("path") or ""
        if root:
            out.append(os.path.join(root, "bin", exe))
    return out


def find_engine_binary(application: str) -> str | None:
    """优先取覆盖该 application 的插件包内的引擎，回退任一已装引擎。"""
    installed = _load_installed()
    for pid, record in installed.items():
        if record.get("enabled") and application in applications_of_installed(record):
            exe = engine_binary_for(record)
            if exe:
                return exe
    for record in installed.values():
        if record.get("enabled"):
            exe = engine_binary_for(record)
            if exe:
                return exe
    return None


def engine_binary_for(record: dict) -> str | None:
    root = record.get("path") or ""
    exe = "echobird-account-engine.exe" if os.name == "nt" else "echobird-account-engine"
    candidate = os.path.join(root, "bin", exe)
    return candidate if os.path.isfile(candidate) else None


# ---------------------------------------------------------------------------
# 索引条目解析

def _entry_releases(entry: dict) -> list[dict]:
    """按当前平台筛出可用 release（index 已按 semver 降序，取第一个匹配）。"""
    plat = current_platform()
    out = []
    for rel in entry.get("releases") or []:
        manifest = rel.get("manifest") or {}
        version = str(manifest.get("version") or "")
        if not version:
            continue
        archs = rel.get("architectures") or []
        version_plat = _version_platform(version)
        matches = version_plat == plat or plat.split(".")[1] in [a for a in archs if isinstance(a, str)]
        if matches:
            out.append(rel)
    return out


def latest_release(entry: dict) -> dict | None:
    releases = _entry_releases(entry)
    return releases[0] if releases else None


def release_download_urls(release: dict, region: str) -> list[str]:
    """某 release 的下载 URL 列表（按区域优先级回退，均用固定 commit 前缀）。"""
    urls = []
    downloads = release.get("downloads") or {}
    order = {"china": ["china", "cnb", "international"],
             "cnb": ["cnb", "china", "international"],
             "international": ["international", "china", "cnb"]}.get(region, ["china", "cnb", "international"])
    for key in order:
        url = downloads.get(key if key != "cnb" else "china")
        if not url:
            continue
        url = str(url)
        if key == "cnb" and url.startswith(RAW_PREFIXES["china"]):
            url = RAW_PREFIXES["cnb"] + url[len(RAW_PREFIXES["china"]):]
        if _public_https(url):
            urls.append(url)
    return urls


# ---------------------------------------------------------------------------
# 安装 / 更新 / 卸载

async def install_plugin(plugin_id: str, index: dict | None = None) -> dict:
    """安装或更新插件（下载→校验→解压→原子落位），返回已装记录。"""
    index = index or await fetch_index(force=True)
    entry = next((e for e in index.get("plugins", []) if e.get("id") == plugin_id), None)
    if not entry:
        raise ValueError(f"Plugin not found in index: {plugin_id}")
    release = latest_release(entry)
    if not release:
        raise ValueError(f"No release for platform {current_platform()}: {plugin_id}")
    manifest = release.get("manifest") or {}
    version = str(manifest.get("version") or "")
    sha256 = str(release.get("sha256") or "").lower()
    region = (_region_order() or ["china"])[0]

    from app.routers.settings import _get_settings
    pref = (_get_settings().get("plugins_region") or "auto").strip().lower()
    region = pref if pref in INDEX_URLS else region

    urls = release_download_urls(release, region)
    if not urls:
        raise ValueError("No download URL for this release")

    last_err: Exception | None = None
    payload = None
    for url in urls:
        try:
            payload = await _fetch_url(url, limit=MAX_PACKAGE_BYTES)
            break
        except Exception as e:
            last_err = e
    if payload is None:
        raise last_err or RuntimeError("download failed")

    digest = hashlib.sha256(payload).hexdigest()
    if sha256 and digest != sha256:
        raise ValueError("Plugin package SHA-256 mismatch")

    token = uuid.uuid4().hex[:12]
    staging = STAGING_DIR / token / "package"
    staging.mkdir(parents=True, exist_ok=True)
    zip_path = STAGING_DIR / token / "package.zip"
    zip_path.write_bytes(payload)
    try:
        _safe_extract(zip_path, staging)
        # zip 内若带顶层目录则下钻到 manifest 所在层
        root = _package_root(staging)
        if not (root / "manifest.json").exists() and not (root / "bin").exists():
            raise ValueError("Invalid plugin package layout")
        version_dir = PACKAGES_DIR / plugin_id / version
        if version_dir.exists():
            shutil.rmtree(version_dir, ignore_errors=True)
        version_dir.parent.mkdir(parents=True, exist_ok=True)
        os.replace(root, version_dir)  # 同盘 rename，原子
    finally:
        shutil.rmtree(STAGING_DIR / token, ignore_errors=True)

    installed = _load_installed()
    old = installed.get(plugin_id) or {}
    record = {
        "id": plugin_id,
        "name": manifest.get("name") or plugin_id,
        "display_name": manifest.get("displayName") or plugin_id,
        "version": version,
        "commit": release.get("commit") or "",
        "path": str(version_dir),
        "enabled": bool(old.get("enabled", True)),
        "installed_at": int(time.time()),
        "manifest": manifest,
    }
    installed[plugin_id] = record
    _save_installed(installed)

    # 热替换：受影响 application 的 worker 下次调用自动用新二进制
    from app.services import plugin_host
    for app in applications_of_installed(record):
        plugin_host.stop(app)
    return record


def _safe_extract(zip_path, dest_dir) -> None:
    """安全解压（EchoBird packages.rs 限制：条目数/深度/symlink/zip-slip）。"""
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        if len(names) > MAX_ENTRIES:
            raise ValueError("Too many entries in plugin package")
        for name in names:
            parts = name.replace("\\", "/").split("/")
            if len(parts) > MAX_PATH_DEPTH:
                raise ValueError("Plugin package path too deep")
            target = (dest_dir / name)
            resolved = os.path.realpath(target)
            if not str(resolved).startswith(str(os.path.realpath(dest_dir)) + os.sep) and resolved != os.path.realpath(dest_dir):
                raise ValueError("Plugin package escapes its destination")
        for info in zf.infolist():
            # symlink 判定：external_attr 高 16 位为 S_IFLNK(0o120000)
            if ((info.external_attr >> 16) & 0o170000) == 0o120000:
                raise ValueError("Symlinks are not allowed in plugin packages")
            if info.filename.endswith("/"):
                (dest_dir / info.filename).mkdir(parents=True, exist_ok=True)
                continue
            target = dest_dir / info.filename
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)


def _package_root(staging_dir):
    """zip 根下若只有单个目录则进入该目录。"""
    children = [p for p in staging_dir.iterdir()]
    if len(children) == 1 and children[0].is_dir():
        return children[0]
    return staging_dir


def uninstall_plugin(plugin_id: str) -> None:
    installed = _load_installed()
    record = installed.pop(plugin_id, None)
    if not record:
        raise ValueError(f"Plugin not installed: {plugin_id}")
    _save_installed(installed)
    for app in applications_of_installed(record):
        from app.services import plugin_host
        plugin_host.stop(app)
    shutil.rmtree(record.get("path") or "", ignore_errors=True)


def set_enabled(plugin_id: str, enabled: bool) -> dict:
    installed = _load_installed()
    record = installed.get(plugin_id)
    if not record:
        raise ValueError(f"Plugin not installed: {plugin_id}")
    record["enabled"] = bool(enabled)
    _save_installed(installed)
    from app.services import plugin_host
    if not enabled:
        for app in applications_of_installed(record):
            plugin_host.stop(app)
    return record


# ---------------------------------------------------------------------------
# 可用列表 / 更新检查

def available_plugins(index: dict) -> list[dict]:
    """索引中的账号类插件（含当前平台最新 release 摘要），按 id 排序。"""
    out = []
    for entry in index.get("plugins", []):
        release = latest_release(entry)
        if not release:
            continue
        rel_manifest = release.get("manifest") or {}
        echobird = rel_manifest.get("echobird") or {}
        providers = (echobird.get("contributes") or {}).get("providers") or []
        if echobird.get("worker", {}).get("runtime") != "native" or not providers:
            continue
        applications = sorted({a for p in providers for a in (p.get("applications") or [])})
        out.append({
            "id": entry.get("id"),
            "name": rel_manifest.get("name") or entry.get("id"),
            "display_name": rel_manifest.get("displayName") or entry.get("id"),
            "description": (entry.get("descriptions") or {}).get("china")
            or (entry.get("descriptions") or {}).get("international") or "",
            "applications": applications,
            "version": rel_manifest.get("version"),
            "operations": sorted({op for p in providers for op in (p.get("operations") or [])}),
            "commit": release.get("commit"),
        })
    out.sort(key=lambda p: p["id"] or "")
    return out


def check_updates(index: dict | None = None) -> list[dict]:
    """已装插件中版本落后于上游最新 release 的列表。"""
    installed = _load_installed()
    if index is None:
        if not _index_cache:
            return []
        index = _index_cache
    updates = []
    for entry in index.get("plugins", []):
        pid = entry.get("id")
        record = installed.get(pid)
        if not record:
            continue
        release = latest_release(entry)
        if not release:
            continue
        latest_version = str((release.get("manifest") or {}).get("version") or "")
        if latest_version and parse_version(latest_version) > parse_version(record.get("version")):
            updates.append({"id": pid, "installed_version": record.get("version"),
                            "latest_version": latest_version})
    return updates


# ---------------------------------------------------------------------------
# 启动同步（后台任务）

_sync_task: asyncio.Task | None = None


def startup_sync() -> None:
    """lifespan 启动时调用：静默拉索引 +（可选）自动更新；失败不影响启动。"""
    global _sync_task
    _sync_task = asyncio.get_running_loop().create_task(_sync_loop())


async def _sync_loop() -> None:
    while True:
        try:
            index = await fetch_index(force=True)
            if _auto_update_enabled():
                for upd in check_updates(index):
                    try:
                        await install_plugin(upd["id"], index)
                    except Exception:
                        pass
        except Exception:
            pass
        await asyncio.sleep(SYNC_INTERVAL)


def _auto_update_enabled() -> bool:
    from app.routers.settings import _get_settings
    return bool(_get_settings().get("plugins_auto_update", False))
