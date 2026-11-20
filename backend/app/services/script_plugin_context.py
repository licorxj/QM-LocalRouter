"""脚本插件执行上下文 —— 按权限注入的受限能力层。

插件脚本通过入口函数 ``run(ctx)`` 接收 ctx，只能调用其 manifest 声明过的
权限对应的方法。所有敏感调用写入审计日志（DATA_DIR/script_plugins/audit.jsonl）。

注入的能力：
    ctx.log(message)                       写执行日志（无需权限）
    ctx.meta                               插件 id / 版本等元信息（无需权限）
    ctx.params                             执行时传入的临时参数（无需权限）
    ctx.settings                           插件自身设置（无需权限，见 SettingsBridge）
    ctx.credentials.list(provider_id=None, reveal=False)   读账号凭证（需 credentials.read）
    ctx.database.query(sql, params=None)   只读查询（需 database.read）
    ctx.database.execute(sql, params=None) 写操作（需 database.write）

安全说明：这是「能力注入 + 显式授权」模型，不是 Python 沙箱。脚本在进程内以
线程池方式执行（共享地址空间），因此只应运行来源可信的插件。审计日志用于事后
追溯，SQL 层做语句类型白名单与多语句拦截以降低误用风险。
"""
from __future__ import annotations

import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable

from app.config import DATA_DIR
from app.utils.crypto import decrypt_value, mask_key
from app.services import script_plugin_permissions as perm

DB_PATH = DATA_DIR / "app.db"

_READ_HEADS = ("SELECT", "WITH", "PRAGMA", "EXPLAIN")
_WRITE_HEADS = ("INSERT", "UPDATE", "DELETE", "REPLACE")


def _sql_head(sql: str) -> str:
    m = re.match(r"\s*([A-Za-z]+)", sql or "")
    return m.group(1).upper() if m else ""


def _single_statement(sql: str) -> None:
    """拦截多语句（堆叠注入）：去除结尾分号后不得再含分号。"""
    stripped = (sql or "").strip().rstrip(";")
    if ";" in stripped:
        raise ValueError("仅允许执行单条 SQL 语句")


class _Audit:
    def __init__(self, plugin_id: str, sink: Callable[[dict], None] | None = None):
        self.plugin_id = plugin_id
        self._sink = sink

    def emit(self, action: str, **detail: Any) -> None:
        rec = {"ts": time.time(), "plugin": self.plugin_id, "action": action, **detail}
        if self._sink:
            try:
                self._sink(rec)
            except Exception:
                pass

    def denied(self, permission: str, api: str) -> None:
        self.emit("permission_denied", permission=permission, api=api)


class DatabaseBridge:
    def __init__(self, permissions: set[str], audit: _Audit):
        self._permissions = permissions
        self._audit = audit

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(DB_PATH), timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def query(self, sql: str, params: Any = None) -> list[dict]:
        """只读查询，返回 list[dict]。需 database.read 权限。"""
        if not perm.has(self._permissions, perm.DATABASE_READ):
            self._audit.denied(perm.DATABASE_READ, "database.query")
            raise PermissionError("缺少权限: " + perm.DATABASE_READ)
        head = _sql_head(sql)
        if head not in _READ_HEADS:
            raise ValueError("query() 仅允许 SELECT/WITH/PRAGMA/EXPLAIN 语句")
        _single_statement(sql)
        conn = self._conn()
        try:
            cur = conn.execute(sql, params or ())
            rows = [dict(r) for r in cur.fetchall()]
        finally:
            conn.close()
        self._audit.emit("database.query", head=head, rows=len(rows))
        return rows

    def execute(self, sql: str, params: Any = None) -> int:
        """写操作，返回受影响行数。需 database.write 权限。"""
        if not perm.has(self._permissions, perm.DATABASE_WRITE):
            self._audit.denied(perm.DATABASE_WRITE, "database.execute")
            raise PermissionError("缺少权限: " + perm.DATABASE_WRITE)
        head = _sql_head(sql)
        if head not in _WRITE_HEADS:
            raise ValueError("execute() 仅允许 INSERT/UPDATE/DELETE/REPLACE 语句")
        _single_statement(sql)
        conn = self._conn()
        try:
            cur = conn.execute(sql, params or ())
            conn.commit()
            rowcount = cur.rowcount
        finally:
            conn.close()
        self._audit.emit("database.execute", head=head, rowcount=rowcount)
        return rowcount


