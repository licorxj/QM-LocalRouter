"""账号接入页的额度/积分查询（移植 EchoBird echobird-plugin 各平台协议）。

统一产出可缓存的额度快照（写入 ApiKey.quota_cache）：
    {"plan": ..., "subscription_end_at": ...,
     "identity": ...,                       # 学到的账号标识（邮箱/昵称），可为空
     "metrics": [ {"key","value","percent","reset_at","remaining","total"} ],
     "checkin": {"claimed": bool, "claimed_at": ...} | None}

各平台协议（与 EchoBird 源码对齐）：
- WorkBuddy CN：并发 billing/meter 三个接口（summary/paid/free），AI 版 get-user-resource；
  积分按 PackageCode 前缀 TCACA_code_XXX 分套餐/奖励/加量三类；请求必须模拟官网前台
  （Bearer + X-User-Id/X-Enterprise-Id/X-Tenant-Id + Origin/Referer: {base}/profile/plans-usage）
- ZCode：GET zcode.z.ai/api/v1/zcode-plan/billing/balance（Bearer JWT + x-device-mid）
- Claude Code：GET api.anthropic.com/api/oauth/usage（anthropic-beta: oauth-2025-04-20）
- Codex：GET chatgpt.com/backend-api/wham/usage（x-openai-target-path 头）
"""
import asyncio
import datetime as _dt
import json
import time
import uuid
from urllib.parse import urlsplit

import httpx

from app.utils import ssl_relax
from app.utils.crypto import decrypt_value

TIMEOUT = 20.0
DAY = 86400


class QuotaError(Exception):
    """额度查询失败（message 面向用户展示）。"""

    def __init__(self, message: str, login_required: bool = False):
        super().__init__(message)
        self.message = message
        self.login_required = login_required


def _num(v):
    """int/float/数字字符串 -> float（EchoBird number()）。"""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            n = float(v.strip())
            return n if n == n and n not in (float("inf"), float("-inf")) else None
        except ValueError:
            return None
    return None


def _ts(v):
    """多形态时间 -> unix 秒（数字自动 ms/s 归一，字符串 RFC3339 / 本地日期）。"""
    if v is None:
        return None
    n = _num(v)
    if n is not None:
        return int(n / 1000) if abs(n) > 10_000_000_000 else int(n)
    if not isinstance(v, str):
        return None
    raw = v.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            return int(_dt.datetime.strptime(raw, fmt).timestamp())
        except ValueError:
            pass
    try:
        return int(_dt.datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp())
    except ValueError:
        pass
    try:
        return int(_dt.datetime.strptime(raw[:10], "%Y-%m-%d").replace(hour=23, minute=59, second=59).timestamp())
    except ValueError:
        return None


def _text(item: dict, *keys: str) -> str:
    for k in keys:
        v = item.get(k)
        if isinstance(v, str) and v.strip():
            return v
    return ""


# ------------------------------------------------------------------ WorkBuddy 积分
# 移植 echobird-plugin native/account-engine/src/services/workbuddy_credits.rs

_CN_PAID_CODES = [
    "TCACA_code_002_AkiJS3ZHF5", "TCACA_code_023_4xbGhMrE6q", "TCACA_code_026_BaESVICNoi",
    "TCACA_code_027_0FCGVA6vSa", "TCACA_code_009_0XmEQc2xOf", "TCACA_code_038_OhvqZtiPKr",
    "TCACA_code_003_FAnt7lcmRT", "TCACA_code_036_lupO5WgNdG",
]
_CN_FREE_CODES = [
    "TCACA_code_008_cfWoLwvjU4", "TCACA_code_007_nzdH5h4Nl0", "TCACA_code_028_NtpWi0jzXs",
    "TCACA_code_029_6wCGEWquYy", "TCACA_code_030_BjSt89qTvr", "TCACA_code_001_PqouKr6QWV",
    "TCACA_code_006_DbXS0lrypC", "TCACA_code_035_ArVxJcGDsm", "TCACA_code_037_WxOD3MpI2o",
    "TCACA_code_039_KRcQj7wUat", "TCACA_code_040_mi9rCYg46x",
]
_WB_BASES = {
    "cn": ["https://www.codebuddy.cn", "https://www.workbuddy.cn"],
    "intl": ["https://www.workbuddy.ai"],
}
_WB_QUANTITY_ALIASES = {"Size": "CycleTotalCapacity", "Remain": "CycleRemainCapacity", "Used": "CycleUsedCapacity"}
_WB_TOPUP_WORDS = ("top-up", "top up", "addon", "add-on", "bonus", "赠", "加量", "补充", "活动", "运营")
_WB_REWARD_WORDS = ("奖励", "赠", "bonus", "reward", "activity")
_WB_BASE_WORDS = ("基础", "体验", "free", "pro", "subscription", "trial")


