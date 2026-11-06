"""免费模型渠道发现，移植自 dsh-our-free-model 插件的清单能力。

插件聚合了两个免费网关：

- OpenCode Zen（https://opencode.ai）：``GET /inference/v1/models`` 列出免费车道
  模型；对话入口 ``/inference/openai/v1/chat/completions``。**该车道上游对
  TLS 客户端指纹敏感**（实测 Go 参考网关 200，Node/curl/浏览器指纹均 429），
  且免费额度按出口网段计（共享 NAT 出口常被耗尽），因此本渠道不能从
  Python 后端直连——加入时指向本地运行的 opencode2api 参考网关
  （https://github.com/jasonxu114514/opencode2api，OpenAI 兼容）。
- Kilo AI（https://kilo.ai）：``GET /api/gateway/models`` 是 OpenAI 兼容目录，
  免费池为其中 ``isFree: true`` 的切片；对话入口
  ``/api/gateway/chat/completions`` 完全免密——实测不带 Authorization 头可用，
  带假 Bearer 反而被拒（INVALID_TOKEN），因此该渠道的 Provider 以
  ``auth_type="none"`` 建立，转发层据此跳过 Authorization 头。
"""
import asyncio
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

import httpx

# 与 dsh-our-free-model 的 UPSTREAM_BASE / KILO_BASE 同源，可用环境变量覆盖
OPENCODE_BASE = (os.getenv("FREE_MODELS_OPENCODE_BASE") or "https://opencode.ai").rstrip("/")
KILO_BASE = (os.getenv("FREE_MODELS_KILO_BASE") or "https://api.kilo.ai/api/gateway").rstrip("/")

CACHE_TTL_SECONDS = 600

_channels_cache: dict | None = None
_channels_cache_at = 0.0


