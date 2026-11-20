# -*- coding: utf-8 -*-
"""Qoder / Qoder CN 签到、Pro 福利与签到流水插件。

进程内运行，直接复用本项目已实现的：
  - app.services.qoder_campaign  (claim_with_creds / pro_claim / checkin_status / status_with_creds)
  - app.services.qoder_activity  (recent)
  - app.services.native_accounts (list_echo_accounts / refresh_account)

账号来自 EchoBird 本地存储，不由 ctx.credentials 提供，故无需声明权限。
入参 params:
  action      claim(默认)|pro|activity|status|all
  platform    qoder|qodercn（单账号时可选）
  account_id  echo 存储里的 account_id（单账号时可选；不传则遍历全部）
"""
import time

# 后端主事件循环引用（由 router 通过 params.__main_loop__ 注入）。
# qoder_campaign 走 ssl_relax 的进程级共享 httpx.AsyncClient，绑定主 loop；
# 插件在线程池里同步执行，必须把协程提交回主 loop，否则报 "Event loop is closed"。
_MAIN_LOOP = None


def _accounts(ctx):
    """解析目标账号列表：指定 platform+account_id 则只取一个，否则遍历全部。"""
    from app.services import native_accounts as na
    from app.services import qoder_accounts as qa

    platform = str(ctx.params.get("platform") or "")
    account_id = str(ctx.params.get("account_id") or "")
    all_entries = []
    for p in ("qoder", "qodercn"):
        for entry in na.list_echo_accounts(p):
            all_entries.append((p, entry))

    if account_id:
        if not platform:
            platform, _ = qa.parse_id(account_id)
        picked = [(p, e) for p, e in all_entries
                  if p == platform and e.get("account_id") == account_id]
        if not picked:
            return []
        return picked
    if platform:
        return [(p, e) for p, e in all_entries if p == platform]
    return all_entries


def _creds(entry):
    return ((entry.get("extra") or {}).get("creds") or {})


def _name(entry):
    return str((entry.get("extra") or {}).get("summary", {}).get("name")
               or entry.get("name") or entry.get("account_id", "")[:10])


def _do_claim(ctx, platform, entry):
    from app.services import qoder_campaign as qc
    creds = _creds(entry)
    try:
        result = await_claim(creds, platform)
        ok = bool(result.get("claimed") or result.get("already"))
        qc._log_activity(creds, platform, "daily_claim", ok,
                         "already" if result.get("already") else "claimed")
        return {"account": _name(entry), "platform": platform, "ok": ok,
                "already": bool(result.get("already")),
                "status": "already" if result.get("already") else ("claimed" if ok else "failed"),
                "reset_at": result.get("reset_at")}
    except qc.CampaignError as e:
        qc._log_activity(creds, platform, "daily_claim", False, e.message)
        # 活动未开放/今日已领完属软失败，不当作插件失败
        soft = ("未开放" in e.message or "已领取" in e.message or "已领完" in e.message
                or "CLAIMED" in e.message.upper())
        return {"account": _name(entry), "platform": platform, "ok": soft,
                "status": "already" if soft else "error", "message": e.message[:160]}
    except Exception as e:
        return {"account": _name(entry), "platform": platform, "ok": False,
                "status": "error", "message": str(e)[:160]}


def await_coro(fn, *args):
    """协程同步化：优先提交到后端主事件循环（共享 httpx client 绑定于此），
    否则退回独立线程 asyncio.run（无主 loop 时的兜底，如本地单测）。"""
    global _MAIN_LOOP
    import asyncio
    loop = _MAIN_LOOP
    if loop is not None and not loop.is_closed():
        future = asyncio.run_coroutine_threadsafe(fn(*args), loop)
        return future.result(timeout=120)
    # 兜底：独立线程新 loop；临时把 ssl_relax 共享 client 清空，强制当次新建
    import concurrent.futures
    from app.utils import ssl_relax

    def _runner():
        # 清掉绑定到别的 loop 的共享 client，强制本次新建（关闭由应用 shutdown 统一处理）
        try:
            ssl_relax._clients.clear()
        except Exception:
            pass
        try:
            return asyncio.run(fn(*args))
        finally:
            try:
                ssl_relax._clients.clear()
            except Exception:
                pass

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(_runner).result()