def _wb_amount(item: dict, suffix: str):
    """CycleCapacity{Suffix}[Precise] / Capacity{Suffix} / SlicePeriodCapacity{Suffix}，
    兼容 CycleTotalCapacity / CycleRemainCapacity / CycleUsedCapacity 别名。"""
    alias = _WB_QUANTITY_ALIASES.get(suffix, "CycleUsedCapacity")
    for prefix in ("CycleCapacity", "Capacity", "SlicePeriodCapacity"):
        for ending in (suffix + "Precise", suffix):
            n = _num(item.get(prefix + ending))
            if n is not None:
                return max(0.0, n)
        if prefix == "CycleCapacity":
            n = _num(item.get(alias))
            if n is not None:
                return max(0.0, n)
    return None


def _wb_resource_amount(item: dict, suffix: str):
    v = _wb_amount(item, suffix)
    if v is not None:
        return v
    slices = item.get("SlicePeriodUsageDetails") or item.get("slicePeriodUsageDetails")
    if isinstance(slices, list) and slices and isinstance(slices[0], dict):
        return _wb_amount(slices[0], suffix)
    return None


def _wb_resources(body: dict, kind: str) -> list:
    """data{,data}{,/Response/Data} 下的 Packages / Accounts 列表。"""
    nodes = [body.get("data")]
    if isinstance(nodes[0], dict):
        inner = nodes[0]
        nodes.append(inner.get("data"))
        resp = inner.get("Response")
        if isinstance(resp, dict):
            nodes.append(resp.get("Data"))
        if isinstance(inner.get("data"), dict):
            resp2 = inner["data"].get("Response")
            if isinstance(resp2, dict):
                nodes.append(resp2.get("Data"))
    for node in nodes:
        if isinstance(node, dict):
            for key in (kind, kind.lower()):
                items = node.get(key)
                if isinstance(items, list):
                    return items
    return []


def _wb_package_expiry(item: dict):
    for k in ("PackageEndTime", "packageEndTime", "DeductionEndTime", "deductionEndTime", "ExpiredTime", "expiredTime"):
        t = _ts(item.get(k))
        if t is not None:
            return t
    return None


def _wb_expiry(item: dict, now: int):
    deduction = _wb_package_expiry(item)
    cycle = _ts(item.get("CycleEndTime")) or _ts(item.get("cycleEndTime"))
    if deduction is not None and cycle is not None and abs(deduction - cycle) > 365 * DAY:
        result = cycle
    else:
        result = deduction if deduction is not None else cycle
    if result is not None and result - now > 730 * DAY:
        return None
    return result


def _wb_summarize(items: list, now: int):
    """(remaining, total, expires_at)——EchoBird summarize()。"""
    remaining = total = 0.0
    expires = None
    for item in items:
        end = _wb_expiry(item, now)
        if end is not None and end <= now:
            continue
        capacity = _wb_resource_amount(item, "Size")
        left = _wb_resource_amount(item, "Remain")
        used = _wb_resource_amount(item, "Used")
        if capacity is None:
            if left is not None and used is not None:
                capacity = left + used
            else:
                capacity = left if left is not None else (used or 0.0)
        if left is None:
            left = max(0.0, capacity - (used or 0.0))
        total += capacity
        remaining += left
        if left > 0.0 and end is not None:
            expires = end if expires is None else min(expires, end)
    return remaining, total, expires


def _wb_category(item: dict):
    """TCACA_code_XXX 前缀 + 包名关键词 -> base/reward/addon/None。"""
    code = _text(item, "PackageCode", "packageCode")
    name = _text(item, "PackageName", "packageName").lower()
    numbered = code.split("_")[0] if code.startswith("TCACA_code_") else None
    if numbered in ("009", "036", "038") or any(w in name for w in ("加量", "top-up", "top up", "addon", "add-on")):
        return "addon"
    if numbered in ("001", "002", "003", "005", "006", "008", "023", "026", "027", "035", "039", "040") \
            or code == "TCACA_code_enterprise":
        return "base"
    if numbered in ("007", "028", "029", "030", "037") or any(w in name for w in _WB_REWARD_WORDS):
        return "reward"
    if any(w in name for w in _WB_BASE_WORDS):
        return "base"
    return None


def _wb_domestic_tier(name: str):
    if "企业" in name or "團隊" in name or "团队" in name:
        return None
    if "旗舰" in name or "旗艦" in name:
        return (4, "旗舰版")
    if "高级" in name or "高級" in name:
        return (3, "高级版")
    if "标准" in name or "標準" in name or "专业" in name:
        return (2, "标准版")
    if "体验" in name or "體驗" in name or "免费" in name:
        return (0, "体验版")
    return None


_WB_CN_CODE_TIERS = {
    "TCACA_code_001_PqouKr6QWV": (0, "体验版"),
    "TCACA_code_006_DbXS0lrypC": (0, "体验版"),
    "TCACA_code_003_FAnt7lcmRT": (2, "标准版"),
}


