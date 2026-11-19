"""账号接入中心 API（移植 EchoBird 账号管理）。

- GET  /api/accounts/platforms        平台目录（含登录就绪状态）
- GET  /api/accounts                  已接入账号卡片列表（不含任何 token）
- POST /api/accounts/{platform}/login 发起浏览器/OAuth 授权（轮询复用 /api/oauth/status/{session_id}）
- POST /api/accounts/{key_id}/refresh 刷新令牌 + 拉取额度（写入 quota_cache）
- POST /api/accounts/{key_id}/claim   WorkBuddy CN 每日签到
删除复用现有 DELETE /api/api-keys/{id}。
"""
import asyncio
import time

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy import func as sa_func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.api_key import ApiKey
from app.utils.crypto import decrypt_value
from app.services import account_hub
from app.services import account_quota
from app.services import account_routing
from app.services import local_cli
from app.services import native_accounts
from app.services import oauth_providers as op
from app.services import plugin_accounts
from app.services import plugin_host
from app.services import plugin_manager
from app.services import qoder_campaign
from app.services import zcode_claim
from app.services.native_accounts import EchoError
from app.services.plugin_host import PluginError

router = APIRouter(prefix="/api/accounts", tags=["accounts"])

AUTO_QUOTA_TTL = 600  # 秒：页面打开时原生账号额度的自动刷新间隔

