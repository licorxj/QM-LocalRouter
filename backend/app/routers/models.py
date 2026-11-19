from fastapi import APIRouter, Depends, HTTPException, Response

from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy import select

from app.database import get_db

from app.models.model import Model

from app.models.provider import Provider

from app.models.api_key import ApiKey

from app.schemas.schemas import ModelCreate, ModelUpdate, ModelOut, BatchTestResult, ModelTestResult, BatchDeleteResult, FetchModelItem

from app.utils.crypto import decrypt_value

from app.utils.known_models import (
    get_known_model_params,
    infer_model_types,
    infer_model_capabilities,
    normalize_model_types,
    primary_model_type,
)

import asyncio
import time
import httpx


def _type_fields(model_id: str, raw: dict | None = None, is_multimodal: bool = False) -> dict:
    """统一的「类型 + 能力」字段计算。

    types 取上游声明（多类型并列）→ 已知模型库 → 模型名启发式 → 文本兜底；
    model_type 仍是路由主类型（媒体类型优先），model_types 仅用于展示。
    """
    known = get_known_model_params(model_id) or {}
    types = infer_model_types(model_id, raw)
    known_type = known.get("model_type")
    if known_type and known_type not in types:
        types = normalize_model_types([known_type] + types)
    caps = infer_model_capabilities(
        model_id, raw, bool(is_multimodal or known.get("is_multimodal"))
    )
    return {
        "model_types": ",".join(types),
        "model_type": primary_model_type(types),
        "is_multimodal": caps["supports_vision"],
        "supports_vision": caps["supports_vision"],
        "supports_search": caps["supports_search"],
        "supports_tools": caps["supports_tools"],
    }


async def _workbuddy_catalog(provider: Provider, key_obj: ApiKey, db: AsyncSession):
    """WorkBuddy/CodeBuddy 模型目录来自官方 /v3/config，而非 OpenAI /models。

    返回 FetchModelItem 字典列表；拉取失败返回 None（回退到常规逻辑）。
    """
    if not str(provider.auth_type or "").startswith("workbuddy") or not key_obj:
        return None
    from app.services import workbuddy
    from app.services.oauth_providers import ensure_fresh_token

    try:
        await ensure_fresh_token(db, key_obj)
        credential = workbuddy.load_credential(key_obj)
        region = workbuddy.resolve_region(credential, provider.base_url or "")
        items = await workbuddy.list_models(region, credential)
    except Exception:
        return None

    out = []
    for item in items:
        known = get_known_model_params(item["model_id"]) or {}
        out.append({
            "model_id": item["model_id"],
            "display_name": known.get("display_name") or item.get("display_name") or item["model_id"],
            "context_window": known.get("context_window") or item.get("context_window"),
            "temperature": known.get("temperature"),
            "price_input": known.get("price_input"),
            "price_output": known.get("price_output"),
            "already_added": False,
            **_type_fields(item["model_id"], item, bool(item.get("is_multimodal"))),
        })
    return out


async def _opencode_catalog(provider: Provider, key_obj: ApiKey):
    """OpenCode 模型目录来自本地服务端的 /config/providers，而非 OpenAI /models。

    返回 FetchModelItem 字典列表；拉取失败返回 None（回退到常规逻辑）。
    """
    if str(provider.auth_type or "") != "opencode":
        return None
    from app.services import opencode as opencode_svc
    from app.utils.crypto import decrypt_value

    password = decrypt_value(key_obj.key_value) if key_obj and key_obj.key_value else ""
    client = opencode_svc.OpenCodeClient(provider.base_url or opencode_svc.DEFAULT_BASE_URL, password)
    try:
        models = await client.list_models()
    except Exception:
        return None

    out = []
    for item in models:
        model_id = item["id"]
        known = get_known_model_params(model_id) or {}
        out.append({
            "model_id": model_id,
            "display_name": known.get("display_name") or item.get("name") or model_id,
            "context_window": known.get("context_window"),
            "temperature": known.get("temperature"),
            "price_input": known.get("price_input"),
            "price_output": known.get("price_output"),
            "already_added": False,
            **_type_fields(model_id, item),
        })
    return out


