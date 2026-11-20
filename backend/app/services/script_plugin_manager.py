"""脚本插件管理：在线目录 / 安装 / 执行 / 卸载 / 审计。

与现有 echobird 二进制插件系统（DATA_DIR/plugins，账号 worker）完全独立。
本子系统面向「用户主动选择下载、主动点击执行」的本地脚本插件：

存储布局（DATA_DIR/script_plugins/）：
    installed.json                       已装记录（原子写）
    packages/<plugin_id>/<version>/      解压后的插件脚本包（manifest.json + 入口 .py）
    staging/<token>/                     下载解压临时区
    audit.jsonl                          执行/鉴权审计日志（按大小滚动）

插件包约定：zip 内含 manifest.json 与一个 Python 入口脚本；manifest 示例：
    {
      "id": "hello", "name": "示例插件", "description": "...", "version": "1.0.0",
      "entry": "main.py",                        # 相对包根目录
      "function": "run",                         # 入口函数名，默认 run
      "permissions": ["database.read"]
    }
入口函数签名：``def run(ctx) -> Any``（ctx 为 script_plugin_context.PluginContext）。
"""
from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import os
import re
import shutil
import sys
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any

import httpx

from app.config import DATA_DIR
from app.services import script_plugin_permissions as perm
from app.services import script_plugin_settings as settings

ROOT = DATA_DIR / "script_plugins"
PACKAGES_DIR = ROOT / "packages"
STAGING_DIR = ROOT / "staging"
INSTALLED_PATH = ROOT / "installed.json"
AUDIT_PATH = ROOT / "audit.jsonl"

# 在线插件目录 JSON（客户端日常请求此 URL 即可获得名称/描述/下载地址）
CATALOG_URL = (
    "https://qmzf-release-1323480989.cos.ap-guangzhou.myqcloud.com/"
    "ApiRouteManeger/plugins/index.json"
)
CATALOG_TTL = 600
MAX_PACKAGE_BYTES = 40 * 1024 * 1024  # 服务型插件需携带编译二进制（单平台约 14MB）
MAX_ENTRIES = 1000
MAX_PATH_DEPTH = 32
RUN_TIMEOUT = 120.0
AUDIT_MAX_BYTES = 2 * 1024 * 1024
DOWNLOAD_UA = "ApiRouteManeger-PluginHub/1.0"

_ID_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")

_catalog_cache: dict | None = None
_catalog_cache_at = 0.0
_catalog_error = ""


# ---------------------------------------------------------------------------
# 工具

def _ensure_dirs() -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    PACKAGES_DIR.mkdir(parents=True, exist_ok=True)
    STAGING_DIR.mkdir(parents=True, exist_ok=True)


