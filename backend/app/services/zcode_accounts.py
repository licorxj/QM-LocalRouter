"""ZCode 账号源（项目内 api_keys 里 oauth_profile=zcode 的账号）。

供脚本插件执行弹窗展示可选账号（脱敏），批量执行时按 key_id 索引。
不返回明文 token；明文由插件通过 credentials.read 权限自行读取。
"""
import os
import sqlite3

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data", "app.db")


def _token_col(conn: sqlite3.Connection) -> str | None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(api_keys)")}
    if "key_value" in cols:
        return "key_value"
    if "key" in cols:
        return "key"
    return None


def list_accounts() -> list[dict]:
    """返回脱敏的 zcode 账号列表：id / name / has_token。"""
    if not os.path.isfile(DB_PATH):
        return []
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        tc = _token_col(conn)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(api_keys)")}
        rows = conn.execute(
            "SELECT id, alias, oauth_profile, %s FROM api_keys "
            "WHERE oauth_profile='zcode' ORDER BY id" % (tc or "id")
        ).fetchall() if tc else []
    finally:
        conn.close()
    out = []
    for r in rows:
        d = dict(r)
        alias = str(d.get("alias") or "")
        name = alias or ("ZCode 账号 %s" % d["id"])
        out.append({
            "id": str(d["id"]),
            "name": name,
            "plan": "",
            "has_token": bool(d.get(tc)),
        })
    return out
