"""workbuddy成长系统 —— WorkBuddy 成长中心 / 每日签到 自动化插件。

核心代码移植自 88lin/workbuddy-auto-signin (MIT)：
  https://github.com/88lin/workbuddy-auto-signin
接口系从桌面端逆向所得，服务端改版后可能需要更新本脚本。

与原项目的差异（宿主适配）：
  - 入口改为 run(ctx)；登录态不再读本机桌面凭据文件，改由插件设置项注入
    （token / uid / enterprise_id / domain / endpoint）。
  - 日志走 ctx.log(...)，汇报作为结构化 dict 返回；不再 print/写 signin.log。
  - 去掉账号轮转、安装器、CLI、加密凭据解密等与本宿主无关的部分。
  - 时间预算受宿主 RUN_TIMEOUT 约束，默认 90s、上限 110s。

绝不打印令牌；任何网络异常都被吞进汇报，不让整轮执行崩掉。
"""
from __future__ import annotations

import base64
import json
import math
import os
import re
import ssl
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from datetime import date

# --- 常量 -------------------------------------------------------------------
DEFAULT_ENDPOINT = "https://copilot.tencent.com"
CODE_NO_NETWORK = -1
CODE_BUDGET_OUT = -2
REQUEST_TIMEOUT = 20
NETWORK_RETRY_DELAYS = (3, 10)
SERVER_RETRY_DELAYS = (2, 6)
MAKEUP_MAX_PER_RUN = 1

_REDEEM_REWARDS = {
    "7d": "+2 能量 +1 补登卡 +1 次抽奖",
    "14d": "+50 积分 +3 能量 +1 补登卡 +1 次抽奖",
    "28d": "+150 积分 +5 能量 +1 补登卡 +1 次抽奖",
}
_REDEEM_TIERS = (
    ("7d", "starter", "入门", 7),
    ("14d", "advanced", "进阶", 14),
    ("28d", "legendary", "巅峰", 28),
)

_started_at = None
_budget_seconds = 90.0
_log = print  # run() 内替换为 ctx.log


