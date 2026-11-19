from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from app.database import get_db
from app.models.api_key import ApiKey
from app.models.provider import Provider
from app.schemas.schemas import ApiKeyCreate, ApiKeyUpdate, ApiKeyOut, ApiKeyTestResult, ApiKeyBatchCreate, ApiKeyBatchCreateResult, BatchTestResult, BatchDeleteResult
from app.utils.crypto import encrypt_value, decrypt_value, mask_key
from app.utils.key_health import (
    KIND_AUTH,
    KIND_NETWORK,
    classify_exception,
    classify_status_code,
    record_key_failure,
    record_key_success,
)
import asyncio
import httpx
import json
import time
from datetime import datetime, timezone

router = APIRouter(prefix="/api", tags=["api-keys"])


def _key_to_out(key: ApiKey) -> dict:
    """Convert ApiKey model to output dict with masked key."""
    masked = ""
    decrypted = ""
    if key.key_value:
        try:
            real = decrypt_value(key.key_value)
            masked = mask_key(real)
            decrypted = real
        except Exception:
            masked = "****"
    return ApiKeyOut(
        id=key.id,
        provider_id=key.provider_id,
        key_masked=masked,
        key_value=decrypted,
        alias=key.alias or "",
        status=key.status or "active",
        weight=key.weight or 1,
        oauth_profile=key.oauth_profile or "",
        oauth_expires_at=key.oauth_expires_at or 0,
        last_used_at=key.last_used_at,
        last_error=key.last_error or "",
        fail_count=key.fail_count or 0,
        last_failure_kind=key.last_failure_kind or "",
        cooldown_until=key.cooldown_until or 0,
        created_at=key.created_at,
    ).model_dump()


@router.get("/providers/{provider_id}/keys", response_model=list[ApiKeyOut])
async def list_keys(provider_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(ApiKey).where(ApiKey.provider_id == provider_id).order_by(ApiKey.id)
    )
    keys = result.scalars().all()
    return [_key_to_out(k) for k in keys]


@router.post("/api-keys", response_model=ApiKeyOut, status_code=201)
async def create_key(data: ApiKeyCreate, db: AsyncSession = Depends(get_db)):
    provider = await db.get(Provider, data.provider_id)
    if not provider:
        raise HTTPException(404, "Provider not found")
    key = ApiKey(
        provider_id=data.provider_id,
        key_value=encrypt_value(data.key_value),
        alias=data.alias,
        weight=data.weight,
        status="untested",
    )
    db.add(key)
    await db.commit()
    await db.refresh(key)
    return _key_to_out(key)


@router.post("/providers/{provider_id}/keys/batch", response_model=ApiKeyBatchCreateResult, status_code=201)
async def create_keys_batch(provider_id: int, data: ApiKeyBatchCreate, db: AsyncSession = Depends(get_db)):
    """Create multiple keys at once with auto-numbered aliases (prefix-N).

    Empty/whitespace lines and duplicate keys (within the batch or already
    stored for this provider) are skipped. Numbering continues from the
    provider's existing key count.
    """
    provider = await db.get(Provider, provider_id)
    if not provider:
        raise HTTPException(404, "Provider not found")

    # Clean input: strip whitespace, drop empties, dedupe preserving order
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in data.keys:
        k = raw.strip()
        if k and k not in seen:
            cleaned.append(k)
            seen.add(k)
    if not cleaned:
        raise HTTPException(400, "No valid keys provided")

    # Skip keys identical to existing ones (need decryption to compare)
    duplicates = 0
    existing_values: set[str] = set()
    result = await db.execute(select(ApiKey).where(ApiKey.provider_id == provider_id))
    for existing_key in result.scalars():
        try:
            existing_values.add(decrypt_value(existing_key.key_value))
        except Exception:
            pass
    new_keys = [k for k in cleaned if k not in existing_values]
    duplicates = len(cleaned) - len(new_keys)
    if not new_keys:
        return ApiKeyBatchCreateResult(created=[], duplicates=duplicates)

    result = await db.execute(select(func.count(ApiKey.id)).where(ApiKey.provider_id == provider_id))
    existing_count = result.scalar() or 0

    prefix = (data.alias_prefix or "Key").strip() or "Key"
    created: list[ApiKey] = []
    for i, kv in enumerate(new_keys):
        key = ApiKey(
            provider_id=provider_id,
            key_value=encrypt_value(kv),
            alias=f"{prefix}-{existing_count + i + 1}",
            weight=data.weight,
            status="untested",
        )
        db.add(key)
        created.append(key)
    await db.commit()
    for k in created:
        await db.refresh(k)
    return ApiKeyBatchCreateResult(created=[_key_to_out(k) for k in created], duplicates=duplicates)


