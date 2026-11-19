"""Qoder / Qoder CN 对话网关适配器（移植 EchoBird account_proxy/qoder*）。

协议（qoder/codec.rs）：
- 请求体：自定义 64 字符 Base64 变体编码（"$" 填充、三段交换 swap）
- 鉴权信封：AES-128-CBC(PKCS7) 加密身份 JSON（随机 key）→ RSA-PKCS1v15 包裹 key
  → payload = base64({"version","requestId","info","cosyVersion","ideVersion"})
  → authorization = "Bearer COSY.{payload}.{md5(payload\\nkey\\nts\\nbody\\npath)}"
- 端点：
  模型目录 GET  {gateway}/algo/api/v2/model/list?Encode=1
  对话     POST {gateway}/algo/api/v2/service/pro/sse/agent_chat_generation
                ?FetchKeys=llm_model_result&AgentId=agent_common&Encode=1
- 请求体结构：{"parameters":{enable_thinking,max_tokens,context_length},
  "business":{product,version,type,id,name,begin_at,stage},
  "agent_id":"agent_common","task_id":"common","session_type":"app",
  "model_config":<目录模型行>,"system":[{type:text,text}],
  "messages":[{role,content:[{type:text,text}]}], "tools":可选}
- 响应：OpenAI 格式 JSON / SSE（choices[].delta），原样中继

凭据（EchoBird 存储 Credentials 字段映射）：
  userId ← user_id, accessToken ← device_token（usage/网关用）,
  machineId ← machine_id, name/email
"""
import base64
import hashlib
import json
import time
import uuid
from datetime import datetime, timezone

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding as rsa_padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.ciphers.algorithms import AES

from app.utils import ssl_relax

GATEWAYS = {
    "qoder": "https://api3.qoder.sh",
    "qodercn": "https://gateway.qoder.com.cn",
}
MODELS_PATH = "/algo/api/v2/model/list?Encode=1"
CHAT_PATH = "/algo/api/v2/service/pro/sse/agent_chat_generation?FetchKeys=llm_model_result&AgentId=agent_common&Encode=1"
VERSION = "1.1.49"

_ALPHABET = b"_doRTgHZBKcGVjlvpC,@aFSx#DPuNJme&i*MzLOEn)sUrthbf%Y^w.(kIQyXqWA!"
_PUBLIC_KEY_PEM = """-----BEGIN PUBLIC KEY-----
MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQDA8iMH5c02LilrsERw9t6Pv5Nc
4k6Pz1EaDicBMpdpxKduSZu5OANqUq8er4GM95omAGIOPOh+Nx0spthYA2BqGz+l
6HRkPJ7S236FZz73In/KVuLnwI8JJ2CbuJap8kvheCCZpmAWpb/cPx/3Vr/J6I17
XcW+ML9FoCI6AOvOzwIDAQAB
-----END PUBLIC KEY-----"""


class QoderError(Exception):
    def __init__(self, message: str, status: int = 502, login_required: bool = False):
        super().__init__(message)
        self.message = message
        self.status = status
        self.login_required = login_required


def _swap(s: str) -> str:
    third = len(s) // 3
    return s[len(s) - third:] + s[third:len(s) - third] + s[:third]


def qoder_encode(text: str) -> str:
    """Qoder 自定义 Base64（64 字符表 + '$' 填充 + 三段交换）。"""
    result: list[str] = []
    acc = 0
    bits = 0
    for byte in text.encode("utf-8"):
        acc = ((acc << 8) | byte) & 0xFFFFFF
        bits += 8
        while bits >= 6:
            bits -= 6
            result.append(chr(_ALPHABET[(acc >> bits) & 63]))
    if bits > 0:
        result.append(chr(_ALPHABET[(acc << (6 - bits)) & 63]))
    while len(result) % 4 != 0:
        result.append("$")
    return _swap("".join(result))


def _pkcs7_pad(data: bytes) -> bytes:
    n = 16 - (len(data) % 16)
    return data + bytes([n]) * n


def _b64(data) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return base64.b64encode(data).decode()


_RSA_KEY = None


