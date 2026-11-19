"""
Protocol adapter: converts OpenAI-format requests to upstream provider formats
and converts streaming responses back to OpenAI format. Multimodal message
content (text / image_url / input_audio) is converted where the target
protocol supports it.
"""
import json
import uuid
import time


def _iter_content_blocks(content) -> list[dict]:
    """Normalize OpenAI message content into a list of content blocks.

    Plain strings become a single text block; arrays pass through as-is.
    """
    if isinstance(content, list):
        return [b for b in content if isinstance(b, dict)]
    return [{"type": "text", "text": content}] if content else []


def _parse_data_url(url: str) -> tuple[str, str] | None:
    """data:image/png;base64,xxx -> (mime_type, base64_data)."""
    if not url.startswith("data:"):
        return None
    try:
        header, data = url.split(",", 1)
        mime = header[5:].split(";", 1)[0] or "application/octet-stream"
        return mime, data
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# 工具 / 联网搜索 通用映射（跨协议透传）
#
# 各协议对「函数工具」与「内置联网搜索」的表达不同：
#   - OpenAI chat:  tools=[{type:function,function:{...}}]，搜索用
#                   tools=[{type:web_search,...}] 或 web_search_options 标量
#   - OpenAI resp:  tools=[{type:function,...}|{type:web_search,...}]
#   - Claude:       tools=[{name,input_schema}|{type:web_search_20250305,...}]
#   - Gemini:       tools=[{functionDeclarations:[...]}|{google_search:{}}]
# 下面一组纯函数把这些形态互相映射，保证工具 / 搜索请求不丢失。
# ---------------------------------------------------------------------------

# 被各协议视为「内置联网搜索」的工具 type / key
_SEARCH_TYPES = {
    "web_search", "web_search_preview", "web_search_20250305",
    "web_search_preview_2025_03_11",
}
_SEARCH_KEYS = ("google_search", "google_search_retrieval", "web_search")


def _is_search_tool(t: dict) -> bool:
    if not isinstance(t, dict):
        return False
    if t.get("type") in _SEARCH_TYPES:
        return True
    return any(k in t for k in _SEARCH_KEYS)


def _iter_function_tools(tools) -> list[dict]:
    """把任意协议形态的 tools 归一为 OpenAI function 工具列表。

    兼容 chat 形态 {type:function,function:{...}}、Responses 形态
    {type:function,name,parameters,...}、Claude 形态 {name,input_schema}、
    Gemini 形态 {functionDeclarations:[...]}。
    """
    out: list[dict] = []
    for t in tools or []:
        if not isinstance(t, dict):
            continue
        # Gemini functionDeclarations
        for fd in t.get("functionDeclarations") or t.get("function_declarations") or []:
            if isinstance(fd, dict) and fd.get("name"):
                out.append({"type": "function", "function": {
                    "name": fd.get("name", ""),
                    "description": fd.get("description", ""),
                    "parameters": fd.get("parameters") or {"type": "object", "properties": {}},
                }})
        # OpenAI chat 标准形态
        fn = t.get("function")
        if isinstance(fn, dict) and fn.get("name"):
            out.append({"type": "function", "function": fn})
            continue
        # Responses / Claude 平铺形态
        name = t.get("name")
        if name and t.get("type") in (None, "function"):
            params = t.get("parameters") or t.get("input_schema") or {"type": "object", "properties": {}}
            out.append({"type": "function", "function": {
                "name": name,
                "description": t.get("description", ""),
                "parameters": params,
            }})
    return out


def _has_search_tool(tools) -> bool:
    return any(_is_search_tool(t) for t in (tools or []))


def _openai_tools_to_claude(tools, web_search_options=None) -> list[dict]:
    """OpenAI chat/Responses tools -> Claude tools（函数 + 联网搜索）。"""
    out: list[dict] = []
    for fn in _iter_function_tools(tools):
        f = fn["function"]
        out.append({
            "name": f.get("name", ""),
            "description": f.get("description", ""),
            "input_schema": f.get("parameters") or {"type": "object", "properties": {}},
        })
    if _has_search_tool(tools) or web_search_options is not None:
        out.append({"type": "web_search_20250305", "name": "web_search"})
    return out


def _openai_tools_to_gemini(tools, web_search_options=None) -> list[dict]:
    """OpenAI chat/Responses tools -> Gemini tools（函数 + google_search）。"""
    out: list[dict] = []
    decls = []
    for fn in _iter_function_tools(tools):
        f = fn["function"]
        decls.append({
            "name": f.get("name", ""),
            "description": f.get("description", ""),
            "parameters": f.get("parameters") or {"type": "object", "properties": {}},
        })
    if decls:
        out.append({"functionDeclarations": decls})
    if _has_search_tool(tools) or web_search_options is not None:
        out.append({"google_search": {}})
    return out


def _openai_tool_choice_to_claude(tool_choice):
    """OpenAI tool_choice -> Claude tool_choice。"""
    if isinstance(tool_choice, str):
        if tool_choice == "required":
            return {"type": "any"}
        if tool_choice == "none":
            return {"type": "none"}
        return {"type": "auto"}
    if isinstance(tool_choice, dict):
        fn = (tool_choice.get("function") or {})
        if fn.get("name"):
            return {"type": "tool", "name": fn["name"]}
    return {"type": "auto"}


def _openai_tool_choice_to_gemini(tool_choice):
    """OpenAI tool_choice -> Gemini toolConfig.functionCallingConfig.mode。"""
    if isinstance(tool_choice, str):
        return {"mode": {"required": "ANY", "none": "NONE"}.get(tool_choice, "AUTO")}
    if isinstance(tool_choice, dict) and (tool_choice.get("function") or {}).get("name"):
        return {"mode": "ANY", "allowedFunctionNames": [tool_choice["function"]["name"]]}
    return {"mode": "AUTO"}


def _claude_tools_to_openai(tools) -> tuple[list[dict], bool]:
    """Claude tools -> (OpenAI function tools, has_search)."""
    fns: list[dict] = []
    has_search = False
    for t in tools or []:
        if not isinstance(t, dict):
            continue
        ttype = t.get("type", "")
        if _is_search_tool(t):
            has_search = True
            continue
        name = t.get("name")
        if name:
            fns.append({"type": "function", "function": {
                "name": name,
                "description": t.get("description", ""),
                "parameters": t.get("input_schema") or {"type": "object", "properties": {}},
            }})
    return fns, has_search


def _gemini_tools_to_openai(tools) -> tuple[list[dict], bool]:
    """Gemini tools -> (OpenAI function tools, has_search)."""
    fns: list[dict] = []
    has_search = False
    for t in tools or []:
        if not isinstance(t, dict):
            continue
        if _is_search_tool(t):
            has_search = True
        for fd in t.get("functionDeclarations") or t.get("function_declarations") or []:
            if isinstance(fd, dict) and fd.get("name"):
                fns.append({"type": "function", "function": {
                    "name": fd.get("name", ""),
                    "description": fd.get("description", ""),
                    "parameters": fd.get("parameters") or {"type": "object", "properties": {}},
                }})
    return fns, has_search


