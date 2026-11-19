"""Qoder / Qoder CN 每日签到（移植 EchoBird qoder_accounts/campaigns.rs）。

端点：GET  {openapi}/sash/api/v1/me/campaigns
      POST {openapi}/sash/api/v1/me/campaigns/{campaign_id}/claim
鉴权：Bearer <device_token>（**不是** job token；job token 仅对话网关/模型目录用）
固定头：Cosy-ClientType=10 / User-Agent=Qoder / Accept=application/json

与上游的差异：EchoBird 的 RequestIdentity::load 会从本地安装的 Qoder 桌面客户端
进程提取 cosy-machinetoken / cosy-machinetype / cosy-machinecode / cosy-version。
本项目是纯 HTTP 移植，没有该 native 层；这些头在 campaigns.rs 中均为 Option，
故这里从凭据 blob 中可选读取（machine_token/machine_type/machine_code/client_version），
缺失时不发送，仅强制发送 cosy-machineid（来自 machine_id）。
"""
import json
import time
from urllib.parse import quote, urlparse

import httpx

from app.utils import ssl_relax
from app.utils.crypto import decrypt_value

CAMPAIGNS_PATH = "/sash/api/v1/me/campaigns"
BASE_URLS = {
    "qoder": "https://openapi.qoder.sh",
    "qodercn": "https://openapi.qoder.com.cn",
}
DAILY_HOSTS = {
    "qoder": "docs.qoder.com",
    "qodercn": "docs.qoder.cn",
}
VERSION = "1.1.49"

_STATUS_CLAIMABLE = "claimable"
_STATUS_CLAIMED = "claimed"
_STATUS_UNAVAILABLE = "unavailable"


class CampaignError(Exception):
    """签到失败（message 面向用户展示）。"""

    def __init__(self, message: str, login_required: bool = False):
        super().__init__(message)
        self.message = message
        self.login_required = login_required


def next_reset(now: int) -> int:
    """对齐 campaigns.rs::next_reset：下一个北京时间 10:00 的时间戳。"""
    return (now - 7200) // 86400 * 86400 + 93600


def _num(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.strip())
        except ValueError:
            return None
    return None


def _os() -> str:
    import os as _os_mod
    if _os_mod.name == "nt":
        return "win32"
    import sys as _sys
    if _sys.platform == "darwin":
        return "darwin"
    return "linux"


def _arch() -> str:
    import platform
    return "arm64" if (platform.machine() or "").lower() in ("arm64", "aarch64") else "x64"


def _campaign_headers(creds: dict) -> dict:
    """构造 campaign 请求头（对齐 RequestIdentity::from_native + bind_machine）。"""
    headers = {
        "Accept": "application/json",
        "Cosy-ClientType": "10",
        "User-Agent": "Qoder",
    }
    machine = str(creds.get("machine_id") or "")
    if machine:
        headers["cosy-machineid"] = machine
    # native 可选头：本项目无桌面客户端 native 层，仅在凭据里存在时发送
    for key, header in (
        ("machine_token", "cosy-machinetoken"),
        ("machine_type", "cosy-machinetype"),
        ("machine_code", "cosy-machinecode"),
        ("client_version", "cosy-version"),
    ):
        value = creds.get(key)
        if value:
            headers[header] = str(value)
    headers["cosy-machineos"] = f"{_arch()}_{_os()}"
    token = str(creds.get("device_token") or "")
    if token:
        headers["Authorization"] = "Bearer " + token
    return headers


def _creds_from_api_key(api_key) -> dict:
    raw = getattr(api_key, "oauth_refresh", "") or ""
    if not raw:
        return {}
    try:
        parsed = json.loads(decrypt_value(raw))
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _daily_link(row: dict, platform: str) -> bool:
    """校验活动 placements 中含官方 /events/100credits 深链（防伪造活动）。"""
    host = DAILY_HOSTS.get(platform)
    if not host:
        return False
    for placement in row.get("placements") or []:
        if not isinstance(placement, dict):
            continue
        content = placement.get("content") or {}
        if not isinstance(content, dict):
            continue
        for locale in ("zh", "en"):
            link = (content.get(locale) or {}).get("detailUrl")
            if not isinstance(link, str) or not link:
                continue
            try:
                u = urlparse(link)
            except ValueError:
                continue
            if (u.scheme == "https" and u.hostname == host and u.port is None
                    and not u.username and not u.password and not u.query and not u.fragment
                    and u.path.endswith("/events/100credits")):
                return True
    return False


def _daily_campaign(row: dict, platform: str) -> bool:
    """对齐 campaigns.rs::daily_campaign：CLAIM_BENEFIT / CREDITS 100 / ALL_MODELS / 30 天。"""
    if not isinstance(row, dict):
        return False
    if row.get("actionType") != "CLAIM_BENEFIT":
        return False
    benefit = row.get("benefit") or {}
    if benefit.get("kind") != "CREDITS" or _num(benefit.get("amount")) != 100.0:
        return False
    if ((benefit.get("modelScope") or {}).get("modelSeries") or {}).get("key") != "ALL_MODELS":
        return False
    validity = benefit.get("validity") or {}
    if validity.get("mode") != "RELATIVE_DAYS" or _num(validity.get("days")) != 30:
        return False
    return _daily_link(row, platform)


def _extract_campaigns(value) -> list | None:
    if isinstance(value, list):
        return value
    if not isinstance(value, dict):
        return None
    for candidate in (
        value.get("campaigns"),
        (value.get("data") or {}).get("campaigns") if isinstance(value.get("data"), dict) else None,
        value.get("data"),
    ):
        if isinstance(candidate, list):
            return candidate
    return None