def _atomic_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _audit(rec: dict) -> None:
    """审计日志落盘（超阈值时归档为 .1）。"""
    try:
        _ensure_dirs()
        line = json.dumps(rec, ensure_ascii=False, default=str) + "\n"
        if AUDIT_PATH.exists() and AUDIT_PATH.stat().st_size > AUDIT_MAX_BYTES:
            os.replace(AUDIT_PATH, AUDIT_PATH.with_suffix(".jsonl.1"))
        with open(AUDIT_PATH, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


def read_audit(limit: int = 100) -> list[dict]:
    if not AUDIT_PATH.exists():
        return []
    lines = AUDIT_PATH.read_text(encoding="utf-8", errors="replace").splitlines()
    out: list[dict] = []
    for line in reversed(lines[-limit:]):
        try:
            out.append(json.loads(line))
        except Exception:
            pass
    return out


# ---------------------------------------------------------------------------
# 已装记录

def _load_installed() -> dict:
    try:
        with open(INSTALLED_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_installed(data: dict) -> None:
    _atomic_json(INSTALLED_PATH, data)


def get_installed(plugin_id: str) -> dict | None:
    return _load_installed().get(plugin_id)


def installed_plugins() -> list[dict]:
    out = []
    for pid, rec in _load_installed().items():
        out.append(_public_record(pid, rec))
    out.sort(key=lambda x: x["id"])
    return out


def _public_record(pid: str, rec: dict) -> dict:
    return {
        "id": pid,
        "name": rec.get("name") or pid,
        "description": rec.get("description") or "",
        "version": rec.get("version") or "",
        "entry": rec.get("entry") or "",
        "permissions": rec.get("permissions") or [],
        "enabled": bool(rec.get("enabled", True)),
        "installed_at": rec.get("installed_at"),
        "path": rec.get("path") or "",
        "has_settings": bool(rec.get("settings")),
        "type": rec.get("type") or "script",       # script | apiserver
        "service": rec.get("service") or {},       # apiserver: {binary, config, platform}
        "platform": rec.get("platform") or "",
    }


def set_enabled(plugin_id: str, enabled: bool) -> dict:
    data = _load_installed()
    rec = data.get(plugin_id)
    if not rec:
        raise ValueError(f"插件未安装: {plugin_id}")
    rec["enabled"] = bool(enabled)
    _save_installed(data)
    return _public_record(plugin_id, rec)


# ---------------------------------------------------------------------------
# 插件设置项

def get_settings(plugin_id: str) -> dict:
    """返回插件的设置 schema 与当前值；未声明设置的插件返回空 schema。"""
    rec = get_installed(plugin_id)
    if not rec:
        raise ValueError(f"插件未安装: {plugin_id}")
    schema = rec.get("settings") or []
    values = settings.merge_values(schema, rec.get("settings_values"))
    return {
        "plugin_id": plugin_id,
        "schema": schema,
        "values": values,
    }


def save_settings(plugin_id: str, submitted: dict | None) -> dict:
    """校验并保存设置值（按 schema 归一化），返回最新 {schema, values}。"""
    data = _load_installed()
    rec = data.get(plugin_id)
    if not rec:
        raise ValueError(f"插件未安装: {plugin_id}")
    schema = rec.get("settings") or []
    values = settings.validate_values(schema, submitted)
    rec["settings_values"] = values
    _save_installed(data)
    _audit({"ts": time.time(), "plugin": plugin_id, "action": "settings.save",
            "keys": sorted(values.keys())})
    return {"plugin_id": plugin_id, "schema": schema, "values": values}


# ---------------------------------------------------------------------------
# 在线目录

async def fetch_catalog(force: bool = False) -> list[dict]:
    """拉取在线插件目录（COS 上的 index.json），带 TTL 缓存。返回 entries 列表。"""
    global _catalog_cache, _catalog_cache_at, _catalog_error
    if not force and _catalog_cache is not None and time.time() - _catalog_cache_at < CATALOG_TTL:
        return _catalog_cache["plugins"]
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=10.0),
                                     trust_env=False, follow_redirects=True) as client:
            resp = await client.get(CATALOG_URL, headers={"User-Agent": DOWNLOAD_UA})
            resp.raise_for_status()
            data = resp.json()
        entries = data.get("plugins") if isinstance(data, dict) else None
        if not isinstance(entries, list):
            raise ValueError("在线目录格式无效（缺少 plugins 数组）")
        _catalog_cache = data
        _catalog_cache_at = time.time()
        _catalog_error = ""
        return entries
    except Exception as e:
        _catalog_error = str(e)[:200]
        raise


def catalog_info() -> dict:
    return {"checked_at": _catalog_cache_at, "error": _catalog_error,
            "has_cache": _catalog_cache is not None, "url": CATALOG_URL}


