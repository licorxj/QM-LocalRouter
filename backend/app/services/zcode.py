"""ZCode（zcode.z.ai · z.ai GLM Start/Coding Plan）转发客户端（移植自 D3-vin/Zcode2Api）。

ZCode 的对话端点不是标准 OpenAI API，而是带客户端指纹校验的 Anthropic 兼容协议：

- 真实端点：POST https://zcode.z.ai/api/v1/zcode-plan/anthropic/v1/messages
- 认证：Authorization: Bearer <JWT> 与 x-api-key: <JWT> 双头携带同一令牌
  （JWT 来自官方 CLI 的 OAuth 授权，导入流程见 local_cli.py 的 cli-zcode）
- 必须携带 ZCode 客户端指纹头（User-Agent / X-ZCode-App-Version / X-Platform ...），
  否则被 WAF 以 405 {"code":3012} "unusual activity" 拦截
- system 必须恰好是官方客户端的 3 个身份块（见 zcode_system.py），客户端自己的
  system 拼进第 3 块；metadata.user_id 是设备身份 JSON 串；最后一条消息追加
  cache_control ephemeral
- 上游限 131072 max_tokens（超出 400/1210）；业务码 3012=WAF、3007=验证码、
  3010=并发准入、1113/1308~1321=配额
- OpenAI 请求先转 Anthropic Messages，上游 SSE 再翻译回 OpenAI chunk
"""
import base64
import copy
import hashlib
import json
import os
import re
import sys
import time
import uuid

import httpx

from app.services.zcode_system import SYSTEM_BLOCKS_JSON

ZCODE_API_BASE = "https://zcode.z.ai"
MESSAGES_PATH = "/api/v1/zcode-plan/anthropic/v1/messages"

# 请求指纹（ZCode 3.16.3 darwin 抓包口径，Zcode2Api 实测 200）
CLIENT_VERSION = "3.16.3"
CHAT_UA_SUFFIX = " ai-sdk/provider-utils/4.0.27 runtime/node.js/24"
CLIENT_PLATFORM = "darwin-x64"
CLIENT_OS_CATEGORY = "macos"
CLIENT_TIMEZONE = "Europe/Bucharest"
CHAT_OS_VERSION = "21.6.0"
ANTHROPIC_VERSION = "2023-06-01"
ANTHROPIC_BETA = "mid-conversation-system-2026-04-07"

DEFAULT_MODEL = "GLM-5.3"
MAX_TOKENS_LIMIT = 131072   # 上游硬上限，超出返回 400/1210
DEFAULT_MAX_TOKENS = 4096   # OpenAI 兼容缺省

# 模型别名（claude-* 探测名 / 小写 glm-*）→ 计费真实 ID（forward.go modelNameMap）
MODEL_NAME_MAP = {
    "glm-5.3": "GLM-5.3",
    "glm-5.3-flash": "GLM-5.3-Flash",
    "glm-5.2": "GLM-5.2",
    "glm-5-turbo": "GLM-5-Turbo",
    "glm-turbo": "GLM-5-Turbo",
    "glm-5.1": "GLM-5.1",
    "glm-4.7": "GLM-4.7",
    "zcode glm 5.3": "GLM-5.3",
    "zcode glm 5.3 flash": "GLM-5.3-Flash",
    "claude-fable-5-1": "GLM-5.3",
    "fable": "GLM-5.3",
    "claude-opus-4-8": "GLM-5.3",
    "claude-opus-5-5": "GLM-5.3",
    "opus": "GLM-5.3",
    "claude-sonnet-4-6": "GLM-5.3-Flash",
    "claude-sonnet-5-5": "GLM-5.3-Flash",
    "sonnet": "GLM-5.3-Flash",
    "claude-3-5-haiku-latest": "GLM-5.3-Flash",
    "claude-haiku-5-5": "GLM-5.3-Flash",
    "haiku": "GLM-5.3-Flash",
}

# 静态模型目录（client/configs + billing 口径；供 /api/cli/zcode/models 同步）
DEFAULT_MODELS = [
    {"model_id": "GLM-5.3", "display_name": "GLM-5.3", "context_window": 1000000, "is_multimodal": True},
    {"model_id": "GLM-5.3-Flash", "display_name": "GLM-5.3 Flash", "context_window": None, "is_multimodal": True},
]

