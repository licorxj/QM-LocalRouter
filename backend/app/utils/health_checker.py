"""
API Key health check utilities.
"""
import time
import httpx
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.api_key import ApiKey
from app.models.provider import Provider
from app.utils.crypto import decrypt_value


async def check_key_health(db: AsyncSession, api_key: ApiKey, provider: Provider) -> dict:
    """Test if an API key is working. Returns {success, message, latency_ms}."""
    real_key = decrypt_value(api_key.key_value)
    start = time.monotonic()

    try:
        if str(provider.auth_type or "").startswith("workbuddy"):
            # WorkBuddy 没有 /models 端点，用官方配置接口验证凭据
            from app.services import workbuddy
            from app.services.oauth_providers import ensure_fresh_token
            await ensure_fresh_token(db, api_key)
            credential = workbuddy.load_credential(api_key, real_key)
            region = workbuddy.resolve_region(credential, provider.base_url or "")
            await workbuddy.fetch_catalog(region, credential)
            elapsed = int((time.monotonic() - start) * 1000)
            return {"success": True, "message": "OK", "latency_ms": elapsed}

        if str(provider.auth_type or "") == "opencode":
            # OpenCode：本地服务端健康检查 /global/health（Basic 认证）
            from app.services import opencode as opencode_svc
            client = opencode_svc.OpenCodeClient(provider.base_url or opencode_svc.DEFAULT_BASE_URL, real_key)
            elapsed = int((time.monotonic() - start) * 1000)
            try:
                healthy = await client.health()
            except Exception as e:
                return {"success": False, "message": str(e)[:200], "latency_ms": elapsed}
            return {"success": healthy, "message": "OK" if healthy else "服务未就绪", "latency_ms": elapsed}

        if str(provider.auth_type or "") == "cline":
            # Cline：拉取模型目录（GET /models）作为可达性健康检查
            from app.services import cline as cline_svc
            client = cline_svc.ClineClient(provider.base_url or cline_svc.CLINE_API_BASE, real_key)
            elapsed = int((time.monotonic() - start) * 1000)
            try:
                ok = await client.health()
            except Exception as e:
                return {"success": False, "message": str(e)[:200], "latency_ms": elapsed}
            return {"success": ok, "message": "OK" if ok else "Cline 上游不可达", "latency_ms": elapsed}

        if provider.protocol == "openai":
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(
                    f"{provider.base_url.rstrip('/')}/models",
                    headers={"Authorization": f"Bearer {real_key}"},
                )
                elapsed = int((time.monotonic() - start) * 1000)
                if resp.status_code == 200:
                    return {"success": True, "message": "OK", "latency_ms": elapsed}
                return {"success": False, "message": f"HTTP {resp.status_code}", "latency_ms": elapsed}

        elif provider.protocol == "claude":
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    f"{provider.base_url.rstrip('/')}/messages",
                    headers={
                        "x-api-key": real_key,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json={"model": "claude-3-haiku-20240307", "max_tokens": 1, "messages": [{"role": "user", "content": "hi"}]},
                )
                elapsed = int((time.monotonic() - start) * 1000)
                if resp.status_code == 200:
                    return {"success": True, "message": "OK", "latency_ms": elapsed}
                return {"success": False, "message": f"HTTP {resp.status_code}", "latency_ms": elapsed}

        elif provider.protocol == "gemini":
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(
                    f"{provider.base_url.rstrip('/')}/models?key={real_key}",
                )
                elapsed = int((time.monotonic() - start) * 1000)
                if resp.status_code == 200:
                    return {"success": True, "message": "OK", "latency_ms": elapsed}
                return {"success": False, "message": f"HTTP {resp.status_code}", "latency_ms": elapsed}

        return {"success": False, "message": "Unsupported protocol"}

    except Exception as e:
        elapsed = int((time.monotonic() - start) * 1000)
        return {"success": False, "message": str(e)[:200], "latency_ms": elapsed}


async def batch_check_keys(db: AsyncSession, provider_id: int) -> list[dict]:
    """Check all keys for a provider. Returns list of {key_id, success, message, latency_ms}."""
    result = await db.execute(
        select(ApiKey).where(ApiKey.provider_id == provider_id, ApiKey.status == "active")
    )
    keys = result.scalars().all()
    provider = await db.get(Provider, provider_id)
    if not provider:
        return []

    results = []
    for key in keys:
        check = await check_key_health(db, key, provider)
        if check["success"]:
            key.status = "active"
            key.last_error = None
        else:
            key.status = "inactive"
            key.last_error = check["message"]
        await db.commit()
        results.append({"key_id": key.id, **check})

    return results
