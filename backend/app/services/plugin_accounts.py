"""插件账号绑定层（移植 EchoBird plugin_accounts/bindings.rs）。

宿主侧负责构造每个 operation 的 params（插件端只收完整 {"operation","params"}）：
- accounts.saved → {}                      返回账号数组（camelCase 字段）
- login.start → {}（claudedesktop 为 {"next"}）返回 {loginId, verificationUri|authorizationUrl, expiresAt}
- login.poll / login.cancel → {"loginId"}   （application 由宿主 LOGINS 表反查）
- login.complete → {"loginId","code"}       （仅 claudecode）
- account.refresh / account.claim / account.remove → {"accountId"}

账号 JSON 按平台族归一为账号接入页的卡片指标（metrics）。
"""
import time

from app.services import plugin_host
from app.services.plugin_host import PluginError

# 平台族 → 卡片指标解析
_CLAUDE_APPS = {"claudecode", "claudedesktop"}
_WORKBUDDY_APPS = {"workbuddy", "workbuddyai"}
_TRAE_APPS = {"trae", "traecn"}
_DOUBAO_APPS = {"doubaowork", "dola"}
_CLAIMABLE_APPS = _WORKBUDDY_APPS | _TRAE_APPS | _DOUBAO_APPS


def account_application(account: dict) -> str:
    """插件账号 JSON 的 application 归属（edition 字段优先，如 workbuddy/workbuddyai）。"""
    edition = str(account.get("edition") or "").strip()
    if edition:
        return edition
    return ""


async def list_accounts(application: str) -> list[dict]:
    data = await plugin_host.call(application, "accounts.saved", {})
    return data if isinstance(data, list) else []


async def start_login(application: str, next_uri: str = "") -> dict:
    params = {"next": next_uri} if application == "claudedesktop" else {}
    result = await plugin_host.call(application, "login.start", params)
    if not isinstance(result, dict) or not result.get("loginId"):
        raise PluginError("login.start returned no loginId", code="invalid")
    login_id = str(result["loginId"])
    plugin_host.register_login(login_id, application)
    return {
        "session_id": login_id,
        "login_id": login_id,
        "application": application,
        "verification_uri": result.get("verificationUri") or result.get("verification_uri") or "",
        "authorize_url": result.get("authorizationUrl") or result.get("authorization_url") or "",
        "expires_at": result.get("expiresAt") or result.get("expires_at") or 0,
        "awaiting_client_exit": application == "claudedesktop",
    }


async def poll_login(login_id: str) -> dict:
    application = plugin_host.login_application(login_id)
    if not application:
        return {"status": "expired"}
    try:
        result = await plugin_host.call(application, "login.poll", {"loginId": login_id})
    except PluginError as e:
        return {"status": "failed", "error": e.message}
    if application == "claudedesktop":
        # {account, awaitingClientExit, expiresAt, cleared}
        if isinstance(result, dict) and result.get("account"):
            plugin_host.forget_login(login_id)
            return {"status": "success", "account": result["account"], "application": application,
                    "cleared": bool(result.get("cleared"))}
        return {"status": "pending", "awaiting_client_exit": bool(isinstance(result, dict) and result.get("awaitingClientExit")),
                "application": application}
    if result:  # Option<Account>：非空即登录成功
        plugin_host.forget_login(login_id)
        return {"status": "success", "account": result, "application": application}
    return {"status": "pending", "application": application}


async def complete_login(login_id: str, code: str) -> dict:
    application = plugin_host.login_application(login_id) or "claudecode"
    result = await plugin_host.call(application, "login.complete", {"loginId": login_id, "code": code})
    plugin_host.forget_login(login_id)
    return {"status": "success", "account": result, "application": application}


async def cancel_login(login_id: str) -> None:
    application = plugin_host.login_application(login_id)
    if application:
        try:
            await plugin_host.call(application, "login.cancel", {"loginId": login_id})
        except PluginError:
            pass
    plugin_host.forget_login(login_id)


async def refresh_account(application: str, account_id: str) -> dict:
    return await plugin_host.call(application, "account.refresh", {"accountId": account_id})


async def claim_account(application: str, account_id: str) -> dict:
    return await plugin_host.call(application, "account.claim", {"accountId": account_id})