# 上游业务码（quota.go）
CODE_CAPTCHA = 3007
CODE_WAF = 3012
QUOTA_CODES_429 = {1113, 1308, 1309, 1310, 1311, 1313, 1316, 1317, 1318, 1319, 1320, 1321}
CODE_QUOTA = 1005

# 官方套话开场白：拼接客户端 system 前删掉，避免每个回复都以同样的话开头
ZCODE_CANNED_OPENER = ("Before your first tool call, say in a sentence what you're about to do; "
                       "while working, give brief updates when you find something load-bearing or change direction.")
ZCODE_LEAD_OUTCOME = ("Lead with the outcome. Your first sentence after finishing should answer "
                      "\"what happened\" or \"what did you find\" — the thing the user would ask for "
                      "if they said \"just give me the TLDR.\" Supporting detail and reasoning come "
                      "after, for readers who want them.")


# ------------------------------------------------------------------ 本地凭据解密
# ZCode 桌面端把凭据存在 ~/.zcode/v2/credentials.json，格式为
#   enc:v1:<b64url(iv)>.<b64url(auth_tag)>.<b64url(ciphertext)>
# 算法 aes-256-gcm，key = sha256(secret)；secret 未设 ZCODE_CREDENTIAL_SECRET
# 环境变量时为 zcode-credential-fallback:{os.platform()}:{homedir}:{username}
# （与官方实现一致，见 app.asar 的 createCredentialCipherProvider；本机同用户
#  解密自己会话的凭据，与 cline/trae 等平台直接读本地令牌文件同性质）。

ZCODE_CRED_PREFIX = "enc:v1:"
ZCODE_CRED_SECRET_ENV = "ZCODE_CREDENTIAL_SECRET"


def credential_secret() -> str:
    env = os.environ.get(ZCODE_CRED_SECRET_ENV)
    if env:
        return env
    username = "unknown"
    try:
        import getpass
        username = getpass.getuser()
    except Exception:
        pass
    return f"zcode-credential-fallback:{sys.platform}:{os.path.expanduser('~')}:{username}"


def decrypt_credential_blob(value: str) -> str:
    """解密 ZCode 桌面端 enc:v1 凭据（非 enc:v1 的值原样返回）。"""
    if not isinstance(value, str) or not value.startswith(ZCODE_CRED_PREFIX):
        return value
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    parts = value[len(ZCODE_CRED_PREFIX):].split(".")
    if len(parts) != 3:
        raise ValueError("invalid enc:v1 payload")
    b64u = lambda s: base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))
    iv, tag, ct = (b64u(x) for x in parts)
    if len(iv) != 12 or len(tag) != 16:
        raise ValueError("invalid enc:v1 iv/tag length")
    key = hashlib.sha256(credential_secret().encode()).digest()
    try:
        return AESGCM(key).decrypt(iv, ct + tag, None).decode("utf-8")
    except Exception as e:
        raise ValueError(f"ZCode 本地凭据解密失败（secret 不匹配或已损坏）: {type(e).__name__}")


class ZcodeError(Exception):
    def __init__(self, status: int, message: str, code: str = "upstream_error", retry_after: int | None = None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.code = code
        self.retry_after = retry_after


# ------------------------------------------------------------------ 设备身份
# device_mid 由 JWT sub 派生（SHA1 命名空间 UUID）：同一账号稳定同一设备指纹。
# 随机/漂移的设备 ID 是 405/3012 "unusual activity" 的常见诱因（import.go 实证）。

def jwt_sub(jwt: str) -> str:
    try:
        payload = jwt.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload))
        return str(data.get("sub") or data.get("user_id") or "")
    except Exception:
        return ""


def jwt_exp(jwt: str) -> int:
    try:
        payload = jwt.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload))
        exp = data.get("exp")
        return int(exp) if exp else 0
    except Exception:
        return 0


def device_mid(jwt: str) -> str:
    name = jwt_sub(jwt) or jwt
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, "zcode-gw." + name))


def session_id_for(device_mid_value: str) -> str:
    if not device_mid_value:
        device_mid_value = "anon"
    digest = hashlib.sha256(("zcode-session." + device_mid_value).encode()).hexdigest()[:32]
    return "session_" + digest