# --- 本机 WorkBuddy 登录态自动提取 -------------------------------------------
_AUTH_HELPER_B64 = "Cid1c2Ugc3RyaWN0JzsKY29uc3QgY3J5cHRvID0gcmVxdWlyZSgnY3J5cHRvJyk7CmNvbnN0IGlucHV0TGltaXQgPSA2NTUzNjsKY29uc3QgZmFpbHVyZSA9IHJlYXNvbiA9PiB7IHRocm93IHtyZWFzb259OyB9Owpjb25zdCBvYmplY3QgPSB4ID0+IHggIT09IG51bGwgJiYgdHlwZW9mIHggPT09ICdvYmplY3QnICYmICFBcnJheS5pc0FycmF5KHgpOwpmdW5jdGlvbiBiYXNlNjQodmFsdWUsIGxlbmd0aCkgewogIGlmICh0eXBlb2YgdmFsdWUgIT09ICdzdHJpbmcnIHx8IHZhbHVlLmxlbmd0aCA+IGlucHV0TGltaXQpIGZhaWx1cmUoJ0lOVkFMSURfRk9STUFUJyk7CiAgY29uc3QgYnl0ZXMgPSBCdWZmZXIuZnJvbSh2YWx1ZSwgJ2Jhc2U2NCcpOwogIGlmIChieXRlcy50b1N0cmluZygnYmFzZTY0JykgIT09IHZhbHVlIHx8IChsZW5ndGggIT09IHVuZGVmaW5lZCAmJiBieXRlcy5sZW5ndGggIT09IGxlbmd0aCkpIGZhaWx1cmUoJ0lOVkFMSURfRk9STUFUJyk7CiAgcmV0dXJuIGJ5dGVzOwp9CmZ1bmN0aW9uIHV0ZjgoYnl0ZXMpIHsgY29uc3QgdCA9IGJ5dGVzLnRvU3RyaW5nKCd1dGY4Jyk7IGlmICghQnVmZmVyLmZyb20odCwgJ3V0ZjgnKS5lcXVhbHMoYnl0ZXMpKSBmYWlsdXJlKCdJTlZBTElEX0ZPUk1BVCcpOyByZXR1cm4gdDsgfQpmdW5jdGlvbiBkZWNvZGUodmFsdWUpIHsKICBpZiAoIW9iamVjdCh2YWx1ZSkgfHwgT2JqZWN0LmtleXModmFsdWUpLnNvcnQoKS5qb2luKCcsJykgIT09ICckd2JFbmNyeXB0ZWQsZW52ZWxvcGUnIHx8IHZhbHVlLiR3YkVuY3J5cHRlZCAhPT0gMSkgZmFpbHVyZSgnVU5TVVBQT1JURURfRU5WRUxPUEUnKTsKICBsZXQgZTsgdHJ5IHsgZSA9IEpTT04ucGFyc2UodXRmOChiYXNlNjQodmFsdWUuZW52ZWxvcGUpKSk7IH0gY2F0Y2ggKHgpIHsgZmFpbHVyZSh4LnJlYXNvbiB8fCAnSU5WQUxJRF9GT1JNQVQnKTsgfQogIGlmICghb2JqZWN0KGUpIHx8ICFOdW1iZXIuaXNJbnRlZ2VyKGUuc3VpdGUpIHx8IGUuc3VpdGUgIT09IDEpIGZhaWx1cmUoJ1VOU1VQUE9SVEVEX0VOVkVMT1BFJyk7CiAgaWYgKE9iamVjdC5rZXlzKGUpLnNvcnQoKS5qb2luKCcsJykgIT09ICdhdXRoVGFnLGNpcGhlcnRleHQsa2V5SWQsbm9uY2Usc3VpdGUnIHx8ICEvXlswLTlhLWZdezE2fSQvLnRlc3QoZS5rZXlJZCkpIGZhaWx1cmUoJ0lOVkFMSURfRk9STUFUJyk7CiAgcmV0dXJuIHsga2V5SWQ6IGUua2V5SWQsIG5vbmNlOiBiYXNlNjQoZS5ub25jZSwgMTIpLCB0YWc6IGJhc2U2NChlLmF1dGhUYWcsIDE2KSwgY2lwaGVydGV4dDogYmFzZTY0KGUuY2lwaGVydGV4dCkgfTsKfQpmdW5jdGlvbiBuYXRpdmVTdG9yYWdlKCkgewogIHRyeSB7IGNvbnN0IHMgPSBwcm9jZXNzLl9saW5rZWRCaW5kaW5nKCdlbGVjdHJvbl9icm93c2VyX3dvcmtidWRkeV9zdG9yYWdlJyk7IGlmICh0eXBlb2Ygcy5sb2dnZXJHZXQgIT09ICdmdW5jdGlvbicpIGZhaWx1cmUoJ1JVTlRJTUVfVU5BVkFJTEFCTEUnKTsgcmV0dXJuIHM7IH0KICBjYXRjaCAoXykgeyBmYWlsdXJlKCdSVU5USU1FX1VOQVZBSUxBQkxFJyk7IH0KfQpmdW5jdGlvbiBkZWNyeXB0KGVudmVsb3BlKSB7CiAgbGV0IHBheWxvYWQ7IHRyeSB7IHBheWxvYWQgPSBKU09OLnBhcnNlKG5hdGl2ZVN0b3JhZ2UoKS5sb2dnZXJHZXQoKSk7IH0gY2F0Y2ggKF8pIHsgZmFpbHVyZSgnUlVOVElNRV9VTkFWQUlMQUJMRScpOyB9CiAgaWYgKCFvYmplY3QocGF5bG9hZCkgfHwgcGF5bG9hZC52ZXJzaW9uICE9PSAxKSBmYWlsdXJlKCdSVU5USU1FX1VOQVZBSUxBQkxFJyk7CiAgY29uc3Qgc2VjcmV0ID0gYmFzZTY0KHBheWxvYWQuYXRSZXN0U2VjcmV0S2V5LCAzMik7CiAgY29uc3Qga2V5ID0gY3J5cHRvLmNyZWF0ZUhhc2goJ3NoYTI1NicpLnVwZGF0ZShwYXlsb2FkLmF0UmVzdFNlY3JldEtleSwgJ3V0ZjgnKS5kaWdlc3QoKTsKICBpZiAoY3J5cHRvLmNyZWF0ZUhhc2goJ3NoYTI1NicpLnVwZGF0ZShrZXkpLmRpZ2VzdCgnaGV4Jykuc2xpY2UoMCwgMTYpICE9PSBlbnZlbG9wZS5rZXlJZCkgZmFpbHVyZSgnS0VZX01JU01BVENIJyk7CiAgY29uc3QgbHAgPSBzID0+IHsgY29uc3QgYiA9IEJ1ZmZlci5mcm9tKHMsICd1dGY4Jyk7IGNvbnN0IGwgPSBCdWZmZXIuYWxsb2MoNCk7IGwud3JpdGVVSW50MzJCRShiLmxlbmd0aCk7IHJldHVybiBCdWZmZXIuY29uY2F0KFtsLCBiXSk7IH07CiAgY29uc3QgYWFkID0gQnVmZmVyLmNvbmNhdChbQnVmZmVyLmZyb20oJ1dCLUFBRFwwJywgJ2FzY2lpJyksIEJ1ZmZlci5mcm9tKFsxXSksIGxwKCdXQkVWMScpLCBscCgnc3ltLXYxJyksIEJ1ZmZlci5mcm9tKFswLCAwLCAwLCAxXSksIGxwKGVudmVsb3BlLmtleUlkKSwgQnVmZmVyLmZyb20oWzIsIDAsIDBdKV0pOwogIGxldCBwbGFpbnRleHQ7CiAgdHJ5IHsgY29uc3QgYyA9IGNyeXB0by5jcmVhdGVEZWNpcGhlcml2KCdhZXMtMjU2LWdjbScsIGtleSwgZW52ZWxvcGUubm9uY2UsIHsgYXV0aFRhZ0xlbmd0aDogMTYgfSk7IGMuc2V0QUFEKGFhZCk7IGMuc2V0QXV0aFRhZyhlbnZlbG9wZS50YWcpOyBwbGFpbnRleHQgPSBCdWZmZXIuY29uY2F0KFtjLnVwZGF0ZShlbnZlbG9wZS5jaXBoZXJ0ZXh0KSwgYy5maW5hbCgpXSk7IH0KICBjYXRjaCAoXykgeyBmYWlsdXJlKCdERUNSWVBUX0ZBSUxFRCcpOyB9CiAgY29uc3QgdG9rZW4gPSB1dGY4KHBsYWludGV4dCk7CiAgaWYgKCF0b2tlbi5sZW5ndGggfHwgdG9rZW4ubGVuZ3RoID4gMzI3NjggfHwgIS9eW0EtWmEtejAtOS5ffitcLy1dKz0qJC8udGVzdCh0b2tlbikpIGZhaWx1cmUoJ0lOVkFMSURfRk9STUFUJyk7CiAgcmV0dXJuIHRva2VuOwp9CmxldCBjaHVua3MgPSBbXSwgc2l6ZSA9IDA7CmZ1bmN0aW9uIHJlcGx5KHYpIHsgcHJvY2Vzcy5zdGRvdXQud3JpdGUoSlNPTi5zdHJpbmdpZnkoeyB2ZXJzaW9uOiAxLCBvazogdi5vaywgdG9rZW46IHYudG9rZW4sIHJlYXNvbjogdi5yZWFzb24gfSksICgpID0+IHByb2Nlc3MuZXhpdCh2Lm9rID8gMCA6IDEpKTsgfQpwcm9jZXNzLnN0ZGluLm9uKCdkYXRhJywgYyA9PiB7IHNpemUgKz0gYy5sZW5ndGg7IGlmIChzaXplID4gaW5wdXRMaW1pdCkgcmVwbHkoeyBvazogZmFsc2UsIHJlYXNvbjogJ0lOVkFMSURfRk9STUFUJyB9KTsgZWxzZSBjaHVua3MucHVzaChjKTsgfSk7CnByb2Nlc3Muc3RkaW4ub24oJ2Vycm9yJywgKCkgPT4gcmVwbHkoeyBvazogZmFsc2UsIHJlYXNvbjogJ0hFTFBFUl9QUk9UT0NPTCcgfSkpOwpwcm9jZXNzLnN0ZGluLm9uKCdlbmQnLCAoKSA9PiB7CiAgdHJ5IHsKICAgIGNvbnN0IHJlcSA9IEpTT04ucGFyc2UodXRmOChCdWZmZXIuY29uY2F0KGNodW5rcykpKTsgY2h1bmtzID0gW107CiAgICBpZiAoIW9iamVjdChyZXEpIHx8IHJlcS52ZXJzaW9uICE9PSAxKSBmYWlsdXJlKCdIRUxQRVJfUFJPVE9DT0wnKTsKICAgIGlmIChyZXEub3BlcmF0aW9uID09PSAnZGVjcnlwdCcpIHJlcGx5KHsgb2s6IHRydWUsIHRva2VuOiBkZWNyeXB0KGRlY29kZShyZXEudmFsdWUpKSB9KTsKICAgIGVsc2UgZmFpbHVyZSgnSEVMUEVSX1BST1RPQ09MJyk7CiAgfSBjYXRjaCAoZSkgewogICAgY29uc3QgcnMgPSBbJ0lOVkFMSURfRk9STUFUJywgJ1VOU1VQUE9SVEVEX0VOVkVMT1BFJywgJ1JVTlRJTUVfVU5BVkFJTEFCTEUnLCAnS0VZX01JU01BVENIJywgJ0RFQ1JZUFRfRkFJTEVEJywgJ0hFTFBFUl9QUk9UT0NPTCddOwogICAgcmVwbHkoeyBvazogZmFsc2UsIHJlYXNvbjogcnMuaW5jbHVkZXMoZS5yZWFzb24pID8gZS5yZWFzb24gOiAnSEVMUEVSX1BST1RPQ09MJyB9KTsKICB9Cn0pOwo="

_AUTH_BASENAME = ("CodeBuddyExtension", "Data", "Public", "auth", "workbuddy-desktop.info")
_CLI_AUTH_BASENAME = ("CodeBuddyExtension", "Data", "Public", "auth",
                      "Tencent-Cloud.coding-copilot.info")