async def _cline_catalog(provider: Provider, key_obj: ApiKey):
    """Cline 模型目录来自官方 /models + recommended-models，而非 OpenAI /models。

    返回 FetchModelItem 字典列表；拉取失败返回 None（回退到常规逻辑）。
    """
    if str(provider.auth_type or "") != "cline":
        return None
    from app.services import cline as cline_svc
    from app.utils.crypto import decrypt_value

    token = decrypt_value(key_obj.key_value) if key_obj and key_obj.key_value else ""
    client = cline_svc.ClineClient(provider.base_url or cline_svc.CLINE_API_BASE, token)
    try:
        models = await client.list_models()
    except Exception:
        return None

    out = []
    for item in models:
        model_id = item["id"]
        known = get_known_model_params(model_id) or {}
        out.append({
            "model_id": model_id,
            "display_name": known.get("display_name") or item.get("name") or model_id,
            "context_window": known.get("context_window"),
            "temperature": known.get("temperature"),
            "price_input": known.get("price_input"),
            "price_output": known.get("price_output"),
            "already_added": False,
            **_type_fields(model_id, item),
        })
    return out

async def _zcode_catalog(provider: Provider, key_obj: ApiKey):
    """ZCode（zcode.z.ai）没有 OpenAI /models 端点，模型目录为静态清单。

    返回 FetchModelItem 字典列表；非 zcode 平台返回 None（回退到常规逻辑）。
    """
    if str(provider.auth_type or "") != "zcode":
        return None
    from app.services import zcode as zcode_svc

    out = []
    for item in zcode_svc.DEFAULT_MODELS:
        model_id = item["model_id"]
        known = get_known_model_params(model_id) or {}
        out.append({
            "model_id": model_id,
            "display_name": known.get("display_name") or item.get("display_name") or model_id,
            "context_window": known.get("context_window") or item.get("context_window"),
            "temperature": known.get("temperature"),
            "price_input": known.get("price_input"),
            "price_output": known.get("price_output"),
            "already_added": False,
            **_type_fields(model_id, item, bool(item.get("is_multimodal"))),
        })
    return out

router = APIRouter(prefix="/api", tags=["models"])





@router.get("/providers/{provider_id}/models", response_model=list[ModelOut])

async def list_models(provider_id: int, db: AsyncSession = Depends(get_db)):

    result = await db.execute(

        select(Model).where(Model.provider_id == provider_id).order_by(Model.id)

    )

    return result.scalars().all()





@router.post("/models", response_model=ModelOut, status_code=201)

async def create_model(data: ModelCreate, db: AsyncSession = Depends(get_db)):

    provider = await db.get(Provider, data.provider_id)

    if not provider:

        raise HTTPException(404, "Provider not found")

    model = Model(**data.model_dump())

    db.add(model)

    await db.commit()

    await db.refresh(model)

    return model





@router.put("/models/{model_id}", response_model=ModelOut)

async def update_model(model_id: int, data: ModelUpdate, db: AsyncSession = Depends(get_db)):

    model = await db.get(Model, model_id)

    if not model:

        raise HTTPException(404, "Model not found")

    for k, v in data.model_dump(exclude_unset=True).items():

        setattr(model, k, v)

    await db.commit()

    await db.refresh(model)

    return model





@router.delete("/models/{model_id}", status_code=204)
async def delete_model(model_id: int, db: AsyncSession = Depends(get_db)):
    model = await db.get(Model, model_id)
    if not model:
        raise HTTPException(404, "Model not found")
    await db.delete(model)
    await db.commit()
    return Response(status_code=204)





