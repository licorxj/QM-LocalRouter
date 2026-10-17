"""API Key 健康状态策略（失败分类 + 防抖 + 自动复活）。

设计原则：**网络抖动不等于 key 失效**。

- 网络类错误（连接被拒 / DNS 失败 / 超时 / TLS 失败）与上游 5xx 属于环境问题，
  只写 ``last_error`` 便于排查，**绝不修改 status**，也不累加鉴权失败计数。
- 只有鉴权类错误（401/403）才可能让 key 失效，且需要**连续**达到
  ``AUTH_FAIL_THRESHOLD`` 次才置为 ``inactive``；任意一次成功即清零计数。
- 429 只进入 ``rate_limited`` + ``cooldown_until`` 冷却窗口，冷却到期由
  :func:`app.services.balancer.Balancer.select_key` 自动复活，无需人工干预。
"""
import time
from datetime import datetime, timezone

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.api_key import ApiKey

# 连续鉴权失败多少次才把 key 标记为 inactive
AUTH_FAIL_THRESHOLD = 3
# 429 后的冷却秒数
RATE_LIMIT_COOLDOWN_SECONDS = 60

KIND_AUTH = "auth"
KIND_RATE_LIMIT = "rate_limit"
KIND_NETWORK = "network"
KIND_SERVER = "server"
KIND_OTHER = "other"

# 只有这些状态会被视为「key 本身的问题」
DISABLING_KINDS = (KIND_AUTH,)


def classify_status_code(status_code: int) -> str:
    """Map an HTTP status code to a failure kind."""
    try:
        code = int(status_code)
    except (TypeError, ValueError):
        return KIND_OTHER
    if code in (401, 403):
        return KIND_AUTH
    if code == 429:
        return KIND_RATE_LIMIT
    if code >= 500:
        return KIND_SERVER
    return KIND_OTHER


def classify_exception(exc: BaseException) -> str:
    """Map an exception raised while calling upstream to a failure kind."""
    if isinstance(exc, httpx.HTTPStatusError):
        return classify_status_code(exc.response.status_code)
    if isinstance(exc, httpx.RequestError):
        # ConnectError / ConnectTimeout / ReadTimeout / ProxyError ...
        return KIND_NETWORK
    text = str(exc).lower()
    network_markers = (
        "connect", "timeout", "timed out", "network", "dns", "unreachable",
        "connection", "ssl", "eof", "broken pipe", "reset by peer",
    )
    if any(m in text for m in network_markers):
        return KIND_NETWORK
    if "401" in text or "403" in text or "unauthorized" in text or "forbidden" in text:
        return KIND_AUTH
    return KIND_OTHER


def is_disabling_kind(kind: str) -> bool:
    """Whether this failure kind is allowed to eventually mark a key inactive."""
    return kind in DISABLING_KINDS


def revive_expired_cooldown(api_key: ApiKey, now: int | None = None) -> bool:
    """If a rate-limited key's cooldown has elapsed, bring it back to active.

    Returns True when the key was revived (caller should commit).
    """
    if api_key.status != "rate_limited":
        return False
    until = int(api_key.cooldown_until or 0)
    if until > int(now if now is not None else time.time()):
        return False
    api_key.status = "active"
    api_key.cooldown_until = 0
    api_key.last_failure_kind = ""
    api_key.last_error = None
    return True


async def record_key_success(api_key: ApiKey, db: AsyncSession) -> None:
    """Mark a successful call: activate the key and clear all failure state."""
    api_key.status = "active"
    api_key.fail_count = 0
    api_key.last_failure_kind = ""
    api_key.last_error = None
    api_key.cooldown_until = 0
    api_key.last_check_at = datetime.now(timezone.utc)
    await db.commit()


async def record_key_failure(
    api_key: ApiKey,
    db: AsyncSession,
    kind: str,
    message: str = "",
    status_code: int | None = None,
) -> str:
    """Apply the failure policy and persist. Returns the resulting status.

    - auth: increment the consecutive counter; only mark ``inactive`` once the
      threshold is reached (single 401/403 no longer kills a key).
    - rate_limit: ``rate_limited`` + cooldown window (auto-revived).
    - network / server / other: record ``last_error`` only, status untouched.
    """
    now = datetime.now(timezone.utc)
    api_key.last_check_at = now
    api_key.last_failure_kind = kind
    api_key.last_error = (message or (f"HTTP {status_code}" if status_code else ""))[:500]

    status = api_key.status or "active"
    if kind == KIND_AUTH:
        api_key.fail_count = int(api_key.fail_count or 0) + 1
        if api_key.fail_count >= AUTH_FAIL_THRESHOLD:
            status = "inactive"
    elif kind == KIND_RATE_LIMIT:
        status = "rate_limited"
        api_key.cooldown_until = int(time.time()) + RATE_LIMIT_COOLDOWN_SECONDS
    # network / server / other: 环境问题，仅记录，不改状态、不计入鉴权失败

    api_key.status = status
    await db.commit()
    return status