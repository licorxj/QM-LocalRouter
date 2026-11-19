from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import event, text
from sqlalchemy.pool import AsyncAdaptedQueuePool
from app.config import settings, BASE_DIR
import asyncio
import sys
from pathlib import Path

# sqlite+aiosqlite 默认 NullPool：每个请求都新建 DBAPI 连接（打开文件、启动
# aiosqlite 后台线程、执行 PRAGMA），Windows 上实测单次 ~20ms，且高并发下
# 受杀软实时扫描影响波动很大。显式改为连接池复用，每请求 DB 开销降到 <1ms。
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    poolclass=AsyncAdaptedQueuePool,
    pool_size=10,
    max_overflow=20,
)


@event.listens_for(engine.sync_engine, "connect")
def _set_sqlite_pragma(dbapi_conn, _record):
    """SQLite 性能 pragmas：WAL + synchronous=NORMAL。

    默认 journal=DELETE + synchronous=FULL 时每次 commit 都要做 journal
    文件落盘 + fsync，Windows 上单次 10-50ms；而转发热路径每请求有 2-3 次
    commit（请求日志、轮询计数、key 状态），累计可达 100ms+ 的额外延迟，
    且写锁会串行化并发请求。WAL 下 commit 不再阻塞读、fsync 次数大幅减少，
    是本应用延迟/吞吐的最大单项优化。崩溃安全性：应用进程崩溃不丢数据，
    仅操作系统断电时可能丢最后几笔写入（对该场景可接受）。
    """
    if not settings.DATABASE_URL.startswith("sqlite"):
        return
    cur = dbapi_conn.cursor()
    try:
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA busy_timeout=5000")
    finally:
        cur.close()


async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db():
    async with async_session() as session:
        try:
            yield session
        finally:
            await session.close()


async def _add_missing_columns():
    columns_to_add = [
        ("models", "is_multimodal", "BOOLEAN DEFAULT 0"),
        ("models", "model_type", "VARCHAR(20) DEFAULT 'text'"),
        # 模型支持的全部类型（逗号分隔）与能力标记：仅展示用，model_type 仍为路由主类型
        ("models", "model_types", "VARCHAR(120) DEFAULT ''"),
        ("models", "supports_vision", "BOOLEAN DEFAULT 0"),
        ("models", "supports_search", "BOOLEAN DEFAULT 0"),
        ("models", "supports_tools", "BOOLEAN DEFAULT 0"),
        # 能力推断版本：存量行 0 → 启动时按模型名补算一次，新建行 ORM 默认 2
        ("models", "cap_version", "INTEGER DEFAULT 0"),
        ("models", "context_window", "INTEGER"),
        ("models", "temperature", "FLOAT"),
        ("models", "price_input", "FLOAT"),
        ("models", "price_output", "FLOAT"),
        ("providers", "icon", "VARCHAR(500) DEFAULT ''"),
        ("strategies", "key_strategy", "VARCHAR(20) DEFAULT 'round_robin'"),
        ("strategies", "key_switch_mode", "VARCHAR(20) DEFAULT 'none'"),
        ("strategies", "key_rpm_threshold", "INTEGER DEFAULT 0"),
        ("strategies", "key_count_threshold", "INTEGER DEFAULT 0"),
        ("strategies", "rule_token_threshold", "INTEGER DEFAULT 0"),
        ("strategies", "rule_token_period", "VARCHAR(20) DEFAULT 'per_day'"),
        ("request_logs", "prompt_tokens", "INTEGER DEFAULT 0"),
        ("request_logs", "completion_tokens", "INTEGER DEFAULT 0"),
        ("request_logs", "total_tokens", "INTEGER DEFAULT 0"),
        ("conversations", "chat_mode", "VARCHAR(20) DEFAULT 'strategy'"),
        ("conversations", "stream_mode", "BOOLEAN DEFAULT 1"),
        ("conversations", "model_used", "VARCHAR(200) DEFAULT ''"),
        ("conversations", "provider_used", "VARCHAR(200) DEFAULT ''"),
        ("models", "status", "VARCHAR(20) DEFAULT 'untested'"),
        ("models", "last_error", "VARCHAR(500) DEFAULT ''"),
        ("providers", "homepage", "VARCHAR(500) DEFAULT ''"),
        ("providers", "auth_type", "VARCHAR(20) DEFAULT 'api_key'"),
        # 上游 v2.x 在 Provider 模型新增的直连多 Key 策略列（模型有、迁移曾漏登）
        ("providers", "key_strategy", "VARCHAR(20) DEFAULT 'round_robin'"),
        ("providers", "key_switch_mode", "VARCHAR(20) DEFAULT 'none'"),
        ("providers", "key_rpm_threshold", "INTEGER DEFAULT 0"),
        ("providers", "key_count_threshold", "INTEGER DEFAULT 0"),
        ("api_keys", "oauth_profile", "VARCHAR(50) DEFAULT ''"),
        ("api_keys", "oauth_refresh", "TEXT DEFAULT ''"),
        ("api_keys", "oauth_expires_at", "INTEGER DEFAULT 0"),
        # 账号接入页额度缓存（EchoBird 移植：套餐/奖励积分、剩余百分比、签到状态）
        ("api_keys", "quota_cache", "TEXT DEFAULT ''"),
        ("api_keys", "quota_fetched_at", "INTEGER DEFAULT 0"),
        # 请求日志记录客户端 IP（可视化监控页左列展示）
        ("request_logs", "client_ip", "VARCHAR(64) DEFAULT ''"),
        ("api_keys", "fail_count", "INTEGER DEFAULT 0"),
        ("api_keys", "last_failure_kind", "VARCHAR(20) DEFAULT ''"),
        ("api_keys", "cooldown_until", "INTEGER DEFAULT 0"),
        ("api_keys", "last_check_at", "DATETIME"),
    ]
    async with engine.begin() as conn:
        for table, col, col_def in columns_to_add:
            try:
                await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {col_def}"))
            except Exception:
                pass