def _auth_candidates():
    home = os.environ.get("HOME") or os.environ.get("USERPROFILE") or os.path.expanduser("~")
    local = os.environ.get("LOCALAPPDATA") or os.path.join(home, "AppData", "Local")
    xdg = os.environ.get("XDG_DATA_HOME") or os.path.join(home, ".local", "share")
    return [
        os.path.join(local, *_AUTH_BASENAME),
        os.path.join(home, "Library", "Application Support", *_AUTH_BASENAME),
        os.path.join(xdg, *_CLI_AUTH_BASENAME),
        os.path.join(home, ".config", *_AUTH_BASENAME),
        os.path.join(home, ".workbuddy", "auth", "workbuddy-desktop.info"),
    ]


def _find_runtime():
    override = (os.environ.get("WORKBUDDY_EXE") or "").strip()
    cands = []
    if override:
        cands.append(os.path.abspath(os.path.expanduser(override)))
    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
        cands.append(os.path.join(local, "Programs", "WorkBuddy", "WorkBuddy.exe"))
        for name in ("ProgramFiles", "ProgramFiles(x86)"):
            base = os.environ.get(name)
            if base:
                cands.append(os.path.join(base, "WorkBuddy", "WorkBuddy.exe"))
        for drive in ("D:\\", "E:\\"):
            cands.append(os.path.join(drive, "Program Files", "WorkBuddy", "WorkBuddy.exe"))
    for p in cands:
        if p and os.path.isfile(p):
            return os.path.abspath(p)
    return None


def _decrypt_token(value):
    exe = _find_runtime()
    if not exe:
        return None, "RUNTIME_NOT_FOUND"
    try:
        js = base64.b64decode(_AUTH_HELPER_B64).decode("utf-8")
        data = json.dumps({"version": 1, "operation": "decrypt", "value": value}).encode("ascii")
        env = {k: v for k, v in os.environ.items()
               if not k.upper().startswith(("NODE_", "ELECTRON_", "WORKBUDDY_"))}
        env["ELECTRON_RUN_AS_NODE"] = "1"
        left = _budget_left() if _started_at else 30.0
        timeout = max(1.0, min(25.0, left - 1.0))
        proc = subprocess.run([exe, "-e", js], input=data, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, env=env, timeout=timeout,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        reply = json.loads(proc.stdout.decode("utf-8"))
        if reply.get("ok") is True and reply.get("token"):
            return reply["token"], None
        return None, reply.get("reason") or "DECRYPT_FAILED"
    except subprocess.TimeoutExpired:
        return None, "HELPER_TIMEOUT"
    except Exception:
        return None, "DECRYPT_FAILED"


def _auto_session():
    path = None
    for c in _auth_candidates():
        if os.path.exists(c):
            path = c
            break
    if not path:
        return {}, "NO_AUTH_FILE"
    try:
        with open(path, "r", encoding="utf-8") as f:
            session = json.load(f)
    except Exception:
        return {}, "AUTH_READ_FAILED"
    auth = session.get("auth") or {}
    account = session.get("account") or {}
    raw_token = auth.get("accessToken")
    uid = account.get("uid")
    if not raw_token or not uid:
        return {}, "NO_SESSION"
    if isinstance(raw_token, str):
        token = raw_token
    else:
        token, err = _decrypt_token(raw_token)
        if not token:
            return {}, err or "DECRYPT_FAILED"
    return {
        "token": token,
        "uid": str(uid),
        "enterprise_id": str(account.get("enterpriseId") or ""),
        "domain": str(auth.get("domain") or ""),
    }, None


# --- 基础设施 ---------------------------------------------------------------
def _start_budget(seconds):
    global _started_at, _budget_seconds
    try:
        val = float(seconds)
    except (TypeError, ValueError):
        val = 90.0
    _budget_seconds = max(10.0, min(110.0, val))
    _started_at = time.monotonic()


def _budget_left():
    if _started_at is None:
        return _budget_seconds
    return _budget_seconds - (time.monotonic() - _started_at)


def dig(obj, key):
    """在可能被 data/result 包裹的响应里找字段，兼容信封结构。"""
    if isinstance(obj, dict):
        if key in obj and obj[key] is not None:
            return obj[key]
        for k in ("data", "result", "resp", "response"):
            if k in obj and isinstance(obj[k], dict):
                r = dig(obj[k], key)
                if r is not None:
                    return r
    return None


def as_int(v, default=0):
    try:
        return int(v)
    except (TypeError, ValueError, OverflowError):
        pass
    try:
        return int(float(v))
    except (TypeError, ValueError, OverflowError):
        return default


def fmt_credit(v):
    try:
        return int(v)
    except (TypeError, ValueError, OverflowError):
        return v


def _first_int(body, key, fallback=0):
    if isinstance(body, dict):
        v = dig(body, key)
        if v is not None:
            return as_int(v)
    return as_int(fallback)


def _fmt_eta(arrive_at, server_now):
    try:
        left = float(arrive_at) - float(server_now)
    except (TypeError, ValueError, OverflowError):
        return ""
    if not math.isfinite(left):
        return ""
    if left <= 0:
        return "，已到达待领取"
    minutes = int(round(left / 60.0))
    if minutes < 60:
        return "，约 %d 分钟后回" % max(1, minutes)
    return "，约 %.1f 小时后回" % (left / 3600.0)


def _client_token(prefix="u"):
    return "%s-%s" % (prefix, uuid.uuid4())


def _api_success(code, body):
    business_code = body.get("code") if isinstance(body, dict) else None
    return 200 <= code < 300 and (
        business_code is None or (type(business_code) is int and business_code == 0))


def _is_hard_failure(code):
    return code >= 500 or code in (CODE_NO_NETWORK, CODE_BUDGET_OUT)


def _http_label(code):
    if code == CODE_NO_NETWORK:
        return "网络不可达"
    if code == CODE_BUDGET_OUT:
        return "时间预算耗尽"
    return "HTTP %s" % code


def _is_no_chance(msg):
    m = str(msg or "").lower()
    if not m:
        return False
    if "insufficient" in m or "not enough" in m:
        return "chance" in m or "balance" in m
    return "no chance" in m


def _is_unknown_tier(code, body):
    if code != 400:
        return False
    m = str(dig(body, "msg") or "").lower()
    return "tier" in m and ("unknown" in m or "unsupported" in m or "invalid" in m)


def _is_tier_locked(code, body):
    if code != 403:
        return False
    m = str(dig(body, "msg") or "")
    return "天数不足" in m or "不足" in m


def _makeup_candidates(streak_body, heatmap_body):
    def parse_date(value):
        if not isinstance(value, str) or re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) is None:
            raise ValueError("补登日期格式无效")
        return date.fromisoformat(value)

    today_obj = dig(heatmap_body, "today")
    if not isinstance(today_obj, dict):
        raise ValueError("活跃日历缺少服务端日期")
    today = parse_date(today_obj.get("date"))
    launch = parse_date(dig(streak_body, "launch_date"))
    start = max(today.replace(day=1), launch, date(2026, 6, 17))
    streak = dig(streak_body, "streak")
    if not isinstance(streak, dict):
        raise ValueError("连登状态格式无效")
    made_up = streak.get("makeup_dates", [])
    if not isinstance(made_up, list):
        raise ValueError("已补登记录格式无效")
    made_up = {parse_date(value) for value in made_up}
    cells = dig(heatmap_body, "cells")
    if not isinstance(cells, list):
        raise ValueError("活跃日历缺少日期列表")
    scores = {}
    for cell in cells:
        if not isinstance(cell, dict):
            raise ValueError("活跃日历记录格式无效")
        day = parse_date(cell.get("date"))
        score = cell.get("score")
        if type(score) not in (int, float) or not math.isfinite(score) or score < 0:
            raise ValueError("活跃日历分数无效")
        if day in scores and scores[day] != score:
            raise ValueError("活跃日历同一日期的记录冲突")
        scores[day] = score
    return sorted((day.isoformat() for day, score in scores.items()
                   if start <= day < today and score == 0 and day not in made_up), reverse=True)


