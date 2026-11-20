# -*- coding: utf-8 -*-
"""ZCode 免费额度卡自动领取（纯 HTTP 协议，移植自 7sen1ot/zcode2api）。

不启动本地 ZCode.exe、不使用 CDP。复用项目内已登录 ZCode 账号的 JWT，
调用官方 marketing/touch + billing/preview + billing/claim 接口领取免费卡。

协议要点（来自 zcode2api 逆向 + 本机实测 200/code:0）：
  - origin: https://zcode.z.ai
  - headers: Authorization Bearer <jwt> + X-ZCode-App-Version + X-Platform:win32
             等客户端指纹；X-Device-Mid 由 JWT sub 派生，保证设备指纹稳定
  - 可领清单: GET /api/v1/marketing/touch -> data.deliveries[].{banner,popup}.buttons[].action
              (type=claim_zcode_plan, args.plan_id)；GET billing/preview -> data.plans[]
  - 领取: POST /api/v1/zcode-plan/billing/claim {"plan_id": <id>}
          code 0 = 成功；1003 = 已领取；含 captcha/risk 码 = 需人工验证
"""
import base64
import hashlib
import json
import urllib.error
import urllib.request
import uuid

ORIGIN = "https://zcode.z.ai"
PROFILE = "zcode"


def _jwt_sub(jwt):
    try:
        payload = jwt.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload))
        return str(data.get("sub") or data.get("user_id") or "")
    except Exception:
        return ""


def _device_mid(jwt):
    name = _jwt_sub(jwt) or jwt
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, "zcode-gw." + name))


def _headers(jwt, mid):
    return {
        "Authorization": "Bearer " + jwt,
        "X-Api-Key": jwt,
        "Content-Type": "application/json",
        "User-Agent": "ZCode/3.18.2",
        "X-Zcode-App-Version": "3.18.2",
        "X-Platform": "win32",
        "X-Client-Language": "zh-CN",
        "X-Client-Timezone": "Asia/Shanghai",
        "X-Os-Category": "windows",
        "X-Release-Channel": "production",
        "Http-Referer": "https://zcode.z.ai",
        "X-Title": "Z Code@electron",
        "X-Zcode-Agent": "glm",
        "X-Device-Mid": mid,
        "Accept": "application/json",
    }


def _call(method, path, jwt, mid, body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(ORIGIN + path, data=data,
                                 headers=_headers(jwt, mid), method=method)
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            raw = resp.read().decode("utf-8", "ignore")
            return resp.status, _parse(raw)
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "ignore")
        return e.code, _parse(raw)
    except Exception as e:
        return -1, {"_error": type(e).__name__ + ": " + str(e)[:160]}


def _parse(raw):
    try:
        return json.loads(raw) if raw else {}
    except Exception:
        return {"_raw": (raw or "")[:300]}


def _obj(v):
    return v if isinstance(v, dict) else {}


def _arr(v):
    return v if isinstance(v, list) else []


def _plan_id(v):
    return str(_obj(v).get("plan_id") or "").strip()


def _extract_plans(preview_json, touch_json):
    """从 preview 与 marketing/touch 提取可领取的 plan_id -> 名称。"""
    plans = {}
    for item in _arr(_obj(_obj(preview_json).get("data")).get("plans")):
        pid = _plan_id(item)
        if pid:
            plans[pid] = str(_obj(item).get("name") or pid)
    for delivery in _arr(_obj(_obj(touch_json).get("data")).get("deliveries")):
        for pos in ("banner", "popup"):
            content = _obj(_obj(delivery).get(pos))
            for btn in _arr(content.get("buttons")):
                action = _obj(_obj(btn).get("action"))
                if str(action.get("type") or "") != "claim_zcode_plan":
                    continue
                pid = _plan_id(_obj(action).get("args"))
                if not pid:
                    continue
                name = pid
                for key in ("background", "hero"):
                    details = _obj(_obj(_obj(content.get(key)).get("args")).get("zcode_plan"))
                    if _plan_id(details) == pid:
                        name = str(details.get("name") or pid)
                        break
                plans.setdefault(pid, name)
    return plans


def _is_verification_required(code):
    text = str(code).upper()
    return any(k in text for k in (
        "CAPTCHA", "VERIFY", "F001", "F014", "F020", "3007", "3012",
        "SECURITY", "RISK", " browser", "ALIYUN", "SDK",
    ))


def _credential_accounts(ctx):
    creds = ctx.credentials.list(reveal=True)
    out = []
    for row in creds:
        if str(row.get("oauth_profile") or "") != PROFILE:
            continue
        jwt = str(row.get("key") or "")
        if not jwt.startswith("eyJ"):
            continue
        out.append({"id": row.get("id"), "alias": row.get("alias") or "",
                    "jwt": jwt, "mid": _device_mid(jwt)})
    return out