def _wb_plan(items: list, summary: dict | None, now: int, edition: str):
    """EchoBird subscription_plan()：先看 summary 定档，再从包名推断。"""
    subscription = ""
    paid = None
    if isinstance(summary, dict):
        subscription = _text(summary, "SubscriptionPackageCode")
        p = summary.get("IsPaidUser")
        paid = p if isinstance(p, bool) else None
    if edition == "cn":
        tier = None
        if summary:
            pkg_name = _text(summary, "SubscriptionPackageName")
            if pkg_name:
                tier = _wb_domestic_tier(pkg_name)
            tier = tier or _WB_CN_CODE_TIERS.get(subscription)
        if tier:
            return tier[1]
    best = None
    for item in items:
        end = _wb_expiry(item, now)
        if end is not None and end <= now:
            continue
        start = _ts(item.get("DeductionStartTime")) or _ts(item.get("deductionStartTime"))
        if start is not None and start > now:
            continue
        status = item.get("Status") if item.get("Status") is not None else item.get("status")
        if isinstance(status, (int, float)) and int(status) not in (0, 3):
            continue
        code = _text(item, "PackageCode", "packageCode")
        if subscription and code != subscription:
            continue
        if edition == "cn":
            tier = _WB_CN_CODE_TIERS.get(code)
            if tier:
                best = max(best or (-1, ""), (tier[0], tier[1]), key=lambda x: x[0])
                continue
        name = _text(item, "PackageName", "packageName")
        if not name:
            continue
        low = name.lower()
        if any(w in low for w in _WB_TOPUP_WORDS):
            continue
        if edition == "cn":
            tier = _wb_domestic_tier(name)
            if tier:
                best = max(best or (-1, ""), (tier[0], tier[1]), key=lambda x: x[0])
                continue
        words = [w for w in "".join(c if c.isascii() and c.isalnum() else " " for c in low).split()]
        is_plan = "subscription" in low or "plan" in words or len(words) == 1
        if (is_plan and "team" in words) or "团队版" in name:
            best = max(best or (-1, ""), (5, "Team"), key=lambda x: x[0])
        elif (is_plan and "pro" in words) or "专业版" in name:
            best = max(best or (-1, ""), (2, "标准版" if edition == "cn" else "Pro"), key=lambda x: x[0])
        elif paid is not True and ((is_plan and "free" in words) or "免费版" in name or "个人体验版" in name):
            best = max(best or (-1, ""), (0, "体验版" if edition == "cn" else "Free"), key=lambda x: x[0])
    if best:
        return best[1]
    if not subscription and paid is False:
        return "体验版" if edition == "cn" else "Free"
    return None


def _wb_quota_payload(items: list, summary: dict | None, now: int, edition: str) -> dict:
    remaining, total, expires_at = _wb_summarize(items, now)
    groups: dict[str, list] = {"base": [], "reward": [], "addon": []}
    for item in items:
        cat = _wb_category(item)
        if cat:
            groups[cat].append(item)

    def _known(group: list) -> bool:
        if not group:
            return False
        alive = [it for it in group
                 if not ((end := _wb_expiry(it, now)) is not None and end <= now)] or group
        return all(
            _wb_resource_amount(it, "Remain") is not None
            or (_wb_resource_amount(it, "Size") is not None and _wb_resource_amount(it, "Used") is not None)
            for it in alive
        )

    metrics = []
    base_values = _wb_summarize(groups["base"], now) if _known(groups["base"]) else None
    if base_values:
        metrics.append(("base", base_values))
    reward_values = _wb_summarize(groups["reward"], now) if _known(groups["reward"]) else None
    if reward_values:
        metrics.append(("reward", reward_values))
    addon_values = _wb_summarize(groups["addon"], now) if _known(groups["addon"]) else None
    if addon_values:
        metrics.append(("addon", addon_values))

    reward_next_expiry = None
    if reward_values:
        for item in groups["reward"]:
            end = _wb_package_expiry(item)
            if end and end > now and end - now <= 730 * DAY and _wb_summarize([item], now)[0] > 0.0:
                reward_next_expiry = end if reward_next_expiry is None else min(reward_next_expiry, end)

    base_reset_at = None
    for item in groups["base"]:
        end = _ts(item.get("CycleEndTime")) or _ts(item.get("cycleEndTime")) or _wb_expiry(item, now)
        if end and end > now:
            base_reset_at = end if base_reset_at is None else min(base_reset_at, end)

    out_metrics = []
    for key, (rem, tot, exp) in metrics:
        m = {"key": key, "value": _fmt_amount(rem), "remaining": rem, "total": tot,
             "percent": round(rem / tot * 100, 1) if tot > 0 else None}
        m["reset_at"] = reward_next_expiry if key == "reward" and reward_next_expiry else (base_reset_at if key == "base" else exp)
        out_metrics.append(m)
    return {
        "plan": _wb_plan(items, summary, now, edition),
        "subscription_end_at": expires_at,
        "metrics": out_metrics,
        "checkin": None,
    }