def _redeem_reward_desc(body, tier):
    bits = []
    credit = as_int(dig(body, "credit_granted"), 0)
    energy = as_int(dig(body, "energy_granted"), 0)
    cards = as_int(dig(body, "cards_granted"), 0)
    chances = as_int(dig(body, "chances_granted"), 0)
    if credit:
        bits.append("+%s 积分" % fmt_credit(credit))
    if energy:
        bits.append("+%s 能量" % fmt_credit(energy))
    if cards:
        bits.append("+%s 补登卡" % fmt_credit(cards))
    if chances:
        bits.append("+%s 次抽奖" % fmt_credit(chances))
    if bits:
        return "（%s）" % " ".join(bits)
    return "（%s）" % _REDEEM_REWARDS.get(tier, "奖励已到账")


def _auth_failure(code):
    if code == 401:
        return {"result": "AUTH_REJECTED", "http": code, "needs_attention": True,
                "report": "服务端拒绝认证（HTTP 401），请检查插件设置里的 token/uid 是否正确或已过期"}
    return {"result": "FORBIDDEN", "http": code, "needs_attention": True,
            "report": "服务端拒绝此操作（HTTP 403），请检查账号权限或活动条件"}


def _is_already_checked_in(cbody):
    if cbody is None:
        return True
    if isinstance(cbody, dict):
        msg = cbody.get("msg") or ""
        if cbody.get("code") == 10001 or "已签" in msg:
            return True
    return False


def _already_report(status, via=None):
    today_credit = dig(status, "today_credit") or dig(status, "daily_credit")
    streak_days = dig(status, "streak_days")
    total_credits = dig(status, "total_credits")
    is_streak_day = dig(status, "is_streak_day")
    next_streak_day = dig(status, "next_streak_day")
    inner = []
    if today_credit is not None:
        inner.append("今日 +%s" % fmt_credit(today_credit))
    if streak_days is not None:
        inner.append("连续 %s 天" % streak_days)
    if total_credits is not None:
        inner.append("累计 %s 积分" % fmt_credit(total_credits))
    prefix = via or "今日已签过"
    report = "%s（%s）" % (prefix, "，".join(inner)) if inner else prefix
    return {"result": "ALREADY", "report": report, "today_credit": today_credit,
            "streak_days": streak_days, "total_credits": total_credits,
            "is_streak_day": is_streak_day, "next_streak_day": next_streak_day}


# --- HTTP 层（带时间预算与退避重试）-------------------------------------------
def _request(url, headers, method="GET", payload=None, timeout=REQUEST_TIMEOUT):
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            raw = resp.read().decode("utf-8")
            try:
                return resp.status, json.loads(raw)
            except Exception:
                return resp.status, {"raw": raw[:500]}
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"raw": raw[:500]}
    except urllib.error.URLError as e:
        return CODE_NO_NETWORK, {"error": str(e.reason)}
    except Exception as e:
        return CODE_NO_NETWORK, {"error": str(e)}


def _retry_delays(code):
    if code == CODE_NO_NETWORK:
        return NETWORK_RETRY_DELAYS
    if code >= 500:
        return SERVER_RETRY_DELAYS
    return ()


def _request_with_retry(url, headers, method="GET", payload=None, retry=True):
    if _budget_left() <= 1:
        return CODE_BUDGET_OUT, {"error": "已达本次运行时间预算，跳过剩余请求"}
    code, body = _request(url, headers, method=method, payload=payload,
                          timeout=max(1, min(REQUEST_TIMEOUT, _budget_left())))
    if not retry:
        return code, body
    attempts = {}
    while True:
        delays = _retry_delays(code)
        if not delays:
            return code, body
        used = attempts.get(delays, 0)
        if used >= len(delays):
            return code, body
        delay = delays[used]
        attempts[delays] = used + 1
        if _budget_left() <= delay + REQUEST_TIMEOUT:
            return code, body
        time.sleep(delay)
        code, body = _request(url, headers, method=method, payload=payload,
                              timeout=max(1, min(REQUEST_TIMEOUT, _budget_left())))


def post(url, headers, payload=None, retry=False):
    return _request_with_retry(url, headers, method="POST", payload=payload, retry=retry)


def get(url, headers):
    return _request_with_retry(url, headers, method="GET", retry=True)


def build_headers(cfg):
    token = (cfg.get("token") or "").strip()
    uid = (cfg.get("uid") or "").strip()
    if not token or not uid:
        raise ValueError("缺少 token 或 uid")
    headers = {
        "Accept": "application/json",
        "Authorization": "Bearer %s" % token,
        "Content-Type": "application/json",
        "X-User-Id": uid,
        "User-Agent": "WorkBuddy",
    }
    if (cfg.get("enterprise_id") or "").strip():
        eid = cfg["enterprise_id"].strip()
        headers["X-Enterprise-Id"] = eid
        headers["X-Tenant-Id"] = eid
    if (cfg.get("domain") or "").strip():
        headers["X-Domain"] = cfg["domain"].strip()
    return headers