# ------------------------------------------------------------------ 请求头
def chat_headers(jwt: str, device_mid_value: str = "", query_id: str = "",
                 trace_id: str = "", request_id: str = "") -> dict:
    """ZCode 3.16.3 darwin 抓包的完整指纹头集合（chatheaders.go chatRequestHeaders）。"""
    return {
        "content-type": "application/json",
        "Authorization": f"Bearer {jwt}",
        "anthropic-beta": ANTHROPIC_BETA,
        "anthropic-version": ANTHROPIC_VERSION,
        "HTTP-Referer": "https://zcode.z.ai",
        "User-Agent": f"ZCode/{CLIENT_VERSION}{CHAT_UA_SUFFIX}",
        "x-api-key": jwt,
        "X-ZCode-App-Version": CLIENT_VERSION,
        "X-Title": "Z Code@electron",
        "X-ZCode-Agent": "glm",
        "X-Platform": CLIENT_PLATFORM,
        "X-Release-Channel": "production",
        "X-Client-Language": "en-US",
        "X-Client-Timezone": CLIENT_TIMEZONE,
        "X-Os-Category": CLIENT_OS_CATEGORY,
        "X-Os-Version": CHAT_OS_VERSION,
        "x-query-id": query_id or str(uuid.uuid4()),
        "x-session-id": session_id_for(device_mid_value or device_mid(jwt)),
        "x-request-id": request_id or str(uuid.uuid4()),
        "x-zcode-query-source": "main_turn",
        "x-zcode-session-type": "main",
        "x-zcode-trace-id": trace_id or str(uuid.uuid4()),
        "Connection": "close",
    }


# ------------------------------------------------------------------ OpenAI -> Anthropic
def _content_to_text(content) -> str:
    """OpenAI content（字符串或 parts 数组）拼接为纯文本。"""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for p in content:
            if isinstance(p, dict) and p.get("type") == "text" and isinstance(p.get("text"), str):
                parts.append(p["text"])
        return "".join(parts)
    return str(content)


def _image_blocks_from_parts(parts: list) -> list[dict] | None:
    """OpenAI 多模态 parts → Anthropic blocks（data-URL 图片转 base64，远程 URL 跳过）。"""
    blocks: list[dict] = []
    for p in parts:
        if not isinstance(p, dict):
            continue
        ptype = p.get("type")
        if ptype == "text" and p.get("text"):
            blocks.append({"type": "text", "text": p["text"]})
        elif ptype == "image_url":
            url = (p.get("image_url") or {}).get("url", "") if isinstance(p.get("image_url"), dict) else ""
            if not url or not url.startswith("data:"):
                continue
            media, _, data = url.partition(",")
            fmt = media[len("data:"):]
            fmt = fmt.split(";", 1)[0]
            if data:
                blocks.append({"type": "image", "source": {"type": "base64", "media_type": fmt, "data": data}})
    return blocks or None


def _openai_msg_to_anth(m: dict) -> dict:
    role = m.get("role", "user")
    if role == "tool":
        return {"role": "user", "content": [{
            "type": "tool_result",
            "tool_use_id": m.get("tool_call_id", ""),
            "content": _content_to_text(m.get("content")),
        }]}
    if role == "assistant" and m.get("tool_calls"):
        blocks: list[dict] = []
        text = _content_to_text(m.get("content"))
        if text:
            blocks.append({"type": "text", "text": text})
        for tc in m["tool_calls"]:
            fn = tc.get("function") or {}
            try:
                parsed = json.loads(fn.get("arguments") or "{}")
                if not isinstance(parsed, (dict, list)):
                    parsed = {}
            except (TypeError, ValueError):
                parsed = {}
            blocks.append({"type": "tool_use", "id": tc.get("id", ""),
                           "name": fn.get("name", ""), "input": parsed})
        return {"role": "assistant", "content": blocks}
    content = m.get("content")
    if isinstance(content, list):
        blocks = _image_blocks_from_parts(content)
        if blocks:
            return {"role": role, "content": blocks}
    return {"role": role, "content": _content_to_text(content)}


