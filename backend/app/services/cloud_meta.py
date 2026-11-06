"""云端软件元数据：软件公告与赞助商席位。

数据来自晴沐云端鉴权系统（https://www.licorxj.online，与
cloud_auth_user_client.py 参考实现同源）：

- 公告：``GET /api/announcement/latest?software_id=<id>``（公开接口，无需登录）
- 软件数据：``GET /api/software/public/{id}/secure-data``（公开接口，无需登录；
  服务端可能以 AES-256-CBC 加密下发，密钥通过环境变量
  ``CLOUD_AUTH_ENCRYPTION_KEY`` 配置，密钥规整逻辑与参考实现
  ``_normalize_aes_key`` 一致，解密后取其中的 ``sponsors`` 作为赞助商席位）

结果做 5 分钟内存缓存；拉取失败不缓存（下次请求重试），保证云端抖动只影响
当次展示而不长时间钉死空数据。
"""
import base64
import hashlib
import json
import os
import time

import httpx

CLOUD_AUTH_BASE_URL = (os.getenv("CLOUD_AUTH_BASE_URL") or "https://www.licorxj.online").rstrip("/")
CLOUD_SOFTWARE_ID = os.getenv("CLOUD_AUTH_SOFTWARE_ID") or "qmrouter"
CACHE_TTL_SECONDS = 300

_cache: dict | None = None
_cache_at = 0.0


def _normalize_aes_key(encryption_key: str) -> bytes:
    """与参考实现 _normalize_aes_key 一致：任意长度密钥规整为 32 字节。"""
    raw = encryption_key.encode("utf-8")
    if len(raw) == 32:
        return raw
    if len(raw) > 32:
        return raw[:32]
    return hashlib.sha256(raw).digest()


def _decrypt_secure_data(ciphertext_b64: str, iv_b64: str, encryption_key: str) -> str:
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives.padding import PKCS7

    cipher = Cipher(
        algorithms.AES(_normalize_aes_key(encryption_key)),
        modes.CBC(base64.b64decode(iv_b64)),
        backend=default_backend(),
    )
    decryptor = cipher.decryptor()
    padded = decryptor.update(base64.b64decode(ciphertext_b64)) + decryptor.finalize()
    unpadder = PKCS7(algorithms.AES.block_size).unpadder()
    return (unpadder.update(padded) + unpadder.finalize()).decode("utf-8")


def _normalize_announcements(items) -> list[dict]:
    out = []
    if isinstance(items, list):
        for i, a in enumerate(items):
            if not isinstance(a, dict):
                continue
            out.append({
                "id": str(a.get("id") or i),
                "title": str(a.get("title") or "").strip(),
                "content": str(a.get("content") or a.get("body") or a.get("text") or "").strip(),
                "created_at": str(a.get("created_at") or a.get("published_at") or a.get("updated_at") or "").strip(),
            })
    return out


def _parse_sponsors(secure: dict) -> list[dict]:
    """从（已解密/明文的）软件数据 JSON 里提取 sponsors 席位。"""
    data = secure.get("data")
    if not isinstance(data, dict):
        return []
    out = []
    for s in data.get("sponsors") or []:
        if not isinstance(s, dict):
            continue
        item = {
            "id": str(s.get("id") or len(out)),
            "name": str(s.get("name") or "").strip(),
            "brief": str(s.get("brief") or "").strip(),
            "website": str(s.get("website") or "").strip(),
        }
        if item["name"]:
            out.append(item)
    return out


async def fetch_cloud_meta(force: bool = False) -> dict:
    """拉取软件公告与软件数据（含赞助商席位），返回 {announcements, sponsors, error}。"""
    global _cache, _cache_at
    if not force and _cache is not None and time.time() - _cache_at < CACHE_TTL_SECONDS:
        return _cache

    result: dict = {"announcements": [], "sponsors": [], "error": None}
    try:
        async with httpx.AsyncClient(trust_env=False, timeout=15) as client:
            # 公告（公开接口，兼容裸列表 / {announcements: [...]} / {code,data} 包装）
            r = await client.get(
                f"{CLOUD_AUTH_BASE_URL}/api/announcement/latest",
                params={"software_id": CLOUD_SOFTWARE_ID},
            )
            payload = r.json()
            if isinstance(payload, dict):
                payload = payload.get("announcements") or payload.get("data") or []
            result["announcements"] = _normalize_announcements(payload)

            # 软件数据（公开接口；secure-data 返回 {code,msg,data} 包装）
            r = await client.get(
                f"{CLOUD_AUTH_BASE_URL}/api/software/public/{CLOUD_SOFTWARE_ID}/secure-data"
            )
            secure = r.json()
            if isinstance(secure, dict) and secure.get("code") == 0:
                secure = secure.get("data") or {}
            if isinstance(secure, dict) and secure.get("has_data"):
                if secure.get("encrypted"):
                    key = (os.getenv("CLOUD_AUTH_ENCRYPTION_KEY") or "").strip()
                    if not key:
                        result["error"] = "云端数据已加密但未配置 CLOUD_AUTH_ENCRYPTION_KEY"
                    else:
                        try:
                            plaintext = _decrypt_secure_data(secure["ciphertext"], secure["iv"], key)
                            secure["data"] = json.loads(plaintext)
                        except Exception as exc:
                            result["error"] = f"云端数据解密失败：{exc}"
                else:
                    # 服务端未配置 encryption_key 时明文下发在 plaintext 字段
                    plaintext = secure.get("plaintext")
                    if plaintext:
                        try:
                            secure["data"] = json.loads(plaintext)
                        except json.JSONDecodeError as exc:
                            result["error"] = f"云端数据 JSON 解析失败：{exc}"
                result["sponsors"] = _parse_sponsors(secure)
    except Exception as exc:
        # 云端不可达：不缓存，返回空数据由前端展示占位
        return {**result, "error": f"云端元数据拉取失败：{exc}"}

    _cache, _cache_at = result, time.time()
    return result
