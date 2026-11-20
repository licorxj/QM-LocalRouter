"""脚本插件设置项：schema 解析、默认值与取值校验。

插件在 manifest.json 的 ``settings`` 字段声明一组设置项，安装后用户可在前端
「设置」页填写；保存的值写入 installed.json 的 settings_values，执行时由宿主
通过 ctx.settings 注入给插件脚本。

schema 单项字段：
    key          必填，^[A-Za-z0-9_]{1,64}$
    type         string | integer | number | boolean | select | textarea | password（默认 string）
    label        字符串或 {"zh": "...", "en": "..."}
    description  同 label，可选
    default      默认值，可选
    required     bool，默认 False
    options      select 类型必填：[{"value": ..., "label": ...}]
    min / max    integer / number 类型的可选范围
    placeholder  同 label，可选

设计约束：设置值是插件自身配置（非敏感权限），读取无需声明权限；所有值在保存
时按 schema 类型校验并归一化，未知 key 一律丢弃，避免写入脏数据。
"""
from __future__ import annotations

import re
from typing import Any

_TYPES = {"string", "integer", "number", "boolean", "select", "textarea", "password"}
_KEY_RE = re.compile(r"^[A-Za-z0-9_]{1,64}$")
MAX_SETTINGS = 100


def _coerce(kind: str, value: Any) -> Any:
    """按类型归一化取值；失败返回 None 表示类型不合法。"""
    if value is None:
        return None
    try:
        if kind in ("string", "textarea", "password", "select"):
            if isinstance(value, bool):
                return None
            if isinstance(value, (dict, list)):
                return None
            return str(value)
        if kind == "boolean":
            if isinstance(value, bool):
                return value
            if isinstance(value, (int, float)) and value in (0, 1):
                return bool(value)
            if isinstance(value, str):
                low = value.strip().lower()
                if low in ("true", "1", "yes", "on"):
                    return True
                if low in ("false", "0", "no", "off"):
                    return False
            return None
        if kind == "integer":
            if isinstance(value, bool):
                return None
            if isinstance(value, int):
                return value
            if isinstance(value, float) and value.is_integer():
                return int(value)
            if isinstance(value, str) and re.fullmatch(r"[+-]?\d+", value.strip()):
                return int(value.strip())
            return None
        if kind == "number":
            if isinstance(value, bool):
                return None
            if isinstance(value, (int, float)):
                return float(value)
            if isinstance(value, str) and value.strip():
                return float(value.strip())
            return None
    except (TypeError, ValueError, OverflowError):
        return None
    return None


def normalize_schema(value: Any) -> list[dict]:
    """校验并规范化 settings schema；非法条目抛 ValueError。"""
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("settings 必须为数组")
    if len(value) > MAX_SETTINGS:
        raise ValueError("settings 条目过多")
    out: list[dict] = []
    seen: set[str] = set()
    for raw in value:
        if not isinstance(raw, dict):
            raise ValueError("settings 每项必须为对象")
        key = str(raw.get("key") or "")
        if not _KEY_RE.match(key):
            raise ValueError(f"设置项 key 无效: {key!r}")
        if key in seen:
            raise ValueError(f"设置项 key 重复: {key}")
        seen.add(key)
        kind = str(raw.get("type") or "string")
        if kind not in _TYPES:
            raise ValueError(f"设置项 {key} 的 type 无效: {kind}")
        item: dict = {
            "key": key,
            "type": kind,
            "label": raw.get("label") or key,
            "description": raw.get("description") or "",
            "placeholder": raw.get("placeholder") or "",
            "required": bool(raw.get("required", False)),
        }
        if kind == "select":
            options = raw.get("options")
            if not isinstance(options, list) or not options:
                raise ValueError(f"select 设置项 {key} 缺少 options")
            normalized_options = []
            for opt in options:
                if not isinstance(opt, dict) or "value" not in opt:
                    raise ValueError(f"select 设置项 {key} 的 options 格式无效")
                normalized_options.append({
                    "value": str(opt["value"]),
                    "label": opt.get("label") or str(opt["value"]),
                })
            item["options"] = normalized_options
        if kind in ("integer", "number"):
            for bound in ("min", "max"):
                if bound in raw and raw[bound] is not None:
                    b = _coerce(kind, raw[bound])
                    if b is None:
                        raise ValueError(f"设置项 {key} 的 {bound} 无效")
                    item[bound] = b
        if "default" in raw:
            d = _coerce(kind, raw["default"])
            if d is not None:
                item["default"] = d
        out.append(item)
    return out


def _validate_one(item: dict, raw: Any) -> Any:
    """校验单个设置项取值，返回归一化值；失败抛 ValueError。"""
    kind = item["type"]
    key = item["key"]
    if raw is None or (isinstance(raw, str) and raw == ""):
        if item.get("required"):
            raise ValueError(f"设置项 {key} 为必填")
        return "" if kind in ("string", "textarea", "password") else None
    value = _coerce(kind, raw)
    if value is None:
        raise ValueError(f"设置项 {key} 的类型应为 {kind}")
    if kind == "select":
        allowed = [o["value"] for o in item.get("options", [])]
        if value not in allowed:
            raise ValueError(f"设置项 {key} 的取值不在候选范围内")
    if kind in ("integer", "number"):
        if "min" in item and value < item["min"]:
            raise ValueError(f"设置项 {key} 不能小于 {item['min']}")
        if "max" in item and value > item["max"]:
            raise ValueError(f"设置项 {key} 不能大于 {item['max']}")
    return value


def default_values(schema: list[dict]) -> dict:
    """根据 schema 生成默认值字典。"""
    out: dict = {}
    for item in schema:
        if "default" in item:
            try:
                out[item["key"]] = _validate_one(item, item["default"])
            except ValueError:
                pass
    return out


def validate_values(schema: list[dict], values: dict | None) -> dict:
    """校验并归一化用户提交的设置值；未知 key 丢弃，缺失项用默认值。"""
    if values is None:
        values = {}
    if not isinstance(values, dict):
        raise ValueError("设置值必须为对象")
    by_key = {item["key"]: item for item in schema}
    out = default_values(schema)
    for key, raw in values.items():
        item = by_key.get(str(key))
        if item is None:
            continue  # 忽略未声明的键
        out[key] = _validate_one(item, raw)
    return out


def merge_values(schema: list[dict], old: dict | None) -> dict:
    """升级插件时保留旧设置值：合法的沿用，其余用默认值。"""
    out = default_values(schema)
    if not isinstance(old, dict):
        return out
    for item in schema:
        key = item["key"]
        if key in old:
            try:
                out[key] = _validate_one(item, old[key])
            except ValueError:
                pass
    return out