@router.post("/models/fetch/{provider_id}", response_model=list[FetchModelItem])
async def fetch_models(provider_id: int, db: AsyncSession = Depends(get_db)):
    provider = await db.get(Provider, provider_id)
    if not provider:
        raise HTTPException(404, "Provider not found")

    # Get first active key for this provider
    result = await db.execute(
        select(ApiKey).where(ApiKey.provider_id == provider_id, ApiKey.status == "active").limit(1)
    )
    key_obj = result.scalar_one_or_none()

    # WorkBuddy: 官方目录 /v3/config
    wb_items = await _workbuddy_catalog(provider, key_obj, db)
    if wb_items is None:
        wb_items = await _opencode_catalog(provider, key_obj)
    if wb_items is None:
        wb_items = await _cline_catalog(provider, key_obj)
    if wb_items is None:
        wb_items = await _zcode_catalog(provider, key_obj)
    if wb_items is not None:
        existing_result = await db.execute(select(Model).where(Model.provider_id == provider_id))
        existing_ids = {m.model_id for m in existing_result.scalars().all()}
        for item in wb_items:
            item["already_added"] = item["model_id"] in existing_ids
        return wb_items

    models_data = []

    try:
        if provider.protocol in ("openai", "custom"):
            # OAuth key（如 Copilot）：先刷新短效 token 并合并平台必需指纹头
            from app.services.oauth_providers import oauth_probe_headers
            headers = await oauth_probe_headers(db, key_obj, provider, {"Content-Type": "application/json"})
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(f"{provider.base_url.rstrip('/')}/models", headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    # 获取已有模型 id 集合，标记是否已存在
                    existing_result = await db.execute(select(Model).where(Model.provider_id == provider_id))
                    existing_ids = {m.model_id for m in existing_result.scalars().all()}
                    for m in data.get("data", []):
                        mid = m.get("id", "")
                        known = get_known_model_params(mid)
                        models_data.append({
                            "model_id": mid,
                            "display_name": known.get("display_name", mid),
                            "context_window": known.get("context_window"),
                            "temperature": known.get("temperature"),
                            "price_input": known.get("price_input"),
                            "price_output": known.get("price_output"),
                            "already_added": mid in existing_ids,
                            **_type_fields(mid, m),
                        })

        elif provider.protocol == "gemini":
            if not key_obj:
                raise HTTPException(400, "No active API key for this provider")
            real_key = decrypt_value(key_obj.key_value)
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(f"{provider.base_url.rstrip('/')}/models?key={real_key}")
                if resp.status_code == 200:
                    data = resp.json()
                    existing_result = await db.execute(select(Model).where(Model.provider_id == provider_id))
                    existing_ids = {m.model_id for m in existing_result.scalars().all()}
                    for m in data.get("models", []):
                        name = m.get("name", "").replace("models/", "")
                        display = m.get("displayName", name)
                        input_limit = m.get("inputTokenLimit")
                        context = input_limit if input_limit else None
                        is_multi = any(k in name.lower() for k in ["vision", "pro", "flash", "ultra"])
                        known = get_known_model_params(name)
                        models_data.append({
                            "model_id": name,
                            "display_name": known.get("display_name", display),
                            "context_window": known.get("context_window", context),
                            "temperature": known.get("temperature"),
                            "price_input": known.get("price_input"),
                            "price_output": known.get("price_output"),
                            "already_added": name in existing_ids,
                            **_type_fields(name, m, is_multi),
                        })

    except httpx.RequestError:
        pass

    return models_data


@router.post("/models/sync/{provider_id}", response_model=list[ModelOut])

async def sync_models(provider_id: int, db: AsyncSession = Depends(get_db)):

    provider = await db.get(Provider, provider_id)

    if not provider:

        raise HTTPException(404, "Provider not found")



    # Get first active key for this provider

    result = await db.execute(

        select(ApiKey).where(ApiKey.provider_id == provider_id, ApiKey.status == "active").limit(1)

    )

    key_obj = result.scalar_one_or_none()



    models_data = []



    try:

        if provider.protocol in ("openai", "custom"):

            headers = {"Content-Type": "application/json"}

            # auth_type="none" 的免密上游（OpenCode Zen / Kilo 免费池）不接受伪造 Bearer
            if key_obj and str(provider.auth_type or "") != "none":

                headers["Authorization"] = f"Bearer {decrypt_value(key_obj.key_value)}"

            async with httpx.AsyncClient(timeout=15) as client:

                resp = await client.get(f"{provider.base_url.rstrip('/')}/models", headers=headers)

                if resp.status_code == 200:

                    data = resp.json()

                    for m in data.get("data", []):

                        mid = m.get("id", "")

                        known = get_known_model_params(mid)

                        models_data.append({

                            "model_id": mid,

                            "display_name": known.get("display_name", mid),

                            "context_window": known.get("context_window"),

                            "temperature": known.get("temperature"),

                            "price_input": known.get("price_input"),

                            "price_output": known.get("price_output"),

                            **_type_fields(mid, m),

                        })



        elif provider.protocol == "gemini":

            if not key_obj:

                raise HTTPException(400, "No active API key for this provider")

            real_key = decrypt_value(key_obj.key_value)

            async with httpx.AsyncClient(timeout=15) as client:

                resp = await client.get(f"{provider.base_url.rstrip('/')}/models?key={real_key}")

                if resp.status_code == 200:

                    data = resp.json()

                    for m in data.get("models", []):

                        name = m.get("name", "").replace("models/", "")

                        display = m.get("displayName", name)

                        # Extract Gemini-specific parameters

                        input_limit = m.get("inputTokenLimit")

                        output_limit = m.get("outputTokenLimit")

                        context = None

                        if input_limit:

                            context = input_limit

                        elif output_limit:

                            context = output_limit

                        # Detect multimodal from supported methods

                        methods = m.get("supportedGenerationMethods", [])

                        is_multi = any(k in name.lower() for k in ["vision", "pro", "flash", "ultra"])

                        # Try known params first

                        known = get_known_model_params(name)

                        models_data.append({

                            "model_id": name,

                            "display_name": known.get("display_name", display),

                            "context_window": known.get("context_window", context),

                            "temperature": known.get("temperature"),

                            "price_input": known.get("price_input"),

                            "price_output": known.get("price_output"),

                            **_type_fields(name, m, is_multi),

                        })



    except httpx.RequestError:

        pass



    # WorkBuddy: 官方目录 /v3/config
    wb_items = await _workbuddy_catalog(provider, key_obj, db)
    if wb_items is None:
        wb_items = await _opencode_catalog(provider, key_obj)
    if wb_items is None:
        wb_items = await _cline_catalog(provider, key_obj)
    if wb_items is None:
        wb_items = await _zcode_catalog(provider, key_obj)
    if wb_items is not None:
        models_data = wb_items

    # Upsert models - preserve manual edits, update synced fields

    existing_result = await db.execute(select(Model).where(Model.provider_id == provider_id))

    existing = {m.model_id: m for m in existing_result.scalars().all()}



    # Enrich existing models that have empty params from known database

    for mid, m in existing.items():

        if not m.context_window and not m.price_input:

            known = get_known_model_params(mid)

            if known:

                if known.get("is_multimodal"):

                    m.is_multimodal = True

                if known.get("model_type") and m.model_type == "text":

                    m.model_type = known["model_type"]

                if known.get("context_window"):

                    m.context_window = known["context_window"]

                if known.get("temperature") is not None:

                    m.temperature = known["temperature"]

                if known.get("price_input") is not None:

                    m.price_input = known["price_input"]

                if known.get("price_output") is not None:

                    m.price_output = known["price_output"]

                if known.get("display_name"):

                    m.display_name = known["display_name"]



    created = []

    for md in models_data:

        if md["model_id"] in existing:

            m = existing[md["model_id"]]

            # Only update fields if they are not empty/default

            m.display_name = md["display_name"] or m.display_name

            if md["is_multimodal"]:

                m.is_multimodal = True

            if md["model_type"] and md["model_type"] != "text":

                m.model_type = md["model_type"]

            if md["context_window"]:

                m.context_window = md["context_window"]

            if md["temperature"] is not None:

                m.temperature = md["temperature"]

            if md["price_input"] is not None:

                m.price_input = md["price_input"]

            if md["price_output"] is not None:

                m.price_output = md["price_output"]

            if md.get("model_types"):

                # 与已有类型取并集：同步只增不删，保留用户手动补充的类型
                merged = normalize_model_types(
                    normalize_model_types(m.model_types or m.model_type)
                    + normalize_model_types(md["model_types"])
                )

                m.model_types = ",".join(merged)

            if md.get("supports_vision"):

                m.supports_vision = True

            if md.get("supports_search"):

                m.supports_search = True

            if md.get("supports_tools"):

                m.supports_tools = True

            created.append(m)

        else:

            new_model = Model(

                provider_id=provider_id,

                model_id=md["model_id"],

                display_name=md["display_name"],

                is_multimodal=md.get("is_multimodal", False),

                model_type=md.get("model_type", "text"),

                model_types=md.get("model_types") or md.get("model_type", "text"),

                supports_vision=md.get("supports_vision", False),

                supports_search=md.get("supports_search", False),

                supports_tools=md.get("supports_tools", False),

                context_window=md.get("context_window"),

                temperature=md.get("temperature"),

                price_input=md.get("price_input"),

                price_output=md.get("price_output"),

            )

            db.add(new_model)

            created.append(new_model)



    await db.commit()

    for m in created:

        await db.refresh(m)

    return created


# 批量测试并发上限：显著提速的同时避免一次性打爆上游触发限流
MODEL_TEST_CONCURRENCY = 10


async def _probe_model(
    client: httpx.AsyncClient, provider: Provider, model: Model, key_obj: ApiKey | None
) -> tuple[bool, str, int]:
    """对单个模型发起一次最小请求探测（纯 HTTP，不碰数据库，可并发调用）。"""
    start = time.time()
    try:
        if provider.protocol in ("openai", "custom"):
            headers = {"Content-Type": "application/json"}
            # auth_type="none" 的免密上游（OpenCode Zen / Kilo 免费池）不接受伪造 Bearer
            if key_obj and str(provider.auth_type or "") != "none":
                headers["Authorization"] = f"Bearer {decrypt_value(key_obj.key_value)}"
            payload = {
                "model": model.model_id,
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 1,
            }
            resp = await client.post(
                f"{provider.base_url.rstrip('/')}/chat/completions",
                json=payload,
                headers=headers,
            )
            ok = resp.status_code == 200
            msg = "OK" if ok else f"HTTP {resp.status_code}"

        elif provider.protocol == "claude":
            headers = {
                "Content-Type": "application/json",
                "x-api-key": decrypt_value(key_obj.key_value) if key_obj else "",
                "anthropic-version": "2023-06-01",
            }
            payload = {
                "model": model.model_id,
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 1,
            }
            resp = await client.post(
                f"{provider.base_url.rstrip('/')}/messages",
                json=payload,
                headers=headers,
            )
            ok = resp.status_code == 200
            msg = "OK" if ok else f"HTTP {resp.status_code}"

        elif provider.protocol == "gemini":
            real_key = decrypt_value(key_obj.key_value) if key_obj else ""
            payload = {
                "contents": [{"parts": [{"text": "hi"}]}],
                "generationConfig": {"maxOutputTokens": 1},
            }
            resp = await client.post(
                f"{provider.base_url.rstrip('/')}/models/{model.model_id}:generateContent?key={real_key}",
                json=payload,
                headers={"Content-Type": "application/json"},
            )
            ok = resp.status_code == 200
            msg = "OK" if ok else f"HTTP {resp.status_code}"

        else:
            ok = False
            msg = "Unsupported protocol"

    except Exception as e:
        ok = False
        msg = str(e)

    return ok, msg, int((time.time() - start) * 1000)


async def _probe_model_via_forwarder(db: AsyncSession, provider: Provider, model: Model, api_key: ApiKey) -> tuple[bool, str, int]:
    """经 forwarder 真实推理路径探测单个模型（与单个测试按钮同链路）。

    OAuth/CLI 车道（cline/opencode/zcode/workbuddy 等）的上游有专门适配器，
    裸打 /chat/completions 会因缺少会话头、被剥离的 max_tokens 等差异而
    全部失败（典型：cline 免费模型带 max_tokens 一律 500，见
    services/cline.py 的 _build_body）。因此这类 key 必须复用真实路径探测。
    db 传入请求级会话：OAuth key 临期时 _real_key 需经它刷新 token。
    """
    from app.services.forwarder import Forwarder
    from app.models.strategy import Strategy, StrategyRule

    pseudo_strategy = Strategy(
        id=0, name=f"__testall__{provider.name}", lb_strategy="round_robin",
        is_active=True, timeout=30, retry_count=0,
        key_strategy=getattr(provider, "key_strategy", None) or "round_robin",
    )
    pseudo_rule = StrategyRule(
        id=0, strategy_id=0, provider_id=provider.id,
        model_id=model.id, priority=1, weight=1, is_active=True,
    )
    body = {
        "model": model.model_id,
        "messages": [{"role": "user", "content": "hi"}],
    }
    start = time.monotonic()
    try:
        forwarder = Forwarder(db)
        resp = await forwarder.forward(pseudo_strategy, pseudo_rule, provider,
                                       model, api_key, body, is_stream=False)
        ok = 200 <= resp.status_code < 300
        msg = "OK" if ok else f"HTTP {resp.status_code}"
        return ok, msg, int((time.monotonic() - start) * 1000)
    except Exception as e:
        return False, str(e)[:200], int((time.monotonic() - start) * 1000)


@router.post("/models/{model_id}/test", response_model=ModelTestResult)
async def test_model(model_id: int, db: AsyncSession = Depends(get_db)):
    """测试单个模型并把结果写回模型状态（列表里的独立测试按钮）。

    走真实转发链路，与原前端直接打 /v1/chat/completions 的语义一致：
    部分聚合桥接上游不支持 GET /models，用轻量探测会误判为失败。
    成功后置 active 并清空 last_error。
    """
    model = await db.get(Model, model_id)
    if not model:
        raise HTTPException(404, "Model not found")
    provider = await db.get(Provider, model.provider_id)
    if not provider:
        raise HTTPException(404, "Provider not found")

    key_result = await db.execute(
        select(ApiKey).where(
            ApiKey.provider_id == provider.id, ApiKey.status == "active"
        ).limit(1)
    )
    key_obj = key_result.scalar_one_or_none()
    if key_obj is None:
        # 诊断用途：没有 active key 时也拿现有 key 探一次，避免全 key 冷却时无法测试
        key_result = await db.execute(
            select(ApiKey).where(ApiKey.provider_id == provider.id).limit(1)
        )
        key_obj = key_result.scalar_one_or_none()

    if key_obj is None:
        ok, msg, latency = False, "No API key for this provider", 0
    else:
        ok, msg, latency = await _probe_model_via_forwarder(db, provider, model, key_obj)

    model.status = "active" if ok else "inactive"
    model.last_error = "" if ok else msg[:500]
    await db.commit()

    return ModelTestResult(
        model_id=model.model_id, success=ok, message=msg, latency_ms=latency
    )


@router.post("/providers/{provider_id}/models/test-all", response_model=BatchTestResult)
async def test_all_models(provider_id: int, db: AsyncSession = Depends(get_db)):
    provider = await db.get(Provider, provider_id)
    if not provider:
        raise HTTPException(404, "Provider not found")

    result = await db.execute(
        select(Model).where(Model.provider_id == provider_id, Model.is_active == True)
    )
    models = result.scalars().all()
    model_map = {m.model_id: m for m in models}
    if not models:
        return BatchTestResult(total=0, success=0, failed=0, results=[])

    key_result = await db.execute(
        select(ApiKey).where(ApiKey.provider_id == provider_id, ApiKey.status == "active").limit(1)
    )
    key_obj = key_result.scalar_one_or_none()

    # OAuth/CLI 车道 key 必须走 forwarder 真实路径（裸探测会被上游风控拒绝）；
    # 普通 API key 上游继续用轻量裸探测（不产生真实推理消耗）
    needs_forwarder = bool(key_obj and key_obj.oauth_profile)

    # 并发探测所有模型（信号量限流），探测完成后统一回写数据库
    sem = asyncio.Semaphore(MODEL_TEST_CONCURRENCY)

    async def bounded_probe(model: Model):
        async with sem:
            if needs_forwarder:
                return await _probe_model_via_forwarder(db, provider, model, key_obj)
            return await _probe_model(client, provider, model, key_obj)

    results: list[ModelTestResult] = []
    success_count = 0
    failed_count = 0

    if needs_forwarder:
        probes = await asyncio.gather(*(bounded_probe(m) for m in models))
    else:
        async with httpx.AsyncClient(timeout=15) as client:
            probes = await asyncio.gather(*(bounded_probe(m) for m in models))

    for model, (ok, msg, latency) in zip(models, probes):
        results.append(ModelTestResult(
            model_id=model.model_id,
            success=ok,
            message=msg,
            latency_ms=latency,
        ))
        db_model = model_map.get(model.model_id)
        if db_model:
            if ok:
                db_model.status = "active"
                db_model.last_error = ""
            else:
                db_model.status = "inactive"
                db_model.last_error = msg[:500]
        if ok:
            success_count += 1
        else:
            failed_count += 1

    await db.commit()

    return BatchTestResult(
        total=len(models),
        success=success_count,
        failed=failed_count,
        results=results,
    )


@router.delete("/providers/{provider_id}/models/invalid", response_model=BatchDeleteResult)
async def delete_invalid_models(provider_id: int, db: AsyncSession = Depends(get_db)):
    provider = await db.get(Provider, provider_id)
    if not provider:
        raise HTTPException(404, "Provider not found")

    # 删除当前平台下所有 status 为 "inactive" 的模型（由"测试全部模型"标记的无效模型）
    result = await db.execute(
        select(Model).where(Model.provider_id == provider_id, Model.status == "inactive")
    )
    invalid_models = result.scalars().all()

    deleted_count = 0
    for m in invalid_models:
        await db.delete(m)
        deleted_count += 1

    if deleted_count > 0:
        await db.commit()

    return BatchDeleteResult(deleted=deleted_count)


@router.delete("/providers/{provider_id}/models", response_model=BatchDeleteResult)
async def clear_models(provider_id: int, db: AsyncSession = Depends(get_db)):
    """Delete all models for a provider."""
    provider = await db.get(Provider, provider_id)
    if not provider:
        raise HTTPException(404, "Provider not found")

    result = await db.execute(
        select(Model).where(Model.provider_id == provider_id)
    )
    models = result.scalars().all()

    deleted_count = 0
    for m in models:
        await db.delete(m)
        deleted_count += 1

    if deleted_count > 0:
        await db.commit()

    return BatchDeleteResult(deleted=deleted_count)