async def _backfill_model_meta():
    """存量模型回填（只在列刚加出来、model_types 还空着时执行一次）。

    - 类型：model_types 默认取路由主类型，保证前端「类型」列不为空；
    - 能力：老库没有能力标记，用模型名启发式推断一次识图/搜索/工具调用，
      推断完写入 model_types，后续启动不会覆盖用户在界面上的手动修改。
    """
    from app.utils.known_models import infer_model_capabilities

    async with engine.begin() as conn:
        try:
            rows = (await conn.execute(text(
                "SELECT id, model_id, is_multimodal FROM models "
                "WHERE model_types IS NULL OR model_types = ''"
            ))).fetchall()
            for rid, mid, is_multi in rows:
                caps = infer_model_capabilities(mid or "", {}, bool(is_multi))
                await conn.execute(
                    text(
                        "UPDATE models SET supports_vision = :v, supports_search = :s, "
                        "supports_tools = :t WHERE id = :i"
                    ),
                    {"v": caps["supports_vision"], "s": caps["supports_search"],
                     "t": caps["supports_tools"], "i": rid},
                )
            await conn.execute(text(
                "UPDATE models SET model_types = COALESCE(NULLIF(model_type, ''), 'text') "
                "WHERE model_types IS NULL OR model_types = ''"
            ))
            await conn.execute(text(
                "UPDATE models SET supports_vision = 1 WHERE is_multimodal = 1 AND supports_vision = 0"
            ))

            # v2：上游 /models 不返回 tools 字段，按模型名补算工具调用能力。
            # cap_version 保证只算一次，且只做「新增」不清除用户手动关闭的标记。
            rows = (await conn.execute(text(
                "SELECT id, model_id, is_multimodal, supports_vision, supports_search, supports_tools "
                "FROM models WHERE cap_version IS NULL OR cap_version < 2"
            ))).fetchall()
            for rid, mid, is_multi, vis, sea, tool in rows:
                caps = infer_model_capabilities(mid or "", {}, bool(is_multi))
                await conn.execute(
                    text(
                        "UPDATE models SET supports_vision = :v, supports_search = :s, "
                        "supports_tools = :t, cap_version = 2 WHERE id = :i"
                    ),
                    {
                        "v": int(bool(vis) or caps["supports_vision"]),
                        "s": int(bool(sea) or caps["supports_search"]),
                        "t": int(bool(tool) or caps["supports_tools"]),
                        "i": rid,
                    },
                )
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Alembic schema management
# ---------------------------------------------------------------------------