_SUCCESS_PAGE = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>LocalRouter</title>
<style>body{margin:0;height:100vh;display:flex;align-items:center;justify-content:center;background:#0b1220;
color:#e2e8f0;font-family:system-ui,sans-serif}}.c{text-align:center}</style></head>
<body><div class="c"><h2>✓ 授权成功</h2><p>账号已接入 LocalRouter，可关闭此窗口</p></div></body></html>"""

_FAILURE_PAGE = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>LocalRouter</title>
<style>body{margin:0;height:100vh;display:flex;align-items:center;justify-content:center;background:#0b1220;
color:#fca5a5;font-family:system-ui,sans-serif}}.c{text-align:center}</style></head>
<body><div class="c"><h2>授权失败</h2><p>{error}</p></div></body></html>"""


@router.get("/platforms")
async def platforms(db: AsyncSession = Depends(get_db)):
    """平台目录 + 每个平台已接入账号数。"""
    rows = await db.execute(
        select(ApiKey.oauth_profile, sa_func.count())
        .where(ApiKey.oauth_profile != "")
        .group_by(ApiKey.oauth_profile)
    )
    counts = {platform: int(n) for platform, n in rows.all()}
    items = []
    for p in account_hub.platform_catalog():
        items.append({**p, "account_count": counts.get(p["id"], 0)})
    return {"platforms": items}


SNAPSHOT_TTL = 30  # 秒：快照有效期；过期后返回旧快照并触发后台重建
_snapshot: dict = {"cards": None, "built_at": 0.0, "refreshing": False}


def invalidate_snapshot() -> None:
    """账号变更（登录/删除/刷新）后使快照过期，下一次 GET 触发后台重建。"""
    _snapshot["built_at"] = 0.0


async def _refresh_native_card(db: AsyncSession, card: dict) -> dict:
    """原生账号额度自动刷新：无缓存或超过 TTL 时实时拉取（EchoBird 行为）。"""
    if not card.get("supports_quota") or card.get("via_plugin") or card.get("via_echo"):
        return card
    fetched_at = card.get("quota_fetched_at") or 0
    if card.get("quota") and time.time() - fetched_at < AUTO_QUOTA_TTL:
        return card
    row = await account_hub.get_account_key(db, card["id"])
    if not row:
        return card
    api_key, provider = row
    platform = str(api_key.oauth_profile or "")
    try:
        payload = await account_quota.fetch_quota(api_key, provider)
        if platform in ("qoder", "qodercn"):
            # Qoder 签到状态（campaign claimStatus）写入 quota.checkin，供签到中心弹窗展示
            status = await qoder_campaign.fetch_status(api_key)
            if status:
                payload = dict(payload or {})
                payload["checkin"] = status
                payload.setdefault("fetched_at", int(time.time()))
        if payload:
            account_quota.save_quota_cache(api_key, payload)
            await db.commit()
            await db.refresh(api_key)
            return account_hub.account_card(api_key, provider)
    except account_quota.QuotaError as e:
        return {**card, "quota_error": e.message, "login_required": e.login_required}
    except Exception:
        return card
    return card


async def _build_snapshot(db: AsyncSession) -> list[dict]:
    """构建账号卡片快照（含原生额度刷新与模型组同步；耗时操作，勿在热路径调用）。"""
    native = await account_hub.list_native_cards(db)
    for card in native:
        card = await _refresh_native_card(db, card)  # 逐个刷新（AsyncSession 不支持并发使用）
    plugin_cards, echo_cards = await asyncio.gather(
        account_hub.list_plugin_cards(), account_hub.list_echo_cards())
    cards = account_hub.sort_cards([*native, *plugin_cards, *echo_cards])
    # 模型组同步（内部 60s 去抖；失败不影响快照）
    try:
        await account_routing.sync_all(db, force=False)
    except Exception:
        pass
    return cards


async def _rebuild_snapshot_async() -> None:
    from app.database import async_session
    try:
        async with async_session() as session:
            cards = await _build_snapshot(session)
            _snapshot["cards"] = cards
            _snapshot["built_at"] = time.time()
    except Exception as e:
        print(f"[accounts] 后台快照重建失败: {type(e).__name__} {str(e)[:200]}", flush=True)
    finally:
        _snapshot["refreshing"] = False


@router.get("")
async def list_accounts(db: AsyncSession = Depends(get_db)):
    """返回账号卡片快照：缓存内直接返回；过期则立即回旧数据并后台重建。

    重建包含引擎调用 / 上游额度拉取 / 模型组同步等耗时操作，全部不阻塞响应。
    """
    now = time.time()
    has_cards = _snapshot["cards"] is not None
    fresh = has_cards and now - _snapshot["built_at"] < SNAPSHOT_TTL
    if not fresh and not _snapshot["refreshing"]:
        _snapshot["refreshing"] = True
        if has_cards:
            asyncio.get_running_loop().create_task(_rebuild_snapshot_async())
        else:
            # 冷启动：同步构建一次，之后所有加载都走缓存 + 后台刷新
            try:
                _snapshot["cards"] = await _build_snapshot(db)
                _snapshot["built_at"] = time.time()
                fresh = True
            finally:
                _snapshot["refreshing"] = False
    return {
        "accounts": _snapshot["cards"] or [],
        "stale": not fresh,
        "supported_quota": list(account_quota.SUPPORTED_QUOTA_PLATFORMS),
    }


@router.get("/zcode/status")
async def zcode_claim_status():
    """ZCode 本地签到前置检查：是否安装 ZCode 客户端、Node 运行时是否就绪。"""
    return zcode_claim.detect_installation()


def _zcode_decrypt_token(row: ApiKey) -> str:
    """反射定位 ApiKey 的加密 token 列（模型里注释为 Encrypted）并解密。

    不写死列名，规避不同版本/脱敏导致的字段名差异。
    """
    exclude = {
        "id", "provider_id", "alias", "status", "weight", "fail_count",
        "last_failure_kind", "cooldown_until", "last_check_at", "oauth_profile",
        "oauth_refresh", "oauth_expires_at", "quota_cache", "quota_fetched_at",
        "last_used_at", "last_error", "created_at",
    }
    for col in ApiKey.__table__.columns:
        if col.name in exclude or col.name.startswith("_"):
            continue
        raw = getattr(row, col.name, None)
        if raw:
            try:
                return decrypt_value(raw)
            except Exception:
                pass
    return ""


def _zcode_key_token(row: ApiKey) -> tuple[str, str]:
    """从 ApiKey 取 zcode 明文 JWT 与 email。"""
    token = _zcode_decrypt_token(row)
    email = ""
    if row.oauth_refresh:
        try:
            import json as _json
            blob = _json.loads(decrypt_value(row.oauth_refresh)) or {}
            email = str(blob.get("email") or "")
        except Exception:
            pass
    return token, email


async def _zcode_refresh_quota(db: AsyncSession, key_id: int) -> None:
    """zcode 签到成功后立即重拉额度写回缓存，使赠送额度第一时间显示。

    绕过列表的 AUTO_QUOTA_TTL 缓存：直接 fetch + save，失败不影响签到结果。
    """
    try:
        row = await account_hub.get_account_key(db, key_id)
        if not row:
            return
        api_key, provider = row
        payload = await account_quota.fetch_quota(api_key, provider)
        if payload:
            account_quota.save_quota_cache(api_key, payload)
            await db.commit()
    except Exception:
        pass  # 签到已成功，额度重拉失败不阻断


@router.post("/zcode/claim")
async def zcode_claim_now(payload: dict | None = None, db: AsyncSession = Depends(get_db)):
    """ZCode 本地签到。

    - 传 {"key_id": N}：用该账号的 token 注入隔离 profile，签该账号自己的账号。
    - 不传：平台级托管签到（复用/拉起默认客户端），向后兼容。
    """
    key_id = None
    if isinstance(payload, dict):
        key_id = payload.get("key_id")
    if key_id is not None:
        try:
            key_id = int(key_id)
        except (ValueError, TypeError):
            raise HTTPException(400, "key_id 必须是整数")
        row = await account_hub.get_account_key(db, key_id)
        if not row:
            raise HTTPException(404, "账号不存在")
        api_key = row[0]
        token, email = _zcode_key_token(api_key)
        label = email or api_key.alias or f"账号 {key_id}"
        result = await zcode_claim.run_claim_for_account(token, key_id, label, email)
    else:
        result = await zcode_claim.run_managed_claim()
    if result.get("ok"):
        # 签到成功：立即重拉该账号额度写回缓存，并让列表快照失效（否则 TTL 内读旧缓存看不到新额度）
        if key_id is not None:
            await _zcode_refresh_quota(db, int(key_id))
        invalidate_snapshot()
    return result


@router.post("/claim-all")
async def claim_all(payload: dict | None = None, db: AsyncSession = Depends(get_db)):
    """一键签到：批量给所有支持签到的账号（原生 WorkBuddy CN + 插件可签到平台）签到。

    传 {"platform": "zcode"} 时只签该平台的账号（前端签到中心“按平台签到”用）；
    不传则签到全部。单个账号失败不阻断其余；返回逐账号结果。
    """
    platform = None
    if isinstance(payload, dict):
        platform = payload.get("platform")
    results = []

    # 1) 原生账号（ApiKey）：WorkBuddy CN / Qoder / Qoder CN
    cards = await account_hub.list_native_cards(db)
    for card in cards:
        if not card.get("supports_claim"):
            continue
        if platform and str(card.get("platform") or "") != str(platform):
            continue
        row = await account_hub.get_account_key(db, card["id"])
        if not row:
            continue
        api_key, provider = row
        entry = {"scope": "native", "platform": card["platform"], "id": card["id"],
                 "identity": card["identity"][:30]}
        try:
            plat = str(card.get("platform") or "")
            if plat in ("qoder", "qodercn"):
                claim = await qoder_campaign.claim_daily(api_key, provider)
            else:
                claim = await account_quota.claim_workbuddy(api_key, provider)
            entry.update(ok=True, **claim)
        except account_quota.QuotaError as e:
            entry.update(ok=False, error=e.message)
        except qoder_campaign.CampaignError as e:
            entry.update(ok=False, error=e.message, login_required=e.login_required)
        except Exception as e:
            entry.update(ok=False, error=str(e)[:150])
        results.append(entry)

    # 1b) EchoBird 原生 Qoder / Qoder CN（echo 账号，不在 list_native_cards 中）
    for pname in ("qoder", "qodercn"):
        if platform and platform != pname:
            continue
        try:
            echo_entries = native_accounts.list_echo_accounts(pname)
        except Exception:
            echo_entries = []
        for e in echo_entries:
            entry = {"scope": "echo", "platform": pname, "id": f"echo:{pname}:{e['account_id']}",
                     "identity": str(e.get("identity") or "")[:30]}
            creds = (entry.get("extra") or {}).get("creds") or {}
            try:
                claim = await qoder_campaign.claim_with_creds(creds, pname)
                native_accounts._save_echo_summary(
                    pname, e["account_id"], {"daily_claimed_at": int(time.time())})
                entry.update(ok=True, **claim)
            except qoder_campaign.CampaignError as ce:
                entry.update(ok=False, error=ce.message, login_required=ce.login_required)
            except Exception as ce:
                entry.update(ok=False, error=str(ce)[:150])
            results.append(entry)

    # 2) 插件账号（supports_claim 的 application）
    try:
        plugin_cards = await account_hub.list_plugin_cards()
    except Exception:
        plugin_cards = []
    for card in plugin_cards:
        if not card.get("supports_claim") or not card.get("application") or not card.get("account_id"):
            continue
        if platform and str(card["application"]) != str(platform):
            continue
        entry = {"scope": "plugin", "platform": card["application"], "id": card["id"],
                 "identity": card["identity"][:30]}
        try:
            await plugin_accounts.claim_account(card["application"], card["account_id"])
            entry.update(ok=True)
        except PluginError as e:
            already = "已签到" in (e.message or "") or "already" in (e.message or "").lower()
            entry.update(ok=False, error=e.message, already=already)
        except Exception as e:
            entry.update(ok=False, error=str(e)[:150])
        results.append(entry)

    # 3) ZCode 本地客户端签到（按账号隔离 profile：每个 zcode 账号签自己的账号）。
    # 逐个账号依次签到，结果带 key_id/account 归因，前端可精确标记到具体卡片。
    # 仅在“全部”或明确指定 zcode 平台时执行。
    if not platform or str(platform) == "zcode":
      zstatus = zcode_claim.detect_installation()
      if zstatus["installed"] and zstatus["node_ok"]:
        zrows = (await db.execute(
            select(ApiKey).where(ApiKey.oauth_profile == "zcode")
        )).scalars().all()
        for zrow in zrows:
            token, zemail = _zcode_key_token(zrow)
            label = zemail or zrow.alias or f"账号 {zrow.id}"
            zentry = {"scope": "zcode", "platform": "zcode", "id": zrow.id,
                      "key_id": zrow.id, "account": label}
            try:
                zr = await zcode_claim.run_claim_for_account(token, zrow.id, label, zemail)
                zentry.update(ok=zr["ok"], status=zr["status"],
                              error=None if zr["ok"] else zr["message"])
                if zr["ok"]:
                    await _zcode_refresh_quota(db, zrow.id)
            except Exception as e:
                zentry.update(ok=False, error=str(e)[:150])
            results.append(zentry)

    invalidate_snapshot()
    ok_count = sum(1 for r in results if r.get("ok"))
    return {"ok": True, "total": len(results), "claimed": ok_count, "results": results}


@router.post("/sync-model-groups")
async def sync_model_groups(db: AsyncSession = Depends(get_db)):
    """把所有已接入账号物化为可路由的模型组（Provider + 模型 + Strategy）。"""
    try:
        result = await account_routing.sync_all(db, force=True)
    except Exception as e:
        raise HTTPException(502, f"模型组同步失败: {str(e)[:200]}")
    invalidate_snapshot()
    return result


@router.post("/{platform}/login")
async def start_login(platform: str, db: AsyncSession = Depends(get_db)):
    """发起平台账号授权。返回会话信息，轮询复用 GET /api/oauth/status/{session_id}。"""
    catalog = account_hub.PLATFORM_BY_ID.get(platform)
    if not catalog or catalog["kind"] not in ("browser", "oauth", "plugin", "echo"):
        detail = "该平台暂未支持在线接入" if catalog else f"未知平台: {platform}"
        raise HTTPException(400, detail)

    # ---- EchoBird 原生平台（DeepSeek Harness / Kilo / Qoder / QwenWork）----
    if catalog["kind"] == "echo":
        try:
            flow = await native_accounts.start_login(platform)
        except native_accounts.EchoError as e:
            raise HTTPException(400 if e.code in ("initialize_client", "auth") else 502, e.message)
        return {**flow, "platform": platform, "label": catalog["label"],
                "flow": "echo-" + platform}

    # ---- 插件平台（echobird-account-engine）----
    if catalog["kind"] == "plugin":
        plugin_id = catalog.get("plugin_id") or ""
        record = plugin_manager.get_installed(plugin_id)
        if not (record and record.get("enabled") and plugin_manager.engine_binary_for(record)):
            raise HTTPException(400, "该平台的账号插件尚未安装或已停用，请在右侧插件卡片安装")
        try:
            flow = await plugin_accounts.start_login(platform)
        except PluginError as e:
            raise HTTPException(502, e.message)
        return {**flow, "platform": platform, "label": catalog["label"],
                "flow": "plugin-" + platform}

    provider_id = await account_hub.ensure_account_provider(db, platform)

    # ---- CLI 平台的浏览器授权（与 oauth_flow.start 的分发一致）----
    if platform in local_cli.CLI_PLATFORMS:
        try:
            if platform == "zcode":
                flow = await local_cli.start_zcode_session(provider_id)
            elif platform == "cline":
                flow = local_cli.start_cline_session(provider_id)
            elif platform == "amazon-q":
                flow = local_cli.start_amazonq_session(provider_id)
            elif platform == "antigravity":
                client_id, client_secret = _antigravity_credentials()
                if not (client_id and client_secret):
                    raise ValueError("本机未检测到 Antigravity 凭据，请先在「平台管理」页导入或粘贴一次以获取 client_id/client_secret")
                flow = local_cli.start_antigravity_session(provider_id, client_id, client_secret)
            elif platform in local_cli.WORKBUDDY_REGION_BY_PLATFORM:
                flow = await local_cli.start_workbuddy_session(provider_id, platform)
            else:
                raise HTTPException(400, f"平台 '{platform}' 暂无浏览器授权流程")
        except HTTPException:
            raise
        except ValueError as e:
            raise HTTPException(400, str(e))
        except Exception as e:
            raise HTTPException(502, f"发起授权失败: {str(e)[:200]}")
        return {**flow, "platform": platform, "label": catalog["label"]}

    # ---- OAuth profile（claude / codex / copilot / codebuddy / kimi / xai）----
    if not account_hub._login_ready(catalog):
        raise HTTPException(400, "该平台需要先在 oauth_profiles.json 中填写公开 client_id")
    try:
        flow = op.start_session(provider_id, platform)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, f"Failed to contact the OAuth provider: {str(e)[:200]}")
    return {**flow, "platform": platform, "label": catalog["label"]}


