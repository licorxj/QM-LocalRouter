"""WorkBuddy 本地账号源（项目内已登录账号）。

账号来自 EchoBird 客户端的本地账号存储：
    ~/.echobird/workbuddy-accounts/<account_id>.json
每个文件是明文 JSON，关键字段：
    summary.id / summary.name / summary.plan / summary.expiresAt
    session.auth.token        -> Bearer token
    session.account.uid       -> X-User-Id
    session.auth.domain       -> X-Domain
    session.auth.refreshToken -> 刷新用（本模块不主动刷新）

对外只暴露脱敏账号列表（不含 token）；凭据解析函数供批量执行时按 id 取用，
且只读取 edition=="workbuddy" 的账号（排除 workbuddyai）。
"""
import glob
import json
import os

_ECHO_ACCOUNTS_DIR = os.path.join(os.path.expanduser("~"), ".echobird", "workbuddy-accounts")


def _account_dir() -> str:
    """允许测试/部署通过环境变量覆盖账号目录。"""
    return os.environ.get("WORKBUDDY_ACCOUNTS_DIR") or _ECHO_ACCOUNTS_DIR


def _iter_account_files():
    pattern = os.path.join(_account_dir(), "*.json")
    for path in sorted(glob.glob(pattern)):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        yield path, data


def _is_workbuddy(data: dict) -> bool:
    edition = str(data.get("edition") or (data.get("summary") or {}).get("edition") or "")
    return edition == "workbuddy"


def _summary(data: dict) -> dict:
    s = data.get("summary")
    return s if isinstance(s, dict) else {}


def list_accounts() -> list[dict]:
    """返回脱敏账号列表（无 token/refreshToken），供前端勾选。"""
    out = []
    for _path, data in _iter_account_files():
        if not _is_workbuddy(data):
            continue
        s = _summary(data)
        account_id = str(s.get("id") or "")
        if not account_id:
            continue
        sess = data.get("session") or {}
        auth = sess.get("auth") or {}
        acc = sess.get("account") or {}
        out.append({
            "id": account_id,
            "name": str(s.get("name") or acc.get("nickname") or account_id[:8]),
            "plan": str(s.get("plan") or ""),
            "expires_at": s.get("expiresAt") or auth.get("expiresAt") or 0,
            "has_token": bool(auth.get("accessToken")),
        })
    return out


def resolve_credentials(account_id: str) -> dict:
    """按 id 解析明文凭据（仅内部批量执行调用，不对外返回）。

    返回 {token, uid, domain, enterprise_id}；找不到或缺 token 抛 ValueError。
    """
    safe_id = os.path.basename(str(account_id or ""))
    if not safe_id:
        raise ValueError("无效账号 id")
    path = os.path.join(_account_dir(), safe_id + ".json")
    if not os.path.isfile(path):
        raise ValueError("账号不存在：%s" % safe_id[:8])
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception as exc:
        raise ValueError("账号文件读取失败：%s" % type(exc).__name__)
    if not _is_workbuddy(data):
        raise ValueError("账号类型不是 workbuddy")
    sess = data.get("session") or {}
    auth = sess.get("auth") or {}
    acc = sess.get("account") or {}
    token = str(auth.get("accessToken") or "")
    uid = str(acc.get("uid") or "")
    if not token or not uid:
        raise ValueError("账号缺少 token 或 uid（可能已登出）")
    return {
        "token": token,
        "uid": uid,
        "domain": str(auth.get("domain") or "www.codebuddy.cn"),
        "enterprise_id": str(acc.get("enterpriseId") or acc.get("enterprise_id") or ""),
    }