def _map_glm_effort(v: str) -> str:
    v = (v or "").strip().lower()
    if v in ("minimal", "low"):
        return "low"
    if v in ("medium", "high"):
        return "high"
    if v in ("xhigh", "extra", "max"):
        return "max"
    return v


def openai_to_anthropic(req: dict) -> dict:
    """OpenAI chat.completions 请求体 → Anthropic /v1/messages 请求体（openai.go OpenAIToAnthropic）。"""
    out: dict = {"model": req.get("model") or "", "stream": bool(req.get("stream"))}
    effort = req.get("reasoning_effort") or (req.get("reasoning") or {}).get("effort")
    if isinstance(effort, str) and effort.strip():
        out_effort = _map_glm_effort(effort)
        if out_effort:
            out["output_config"] = {"effort": out_effort}
    max_tok = req.get("max_tokens") or req.get("max_completion_tokens") or 0
    try:
        max_tok = int(max_tok)
    except (TypeError, ValueError):
        max_tok = 0
    out["max_tokens"] = min(max_tok, MAX_TOKENS_LIMIT) if max_tok > 0 else DEFAULT_MAX_TOKENS
    if isinstance(req.get("temperature"), (int, float)):
        out["temperature"] = req["temperature"]
    if isinstance(req.get("top_p"), (int, float)):
        out["top_p"] = req["top_p"]
    if isinstance(req.get("top_k"), (int, float)):
        out["top_k"] = req["top_k"]
    stop = req.get("stop")
    if isinstance(stop, str):
        stop = [stop]
    if stop:
        out["stop_sequences"] = stop
    if isinstance(req.get("thinking"), dict):
        out["thinking"] = req["thinking"]

    system_texts: list[str] = []
    msgs: list[dict] = []
    for m in req.get("messages") or []:
        if not isinstance(m, dict):
            continue
        if m.get("role") in ("system", "developer"):
            text = _content_to_text(m.get("content"))
            if text.strip():
                system_texts.append(text)
            continue
        msgs.append(_openai_msg_to_anth(m))
    if system_texts:
        out["system"] = [{"type": "text", "text": t} for t in system_texts]
    out["messages"] = msgs

    tools_in = [t for t in (req.get("tools") or []) if isinstance(t, dict)]
    if tools_in:
        tools = []
        for t in tools_in:
            fn = t.get("function") or {}
            schema = fn.get("parameters") or {"type": "object", "properties": {}}
            tools.append({"name": fn.get("name", ""), "description": fn.get("description", ""),
                          "input_schema": schema})
        choice = "auto"
        tc_name = ""
        tc = req.get("tool_choice")
        if isinstance(tc, str):
            if tc == "none":
                tools = []
            elif tc == "required":
                choice = "any"
        elif isinstance(tc, dict) and isinstance(tc.get("function"), dict):
            choice, tc_name = "tool", tc["function"].get("name", "")
        if tools:
            out["tools"] = tools
            out["tool_choice"] = ({"type": "tool", "name": tc_name} if choice == "tool"
                                  else {"type": choice})
    return out


# ------------------------------------------------------------------ 请求体指纹规范化
def _take_client_system_texts(body: dict) -> list[str]:
    """取出调用方的 system 文本（规范化后拼进官方第 3 块），跳过 billing 头块。"""
    sys_val = body.get("system")
    if not sys_val:
        return []
    texts: list[str] = []
    if isinstance(sys_val, str):
        if sys_val.strip():
            texts.append(sys_val.strip())
    elif isinstance(sys_val, list):
        for item in sys_val:
            if isinstance(item, dict):
                t = item.get("text")
                if isinstance(t, str) and t.strip() and not t.strip().startswith("x-anthropic-billing-header:"):
                    texts.append(t.strip())
            elif isinstance(item, str) and item.strip():
                texts.append(item.strip())
    return texts


def _splice_client_into_last_system(official: list[dict], texts: list[str]) -> None:
    """保持 len(system)==3（WAF 约束），把客户端规则并入最后一块。"""
    if not official:
        return
    last = official[-1]
    text = last.get("text", "")
    text = text.replace(ZCODE_CANNED_OPENER, "").replace(ZCODE_LEAD_OUTCOME, "")
    if texts:
        text += "\n\n# Client instructions\n" + "\n\n".join(texts)
    last["text"] = text