@router.put("/api-keys/{key_id}", response_model=ApiKeyOut)
async def update_key(key_id: int, data: ApiKeyUpdate, db: AsyncSession = Depends(get_db)):
    key = await db.get(ApiKey, key_id)
    if not key:
        raise HTTPException(404, "API Key not found")
    for k, v in data.model_dump(exclude_unset=True).items():
        setattr(key, k, v)
    # 手动启用/复位时一并清掉失败计数与冷却
    if data.status and data.status in ("active", "untested"):
        key.fail_count = 0
        key.cooldown_until = 0
        key.last_failure_kind = ""
        key.last_error = None
    await db.commit()
    await db.refresh(key)
    return _key_to_out(key)


@router.delete("/api-keys/{key_id}", status_code=204)
async def delete_key(key_id: int, db: AsyncSession = Depends(get_db)):
    key = await db.get(ApiKey, key_id)
    if not key:
        raise HTTPException(404, "API Key not found")
    await db.delete(key)
    await db.commit()


@router.delete("/providers/{provider_id}/keys/invalid", response_model=BatchDeleteResult)
async def delete_invalid_keys(provider_id: int, db: AsyncSession = Depends(get_db)):
    """Delete all inactive/invalid keys for a provider."""
    result = await db.execute(
        select(ApiKey).where(
            ApiKey.provider_id == provider_id,
            ApiKey.status.in_(["inactive", "expired", "rate_limited"])
        )
    )
    keys = result.scalars().all()
    count = len(keys)
    for k in keys:
        await db.delete(k)
    await db.commit()
    return BatchDeleteResult(deleted=count)


# 批量测试并发上限：显著提速的同时避免一次性打爆上游触发限流
KEY_TEST_CONCURRENCY = 10


@router.post("/providers/{provider_id}/keys/test-all", response_model=BatchTestResult)
async def test_all_keys(provider_id: int, db: AsyncSession = Depends(get_db)):
    """Test all keys for a provider."""
    result = await db.execute(
        select(ApiKey).where(ApiKey.provider_id == provider_id).order_by(ApiKey.id)
    )
    keys = result.scalars().all()
    if not keys:
        return BatchTestResult(total=0, success=0, failed=0, results=[])

    provider = await db.get(Provider, provider_id)
    if not provider:
        raise HTTPException(404, "Provider not found")

    # 并发探测所有 key（信号量限流），共享一个 httpx client；
    # 探测完成后统一记账写库
    sem = asyncio.Semaphore(KEY_TEST_CONCURRENCY)

    async def bounded_probe(key: ApiKey, client: httpx.AsyncClient):
        async with sem:
            return await _probe_single_key(provider, key, client)

    async with httpx.AsyncClient(timeout=15) as shared_client:
        probes = await asyncio.gather(*(bounded_probe(k, shared_client) for k in keys))

    success_count = 0
    failed_count = 0
    results = []
    for key, probe in zip(keys, probes):
        await _apply_key_probe(key, db, probe)
        results.append({k: probe[k] for k in ("key_id", "alias", "success", "message", "latency_ms")})
        if probe["success"]:
            success_count += 1
        else:
            failed_count += 1

    return BatchTestResult(
        total=len(keys),
        success=success_count,
        failed=failed_count,
        results=results,
    )