def _tool_result_text(content) -> str:
    """把工具结果 content（str 或 block 列表）压成 Claude/Gemini 需要的纯文本/JSON。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = []
        for b in content:
            if isinstance(b, str):
                texts.append(b)
            elif isinstance(b, dict):
                if b.get("type") == "text" and b.get("text"):
                    texts.append(b["text"])
                elif b.get("type") == "image_url":
                    texts.append(json.dumps(b, ensure_ascii=False))
        return "\n".join(texts) if texts else json.dumps(content, ensure_ascii=False)
    if content is None:
        return ""
    return json.dumps(content, ensure_ascii=False)


def _openai_assistant_to_claude_blocks(msg: dict) -> tuple[list[dict], list[dict]]:
    """OpenAI assistant 消息 -> (Claude content blocks, 待合并的 tool_use blocks)。"""
    blocks = _convert_openai_content_to_claude(msg.get("content", ""))
    tool_uses = []
    for tc in msg.get("tool_calls") or []:
        if not isinstance(tc, dict):
            continue
        fn = tc.get("function") or {}
        args = fn.get("arguments")
        if isinstance(args, str):
            try:
                args = json.loads(args) if args else {}
            except (ValueError, TypeError):
                args = {}
        tool_uses.append({"type": "tool_use", "id": tc.get("id") or _tool_call_id_fallback(),
                          "name": fn.get("name", ""), "input": args or {}})
    return blocks, tool_uses


def _openai_assistant_to_gemini_parts(msg: dict) -> list[dict]:
    """OpenAI assistant 消息 -> Gemini model parts（含 functionCall）。"""
    parts = _convert_openai_content_to_gemini_parts(msg.get("content", "")) or [{"text": ""}]
    for tc in msg.get("tool_calls") or []:
        if not isinstance(tc, dict):
            continue
        fn = tc.get("function") or {}
        args = fn.get("arguments")
        if isinstance(args, str):
            try:
                args = json.loads(args) if args else {}
            except (ValueError, TypeError):
                args = {}
        parts.append({"functionCall": {"name": fn.get("name", ""), "args": args or {}}})
    return parts


def _tool_call_id_fallback() -> str:
    return "toolu_" + uuid.uuid4().hex[:16]


def _convert_openai_content_to_claude(content) -> list[dict]:
    """OpenAI content blocks -> Claude content blocks.

    image_url -> type:image source (base64 for data URLs, url source for remote).
    input_audio is not supported by Claude and is skipped.
    """
    blocks = []
    for b in _iter_content_blocks(content):
        btype = b.get("type", "text")
        if btype == "text":
            blocks.append({"type": "text", "text": b.get("text", "")})
        elif btype == "image_url":
            url = (b.get("image_url") or {}).get("url", "")
            data = _parse_data_url(url)
            if data:
                blocks.append({"type": "image", "source": {"type": "base64", "media_type": data[0], "data": data[1]}})
            elif url.startswith("http"):
                blocks.append({"type": "image", "source": {"type": "url", "url": url}})
        elif btype == "image":
            # already Anthropic-shaped (e.g. re-sent history); keep it
            blocks.append(b)
        # input_audio / file / unknown types: Claude cannot accept them -> skip
    return blocks


def _convert_openai_content_to_gemini_parts(content) -> list[dict]:
    """OpenAI content blocks -> Gemini parts.

    image_url -> inline_data (data URLs) or file_data (remote, best effort).
    input_audio -> inline_data with its audio mime type.
    """
    parts = []
    for b in _iter_content_blocks(content):
        btype = b.get("type", "text")
        if btype == "text":
            if b.get("text"):
                parts.append({"text": b.get("text", "")})
        elif btype == "image_url":
            url = (b.get("image_url") or {}).get("url", "")
            data = _parse_data_url(url)
            if data:
                parts.append({"inline_data": {"mime_type": data[0], "data": data[1]}})
            elif url.startswith("http"):
                parts.append({"file_data": {"file_uri": url, "mime_type": "image/*"}})
        elif btype == "input_audio":
            audio = b.get("input_audio") or {}
            fmt = audio.get("format", "wav")
            parts.append({"inline_data": {"mime_type": f"audio/{fmt}", "data": audio.get("data", "")}})
        # unknown types: skip
    return parts


def openai_to_claude(body: dict) -> tuple[dict, dict]:
    """Convert OpenAI chat completion request to Claude Messages API format."""
    messages = body.get("messages", [])
    system_parts = []
    claude_messages = []

    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if role == "system":
            blocks = _convert_openai_content_to_claude(content)
            system_parts.append("\n".join(b["text"] for b in blocks if b["type"] == "text"))
        elif role == "assistant":
            blocks, tool_uses = _openai_assistant_to_claude_blocks(msg)
            claude_messages.append({"role": "assistant", "content": blocks + tool_uses})
        elif role == "tool":
            # OpenAI 工具结果消息 -> Claude tool_result（归到 user turn）
            tool_call_id = msg.get("tool_call_id") or ""
            claude_messages.append({"role": "user", "content": [{
                "type": "tool_result", "tool_use_id": tool_call_id,
                "content": _tool_result_text(content),
            }]})
        else:
            claude_messages.append({"role": "user", "content": _convert_openai_content_to_claude(content)})

    claude_body = {
        "model": body.get("model", ""),
        "max_tokens": body.get("max_tokens", 4096),
        "messages": claude_messages,
    }
    if system_parts:
        claude_body["system"] = "\n".join(system_parts)
    if "temperature" in body:
        claude_body["temperature"] = body["temperature"]
    if "top_p" in body:
        claude_body["top_p"] = body["top_p"]
    if body.get("stream"):
        claude_body["stream"] = True

    # 透传工具调用 / 联网搜索：函数工具 + 内置搜索工具 + tool_choice
    ctools = _openai_tools_to_claude(body.get("tools"), body.get("web_search_options"))
    if ctools:
        claude_body["tools"] = ctools
        if body.get("tool_choice") is not None:
            claude_body["tool_choice"] = _openai_tool_choice_to_claude(body["tool_choice"])

    return claude_body, {"anthropic-version": "2023-06-01", "content-type": "application/json"}


def openai_to_gemini(body: dict) -> tuple[dict, dict]:
    """Convert OpenAI chat completion request to Gemini GenerateContent format."""
    messages = body.get("messages", [])
    contents = []
    system_instruction = None

    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if role == "system":
            parts = _convert_openai_content_to_gemini_parts(content) or [{"text": ""}]
            system_instruction = {"parts": parts}
        elif role == "assistant":
            parts = _openai_assistant_to_gemini_parts(msg)
            contents.append({"role": "model", "parts": parts})
        elif role == "tool":
            # OpenAI 工具结果 -> Gemini functionResponse（user 角色）
            name = msg.get("name") or ""
            resp = msg.get("content", "")
            if isinstance(resp, str):
                try:
                    resp_obj = json.loads(resp) if resp else {}
                except (ValueError, TypeError):
                    resp_obj = {"output": resp}
            elif isinstance(resp, (dict, list)):
                resp_obj = resp
            else:
                resp_obj = {"output": str(resp)}
            contents.append({"role": "user", "parts": [{
                "functionResponse": {"name": name, "response": resp_obj},
            }]})
        else:
            parts = _convert_openai_content_to_gemini_parts(content) or [{"text": ""}]
            contents.append({"role": "user", "parts": parts})

    gemini_body = {"contents": contents}
    if system_instruction:
        gemini_body["systemInstruction"] = system_instruction

    gen_config = {}
    if "temperature" in body:
        gen_config["temperature"] = body["temperature"]
    if "max_tokens" in body:
        gen_config["maxOutputTokens"] = body["max_tokens"]
    if "top_p" in body:
        gen_config["topP"] = body["top_p"]
    if gen_config:
        gemini_body["generationConfig"] = gen_config

    # 透传工具调用 / 联网搜索：函数声明 + google_search + toolConfig
    gtools = _openai_tools_to_gemini(body.get("tools"), body.get("web_search_options"))
    if gtools:
        gemini_body["tools"] = gtools
        if body.get("tool_choice") is not None:
            gemini_body["toolConfig"] = {"functionCallingConfig": _openai_tool_choice_to_gemini(body["tool_choice"])}

    return gemini_body, {"content-type": "application/json"}


def claude_stream_chunk_to_openai(chunk_data: dict, model: str, completion_id: str) -> dict | None:
    """Convert a Claude streaming event to OpenAI SSE chunk format."""
    event_type = chunk_data.get("type", "")

    if event_type == "content_block_delta":
        text = chunk_data.get("delta", {}).get("text", "")
        if text:
            return {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": model,
                "choices": [{
                    "index": 0,
                    "delta": {"content": text},
                    "finish_reason": None,
                }],
            }

    elif event_type == "message_stop":
        return {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": model,
            "choices": [{
                "index": 0,
                "delta": {},
                "finish_reason": "stop",
            }],
        }

    return None


def gemini_stream_chunk_to_openai(chunk_data: dict, model: str, completion_id: str) -> dict | None:
    """Convert a Gemini streaming chunk to OpenAI SSE chunk format."""
    candidates = chunk_data.get("candidates", [])
    if not candidates:
        return None

    candidate = candidates[0]
    content = candidate.get("content", {})
    parts = content.get("parts", [])
    text = _gemini_parts_to_text(parts) if parts else ""
    finish_reason = candidate.get("finishReason")

    if text:
        return {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": model,
            "choices": [{
                "index": 0,
                "delta": {"content": text},
                "finish_reason": None,
            }],
        }
    elif finish_reason:
        return {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": model,
            "choices": [{
                "index": 0,
                "delta": {},
                "finish_reason": "stop",
            }],
        }

    return None


def openai_response_to_openai(data: dict, model: str) -> dict:
    """Passthrough for OpenAI-protocol responses."""
    return data


def claude_response_to_openai(data: dict, model: str) -> dict:
    """Convert a complete Claude response to OpenAI format.

    保留文本内容、图片块与 tool_use（工具调用），保证工具/图片能力不丢失。
    """
    content_blocks = []
    tool_calls = []
    for i, block in enumerate(data.get("content", [])):
        btype = block.get("type")
        if btype == "text":
            content_blocks.append({"type": "text", "text": block.get("text", "")})
        elif btype == "image":
            source = block.get("source") or {}
            if source.get("type") == "base64":
                media = source.get("media_type", "image/png")
                data_url = f"data:{media};base64,{source.get('data', '')}"
            elif source.get("url"):
                data_url = source["url"]
            else:
                continue
            content_blocks.append({"type": "image_url", "image_url": {"url": data_url}})
        elif btype == "tool_use":
            args = block.get("input")
            if not isinstance(args, str):
                args = json.dumps(args if args is not None else {}, ensure_ascii=False)
            tool_calls.append({
                "id": block.get("id") or f"call_{uuid.uuid4().hex[:16]}",
                "type": "function",
                "function": {"name": block.get("name", ""), "arguments": args},
            })
    # 只有文本且单个文本块时，content 用纯字符串（兼容性最好）；否则用数组
    if content_blocks and all(b.get("type") == "text" for b in content_blocks):
        content: object = "\n".join(b.get("text", "") for b in content_blocks)
    else:
        content = content_blocks

    message: dict = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    usage = data.get("usage") or {}
    stop_reason = data.get("stop_reason") or ""
    finish = "tool_calls" if tool_calls else ("length" if stop_reason == "max_tokens" else "stop")
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "message": message,
            "finish_reason": finish,
        }],
        "usage": {
            "prompt_tokens": usage.get("input_tokens", 0),
            "completion_tokens": usage.get("output_tokens", 0),
            "total_tokens": (
                usage.get("input_tokens", 0)
                + usage.get("output_tokens", 0)
            ),
        },
    }


def _gemini_parts_to_text(parts: list) -> str:
    """Flatten Gemini parts to text; inline images become markdown data-URI images."""
    chunks = []
    for p in parts:
        if p.get("text"):
            chunks.append(p["text"])
        inline = p.get("inline_data") or p.get("inlineData")
        if inline and inline.get("data"):
            mime = inline.get("mime_type") or inline.get("mimeType") or "image/png"
            chunks.append(f"![image](data:{mime};base64,{inline['data']})")
    return "".join(chunks)


def gemini_response_to_openai(data: dict, model: str) -> dict:
    """Convert a complete Gemini response to OpenAI format.

    保留文本、图片（markdown data URI）与 functionCall（工具调用）。
    """
    candidates = data.get("candidates", [])
    content = ""
    tool_calls = []
    finish = "stop"
    if candidates:
        cand = candidates[0]
        parts = cand.get("content", {}).get("parts", [])
        content = _gemini_parts_to_text(parts)
        for i, p in enumerate(parts):
            fc = p.get("functionCall") or p.get("function_call")
            if not fc:
                continue
            args = fc.get("args")
            if not isinstance(args, str):
                args = json.dumps(args if args is not None else {}, ensure_ascii=False)
            tool_calls.append({
                "id": f"call_{uuid.uuid4().hex[:16]}",
                "type": "function",
                "function": {"name": fc.get("name", ""), "arguments": args},
            })
        fr = cand.get("finishReason") or ""
        if fr in ("MAX_TOKENS",):
            finish = "length"
        elif tool_calls or fr in ("STOP", "FUNCTION_CALL"):
            finish = "tool_calls" if tool_calls else "stop"

    usage = data.get("usageMetadata") or {}
    message: dict = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "message": message,
            "finish_reason": finish,
        }],
        "usage": {
            "prompt_tokens": usage.get("promptTokenCount", 0),
            "completion_tokens": usage.get("candidatesTokenCount", 0),
            "total_tokens": usage.get("totalTokenCount", 0),
        },
    }

def openai_to_claude_response(data: dict) -> dict:
    """Convert an OpenAI-format completion response to Claude Messages API format.

    保留文本、图片块与 tool_calls（作为 tool_use）。
    """
    choices = data.get("choices", [])
    message = choices[0].get("message", {}) if choices else {}
    usage = data.get("usage", {})

    content = []
    text = _responses_text_of(message.get("content"))
    if text:
        content.append({"type": "text", "text": text})
    # 图片：OpenAI content 数组里的 image_url
    raw_content = message.get("content")
    if isinstance(raw_content, list):
        for b in raw_content:
            if isinstance(b, dict) and b.get("type") == "image_url":
                url = (b.get("image_url") or {}).get("url", "")
                data_url = _parse_data_url(url)
                if data_url:
                    content.append({"type": "image", "source": {
                        "type": "base64", "media_type": data_url[0], "data": data_url[1]}})
                elif url.startswith("http"):
                    content.append({"type": "image", "source": {"type": "url", "url": url}})
    # 工具调用
    for tc in message.get("tool_calls") or []:
        if not isinstance(tc, dict):
            continue
        fn = tc.get("function") or {}
        args = fn.get("arguments")
        if isinstance(args, str):
            try:
                args = json.loads(args) if args else {}
            except (ValueError, TypeError):
                args = {}
        content.append({"type": "tool_use", "id": tc.get("id") or _tool_call_id_fallback(),
                        "name": fn.get("name", ""), "input": args or {}})
    if not content:
        content = [{"type": "text", "text": ""}]

    has_tools = any(b.get("type") == "tool_use" for b in content)
    finish = choices[0].get("finish_reason") if choices else "stop"
    stop_reason = "tool_use" if has_tools else ("max_tokens" if finish == "length" else "end_turn")
    return {
        "id": data.get("id") or ("msg_" + uuid.uuid4().hex[:16]),
        "type": "message",
        "role": "assistant",
        "content": content,
        "model": data.get("model", ""),
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
        },
    }


def openai_to_gemini_response(data: dict) -> dict:
    "Convert an OpenAI-format completion response to Gemini GenerateContent format."
    choices = data.get("choices", [])
    message = choices[0].get("message", {}) if choices else {}
    usage = data.get("usage", {})
    finish_reason = choices[0].get("finish_reason", "stop") if choices else "stop"

    parts = []
    text = _responses_text_of(message.get("content"))
    if text:
        parts.append({"text": text})
    raw_content = message.get("content")
    if isinstance(raw_content, list):
        for b in raw_content:
            if isinstance(b, dict) and b.get("type") == "image_url":
                url = (b.get("image_url") or {}).get("url", "")
                data_url = _parse_data_url(url)
                if data_url:
                    parts.append({"inlineData": {"mimeType": data_url[0], "data": data_url[1]}})
    for tc in message.get("tool_calls") or []:
        if not isinstance(tc, dict):
            continue
        fn = tc.get("function") or {}
        args = fn.get("arguments")
        if isinstance(args, str):
            try:
                args = json.loads(args) if args else {}
            except (ValueError, TypeError):
                args = {}
        parts.append({"functionCall": {"name": fn.get("name", ""), "args": args or {}}})
    if not parts:
        parts = [{"text": ""}]

    gemini_finish = "STOP"
    if finish_reason == "length":
        gemini_finish = "MAX_TOKENS"

    return {
        "candidates": [{
            "content": {"parts": parts, "role": "model"},
            "finishReason": gemini_finish,
        }],
        "usageMetadata": {
            "promptTokenCount": usage.get("prompt_tokens", 0),
            "candidatesTokenCount": usage.get("completion_tokens", 0),
            "totalTokenCount": usage.get("total_tokens", 0),
        },
    }


def convert_openai_stream_to_claude_chunks(openai_chunk: dict, msg_id: str,
                                            state: dict | None = None) -> str | None:
    """Convert an OpenAI SSE chunk to Claude streaming event string.

    支持文本增量与工具调用增量。工具调用需要跨 chunk 累积（index -> block），
    调用方需为每次请求传入同一个可变 dict 作为 state（None 时为无状态模式）。
    """
    choices = openai_chunk.get("choices", [])
    if not choices:
        return None

    choice = choices[0]
    delta = choice.get("delta", {})
    finish = choice.get("finish_reason")
    content = delta.get("content", "")
    state = state if state is not None else {}
    tool_blocks = state.setdefault("_claude_tool_blocks", {})
    next_index = state.setdefault("_claude_next_index", [0])

    events: list[str] = []

    if content:
        event = {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": content},
        }
        events.append("event: content_block_delta\ndata: " + json.dumps(event, ensure_ascii=False) + "\n\n")

    # 工具调用增量：首次出现（带 id/name）发 content_block_start，arguments 发 input_json_delta
    for tc in delta.get("tool_calls") or []:
        if not isinstance(tc, dict):
            continue
        idx = tc.get("index", 0)
        fn = tc.get("function") or {}
        block = tool_blocks.get(idx)
        if block is None:
            bidx = next_index[0]
            next_index[0] += 1
            block = {"block_index": bidx, "started": False}
            tool_blocks[idx] = block
        if not block["started"]:
            start_event = {
                "type": "content_block_start",
                "index": block["block_index"],
                "content_block": {
                    "type": "tool_use",
                    "id": tc.get("id") or _tool_call_id_fallback(),
                    "name": fn.get("name", ""),
                    "input": {},
                },
            }
            events.append("event: content_block_start\ndata: " + json.dumps(start_event, ensure_ascii=False) + "\n\n")
            block["started"] = True
        arg = fn.get("arguments")
        if arg:
            delta_event = {
                "type": "content_block_delta",
                "index": block["block_index"],
                "delta": {"type": "input_json_delta", "partial_json": arg},
            }
            events.append("event: content_block_delta\ndata: " + json.dumps(delta_event, ensure_ascii=False) + "\n\n")

    if finish in ("stop", "tool_calls"):
        for block in tool_blocks.values():
            stop_event = {"type": "content_block_stop", "index": block["block_index"]}
            events.append("event: content_block_stop\ndata: " + json.dumps(stop_event, ensure_ascii=False) + "\n\n")
        event = {"type": "message_stop"}
        events.append("event: message_stop\ndata: " + json.dumps(event) + "\n\n")

    return "".join(events) if events else None


def convert_openai_stream_to_gemini_chunks(openai_chunk: dict,
                                            state: dict | None = None) -> str | None:
    """Convert an OpenAI SSE chunk to Gemini streaming JSON line(s).

    支持文本增量与工具调用。工具调用跨 chunk 累积，在 finish 时输出完整
    functionCall parts（Gemini 流式协议以完整 functionCall 表达）。
    """
    choices = openai_chunk.get("choices", [])
    if not choices:
        return None

    choice = choices[0]
    delta = choice.get("delta", {})
    finish = choice.get("finish_reason")
    content = delta.get("content", "")
    state = state if state is not None else {}
    pending = state.setdefault("_gemini_tool_calls", {})

    for tc in delta.get("tool_calls") or []:
        if not isinstance(tc, dict):
            continue
        idx = tc.get("index", 0)
        acc = pending.setdefault(idx, {"id": "", "name": "", "arguments": ""})
        if tc.get("id"):
            acc["id"] = tc["id"]
        fn = tc.get("function") or {}
        if fn.get("name"):
            acc["name"] = fn["name"]
        if fn.get("arguments"):
            acc["arguments"] += fn["arguments"]

    lines: list[str] = []
    if content:
        lines.append(json.dumps({
            "candidates": [{"content": {"parts": [{"text": content}], "role": "model"}}],
        }, ensure_ascii=False) + "\n")

    if finish in ("stop", "tool_calls") and pending:
        parts = []
        for acc in pending.values():
            args = acc["arguments"] or "{}"
            try:
                args_obj = json.loads(args) if args else {}
            except (ValueError, TypeError):
                args_obj = {}
            parts.append({"functionCall": {"name": acc["name"], "args": args_obj}})
        lines.append(json.dumps({
            "candidates": [{"content": {"parts": parts, "role": "model"}, "finishReason": "STOP"}],
        }, ensure_ascii=False) + "\n")

    if finish in ("stop", "tool_calls") and not pending:
        lines.append(json.dumps({
            "candidates": [{"content": {"parts": [], "role": "model"}, "finishReason": "STOP"}],
        }) + "\n")

    return "".join(lines) if lines else None
# ============================================================
# Image Generation Protocol Adapters
# ============================================================

def openai_image_to_gemini(body: dict) -> dict:
    """Convert OpenAI /v1/images/generations request to Gemini generateContent format.

    For Gemini native image generation models (gemini-2.0-flash-preview-image-generation etc.),
    we use the generateContent endpoint with responseModalities including IMAGE.

    For Imagen models, we use the predict endpoint instead.
    Returns (gemini_body, endpoint_action) where endpoint_action is 'generateContent' or 'predict'.
    """
    model = body.get("model", "")
    prompt = body.get("prompt", "")

    if "imagen" in model.lower():
        # Imagen predict endpoint format
        imagen_body = {
            "instances": [{"prompt": prompt}],
            "parameters": {
                "sampleCount": body.get("n", 1),
            },
        }
        size = body.get("size", "1024x1024")
        if size and "x" in size:
            parts = size.split("x")
            try:
                imagen_body["parameters"]["width"] = int(parts[0])
                imagen_body["parameters"]["height"] = int(parts[1])
            except ValueError:
                pass
        return {"body": imagen_body, "action": "predict"}

    # Gemini native image generation via generateContent
    gemini_body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseModalities": ["TEXT", "IMAGE"],
        },
    }
    n = body.get("n", 1)
    if n and n > 1:
        gemini_body["generationConfig"]["candidateCount"] = min(n, 4)

    return {"body": gemini_body, "action": "generateContent"}


def gemini_image_response_to_openai(data: dict, model: str, prompt: str = "") -> dict:
    """Convert Gemini image generation response to OpenAI /v1/images/generations format."""
    created = int(time.time())
    image_data_list = []

    candidates = data.get("candidates", [])
    if candidates:
        parts = candidates[0].get("content", {}).get("parts", [])
        for part in parts:
            if "inlineData" in part:
                mime = part["inlineData"].get("mimeType", "image/png")
                b64 = part["inlineData"].get("data", "")
                image_data_list.append({
                    "b64_json": b64,
                    "revised_prompt": prompt,
                })
    elif "predictions" in data:
        for pred in data["predictions"]:
            b64 = pred.get("bytesBase64Encoded", "")
            if b64:
                image_data_list.append({
                    "b64_json": b64,
                    "revised_prompt": prompt,
                })

    return {
        "created": created,
        "data": image_data_list if image_data_list else [],
    }


def imagen_response_to_openai(data: dict, model: str) -> dict:
    """Convert Imagen predict response to OpenAI format."""
    return gemini_image_response_to_openai(data, model)


# ============================================================
# TTS / Audio Speech Protocol Adapters
# ============================================================

def openai_tts_to_gemini(body: dict) -> dict:
    """Convert OpenAI /v1/audio/speech request to Gemini generateContent format.

    Gemini TTS uses generateContent with speechConfig.
    """
    input_text = body.get("input", "")
    voice = body.get("voice", "Kore")

    gemini_body = {
        "contents": [{"role": "user", "parts": [{"text": input_text}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {
                "voiceConfig": {
                    "prebuiltVoiceConfig": {
                        "voiceName": voice,
                    }
                }
            },
        },
    }

    speed = body.get("speed")
    if speed:
        gemini_body["generationConfig"]["speechConfig"]["speakingRate"] = float(speed)

    return gemini_body


def gemini_tts_response_to_openai_audio(data: dict) -> bytes:
    """Extract raw audio bytes from Gemini TTS generateContent response."""
    candidates = data.get("candidates", [])
    if not candidates:
        return b""
    parts = candidates[0].get("content", {}).get("parts", [])
    for part in parts:
        if "inlineData" in part:
            import base64
            return base64.b64decode(part["inlineData"].get("data", ""))
    return b""


# ============================================================
# Video Generation Protocol Adapters
# ============================================================

def openai_video_request(body: dict) -> dict:
    """Normalize an OpenAI-style video generation request body.

    Different providers use different formats. This returns a unified dict
    that forwarder can dispatch.
    """
    return {
        "model": body.get("model", ""),
        "prompt": body.get("prompt", ""),
        "image": body.get("image") or body.get("init_image"),
        "images": body.get("images", []),
        "mode": body.get("mode", "ti2vid"),
        "n": body.get("n", 1),
        "size": body.get("size", "1152x768"),
        "num_frames": body.get("num_frames", 121),
        "frame_rate": body.get("frame_rate", 24),
        "seed": body.get("seed"),
        "negative_prompt": body.get("negative_prompt"),
        "extra_body": body.get("extra_body", {}),
    }


def video_request_to_gemini(body: dict) -> dict:
    """Convert normalized video request to Gemini Veo format.

    Note: Veo video generation is typically done via the Vertex AI /predict endpoint.
    This returns the body for that endpoint.
    """
    prompt = body.get("prompt", "")
    instances = [{"prompt": prompt}]

    image = body.get("image")
    if image:
        instances[0]["image"] = {"gcsUri": image} if image.startswith("gs://") else {"bytesBase64Encoded": ""}

    params = {}
    if body.get("num_frames"):
        params["sampleCount"] = body.get("n", 1)
    if body.get("seed") is not None:
        params["seed"] = body["seed"]

    return {"instances": instances, "parameters": params}


def video_response_to_openai(data: dict, task_id: str = "") -> dict:
    """Normalize video generation response to OpenAI-like format.

    Returns a dict with 'id', 'object', 'status', and optionally 'data' or 'video_url'.
    """
    # AgnesAi format
    if "task_id" in data or "id" in data:
        return {
            "id": data.get("task_id") or data.get("id") or task_id,
            "object": "video.generation",
            "status": data.get("status", "queued"),
            "video_url": data.get("video_url") or data.get("url", ""),
            "created": data.get("created", int(time.time())),
        }
    # Gemini Veo format (operation-based)
    if "name" in data:
        return {
            "id": data["name"].split("/")[-1] if "/" in data["name"] else data["name"],
            "object": "video.generation",
            "status": "in_progress" if not data.get("done") else "completed",
            "created": int(time.time()),
        }
    return {
        "id": task_id or f"vid-{uuid.uuid4().hex[:12]}",
        "object": "video.generation",
        "status": "unknown",
        "raw": data,
        "created": int(time.time()),
    }

# ============================================================
# OpenAI Responses API (Codex OAuth upstream) conversions
# ============================================================

def chat_to_responses_request(body: dict, model_id: str) -> dict:
    """Convert an OpenAI chat/completions body into a Codex Responses API request.

    保留文本/图片（含 detail）/工具函数/联网搜索，以及历史工具调用与结果。
    """
    instructions = ""
    input_items = []
    for msg in body.get("messages", []):
        role = msg.get("role", "user")
        blocks = _iter_content_blocks(msg.get("content", ""))
        if role == "system":
            instructions += "\n".join(b.get("text", "") for b in blocks if b.get("type") == "text")
            continue
        if role == "assistant" and msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                if not isinstance(tc, dict):
                    continue
                fn = tc.get("function") or {}
                args = fn.get("arguments")
                if isinstance(args, dict):
                    args = json.dumps(args, ensure_ascii=False)
                input_items.append({
                    "type": "function_call",
                    "call_id": tc.get("id") or f"call_{uuid.uuid4().hex[:16]}",
                    "name": fn.get("name", ""),
                    "arguments": args or "",
                })
            continue
        if role == "tool":
            out = msg.get("content", "")
            if not isinstance(out, str):
                out = json.dumps(out, ensure_ascii=False)
            input_items.append({
                "type": "function_call_output",
                "call_id": msg.get("tool_call_id") or "",
                "output": out,
            })
            continue
        content_items = []
        for b in blocks:
            if b.get("type") == "text":
                content_items.append({"type": "input_text", "text": b.get("text", "")})
            elif b.get("type") == "image_url":
                img = b.get("image_url") or {}
                url = img.get("url", "")
                item = {"type": "input_image", "image_url": url}
                if img.get("detail"):
                    item["detail"] = img["detail"]
                content_items.append(item)
            elif b.get("type") == "input_audio":
                audio = b.get("input_audio") or {}
                content_items.append({"type": "input_audio", "data": audio.get("data", ""),
                                      "format": audio.get("format", "wav")})
        if not content_items:
            continue
        input_items.append({"role": "assistant" if role == "assistant" else "user", "content": content_items})

    req = {
        "model": model_id,
        "instructions": instructions,
        "input": input_items,
        "stream": bool(body.get("stream")),
        "store": False,
    }
    if "temperature" in body:
        req["temperature"] = body["temperature"]
    if "top_p" in body:
        req["top_p"] = body["top_p"]

    # 工具：函数工具 + 内置联网搜索
    tools = []
    for fn in _iter_function_tools(body.get("tools")):
        f = fn["function"]
        tools.append({"type": "function", "name": f.get("name", ""),
                      "description": f.get("description", ""),
                      "parameters": f.get("parameters") or {"type": "object", "properties": {}}})
    if _has_search_tool(body.get("tools")) or body.get("web_search_options") is not None:
        tools.append({"type": "web_search"})
    if tools:
        req["tools"] = tools
        if body.get("tool_choice") is not None:
            req["tool_choice"] = body["tool_choice"]
    return req


def responses_to_chat(data: dict, model_id: str) -> dict:
    """Convert a Responses API result into an OpenAI chat.completion response.

    保留文本与 function_call（工具调用）。
    """
    text = ""
    tool_calls = []
    for out in data.get("output", []):
        otype = out.get("type")
        if otype == "message":
            for c in out.get("content", []):
                if c.get("type") == "output_text":
                    text += c.get("text", "")
                elif c.get("type") == "output_image":
                    url = c.get("image_url") or c.get("url")
                    if url:
                        text += f"![image]({url})"
        elif otype == "function_call":
            args = out.get("arguments")
            if isinstance(args, dict):
                args = json.dumps(args, ensure_ascii=False)
            tool_calls.append({
                "id": out.get("call_id") or f"call_{uuid.uuid4().hex[:16]}",
                "type": "function",
                "function": {"name": out.get("name", ""), "arguments": args or ""},
            })
    usage = data.get("usage") or {}
    message: dict = {"role": "assistant", "content": text}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {
        "id": data.get("id") or f"chatcmpl-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model_id,
        "choices": [{"index": 0, "message": message,
                     "finish_reason": "tool_calls" if tool_calls else "stop"}],
        "usage": {
            "prompt_tokens": usage.get("input_tokens", 0),
            "completion_tokens": usage.get("output_tokens", 0),
            "total_tokens": usage.get("total_tokens", usage.get("input_tokens", 0) + usage.get("output_tokens", 0)),
        },
    }


# ============================================================
# OpenAI Responses API (inbound) conversions
# 网关对外提供 POST /v1/responses：入站是 Responses 请求 / 响应，
# 内部统一走 chat.completions 链路，这里负责两个方向的转换。
# ============================================================

# 请求里需要「原样透传给上游 chat 端点」的标量字段
_RESPONSES_PASSTHROUGH = (
    "temperature", "top_p", "seed", "frequency_penalty", "presence_penalty",
    "stop", "user", "service_tier", "parallel_tool_calls",
)

_FINISH_REASON_TO_STATUS = {
    "stop": ("completed", None),
    "tool_calls": ("completed", None),
    "function_call": ("completed", None),
    "content_filter": ("incomplete", "content_filter"),
    "length": ("incomplete", "max_output_tokens"),
}


def _responses_text_of(content) -> str:
    """Flatten Responses input/instructions text content into plain text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            b.get("text", "") for b in content
            if isinstance(b, dict) and b.get("type") in ("input_text", "output_text", "text", "summary_text")
        )
    return ""