def await_claim(creds, platform):
    """同步包装 qoder_campaign.claim_with_creds。"""
    from app.services import qoder_campaign as qc
    return await_coro(qc.claim_with_creds, creds, platform)


def _do_pro(ctx, platform, entry):
    from app.services import qoder_campaign as qc
    creds = _creds(entry)
    try:
        result = await_coro(qc.pro_claim, creds, platform)
        ok = bool(result.get("ok"))
        qc._log_activity(creds, platform, "pro_claim", ok,
                         "already" if result.get("already") else "claimed",
                         amount=int(result.get("amount") or 0))
        return {"account": _name(entry), "platform": platform, "ok": ok,
                "already": bool(result.get("already")),
                "status": "already" if result.get("already") else ("claimed" if ok else "failed"),
                "amount": int(result.get("amount") or 0)}
    except qc.CampaignError as e:
        qc._log_activity(creds, platform, "pro_claim", False, e.message)
        soft = "已领" in e.message or "已领取" in e.message or "ALREADY" in e.message.upper()
        return {"account": _name(entry), "platform": platform, "ok": soft,
                "status": "already" if soft else "error", "message": e.message[:160]}
    except Exception as e:
        return {"account": _name(entry), "platform": platform, "ok": False,
                "status": "error", "message": str(e)[:160]}


def _do_status(ctx, platform, entry):
    from app.services import qoder_campaign as qc
    try:
        st = await_coro(qc.status_with_creds, _creds(entry), platform)
        return {"account": _name(entry), "platform": platform, "ok": True,
                "status": (st or {}).get("status") or "",
                "daily_claimed_at": (st or {}).get("claimed_at") or 0,
                "reset_at": (st or {}).get("reset_at") or 0}
    except Exception as e:
        return {"account": _name(entry), "platform": platform, "ok": False,
                "status": "error", "message": str(e)[:160]}


def _do_activity(ctx):
    from app.services import qoder_activity as qa
    limit = ctx.params.get("limit", 100)
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 100
    rows = qa.recent(limit=limit)
    return {"ok": True, "result": "ACTIVITY", "items": rows,
            "report": "最近 %d 条签到/领奖流水" % len(rows)}


def run(ctx):
    global _MAIN_LOOP
    _MAIN_LOOP = ctx.params.get("__main_loop__")
    action = str(ctx.params.get("action")
                 or ctx.settings.get("action", "claim") or "claim").strip().lower()
    ctx.log("Qoder claim start: action=%s params=%s" % (action, sorted(ctx.params.keys())))

    if action == "activity":
        return _do_activity(ctx)

    targets = _accounts(ctx)
    if not targets:
        return {"ok": False, "result": "NO_ACCOUNT",
                "report": "未找到匹配的 Qoder 账号（请先在账号页登录 Qoder / Qoder CN）"}

    results = []
    for platform, entry in targets:
        if action in ("claim", "all"):
            results.append(_do_claim(ctx, platform, entry))
        if action in ("pro", "all"):
            results.append(_do_pro(ctx, platform, entry))
        if action == "status":
            results.append(_do_status(ctx, platform, entry))
        if action not in ("claim", "pro", "all", "status"):
            return {"ok": False, "result": "BAD_ACTION", "report": "未知操作: %s" % action}

    ok_n = sum(1 for r in results if r.get("ok"))
    already_n = sum(1 for r in results if r.get("status") == "already")
    failed_n = sum(1 for r in results if r.get("status") == "error")
    parts = ["%s：成功 %d，已领过 %d，失败 %d" % (
        {"claim": "签到", "pro": "Pro 福利", "all": "签到+福利", "status": "状态"}.get(action, action),
        ok_n, already_n, failed_n)]
    for r in results:
        if r.get("status") == "error":
            parts.append("%s(%s) 失败：%s" % (r.get("account"), r.get("platform"), r.get("message", "")))
    return {"ok": failed_n == 0, "result": "DONE", "action": action,
            "ok_count": ok_n, "already_count": already_n, "failed_count": failed_n,
            "results": results, "report": "；".join(parts)}
