"""Qoder / Qoder CN 额度查询增强（移植 qoder2api-hub 的 fetch_credits 思路）。

与项目内既有 ``native_accounts.refresh_account`` 的 qoder 分支互补：这里提供
可复用的纯 HTTP 查询函数，不依赖桌面客户端 native 层。

要点（对齐 qoder2api-hub）：
- 候选路径矩阵探测：INTL 的 ``/api/v2/quota/usage`` 常 404/403，而官网会话侧
  ``/sash/api/v1/me/*`` 才有数据；按「主机 × 路径」顺序试，命中后按区域缓存到
  ``~/.echobird/qoder-credits-source.json``（6 小时 TTL），下次优先用命中项。
- 套餐名：``GET /api/v2/user/plan`` 取 plan_tier_name / user_type，失败回退额度
  载荷里的 userType。
- 归一化：把任意额度对象归一成 ``{name, kind, remaining, total, expires_at}``，
  团队共享池（orgResourcePackage/sharedQuota/teamQuota）计入 remain_effective。
- 退化守卫：全 0 / 无可识别字段的载荷标记 degraded，调用方据此保留旧快照。
"""
import json
import os
import time
from urllib.parse import urlsplit

import httpx

from app.utils import ssl_relax

BASE_URLS = {
    "qoder": "https://openapi.qoder.sh",
    "qodercn": "https://openapi.qoder.com.cn",
}
WEBSITES = {
    "qoder": "https://qoder.com",
    "qodercn": "https://qoder.cn",
}
CREDITS_PATHS = (
    "/sash/api/v2/me/usage",
    "/api/v2/quota/usage",
    "/api/v2/user/quota",
    "/api/v2/me/quota",
    "/sash/api/v1/me/quota",
    "/sash/api/v1/me/credits",
    "/sash/api/v1/me/usage",
)
PLAN_PATH = "/api/v2/user/plan"
SOURCE_TTL = 6 * 3600
ECHO_DIR = os.path.expanduser("~/.echobird")
SOURCE_FILE = os.path.join(ECHO_DIR, "qoder-credits-source.json")

_NUM_FIELDS = ("remaining", "remain", "balance", "remainingQuota", "remainQuota")
_TOTAL_FIELDS = ("total", "size", "totalQuota", "capacity", "quota")
_SHARED_KEYS = ("orgResourcePackage", "org_resource_package", "sharedQuota",
                "shared_quota", "teamQuota", "team_quota", "organization")


def _num(v):
    if isinstance(v, bool) or v is None:
        return None
    try:
        n = float(v)
        return n if n == n and n not in (float("inf"), float("-inf")) else None
    except (TypeError, ValueError):
        return None


def _headers(creds: dict) -> dict:
    token = str(creds.get("device_token") or creds.get("access_token") or "")
    return {
        "Accept": "application/json",
        "Authorization": "Bearer " + token,
        "Cosy-ClientType": "10",
        "User-Agent": "Qoder",
    }


def _load_sources() -> dict:
    try:
        with open(SOURCE_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_source(platform: str, host: str, path: str, fields: list) -> None:
    data = _load_sources()
    data[platform] = {"host": host, "path": path,
                      "fields": list(fields or []), "at": time.time()}
    try:
        os.makedirs(ECHO_DIR, exist_ok=True)
        tmp = SOURCE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, SOURCE_FILE)
    except Exception:
        pass


def _source_fresh(src: dict) -> bool:
    if not isinstance(src, dict) or not src.get("host") or not src.get("path"):
        return False
    try:
        return time.time() - float(src.get("at") or 0) < SOURCE_TTL
    except (TypeError, ValueError):
        return False


def _bucket(node, name: str, kind: str = "pack") -> dict | None:
    if not isinstance(node, dict):
        return None
    remaining = None
    for key in _NUM_FIELDS:
        remaining = _num(node.get(key))
        if remaining is not None:
            break
    total = None
    for key in _TOTAL_FIELDS:
        total = _num(node.get(key))
        if total is not None:
            break
    if remaining is None and total is None:
        return None
    exp = node.get("expires_at") or node.get("expiresAt") or node.get("resetAt") or node.get("reset_at")
    if isinstance(exp, str) and exp.isdigit():
        exp = int(exp)
    return {"name": name, "kind": kind,
            "remaining": remaining, "total": total, "expires_at": exp}