def _alembic_sync_url() -> str:
    """alembic runs on a synchronous engine; reuse the exact same SQLite file."""
    return settings.DATABASE_URL.replace("sqlite+aiosqlite", "sqlite")


def _alembic_assets_dir() -> Path:
    """Locate bundled alembic.ini + alembic/ in source and PyInstaller builds.

    In onefile builds PyInstaller extracts `datas` under sys._MEIPASS while
    `BASE_DIR` is deliberately pointed at the exe's directory (user data lives
    next to the exe), so the migration scripts must be read from _MEIPASS.
    """
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass)
    return BASE_DIR


def _make_alembic_config():
    from alembic.config import Config

    assets = _alembic_assets_dir()
    ini = assets / "alembic.ini"
    cfg = Config(str(ini)) if ini.exists() else Config()
    # Absolute path so it works regardless of the process working directory.
    cfg.set_main_option("script_location", str(assets / "alembic"))
    cfg.set_main_option("sqlalchemy.url", _alembic_sync_url())
    return cfg


def _sync_inspect():
    """Return (has_business_tables, has_version_table, current_revision)."""
    from sqlalchemy import create_engine, inspect
    from alembic.runtime.migration import MigrationContext

    eng = create_engine(_alembic_sync_url())
    try:
        with eng.connect() as conn:
            insp = inspect(conn)
            has_tables = insp.has_table("providers")
            has_version = insp.has_table("alembic_version")
            rev = None
            if has_version:
                rev = MigrationContext.configure(conn).get_current_revision()
            return has_tables, has_version, rev
    finally:
        eng.dispose()


def _alembic_upgrade():
    from alembic import command

    command.upgrade(_make_alembic_config(), "head")


def _alembic_reconcile(has_tables: bool, has_version: bool, revision):
    """Bring an existing database under alembic management without touching data.

    - legacy DB (tables but no version table): `stamp head` marks the current
      schema as the baseline; the caller has already run _add_missing_columns.
    - managed DB (version present): `upgrade head` applies pending migrations.
    """
    from alembic import command

    if has_tables and not has_version:
        command.stamp(_make_alembic_config(), "head")
    elif has_version:
        command.upgrade(_make_alembic_config(), "head")


async def init_db():
    # Explicitly import models so every table is registered on Base.metadata.
    # Direct init_db() callers (scripts/init_db.py, standalone scripts) may not
    # import routers first, otherwise create_all would silently build an empty db.
    from app import models  # noqa: F401
    from app.models.conversation import Conversation  # noqa: F401

    # Inspect schema state in a worker thread (alembic needs a sync connection).
    try:
        has_tables, has_version, revision = await asyncio.to_thread(_sync_inspect)
    except Exception as exc:  # inspection failure: fall back to legacy schema setup
        print(f"[alembic] schema inspection failed ({exc}); using legacy create_all")
        has_tables, has_version, revision = True, True, "unknown"

    if not has_tables:
        # Fresh install: baseline migration builds the whole schema.
        try:
            await asyncio.to_thread(_alembic_upgrade)
            print("[alembic] fresh database initialized from baseline migration")
        except Exception as exc:
            print(f"[alembic] baseline upgrade failed: {exc}")
            raise
    else:
        # Existing install: keep the historical column backfill compatibility net,
        # then reconcile alembic bookkeeping (stamp legacy / upgrade managed).
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await _add_missing_columns()
        try:
            await asyncio.to_thread(_alembic_reconcile, has_tables, has_version, revision)
            print("[alembic] schema reconciled to head")
        except Exception as exc:  # never block startup on bookkeeping
            print(f"[alembic] schema reconcile skipped: {exc}")

    # 首次安装（providers 表为空）时预置 DeepSeek 平台 / 默认模型 / 全部策略，
    # 降低新用户学习成本；老用户库已有数据，seed_new_install() 直接返回不改动。
    try:
        from app.seed import seed_new_install

        seeded = await seed_new_install()
        if seeded:
            print(
                "[init_db] 已初始化默认配置: "
                f"平台={seeded['provider']} 默认模型={seeded['model']} "
                f"策略={len(seeded['strategies'])} 个"
            )
    except Exception as exc:  # 种子失败不应阻塞服务启动
        print(f"[init_db] 默认配置初始化跳过: {exc}")
    await _backfill_model_meta()