async def _probe_single_key(provider: Provider, key: ApiKey, client: "httpx.AsyncClient | None" = None) -> dict:
    """对单个 key 发起一次探测（纯 HTTP，不碰数据库，可并发调用）。

    client 可传入共享的 httpx.AsyncClient（批量并发时复用，省去每个 client
    约 1.5s 的 Windows 证书库初始化）；不传则自建（单 key 测试路径）。
    返回 dict：key_id / alias / success / message / latency_ms，
    外加 kind（失败类型，None 表示无需记账）与 status_code。
    失败按类型记账（见 app.utils.key_health）：网络不可达 / 上游 5xx 只写
    last_error，**不会**把 key 标成无效；只有 401/403 连续多次才禁用。
    """
    real_key = decrypt_value(key.key_value)
    start = time.monotonic()
    key_id = key.id
    alias = key.alias or ""

    def _result(success: bool, message: str, kind: str | None = None, status_code: int | None = None) -> dict:
        return {
            "key_id": key_id,
            "alias": alias,
            "success": success,
            "message": message,
            "latency_ms": int((time.monotonic() - start) * 1000),
            "kind": kind,
            "status_code": status_code,
        }

    try:
        # Qoder / QoderCN（EchoBird 原生账号）：鉴权是 COSY 签名头，不能用普通 Bearer /models 探测。
        # 用模型目录接口作为可达性/鉴权探针，凭据从加密的 oauth_refresh blob 解出。
        if key.oauth_profile in ("qoder", "qodercn") or str(provider.auth_type or "") in ("qoder-g", "qodercn-g"):
            from app.services import qoder_proxy
            platform = key.oauth_profile or ("qodercn" if str(provider.auth_type) == "qodercn-g" else "qoder")
            try:
                blob = json.loads(decrypt_value(key.oauth_refresh)) if key.oauth_refresh else {}
            except Exception:
                blob = {}
            if not isinstance(blob, dict) or not blob:
                return _result(False, "Qoder 账号凭据缺失，请重新登录该平台账号", KIND_AUTH)
            try:
                catalog = await qoder_proxy.list_models(blob, platform)
                return _result(True, f"Key is valid（可访问 {len(catalog)} 个模型）")
            except qoder_proxy.QoderError as e:
                kind = KIND_AUTH if e.login_required else (KIND_AUTH if e.status == 401 else classify_exception(e))
                return _result(False, e.message, kind, e.status)
            except Exception as e:
                return _result(False, str(e)[:200], classify_exception(e))

        if str(provider.auth_type or "") == "opencode" or key.oauth_profile == "opencode":
            # OpenCode：本地服务端健康检查 /global/health（而非 OpenAI /models）
            from app.services import opencode as opencode_svc
            oc = opencode_svc.OpenCodeClient(provider.base_url or opencode_svc.DEFAULT_BASE_URL, real_key)
            try:
                healthy = await oc.health()
                msg = "Key is valid" if healthy else "OpenCode 服务未就绪"
            except Exception as e:
                healthy = False
                msg = str(e)[:200]
            if healthy:
                return _result(True, msg)
            # 服务未就绪属于本地服务/网络问题，不算 key 失效
            return _result(False, msg, KIND_NETWORK)

        if str(provider.auth_type or "") == "cline" or key.oauth_profile == "cline":
            # Cline：拉取模型目录（GET /models）作为可达性健康检查
            from app.services import cline as cline_svc
            cc = cline_svc.ClineClient(provider.base_url or cline_svc.CLINE_API_BASE, real_key)
            try:
                ok = await cc.health()
                msg = "Key is valid" if ok else "Cline 上游不可达"
            except Exception as e:
                ok = False
                msg = str(e)[:200]
            if ok:
                return _result(True, msg)
            return _result(False, msg, KIND_NETWORK)

        own = client is None
        http = client if client is not None else httpx.AsyncClient(timeout=15)
        try:
            if provider.protocol == "openai":
                # OAuth key（如 Copilot）：补平台必需指纹头（不碰 DB，保持并发探测契约）
                from app.services.oauth_providers import oauth_probe_headers
                headers = await oauth_probe_headers(None, key, provider, {})
                resp = await http.get(
                    f"{provider.base_url.rstrip('/')}/models",
                    headers=headers,
                )
                if resp.status_code == 200:
                    return _result(True, "Key is valid")
                msg = f"HTTP {resp.status_code}: {resp.text[:200]}"
                return _result(False, msg, classify_status_code(resp.status_code), resp.status_code)

            elif provider.protocol == "claude":
                resp = await http.post(
                    f"{provider.base_url.rstrip('/')}/messages",
                    headers={
                        "x-api-key": real_key,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json={"model": "claude-3-haiku-20240307", "max_tokens": 1, "messages": [{"role": "user", "content": "hi"}]},
                )
                if resp.status_code == 200:
                    return _result(True, "Key is valid")
                msg = f"HTTP {resp.status_code}: {resp.text[:200]}"
                return _result(False, msg, classify_status_code(resp.status_code), resp.status_code)

            elif provider.protocol == "gemini":
                resp = await http.get(
                    f"{provider.base_url.rstrip('/')}/models?key={real_key}",
                )
                if resp.status_code == 200:
                    return _result(True, "Key is valid")
                msg = f"HTTP {resp.status_code}: {resp.text[:200]}"
                return _result(False, msg, classify_status_code(resp.status_code), resp.status_code)

            else:
                return _result(False, "Test not supported for custom protocol")
        finally:
            if own:
                await http.aclose()

    except Exception as e:
        # 连接失败/超时/DNS 等：只记录错误，不改 key 状态
        return _result(False, str(e)[:200], classify_exception(e))


async def _apply_key_probe(key: ApiKey, db: AsyncSession, probe: dict) -> None:
    """把单个 key 的探测结果记入数据库（串行调用，避免并发写同一 session）。

    成功探测不带 kind（见 _result），必须先按 success 复活 key：
    否则会出现「提示测试通过、列表仍显示无效且残留旧 last_error」。
    """
    if probe["success"]:
        await record_key_success(key, db)
        return
    kind = probe.get("kind")
    if kind is None:
        return
    await record_key_failure(key, db, kind, probe["message"], probe.get("status_code"))


async def _test_single_key(key: ApiKey, provider: Provider, db: AsyncSession) -> dict:
    """Test a single key and update its status."""
    probe = await _probe_single_key(provider, key)
    await _apply_key_probe(key, db, probe)
    return {k: probe[k] for k in ("key_id", "alias", "success", "message", "latency_ms")}


@router.post("/providers/{provider_id}/keys/recover")
async def recover_keys(provider_id: int, db: AsyncSession = Depends(get_db)):
    """Reset inactive / rate_limited keys back to untested.

    Used to bring back keys that were wrongly disabled by an earlier version
    (e.g. a network outage). ``expired`` keys are left untouched because they
    need a fresh browser login.
    """
    result = await db.execute(
        select(ApiKey).where(
            ApiKey.provider_id == provider_id,
            ApiKey.status.in_(["inactive", "rate_limited"]),
        )
    )
    keys = result.scalars().all()
    now = datetime.now(timezone.utc)
    for k in keys:
        k.status = "untested"
        k.fail_count = 0
        k.cooldown_until = 0
        k.last_failure_kind = ""
        k.last_error = None
        k.last_check_at = now
    await db.commit()
    return {"recovered": len(keys)}


@router.post("/api-keys/{key_id}/test", response_model=ApiKeyTestResult)
async def test_key(key_id: int, db: AsyncSession = Depends(get_db)):
    key = await db.get(ApiKey, key_id)
    if not key:
        raise HTTPException(404, "API Key not found")
    provider = await db.get(Provider, key.provider_id)
    if not provider:
        raise HTTPException(404, "Provider not found")

    real_key = decrypt_value(key.key_value)
    start = time.monotonic()

    # 与批量测试共用同一实现（含 opencode 本地服务端分支）
    result = await _test_single_key(key, provider, db)
    return ApiKeyTestResult(success=result["success"], message=result["message"],
                            latency_ms=result.get("latency_ms"))