def _rsa_key():
    global _RSA_KEY
    if _RSA_KEY is None:
        _RSA_KEY = serialization.load_pem_public_key(_PUBLIC_KEY_PEM.encode())
    return _RSA_KEY


def codec_headers(creds: dict, path: str, body: str, cosy_version: str = VERSION,
                  ide_version: str = "") -> dict:
    user = str(creds.get("userId") or "")
    token = str(creds.get("accessToken") or "")
    machine = str(creds.get("machineId") or "")
    if not user or not token or not machine:
        raise QoderError("Qoder 账号凭据不完整（缺少 userId/accessToken/machineId）", 401)

    # 随机 AES-128 key 加密身份 JSON，RSA 公钥包裹 key
    aes_key = uuid.uuid4().hex.encode()[:16]
    blob = json.dumps({"uid": user, "aid": "", "name": creds.get("name") or "",
                       "email": creds.get("email") or "",
                       "security_oauth_token": token}, separators=(",", ":"))
    padded = _pkcs7_pad(blob.encode())
    encryptor = Cipher(AES(aes_key), modes.CBC(aes_key)).encryptor()
    info = _b64(encryptor.update(padded) + encryptor.finalize())
    wrapped = _b64(_rsa_key().encrypt(aes_key, rsa_padding.PKCS1v15()))
    payload = _b64(json.dumps({
        "version": "v1", "requestId": uuid.uuid4().hex,
        "info": info, "cosyVersion": cosy_version, "ideVersion": ide_version,
    }, separators=(",", ":")))
    timestamp = str(int(datetime.now(timezone.utc).timestamp()))
    clean_path = path.split("?")[0]
    if clean_path.startswith("/algo"):
        clean_path = clean_path[len("/algo"):]
    signature = hashlib.md5(
        f"{payload}\n{wrapped}\n{timestamp}\n{body}\n{clean_path}".encode()
    ).hexdigest()
    authorization = f"Bearer COSY.{payload}.{signature}"
    machine_os = f"{_arch()}_{_os()}"
    headers = {
        "accept": "application/json",
        "accept-encoding": "identity",
        "content-type": "application/json",
        "authorization": authorization,
        "cosy-business-product": "app",
        "cosy-business-type": "agent",
        "cosy-clientip": machine,
        "cosy-clienttype": "10",
        "cosy-data-policy": "disagree",
        "cosy-date": timestamp,
        "cosy-key": wrapped,
        "cosy-machineid": machine,
        "cosy-machinetoken": machine,
        "cosy-machinetype": "5",
        "cosy-machineos": machine_os,
        "cosy-scene": "app",
        "cosy-user": user,
        "cosy-version": cosy_version,
        "login-version": "v2",
    }
    return headers


def _os() -> str:
    import os as _os_mod
    if _os_mod.name == "nt":
        return "win32"
    import sys as _sys
    if _sys.platform == "darwin":
        return "darwin"
    return "linux"


def _arch() -> str:
    import platform
    machine = (platform.machine() or "").lower()
    return "arm64" if machine in ("arm64", "aarch64") else "x64"


def _qoder_headers_from_creds(creds: dict) -> dict:
    """EchoBird Credentials JSON → codec 身份字段。"""
    return {
        "userId": creds.get("user_id") or "",
        "accessToken": creds.get("access_token") or creds.get("device_token") or "",
        "machineId": creds.get("machine_id") or "",
        "name": creds.get("name") or "",
        "email": creds.get("email") or "",
    }


# ---------------------------------------------------------------------------
# 模型目录

def _model_ok(row: dict) -> bool:
    if row.get("enable") is not True or row.get("hidden") is True or row.get("disabled") is True:
        return False
    text = json.dumps(row, ensure_ascii=False)
    # contains_secret：过滤带密钥字样的行（EchoBird contains_secret）
    return "sk-" not in text and "apikey" not in text.lower()


