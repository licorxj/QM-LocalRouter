"""账号接入中心：平台目录 + 账号卡片聚合（移植 EchoBird AccountCenter）。

账号沿用本项目的存储模型：ApiKey.oauth_profile 非空即视为「接入的账号」，
Provider 按 auth_type 与平台对应（CLI 平台由 local_cli.ensure_provider 自动建档，
OAuth 平台在首次登录时同样自动建档）。额度缓存挂在 ApiKey.quota_cache。
"""
import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.api_key import ApiKey
from app.models.provider import Provider
from app.services import account_quota
from app.services import local_cli
from app.services import native_accounts
from app.services import oauth_providers as op
from app.services import plugin_accounts
from app.services import plugin_manager

# 平台目录。kind: browser=浏览器授权 / oauth=OAuth 授权 / import=走平台管理页导入
# / unsupported=暂未支持（EchoBird 列表对齐，仅展示官网入口）
ACCOUNT_PLATFORMS: list[dict] = [
    {"id": "workbuddy", "label": "WorkBuddy", "domain": "codebuddy.cn",
     "website": "https://www.codebuddy.cn/work/", "kind": "browser", "quota": True, "claim": True,
     "icon": "workbuddy", "note": "腾讯 CodeBuddy 国内版"},
    {"id": "workbuddy-intl", "label": "WorkBuddy AI", "domain": "workbuddy.ai",
     "website": "https://www.workbuddy.ai/", "kind": "browser", "quota": True, "claim": False,
     "icon": "workbuddyai", "note": "WorkBuddy 国际版"},
    {"id": "zcode", "label": "ZCode · BigModel", "domain": "bigmodel.cn",
     "website": "https://bigmodel.cn/glm-coding", "kind": "browser", "quota": True, "claim": False,
     "icon": "zcode", "note": "z.ai / 智谱 GLM Coding Plan"},
    {"id": "claude", "label": "Claude Code", "domain": "claude.ai",
     "website": "https://claude.ai/", "kind": "oauth", "quota": True, "claim": False,
     "icon": "claudecode", "note": "Pro/Max 订阅 OAuth"},
    {"id": "codex", "label": "ChatGPT / Codex", "domain": "openai.com",
     "website": "https://openai.com/", "kind": "oauth", "quota": True, "claim": False,
     "icon": "codex", "note": "ChatGPT 订阅 OAuth"},
    {"id": "cline", "label": "Cline", "domain": "cline.bot",
     "website": "https://cline.bot/cli", "kind": "browser", "quota": False, "claim": False,
     "icon": "cline", "note": "免费模型通道"},
    {"id": "antigravity", "label": "Antigravity", "domain": "antigravity.google",
     "website": "https://antigravity.google/download", "kind": "browser", "quota": False, "claim": False,
     "icon": "antigravity", "note": "Google AGY CLI"},
    {"id": "github-copilot", "label": "GitHub Copilot", "domain": "github.com",
     "website": "https://github.com/features/copilot", "kind": "oauth", "quota": False, "claim": False,
     "icon": "copilot", "note": "设备码授权"},
    {"id": "codebuddy-cn", "label": "CodeBuddy CN", "domain": "copilot.tencent.com",
     "website": "https://copilot.tencent.com/", "kind": "oauth", "quota": False, "claim": False,
     "icon": "codebuddy", "note": "腾讯 CodeBuddy 插件通道"},
    {"id": "kimi-coding", "label": "Kimi Code", "domain": "kimi.com",
     "website": "https://www.kimi.com/", "kind": "oauth", "quota": False, "claim": False,
     "icon": "kimi", "note": "Kimi Coding Plan（需填 client_id）"},
    {"id": "xai-oauth", "label": "Grok", "domain": "x.ai",
     "website": "https://x.ai/", "kind": "oauth", "quota": False, "claim": False,
     "icon": "grok", "note": "xAI OAuth（需填 client_id）"},
    {"id": "amazon-q", "label": "Amazon Q", "domain": "aws.amazon.com",
     "website": "https://aws.amazon.com/q/developer/", "kind": "browser", "quota": False, "claim": False,
     "icon": "amazonq", "note": "Kiro / AWS SSO"},
    {"id": "opencode", "label": "OpenCode", "domain": "opencode.ai",
     "website": "https://opencode.ai/", "kind": "import", "quota": False, "claim": False,
     "icon": "opencode", "note": "本地服务端接入，在平台管理页导入"},
    # 插件平台（echobird-plugin 账号引擎；安装对应插件后点亮）
    {"id": "claudedesktop", "label": "Claude Desktop", "domain": "claude.com",
     "website": "https://claude.com/download", "kind": "plugin", "quota": True, "claim": False,
     "icon": "claudedesktop", "plugin_id": "echobird-community.claude-desktop-accounts",
     "note": "需安装 Claude Desktop 插件"},
    {"id": "doubaowork", "label": "豆包工作", "domain": "doubao.com",
     "website": "https://www.doubao.com/work", "kind": "plugin", "quota": True, "claim": True,
     "icon": "doubaowork", "plugin_id": "echobird-community.doubao-work-accounts",
     "note": "需安装豆包工作插件"},
    {"id": "dola", "label": "Dola", "domain": "dola.com",
     "website": "https://www.dola.com/download/desktop", "kind": "plugin", "quota": True, "claim": True,
     "icon": "dola", "plugin_id": "echobird-community.dola-accounts",
     "note": "需安装 Dola 插件"},
    {"id": "trae", "label": "TRAE", "domain": "trae.ai",
     "website": "https://www.trae.ai/", "kind": "plugin", "quota": True, "claim": True,
     "icon": "trae", "plugin_id": "echobird-community.trae-accounts",
     "note": "需安装 TRAE 插件（国际版）"},
    {"id": "traecn", "label": "TRAE CN", "domain": "trae.cn",
     "website": "https://www.trae.cn/", "kind": "plugin", "quota": True, "claim": True,
     "icon": "traecn", "plugin_id": "echobird-community.trae-cn-accounts",
     "note": "需安装 TRAE CN 插件（国内版）"},
    # EchoBird 原生平台（协议移植自主仓库 services/*_accounts.rs；读写 ~/.echobird 兼容存储）
    {"id": "deepseek", "label": "DeepSeek Harness", "domain": "deepseek.com",
     "website": "https://www.deepseek.com/", "kind": "echo", "quota": True, "claim": False,
     "icon": "deepseek", "note": "浏览器 PKCE 授权，余额按 CNY/USD 钱包显示"},
    {"id": "kilo", "label": "Kilo", "domain": "kilo.ai",
     "website": "https://kilo.ai/", "kind": "echo", "quota": True, "claim": False,
     "icon": "kilo", "note": "官方设备码授权"},
    {"id": "qoder", "label": "Qoder", "domain": "qoder.com",
     "website": "https://qoder.com/", "kind": "echo", "quota": True, "claim": True,
     "icon": "qoder", "note": "浏览器授权（国际版）"},
    {"id": "qodercn", "label": "Qoder CN", "domain": "qoder.com.cn",
     "website": "https://qoder.cn/", "kind": "echo", "quota": True, "claim": True,
     "icon": "qodercn", "note": "浏览器授权（国内版）"},
    {"id": "qwenwork", "label": "QwenWork", "domain": "qwenwork.ai",
     "website": "https://qwenwork.ai/", "kind": "echo", "quota": True, "claim": False,
     "icon": "qwenwork", "note": "需已装 QwenWork 官方客户端"},
    {"id": "qwenworkcn", "label": "QwenWork CN", "domain": "qwenwork.cn",
     "website": "https://qwenwork.cn/", "kind": "echo", "quota": True, "claim": False,
     "icon": "qwenworkcn", "note": "需已装 QwenWork CN 官方客户端"},
    {"id": "cursor", "label": "Cursor", "domain": "cursor.com",
     "website": "https://cursor.com/", "kind": "unsupported", "quota": False, "claim": False,
     "icon": "cursor", "note": "暂未支持"},
]

