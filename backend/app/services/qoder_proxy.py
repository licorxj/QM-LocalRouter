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

错误分类 / 冷却 / 排队 / 体积预检（移植 qoder2api-hub 保守子集，不改成功转发路径）：
- 排队（10605 / isQueued / RESOURCE_LIMIT）：按 retryAfterSeconds 有限等待后重试，
  不惩罚账号；未吐字节才重开上游，超预算返回明确错误。
- 额度耗尽（业务码 112）/ 每日上限（110）：冷却账号（30 分钟 / 到本地 0 点）。
- 瞬时错误（418/5xx）：同账号重试（1s/2s 退避），仍失败再返回。
- 会话亲和：同一 userId 的对话复用上次成功的网关主机（INTL api3→api2→api1）。
- 体积预检：编码后请求体超 MAX_BODY_BYTES 直接 413，不发网络、不换号。
"""
import asyncio
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
# INTL 备用主机（官方客户端候选顺序），用于连接层失败时切换
GATEWAY_FALLBACKS = {
    "qoder": ["https://api3.qoder.sh", "https://api2.qoder.sh", "https://api1.qoder.sh"],
    "qodercn": ["https://gateway.qoder.com.cn"],
}
MODELS_PATH = "/algo/api/v2/model/list?Encode=1"
CHAT_PATH = "/algo/api/v2/service/pro/sse/agent_chat_generation?FetchKeys=llm_model_result&AgentId=agent_common&Encode=1"
VERSION = "1.1.75"  # 对齐 qodercli 1.1.75（原 1.1.49）

# 体积预检：编码后请求体超限直接 413（默认 29MB）
MAX_BODY_BYTES = int(__import__("os").environ.get("QD_MAX_BODY_MB", "29")) * 1024 * 1024
# 瞬时错误重试
TRANSIENT_CODES = (418, 500, 502, 503, 504)
TRANSIENT_RETRIES = 2
TRANSIENT_BACKOFF = (1.0, 2.0)
# 排队等待（有限预算）
QUEUE_MAX_ROUNDS = 3
QUEUE_WAIT_MAX_SECS = 180
QUEUE_WAIT_BUDGET_SECS = 600

# 账号冷却表：key = (platform, userId) -> (until_ts, reason)
_COOLDOWNS: dict[tuple, tuple[float, str]] = {}
# 会话亲和：userId -> 上次成功的网关主机（TTL 内复用）
_AFFINITY: dict[str, tuple[str, float]] = {}
_AFFINITY_TTL = 7200.0

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


# ---------------------------------------------------------------------------
# 错误分类 / 冷却 / 排队 / 会话亲和 辅助（保守增强，不触碰成功路径）
# ---------------------------------------------------------------------------

def _queue_info(text: str) -> dict | None:
    """从上游错误信封文本中识别排队信号，返回 {retry_after} 或 None。"""
    if not text:
        return None
    lowered = text.lower()
    queued = ("10605" in text or "isqueued" in lowered or "resource_limit" in lowered
              or "resource limit" in lowered or '"queued":true' in lowered)
    if not queued:
        return None
    import re
    retry = 30
    m = re.search(r'retryAfterSeconds["\s:=]*([\d.]+)', text, re.IGNORECASE)
    if not m:
        m = re.search(r'retry.after["\s:=]*([\d.]+)', text, re.IGNORECASE)
    if m:
        try:
            retry = min(300, max(1, int(float(m.group(1)))))
        except (TypeError, ValueError):
            pass
    return {"retry_after": retry}


def _classify_inner_error(inner_status: int, message: str, code: str = "") -> QoderError | None:
    """把业务错误映射成面向用户的明确 QoderError；返回 None 表示非特殊错误。"""
    text = message or ""
    if inner_status == 430 or code == "430":
        return QoderError("客户端 / 网关版本过低，请升级后重试", inner_status)
    if code in ("112", 112) or "额度已用尽" in text or "quota" in text.lower():
        return QoderError(f"该账号额度已用尽（{text[:120]}）", inner_status or 403)
    if code in ("110", 110) or "daily" in text.lower() and "limit" in text.lower():
        return QoderError(f"该账号今日用量已达上限（{text[:120]}）", inner_status or 403)
    if any(word in text for word in ("内容安全", "content safety", "sensitive", "审核")):
        return QoderError(f"内容安全审核未通过（{text[:120]}）", inner_status or 400)
    if _queue_info(text):
        return QoderError(f"上游排队中，请稍后重试（{text[:120]}）", 429)
    return None


def _cooldown_key(platform: str, creds: dict) -> tuple:
    return (platform, str(creds.get("userId") or creds.get("user_id") or "")[:40])


def _cooldown_remaining(key: tuple) -> float:
    until, reason = _COOLDOWNS.get(key, (0, ""))
    return max(0.0, until - time.time())


def _set_cooldown(key: tuple, seconds: float, reason: str) -> None:
    _COOLDOWNS[key] = (time.time() + max(0, seconds), reason)
    if len(_COOLDOWNS) > 512:
        now = time.time()
        for k in [k for k, (u, _) in _COOLDOWNS.items() if u < now][:256]:
            _COOLDOWNS.pop(k, None)


def _seconds_until_local_midnight() -> int:
    import datetime as _dt
    now = _dt.datetime.now()
    tomorrow = (now + _dt.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(60, int((tomorrow - now).total_seconds()))


def _affinity_host(platform: str, creds: dict) -> str:
    uid = str(creds.get("userId") or creds.get("user_id") or "")
    entry = _AFFINITY.get(uid)
    if entry and time.time() - entry[1] < _AFFINITY_TTL and entry[0] in GATEWAY_FALLBACKS.get(platform, []):
        return entry[0]
    return GATEWAY_FALLBACKS.get(platform, [GATEWAYS[platform]])[0]


def _remember_host(platform: str, creds: dict, host: str) -> None:
    uid = str(creds.get("userId") or creds.get("user_id") or "")
    if uid:
        _AFFINITY[uid] = (host, time.time())
        if len(_AFFINITY) > 2048:
            now = time.time()
            for k in [k for k, (_, t) in _AFFINITY.items() if now - t > _AFFINITY_TTL][:1024]:
                _AFFINITY.pop(k, None)


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

def _unpack_envelope(resp) -> tuple[int, str, bytes]:
    """解析 Qoder SSE 信封 {"headers","body","statusCodeValue"}。

    Qoder 网关把业务结果统一包在信封里、外层 HTTP 恒为 200：
    - inner_status >= 400：业务错误，映射成明确 QoderError（含排队/额度/版本分类），
      否则会被当成正常 SSE 转发，前端读不到 choices 而显示空回复；
    - inner_status == 200：把 body 里的真实 OpenAI 响应解包后下发。
    排队信号抛 QoderError(status=429)，由调用方决定是否有限等待重试。
    """
    content = resp.content
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
                try:
                    event = json.loads(payload)
                except (ValueError, TypeError):
                    unpacked.append(line)
                    continue
                if not isinstance(event, dict) or "statusCodeValue" not in event:
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
                    code = str(body_obj.get("code") or "")
                    special = _classify_inner_error(inner_status, message, code)
                    if special:
                        raise special
                    if inner_status == 403 or code == "112":
                        message = f"该账号额度已用尽或无权调用（{message}）"
                    raise QoderError(message, inner_status,
                                     login_required=inner_status in (401, 403))
                if body_raw:
                    unpacked.append(body_raw)
            if saw_envelope:
                data = "\n".join(unpacked).encode("utf-8")
                return resp.status_code, resp.headers.get("content-type", "application/json"), data
        except QoderError:
            raise
        except (ValueError, TypeError):
            pass
    return resp.status_code, resp.headers.get("content-type", "application/json"), content


async def call_model(creds: dict, platform: str, model_id: str, body: dict,
                     timeout: float = 180.0):
    """调用对话网关，返回 (status, content_type, content bytes)。"""
    identity = _qoder_headers_from_creds(creds)
    ckey = _cooldown_key(platform, identity)

    # 1) 账号冷却检查（额度耗尽 / 每日上限），短冷却等待、长冷却直接报错
    remaining = _cooldown_remaining(ckey)
    if remaining > 10:
        reason = _COOLDOWNS.get(ckey, (0, ""))[1]
        raise QoderError(f"该账号冷却中（{reason or '额度'}），约 {int(remaining)} 秒后可用", 429)
    if remaining > 0:
        await asyncio.sleep(remaining)

    catalog = await list_models(creds, platform)
    model_row = resolve_model(catalog, model_id)
    if not model_row:
        available = ", ".join(sorted(str(r.get("key")) for r in catalog)[:20])
        raise QoderError(f"该账号不可用模型 '{model_id}'（可用: {available}）", 404)
    request = prepare_request(body, model_row)
    wire = qoder_encode(json.dumps(request, ensure_ascii=False, separators=(",", ":")))

    # 2) 体积预检：超限直接 413，不发网络、不换号
    if len(wire.encode("utf-8")) > MAX_BODY_BYTES:
        raise QoderError(f"请求体过大（>{MAX_BODY_BYTES // (1024 * 1024)}MB），请缩短上下文后重试", 413)

    headers = codec_headers(identity, CHAT_PATH, wire)
    headers["accept"] = "text/event-stream"
    headers["cache-control"] = "no-cache"
    wire_bytes = wire.encode("utf-8")

    # 3) 会话亲和 + 主机切换（INTL api3→api2→api1），连接层瞬时错误同账号重试
    hosts = list(GATEWAY_FALLBACKS.get(platform, [GATEWAYS[platform]]))
    affinity = _affinity_host(platform, identity)
    ordered = [affinity] + [h for h in hosts if h != affinity]
    resp = None
    last_exc: Exception | None = None
    for host in ordered:
        url = host + CHAT_PATH
        for attempt in range(TRANSIENT_RETRIES + 1):
            try:
                client = ssl_relax.get_client(url)
                resp = await client.request("POST", url, headers=headers,
                                            content=wire_bytes, timeout=timeout)
            except httpx.HTTPError as e:
                last_exc = e
                if attempt < TRANSIENT_RETRIES:
                    await asyncio.sleep(TRANSIENT_BACKOFF[min(attempt, len(TRANSIENT_BACKOFF) - 1)])
                    continue
                break  # 换主机
            if resp.status_code in TRANSIENT_CODES and attempt < TRANSIENT_RETRIES:
                await asyncio.sleep(TRANSIENT_BACKOFF[min(attempt, len(TRANSIENT_BACKOFF) - 1)])
                continue
            break
        if resp is not None and resp.status_code not in TRANSIENT_CODES:
            _remember_host(platform, identity, host)
            break
        resp = None

    if resp is None:
        detail = f": {str(last_exc)[:120]}" if last_exc else ""
        raise QoderError("对话网关请求失败" + detail)

    if resp.status_code in (401, 403):
        raise QoderError("登录已失效，请重新登录该平台账号", 401, login_required=True)
    if resp.status_code >= 400:
        raise QoderError(f"对话网关返回 HTTP {resp.status_code}")

    # 4) 解包 + 排队有限等待重试（未吐字节、按 retryAfterSeconds、总预算封顶）
    queue_rounds = 0
    queue_total = 0.0
    while True:
        try:
            result = _unpack_envelope(resp)
            return result
        except QoderError as e:
            qi = _queue_info(e.message)
            if e.status != 429 or not qi:
                # 额度类错误：设置账号冷却
                if "额度已用尽" in e.message or "112" in e.message:
                    _set_cooldown(ckey, 1800, "quota-exhausted")
                elif "上限" in e.message or "110" in e.message:
                    _set_cooldown(ckey, _seconds_until_local_midnight(), "daily-limit")
                raise
            wait = min(qi["retry_after"], QUEUE_WAIT_MAX_SECS)
            queue_rounds += 1
            if queue_rounds > QUEUE_MAX_ROUNDS or queue_total + wait > QUEUE_WAIT_BUDGET_SECS:
                raise QoderError(f"上游持续排队，已等待 {int(queue_total)} 秒仍不可用，请稍后重试", 429)
            await asyncio.sleep(wait)
            queue_total += wait
            # 重发请求（排队不惩罚账号，继续用当前亲和主机）
            try:
                client = ssl_relax.get_client(affinity + CHAT_PATH)
                resp = await client.request("POST", affinity + CHAT_PATH, headers=headers,
                                            content=wire_bytes, timeout=timeout)
            except httpx.HTTPError as e2:
                raise QoderError(f"排队后重试失败: {str(e2)[:120]}")
