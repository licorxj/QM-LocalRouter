"""账号 → 模型组（可路由化）：把插件/原生账号包装为 Provider + Strategy。

每个账号物化为：
- Provider（name = "平台名 账号名"，auth_type = 平台 id）
- ApiKey（alias = "plugin:{account_id}" 作插件标记 / "echo:{account_id}"）：
  - 插件账号：凭据在引擎内，key_value 为占位；转发走 forwarder 的插件代理分支
    （POST 引擎 /operation {"operation":"proxy"}，OpenAI 格式进出）
  - EchoBird 原生账号：真实 token 加密入库（oauth_refresh 存凭据 blob）
- 模型：插件账号经引擎 "models" 操作拉取目录；echo 平台暂无目录源则跳过
- 调用：免策略直连，model = "组名/模型id"（如 "WorkBuddy 灿灿小可爱/auto"），
  由 /v1/models 目录与 _resolve_direct_model 支持；不占用路由策略页
"""
import json
import time

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.api_key import ApiKey
from app.models.model import Model
from app.models.provider import Provider
from app.models.strategy import Strategy, StrategyRule
from app.services import account_hub
from app.services import native_accounts
from app.services import plugin_accounts
from app.services import plugin_host
from app.services import plugin_manager
from app.utils.crypto import encrypt_value

_UPSTREAM_BASE = {
    "workbuddy": "https://www.workbuddy.cn",
    "workbuddyai": "https://www.workbuddy.ai",
    "zcode": "https://zcode.z.ai",
    "cline": "https://api.cline.bot",
    "doubaowork": "https://www.doubao.com",
    "dola": "https://www.dola.com",
    "trae": "https://api.trae.ai",
    "traecn": "https://api.trae.cn",
    "deepseek": "https://platform.deepseek.com",
    "kilo": "https://api.kilo.ai",
    "qoder": "https://openapi.qoder.sh",
    "qodercn": "https://openapi.qoder.com.cn",
    "qwenwork": "https://gateway.qwenwork.ai",
    "qwenworkcn": "https://gateway.qwenwork.cn",
}

_last_sync: dict = {"at": 0.0, "result": None}


def last_sync() -> dict:
    return {"at": _last_sync["at"],
            "result": _last_sync["result"],
            "cooldown": max(0, int(60 - (time.time() - _last_sync["at"])))}


async def _ensure_provider(db: AsyncSession, group_name: str, platform: str, note: str) -> Provider:
    result = await db.execute(select(Provider).where(Provider.name == group_name))
    provider = result.scalars().first()
    if provider:
        return provider
    provider = Provider(
        name=group_name,
        protocol="openai",
        base_url=_UPSTREAM_BASE.get(platform, ""),
        # 模型组用独立 auth_type（-g 后缀），避免 ensure_provider 在原生
        # 重新登录时误命中模型组 Provider（auth_type 列宽 20，"workbuddy-g" 合规）
        auth_type=(platform + "-g")[:20],
        description=note,
        icon="",
        homepage="",
        is_active=True,
    )
    db.add(provider)
    await db.commit()
    await db.refresh(provider)
    return provider


async def _ensure_plugin_key(db: AsyncSession, provider_id: int, platform: str, account_id: str) -> ApiKey:
    marker = f"plugin:{account_id}"
    result = await db.execute(
        select(ApiKey).where(ApiKey.provider_id == provider_id, ApiKey.alias == marker)
    )
    key = result.scalars().first()
    if key:
        return key
    key = ApiKey(
        provider_id=provider_id,
        key_value=encrypt_value("plugin-managed"),
        alias=marker[:100],
        weight=1,
        status="active",
        oauth_profile=platform[:50],
        oauth_refresh="",
        oauth_expires_at=0,
    )
    db.add(key)
    await db.commit()
    await db.refresh(key)
    return key


async def _ensure_echo_key(db: AsyncSession, provider_id: int, platform: str,
                           account_id: str, access_token: str, blob: dict,
                           expires_at: int) -> ApiKey:
    marker = f"echo:{account_id}"
    result = await db.execute(
        select(ApiKey).where(ApiKey.provider_id == provider_id, ApiKey.alias == marker)
    )
    key = result.scalars().first()
    if key:
        key.key_value = encrypt_value(access_token)
        key.oauth_refresh = encrypt_value(json.dumps(blob, ensure_ascii=False)) if blob else ""
        key.oauth_expires_at = expires_at or 0
        key.status = "active"
        await db.commit()
        await db.refresh(key)
        return key
    key = ApiKey(
        provider_id=provider_id,
        key_value=encrypt_value(access_token),
        alias=marker[:100],
        weight=1,
        status="active",
        oauth_profile=platform[:50],
        oauth_refresh=encrypt_value(json.dumps(blob, ensure_ascii=False)) if blob else "",
        oauth_expires_at=expires_at or 0,
    )
    db.add(key)
    await db.commit()
    await db.refresh(key)
    return key


async def _sync_plugin_models(db: AsyncSession, provider: Provider, platform: str,
                              account_id: str) -> int:
    """经引擎 "models" 操作拉取该账号的模型目录并 upsert 进 Provider。"""
    from app.routers.cli_import import _upsert_models
    try:
        result = await plugin_host.call(platform, "models", {"accountId": account_id})
    except Exception:
        return 0
    if not isinstance(result, list):
        return 0
    items = [{"model_id": str(m), "display_name": str(m)} for m in result if m]
    if not items:
        return 0
    return await _upsert_models(db, provider.id, items)