async def list_models(creds: dict, platform: str) -> list[dict]:
    """拉取模型目录；返回 EchoBird Catalog 形状 {model_id: 模型行} 的列表形式。"""
    identity = _qoder_headers_from_creds(creds)
    gateway = GATEWAYS[platform]
    headers = codec_headers(identity, MODELS_PATH, "")
    url = gateway + MODELS_PATH
    try:
        client = ssl_relax.get_client(url)
        resp = await client.request("GET", url, headers=headers, timeout=15)
    except httpx.HTTPError as e:
        raise QoderError(f"模型目录请求失败: {str(e)[:120]}")
    if resp.status_code in (401, 403):
        raise QoderError("登录已失效，请重新登录该平台账号", 401, login_required=True)
    if resp.status_code >= 400:
        raise QoderError(f"模型目录返回 HTTP {resp.status_code}")
    try:
        value = resp.json()
    except ValueError:
        raise QoderError("模型目录返回非 JSON")
    if value.get("code") not in (None, 0, "0"):
        raise QoderError(f"模型目录业务错误: {str(value.get('message') or value.get('code'))[:120]}")
    out = []
    for section in ("app", "chat"):
        for row in value.get(section) or []:
            if not isinstance(row, dict):
                continue
            key = str(row.get("key") or "")
            if not key or key in ("auto", "default") or not _model_ok(row):
                continue
            out.append(row)
    return out


def resolve_model(catalog: list[dict], model_id: str) -> dict | None:
    for row in catalog:
        if row.get("key") == model_id:
            return row
    return None


# ---------------------------------------------------------------------------
# 请求体构造（EchoBird prepare() 的简化忠实版：纯文本消息 + max_tokens/thinking）

def prepare_request(body: dict, model_row: dict) -> dict:
    messages_in = body.get("messages") or []
    if not isinstance(messages_in, list):
        raise QoderError("messages 必须是数组", 400)
    system_parts = ["You are a Qoder agent. Use the instructions below and the tools available to you to assist the user."]
    messages = []
    for msg in messages_in:
        role = str(msg.get("role") or "")
        if role not in ("system", "developer", "user", "assistant", "tool"):
            raise QoderError(f"不支持的 messages.role: {role}", 400)
        content = msg.get("content")
        blocks = [{"type": "text", "text": content}] if isinstance(content, str) \
            else (content if isinstance(content, list) else [])
        if role in ("system", "developer"):
            texts = [b.get("text", "") for b in blocks if isinstance(b, dict) and b.get("type") == "text"]
            system_parts.append("\n".join(t for t in texts if t))
            continue
        if role == "tool":
            item = {"role": "tool", "tool_call_id": msg.get("tool_call_id") or "",
                    "content": content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)}
        else:
            item = {"role": role, "content": blocks}
        if role == "assistant" and isinstance(msg.get("tool_calls"), list):
            item["content"] = content if isinstance(content, str) else ""
            item["tool_calls"] = [{"id": c.get("id"), "type": "function", "function": c.get("function")}
                                  for c in msg["tool_calls"] if isinstance(c, dict)]
        messages.append(item)
    sys_block = {"type": "text", "text": "\n\n".join(system_parts)}
    messages.insert(0, {"role": "system", "content": [sys_block]})

    cap = body.get("max_completion_tokens") or body.get("max_tokens") or 32000
    try:
        cap = max(1, int(cap))
    except (TypeError, ValueError):
        cap = 32000
    thinking = bool(model_row.get("is_reasoning"))
    parameters = {"enable_thinking": thinking, "max_tokens": cap}
    context = None
    cc = model_row.get("context_config")
    if isinstance(cc, dict):
        for v in cc.values():
            if isinstance(v, dict) and v.get("is_default") is True:
                context = v.get("token_count")
                break
    context = context or model_row.get("default_context_window") or model_row.get("max_input_tokens")
    if isinstance(context, (int, float)) and context > 0:
        parameters["context_length"] = int(context)

    request = {
        "parameters": parameters,
        "business": {"product": "app", "version": VERSION, "type": "agent",
                     "id": uuid.uuid4().hex, "name": "LocalRouter session",
                     "begin_at": int(time.time() * 1000), "stage": "start"},
        "agent_id": "agent_common",
        "task_id": "common",
        "session_type": "app",
        "model_config": model_row,
        "system": [sys_block],
        "messages": messages,
    }
    tools = body.get("tools")
    if isinstance(tools, list) and tools and body.get("tool_choice") != "none":
        request["tools"] = tools
    return request