def _apply_cache_control_ephemeral(body: dict) -> None:
    """最后一条非 system 消息的最后一个 block 追加 cache_control ephemeral（幂等）。"""
    msgs = body.get("messages")
    if not isinstance(msgs, list):
        return
    for m in reversed(msgs):
        if not isinstance(m, dict) or m.get("role") == "system":
            continue
        content = m.get("content")
        if isinstance(content, str):
            m["content"] = [{"type": "text", "text": content,
                             "cache_control": {"type": "ephemeral"}}]
        elif isinstance(content, list) and content:
            last = content[-1]
            if isinstance(last, dict) and "cache_control" not in last:
                last["cache_control"] = {"type": "ephemeral"}
        return


def _remap_claude_thinking(body: dict) -> None:
    thinking = body.get("thinking")
    if isinstance(thinking, dict) and str(thinking.get("type", "")).lower() == "adaptive":
        thinking["type"] = "enabled"
    oc = body.get("output_config")
    if isinstance(oc, dict) and isinstance(oc.get("effort"), str):
        oc["effort"] = _map_glm_effort(oc["effort"])


def map_model(model_id: str) -> str:
    mapped = MODEL_NAME_MAP.get((model_id or "").strip().lower())
    return mapped or (model_id or DEFAULT_MODEL).strip() or DEFAULT_MODEL


def normalize_body(body: dict, jwt: str) -> dict:
    """官方指纹规范化（forward.go normalizeBody）：模型别名、字符串 content 桥接为
    text block、官方 3 段 system + 客户端规则拼接、cache_control、metadata.user_id、
    thinking 映射。"""
    out = copy.deepcopy(body)
    out["model"] = map_model(out.get("model") or "")

    messages = out.get("messages")
    if isinstance(messages, list):
        for m in messages:
            if isinstance(m, dict) and isinstance(m.get("content"), str):
                m["content"] = [{"type": "text", "text": m["content"]}]

    client_sys = _take_client_system_texts(out)
    official = json.loads(SYSTEM_BLOCKS_JSON)  # 每次深拷贝，避免污染常量
    _splice_client_into_last_system(official, client_sys)
    out["system"] = official

    _apply_cache_control_ephemeral(out)

    mid = device_mid(jwt)
    out["metadata"] = {"user_id": json.dumps(
        {"device_id": mid, "account_uuid": "", "session_id": session_id_for(mid)},
        ensure_ascii=False, separators=(",", ":"))}

    _remap_claude_thinking(out)
    return out


# ------------------------------------------------------------------ 上游错误
_CODE_RE = re.compile(r'"code"\s*:\s*"?([0-9]+)')


def peek_json_code(text: str) -> int | None:
    """从错误响应体取业务码（{"code":3012,...} 或 {"type":"error",...}）。"""
    try:
        data = json.loads(text)
        code = data.get("code") or data.get("error", {}).get("code") if isinstance(data, dict) else None
        if isinstance(code, int):
            return code
    except (ValueError, AttributeError):
        pass
    m = _CODE_RE.search(text[:256])
    return int(m.group(1)) if m else None


def _error_message(resp: httpx.Response) -> str:
    text = (resp.text or "").strip()
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            if isinstance(data.get("error"), dict):
                return str(data["error"].get("message") or text[:300])
            msg = data.get("msg") or data.get("message")
            if msg:
                return f"{msg} (code={data.get('code')})"
    except ValueError:
        pass
    return text[:300] or f"HTTP {resp.status_code}"


def _map_error(status: int, code: int | None, message: str, retry_after: int | None = None) -> ZcodeError:
    """按上游业务码映射为 ZcodeError（quota.go 口径）。"""
    if status == 405 or code == CODE_WAF:
        return ZcodeError(502, f"ZCode 上游 WAF 拦截（405/3012，令牌或指纹被风控）: {message}", "waf_blocked")
    if status == 429:
        kind = "quota" if (code in QUOTA_CODES_429 or code == CODE_QUOTA) else "rate_limited"
        return ZcodeError(429, f"ZCode 上游限流/配额: {message}", kind, retry_after=retry_after)
    if code == CODE_CAPTCHA:
        return ZcodeError(503, f"ZCode 上游要求验证码（3007）: {message}", "captcha_required")
    if code is not None and (code == CODE_QUOTA or code in QUOTA_CODES_429):
        return ZcodeError(429, f"ZCode 上游配额不足（{code}）: {message}", "quota", retry_after=retry_after)
    if status >= 500:
        return ZcodeError(502, f"ZCode 上游错误 HTTP {status}: {message}", "upstream_error")
    return ZcodeError(status, f"ZCode 上游错误 HTTP {status}: {message}", "upstream_error")