def _parse(value, platform: str, now: int) -> dict:
    """对齐 campaigns.rs::parse，返回 {status, campaign_id, reset_at}。"""
    unavailable = {"status": _STATUS_UNAVAILABLE, "campaign_id": None, "reset_at": next_reset(now)}
    if isinstance(value, dict) and value.get("showCampaign") is False:
        return unavailable
    campaigns = _extract_campaigns(value)
    if campaigns is None:
        raise CampaignError("签到活动返回格式异常")
    selected = None
    for row in campaigns:
        if not _daily_campaign(row, platform):
            continue
        start = _num(row.get("startAt"))
        end = _num(row.get("endAt"))
        if start is None or end is None or start <= 0 or end <= start:
            raise CampaignError("签到活动返回格式异常")
        if now < start or now >= end:
            continue
        cid = str(row.get("campaignId") or "").strip()
        if not cid:
            raise CampaignError("签到活动返回格式异常")
        if selected is not None:
            raise CampaignError("签到活动返回格式异常（多个有效活动）")
        status = str(row.get("claimStatus") or "").upper()
        state = _STATUS_CLAIMABLE if status == "CLAIMABLE" else (
            _STATUS_CLAIMED if status == "CLAIMED" else _STATUS_UNAVAILABLE)
        selected = {
            "status": state,
            "campaign_id": cid if state == _STATUS_CLAIMABLE else None,
            "reset_at": min(end, next_reset(now)),
        }
    return selected or unavailable


async def _request(method: str, url: str, creds: dict, body: dict | None = None):
    try:
        client = ssl_relax.get_client(url)
        resp = await client.request(method, url, json=body, headers=_campaign_headers(creds), timeout=20)
    except httpx.HTTPError as e:
        raise CampaignError("网络请求失败: " + str(e)[:120])
    if resp.status_code in (401, 403):
        raise CampaignError("登录已失效，请重新登录该账号", login_required=True)
    if not resp.is_success:
        raise CampaignError(f"签到接口返回 HTTP {resp.status_code}")
    try:
        return resp.json()
    except ValueError:
        raise CampaignError("签到接口返回非 JSON")


async def fetch_reward(creds: dict, platform: str, now: int | None = None) -> dict:
    """拉取每日奖励资格（不执行领取）。"""
    if platform not in BASE_URLS:
        raise CampaignError("该平台不支持每日签到")
    if not creds.get("device_token"):
        raise CampaignError("凭据缺失，请重新登录该账号", login_required=True)
    value = await _request("GET", BASE_URLS[platform] + CAMPAIGNS_PATH, creds)
    return _parse(value, platform, int(now if now is not None else time.time()))


async def _claim(creds: dict, platform: str, campaign_id: str) -> None:
    if not campaign_id.strip():
        raise CampaignError("签到活动 ID 无效")
    cid = quote(campaign_id, safe="")
    url = f"{BASE_URLS[platform]}{CAMPAIGNS_PATH}/{cid}/claim"
    value = await _request("POST", url, creds, body={})
    node = value.get("data") if isinstance(value, dict) and isinstance(value.get("data"), dict) else value
    if not (isinstance(node, dict) and str(node.get("status") or "").upper() == "CLAIMED"):
        raise CampaignError("签到结果未确认，请稍后重试")


async def claim_with_creds(creds: dict, platform: str) -> dict:
    """按凭据签到（echo 账号与 ApiKey 账号共用的底层入口）。

    返回 {"claimed", "already", "status", "reset_at"}。
    """
    if platform not in BASE_URLS:
        raise CampaignError("仅 Qoder / Qoder CN 支持每日签到")
    if not creds.get("device_token"):
        raise CampaignError("凭据缺失，请重新登录该账号", login_required=True)
    eligibility = await fetch_reward(creds, platform)
    state = eligibility["status"]
    if state == _STATUS_CLAIMED:
        return {"claimed": False, "already": True, "status": state, "reset_at": eligibility["reset_at"]}
    if state != _STATUS_CLAIMABLE:
        raise CampaignError("签到活动未开放或今日已领取完毕")
    await _claim(creds, platform, eligibility["campaign_id"])
    return {"claimed": True, "already": False, "status": _STATUS_CLAIMED,
            "reset_at": eligibility["reset_at"]}


async def status_with_creds(creds: dict, platform: str) -> dict | None:
    """只读查询今日签到状态（creds 入口），异常静默返回 None。"""
    if platform not in BASE_URLS:
        return None
    if not creds.get("device_token"):
        return None
    try:
        reward = await fetch_reward(creds, platform)
    except Exception:
        return None
    claimed = reward.get("status") == _STATUS_CLAIMED
    return {
        "claimed": claimed,
        # 上游只给"今日是否已签"，claimed_at 用查询时刻近似；前端仅按自然日判断
        "claimed_at": int(time.time()) if claimed else 0,
        "reset_at": reward.get("reset_at"),
    }


async def claim_daily(api_key, provider) -> dict:
    """ApiKey 单账号签到入口。"""
    platform = str(getattr(api_key, "oauth_profile", "") or "")
    return await claim_with_creds(_creds_from_api_key(api_key), platform)


async def fetch_status(api_key) -> dict | None:
    """ApiKey 只读签到状态（供账号卡片展示）。"""
    platform = str(getattr(api_key, "oauth_profile", "") or "")
    return await status_with_creds(_creds_from_api_key(api_key), platform)