async def sync_all(db: AsyncSession, force: bool = False) -> dict:
    """把所有已接入账号物化为可路由的模型组。返回同步摘要。"""
    if not force and _last_sync["result"] and time.time() - _last_sync["at"] < 60:
        return _last_sync["result"]
    started = time.time()
    summary = {"groups": [], "skipped": [], "errors": [], "at": int(started)}

    enabled_apps: set[str] = set()
    for record in plugin_manager.installed_plugins().values():
        if record.get("enabled") and plugin_manager.engine_binary_for(record):
            enabled_apps.update(plugin_manager.applications_of_installed(record))

    # ---- 插件账号（凭据在引擎内；转发走插件代理分支）----
    for application in sorted(enabled_apps & account_hub.PLUGIN_SURFACED_APPS):
        try:
            accounts = await plugin_accounts.list_accounts(application)
        except Exception as e:
            summary["errors"].append(f"{application}: {str(e)[:120]}")
            continue
        label = account_hub.PLATFORM_BY_ID.get(application, {}).get("label", application)
        for account in accounts:
            if not isinstance(account, dict) or not account.get("id"):
                continue
            identity = str(account.get("email") or account.get("name") or account["id"])[:40]
            group_name = f"{label} {identity}".strip()[:100]
            try:
                provider = await _ensure_provider(db, group_name, application,
                                                  f"模型组：{label} 账号 {identity}（插件账号）")
                await _ensure_plugin_key(db, provider.id, application, str(account["id"]))
                synced = await _sync_plugin_models(db, provider, application, str(account["id"]))
                summary["groups"].append({
                    "platform": application, "identity": identity, "group": group_name,
                    "provider_id": provider.id, "models_synced": synced,
                })
            except Exception as e:
                summary["errors"].append(f"{group_name}: {str(e)[:120]}")

    # ---- EchoBird 原生平台账号（真实 token 入库；qoder 系拉目录同步模型）----
    from app.routers.cli_import import _upsert_models
    from app.services import qoder_proxy
    for platform in sorted(account_hub.ECHO_SURFACED_APPS):
        try:
            entries = native_accounts.list_echo_accounts(platform)
        except Exception:
            continue
        label = account_hub.PLATFORM_BY_ID.get(platform, {}).get("label", platform)
        for entry in entries:
            identity = str(entry.get("identity"))[:40]
            group_name = f"{label} {identity}".strip()[:100]
            extra = entry.get("extra") or {}
            token = extra.get("token") or ""
            try:
                provider = await _ensure_provider(db, group_name, platform,
                                                  f"模型组：{label} 账号 {identity}（EchoBird 存储）")
                blob = extra.get("creds") or {}
                await _ensure_echo_key(db, provider.id, platform, entry["account_id"],
                                       token, blob, int(extra.get("expires_at") or 0))
                synced = 0
                if platform in ("qoder", "qodercn") and blob:
                    try:
                        catalog = await qoder_proxy.list_models(blob, platform)
                        seen = set()
                        items = []
                        for row in catalog:
                            key = str(row.get("key") or "")
                            if key and key not in seen:
                                seen.add(key)
                                items.append({"model_id": key,
                                              "display_name": str(row.get("name") or key)})
                        synced = await _upsert_models(db, provider.id, items)
                    except Exception as e:
                        summary["errors"].append(f"{group_name} 模型目录: {str(e)[:100]}")
                summary["groups"].append({
                    "platform": platform, "identity": identity, "group": group_name,
                    "provider_id": provider.id, "models_synced": synced,
                })
            except Exception as e:
                summary["errors"].append(f"{group_name}: {str(e)[:120]}")

    _last_sync["at"] = time.time()
    _last_sync["result"] = summary
    return summary


async def remove_group(db: AsyncSession, kind: str, platform: str, account_id: str) -> bool:
    """账号删除时清理其模型组（Provider + Strategy）。"""
    entries = []
    if kind == "plugin":
        label = account_hub.PLATFORM_BY_ID.get(platform, {}).get("label", platform)
        entries = await plugin_accounts.list_accounts(platform)
    elif kind == "echo":
        label = account_hub.PLATFORM_BY_ID.get(platform, {}).get("label", platform)
        try:
            entries = native_accounts.list_echo_accounts(platform)
        except Exception:
            entries = []
    else:
        return False
    entry = next((e for e in entries if e.get("account_id") == account_id), None)
    if not entry:
        return False
    identity = str(entry.get("identity"))[:40]
    group_name = f"{label} {identity}".strip()[:100]
    provider = (await db.execute(select(Provider).where(Provider.name == group_name))).scalars().first()
    if not provider:
        return False
    strategy = (await db.execute(select(Strategy).where(Strategy.name == group_name))).scalars().first()
    if strategy:
        for rule in (await db.execute(select(StrategyRule).where(StrategyRule.strategy_id == strategy.id))).scalars().all():
            await db.delete(rule)
        await db.delete(strategy)
    for key in (await db.execute(select(ApiKey).where(ApiKey.provider_id == provider.id))).scalars().all():
        await db.delete(key)
    for model in (await db.execute(select(Model).where(Model.provider_id == provider.id))).scalars().all():
        await db.delete(model)
    await db.delete(provider)
    await db.commit()
    return True