def _filter_entries_for_platform(entries: list[dict]) -> list[dict]:
    """apiserver 条目按当前系统平台收敛：同 id 仅保留匹配平台的一条。

    目录里同一 apiserver id 会发布 6 个平台包（windows.x64/macos.arm64/...），
    客户端只需看到并安装与自身系统匹配的那个。无 platform 字段的普通条目原样保留。
    """
    from app.services import apiserver_manager
    plat = apiserver_manager._platform()
    api_ids = {str(e.get("id") or "") for e in entries if e.get("type") == "apiserver"}
    seen: set[str] = set()
    out = []
    for e in entries:
        if e.get("type") != "apiserver":
            out.append(e)
            continue
        pid = str(e.get("id") or "")
        eplat = str(e.get("platform") or "")
        if eplat and eplat != plat:
            continue  # 非当前平台，跳过
        if pid in seen:
            continue  # 同平台重复，取第一条
        seen.add(pid)
        out.append(e)
    return out


def online_plugins(entries: list[dict]) -> list[dict]:
    """把在线目录条目整理为前端展示结构，并标注安装/更新状态。

    apiserver 型插件在目录里按平台发布多个条目（同 id 不同 platform），
    这里按当前系统收敛为每个 id 仅保留匹配平台的一条，避免在线页出现 6 个
    同名插件、以及安装到非当前平台二进制。
    """
    entries = _filter_entries_for_platform(entries)
    installed = _load_installed()
    out = []
    for e in entries:
        if not isinstance(e, dict) or not e.get("id"):
            continue
        pid = str(e["id"])
        if not _ID_RE.match(pid):
            continue
        rec = installed.get(pid)
        out.append({
            "id": pid,
            "name": e.get("name") or pid,
            "description": e.get("description") or "",
            "version": str(e.get("version") or ""),
            "download_url": e.get("download_url") or "",
            "sha256": str(e.get("sha256") or "").lower(),
            "permissions": perm.normalize_permissions(e.get("permissions") or []),
            "installed_version": rec.get("version") if rec else None,
            "enabled": bool(rec.get("enabled", True)) if rec else False,
            "type": e.get("type") or "script",
            "platform": str(e.get("platform") or ""),
            "service": e.get("service") or {},
        })
    out.sort(key=lambda x: x["id"])
    return out


# ---------------------------------------------------------------------------
# 下载 / 安装

async def _fetch_bytes(url: str) -> bytes:
    if not url.startswith("https://"):
        raise ValueError("下载地址必须为 https")
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=15.0),
                                 trust_env=False, follow_redirects=True) as client:
        async with client.stream("GET", url, headers={"User-Agent": DOWNLOAD_UA}) as resp:
            resp.raise_for_status()
            chunks: list[bytes] = []
            total = 0
            async for chunk in resp.aiter_bytes():
                total += len(chunk)
                if total > MAX_PACKAGE_BYTES:
                    raise ValueError("插件包超过大小限制")
                chunks.append(chunk)
            return b"".join(chunks)


def _safe_extract(zip_path: Path, dest: Path) -> None:
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        if len(names) > MAX_ENTRIES:
            raise ValueError("插件包条目过多")
        root_real = os.path.realpath(dest)
        for info in zf.infolist():
            parts = info.filename.replace("\\", "/").split("/")
            if len(parts) > MAX_PATH_DEPTH:
                raise ValueError("插件包路径层级过深")
            target = os.path.realpath(dest / info.filename)
            if target != root_real and not target.startswith(root_real + os.sep):
                raise ValueError("插件包包含越界路径（zip-slip）")
            if ((info.external_attr >> 16) & 0o170000) == 0o120000:
                raise ValueError("插件包不允许包含符号链接")
            if info.filename.endswith("/"):
                (dest / info.filename).mkdir(parents=True, exist_ok=True)
                continue
            target_path = dest / info.filename
            target_path.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(target_path, "wb") as dst:
                shutil.copyfileobj(src, dst)


def _package_root(staging: Path) -> Path:
    children = [p for p in staging.iterdir()]
    if len(children) == 1 and children[0].is_dir():
        return children[0]
    return staging