class CredentialsBridge:
    def __init__(self, permissions: set[str], audit: _Audit):
        self._permissions = permissions
        self._audit = audit

    def list(self, provider_id: int | None = None, reveal: bool = False) -> list[dict]:
        """读取账号凭证列表。需 credentials.read 权限。

        默认脱敏（reveal=False）；reveal=True 返回解密后的完整 key，
        该行为会被审计记录。
        """
        if not perm.has(self._permissions, perm.CREDENTIALS_READ):
            self._audit.denied(perm.CREDENTIALS_READ, "credentials.list")
            raise PermissionError("缺少权限: " + perm.CREDENTIALS_READ)
        conn = sqlite3.connect(str(DB_PATH), timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            # 兼容不同版本的 token 列名（本项目为 key_value，旧版为 key）
            cols = {r[1] for r in conn.execute("PRAGMA table_info(api_keys)")}
            token_col = "key_value" if "key_value" in cols else ("key" if "key" in cols else None)
            select_cols = "id, provider_id, alias, %s, oauth_profile, status" % token_col if token_col else \
                          "id, provider_id, alias, oauth_profile, status"
            if provider_id is None:
                rows = conn.execute("SELECT %s FROM api_keys ORDER BY id" % select_cols).fetchall()
            else:
                rows = conn.execute("SELECT %s FROM api_keys WHERE provider_id=? ORDER BY id" % select_cols,
                                    (provider_id,)).fetchall()
        finally:
            conn.close()
        out: list[dict] = []
        for r in rows:
            d = dict(r)
            raw = d.get(token_col) if token_col else None
            raw = raw or ""
            try:
                plain = decrypt_value(raw) if raw else ""
            except Exception:
                plain = ""
            d["key"] = plain if reveal else mask_key(plain)
            if token_col and token_col != "key":
                d.pop(token_col, None)
            out.append(d)
        self._audit.emit("credentials.list", count=len(out), reveal=bool(reveal),
                         provider_id=provider_id)
        return out


class SettingsBridge:
    """读取插件自身设置值（用户在设置页保存，无需额外权限）。"""

    def __init__(self, values: dict | None):
        self._values: dict = dict(values or {})

    def get(self, key: str, default: Any = None) -> Any:
        return self._values.get(key, default)

    def require(self, key: str) -> Any:
        if key not in self._values or self._values[key] in (None, ""):
            raise KeyError(f"设置项 {key} 未配置")
        return self._values[key]

    def all(self) -> dict:
        return dict(self._values)

    def __contains__(self, key: object) -> bool:
        return key in self._values


class PluginContext:
    """注入给插件脚本的唯一接口对象（按权限裁剪）。"""

    def __init__(self, plugin_id: str, version: str,
                 permissions: list[str], params: dict | None = None,
                 settings_values: dict | None = None,
                 sink: Callable[[dict], None] | None = None):
        self._permissions = set(perm.normalize_permissions(permissions))
        self._audit = _Audit(plugin_id, sink)
        self.meta = {
            "plugin_id": plugin_id,
            "version": version,
            "permissions": sorted(self._permissions),
        }
        self.params: dict = params or {}
        self.settings = SettingsBridge(settings_values)
        self.logs: list[str] = []
        self.database = DatabaseBridge(self._permissions, self._audit)
        self.credentials = CredentialsBridge(self._permissions, self._audit)

    def log(self, message: Any) -> None:
        self.logs.append(str(message))

    @property
    def permissions(self) -> list[str]:
        return sorted(self._permissions)

    def has_permission(self, permission: str) -> bool:
        return permission in self._permissions