def _responses_content_to_openai(content) -> list[dict]:
    """Responses content parts -> OpenAI chat content blocks."""
    blocks = []
    if isinstance(content, str):
        return [{"type": "text", "text": content}] if content else []
    if not isinstance(content, list):
        return []
    for b in content:
        if not isinstance(b, dict):
            continue
        btype = b.get("type", "")
        if btype in ("input_text", "output_text", "text", "summary_text"):
            blocks.append({"type": "text", "text": b.get("text", "")})
        elif btype == "input_image":
            url = b.get("image_url")
            if isinstance(url, dict):
                url = url.get("url")
            url = url or b.get("url") or b.get("file_id") or ""
            if url:
                image: dict = {"url": url}
                if b.get("detail"):
                    image["detail"] = b["detail"]
                blocks.append({"type": "image_url", "image_url": image})
        elif btype == "input_audio":
            audio = b.get("input_audio") if isinstance(b.get("input_audio"), dict) else b
            blocks.append({
                "type": "input_audio",
                "input_audio": {
                    "data": audio.get("data", ""),
                    "format": audio.get("format", "wav"),
                },
            })
        elif btype == "input_file":
            ref = b.get("file_id") or b.get("filename") or b.get("file_url") or ""
            if ref:
                blocks.append({"type": "text", "text": f"[file: {ref}]"})
        # unknown part types are dropped
    return blocks