PLATFORM_BY_ID = {p["id"]: p for p in ACCOUNT_PLATFORMS}

# EchoBird 原生账号在账号接入页展示的 application 集合
ECHO_SURFACED_APPS = set(native_accounts.ECHO_PLATFORMS)

# 插件账号在账号接入页展示的 application 集合。
# workbuddy/workbuddyai/claudecode 也有同名插件：登录走原生（原生优先），
# 但插件里登录的账号同样展示（带「插件」徽章）——它们是 EchoBird 兼容存储里的
# 独立账号，与网关凭据（ApiKey）并存。
PLUGIN_SURFACED_APPS = {"claudedesktop", "doubaowork", "dola", "trae", "traecn",
                        "workbuddy", "workbuddyai", "claudecode"}


def platform_label(platform_id: str) -> str:
    p = PLATFORM_BY_ID.get(platform_id)
    if p:
        return p["label"]
    profile = op.get_profile(platform_id)
    return profile.get("label", platform_id) if profile else platform_id


def _login_ready(platform: dict) -> bool:
    """oauth 平台需要 client_id 已配置；插件平台需要对应插件已装且启用。"""
    if platform["kind"] == "echo":
        return True  # dsh/kilo/qoder 浏览器可用；qwenwork 缺客户端时在登录时明确报错
    if platform["kind"] == "plugin":
        plugin_id = platform.get("plugin_id") or ""
        record = plugin_manager.get_installed(plugin_id) if plugin_id else None
        return bool(record and record.get("enabled")
                    and plugin_manager.engine_binary_for(record))
    if platform["kind"] != "oauth":
        return True
    profile = op.get_profile(platform["id"])
    if not profile:
        return False
    if profile.get("requires_client_id") and not profile.get("client_id"):
        return False
    return True


