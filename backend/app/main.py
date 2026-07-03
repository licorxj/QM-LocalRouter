from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from app.config import settings
from app.database import init_db
from app.routers import providers, api_keys, models, strategies, logs, dashboard, proxy, icons, backup, conversations
from app.routers.settings import router as settings_router
from app.routers import providers, api_keys, models, strategies, logs, dashboard, proxy, icons, backup, conversations, service

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    # Check auto-backup on startup
    try:
        await backup.check_auto_backup()
    except Exception:
        pass
    yield


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Management APIs
app.include_router(providers.router)
app.include_router(api_keys.router)
app.include_router(models.router)
app.include_router(strategies.router)
app.include_router(logs.router)
app.include_router(dashboard.router)

# Icon search/download
app.include_router(icons.router)

# Backup/restore
app.include_router(backup.router)
app.include_router(conversations.router)

app.include_router(service.router)
# Proxy endpoints (OpenAI-compatible)
app.include_router(settings_router)
app.include_router(proxy.router)


@app.get("/api/health")
async def health():
    return {"status": "ok", "version": settings.APP_VERSION}