def raise_for_response(resp: httpx.Response) -> None:
    """按上游业务码映射为 ZcodeError（quota.go 口径）。"""
    if resp.status_code < 400:
        return
    body = resp.text or ""
    code = peek_json_code(body)
    message = _error_message(resp)
    retry_after = None
    if resp.status_code == 429:
        try:
            retry_after = int(resp.headers.get("retry-after", "") or 0) or None
        except ValueError:
            pass
    raise _map_error(resp.status_code, code, message, retry_after)


def raise_for_body(data: dict, status: int = 200) -> None:
    """上游会以 HTTP 200 + JSON 错误体返回业务错误（如 1005 exceed quota limit），
    仅看状态码会漏判，需检查响应体（Zcode2Api 对每条响应都 peek body）。"""
    if not isinstance(data, dict):
        return
    code = data.get("code")
    if not isinstance(code, int) or code == 0:
        return
    message = str(data.get("msg") or data.get("message") or f"code {code}")
    raise _map_error(status, code, message)


# ------------------------------------------------------------------ SSE 翻译
_STOP_REASON_MAP = {"tool_use": "tool_calls", "max_tokens": "length", "stop_sequence": "stop"}


def _usage_from(data: dict) -> dict | None:
    u = data.get("usage")
    if not isinstance(u, dict):
        return None
    return {
        "prompt_tokens": int(u.get("input_tokens") or 0),
        "completion_tokens": int(u.get("output_tokens") or 0),
        "total_tokens": int(u.get("input_tokens") or 0) + int(u.get("output_tokens") or 0),
    }


def anth_event_to_openai(ev: str, data: dict, completion_id: str, model: str) -> dict | None:
    """Anthropic SSE 事件 → OpenAI chat.completion.chunk（openai.go anthEventToOA，
    另补充 message_start 的 role 首帧与 usage 透传，便于标准 OpenAI 客户端解析）。"""
    delta: dict = {}
    finish = None
    usage = None
    if ev == "message_start":
        usage = _usage_from(data.get("message") or {})
        delta = {"role": "assistant", "content": ""}
    elif ev == "content_block_delta":
        d = data.get("delta") or {}
        dtype = d.get("type")
        if dtype == "text_delta":
            delta = {"content": d.get("text", "")}
        elif dtype == "thinking_delta":
            delta = {"reasoning_content": d.get("thinking", "")}
        elif dtype == "input_json_delta":
            delta = {"tool_calls": [{"index": data.get("index", 0),
                                     "function": {"arguments": d.get("partial_json", "")}}]}
        else:
            return None
    elif ev == "content_block_start":
        cb = data.get("content_block") or {}
        if cb.get("type") != "tool_use":
            return None
        delta = {"tool_calls": [{"index": data.get("index", 0), "id": cb.get("id", ""),
                                 "type": "function", "function": {"name": cb.get("name", ""),
                                                                  "arguments": ""}}]}
    elif ev == "message_delta":
        md = data.get("delta") or {}
        stop_reason = md.get("stop_reason")
        if stop_reason:
            finish = _STOP_REASON_MAP.get(stop_reason, "stop")
        usage = _usage_from(data)
    elif ev == "message_stop":
        finish = "stop"
    else:
        return None
    choice: dict = {"index": 0, "delta": delta}
    if finish is not None:
        choice["finish_reason"] = finish
    if usage is not None:
        choice["usage"] = usage
    return {"id": completion_id, "object": "chat.completion.chunk",
            "created": int(time.time()), "model": model, "choices": [choice]}