def _antigravity_credentials() -> tuple[str, str]:
    """从本机 Antigravity/AGY 凭据文件提取 client_id/client_secret（一次点击即可授权）。"""
    candidates = local_cli.detect_credentials("antigravity")
    for candidate in candidates:
        try:
            data = local_cli.extract_credentials("antigravity", candidate["path"])
        except (ValueError, OSError):
            continue
        client_id = str(data.get("client_id") or "")
        client_secret = str(data.get("client_secret") or "")
        if client_id and client_secret:
            return client_id, client_secret
    return "", ""


@router.post("/{key_id}/refresh")
async def refresh_account(key_id: int, db: AsyncSession = Depends(get_db)):
    """刷新令牌（需要时）并拉取额度快照，写回 quota_cache。"""
    row = await account_hub.get_account_key(db, key_id)
    if not row:
        raise HTTPException(404, "Account not found")
    api_key, provider = row
    platform = api_key.oauth_profile or ""
    invalidate_snapshot()

    # 1) 令牌续期（CLI 平台在 ensure_fresh_token 内部分发；workbuddy 仅在临近过期时才真正请求）
    try:
        await op.ensure_fresh_token(db, api_key, force=True)
        await db.refresh(api_key)
    except Exception as e:  # 续期失败不阻断额度查询
        api_key.last_error = f"refresh: {str(e)[:200]}"
        await db.commit()

    # 2) 额度快照
    card = account_hub.account_card(api_key, provider)
    if account_hub.PLATFORM_BY_ID.get(platform, {}).get("quota"):
        try:
            payload = await account_quota.fetch_quota(api_key, provider)
            if payload:
                account_quota.save_quota_cache(api_key, payload)
                await db.commit()
                await db.refresh(api_key)
                card = account_hub.account_card(api_key, provider)
        except account_quota.QuotaError as e:
            card = {**card, "quota_error": e.message, "login_required": e.login_required}
            if e.login_required:
                api_key.last_error = e.message
                await db.commit()
        except Exception as e:
            card = {**card, "quota_error": str(e)[:200], "login_required": False}
    return card