def _load_manifest(root: Path) -> dict:
    mf_path = root / "manifest.json"
    if not mf_path.is_file():
        raise ValueError("插件包缺少 manifest.json")
    try:
        with open(mf_path, encoding="utf-8") as f:
            mf = json.load(f)
    except Exception as e:
        raise ValueError(f"manifest.json 解析失败: {e}")
    if not isinstance(mf, dict):
        raise ValueError("manifest.json 必须为对象")
    pid = str(mf.get("id") or "")
    if not _ID_RE.match(pid):
        raise ValueError("manifest.json 的 id 无效")
    entry = str(mf.get("entry") or "")
    if not entry or not (root / entry).is_file() or not entry.endswith(".py"):
        raise ValueError("manifest.json 的 entry 未指向包内 .py 脚本")
    unknown = perm.unknown_permissions(mf.get("permissions") or [])
    if unknown:
        raise ValueError("manifest.json 含未知权限: " + ", ".join(unknown))
    mf["permissions"] = perm.normalize_permissions(mf.get("permissions") or [])
    mf["settings"] = settings.normalize_schema(mf.get("settings"))
    ptype = str(mf.get("type") or "script")
    if ptype not in ("script", "apiserver"):
        raise ValueError("manifest.json 的 type 无效（仅支持 script / apiserver）")
    mf["type"] = ptype
    if ptype == "apiserver":
        svc = mf.get("service")
        if not isinstance(svc, dict) or not svc.get("binary"):
            raise ValueError("apiserver 插件必须在 service.binary 声明服务二进制路径")
        mf["service"] = svc
    return mf


async def install_from_url(url: str, *, expected_sha256: str = "",
                           plugin_id: str = "") -> dict:
    """下载 -> 校验 -> 安全解压 -> 校验 manifest -> 原子落位 -> 写已装记录。"""
    _ensure_dirs()
    payload = await _fetch_bytes(url)
    if expected_sha256:
        digest = hashlib.sha256(payload).hexdigest()
        if digest != expected_sha256.lower():
            raise ValueError("插件包 SHA-256 校验失败")

    token = uuid.uuid4().hex[:12]
    work = STAGING_DIR / token
    zip_path = work / "package.zip"
    extract_dir = work / "package"
    try:
        work.mkdir(parents=True, exist_ok=True)
        extract_dir.mkdir(parents=True, exist_ok=True)
        zip_path.write_bytes(payload)
        _safe_extract(zip_path, extract_dir)
        root = _package_root(extract_dir)
        mf = _load_manifest(root)
        pid = str(mf["id"])
        if plugin_id and pid != plugin_id:
            raise ValueError(f"插件 id 不匹配: 期望 {plugin_id}, 实际 {pid}")
        version = str(mf.get("version") or "0.0.0")
        dest = PACKAGES_DIR / pid / version
        if dest.exists():
            shutil.rmtree(dest, ignore_errors=True)
        dest.parent.mkdir(parents=True, exist_ok=True)
        os.replace(root, dest)
    finally:
        shutil.rmtree(work, ignore_errors=True)

    installed = _load_installed()
    old = installed.get(pid) or {}
    installed[pid] = {
        "id": pid,
        "name": mf.get("name") or pid,
        "description": mf.get("description") or "",
        "version": version,
        "entry": str(mf["entry"]),
        "function": str(mf.get("function") or "run"),
        "permissions": mf["permissions"],
        "settings": mf["settings"],
        "settings_values": settings.merge_values(mf["settings"], old.get("settings_values")),
        "enabled": bool(old.get("enabled", True)),
        "installed_at": int(time.time()),
        "path": str(dest),
        "download_url": url,
        "type": mf.get("type") or "script",
        "service": mf.get("service") or {},
        "platform": str(mf.get("platform") or ""),
    }
    _save_installed(installed)
    _audit({"ts": time.time(), "plugin": pid, "action": "install", "version": version, "url": url})
    return _public_record(pid, installed[pid])