def _responses_item_to_chat_messages(item) -> list[dict]:
    """One Responses input item -> zero or more chat messages."""
    if isinstance(item, str):
        return [{"role": "user", "content": item}] if item else []
    if not isinstance(item, dict):
        return []
    itype = item.get("type")
    if itype == "reasoning":
        # 无状态模式下 reasoning item 无实质内容，回传只会污染上下文
        return []
    if itype == "function_call":
        args = item.get("arguments") or ""
        if isinstance(args, (dict, list)):
            args = json.dumps(args, ensure_ascii=False)
        return [{"role": "assistant", "content": f"[tool_call] {item.get('name', '')} {args}".strip()}]
    if itype == "function_call_output":
        out = item.get("output", "")
        if not isinstance(out, str):
            out = json.dumps(out, ensure_ascii=False)
        return [{"role": "user", "content": f"[tool_result] {out}"}]
    role = item.get("role")
    content = item.get("content")
    if isinstance(content, str):
        return [{"role": role or "user", "content": content}] if content else []
    blocks = _responses_content_to_openai(content)
    if not blocks:
        return []
    return [{"role": role or "user", "content": blocks}]


def _responses_tools_to_chat(tools) -> tuple[list[dict], list[dict]]:
    """Responses tools -> (chat function tools, web_search tool markers).

    函数工具直接转换；内置 web_search 工具以 chat 可识别的
    ``{"type": "web_search"}`` 标记返回，供下游 openai_to_claude/gemini
    转成对应形态，避免搜索能力丢失。
    """
    out: list[dict] = []
    search: list[dict] = []
    for t in tools or []:
        if not isinstance(t, dict):
            continue
        ttype = t.get("type")
        if _is_search_tool(t):
            search.append({"type": "web_search"})
            continue
        if ttype != "function":
            continue
        if "function" in t:
            out.append(t)
            continue
        fn = {k: v for k, v in t.items() if k != "type"}
        if fn.get("name"):
            out.append({"type": "function", "function": fn})
    return out, search