def normalize_usage(payload, platform: str) -> dict | None:
    """把 /sash/api/v2/me/usage 或 /api/v2/quota/usage 归一成统一结构。

    返回 ``{"windows": [...], "plan": ..., "fields": [...], "degraded": bool}``；
    无法识别任何额度字段时返回 None。
    """
    if not isinstance(payload, dict):
        return None
    if payload.get("showCampaign") is False and not payload.get("qoderUsage"):
        # 与参考仓库一致：显式关闭时不强行解析
        return None
    value = payload.get("qoderUsage")
    if not isinstance(value, dict):
        value = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    if not isinstance(value, dict):
        return None

    windows: list[dict] = []
    seen = set()

    def add(node, name, kind="pack"):
        bucket = _bucket(node, name, kind)
        if bucket and name not in seen:
            seen.add(name)
            windows.append(bucket)

    # enterprise 模式：只给一个占位
    if str(payload.get("displayMode") or "") == "enterprise":
        windows.append({"name": "Enterprise", "kind": "pack",
                        "remaining": None, "total": None, "expires_at": None})

    for a, b, name in (
        ("userQuota", "user_quota", "Credits"),
        ("addOnQuota", "add_on_quota", "Add-on"),
        ("dedicatedResourcePackages", "dedicated_resource_packages", "Dedicated"),
        ("bonusQuota", "bonus_quota", "Bonus"),
    ):
        node = value.get(a) if value.get(a) is not None else value.get(b)
        if isinstance(node, list):
            for i, row in enumerate(node):
                add(row, f"{name} {i + 1}" if len(node) > 1 else name)
        else:
            add(node, name)

    shared = None
    for key in _SHARED_KEYS:
        node = value.get(key)
        if isinstance(node, dict):
            shared = _bucket(node, "Shared", "shared")
            break
        if isinstance(node, list) and node:
            shared = _bucket(node[0], "Shared", "shared")
            break
    if not shared:
        # 顶层独立字段（参考仓库的团队共享识别）
        for key in ("sharedQuota", "shared_quota", "teamQuota", "team_quota"):
            shared = _bucket(payload, "Shared", "shared") if key in payload else None
            if shared:
                break
    if shared:
        windows.append(shared)

    # 通用兜底：remain/total 直接挂在载荷上（/api/v2/quota/usage 形态）
    if not windows:
        for key in ("credits", "quota", "usage", "data"):
            node = payload.get(key)
            if isinstance(node, dict):
                b = _bucket(node, "Credits")
                if b:
                    windows.append(b)
                    break
        if not windows:
            b = _bucket(payload, "Credits")
            if b:
                windows.append(b)

    plan = ""
    for key in ("userType", "user_type", "planTierName", "plan_tier_name", "plan"):
        v = payload.get(key) or value.get(key)
        if isinstance(v, str) and v.strip():
            plan = v.strip()
            break

    if not windows:
        return None
    degraded = all(
        (w.get("remaining") in (None, 0)) and (w.get("total") in (None, 0))
        for w in windows
    )
    return {"windows": windows, "plan": plan,
            "fields": sorted({k for w in windows for k in w if w.get(k) is not None}),
            "degraded": degraded}


async def _get_json(client: httpx.AsyncClient, url: str, creds: dict, timeout: float):
    resp = await client.get(url, headers=_headers(creds), timeout=timeout)
    if resp.status_code in (401, 403):
        raise PermissionError(f"HTTP {resp.status_code}")
    if resp.status_code == 404:
        raise FileNotFoundError("HTTP 404")
    if not resp.is_success:
        raise RuntimeError(f"HTTP {resp.status_code}")
    try:
        return resp.json()
    except ValueError:
        return None


async def fetch_usage(creds: dict, platform: str, timeout: float = 20.0,
                      probe_limit: int = 5) -> dict:
    """查询并归一化 Qoder 额度。

    返回 ``{"windows","plan","source","degraded","error","login_required"}``。
    先试缓存命中项，再走默认路径，失败时按候选矩阵探测（按区域缓存命中）。
    """
    out = {"windows": [], "plan": "", "source": "", "degraded": False,
           "error": "", "login_required": False}
    if platform not in BASE_URLS or not (creds.get("device_token") or creds.get("access_token")):
        out["error"] = "凭据缺失或平台不支持"
        return out

    base = BASE_URLS[platform]
    website = WEBSITES[platform]
    hosts = [base, website]
    cached = _load_sources().get(platform) if _source_fresh(_load_sources().get(platform)) else None

    candidates: list[tuple[str, str]] = []
    if cached:
        candidates.append((cached["host"], cached["path"]))
    candidates.append((base, CREDITS_PATHS[0]))
    for host in hosts:
        for path in CREDITS_PATHS:
            pair = (host, path)
            if pair not in candidates:
                candidates.append(pair)

    async with httpx.AsyncClient(trust_env=False, follow_redirects=True) as client:
        tried = 0
        for host, path in candidates:
            if tried >= probe_limit:
                break
            tried += 1
            try:
                payload = await _get_json(client, host + path, creds, timeout)
            except PermissionError:
                continue
            except (FileNotFoundError, RuntimeError):
                continue
            except httpx.HTTPError:
                continue
            norm = normalize_usage(payload, platform)
            if norm:
                out["windows"] = norm["windows"]
                out["plan"] = norm["plan"]
                out["source"] = host + path
                out["degraded"] = norm["degraded"]
                if path != CREDITS_PATHS[0] or host != base:
                    _save_source(platform, host, path, norm["fields"])
                break

        # 套餐名独立端点（尽力，失败不影响额度）
        if not out["plan"]:
            try:
                plan_payload = await _get_json(client, base + PLAN_PATH, creds, timeout)
                if isinstance(plan_payload, dict):
                    node = plan_payload.get("data") if isinstance(plan_payload.get("data"), dict) else plan_payload
                    for key in ("planTierName", "plan_tier_name", "planName", "userType", "user_type"):
                        v = node.get(key)
                        if isinstance(v, str) and v.strip():
                            out["plan"] = v.strip()
                            break
            except Exception:
                pass
    if not out["windows"] and not out["error"]:
        out["error"] = "未获取到额度数据（候选接口均无有效字段）"
    return out