def _fmt_amount(v: float | None) -> str:
    if v is None:
        return ""
    if abs(v - round(v)) < 1e-9:
        return str(int(round(v)))
    return f"{v:,.2f}".rstrip("0").rstrip(".")


def _wb_headers(credential: dict, base: str) -> dict:
    """billing 接口必须模拟官网前台（EchoBird authorized()）。

    凭据 blob 字段沿用本项目 workbuddy 登录产物：uid / enterprise_id / department。
    User-Agent 必须为 CLI 口径：默认 python-httpx UA 会被上游以 10085「请求不合法」拒绝。
    不发送 X-Department-Info（EchoBird 计费请求同样不带）。
    """
    cfg = _wb_region_config(credential)
    app_name = "WorkBuddy" if cfg == "cn" else "workbuddy-ai"
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": f"CLI/unknown {app_name}/5.6.2 LocalRouter/1.0",
        "Authorization": "Bearer " + str(credential.get("access_token") or ""),
        "Origin": base,
        "Referer": base + "/profile/plans-usage",
        "X-Domain": urlsplit(base).netloc,
    }
    uid = credential.get("uid")
    if uid not in (None, ""):
        headers["X-User-Id"] = str(uid)
    ent = credential.get("enterprise_id") or credential.get("enterpriseId")
    if ent not in (None, ""):
        headers["X-Enterprise-Id"] = str(ent)
        headers["X-Tenant-Id"] = str(ent)
    return headers


def _wb_region_config(credential: dict) -> str:
    from app.services import workbuddy
    return workbuddy.normalize_region(str(credential.get("region") or "cn"))


async def _wb_post(base: str, path: str, credential: dict, body: dict, paths_extra: bool = False) -> dict:
    """POST billing 接口；AI 版 404 时回退 /v2 前缀（EchoBird post()）。"""
    paths = [path, "/v2" + path] if paths_extra else [path]
    last_status = 0
    for idx, p in enumerate(paths):
        url = base + p
        try:
            client = ssl_relax.get_client(url)
            resp = await client.request("POST", url, json=body, headers=_wb_headers(credential, base),
                                        timeout=TIMEOUT)
        except httpx.HTTPError as e:
            raise QuotaError(f"网络请求失败: {str(e)[:120]}")
        last_status = resp.status_code
        try:
            data = resp.json()
        except ValueError:
            data = {}
        code = data.get("code") if isinstance(data, dict) else None
        if resp.status_code == 404 or code == 404:
            if idx + 1 < len(paths):
                continue
            raise QuotaError("上游接口不存在（HTTP 404）")
        if resp.status_code in (401, 403) or code in (401, 10085):
            raise QuotaError("登录已失效，请重新登录该账号", login_required=True)
        if resp.status_code == 429:
            raise QuotaError("上游限流，请稍后再试")
        if not isinstance(data, dict):
            raise QuotaError("上游返回异常")
        # 与 workbuddy.call 一致的成功判定：code 为空/0/200 且无 error
        err = data.get("error")
        ok_code = code is None or str(code) in ("0", "200")
        if resp.status_code >= 400 or err or not ok_code:
            message = ""
            if isinstance(err, dict):
                message = str(err.get("message") or err.get("msg") or "")
            message = message or str(data.get("message") or data.get("msg") or f"HTTP {resp.status_code}")
            raise QuotaError(f"积分查询失败: {message[:150]}")
        return data
    raise QuotaError(f"积分查询失败（HTTP {last_status}）")


def _wb_credential(api_key, provider) -> tuple[str, dict]:
    """解出 WorkBuddy 凭据 + 官方区域（复用 workbuddy.load_credential）。"""
    from app.services import workbuddy
    credential = workbuddy.load_credential(api_key)
    region = workbuddy.resolve_region(credential, getattr(provider, "base_url", "") or "")
    if not credential.get("access_token"):
        raise QuotaError("凭据缺失，请重新登录", login_required=True)
    return region, credential


