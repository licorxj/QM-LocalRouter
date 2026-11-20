"""Qoder 账号源（EchoBird 存储 ~/.echobird/{qoder,qodercn}-accounts.json）。

供脚本插件 qoder-claim 的执行弹窗展示可选账号（脱敏，无 token），
批量执行时按 id 定位账号。Qoder 账号不在 api_keys 表，而在 EchoBird 本地
存储，所以这里复用 native_accounts.list_echo_accounts 读取，绝不外泄凭据。

账号 id 编码为 ``"{platform}|{account_id}"``，插件据此解析 platform 与 account_id；
明文凭据由插件在进程内通过 app.services.native_accounts 自行获取。
"""
import os

DBG = os.environ.get("QODER_ACCOUNTS_DEBUG") == "1"

# 延迟导入，避免模块加载期触碰用户目录
def _list(platform: str) -> list[dict]:
    from app.services import native_accounts as na
    try:
        return na.list_echo_accounts(platform)
    except Exception:
        return []


def list_accounts() -> list[dict]:
    """返回脱敏的 Qoder 账号：id / name / platform / account_id / has_token。"""
    out: list[dict] = []
    for platform in ("qoder", "qodercn"):
        for entry in _list(platform):
            creds = (entry.get("extra") or {}).get("creds") or {}
            has_token = bool(creds.get("device_token")
                             or creds.get("access_token"))
            account_id = str(entry.get("account_id") or "")
            if not account_id:
                continue
            summary = (entry.get("extra") or {}).get("summary") or {}
            name = str(entry.get("identity") or "") \
                or str(summary.get("name") or "") or account_id[:10]
            out.append({
                "id": f"{platform}|{account_id}",
                "name": name,
                "platform": platform,
                "account_id": account_id,
                "plan": summary.get("plan") or "",
                "has_token": has_token,
            })
    return out


def parse_id(account_ref: str) -> tuple[str, str]:
    """把账号源 id 解析为 (platform, account_id)。兼容直接传 account_id（默认 qodercn）。"""
    ref = str(account_ref or "")
    if "|" in ref:
        platform, account_id = ref.split("|", 1)
        if platform in ("qoder", "qodercn") and account_id:
            return platform, account_id
    return "qodercn", ref