@router.post("/{key_id}/claim")
async def claim_account(key_id: int, db: AsyncSession = Depends(get_db)):
    """每日签到（WorkBuddy CN / Qoder / Qoder CN），成功后顺带重拉额度。"""
    row = await account_hub.get_account_key(db, key_id)
    if not row:
        raise HTTPException(404, "Account not found")
    api_key, provider = row
    platform = str(api_key.oauth_profile or "")
    try:
        if platform in ("qoder", "qodercn"):
            result = await qoder_campaign.claim_daily(api_key, provider)
        elif platform == "workbuddy":
            result = await account_quota.claim_workbuddy(api_key, provider)
        else:
            raise HTTPException(400, "该平台暂不支持每日签到")
    except qoder_campaign.CampaignError as e:
        raise HTTPException(400, e.message)
    invalidate_snapshot()
    card = account_hub.account_card(api_key, provider)
    if platform in ("qoder", "qodercn"):
        # 签到成功后立即落库签到状态，避免刷新页面时短暂显示未签
        payload = account_quota.load_quota_cache(api_key) or {}
        payload["checkin"] = {
            "claimed": True,
            "claimed_at": int(time.time()),
            "reset_at": result.get("reset_at"),
        }
        payload.setdefault("fetched_at", int(time.time()))
        account_quota.save_quota_cache(api_key, payload)
        await db.commit()
        await db.refresh(api_key)
        card = account_hub.account_card(api_key, provider)
    else:
        try:
            payload = await account_quota.fetch_quota(api_key, provider)
            if payload:
                account_quota.save_quota_cache(api_key, payload)
                await db.commit()
                await db.refresh(api_key)
                card = account_hub.account_card(api_key, provider)
        except Exception:
            pass  # 签到已成功，积分重拉失败不阻断
    return {**card, "claim": result}


