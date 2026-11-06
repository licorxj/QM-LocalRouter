"""免费模型合集路由：匿名免费渠道清单与一键加入平台管理。

清单来自 app.services.free_models（OpenCode Zen + Kilo 免费池，同
dsh-our-free-model 的数据源）；「加入」创建 auth_type="none" 的 Provider、
免密占位 Key，并把渠道的免费模型批量入库。
"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.api_key import ApiKey
from app.models.model import Model
from app.models.provider import Provider
from app.services import free_models
from app.utils.crypto import encrypt_value, decrypt_value

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/providers/free-models", tags=["free-models"])


class FreeChannelJoinRequest(BaseModel):
    channel_id: str
    # requires_gateway 渠道（如 OpenCode Zen）必填：本地网关的 OpenAI 兼容地址
    gateway_base_url: str = ""
    # 网关本地密钥（opencode2api 的 server_keys 之一）；留空则以免密方式接入
    gateway_key: str = ""


@router.get("")
async def list_free_channels(force: bool = False):
    """免费渠道清单（含各渠道的免费模型），10 分钟内存缓存。"""
    return {"channels": await free_models.get_free_channels(force=force)}


@router.post("/join")
async def join_free_channel(data: FreeChannelJoinRequest, db: AsyncSession = Depends(get_db)):
    channel = free_models.find_channel(await free_models.get_free_channels(), data.channel_id)
    if not channel:
        raise HTTPException(404, f"Free channel '{data.channel_id}' not found")
    if not channel.get("ok") or not channel.get("models"):
        raise HTTPException(502, f"渠道 '{channel['name']}' 清单拉取失败，请稍后重试")

    base_url = channel["base_url"]
    auth_type = channel["auth_type"]
    key_value = "free"
    if channel.get("requires_gateway"):
        gateway_url = (data.gateway_base_url or "").strip().rstrip("/")
        gateway_key = (data.gateway_key or "").strip()
        if not gateway_url or not gateway_key:
            # 未填写时自动读取根目录 config.json 的 listen + server_keys
            auto = free_models.read_gateway_config()
            if auto:
                gateway_url = gateway_url or auto["url"]
                gateway_key = gateway_key or auto["key"]
        if not gateway_url:
            raise HTTPException(400, "未找到本地网关：请先启动 opencode2api 并在根目录 config.json 配置 listen，或手动填写网关地址")
        base_url = gateway_url
        key_value = gateway_key or "free"
        auth_type = "api_key" if gateway_key else "none"

    # 幂等：同名 Provider 视为已加入，只补模型；需网关渠道的重新加入可同步修正网关配置
    result = await db.execute(select(Provider).where(Provider.name == channel["name"]).limit(1))
    provider = result.scalar_one_or_none()
    created = provider is None
    if created:
        provider = Provider(
            name=channel["name"],
            protocol=channel["protocol"],
            base_url=base_url,
            auth_type=auth_type,
            description=f"{channel['description']}（来源：{channel['source']}）",
            homepage=channel["homepage"],
            icon=channel["icon"],
            is_active=True,
        )
        db.add(provider)
        await db.flush()
    elif channel.get("requires_gateway") and (data.gateway_base_url or "").strip():
        if provider.base_url != base_url:
            provider.base_url = base_url
        if (data.gateway_key or "").strip():
            provider.auth_type = "api_key"
            # 已有同值密钥则不重复添加（解密失败的旧值视为不匹配）
            existing_keys = (await db.execute(select(ApiKey).where(ApiKey.provider_id == provider.id))).scalars().all()

            def _plain(key: ApiKey) -> str:
                try:
                    return decrypt_value(key.key_value)
                except Exception:
                    return ""

            if not any(_plain(k) == key_value for k in existing_keys):
                db.add(ApiKey(provider_id=provider.id, key_value=encrypt_value(key_value), alias="网关密钥"))

    # 免密渠道也需要至少一个 Key 供 Balancer 选中；auth_type="none" 时转发不发认证头
    key_result = await db.execute(select(ApiKey).where(ApiKey.provider_id == provider.id).limit(1))
    if key_result.scalar_one_or_none() is None:
        alias = "免密通道" if auth_type == "none" else "网关密钥"
        db.add(ApiKey(provider_id=provider.id, key_value=encrypt_value(key_value), alias=alias))

    # 批量加入免费模型，跳过已存在的 model_id
    existing = await db.execute(select(Model.model_id).where(Model.provider_id == provider.id))
    existing_ids = {row[0] for row in existing.all()}
    added = 0
    for item in channel["models"]:
        if item["model_id"] in existing_ids:
            continue
        db.add(Model(
            provider_id=provider.id,
            model_id=item["model_id"],
            display_name=item["display_name"],
            is_active=True,
            is_multimodal=bool(item.get("is_multimodal")),
            model_type=item.get("model_type") or "text",
            context_window=item.get("context_window"),
            price_input=0.0,
            price_output=0.0,
        ))
        added += 1
    await db.commit()
    await db.refresh(provider)

    return {
        "provider_id": provider.id,
        "provider_name": provider.name,
        "created": created,
        "added_models": added,
        "skipped_models": max(len(channel["models"]) - added, 0),
    }