async def _fetch_workbuddy(api_key, provider) -> dict:
    region, credential = _wb_credential(api_key, provider)
    edition = "intl" if region == "intl" else "cn"
    now = int(time.time())
    # EchoBird base() 逻辑：积分接口的域名必须与登录会话域名一致
    # （workbuddy.cn 会话的 token 在 codebuddy.cn 会被拒 401/10085）
    bases = _WB_BASES[edition]
    session_host = str(credential.get("domain") or urlsplit(getattr(provider, "base_url", "") or "").netloc or "").lower()
    if edition == "cn":
        if "workbuddy.cn" in session_host:
            bases = ["https://www.workbuddy.cn", "https://www.codebuddy.cn"]
        elif "codebuddy.cn" in session_host:
            bases = ["https://www.codebuddy.cn", "https://www.workbuddy.cn"]

    async def _post_on_working_base(path: str, body: dict, paths_extra: bool = False) -> dict:
        last_err: QuotaError | None = None
        for base in bases:
            try:
                return await _wb_post(base, path, credential, body, paths_extra=paths_extra)
            except QuotaError as e:
                if e.login_required:
                    raise
                last_err = e
        raise last_err or QuotaError("积分查询失败")

    if edition == "cn":
        day = _dt.datetime.fromtimestamp(now)
        summary_body = {"PageNumber": 1, "PageSize": 200, "Status": [0, 3], "NeedRenewInfo": True,
                        "PackageCodes": _CN_PAID_CODES}
        free_body = {"PageNumber": 1, "PageSize": 200, "Status": [0, 3],
                     "SlicePeriodStartTime": day.strftime("%Y-%m-%d 00:00:00"),
                     "SlicePeriodEndTime": day.strftime("%Y-%m-%d 23:59:59"),
                     "PackageCodes": _CN_FREE_CODES}
        summary_r, paid_r, free_r = await asyncio.gather(
            _post_on_working_base("/billing/meter/get-user-resource-summary", {}),
            _post_on_working_base("/billing/meter/get-user-resource-paid-packages", summary_body),
            _post_on_working_base("/billing/meter/get-user-resource-free-packages", free_body),
        )
        packages = _wb_resources(summary_r, "Packages")
        items = list(_wb_resources(paid_r, "Accounts")) + list(_wb_resources(free_r, "Accounts"))
        seen_codes = {_text(it, "PackageCode", "packageCode") for it in items}
        for pkg in packages:
            if _text(pkg, "PackageCode", "packageCode") not in seen_codes:
                items.append(pkg)
        if not items:
            raise QuotaError("上游未返回积分明细")
        payload = _wb_quota_payload(items, summary_r, now, edition)
        payload["checkin"] = {"claimed": await _wb_checkin_claimed(credential, bases[0])}
        return payload

    body = await _post_on_working_base(
        "/billing/meter/get-user-resource",
        {"PageNumber": 1, "PageSize": 100, "ProductCode": "p_tcaca", "Status": [0, 3],
         "PackageEndTimeRangeBegin": _dt.datetime.fromtimestamp(now).strftime("%Y-%m-%d %H:%M:%S"),
         "PackageEndTimeRangeEnd": _dt.datetime.fromtimestamp(now + 365 * 101 * DAY).strftime("%Y-%m-%d %H:%M:%S")},
        paths_extra=True,
    )
    items = _wb_resources(body, "Accounts")
    if not items:
        raise QuotaError("上游未返回积分明细")
    return _wb_quota_payload(items, None, now, edition)


async def _wb_checkin_claimed(credential: dict, base: str) -> bool:
    """GET checkin-activity-status / checkin-status -> 今日是否已签（失败不阻断积分展示）。"""
    for path in ("checkin-activity-status", "checkin-status"):
        url = base + "/v2/billing/meter/" + path
        try:
            client = ssl_relax.get_client(url)
            resp = await client.request("GET", url, json={}, headers=_wb_headers(credential, base),
                                        timeout=TIMEOUT)
            data = resp.json() if resp.status_code == 200 else {}
            if isinstance(data, dict):
                node = data.get("data")
                if isinstance(node, dict):
                    claimed = node.get("today_checked_in")
                    if claimed is None:
                        claimed = node.get("todayCheckedIn")
                    if isinstance(claimed, bool):
                        return claimed
        except Exception:
            continue
    return False


async def claim_workbuddy(api_key, provider) -> dict:
    """WorkBuddy 每日签到（仅 CN）。返回 {"claimed": bool, "already": bool}。"""
    region, credential = _wb_credential(api_key, provider)
    if region != "cn":
        raise QuotaError("每日签到仅国内版支持")
    base = _WB_BASES["cn"][0]
    for path in ("/v2/billing/meter/daily-checkin", "/billing/meter/daily-checkin"):
        url = base + path
        try:
            client = ssl_relax.get_client(url)
            resp = await client.request("POST", url, json={}, headers=_wb_headers(credential, base),
                                        timeout=TIMEOUT)
        except httpx.HTTPError as e:
            raise QuotaError(f"网络请求失败: {str(e)[:120]}")
        try:
            body = resp.json()
        except ValueError:
            body = {}
        code = body.get("code") if isinstance(body, dict) else None
        message = _text(body if isinstance(body, dict) else {}, "message", "msg").lower()
        if resp.status_code in (401, 403) or code in (401, 10085):
            raise QuotaError("登录已失效，请重新登录该账号", login_required=True)
        if code == 10001 and any(w in message for w in ("已签到", "已领取", "已经签到", "已经领取", "重复签到",
                                                        "already checked in", "already claimed")):
            return {"claimed": False, "already": True}
        if resp.status_code == 404 or code == 404:
            continue
        # EchoBird claim_result：success(body) 且 data.success != false 即成功
        ok_code = code is None or str(code) in ("0", "200")
        data_node = body.get("data") if isinstance(body, dict) else None
        success_flag = ok_code and not body.get("error") and (not isinstance(data_node, dict) or data_node.get("success") is not False)
        if success_flag:
            return {"claimed": True, "already": False}
        if any(w in message for w in ("未开启", "未开始", "未开放", "活动已过期", "活动已结束", "inactive")):
            raise QuotaError("签到活动未开放或已结束")
        raise QuotaError("签到失败: " + (message[:120] or f"HTTP {resp.status_code}"))
    raise QuotaError("签到接口不可用")


