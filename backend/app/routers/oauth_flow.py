"""API routes for provider OAuth browser login (start / callback / status)."""
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.api_key import ApiKey
from app.models.provider import Provider
from app.schemas.schemas import ApiKeyOut
from app.services import oauth_providers as op

router = APIRouter(prefix="/api/oauth", tags=["oauth-flow"])

_SUCCESS_PAGE = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>LocalRouter</title>
<style>body{{margin:0;height:100vh;display:flex;align-items:center;justify-content:center;background:#0b1220;
color:#e2e8f0;font-family:system-ui,sans-serif}}.c{{text-align:center}}</style></head>
<body><div class="c"><h2>✓ 授权成功</h2><p>令牌已保存到 LocalRouter，可关闭此窗口</p></div></body></html>"""

_FAILURE_PAGE = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>LocalRouter</title>
<style>body{{margin:0;height:100vh;display:flex;align-items:center;justify-content:center;background:#0b1220;
color:#fca5a5;font-family:system-ui,sans-serif}}.c{{text-align:center}}</style></head>
<body><div class="c"><h2>授权失败</h2><p>{error}</p></div></body></html>"""


class OauthStart(BaseModel):
    provider_id: int
    profile_id: str = ""  # defaults to the provider's auth_type


@router.post("/start")
async def oauth_start(data: OauthStart, db: AsyncSession = Depends(get_db)):
    provider = await db.get(Provider, data.provider_id)
    if not provider:
        raise HTTPException(404, "Provider not found")
    profile_id = data.profile_id or provider.auth_type
    if not profile_id or profile_id == "api_key":
        raise HTTPException(400, "This provider is not configured for OAuth login (auth_type is 'api_key')")

    # CLI 平台（local_cli）的浏览器授权桥接：供应商密钥对话框与 CLI 导入对话框
    # 共用同一套本地会话机制，轮询由 /api/oauth/status 按会话来源分发
    from app.services import local_cli
    cli_platform = local_cli.get_cli_platform(profile_id)
    if cli_platform:
        try:
            if profile_id == "zcode":
                flow = await local_cli.start_zcode_session(data.provider_id)
            elif profile_id == "cline":
                flow = local_cli.start_cline_session(data.provider_id)
            elif profile_id == "amazon-q":
                flow = local_cli.start_amazonq_session(data.provider_id)
            elif profile_id == "antigravity":
                flow = local_cli.start_antigravity_session(data.provider_id, "", "")
            elif profile_id in local_cli.WORKBUDDY_REGION_BY_PLATFORM:
                flow = await local_cli.start_workbuddy_session(data.provider_id, profile_id)
            else:
                raise HTTPException(400, f"CLI platform '{profile_id}' has no browser login flow")
        except HTTPException:
            raise
        except ValueError as e:
            raise HTTPException(400, str(e))
        except Exception as e:
            raise HTTPException(502, f"Failed to start browser flow: {str(e)[:200]}")
        return {**flow, "profile_id": profile_id, "label": cli_platform.get("label", profile_id)}

    profile = op.get_profile(profile_id)
    if not profile:
        raise HTTPException(400, f"Unknown OAuth profile: {profile_id}")
    try:
        flow = op.start_session(data.provider_id, profile_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, f"Failed to contact the OAuth provider: {str(e)[:200]}")
    return {**flow, "profile_id": profile_id, "label": profile.get("label", profile_id)}


@router.get("/callback/{session_id}")
async def oauth_callback(session_id: str, code: str = "", state: str = "", error: str = ""):
    session = op.get_session(session_id)
    if not session:
        return HTMLResponse(_FAILURE_PAGE.format(error="会话已过期，请重新发起登录"), status_code=410)
    if error:
        op.fail_session(session_id, error)
        return HTMLResponse(_FAILURE_PAGE.format(error=error), status_code=200)
    if state != session["state"] or not code:
        op.fail_session(session_id, "state mismatch")
        return HTMLResponse(_FAILURE_PAGE.format(error="state 校验失败"), status_code=400)

    profile = op.get_profile(session["profile_id"])
    try:
        tokens = await op.exchange_code(profile, code, session)
        from app.database import async_session
        async with async_session() as db:
            key = await op.store_oauth_key(db, session["provider_id"], session["profile_id"], tokens)
        op.complete_session(session_id, key.id)
    except Exception as e:
        op.fail_session(session_id, str(e)[:200])
        return HTMLResponse(_FAILURE_PAGE.format(error=str(e)[:200]), status_code=200)
    return HTMLResponse(_SUCCESS_PAGE)


@router.get("/status/{session_id}")
async def oauth_status(session_id: str, db: AsyncSession = Depends(get_db)):
    session = op.get_session(session_id)
    if not session:
        # CLI 平台浏览器授权会话的轮询桥接（zcode / amazon-q / workbuddy 需主动拉取；
        # cline / antigravity 由本地回调页完成，此处只读状态）
        from app.services import local_cli
        cli = local_cli.get_cli_session(session_id)
        if not cli:
            return {"status": "expired"}
        if cli["status"] == "pending":
            if cli["flow"] == "cli-aws-device":
                result = await local_cli.poll_amazonq_session(session_id, db)
            elif cli["flow"] == "cli-workbuddy":
                result = await local_cli.poll_workbuddy_session(session_id, db)
            elif cli["flow"] == "cli-zcode":
                result = await local_cli.poll_zcode_session(session_id, db)
            else:
                result = {"status": "pending"}
            return result
        return {"status": cli["status"], "key_id": cli.get("key_id"), "error": cli.get("error", "")}
    if session.get("flow") in ("device", "codebuddy") and session["status"] == "pending":
        # Device flows: each status poll drives one token-poll attempt upstream
        result = await op.poll_device_session(session_id, db)
        result.setdefault("user_code", session.get("user_code", ""))
        result.setdefault("verification_uri", session.get("verification_uri", ""))
        return result
    return {"status": session["status"], "key_id": session["key_id"], "error": session["error"]}


@router.get("/profiles")
async def oauth_profiles():
    return op.load_profiles()


@router.post("/refresh/{key_id}", response_model=ApiKeyOut)
async def oauth_refresh(key_id: int, db: AsyncSession = Depends(get_db)):
    api_key = await db.get(ApiKey, key_id)
    if not api_key or not api_key.oauth_profile:
        raise HTTPException(404, "OAuth key not found")
    await op.ensure_fresh_token(db, api_key, force=True)
    await db.refresh(api_key)
    from app.routers.api_keys import _key_to_out
    return _key_to_out(api_key)