async def iter_anthropic_sse(resp: httpx.Response):
    """解析 Anthropic SSE（event:/data: 行，data.type 覆盖 event 名）。"""
    current_event = ""
    async for line in resp.aiter_lines():
        if line.startswith("event:"):
            current_event = line[6:].strip()
            continue
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            data = json.loads(payload)
        except ValueError:
            continue
        if isinstance(data, dict):
            ev = data.get("type") or current_event
            yield ev, data


async def collect_anthropic(resp: httpx.Response):
    """聚合 Anthropic SSE 为 (text, reasoning, tool_calls, finish_reason, usage)。"""
    text_parts: list[str] = []
    reasoning_parts: list[str] = []
    tool_calls: list[dict] = []
    finish_reason = None
    usage: dict | None = None
    cur_tool: dict | None = None
    args_parts: list[str] = []
    async for ev, data in iter_anthropic_sse(resp):
        if ev == "message_start":
            usage = _usage_from(data.get("message") or {}) or usage
        elif ev == "content_block_start":
            cb = data.get("content_block") or {}
            if cb.get("type") == "tool_use":
                cur_tool = {"index": data.get("index", 0), "id": cb.get("id", ""), "type": "function",
                            "function": {"name": cb.get("name", ""), "arguments": ""}}
                args_parts = []
        elif ev == "content_block_delta":
            d = data.get("delta") or {}
            dtype = d.get("type")
            if dtype == "text_delta":
                text_parts.append(d.get("text", ""))
            elif dtype == "thinking_delta":
                reasoning_parts.append(d.get("thinking", ""))
            elif dtype == "input_json_delta":
                args_parts.append(d.get("partial_json", ""))
        elif ev == "content_block_stop":
            if cur_tool is not None:
                cur_tool["function"]["arguments"] = "".join(args_parts)
                tool_calls.append(cur_tool)
                cur_tool, args_parts = None, []
        elif ev == "message_delta":
            md = data.get("delta") or {}
            if md.get("stop_reason"):
                finish_reason = _STOP_REASON_MAP.get(md["stop_reason"], "stop")
            usage = _usage_from(data) or usage
    return "".join(text_parts), "".join(reasoning_parts), tool_calls, finish_reason, usage