@router.get("/plugin-login/{login_id}")
async def plugin_login_status(login_id: str):
    """轮询插件登录会话（claudedesktop 额外返回 awaiting_client_exit）。"""
    result = await plugin_accounts.poll_login(login_id)
    if result.get("status") == "success":
        invalidate_snapshot()
        account = result.get("account") or {}
        application = result.get("application") or ""
        label = account_hub.PLATFORM_BY_ID.get(application, {}).get("label", application)
        return {"status": "success",
                "account": plugin_accounts.account_card(application, account, label) if account else None,
                "cleared": result.get("cleared", False)}
    return result


@router.post("/sync-model-groups")


@router.post("/plugin-login/{login_id}/cancel")
async def plugin_login_cancel(login_id: str):
    await plugin_accounts.cancel_login(login_id)
    return {"ok": True}


# ------------------------------------------------------------------ EchoBird 原生平台账号
# 卡片 id 形如 "echo:{platform}:{account_id}"（DeepSeek Harness / Kilo / Qoder / QwenWork）

@router.get("/echo-login/{login_id}")
async def echo_login_status(login_id: str):
    """轮询 EchoBird 原生平台登录会话。"""
    return await native_accounts.poll_login(login_id)


@router.post("/echo-login/{login_id}/cancel")
async def echo_login_cancel(login_id: str):
    native_accounts.cancel_login(login_id)
    return {"ok": True}