# ------------------------------------------------------------------ ZCode 余额
# 移植 EchoBird src-tauri/src/services/zcode_accounts.rs apply_balance

ZCODE_BALANCE_URL = "https://zcode.z.ai/api/v1/zcode-plan/billing/balance"
ZCODE_PLAN_LABELS = ("lite", "pro", "max")


def _zcode_plan_label(name: str) -> str:
    low = name.strip().lower()
    words = [w for w in "".join(c if c.isascii() and c.isalnum() else " " for c in low).split()]
    if "团队" in name or "團隊" in name or "team" in words:
        return "Team"
    for w in words:
        if w in ZCODE_PLAN_LABELS:
            return w.capitalize()
    if "start plan" in low or "trial" in low or "trust build" in low or "体验" in name or "體驗" in name:
        return "Trial"
    return name.strip()


def _zcode_device_mid() -> str:
    """官方客户端 telemetry 的 deviceMid，缺省时持久化一个本机 UUID。"""
    import os
    home = os.path.expanduser("~")
    for path in (os.path.join(home, ".zcode", "v2", "telemetry-state.json"),):
        try:
            with open(path, "r", encoding="utf-8") as f:
                mid = (json.load(f) or {}).get("deviceMid")
            if isinstance(mid, str) and mid:
                return mid
        except Exception:
            pass
    mid = str(uuid.uuid4())
    try:
        from app.config import settings
        path = str(settings.DATA_DIR / "zcode-device-id")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(mid)
    except Exception:
        pass
    return mid


def _zcode_jwt(api_key) -> str:
    """余额接口用官方对话 JWT（access_token 即 zcodejwttoken）。"""
    try:
        token = decrypt_value(api_key.key_value)
    except Exception:
        token = ""
    if not token and api_key.oauth_refresh:
        try:
            blob = json.loads(decrypt_value(api_key.oauth_refresh)) or {}
            token = str(blob.get("access_token") or blob.get("zcodejwttoken") or "")
        except Exception:
            token = ""
    return token


async def _fetch_zcode(api_key, provider) -> dict:
    token = _zcode_jwt(api_key)
    if not token:
        raise QuotaError("凭据缺失，请重新导入该账号", login_required=True)
    now = int(time.time())
    url = ZCODE_BALANCE_URL + "?app_version=3.14.3"
    headers = {"Accept": "application/json", "Authorization": f"Bearer {token}",
               "x-device-mid": _zcode_device_mid()}
    try:
        client = ssl_relax.get_client(url)
        resp = await client.request("GET", url, headers=headers, timeout=TIMEOUT)
    except httpx.HTTPError as e:
        raise QuotaError(f"网络请求失败: {str(e)[:120]}")
    if resp.status_code in (401, 403):
        raise QuotaError("登录已失效，请重新导入该账号", login_required=True)
    try:
        body = resp.json()
    except ValueError:
        raise QuotaError(f"上游返回异常（HTTP {resp.status_code}）")
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, dict):
        raise QuotaError(f"上游返回异常（HTTP {resp.status_code}）")

    plans = data.get("plans") if isinstance(data.get("plans"), list) else []
    active = None
    for p in plans:
        if not isinstance(p, dict):
            continue
        end = _ts(p.get("ends_at"))
        if p.get("status") == "active" and (end is None or end > now):
            active = p
            break
    metrics = []
    plan = _zcode_plan_label(str(active.get("name") or "")) if active and active.get("name") else None
    balances = [b for b in (data.get("balances") or []) if isinstance(b, dict)]
    total = sum(_num(b.get("total_units")) or 0.0 for b in balances)
    remaining = sum(_num(b.get("remaining_units")) or 0.0 for b in balances)
    reset_at = None
    expires = [t for b in balances if (t := _ts(b.get("expires_at"))) is not None]
    if expires:
        reset_at = min(expires)
    if total > 0:
        metrics.append({
            "key": "quota", "value": _fmt_amount(remaining), "remaining": remaining, "total": total,
            "percent": round(max(0.0, min(100.0, remaining / total * 100)), 1),
            "reset_at": reset_at,
        })

    # 赠送 token 额度不在 balances，而在 active plan 的 entitlements[].grant_units
    # （period=one_time，签到领取后即将生效）。balances 为空时必须从这里取，否则 3 亿额度不显示。
    now_ts = int(time.time())
    entitle_effective_future = False
    if active and total <= 0:
        for ent in (active.get("entitlements") or []):
            if not isinstance(ent, dict):
                continue
            grant = _num(ent.get("grant_units")) or 0.0
            if grant <= 0:
                continue
            eff = _ts(ent.get("effective_at"))
            if eff and eff > now_ts:
                entitle_effective_future = True
            label = str(ent.get("show_name") or "").strip()
            metrics.append({
                "key": "quota",
                "label": label or "赠送额度",
                "value": _fmt_amount(grant), "remaining": grant, "total": grant,
                "percent": 100.0, "reset_at": eff,
                "pending": bool(eff and eff > now_ts),
            })
            total += grant
            remaining += grant
    identity = None
    try:
        blob = json.loads(decrypt_value(api_key.oauth_refresh)) if api_key.oauth_refresh else {}
        identity = blob.get("email") or None
    except Exception:
        pass
    # 面向用户的额度说明：无有效套餐/额度时给出明确提示，而不是空白或 "No Plan"
    if total > 0:
        notice = "赠送额度尚未生效，将在生效后可用" if entitle_effective_future and not balances else ""
    elif not plans:
        notice = "该账号当前没有可用的 Coding Plan 套餐"
    elif not active:
        notice = "套餐已过期或尚未生效"
    else:
        notice = "套餐有效，但当前没有可用额度余额"
    return {
        "plan": plan or ("No Plan" if not plans else None),
        "subscription_end_at": _ts(active.get("ends_at")) if active else None,
        "identity": identity,
        "metrics": metrics,
        "checkin": None,
        "notice": notice,
    }