def anthropic_message_to_openai(data: dict, model: str) -> dict:
    """非流式 Anthropic message JSON → chat.completion。"""
    text_parts: list[str] = []
    reasoning_parts: list[str] = []
    tool_calls: list[dict] = []
    for block in data.get("content") or []:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            text_parts.append(block.get("text", ""))
        elif btype == "thinking":
            reasoning_parts.append(block.get("thinking", ""))
        elif btype == "tool_use":
            tool_calls.append({"index": len(tool_calls), "id": block.get("id", ""), "type": "function",
                               "function": {"name": block.get("name", ""),
                                            "arguments": json.dumps(block.get("input") or {}, ensure_ascii=False)}})
    stop_reason = data.get("stop_reason")
    msg: dict = {"role": "assistant", "content": "".join(text_parts) or None}
    if reasoning_parts:
        msg["reasoning_content"] = "".join(reasoning_parts)
    if tool_calls:
        msg["tool_calls"] = tool_calls
    if not msg["content"] and reasoning_parts:
        msg["content"] = "".join(reasoning_parts)
        msg.pop("reasoning_content", None)
    return {
        "id": data.get("id") or ("chatcmpl-" + uuid.uuid4().hex[:12]),
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "message": msg,
            "finish_reason": _STOP_REASON_MAP.get(stop_reason or "", "stop"),
        }],
        "usage": _usage_from({"usage": data.get("usage")}) or
                 {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


# ------------------------------------------------------------------ 客户端
class ZcodeClient:
    def __init__(self, base_url: str = ZCODE_API_BASE, jwt: str = "", timeout: float = 600):
        self.base_url = (base_url or ZCODE_API_BASE).rstrip("/")
        self.jwt = jwt or ""
        self.timeout = timeout

    async def chat_completion(self, request_body: dict, model_id: str, *,
                              temperature=None, reasoning_effort=None) -> dict:
        """非流式：上游可能返回 SSE，统一聚合成 chat.completion。"""
        req = {**request_body, "model": model_id, "stream": False}
        if temperature is not None:
            req["temperature"] = temperature
        if reasoning_effort:
            req["reasoning_effort"] = reasoning_effort
        body = normalize_body(openai_to_anthropic(req), self.jwt)
        try:
            async with httpx.AsyncClient(timeout=self.timeout, trust_env=False) as client:
                resp = await client.post(self.base_url + MESSAGES_PATH,
                                         headers=chat_headers(self.jwt),
                                         content=json.dumps(body, ensure_ascii=False).encode("utf-8"))
        except httpx.TimeoutException:
            raise ZcodeError(504, "ZCode 上游超时", "timeout")
        except httpx.HTTPError as e:
            raise ZcodeError(502, f"无法连接 ZCode 上游: {e}", "connection_error")
        raise_for_response(resp)
        if "text/event-stream" in (resp.headers.get("content-type") or ""):
            text, reasoning, tool_calls, finish, usage = await collect_anthropic(resp)
            msg: dict = {"role": "assistant", "content": text or None}
            if reasoning:
                msg["reasoning_content"] = reasoning
            if tool_calls:
                msg["tool_calls"] = tool_calls
            if not msg["content"] and reasoning:
                msg["content"] = reasoning
                msg.pop("reasoning_content", None)
            return {"id": "chatcmpl-" + uuid.uuid4().hex[:12], "object": "chat.completion",
                    "created": int(time.time()), "model": model_id,
                    "choices": [{"index": 0, "message": msg, "finish_reason": finish or "stop"}],
                    "usage": usage or {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}}
        try:
            data = resp.json()
        except ValueError:
            raise ZcodeError(502, "ZCode 返回非 JSON", "invalid_upstream")
        if not isinstance(data, dict) or data.get("type") == "error":
            raise ZcodeError(502, f"ZCode 返回异常: {json.dumps(data, ensure_ascii=False)[:300]}", "invalid_upstream")
        raise_for_body(data)
        return anthropic_message_to_openai(data, model_id)

    async def stream_chunks(self, request_body: dict, model_id: str, *,
                            temperature=None, reasoning_effort=None):
        """流式：上游 SSE 逐事件翻译成 OpenAI chunk（dict），由 forwarder 包装为 SSE 帧。"""
        req = {**request_body, "model": model_id, "stream": True}
        if temperature is not None:
            req["temperature"] = temperature
        if reasoning_effort:
            req["reasoning_effort"] = reasoning_effort
        body = normalize_body(openai_to_anthropic(req), self.jwt)
        try:
            async with httpx.AsyncClient(timeout=self.timeout, trust_env=False) as client:
                async with client.stream("POST", self.base_url + MESSAGES_PATH,
                                         headers=chat_headers(self.jwt),
                                         content=json.dumps(body, ensure_ascii=False).encode("utf-8")) as resp:
                    if resp.status_code != 200:
                        raw = await resp.aread()
                        raise_for_response(httpx.Response(resp.status_code, content=raw,
                                                          headers=dict(resp.headers)))
                        raise ZcodeError(resp.status_code, f"ZCode 上游错误 HTTP {resp.status_code}",
                                         "upstream_error")
                    ct = (resp.headers.get("content-type") or "")
                    if "application/json" in ct:
                        # HTTP 200 + JSON 错误体（如 1005 配额），不会以 SSE 事件出现
                        raw = await resp.aread()
                        try:
                            err = json.loads(raw)
                        except ValueError:
                            raise ZcodeError(502, f"ZCode 上游返回非 JSON: {raw[:200]!r}", "invalid_upstream")
                        raise_for_body(err, resp.status_code)
                        raise ZcodeError(502, f"ZCode 上游返回异常: {json.dumps(err, ensure_ascii=False)[:200]}",
                                         "invalid_upstream")
                    completion_id = "chatcmpl-" + uuid.uuid4().hex[:12]
                    async for ev, data in iter_anthropic_sse(resp):
                        chunk = anth_event_to_openai(ev, data, completion_id, model_id)
                        if chunk is not None:
                            yield chunk
        except httpx.TimeoutException:
            raise ZcodeError(504, "ZCode 上游超时", "timeout")
        except httpx.HTTPError as e:
            raise ZcodeError(502, f"无法连接 ZCode 上游: {e}", "connection_error")