def _responses_input_to_chat_messages(raw_input) -> list[dict]:
    """Responses input item 列表 -> chat messages，保留工具调用历史结构。

    - message item -> 常规 user/assistant（含图片等 content blocks）
    - function_call item -> assistant 消息的 tool_calls（连续的合并）
    - function_call_output item -> role=tool 消息
    - reasoning item -> 丢弃（无状态模式无实质内容）
    """
    messages: list[dict] = []
    pending_tool_calls: list[dict] = []

    def flush():
        if pending_tool_calls:
            messages.append({"role": "assistant", "content": "", "tool_calls": list(pending_tool_calls)})
            pending_tool_calls.clear()

    for item in raw_input or []:
        if isinstance(item, str):
            if item:
                flush()
                messages.append({"role": "user", "content": item})
            continue
        if not isinstance(item, dict):
            continue
        itype = item.get("type")
        if itype == "reasoning":
            continue
        if itype == "function_call":
            args = item.get("arguments") or ""
            if isinstance(args, (dict, list)):
                args = json.dumps(args, ensure_ascii=False)
            pending_tool_calls.append({
                "id": item.get("call_id") or f"call_{uuid.uuid4().hex[:16]}",
                "type": "function",
                "function": {"name": item.get("name", ""), "arguments": args},
            })
            continue
        if itype == "function_call_output":
            flush()
            out = item.get("output", "")
            if not isinstance(out, str):
                out = json.dumps(out, ensure_ascii=False)
            messages.append({
                "role": "tool",
                "tool_call_id": item.get("call_id") or "",
                "content": out,
            })
            continue
        # 普通 message：先冲刷挂起的 tool_calls
        flush()
        role = item.get("role")
        content = item.get("content")
        if isinstance(content, str):
            if content:
                messages.append({"role": role or "user", "content": content})
        else:
            blocks = _responses_content_to_openai(content)
            if blocks:
                messages.append({"role": role or "user", "content": blocks})
    flush()
    return messages