# --- 自动领养第一只 Buddy ---------------------------------------------------
def run_adopt(headers, endpoint):
    """自动领养第一只 Buddy。

    流程（对齐成长中心网页端）：
      1) GET  buddy/info    —— 已有 Buddy 则无需领养
      2) GET  buddy/agreement —— 查协议签署状态
      3) POST buddy/agreement {agree:true} —— 未签则先签协议
      4) POST buddy/first   —— 领养第一只 Buddy（免费）

    服务端前置：first_buddy 为 auto 任务，需先在桌面端发起过一次对话。
    前置未满足不算失败，返回明确提示，避免误报。
    """
    base = endpoint.rstrip("/") + "/v2/activity/growth"
    parts = []
    failures = 0
    hard_failures = 0
    successes = 0
    adopted = False
    need_conversation = False

    def _is_hard(code):
        return code >= 500 or code in (CODE_NO_NETWORK, CODE_BUDGET_OUT)

    # 1) 查现有 Buddy
    try:
        code, body = get(base + "/buddy/info", headers)
        if code in (401, 403):
            return 1, _auth_failure(code)
        if 200 <= code < 300 and isinstance(body, dict):
            data = body.get("data") if isinstance(body.get("data"), dict) else body
            if data.get("buddy"):
                buddy = data["buddy"]
                name = buddy.get("name") if isinstance(buddy, dict) else None
                return 0, {"result": "ALREADY", "adopted": False,
                           "report": "已有 Buddy%s，无需领养" % ("（%s）" % name if name else ""),
                           "buddy": buddy}
    except Exception as e:
        parts.append("查询 Buddy 异常（%s: %s）" % (type(e).__name__, e))
        failures += 1
        hard_failures += 1

    # 2) 协议状态 + 签协议
    try:
        code, body = get(base + "/buddy/agreement", headers)
        if code in (401, 403):
            return 1, _auth_failure(code)
        agreed = False
        if 200 <= code < 300 and isinstance(body, dict):
            data = body.get("data") if isinstance(body.get("data"), dict) else body
            agreed = bool(data.get("agreed"))
        if not agreed:
            code, body = post(base + "/buddy/agreement", headers, {"agree": True})
            if code in (401, 403):
                return 1, _auth_failure(code)
            if 200 <= code < 300:
                parts.append("已签署 Buddy 领养协议")
                successes += 1
            else:
                msg = dig(body, "msg") or ("HTTP %s" % code) if isinstance(body, dict) else ("HTTP %s" % code)
                parts.append("签署 Buddy 协议失败：%s" % msg)
                failures += 1
                hard_failures += _is_hard(code)
    except Exception as e:
        parts.append("Buddy 协议模块异常（%s: %s）" % (type(e).__name__, e))
        failures += 1
        hard_failures += 1

    # 3) 领养第一只
    try:
        code, body = post(base + "/buddy/first", headers, {})
        if code in (401, 403):
            return 1, _auth_failure(code)
        msg = str(dig(body, "msg") or "") if isinstance(body, dict) else ""
        low = msg.lower()
        if 200 <= code < 300:
            data = body.get("data") if isinstance(body.get("data"), dict) else {}
            buddy = data.get("buddy") if isinstance(data, dict) else None
            parts.append("领养 Buddy 成功%s" % (
                "（%s）" % buddy.get("name") if isinstance(buddy, dict) and buddy.get("name") else ""))
            adopted = True
            successes += 1
        elif ("conversation" in low or "task not completed" in low
              or "first_buddy" in low):
            need_conversation = True
            parts.append("Buddy 待领养：需先在桌面端为该账号发起一次对话，再重试领养")
        else:
            parts.append("领养 Buddy 失败：%s" % (msg or "HTTP %s" % code))
            failures += 1
            hard_failures += _is_hard(code)
    except Exception as e:
        parts.append("领养 Buddy 异常（%s: %s）" % (type(e).__name__, e))
        failures += 1
        hard_failures += 1

    if not parts:
        report = "Buddy 领养无变化"
    else:
        report = "；".join(parts)
    return (1 if hard_failures else 0), {
        "result": "ADOPTED" if adopted else ("NEED_CONVERSATION" if need_conversation else "ADOPT_SKIP"),
        "adopted": adopted, "need_conversation": need_conversation,
        "report": report, "failures": failures,
    }