# ------------------------------------------------------------------ Claude Code 用量
# 移植 echobird-plugin claude_code_accounts.rs：GET /api/oauth/usage

CLAUDE_USAGE_URL = "https://api.anthropic.com/api/oauth/usage"


def _usage_window_metrics(usage: dict, keys: list[tuple[str, str]]) -> list:
    """(metric_key, 响应字段) -> metrics；utilization 为已用百分比。"""
    out = []
    for key, field in keys:
        node = usage.get(field)
        if not isinstance(node, dict):
            continue
        used = _num(node.get("utilization"))
        if used is None:
            used = _num(node.get("utilization_percent")) or _num(node.get("used_percent"))
        if used is None:
            continue
        reset_at = _ts(node.get("resets_at"))
        out.append({
            "key": key, "value": f"{max(0.0, min(100.0, 100.0 - used)):.0f}%",
            "percent": round(max(0.0, min(100.0, 100.0 - used)), 1),
            "reset_at": reset_at, "remaining": None, "total": None,
        })
    return out


async def _fetch_claude(api_key, provider) -> dict:
    try:
        token = decrypt_value(api_key.key_value)
    except Exception:
        token = ""
    if not token:
        raise QuotaError("凭据缺失，请重新登录该账号", login_required=True)
    headers = {"Accept": "application/json", "Authorization": f"Bearer {token}",
               "anthropic-beta": "oauth-2025-04-20"}
    try:
        client = ssl_relax.get_client(CLAUDE_USAGE_URL)
        resp = await client.request("GET", CLAUDE_USAGE_URL, headers=headers, timeout=TIMEOUT)
    except httpx.HTTPError as e:
        raise QuotaError(f"网络请求失败: {str(e)[:120]}")
    if resp.status_code in (401, 403):
        raise QuotaError("登录已失效，请重新登录该账号", login_required=True)
    try:
        body = resp.json()
    except ValueError:
        raise QuotaError(f"上游返回异常（HTTP {resp.status_code}）")
    if not isinstance(body, dict):
        raise QuotaError(f"上游返回异常（HTTP {resp.status_code}）")
    usage = body
    if isinstance(body.get("data"), dict) and ("five_hour" in body["data"] or "seven_day" in body["data"]):
        usage = body["data"]
    metrics = _usage_window_metrics(usage, [("claude_5h", "five_hour"), ("claude_7d", "seven_day")])
    return {"plan": None, "subscription_end_at": None, "identity": None,
            "metrics": metrics, "checkin": None}


# ------------------------------------------------------------------ Codex 用量
# 移植 EchoBird src-tauri/src/services/codex_accounts.rs request_usage / 窗口解析

CODEX_USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
_CODEX_WINDOW_FIELDS = (
    "used_percent", "usedPercent", "used_percentage", "usedPercentage",
    "remaining_percent", "remainingPercent", "remaining_percentage", "remainingPercentage",
)


def _codex_window_metrics(rate_limit: dict) -> list:
    metrics = []
    for key, field, label in (("codex_5h", "primary_window", "5h"), ("codex_7d", "secondary_window", "7d")):
        node = rate_limit.get(field)
        if not isinstance(node, dict):
            continue
        percent = None
        used = _num(node.get("used_percent")) or _num(node.get("usedPercent")) \
            or _num(node.get("used_percentage")) or _num(node.get("usedPercentage"))
        if used is not None:
            percent = max(0.0, min(100.0, 100.0 - used))
        else:
            remaining = _num(node.get("remaining_percent")) or _num(node.get("remainingPercent")) \
                or _num(node.get("remaining_percentage")) or _num(node.get("remainingPercentage"))
            if remaining is not None:
                percent = max(0.0, min(100.0, remaining))
        if percent is None:
            continue
        reset_at = _ts(node.get("reset_at")) or _ts(node.get("resetAt"))
        if reset_at is None:
            after = _num(node.get("reset_after_seconds")) or _num(node.get("resetAfterSeconds"))
            if after is not None:
                reset_at = int(time.time() + after)
        metrics.append({
            "key": key, "value": f"{percent:.0f}%", "percent": round(percent, 1),
            "reset_at": reset_at, "remaining": None, "total": None, "label": label,
        })
    return metrics