def responses_request_to_chat(body: dict) -> dict:
    """Convert an inbound /v1/responses request into a chat/completions body.

    Deliberately stateless: `store` / `previous_response_id` are not honoured
    (the gateway keeps no response store), the caller owns the full history.
    """
    chat: dict = {"model": body.get("model", "")}
    messages: list[dict] = []

    instructions = body.get("instructions")
    instructions_text = _responses_text_of(instructions)
    if instructions_text.strip():
        messages.append({"role": "system", "content": instructions_text})

    raw_input = body.get("input")
    if isinstance(raw_input, str):
        if raw_input:
            messages.append({"role": "user", "content": raw_input})
    else:
        messages.extend(_responses_input_to_chat_messages(raw_input))

    chat["messages"] = messages

    if body.get("stream"):
        chat["stream"] = True
    if body.get("max_output_tokens") is not None:
        chat["max_tokens"] = body["max_output_tokens"]
    for key in _RESPONSES_PASSTHROUGH:
        if body.get(key) is not None:
            chat[key] = body[key]

    tools, search_tools = _responses_tools_to_chat(body.get("tools"))
    if tools:
        chat["tools"] = tools
    if search_tools:
        chat["web_search_options"] = {}
    tool_choice = body.get("tool_choice")
    if tools and tool_choice is not None:
        chat["tool_choice"] = tool_choice

    reasoning = body.get("reasoning")
    if isinstance(reasoning, dict) and reasoning.get("effort"):
        chat["reasoning_effort"] = reasoning["effort"]

    text_cfg = body.get("text")
    if isinstance(text_cfg, dict) and isinstance(text_cfg.get("format"), dict):
        chat["response_format"] = text_cfg["format"]

    direct_provider_id = body.get("_direct_provider_id")
    if direct_provider_id is not None:
        chat["_direct_provider_id"] = direct_provider_id

    return chat


def _chat_usage_to_responses(usage: dict | None) -> dict | None:
    if not usage:
        return None
    input_tokens = usage.get("prompt_tokens", 0) or 0
    output_tokens = usage.get("completion_tokens", 0) or 0
    total = usage.get("total_tokens", 0) or (input_tokens + output_tokens)
    details_in = usage.get("prompt_tokens_details") or {}
    details_out = usage.get("completion_tokens_details") or {}
    return {
        "input_tokens": input_tokens,
        "input_tokens_details": {"cached_tokens": details_in.get("cached_tokens", 0) or 0},
        "output_tokens": output_tokens,
        "output_tokens_details": {"reasoning_tokens": details_out.get("reasoning_tokens", 0) or 0},
        "total_tokens": total,
    }


def _response_message_item(text: str, item_id: str, status: str = "completed") -> dict:
    return {
        "id": item_id,
        "type": "message",
        "status": status,
        "role": "assistant",
        "content": [{"type": "output_text", "text": text, "annotations": []}],
    }


def _response_function_call_items(tool_calls) -> list[dict]:
    items = []
    for tc in tool_calls or []:
        if not isinstance(tc, dict):
            continue
        fn = tc.get("function") or {}
        items.append({
            "id": f"fc_{uuid.uuid4().hex[:20]}",
            "type": "function_call",
            "status": "completed",
            "call_id": tc.get("id") or f"call_{uuid.uuid4().hex[:20]}",
            "name": fn.get("name", ""),
            "arguments": fn.get("arguments", "") or "",
        })
    return items


def response_envelope(
    response_id: str,
    model_id: str,
    status: str,
    output: list[dict],
    usage: dict | None = None,
    created_at: int | None = None,
    error: dict | None = None,
    incomplete_details: dict | None = None,
    instructions: str | None = None,
    max_output_tokens: int | None = None,
) -> dict:
    """Build a full OpenAI Response object."""
    return {
        "id": response_id,
        "object": "response",
        "created_at": created_at if created_at is not None else int(time.time()),
        "status": status,
        "error": error,
        "incomplete_details": incomplete_details,
        "instructions": instructions,
        "max_output_tokens": max_output_tokens,
        "model": model_id,
        "output": output,
        "parallel_tool_calls": True,
        "previous_response_id": None,
        "reasoning": {"effort": None, "summary": None},
        "store": False,
        "temperature": 1.0,
        "text": {"format": {"type": "text"}},
        "tool_choice": "auto",
        "tools": [],
        "top_p": 1.0,
        "truncation": "disabled",
        "usage": usage,
        "user": None,
        "metadata": {},
    }