def _preview_one(ctx, acc, app_ver):
    jwt, mid = acc["jwt"], acc["mid"]
    touch_code, touch = _call("GET", "/api/v1/marketing/touch", jwt, mid)
    prev_code, prev = _call(
        "GET",
        "/api/v1/zcode-plan/billing/preview?app_version=" + app_ver + "&platform=win32",
        jwt, mid,
    )
    plans = _extract_plans(prev, touch)
    return {
        "account": acc["alias"], "key_id": acc["id"],
        "touch_http": touch_code, "preview_http": prev_code,
        "plans": [{"plan_id": k, "name": v} for k, v in plans.items()],
        "error": (prev.get("_error") or touch.get("_error") or ""),
    }


def _claim_one(ctx, acc, app_ver, plan_id):
    jwt, mid = acc["jwt"], acc["mid"]
    code, body = _call("POST", "/api/v1/zcode-plan/billing/claim", jwt, mid,
                       {"plan_id": plan_id})
    business = str(_obj(body).get("code") if _obj(body).get("code") is not None
                   else _obj(_obj(body).get("error")).get("code") or "")
    msg = str(_obj(body).get("msg") or _obj(_obj(body).get("data")).get("message") or "")
    ok = (code == 200 and str(_obj(body).get("code")) == "0")
    already = "1003" in business or "already" in msg.lower() or "已领取" in msg or "已领" in msg
    need_verify = _is_verification_required(business) or _is_verification_required(msg)
    if ok:
        status = "claimed"
    elif already:
        status = "already"
    elif need_verify:
        status = "verification_required"
    else:
        status = "failed"
    return {
        "account": acc["alias"], "key_id": acc["id"], "plan_id": plan_id,
        "ok": ok or already, "status": status, "http": code,
        "code": business, "message": msg[:200],
    }


def run(ctx):
    action = str(ctx.params.get("action", "") or ctx.settings.get("action", "status") or "status")
    app_ver = str(ctx.params.get("app_version", "") or ctx.settings.get("app_version", "3.18.2") or "3.18.2")
    key_id = ctx.params.get("key_id")

    try:
        accounts = _credential_accounts(ctx)
    except PermissionError:
        return {"ok": False, "result": "NO_PERMISSION",
                "report": "插件缺少 credentials.read 权限，无法读取 ZCode 账号 JWT"}
    if not accounts:
        return {"ok": False, "result": "NO_ACCOUNT",
                "report": "未找到已登录的 ZCode 账号（请先在账号页授权 ZCode）"}

    if key_id is not None:
        accounts = [a for a in accounts if str(a["id"]) == str(key_id)]
        if not accounts:
            return {"ok": False, "result": "ACCOUNT_NOT_FOUND",
                    "report": "指定的 ZCode 账号不存在或未登录 (key_id=%s)" % key_id}

    ctx.log("ZCode 领卡 start: action=%s, accounts=%d, app_version=%s" % (
        action, len(accounts), app_ver))

    if action == "status":
        previews = [_preview_one(ctx, a, app_ver) for a in accounts]
        total = sum(len(p["plans"]) for p in previews)
        parts = ["账号 %d 个，当前可领卡 %d 张" % (len(accounts), total)]
        for p in previews:
            if p["error"]:
                parts.append("%s: 查询失败 %s" % (p["account"], p["error"]))
            elif p["plans"]:
                names = "、".join(x["name"] for x in p["plans"][:4])
                parts.append("%s: %d 张（%s）" % (p["account"], len(p["plans"]), names))
            else:
                parts.append("%s: 暂无可领活动" % p["account"])
        return {"ok": True, "result": "STATUS", "accounts": previews,
                "report": "；".join(parts)}

    if action != "claim":
        return {"ok": False, "result": "BAD_ACTION", "report": "未知操作: %s" % action}

    results = []
    claimed = 0
    already = 0
    need_verify = 0
    for acc in accounts:
        previews = [_preview_one(ctx, acc, app_ver)]
        plans = previews[0]["plans"]
        if previews[0]["error"]:
            results.append({"account": acc["alias"], "ok": False, "status": "error",
                            "message": previews[0]["error"]})
            continue
        if not plans:
            results.append({"account": acc["alias"], "ok": True, "status": "no_cards",
                            "message": "暂无可领活动"})
            continue
        for p in plans:
            r = _claim_one(ctx, acc, app_ver, p["plan_id"])
            r["plan_name"] = p["name"]
            results.append(r)
            if r["status"] == "claimed":
                claimed += 1
            elif r["status"] == "already":
                already += 1
            elif r["status"] == "verification_required":
                need_verify += 1

    parts = ["领取完成：成功 %d，已领过 %d，需验证 %d，账号 %d 个" % (
        claimed, already, need_verify, len(accounts))]
    for r in results:
        if r["status"] == "claimed":
            parts.append("%s《%s》领取成功" % (r["account"], r.get("plan_name", r["plan_id"])))
        elif r["status"] == "verification_required":
            parts.append("%s《%s》需完成安全验证后领取（%s）" % (
                r["account"], r.get("plan_name", r["plan_id"]), r.get("code") or r.get("message")))
        elif r["status"] == "failed":
            parts.append("%s《%s》领取失败：%s" % (
                r["account"], r.get("plan_name", r["plan_id"]), r.get("message") or r.get("code")))
    return {"ok": True, "result": "CLAIM_DONE", "claimed": claimed,
            "already": already, "need_verification": need_verify,
            "results": results, "report": "；".join(parts)}
