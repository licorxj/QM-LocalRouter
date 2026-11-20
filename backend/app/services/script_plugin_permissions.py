"""脚本插件权限定义与校验。

权限是一组字符串能力标识，插件在 manifest.json 的 ``permissions`` 字段声明。
执行宿主据此只向插件注入已声明能力的 API，并写入审计日志。

安全边界：本系统面向「本地受信脚本插件」——插件由用户主动选择安装、主动点击
执行，权限机制用于最小能力注入 + 用户知情 + 审计，并非安全沙箱。Python 层面
无法完全隔离恶意本地代码，请勿运行来源不明的插件。
"""
from __future__ import annotations

CREDENTIALS_READ = "credentials.read"
DATABASE_READ = "database.read"
DATABASE_WRITE = "database.write"

# 权限 -> 简述（供前端展示）
PERMISSION_INFO = {
    CREDENTIALS_READ: {
        "zh": "读取账号凭证（解密后的 API Key）",
        "en": "Read account credentials (decrypted API keys)",
    },
    DATABASE_READ: {
        "zh": "只读查询应用数据库",
        "en": "Read-only access to the application database",
    },
    DATABASE_WRITE: {
        "zh": "写入应用数据库（增/改/删）",
        "en": "Write to the application database (insert/update/delete)",
    },
}

ALL_PERMISSIONS = tuple(PERMISSION_INFO)


def normalize_permissions(values) -> list[str]:
    """规整权限列表：去空、去重、保序。"""
    if not isinstance(values, (list, tuple, set)):
        return []
    out: list[str] = []
    for v in values:
        if isinstance(v, str):
            v = v.strip()
            if v and v not in out:
                out.append(v)
    return out


def unknown_permissions(values) -> list[str]:
    return [p for p in normalize_permissions(values) if p not in PERMISSION_INFO]


def has(permissions, perm: str) -> bool:
    return perm in (permissions or ())


def describe(perm: str, lang: str = "zh") -> str:
    info = PERMISSION_INFO.get(perm)
    if not info:
        return perm
    return info.get(lang) or info["zh"]