def read_gateway_config() -> dict | None:
    """读取根目录 opencode2api 网关 config.json 的 listen + server_keys，
    返回 {url, key, path}；找不到文件或未配置 listen 时返回 None。

    listen 形如 ``127.0.0.1:12005`` → 网关 OpenAI 兼容地址 ``http://127.0.0.1:12005/v1``；
    server_keys 取第一个作为网关密钥（如 ["admin"] → "admin"）。
    """
    here = os.path.dirname(os.path.abspath(__file__))  # backend/app/services
    candidates = [
        os.path.dirname(os.path.dirname(os.path.dirname(here))),  # 仓库根（services→app→backend→根）
        os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else "",
        os.getcwd(),
    ]
    for root in candidates:
        if not root:
            continue
        p = os.path.join(root, "config.json")
        try:
            with open(p, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        listen = str(data.get("listen") or "").strip()
        if not listen:
            continue
        host = listen if "://" in listen else "http://" + listen
        keys = data.get("server_keys") or []
        return {
            "url": host.rstrip("/") + "/v1",
            "key": str(keys[0]).strip() if keys else "",
            "path": p,
        }
    return None


_gw_lock: asyncio.Lock | None = None


async def _probe_gateway(base_url: str) -> bool:
    """网关探活：/models 可达（含 401/403 等鉴权拒绝）即视为在运行。"""
    try:
        async with httpx.AsyncClient(timeout=2.0, trust_env=False) as client:
            resp = await client.get(base_url.rstrip("/") + "/models")
        return resp.status_code < 500
    except Exception:
        return False


async def ensure_local_gateway(base_url: str, wait_seconds: int = 20) -> bool:
    """确保本地 opencode2api 网关在运行：探活失败时自动拉起根目录的
    opencode2api.exe（读取同目录 config.json），并等待其就绪。

    仅当请求地址与 config.json 的 listen 匹配时才代管，其他本地服务
    （如 opencode serve 的 4096）不受影响。返回网关是否就绪。
    """
    cfg = read_gateway_config()
    if not cfg:
        return False
    if not base_url.rstrip("/").startswith(cfg["url"].rstrip("/")):
        return False
    if await _probe_gateway(base_url):
        return True
    root = os.path.dirname(cfg["path"])
    exe = next((os.path.join(root, name) for name in
                ("opencode2api.exe", "opencode.exe", "opencode2api", "opencode")
                if os.path.isfile(os.path.join(root, name))), None)
    if not exe:
        return False

    global _gw_lock
    if _gw_lock is None:
        _gw_lock = asyncio.Lock()
    async with _gw_lock:
        if await _probe_gateway(base_url):
            return True  # 并发请求时已有拉起
        try:
            log_dir = os.path.join(root, "run")
            os.makedirs(log_dir, exist_ok=True)
            logf = open(os.path.join(log_dir, "opencode2api.log"), "ab")
            kwargs: dict = {}
            if sys.platform == "win32":
                kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            subprocess.Popen([exe], cwd=root, stdout=logf, stderr=subprocess.STDOUT, **kwargs)
        except Exception:
            return False
        for _ in range(wait_seconds):
            await asyncio.sleep(1)
            if await _probe_gateway(base_url):
                return True
    return False


def _channel_meta() -> list[dict]:
    """两个免费渠道的固定元信息（清单实时向上游拉取，不存静态快照）。"""
    return [
        {
            "id": "opencode-free",
            "name": "OpenCode Zen 免费车道",
            "base_url": f"{OPENCODE_BASE}/inference/openai/v1",
            "protocol": "openai",
            "auth_type": "none",
            "requires_gateway": True,
            "default_gateway_url": "http://127.0.0.1:12005/v1",
            "default_gateway_key": "admin",
            "description": "OpenCode Zen 公共网关的免费车道；上游校验客户端指纹，经本地 opencode2api 网关接入（未运行时后端自动拉起根目录网关）",
            "notice": "上游对客户端 TLS 指纹敏感且免费额度按出口网段计，Python 直连会被 429；"
                      "接入参数自动读取根目录 config.json（listen + server_keys），网关未运行时"
                      "转发前会自动启动根目录的 opencode2api.exe。若只需免密直连，请使用 Kilo 免费池",
            "icon": f"{OPENCODE_BASE}/favicon-96x96-v3.png",
            "homepage": "https://github.com/jasonxu114514/opencode2api",
            "source": "dsh-our-free-model 同源免费车道（OpenCode Zen）",
        },
        {
            "id": "kilo-free",
            "name": "Kilo 免费池",
            "base_url": KILO_BASE,
            "protocol": "openai",
            "auth_type": "none",
            "requires_gateway": False,
            "description": "Kilo AI 公共网关免费模型池，完全免密（含 kilo-auto/free 自动路由）",
            "notice": "上游声明 prompt 可能被记录用于改进服务，请勿发送敏感内容",
            "icon": "https://kilo.ai/favicon.ico",
            "homepage": "https://kilo.ai",
            "source": "dsh-our-free-model 同源免费渠道（Kilo 公共网关）",
        },
    ]


def _normalize_opencode(entry: dict) -> dict | None:
    """OpenCode 清单条目 → 统一模型字段；价格非 0 的付费模型直接剔除。

    清单里混有大量付费模型（80 个里只有约 10 个免费，2026-11-07 实测），
    免费判定 = 输入与输出价格均为 0。
    """
    mid = (entry.get("id") or "").strip()
    if not mid:
        return None
    cost = (entry.get("cost") or {}).get("text") or {}
    input_price = ((cost.get("input") or {}).get("price"))
    output_price = (cost.get("output") or {}).get("price")
    if input_price not in (0, 0.0, None) or output_price not in (0, 0.0, None):
        return None
    limit = entry.get("limit") or {}
    caps = entry.get("capabilities") or {}
    modalities = entry.get("modalities") or {}
    return {
        "model_id": mid,
        "display_name": (entry.get("name") or mid).strip(),
        "description": (entry.get("description") or "").strip()[:300],
        "context_window": limit.get("context"),
        "max_output": limit.get("output"),
        "is_multimodal": "image" in (modalities.get("input") or []),
        "model_type": "text",
        "supports_tools": bool((caps.get("tools") or {}).get("supported")),
        "supports_reasoning": bool((caps.get("reasoning") or {}).get("supported")),
    }


def _normalize_kilo(entry: dict) -> dict | None:
    """Kilo 清单条目 → 统一模型字段；调用方已按 isFree 过滤。"""
    mid = (entry.get("id") or "").strip()
    if not mid:
        return None
    provider = entry.get("top_provider") or {}
    arch = entry.get("architecture") or {}
    params = entry.get("supported_parameters") or []
    return {
        "model_id": mid,
        "display_name": (entry.get("name") or mid).strip(),
        "description": (entry.get("description") or "").strip()[:300],
        "context_window": entry.get("context_length") or provider.get("context_length"),
        "max_output": provider.get("max_completion_tokens"),
        "is_multimodal": "image" in (arch.get("input_modalities") or []),
        "model_type": "text",
        "supports_tools": "tools" in params,
        "supports_reasoning": "reasoning" in params,
    }


async def _fetch_one(client: httpx.AsyncClient, url: str) -> list[dict]:
    resp = await client.get(url, headers={"accept": "application/json"}, timeout=15)
    resp.raise_for_status()
    payload = resp.json()
    data = payload.get("data") if isinstance(payload, dict) else None
    return data if isinstance(data, list) else []


async def _fetch_channels() -> list[dict]:
    """并发拉取两个渠道的免费模型清单；单渠道失败不影响另一个。"""
    metas = _channel_meta()
    async with httpx.AsyncClient(trust_env=False) as client:
        results = await asyncio.gather(
            _fetch_one(client, f"{OPENCODE_BASE}/inference/v1/models"),
            _fetch_one(client, f"{KILO_BASE}/models"),
            return_exceptions=True,
        )

    opencode_rows, kilo_rows = results
    channels = []

    for meta, rows, normalizer, is_free_filter in (
        (metas[0], opencode_rows, _normalize_opencode, False),
        (metas[1], kilo_rows, _normalize_kilo, True),
    ):
        models: list[dict] = []
        if isinstance(rows, list):
            for entry in rows:
                if not isinstance(entry, dict):
                    continue
                if is_free_filter and not entry.get("isFree"):
                    continue  # Kilo 只取免费切片，付费 id 会 401
                item = normalizer(entry)
                if item:
                    models.append(item)
        models.sort(key=lambda m: m["display_name"].lower())
        channels.append({
            **meta,
            "models_count": len(models),
            "models": models,
            "ok": isinstance(rows, list),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })

    # 需网关的渠道：根目录 config.json 已配置时自动透出接入参数（前端免填直接加入）
    gw = read_gateway_config()
    for ch in channels:
        if ch.get("requires_gateway"):
            if gw:
                ch["default_gateway_url"] = gw["url"]
                ch["default_gateway_key"] = gw["key"]
                ch["auto_gateway"] = {"url": gw["url"], "key": gw["key"]}
            else:
                ch["auto_gateway"] = None
    return channels


async def get_free_channels(force: bool = False) -> list[dict]:
    """带 TTL 的渠道清单；上游拉取失败时退回上次缓存。"""
    global _channels_cache, _channels_cache_at
    now = time.monotonic()
    if not force and _channels_cache is not None and now - _channels_cache_at < CACHE_TTL_SECONDS:
        return _channels_cache
    try:
        channels = await _fetch_channels()
        _channels_cache = channels
        _channels_cache_at = now
    except Exception:
        if _channels_cache is not None:
            return _channels_cache
        raise
    return channels


def find_channel(channels: list[dict], channel_id: str) -> dict | None:
    for channel in channels:
        if channel["id"] == channel_id:
            return channel
    return None