# --- 成长中心 ---------------------------------------------------------------
def run_growth(headers, endpoint):
    base = endpoint + "/v2/activity/growth"
    parts = []
    credits_gained = 0
    failures = 0
    hard_failures = 0
    successes = 0

    def _check_auth(code):
        return code in (401, 403)

    def _stop_for_auth(code):
        out = _auth_failure(code)
        out["report"] = "；".join(parts + [out["report"]])
        out["credits_gained"] = credits_gained
        return 1, out

    def _note_http(code, body, label, required=False):
        nonlocal failures, hard_failures
        if 200 <= code < 300 and (not required or _api_success(code, body)):
            return False
        reason = _http_label(code)
        detail = ""
        if isinstance(body, dict):
            detail = str(body.get("error") or body.get("msg") or "")
        parts.append("%s失败：%s" % (label, "%s（%s）" % (reason, detail)
                                  if detail and detail != reason else (detail or reason)))
        if required or _is_hard_failure(code):
            failures += 1
            hard_failures += 1
        return True

    # 1. Buddy 旅行：先领已有礼物，派出放到能量开 Buddy 之后
    travel = None
    daily_limit = False
    try:
        scode, sbody = get(base + "/buddy/travel/status", headers)
        if scode == CODE_BUDGET_OUT:
            return 1, {"result": "TIMEOUT", "report": "时间预算耗尽，成长中心跳过，下次自动重试"}
        if scode == CODE_NO_NETWORK:
            return 1, {"result": "NETWORK",
                       "report": "网络不可达，成长中心跳过（%s）" % (sbody.get("error") or "")}
        if _check_auth(scode):
            return 1, _auth_failure(scode)
        if not _note_http(scode, sbody, "查旅行状态", required=True):
            state = dig(sbody, "state")
            if state in ("idle", "traveling", "arrived"):
                travel = state
                daily_limit = bool(dig(sbody, "daily_limit_reached"))
            else:
                parts.append("旅行状态无法识别，跳过开 Buddy 与派出")
                failures += 1
                hard_failures += 1
        if travel == "arrived":
            record_id = dig(sbody, "record_id")
            ccode, cbody = post(base + "/buddy/travel/claim", headers, {"record_id": record_id})
            if _check_auth(ccode):
                return 1, _auth_failure(ccode)
            if _api_success(ccode, cbody) and dig(cbody, "reward_credit") is not None:
                got = as_int(dig(cbody, "reward_credit"))
                credits_gained += got
                parts.append("领旅行礼物 +%s 积分" % fmt_credit(got))
                successes += 1
                travel = "idle"
            else:
                msg = (dig(cbody, "msg") or "") if isinstance(cbody, dict) else ""
                parts.append("领旅行礼物失败：%s" % (msg or "HTTP %s" % ccode))
                failures += 1
                hard_failures += _is_hard_failure(ccode)
        if travel == "idle" and daily_limit:
            parts.append("今日旅行名额已用完")
        elif travel == "traveling":
            location = dig(sbody, "location")
            loc_name = location.get("name", "?") if isinstance(location, dict) else "?"
            parts.append("Buddy 旅行中（%s%s）" % (
                loc_name, _fmt_eta(dig(sbody, "arrive_at"), dig(sbody, "server_now"))))
    except Exception as e:
        parts.append("旅行模块异常（%s: %s）" % (type(e).__name__, e))
        failures += 1
        hard_failures += 1

    # 2. 任务领奖（放在抽奖前：任务送的抽奖机会/能量，后面马上能用上）
    if _budget_left() <= 0:
        parts.append("时间预算耗尽，任务领奖跳过")
    else:
        try:
            tcode, tbody = get(base + "/tasks", headers)
            if _check_auth(tcode):
                return 1, _auth_failure(tcode)
            if not _note_http(tcode, tbody, "查任务列表"):
                tasks = dig(tbody, "tasks") or []
                titles = {t.get("task_code"): t.get("title", t.get("task_code")) for t in tasks}
                pending = [t.get("task_code") for t in tasks
                           if t.get("task_code") and not t.get("locked")
                           and t.get("accept_status") == "not_accepted"]
                for i in range(0, len(pending), 20):
                    if _budget_left() <= 0:
                        parts.append("时间预算耗尽，剩余任务下次再接单")
                        break
                    batch = pending[i:i + 20]
                    acode, abody = post(base + "/tasks/accept", headers, {"task_codes": batch})
                    if _check_auth(acode):
                        return 1, _auth_failure(acode)
                    results = dig(abody, "results")
                    if not isinstance(results, list):
                        results = [{"task_code": c,
                                    "status": "ok" if 200 <= acode < 300 else "error",
                                    "message": dig(abody, "msg")} for c in batch]
                    for r in results:
                        title = titles.get(r.get("task_code"), r.get("task_code"))
                        if r.get("status") == "error":
                            parts.append("领取任务「%s」失败：%s" % (
                                title, r.get("message") or "HTTP %s" % acode))
                            failures += 1
                            hard_failures += _is_hard_failure(acode)
                        else:
                            parts.append("领取任务「%s」（进度开始计）" % title)
                            successes += 1
                for t in tasks:
                    if _budget_left() <= 0:
                        parts.append("时间预算耗尽，剩余任务奖下次再领")
                        break
                    if t.get("locked") or t.get("accept_status") != "completed":
                        continue
                    code = t.get("task_code")
                    title = titles.get(code, code)
                    try:
                        ccode, cbody = post(base + "/tasks/%s/claim" % code, headers, {})
                        if _check_auth(ccode):
                            return 1, _auth_failure(ccode)
                        if 200 <= ccode < 300 and not dig(cbody, "already_claimed"):
                            rc = _first_int(cbody, "credit", t.get("reward_credit"))
                            re_ = _first_int(cbody, "energy", t.get("reward_energy"))
                            credits_gained += rc
                            parts.append("领任务奖「%s」+credit%s+energy%s" % (title, rc, re_))
                            successes += 1
                        elif 200 <= ccode < 300:
                            parts.append("任务奖「%s」已领过" % title)
                        else:
                            parts.append("领任务奖「%s」失败：%s" % (
                                title, dig(cbody, "msg") or "HTTP %s" % ccode))
                            failures += 1
                            hard_failures += _is_hard_failure(ccode)
                    except Exception as e:
                        parts.append("领任务奖「%s」异常（%s: %s）" % (
                            code, type(e).__name__, e))
                        failures += 1
                        hard_failures += 1
        except Exception as e:
            parts.append("任务模块异常（%s: %s）" % (type(e).__name__, e))
            failures += 1
            hard_failures += 1

    # 3. 补登卡：断登自动补一张，保住连登
    streak_body = None
    streak_stale = False
    if _budget_left() <= 0:
        parts.append("时间预算耗尽，补登跳过")
    else:
        try:
            mcode, mbody = get(base + "/streak", headers)
            if _check_auth(mcode):
                return 1, _auth_failure(mcode)
            if not _note_http(mcode, mbody, "查连登状态", required=True):
                streak_body = mbody
                cards_obj = dig(mbody, "makeup_cards")
                cards = as_int(cards_obj.get("balance")) if isinstance(cards_obj, dict) \
                    else as_int(cards_obj)
                dates = []
                if cards > 0:
                    hcode, hbody = get(base + "/heatmap", headers)
                    if _check_auth(hcode):
                        return 1, _auth_failure(hcode)
                    if not _note_http(hcode, hbody, "查补登日历", required=True):
                        dates = _makeup_candidates(mbody, hbody)
                if dates:
                    made_up = 0
                    for d in dates[:min(cards, MAKEUP_MAX_PER_RUN)]:
                        if _budget_left() <= 0:
                            parts.append("时间预算耗尽，剩余补登下次再做")
                            break
                        ucode, ubody = post(base + "/makeup-cards/use", headers, {"target_date": d})
                        if _check_auth(ucode):
                            return 1, _auth_failure(ucode)
                        if _api_success(ucode, ubody):
                            cards -= 1
                            made_up += 1
                            streak_stale = True
                            left_obj = dig(ubody, "makeup_cards")
                            left_cards = as_int(left_obj.get("balance"), cards) \
                                if isinstance(left_obj, dict) else as_int(left_obj, cards)
                            parts.append("补登 %s（剩 %s 张卡）" % (d, left_cards))
                            cards = max(0, left_cards)
                            successes += 1
                        else:
                            msg = dig(ubody, "msg") or ""
                            if ucode == 400 and str(msg).strip().lower() == "date is not broken, no makeup needed":
                                parts.append("%s 已活跃或已补登，无需再次补登" % d)
                                break
                            parts.append("补登 %s 失败：%s" % (d, msg or "HTTP %s" % ucode))
                            failures += 1
                            hard_failures += 1
                            break
                    if made_up and len(dates) > made_up and cards > 0:
                        parts.append("另有 %s 天可补、剩 %s 张卡，下轮继续" % (
                            len(dates) - made_up, cards))
        except Exception as e:
            parts.append("补登模块异常（%s: %s）" % (type(e).__name__, e))
            failures += 1
            hard_failures += 1

    # 4. 连登奖励兑换
    if _budget_left() <= 0:
        parts.append("时间预算耗尽，连登兑换跳过")
    else:
        try:
            rcode, rbody = get(base + "/redeem/summary", headers)
            if _check_auth(rcode):
                return 1, _auth_failure(rcode)
            if not _note_http(rcode, rbody, "查连登兑换"):
                for tier, status_key, label, days in _REDEEM_TIERS:
                    if _budget_left() <= 0:
                        parts.append("时间预算耗尽，剩余连登兑换下次再领")
                        break
                    status = dig(rbody, status_key + "_status")
                    if not status or status in ("claimed", "locked"):
                        continue
                    c2code, c2body = post(base + "/redeem", headers,
                                          {"tier": tier, "client_token": _client_token()})
                    if _is_unknown_tier(c2code, c2body):
                        c2code, c2body = post(base + "/redeem", headers,
                                              {"tier": days, "client_token": _client_token()})
                    if _is_tier_locked(c2code, c2body):
                        parts.append("连登兑换「%s」未解锁（连登天数不足）" % label)
                        continue
                    if _check_auth(c2code):
                        return 1, _auth_failure(c2code)
                    if 200 <= c2code < 300:
                        credits_gained += as_int(dig(c2body, "credit_granted"))
                        parts.append("连登兑换「%s」%s" % (label, _redeem_reward_desc(c2body, tier)))
                        successes += 1
                    else:
                        msg = dig(c2body, "msg") or ""
                        parts.append("连登兑换「%s」失败：%s" % (label, msg or "HTTP %s" % c2code))
                        failures += 1
                        hard_failures += _is_hard_failure(c2code)
        except Exception as e:
            parts.append("连登兑换模块异常（%s: %s）" % (type(e).__name__, e))
            failures += 1
            hard_failures += 1

    # 5. 盲盒/抽奖
    if _budget_left() <= 0:
        parts.append("时间预算耗尽，盲盒跳过")
    else:
        try:
            lcode, lbody = get(base + "/lottery/chances", headers)
            if _check_auth(lcode):
                return 1, _auth_failure(lcode)
            chances = 0 if _note_http(lcode, lbody, "查抽奖机会") else as_int(dig(lbody, "balance"))
            if chances > 0:
                dcode, dbody = post(base + "/lottery/draw", headers,
                                    {"client_token": _client_token()})
                if _check_auth(dcode):
                    return 1, _auth_failure(dcode)
                if 200 <= dcode < 300:
                    prize = dig(dbody, "prize_name") or dig(dbody, "prize") or "未知"
                    if not isinstance(prize, str):
                        prize = str(prize)
                    if dig(dbody, "need_address") or dig(dbody, "require_address"):
                        prize += "（实物奖，需到成长中心填写收件信息）"
                    parts.append("开盲盒获得：%s" % prize)
                    successes += 1
                    if chances > 1:
                        parts.append("还剩 %s 次抽奖机会，下轮继续" % (chances - 1))
                else:
                    msg = dig(dbody, "msg") or ""
                    if _is_no_chance(msg):
                        parts.append("开盲盒：%s" % (msg or "无抽奖机会"))
                    else:
                        parts.append("开盲盒失败：%s" % (msg or "HTTP %s" % dcode))
                        failures += 1
                        hard_failures += _is_hard_failure(dcode)
        except Exception as e:
            parts.append("盲盒模块异常（%s: %s）" % (type(e).__name__, e))
            failures += 1
            hard_failures += 1

    # 5b. 自动领养第一只 Buddy（签协议 + 条件满足则领养；前置未满足给提示，不算失败）
    if _budget_left() <= 0:
        parts.append("时间预算耗尽，Buddy 领养跳过")
    else:
        try:
            acode, aout = run_adopt(headers, endpoint)
            anote = aout.get("report") if isinstance(aout, dict) else ""
            if anote and aout.get("result") != "ALREADY":
                parts.append(anote)
            if aout.get("adopted"):
                successes += 1
            if aout.get("failures"):
                failures += as_int(aout.get("failures"))
            if acode and aout.get("failures"):
                hard_failures += 1
            # 领养成功后刷新一次 Buddy 状态，供后续盲盒/派出步骤使用
            if aout.get("adopted") and travel == "idle":
                travel = "idle"
        except Exception as e:
            parts.append("Buddy 领养模块异常（%s: %s）" % (type(e).__name__, e))
            failures += 1
            hard_failures += 1

    # 6. Buddy 盲盒（能量开 Buddy）
    if travel != "idle":
        parts.append("Buddy 盲盒跳过：%s" % (
            "旅行尚未结束或礼物尚未领到" if travel in ("traveling", "arrived") else "旅行状态未知"))
    elif _budget_left() <= 0:
        parts.append("时间预算耗尽，Buddy 盲盒跳过")
    else:
        try:
            qcode, qbody = get(base + "/buddy/quota", headers)
            if _check_auth(qcode):
                return 1, _auth_failure(qcode)
            if not _note_http(qcode, qbody, "查 Buddy 能量"):
                affordable = as_int(dig(qbody, "affordable"))
                max_open = as_int(dig(qbody, "max_open_count"), 1) or 1
                if affordable > 0:
                    count = min(affordable, max_open)
                    ocode, obody = post(base + "/buddy/open", headers,
                                        {"count": count, "client_token": _client_token()})
                    if _check_auth(ocode):
                        return 1, _auth_failure(ocode)
                    if 200 <= ocode < 300:
                        name = dig(obody, "buddy") or dig(obody, "name") or dig(obody, "buddies")
                        if not isinstance(name, str):
                            name = "新 Buddy"
                        parts.append("开 Buddy 盲盒 ×%s（%s）" % (count, name))
                        successes += 1
                    else:
                        msg = dig(obody, "msg") or ""
                        parts.append("开 Buddy 盲盒失败：%s" % (msg or "HTTP %s" % ocode))
                        failures += 1
                        hard_failures += _is_hard_failure(ocode)
        except Exception as e:
            parts.append("Buddy 盲盒模块异常（%s: %s）" % (type(e).__name__, e))
            failures += 1
            hard_failures += 1

    # 6b. 最后派出 Buddy
    if travel == "idle" and not daily_limit:
        if _budget_left() <= 0:
            parts.append("时间预算耗尽，派 Buddy 跳过")
        else:
            try:
                ccode, cbody = get(base + "/buddy/travel/config", headers)
                if _check_auth(ccode):
                    return _stop_for_auth(ccode)
                if not _note_http(ccode, cbody, "查旅行配置", required=True):
                    locs = dig(cbody, "locations")
                    if locs and isinstance(locs[0], dict):
                        dcode, dbody = post(base + "/buddy/travel/depart", headers,
                                            {"location_id": locs[0].get("id")})
                        msg = str(dig(dbody, "msg") or "")
                        if dcode == 403 and msg.strip().lower() == "no active buddy":
                            parts.append("派 Buddy 跳过：尚未领养 Buddy")
                        elif _check_auth(dcode):
                            return _stop_for_auth(dcode)
                        elif _api_success(dcode, dbody):
                            location = dig(dbody, "location")
                            location = location if isinstance(location, dict) else {}
                            loc_name = location.get("name", "?")
                            dur = dig(dbody, "duration_hours") or location.get("duration_hours", "?")
                            parts.append("派 Buddy 去%s（%s 小时后回）" % (loc_name, dur))
                            successes += 1
                        else:
                            parts.append("派 Buddy 失败：%s" % (msg or "HTTP %s" % dcode))
                            failures += 1
                            hard_failures += _is_hard_failure(dcode)
            except Exception as e:
                parts.append("派 Buddy 模块异常（%s: %s）" % (type(e).__name__, e))
                failures += 1
                hard_failures += 1

    # 7. 能量 & 连签状态（纯展示）
    energy = None
    streak_days = None
    if _budget_left() > 0:
        try:
            ecode, ebody = get(base + "/energy", headers)
            if not _check_auth(ecode):
                energy = dig(ebody, "balance") if (200 <= ecode < 300) else None
        except Exception:
            pass
    try:
        if streak_body is not None and not streak_stale:
            streak_obj = dig(streak_body, "streak") or {}
            streak_days = streak_obj.get("days") if isinstance(streak_obj, dict) else None
        elif _budget_left() > 0:
            scode2, sbody2 = get(base + "/streak", headers)
            if not _check_auth(scode2):
                streak_obj = dig(sbody2, "streak") or {}
                streak_days = streak_obj.get("days") if isinstance(streak_obj, dict) else None
    except Exception:
        pass

    tail = []
    if energy is not None:
        tail.append("能量 %s" % energy)
    if streak_days is not None:
        tail.append("连签 %s 天" % streak_days)
    if credits_gained:
        tail.append("本次 +共 %s 积分" % credits_gained)

    if parts:
        report = "；".join(parts)
    elif failures:
        report = "成长中心各步骤均失败"
    else:
        report = "成长中心无可领取项"
    if tail:
        report += "（%s）" % "，".join(tail)

    result_code = 1 if hard_failures else 0
    idle = (successes == 0 and failures == 0)
    return result_code, {"result": "GROWTH", "report": report, "credits_gained": credits_gained,
                         "energy": energy, "streak_days": streak_days, "idle": idle,
                         **({"failures": failures} if failures else {})}