def install_from_catalog(plugin_id: str) -> dict:
    """从已缓存的在线目录中按 id 安装（同步入口，供后台任务调用需先 fetch）。"""
    entries = (_catalog_cache or {}).get("plugins") or []
    entry = next((e for e in entries if e.get("id") == plugin_id), None)
    if not entry:
        raise ValueError(f"在线目录中未找到插件: {plugin_id}")
    url = entry.get("download_url") or ""
    if not url:
        raise ValueError("该插件没有下载地址")
    # install_from_url 为 async；此处由调用方在异步上下文内 await
    return url, str(entry.get("sha256") or "")


def uninstall(plugin_id: str) -> None:
    data = _load_installed()
    rec = data.pop(plugin_id, None)
    if not rec:
        raise ValueError(f"插件未安装: {plugin_id}")
    _save_installed(data)
    if rec.get("path"):
        shutil.rmtree(rec["path"], ignore_errors=True)
        # 清理空的插件版本父目录
        parent = Path(rec["path"]).parent
        try:
            if parent.is_dir() and not any(parent.iterdir()):
                parent.rmdir()
        except Exception:
            pass
    _audit({"ts": time.time(), "plugin": plugin_id, "action": "uninstall"})


# ---------------------------------------------------------------------------
# 执行

def run_plugin(plugin_id: str, params: dict | None = None) -> dict:
    """加载插件入口脚本并注入受限 ctx 执行，返回结构化结果。

    在线程池中调用本函数（见 router），使用子进程隔离的 import；脚本 return 值
    需可被 JSON 序列化，否则以 str 兜底。
    """
    from app.services.script_plugin_context import PluginContext

    rec = get_installed(plugin_id)
    if not rec:
        raise ValueError(f"插件未安装: {plugin_id}")
    if not rec.get("enabled", True):
        raise ValueError(f"插件已停用: {plugin_id}")

    root = Path(rec["path"])
    entry = root / rec["entry"]
    if not entry.is_file():
        raise ValueError("插件入口脚本不存在")

    started = time.time()
    logs: list[str] = []
    audit_records: list[dict] = []

    def sink(audit_rec: dict) -> None:
        audit_records.append(audit_rec)

    try:
        ctx = PluginContext(
            plugin_id=plugin_id,
            version=rec.get("version") or "",
            permissions=rec.get("permissions") or [],
            params=params or {},
            settings_values=rec.get("settings_values") or {},
            sink=sink,
        )
        # 每次执行使用唯一模块名，避免多插件互相污染 sys.modules
        mod_name = f"_sp_{plugin_id.replace('-', '_').replace('.', '_')}_{uuid.uuid4().hex[:8]}"
        spec = importlib.util.spec_from_file_location(mod_name, entry)
        if spec is None or spec.loader is None:
            raise ValueError("无法加载插件脚本")
        module = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = module
        try:
            spec.loader.exec_module(module)
            func_name = str(rec.get("function") or "run")
            func = getattr(module, func_name, None)
            if not callable(func):
                raise ValueError(f"插件脚本缺少入口函数 {func_name}()")
            result = func(ctx)
        finally:
            sys.modules.pop(mod_name, None)
        try:
            json.dumps(result)
            safe_result = result
        except Exception:
            safe_result = str(result)
        elapsed = round(time.time() - started, 3)
        _audit({"ts": time.time(), "plugin": plugin_id, "action": "run",
                "ok": True, "elapsed": elapsed})
        return {
            "ok": True,
            "plugin_id": plugin_id,
            "result": safe_result,
            "logs": list(ctx.logs),
            "elapsed": elapsed,
        }
    except Exception as e:
        elapsed = round(time.time() - started, 3)
        _audit({"ts": time.time(), "plugin": plugin_id, "action": "run",
                "ok": False, "error": str(e)[:300], "elapsed": elapsed})
        for ar in audit_records:
            _audit(ar)
        return {
            "ok": False,
            "plugin_id": plugin_id,
            "error": str(e)[:500],
            "logs": logs,
            "elapsed": elapsed,
        }