def chat_completion_to_response(
    data: dict, model_id: str, response_id: str | None = None,
    instructions: str | None = None, max_output_tokens: int | None = None,
) -> dict:
    """Convert a chat.completion payload into a Responses API object."""
    choices = data.get("choices") or [{}]
    choice = choices[0] if isinstance(choices[0], dict) else {}
    message = choice.get("message") or {}
    text = _responses_text_of(message.get("content"))
    finish_reason = choice.get("finish_reason")

    output: list[dict] = []
    if text:
        output.append(_response_message_item(text, f"msg_{uuid.uuid4().hex[:20]}"))
    output.extend(_response_function_call_items(message.get("tool_calls")))

    status, incomplete_reason = _FINISH_REASON_TO_STATUS.get(finish_reason or "stop", ("completed", None))
    incomplete_details = {"reason": incomplete_reason} if incomplete_reason else None

    return response_envelope(
        response_id=response_id or f"resp_{uuid.uuid4().hex[:24]}",
        model_id=model_id,
        status=status,
        output=output,
        usage=_chat_usage_to_responses(data.get("usage")),
        created_at=data.get("created") if isinstance(data.get("created"), int) else None,
        incomplete_details=incomplete_details,
        instructions=instructions,
        max_output_tokens=max_output_tokens,
    )


class ResponsesStreamWriter:
    """Incremental chat.completion.chunk -> Responses API SSE events.

    Chat chunks arrive as {"choices": [{"delta": {...}, "finish_reason": ...}]}.
    Each returned item is an (event_type, payload) pair to be written as
    `event: <type>` + `data: <payload>` (no `data: [DONE]` terminator).
    """

    def __init__(self, model_id: str, instructions: str | None = None,
                 max_output_tokens: int | None = None):
        self.model_id = model_id
        self.instructions = instructions
        self.max_output_tokens = max_output_tokens
        self.response_id = f"resp_{uuid.uuid4().hex[:24]}"
        self.item_id = f"msg_{uuid.uuid4().hex[:20]}"
        self.created_at = int(time.time())
        self.sequence = 0
        self.text = ""
        self.started = False
        self.finished = False
        self.finish_reason: str | None = None
        self.usage: dict | None = None
        self.error_payload: dict | None = None
        self.tool_calls: dict[int, dict] = {}  # index -> {id,name,arguments}
        self.tool_call_emitted: set[int] = set()

    # -- internals ---------------------------------------------------------
    def _event(self, event_type: str, payload: dict) -> tuple[str, dict]:
        self.sequence += 1
        payload = {"type": event_type, "sequence_number": self.sequence, **payload}
        return event_type, payload

    def _item(self, status: str, text: str | None = None) -> dict:
        return _response_message_item(text if text is not None else self.text, self.item_id, status)

    # -- public API --------------------------------------------------------
    def _begin_events(self) -> list[tuple[str, dict]]:
        """Emit response.created / in_progress / output_item.added / content_part.added."""
        self.started = True
        empty = self._envelope("in_progress")
        return [
            self._event("response.created", {"response": empty}),
            self._event("response.in_progress", {"response": empty}),
            self._event("response.output_item.added", {
                "output_index": 0,
                "item": self._item("in_progress", text=""),
            }),
            self._event("response.content_part.added", {
                "item_id": self.item_id,
                "output_index": 0,
                "content_index": 0,
                "part": {"type": "output_text", "text": "", "annotations": []},
            }),
        ]

    def feed(self, chunk: dict) -> list[tuple[str, dict]]:
        if self.finished or not isinstance(chunk, dict):
            return []
        if chunk.get("model"):
            self.model_id = chunk["model"]
        if chunk.get("usage"):
            self.usage = chunk["usage"]

        upstream_error = chunk.get("error")
        if upstream_error:
            if isinstance(upstream_error, dict):
                message = upstream_error.get("message", "") or json.dumps(upstream_error, ensure_ascii=False)
                code = upstream_error.get("code") or upstream_error.get("type") or "server_error"
            else:
                message, code = str(upstream_error), "server_error"
            self.error_payload = {"code": code, "message": message}
            return self.fail(message)

        events: list[tuple[str, dict]] = []
        choices = chunk.get("choices") or []
        choice = choices[0] if choices and isinstance(choices[0], dict) else {}
        delta = choice.get("delta") or {}
        text_delta = _responses_text_of(delta.get("content"))
        if choice.get("finish_reason"):
            self.finish_reason = choice["finish_reason"]

        if not self.started:
            events.extend(self._begin_events())

        if text_delta:
            self.text += text_delta
            events.append(self._event("response.output_text.delta", {
                "item_id": self.item_id,
                "output_index": 0,
                "content_index": 0,
                "delta": text_delta,
            }))

        # 工具调用增量：跨 chunk 累积（index -> id/name/arguments）
        for tc in delta.get("tool_calls") or []:
            if not isinstance(tc, dict):
                continue
            idx = tc.get("index", 0)
            acc = self.tool_calls.setdefault(idx, {"id": "", "name": "", "arguments": ""})
            if tc.get("id"):
                acc["id"] = tc["id"]
            fn = tc.get("function") or {}
            if fn.get("name"):
                acc["name"] = fn["name"]
            if fn.get("arguments"):
                acc["arguments"] += fn["arguments"]

        return events

    def _envelope(self, status: str, output: list[dict] | None = None,
                  incomplete_details: dict | None = None) -> dict:
        return response_envelope(
            response_id=self.response_id,
            model_id=self.model_id,
            status=status,
            output=output or [],
            usage=_chat_usage_to_responses(self.usage) if status != "in_progress" else None,
            created_at=self.created_at,
            error=self.error_payload,
            incomplete_details=incomplete_details,
            instructions=self.instructions,
            max_output_tokens=self.max_output_tokens,
        )

    def _final_status(self) -> tuple[str, dict | None]:
        status, incomplete_reason = _FINISH_REASON_TO_STATUS.get(
            self.finish_reason or "stop", ("completed", None)
        )
        if self.error_payload:
            return "failed", None
        return status, ({"reason": incomplete_reason} if incomplete_reason else None)

    def _function_call_items(self) -> list[dict]:
        items = []
        for idx in sorted(self.tool_calls):
            acc = self.tool_calls[idx]
            if not acc.get("name"):
                continue
            items.append({
                "id": f"fc_{idx}_{uuid.uuid4().hex[:12]}",
                "type": "function_call",
                "status": "completed",
                "call_id": acc.get("id") or f"call_{uuid.uuid4().hex[:16]}",
                "name": acc["name"],
                "arguments": acc.get("arguments") or "",
            })
        return items

    def _final_response(self) -> dict:
        status, incomplete_details = self._final_status()
        output: list[dict] = []
        if self.text:
            output.append(self._item("completed"))
        output.extend(self._function_call_items())
        return self._envelope(status, output, incomplete_details)

    def finish(self) -> list[tuple[str, dict]]:
        if self.finished:
            return []
        self.finished = True
        events: list[tuple[str, dict]] = []
        if not self.started:
            events.extend(self._begin_events())
        if self.text:
            events.append(self._event("response.output_text.done", {
                "item_id": self.item_id,
                "output_index": 0,
                "content_index": 0,
                "text": self.text,
            }))
        events.append(self._event("response.content_part.done", {
            "item_id": self.item_id,
            "output_index": 0,
            "content_index": 0,
            "part": {"type": "output_text", "text": self.text, "annotations": []},
        }))
        events.append(self._event("response.output_item.done", {
            "output_index": 0,
            "item": self._item("completed"),
        }))
        # 工具调用：作为额外的 function_call output item 下发
        output_index = 1
        for fc in self._function_call_items():
            events.append(self._event("response.output_item.added", {
                "output_index": output_index,
                "item": fc,
            }))
            events.append(self._event("response.function_call_arguments.done", {
                "item_id": fc["id"],
                "output_index": output_index,
                "name": fc["name"],
                "arguments": fc["arguments"],
            }))
            events.append(self._event("response.output_item.done", {
                "output_index": output_index,
                "item": fc,
            }))
            output_index += 1
        events.append(self._event("response.completed", {"response": self._final_response()}))
        return events

    def fail(self, message: str) -> list[tuple[str, dict]]:
        if not self.error_payload:
            self.error_payload = {"code": "server_error", "message": message}
        self.finished = True
        events: list[tuple[str, dict]] = []
        if not self.started:
            events.extend(self._begin_events())
        events.append(self._event("error", {
            "code": (self.error_payload or {}).get("code", "server_error"),
            "message": message,
            "param": None,
        }))
        events.append(self._event("response.failed", {"response": self._final_response()}))
        return events