@router.get("/dsh/callback/{login_id}")
async def dsh_callback(login_id: str, code: str = "", error: str = ""):
    """DeepSeek Harness 浏览器回环回调（授权码换 token）。"""
    if error:
        return HTMLResponse(_FAILURE_PAGE.format(error=error))
    await native_accounts.complete_deepseek(login_id, code)
    session = native_accounts._sessions.get(login_id) or {}
    if session.get("status") == "success":
        invalidate_snapshot()
        return HTMLResponse(_SUCCESS_PAGE)
    return HTMLResponse(_FAILURE_PAGE.format(error=session.get("error", "授权失败")))


def _plugin_card(application: str, account: dict) -> dict:
    label = account_hub.PLATFORM_BY_ID.get(application, {}).get("label", application)
    return plugin_accounts.account_card(application, account, label)


def _plugin_application(platform: str) -> dict:
    """校验插件账号操作的平台：kind=plugin，或原生优先平台上已装插件覆盖的（PLUGIN_SURFACED_APPS）。"""
    catalog = account_hub.PLATFORM_BY_ID.get(platform)
    if catalog and catalog["kind"] == "plugin":
        return catalog
    if platform in account_hub.PLUGIN_SURFACED_APPS:
        for record in plugin_manager.installed_plugins().values():
            if record.get("enabled") and plugin_manager.engine_binary_for(record) \
                    and platform in plugin_manager.applications_of_installed(record):
                return catalog or {"id": platform, "label": platform}
    raise HTTPException(400, f"Platform '{platform}' is not plugin-backed")