async def remove_account(application: str, account_id: str) -> None:
    await plugin_host.call(application, "account.remove", {"accountId": account_id})


# ---------------------------------------------------------------------------
# 插件账号 JSON → 账号接入页卡片（与 account_hub.account_card 字段对齐）

def _pct(remaining, total):
    try:
        remaining = float(remaining)
        total = float(total)
    except (TypeError, ValueError):
        return None
    if total <= 0:
        return None
    return round(max(0.0, min(100.0, remaining / total * 100)), 1)


def _fmt(value) -> str:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return ""
    if abs(v - round(v)) < 1e-9:
        return str(int(round(v)))
    return f"{v:,.2f}".rstrip("0").rstrip(".")


def _metric(key: str, remaining=None, total=None, reset_at=None, percent=None) -> dict:
    if percent is None:
        percent = _pct(remaining, total)
    return {"key": key, "value": _fmt(remaining), "remaining": remaining, "total": total,
            "percent": percent, "reset_at": reset_at}


def account_metrics(application: str, account: dict) -> list[dict]:
    """按平台族解析 metrics（字段名与 EchoBird bindings 结构体对齐）。"""
    metrics: list[dict] = []
    if application in _CLAUDE_APPS:
        for key, field in (("claude_5h", "fiveHour"), ("claude_7d", "sevenDay")):
            node = account.get(field)
            if isinstance(node, dict):
                percent = node.get("remainingPercent")
                metrics.append(_metric(key, percent=percent, reset_at=node.get("resetAt")))
        return metrics
    if application in _WORKBUDDY_APPS or application in _TRAE_APPS:
        if account.get("baseRemaining") is not None or account.get("baseTotal") is not None:
            metrics.append(_metric("base", account.get("baseRemaining"), account.get("baseTotal"),
                                   account.get("baseResetAt") or account.get("base_reset_at")))
        if account.get("rewardRemaining") is not None or account.get("rewardTotal") is not None:
            metrics.append(_metric("reward", account.get("rewardRemaining"), account.get("rewardTotal"),
                                   account.get("rewardNextExpiryAt")))
        if account.get("addonRemaining") is not None:
            metrics.append(_metric("addon", account.get("addonRemaining"), account.get("addonTotal")))
    if application in _TRAE_APPS or application in _DOUBAO_APPS or application in _WORKBUDDY_APPS:
        # EchoBird 卡片只展示套餐/奖励分账；无分账数据时才回退到汇总额度
        if not metrics and (account.get("remaining") is not None or account.get("quota") is not None):
            remaining = account.get("remaining") if account.get("remaining") is not None else account.get("quota")
            metrics.append(_metric("quota", remaining, account.get("total"),
                                   account.get("expiresAt") or account.get("subscriptionEndAt")))
    return metrics


def account_card(application: str, account: dict, platform_label: str) -> dict:
    account_id = str(account.get("id") or "")
    identity = account.get("email") or account.get("name") or account.get("nickname") or account_id
    daily_claimed_at = account.get("dailyClaimedAt") or account.get("dailyReward") or 0
    # EchoBird 的 active 是「客户端当前使用的账号」标记，不是停用状态；
    # 插件账号一律视为正常，避免误显示「已停用」
    return {
        "id": f"plugin:{application}:{account_id}",
        "platform": application,
        "platform_label": platform_label,
        "provider_id": 0,
        "provider_name": "plugin",
        "identity": str(identity),
        "alias": "",
        "plan": account.get("plan"),
        "status": "active",
        "token_expires_at": int(account.get("expiresAt") or account.get("subscriptionEndAt") or 0),
        "quota": {"metrics": account_metrics(application, account), "checkin": None,
                  "fetched_at": int(time.time())},
        "quota_fetched_at": int(time.time()),
        "supports_quota": True,
        "supports_claim": application in _CLAIMABLE_APPS,
        "via_plugin": True,
        "application": application,
        "account_id": account_id,
        "is_active_account": bool(account.get("active")) if account.get("active") is not None else None,
        "daily_claimed_at": int(daily_claimed_at) if isinstance(daily_claimed_at, (int, float)) else 0,
        "last_error": "",
    }