# ---------------------------------------------------------------------------
# 调用（返回原始响应，供转发器中继）

async def call_model(creds: dict, platform: str, model_id: str, body: dict,
                     timeout: float = 180.0):
    """调用对话网关，返回 (status, content_type, content bytes)。"""
    catalog = await list_models(creds, platform)
    model_row = resolve_model(catalog, model_id)
    if not model_row:
        available = ", ".join(sorted(str(r.get("key")) for r in catalog)[:20])
        raise QoderError(f"该账号不可用模型 '{model_id}'（可用: {available}）", 404)
    request = prepare_request(body, model_row)
    wire = qoder_encode(json.dumps(request, ensure_ascii=False, separators=(",", ":")))
    headers = codec_headers(_qoder_headers_from_creds(creds), CHAT_PATH, wire)
    headers["accept"] = "text/event-stream"
    headers["cache-control"] = "no-cache"
    url = GATEWAYS[platform] + CHAT_PATH
    try:
        client = ssl_relax.get_client(url)
        resp = await client.request("POST", url, headers=headers, content=wire.encode(),
                                    timeout=timeout)
    except httpx.HTTPError as e:
        raise QoderError(f"对话网关请求失败: {str(e)[:120]}")
    content = resp.content
    # Qoder 网关把「业务结果」统一包在 SSE 信封 {"headers","body","statusCodeValue"} 里，
    # 且外层 HTTP 恒为 200。必须逐帧解析信封：
    #   - inner_status >= 400：业务错误（登录失效 / 额度耗尽），转成明确 QoderError，
    #     否则会被当成正常 SSE 转发，前端读不到 choices 而显示空回复；
    #   - inner_status == 200：把 body 里的真实 OpenAI 响应解包后下发。
    # 旧实现用 b"choices" not in content[:2000] 判断，body 中一旦含 choices 字样就整段跳过，
    # 导致错误信封原样透传 —— 这正是"日志 200 但对话无内容"的根因。
    if resp.status_code == 200 and b"statusCodeValue" in content[:4000]:
        try:
            unpacked: list[str] = []
            saw_envelope = False
            for line in content.decode("utf-8", "replace").splitlines():
                s = line.strip()
                if not s.startswith("data:"):
                    if s:
                        unpacked.append(line)
                    continue
                payload = s[5:].strip()
                if payload == "[DONE]":
                    unpacked.append("data: [DONE]")
                    continue
                event = json.loads(payload)
                if not isinstance(event, dict) or "statusCodeValue" not in event:
                    # 非信封帧，原样保留
                    unpacked.append(line)
                    continue
                saw_envelope = True
                inner_status = int(event.get("statusCodeValue") or 0)
                body_raw = event.get("body")
                if isinstance(body_raw, str):
                    pass
                elif body_raw is not None:
                    body_raw = json.dumps(body_raw, ensure_ascii=False)
                else:
                    body_raw = ""
                if inner_status >= 400:
                    try:
                        body_obj = json.loads(body_raw) if body_raw else {}
                    except (ValueError, TypeError):
                        body_obj = {}
                    message = str(body_obj.get("message") or body_raw)[:300]
                    if inner_status == 403 or body_obj.get("code") == "112":
                        message = f"该账号额度已用尽或无权调用（{message}）"
                    raise QoderError(message, inner_status,
                                     login_required=inner_status in (401, 403))
                # 成功信封：提取 body 中的真实内容
                if body_raw:
                    unpacked.append(body_raw)
            if saw_envelope:
                content = "\n".join(unpacked).encode("utf-8")
                # 解包后若已是标准 SSE，保持 event-stream；否则按 JSON 处理
                return resp.status_code, resp.headers.get("content-type", "application/json"), content
        except QoderError:
            raise
        except (ValueError, TypeError):
            pass
    return resp.status_code, resp.headers.get("content-type", "application/json"), content