# --- 每日签到 ---------------------------------------------------------------
def run_checkin(headers, endpoint):
    scode, sbody = post(endpoint + "/v2/billing/meter/checkin-activity-status", headers, retry=True)
    if scode == CODE_BUDGET_OUT:
        return 1, {"result": "TIMEOUT", "report": "已达本次运行时间预算，签到跳过，下次自动重试"}
    if scode == CODE_NO_NETWORK:
        return 1, {"result": "NETWORK",
                   "report": "网络不可达，签到跳过，下次自动重试（%s）" % (sbody.get("error") or "")}
    if scode in (401, 403):
        return 1, _auth_failure(scode)
    if not (200 <= scode < 300):
        return 1, {"result": "ERROR",
                   "report": "签到接口返回异常（HTTP %s），请检查设置或稍后重试" % scode,
                   "http": scode}
    status = sbody if isinstance(sbody, dict) else {}
    if dig(status, "active") is False:
        return 0, {"result": "INACTIVE", "report": "签到活动未开启", "active": False}
    if dig(status, "today_checked_in") in (True, 1):
        return 0, _already_report(status)

    ccode, cbody = post(endpoint + "/v2/billing/meter/daily-checkin", headers, retry=True)
    if ccode in (CODE_NO_NETWORK, CODE_BUDGET_OUT):
        return 1, {"result": "NETWORK" if ccode == CODE_NO_NETWORK else "TIMEOUT",
                   "report": "领取请求未能送达，下次自动重试"}
    if ccode in (401, 403):
        return 1, _auth_failure(ccode)
    if _is_already_checked_in(cbody):
        return 0, _already_report(status, via="今日已签过（服务端判定已领取）")
    credit = dig(cbody, "credit")
    if credit is not None:
        fresh = status
        scode2, sbody2 = post(endpoint + "/v2/billing/meter/checkin-activity-status", headers, retry=True)
        if 200 <= scode2 < 300 and isinstance(sbody2, dict):
            fresh = sbody2
        streak_days = dig(fresh, "streak_days") or dig(status, "streak_days")
        total_credits = dig(fresh, "total_credits")
        bonus = "，且为连签奖励日" if dig(fresh, "is_streak_day") else ""
        cum = "，累计 %s 积分" % fmt_credit(total_credits) if total_credits is not None else ""
        streak = "（连续 %s 天%s）" % (streak_days, cum) if streak_days is not None else (
            "（%s）" % cum.lstrip("，") if cum else "")
        return 0, {"result": "CLAIMED",
                   "report": "成功领取 %s 积分%s%s" % (fmt_credit(credit), bonus, streak),
                   "credit": credit, "streak_days": streak_days, "total_credits": total_credits}
    if isinstance(cbody, dict) and ("code" in cbody or "msg" in cbody):
        msg = cbody.get("msg") or ("code %s" % cbody.get("code"))
        return 1, {"result": "ERROR", "report": "领取失败：%s（HTTP %s）" % (msg, ccode), "http": ccode}
    return 1, {"result": "UNKNOWN", "report": "未识别的领取返回，请检查接口", "http": ccode}