def responses_stream_chunk_to_chat(chunk_data: dict, model_id: str, completion_id: str) -> dict | None:
    """Convert one Responses API SSE event into an OpenAI chat chunk (or None)."""
    event_type = chunk_data.get("type", "")
    if event_type == "response.output_text.delta":
        return {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": model_id,
            "choices": [{"index": 0, "delta": {"content": chunk_data.get("delta", "")}, "finish_reason": None}],
        }
    if event_type == "response.completed":
        return {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": model_id,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        }
    return None


# ============================================================
# Inbound client-format endpoints (POST /v1/messages,
# POST /v1beta/models/{model}:generateContent) -> chat body.
# Responses (inbound) -> openai_to_claude_response / openai_to_gemini_response.
# ============================================================

def _claude_content_to_openai(content) -> list[dict]:
    """Claude Messages content (string or block list) -> OpenAI content blocks."""
    if isinstance(content, str):
        return [{"type": "text", "text": content}] if content else []
    blocks = []
    for b in content or []:
        if not isinstance(b, dict):
            continue
        btype = b.get("type", "")
        if btype == "text":
            blocks.append({"type": "text", "text": b.get("text", "")})
        elif btype == "image":
            source = b.get("source") or {}
            if source.get("type") == "base64":
                media = source.get("media_type", "image/png")
                blocks.append({"type": "image_url", "image_url": {
                    "url": f"data:{media};base64,{source.get('data', '')}"}})
            elif source.get("url"):
                blocks.append({"type": "image_url", "image_url": {"url": source["url"]}})
        elif btype == "tool_use":
            blocks.append({"type": "text", "text": json.dumps(
                {"tool_call": b.get("name", ""), "input": b.get("input", {})}, ensure_ascii=False)})
        elif btype == "tool_result":
            inner = b.get("content", "")
            if not isinstance(inner, str):
                inner = "\n".join(
                    c.get("text", "") for c in (inner or []) if isinstance(c, dict))
            blocks.append({"type": "text", "text": f"[tool_result] {inner}"})
        # thinking / redacted_thinking blocks are dropped (not usable upstream)
    return blocks


def _claude_tools_to_chat(tools) -> tuple[list[dict], bool]:
    """Claude tools -> (chat function tools, has_search).

    函数工具正常转换；内置 web_search_* 工具以 has_search 标记透传。
    """
    out = []
    has_search = False
    for t in tools or []:
        if not isinstance(t, dict):
            continue
        ttype = t.get("type", "")
        if _is_search_tool(t):
            has_search = True
            continue
        if not t.get("name"):
            continue
        out.append({"type": "function", "function": {
            "name": t.get("name", ""),
            "description": t.get("description", ""),
            "parameters": t.get("input_schema") or {"type": "object", "properties": {}},
        }})
    return out, has_search


def _claude_tool_choice_to_chat(tool_choice):
    if isinstance(tool_choice, str):
        return tool_choice
    if isinstance(tool_choice, dict):
        ttype = tool_choice.get("type", "auto")
        if ttype == "auto":
            return "auto"
        if ttype == "any":
            return "required"
        if ttype == "tool" and tool_choice.get("name"):
            return {"type": "function", "function": {"name": tool_choice["name"]}}
        if ttype == "none":
            return "none"
    return "auto"


def claude_request_to_chat(body: dict) -> dict:
    """Convert an Anthropic /v1/messages request into a chat/completions body."""
    chat: dict = {"model": body.get("model", "")}
    messages: list[dict] = []

    system = body.get("system")
    if isinstance(system, str):
        if system:
            messages.append({"role": "system", "content": system})
    elif isinstance(system, list):
        text = "\n".join(
            b.get("text", "") for b in system if isinstance(b, dict) and b.get("type") == "text")
        if text:
            messages.append({"role": "system", "content": text})

    for msg in body.get("messages", []):
        role = msg.get("role", "user")
        if role not in ("user", "assistant"):
            role = "user"
        blocks = _claude_content_to_openai(msg.get("content", ""))
        if blocks:
            messages.append({"role": role, "content": blocks})

    chat["messages"] = messages
    for src, dst in (("max_tokens", "max_tokens"), ("temperature", "temperature"),
                     ("top_p", "top_p"), ("top_k", "top_k")):
        if body.get(src) is not None:
            chat[dst] = body[src]
    if body.get("stop_sequences"):
        chat["stop"] = body["stop_sequences"]
    if body.get("stream"):
        chat["stream"] = True

    tools, has_search = _claude_tools_to_chat(body.get("tools"))
    if tools:
        chat["tools"] = tools
    if has_search:
        chat["web_search_options"] = {}
    if body.get("tool_choice") is not None:
        chat["tool_choice"] = _claude_tool_choice_to_chat(body.get("tool_choice"))
    return chat


def _gemini_parts_to_openai(parts) -> list[dict]:
    """Gemini parts -> OpenAI content blocks."""
    blocks = []
    for p in parts or []:
        if not isinstance(p, dict):
            continue
        if p.get("text"):
            blocks.append({"type": "text", "text": p["text"]})
        inline = p.get("inlineData") or p.get("inline_data")
        if inline:
            mime = inline.get("mimeType") or inline.get("mime_type") or "application/octet-stream"
            data = inline.get("data", "")
            if mime.startswith("audio/"):
                blocks.append({"type": "input_audio",
                               "input_audio": {"data": data, "format": mime.split("/", 1)[1]}})
            else:
                blocks.append({"type": "image_url",
                               "image_url": {"url": f"data:{mime};base64,{data}"}})
        file_ref = p.get("fileData") or p.get("file_data")
        if file_ref:
            uri = file_ref.get("fileUri") or file_ref.get("file_uri") or ""
            if uri:
                blocks.append({"type": "image_url", "image_url": {"url": uri}})
    return blocks


def _gemini_tools_to_chat(tools) -> tuple[list[dict], bool]:
    """Gemini tools -> (chat function tools, has_search).

    函数声明正常转换；google_search 内置工具以 has_search 标记透传。
    """
    out = []
    has_search = False
    for t in tools or []:
        if not isinstance(t, dict):
            continue
        if _is_search_tool(t):
            has_search = True
        for fd in t.get("functionDeclarations") or t.get("function_declarations") or []:
            if not fd.get("name"):
                continue
            out.append({"type": "function", "function": {
                "name": fd.get("name", ""),
                "description": fd.get("description", ""),
                "parameters": fd.get("parameters") or {"type": "object", "properties": {}},
            }})
    return out, has_search


def gemini_request_to_chat(body: dict, model: str) -> dict:
    """Convert a Gemini GenerateContent request into a chat/completions body.

    `model` comes from the URL path (`/v1beta/models/{model}:generateContent`).
    """
    chat: dict = {"model": model}
    messages: list[dict] = []

    system_instruction = body.get("systemInstruction") or body.get("system_instruction")
    if system_instruction:
        parts = _gemini_parts_to_openai(system_instruction.get("parts"))
        if parts:
            messages.append({"role": "system", "content": parts})

    for content in body.get("contents", []):
        role = content.get("role", "user")
        parts = _gemini_parts_to_openai(content.get("parts"))
        if not parts:
            continue
        messages.append({"role": "model" if role == "model" else "user", "content": parts})

    chat["messages"] = messages

    gen = body.get("generationConfig") or body.get("generation_config") or {}
    for src, dst in (("temperature", "temperature"), ("topP", "top_p"),
                     ("topK", "top_k"), ("maxOutputTokens", "max_tokens"),
                     ("stopSequences", "stop")):
        if gen.get(src) is not None:
            chat[dst] = gen[src]

    tools, has_search = _gemini_tools_to_chat(body.get("tools"))
    if tools:
        chat["tools"] = tools
    if has_search:
        chat["web_search_options"] = {}
    tool_config = body.get("toolConfig") or body.get("tool_config") or {}
    fcc = tool_config.get("functionCallingConfig") or tool_config.get("function_calling_config") or {}
    mode = fcc.get("mode")
    if mode == "ANY":
        chat["tool_choice"] = "required"
    elif mode == "NONE":
        chat["tool_choice"] = "none"
    elif mode == "AUTO":
        chat["tool_choice"] = "auto"
    return chat