def platform_catalog() -> list[dict]:
    out = []
    for p in ACCOUNT_PLATFORMS:
        out.append({
            **{k: p.get(k, "") for k in ("id", "label", "domain", "website", "kind", "quota", "claim", "icon", "note", "plugin_id")},
            "login_ready": _login_ready(p),
        })
    return out


async def ensure_account_provider(db: AsyncSession, platform_id: str) -> int:
    """返回该平台对应的 Provider id；不存在则自动建档（幂等）。"""
    if platform_id in local_cli.CLI_PLATFORMS:
        return await local_cli.ensure_provider(db, platform_id, 0)
    profile = op.get_profile(platform_id)
    if not profile:
        raise ValueError(f"Unknown account platform: {platform_id}")
    found = await db.execute(
        select(Provider).where(Provider.auth_type == platform_id).order_by(Provider.id)
    )
    provider = found.scalars().first()
    if provider:
        return provider.id
    upstream = profile.get("upstream") or {}
    protocols = upstream.get("protocols") or ["openai"]
    provider = Provider(
        name=f"{profile.get('label', platform_id)} (OAuth)",
        protocol=protocols[0],
        base_url=upstream.get("api_base", ""),
        auth_type=platform_id,
        description=f"OAuth login via account center ({platform_id})",
        icon="",
        homepage="",
        is_active=True,
    )
    db.add(provider)
    await db.commit()
    await db.refresh(provider)
    return provider.id


def _identity_from_blob(platform_id: str, blob: dict) -> str:
    """从解密后的凭据 blob 提取可展示的账号标识（不含任何 token）。"""
    for key in ("email", "nickname", "user", "name"):
        v = blob.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    uid = blob.get("uid")
    if uid not in (None, ""):
        return str(uid)
    return ""


def account_card(api_key: ApiKey, provider: Provider | None) -> dict:
    """ApiKey -> 账号卡片 DTO（绝不包含 token / refresh token）。"""
    platform_id = api_key.oauth_profile or ""
    platform = PLATFORM_BY_ID.get(platform_id, {})
    blob: dict = {}
    if api_key.oauth_refresh:
        try:
            from app.utils.crypto import decrypt_value
            parsed = json.loads(decrypt_value(api_key.oauth_refresh))
            if isinstance(parsed, dict):
                blob = parsed
        except Exception:
            blob = {}
    identity = _identity_from_blob(platform_id, blob)
    alias = api_key.alias or ""
    if not identity:
        # CLI/OAuth 的默认别名形如 CLI-workbuddy / OAuth-claude，仅作兜底展示
        identity = alias if alias and not alias.startswith(("CLI-", "OAuth-")) else platform_label(platform_id)

    quota = account_quota.load_quota_cache(api_key)
    supports_quota = bool(platform.get("quota"))
    claim = bool(platform.get("claim"))
    return {
        "id": api_key.id,
        "platform": platform_id or "unknown",
        "platform_label": platform_label(platform_id),
        "provider_id": api_key.provider_id,
        "provider_name": provider.name if provider else "",
        "identity": identity,
        "alias": alias,
        "plan": (quota or {}).get("plan"),
        "status": api_key.status,
        "token_expires_at": api_key.oauth_expires_at or 0,
        "quota": quota,
        "quota_fetched_at": api_key.quota_fetched_at or 0,
        "supports_quota": supports_quota,
        "supports_claim": claim,
        "last_error": api_key.last_error or "",
    }


