import os
import sys

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from contextlib import asynccontextmanager
from app.config import settings
from app.database import init_db
from app.routers import providers, api_keys, models, strategies, logs, dashboard, proxy, icons, backup, conversations
from app.routers.settings import router as settings_router, _get_settings
from app.routers import providers, api_keys, models, strategies, logs, dashboard, proxy, icons, backup, conversations, service
from app.routers import online_providers
from app.routers import free_models
from app.routers import client_keys
from app.routers import oauth_login
from app.routers import oauth_flow
from app.routers import cli_import
from app.routers import cloud_meta
from app.routers import app_update
from app.routers import accounts
from app.routers import plugins as plugins_router
from app.routers import script_plugins as script_plugins_router
from app.routers import scheduled_tasks as scheduled_tasks_router
from app.version import get_version_info


class DynamicCORSMiddleware(BaseHTTPMiddleware):
    """CORS middleware that reads lan_access setting dynamically."""
    async def dispatch(self, request: Request, call_next):
        origin = request.headers.get("origin", "")
        app_settings = _get_settings()
        lan_access = app_settings.get("lan_access", False)

        # Preflight
        if request.method == "OPTIONS":
            response = Response()
            if lan_access or not origin:
                response.headers["Access-Control-Allow-Origin"] = "*"
            elif origin in settings.CORS_ORIGINS:
                response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Allow-Methods"] = "*"
            response.headers["Access-Control-Allow-Headers"] = "*"
            response.headers["Access-Control-Allow-Credentials"] = "true"
            return response

        response = await call_next(request)

        if lan_access or not origin:
            response.headers["Access-Control-Allow-Origin"] = "*"
        elif origin in settings.CORS_ORIGINS:
            response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Methods"] = "*"
        response.headers["Access-Control-Allow-Headers"] = "*"
        response.headers["Access-Control-Allow-Credentials"] = "true"

        return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    import asyncio
    await init_db()
    # 预热版本信息：git describe 在 Windows 上同步 spawn 子进程（50-100ms），
    # 放到启动阶段用线程池执行，避免首个 /api/health、/api/version 请求阻塞事件循环。
    try:
        await asyncio.to_thread(get_version_info)
    except Exception:
        pass
    # Check auto-backup on startup
    try:
        await backup.check_auto_backup()
    except Exception:
        pass
    # 插件索引后台同步（静默，失败不影响启动）
    try:
        from app.services import plugin_manager
        plugin_manager.startup_sync()
    except Exception:
        pass
    # 脚本插件定时任务调度器（静默启动）
    try:
        from app.services import script_scheduler
        await script_scheduler.start()
    except Exception:
        pass
    # 恢复已启用的 apiserver 型脚本插件（本地网关服务）
    try:
        from app.services import apiserver_manager
        await apiserver_manager.recover_enabled()
    except Exception:
        pass
    yield
    # 停止所有 apiserver 型插件进程
    try:
        from app.services import apiserver_manager
        await apiserver_manager.shutdown_all()
    except Exception:
        pass
    # 停止定时任务调度器
    try:
        from app.services import script_scheduler
        await script_scheduler.stop()
    except Exception:
        pass
    # 关闭转发层共享的 httpx client 池（forwarder/ssl_relax 进程级复用）
    try:
        from app.utils import ssl_relax
        await ssl_relax.aclose_all()
    except Exception:
        pass
    # 停掉所有插件引擎子进程
    try:
        from app.services import plugin_host
        await plugin_host.stop_all()
    except Exception:
        pass


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    lifespan=lifespan,
)

app.add_middleware(DynamicCORSMiddleware)

# Management APIs
app.include_router(online_providers.router)
app.include_router(free_models.router)
app.include_router(client_keys.router)
app.include_router(client_keys.endpoints_router)
app.include_router(oauth_login.router)
app.include_router(oauth_flow.router)
app.include_router(cli_import.router)
app.include_router(cloud_meta.router)
app.include_router(app_update.router)
app.include_router(plugins_router.router)
app.include_router(script_plugins_router.router)
app.include_router(scheduled_tasks_router.router)
app.include_router(accounts.router)
app.include_router(providers.router)
app.include_router(proxy.alias_router)
app.include_router(api_keys.router)
app.include_router(models.router)
app.include_router(strategies.router)
app.include_router(logs.router)
app.include_router(dashboard.router)
from app.routers import monitor  # 可视化监控（SSE 实时推流）
app.include_router(monitor.router)

# Icon search/download
app.include_router(icons.router)

# Backup/restore
app.include_router(backup.router)
app.include_router(conversations.router)

app.include_router(service.router)
# Proxy endpoints (OpenAI-compatible)
app.include_router(settings_router)
app.include_router(proxy.router)


@app.get("/api/ping")
async def ping():
    """轻量探活：无 DB、无子进程，用于测量框架本身的基础 RTT。"""
    return {"pong": True}


@app.get("/api/health")
async def health():
    info = get_version_info()
    return {
        "status": "ok",
        "version": info["version"],
        "git_tag": info["git_tag"],
        "git_commit": info["git_commit"],
        "git_dirty": info["git_dirty"],
        "build_type": info["build_type"],
    }


@app.get("/api/version")
async def version():
    """Version identifier plus git/release metadata for the UI footer."""
    return get_version_info()


# --- Production frontend serving ---
# In production the built SPA (frontend/dist) is served by this same process,
# so a single uvicorn handles UI + API on one port. The Vite dev server is
# used during development and takes precedence there (nginx fronts dist in Docker).
_repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_FRONTEND_CANDIDATES = []
if getattr(sys, "frozen", False):
    # PyInstaller one-file build ships frontend/dist next to the executable.
    _FRONTEND_CANDIDATES.append(os.path.join(os.path.dirname(sys.executable), "frontend", "dist"))
_FRONTEND_CANDIDATES.append(os.path.join(_repo_root, "frontend", "dist"))
_FRONTEND_DIST = next((c for c in _FRONTEND_CANDIDATES if os.path.isdir(c)), _FRONTEND_CANDIDATES[-1])

if os.path.isdir(_FRONTEND_DIST):
    _assets_dir = os.path.join(_FRONTEND_DIST, "assets")
    if os.path.isdir(_assets_dir):
        app.mount("/assets", StaticFiles(directory=_assets_dir), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str):
        # Unknown API paths must stay JSON 404s, never the SPA shell
        if full_path.split("/", 1)[0] in ("api", "v1", "k"):
            raise HTTPException(status_code=404, detail="Not Found")
        candidate = os.path.abspath(os.path.join(_FRONTEND_DIST, full_path))
        if full_path and candidate.startswith(_FRONTEND_DIST + os.sep) and os.path.isfile(candidate):
            return FileResponse(candidate)
        return FileResponse(os.path.join(_FRONTEND_DIST, "index.html"))