def _echo_platform(platform: str) -> str:
    if platform not in account_hub.ECHO_SURFACED_APPS:
        raise HTTPException(400, f"Platform '{platform}' is not an EchoBird-native platform")
    return platform


@router.post("/echo/{platform}/{account_id}/refresh")
async def echo_account_refresh(platform: str, account_id: str):
    _echo_platform(platform)
    try:
        card = await native_accounts.refresh_account(platform, account_id)
        invalidate_snapshot()
        return card
    except EchoError as e:
        raise HTTPException(502, e.message)


@router.post("/echo/{platform}/{account_id}/claim")
async def echo_account_claim(platform: str, account_id: str):
    """Qoder / Qoder CN（echo 账号）每日签到，成功后回写签到状态到存储。"""
    _echo_platform(platform)
    if platform not in ("qoder", "qodercn"):
        raise HTTPException(400, "该平台暂不支持每日签到")
    entries = native_accounts.list_echo_accounts(platform)
    entry = next((e for e in entries if e["account_id"] == account_id), None)
    if not entry:
        raise HTTPException(404, "Account not found")
    creds = (entry.get("extra") or {}).get("creds") or {}
    try:
        result = await qoder_campaign.claim_with_creds(creds, platform)
    except qoder_campaign.CampaignError as e:
        raise HTTPException(400, e.message)
    # 已签（新签或此前已领）都回写状态，供卡片展示；claimed_at 取查询时刻
    native_accounts._save_echo_summary(
        platform, account_id, {"daily_claimed_at": int(time.time())})
    # 同步更新内存 entry，保证本次返回的卡片也显示已签
    (entry.get("extra") or {}).setdefault("summary", {})["daily_claimed_at"] = int(time.time())
    invalidate_snapshot()
    card = native_accounts.account_card(platform, entry)
    return {**card, "claim": result}


@router.delete("/echo/{platform}/{account_id}")
async def echo_account_delete(platform: str, account_id: str, db: AsyncSession = Depends(get_db)):
    _echo_platform(platform)
    try:
        await native_accounts.delete_account(platform, account_id)
    except EchoError as e:
        raise HTTPException(502, e.message)
    try:
        await account_routing.remove_group(db, "echo", platform, account_id)
    except Exception:
        pass
    invalidate_snapshot()
    return {"ok": True}


@router.post("/plugin/{application}/{account_id}/refresh")
async def plugin_account_refresh(application: str, account_id: str):
    _plugin_application(application)
    try:
        account = await plugin_accounts.refresh_account(application, account_id)
    except PluginError as e:
        raise HTTPException(502, e.message)
    invalidate_snapshot()
    return _plugin_card(application, account)


@router.post("/plugin/{application}/{account_id}/claim")
async def plugin_account_claim(application: str, account_id: str):
    _plugin_application(application)
    try:
        account = await plugin_accounts.claim_account(application, account_id)
        invalidate_snapshot()
        return _plugin_card(application, account)
    except PluginError as e:
        raise HTTPException(502, e.message)


@router.delete("/plugin/{application}/{account_id}")
async def plugin_account_delete(application: str, account_id: str, db: AsyncSession = Depends(get_db)):
    _plugin_application(application)
    try:
        await plugin_accounts.remove_account(application, account_id)
    except PluginError as e:
        raise HTTPException(502, e.message)
    try:
        await account_routing.remove_group(db, "plugin", application, account_id)
    except Exception:
        pass
    invalidate_snapshot()
    return {"ok": True}