async def list_native_cards(db: AsyncSession) -> list[dict]:
    """仅查库的原生账号卡片（快路径，不做任何上游调用）。

    用 LEFT JOIN 保留 provider 已被删除的「孤儿」账号：它们仍持有有效 token，
    INNER JOIN 会让其在卡片中消失，导致平台计数与卡片数量不一致且无法管理。
    """
    result = await db.execute(
        select(ApiKey, Provider)
        .outerjoin(Provider, ApiKey.provider_id == Provider.id)
        .where(ApiKey.oauth_profile != "")
        .where(ApiKey.alias.notlike("plugin:%"))
        .where(ApiKey.alias.notlike("echo:%"))
    )
    rows = result.all()
    return [account_card(api_key, provider) for api_key, provider in rows]


async def list_plugin_cards() -> list[dict]:
    """插件账号卡片（调用引擎 accounts.saved；按已装插件覆盖的 applications 遍历）。

    注意：workbuddy/workbuddyai/claudecode 在目录里是原生 kind，但只要对应插件
    已装且启用，其插件账号同样聚合展示（带「插件」徽章）。
    """
    cards: list[dict] = []
    installed = plugin_manager.installed_plugins()
    surfaced: dict[str, str] = {}
    for record in installed.values():
        if not record.get("enabled") or not plugin_manager.engine_binary_for(record):
            continue
        for application in plugin_manager.applications_of_installed(record):
            if application in PLUGIN_SURFACED_APPS and application not in surfaced:
                surfaced[application] = PLATFORM_BY_ID.get(application, {}).get("label", application)
    for application, label in surfaced.items():
        try:
            accounts = await plugin_accounts.list_accounts(application)
        except Exception as e:
            print(f"[account_hub] 插件账号聚合失败 {application}: {type(e).__name__} {str(e)[:150]}", flush=True)
            continue
        for account in accounts:
            if not isinstance(account, dict) or not account.get("id"):
                continue
            cards.append(plugin_accounts.account_card(application, account, label))
    return cards


async def list_echo_cards() -> list[dict]:
    """EchoBird 原生平台账号卡片（读取 ~/.echobird 兼容存储）。"""
    cards: list[dict] = []
    for platform in ACCOUNT_PLATFORMS:
        if platform["kind"] != "echo":
            continue
        try:
            entries = native_accounts.list_echo_accounts(platform["id"])
        except Exception:
            continue
        for entry in entries:
            cards.append(native_accounts.account_card(platform["id"], entry))
    return cards


def sort_cards(cards: list[dict]) -> list[dict]:
    order = {p["id"]: i for i, p in enumerate(ACCOUNT_PLATFORMS)}
    cards.sort(key=lambda c: (order.get(c["platform"], 999), str(c["id"])))
    return cards


async def list_account_cards(db: AsyncSession) -> list[dict]:
    """聚合所有接入账号（原生 + 插件 + EchoBird 存储）。

    注意：插件/echo 部分有网络与引擎调用，页面加载请用路由层的快照机制，
    不要在请求路径里直接调本函数。
    """
    cards = await list_native_cards(db)
    cards.extend(await list_plugin_cards())
    cards.extend(await list_echo_cards())
    return sort_cards(cards)


async def get_account_key(db: AsyncSession, key_id: int) -> tuple[ApiKey, Provider | None] | None:
    result = await db.execute(
        select(ApiKey, Provider)
        .outerjoin(Provider, ApiKey.provider_id == Provider.id)
        .where(ApiKey.id == key_id)
    )
    row = result.first()
    return row if row else None