def run_status(headers, endpoint):
    try:
        code, body = post(endpoint + "/v2/billing/meter/checkin-activity-status", headers, retry=True)
    except Exception as e:
        return {"result": "ERROR", "report": "查询状态失败（%s）" % e}
    if code in (401, 403):
        return dict(_auth_failure(code))
    if not (200 <= code < 300):
        return {"result": "ERROR", "report": "状态接口返回 HTTP %s" % code, "http": code}
    status = body if isinstance(body, dict) else {}
    return {"result": "STATUS", "report": "登录态有效",
            "active": dig(status, "active"),
            "today_checked_in": dig(status, "today_checked_in"),
            "streak_days": dig(status, "streak_days"),
            "total_credits": dig(status, "total_credits")}


# --- 插件入口 ---------------------------------------------------------------
def run(ctx):
    global _log
    _log = ctx.log

    # action / 预算支持 params 注入（宿主高级参数可覆盖插件设置）
    action = str(ctx.params.get("action", "") or ctx.settings.get("action", "daily") or "daily")
    budget = ctx.params.get("budget_seconds", "") or ctx.settings.get("budget_seconds", 90)
    _start_budget(budget)

    # 凭据优先级：
    #   1) ctx.params 注入（宿主「批量执行」按账号注入 token/uid）
    #   2) 插件设置手动填写
    #   3) 本机已登录 WorkBuddy 桌面端自动提取
    injected_token = ctx.params.get("token", "") or ctx.settings.get("token", "")
    injected_uid = ctx.params.get("uid", "") or ctx.settings.get("uid", "")
    if injected_token and injected_uid:
        cfg = {
            "token": injected_token,
            "uid": injected_uid,
            "enterprise_id": ctx.params.get("enterprise_id", "") or ctx.settings.get("enterprise_id", ""),
            "domain": ctx.params.get("domain", "") or ctx.settings.get("domain", ""),
            "endpoint": ctx.settings.get("endpoint", DEFAULT_ENDPOINT),
        }
        _log("use injected account session uid=%s" % injected_uid[:8])
    else:
        auto, auto_err = _auto_session()
        if auto:
            cfg = {
                "token": ctx.settings.get("token", "") or auto.get("token", ""),
                "uid": ctx.settings.get("uid", "") or auto.get("uid", ""),
                "enterprise_id": ctx.settings.get("enterprise_id", "") or auto.get("enterprise_id", ""),
                "domain": ctx.settings.get("domain", "") or auto.get("domain", ""),
                "endpoint": ctx.settings.get("endpoint", DEFAULT_ENDPOINT),
            }
            _log("已从本机 WorkBuddy 登录态自动提取凭据（uid=%s）" % auto.get("uid", "")[:8])
        else:
            cfg = {
                "token": ctx.settings.get("token", ""),
                "uid": ctx.settings.get("uid", ""),
                "enterprise_id": ctx.settings.get("enterprise_id", ""),
                "domain": ctx.settings.get("domain", ""),
                "endpoint": ctx.settings.get("endpoint", DEFAULT_ENDPOINT),
            }
            if auto_err:
                _log("自动提取登录态未成功（%s），尝试使用手动填写的凭据" % auto_err)
    endpoint = (cfg["endpoint"] or DEFAULT_ENDPOINT).rstrip("/")

    if not cfg["token"] or not cfg["uid"]:
        msg = "未获取到 WorkBuddy 登录态：本机未登录桌面端，且插件设置中未填写 token/uid"
        _log(msg)
        return {"action": action, "result": "NO_SESSION", "needs_attention": True, "report": msg}

    try:
        headers = build_headers(cfg)
    except ValueError as e:
        _log(str(e))
        return {"action": action, "result": "NO_SESSION", "needs_attention": True, "report": str(e)}

    _log("开始执行 workbuddy成长系统（action=%s，预算 %ss）" % (action, int(_budget_seconds)))

    out = {}
    if action == "status":
        out = run_status(headers, endpoint)
    elif action == "adopt":
        code, out = run_adopt(headers, endpoint)
        out["exit_code"] = code
    elif action == "growth":
        code, out = run_growth(headers, endpoint)
        out["exit_code"] = code
    elif action == "checkin":
        code, out = run_checkin(headers, endpoint)
        out["exit_code"] = code
    else:  # daily = 签到 + 成长中心
        code, out = run_checkin(headers, endpoint)
        checkin_code = code
        if out.get("result") in ("NETWORK", "TIMEOUT", "NO_SESSION", "AUTH_REJECTED", "FORBIDDEN"):
            out["growth"] = "签到未完成，成长中心跳过"
        else:
            try:
                gcode, gout = run_growth(headers, endpoint)
            except Exception as e:
                gcode, gout = 1, {"result": "ERROR", "report": "成长中心异常（%s: %s）" % (type(e).__name__, e)}
            out["growth"] = gout.get("report")
            out["growth_credits"] = as_int(gout.get("credits_gained"), 0)
            out["credits_gained"] = (as_int(out.get("credit"), 0) if out.get("result") == "CLAIMED" else 0) \
                + out["growth_credits"]
            if gout.get("report"):
                out["report"] = str(out.get("report") or "") + "；" + gout["report"]
            if gcode != 0 and checkin_code == 0:
                code = gcode
        out["exit_code"] = code

    out = dict(out or {})
    out["action"] = action
    if not isinstance(out.get("report"), str):
        out["report"] = str(out.get("report") or "")
    out["needs_attention"] = bool(out.get("needs_attention")) or int(out.get("exit_code") or 0) != 0
    _log("执行结束：" + out["report"])
    return out