async def _fetch_codex(api_key, provider) -> dict:
    try:
        token = decrypt_value(api_key.key_value)
    except Exception:
        token = ""
    if not token:
        raise QuotaError("凭据缺失，请重新登录该账号", login_required=True)
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
        "User-Agent": "Codex Desktop",
        "originator": "Codex Desktop",
        "x-openai-target-path": "/backend-api/wham/usage",
        "x-openai-target-route": "/backend-api/wham/usage",
    }
    try:
        client = ssl_relax.get_client(CODEX_USAGE_URL)
        resp = await client.request("GET", CODEX_USAGE_URL, headers=headers, timeout=TIMEOUT)
    except httpx.HTTPError as e:
        raise QuotaError(f"网络请求失败: {str(e)[:120]}")
    if resp.status_code in (401, 403):
        raise QuotaError("登录已失效，请重新登录该账号", login_required=True)
    try:
        body = resp.json()
    except ValueError:
        raise QuotaError(f"上游返回异常（HTTP {resp.status_code}）")
    rate_limit = body.get("rate_limit") if isinstance(body, dict) else None
    if not isinstance(rate_limit, dict):
        raise QuotaError("上游未返回用量窗口")
    return {"plan": None, "subscription_end_at": None, "identity": None,
            "metrics": _codex_window_metrics(rate_limit), "checkin": None}


# ------------------------------------------------------------------ 统一入口

async def _fetch_qoder(api_key, provider) -> dict:
    """Qoder / Qoder CN 额度（跨区探测 + 套餐名 + 共享额度归一）。

    物化的 echo ApiKey：device_token 在 oauth_refresh 的凭据 blob 中。
    """
    from app.services import qoder_quota
    platform = api_key.oauth_profile or "qoder"
    blob = {}
    if api_key.oauth_refresh:
        try:
            blob = json.loads(decrypt_value(api_key.oauth_refresh)) or {}
        except Exception:
            blob = {}
    token = str(blob.get("device_token") or blob.get("access_token")
                or (decrypt_value(api_key.key_value) if getattr(api_key, "key_value", "") else "") or "")
    creds = {**blob, "device_token": token}
    result = await qoder_quota.fetch_usage(creds, platform, timeout=TIMEOUT)
    if result.get("login_required"):
        raise QuotaError("登录已失效，请重新登录该账号", login_required=True)
    if not result.get("windows"):
        raise QuotaError(result.get("error") or "未获取到 Qoder 额度数据")
    metrics = []
    for w in result["windows"]:
        metrics.append({"key": "quota", "label": w.get("name") or "Credits",
                        "value": str(int(w["remaining"])) if w.get("remaining") is not None else "—",
                        "percent": (_num(w["remaining"]) / _num(w["total"]) * 100
                                    if w.get("total") and _num(w.get("total")) else None),
                        "reset_at": w.get("expires_at"),
                        "remaining": w.get("remaining"), "total": w.get("total")})
    return {"plan": result.get("plan") or None, "subscription_end_at": None,
            "identity": blob.get("email") or None, "metrics": metrics, "checkin": None}


_QUOTA_FETCHERS = {
    "workbuddy": _fetch_workbuddy,
    "workbuddy-intl": _fetch_workbuddy,
    "zcode": _fetch_zcode,
    "claude": _fetch_claude,
    "codex": _fetch_codex,
    "qoder": _fetch_qoder,
    "qodercn": _fetch_qoder,
}

SUPPORTED_QUOTA_PLATFORMS = tuple(_QUOTA_FETCHERS)


async def fetch_quota(api_key, provider) -> dict | None:
    """按平台取额度快照；平台无对应接口时返回 None。失败抛 QuotaError。"""
    platform = api_key.oauth_profile or ""
    fetcher = _QUOTA_FETCHERS.get(platform)
    if not fetcher:
        return None
    payload = await fetcher(api_key, provider)
    payload["fetched_at"] = int(time.time())
    return payload


def load_quota_cache(api_key) -> dict | None:
    """读取缓存额度（损坏时静默丢弃）。"""
    if not getattr(api_key, "quota_cache", ""):
        return None
    try:
        data = json.loads(api_key.quota_cache)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def save_quota_cache(api_key, payload: dict) -> None:
    api_key.quota_cache = json.dumps(payload, ensure_ascii=False)
    api_key.quota_fetched_at = int(payload.get("fetched_at") or time.time())
