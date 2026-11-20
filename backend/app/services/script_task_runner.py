"""脚本插件定时执行内核：账号解析 + 参数注入 + 串行执行 + 落库日志。

从 routers/script_plugins.py 的账号编排逻辑下沉而来，供定时任务调度器与
手动触发共用，避免 API 与调度两套实现漂移。
"""
import asyncio
import json
import time

from app.database import async_session
from app.models.scheduled_task import ScheduledTask, ScheduledTaskRun
from app.services import script_plugin_manager as mgr
from app.services import workbuddy_accounts as wb
from app.services import zcode_accounts as zc
from app.services import qoder_accounts as qd

ACCOUNT_SOURCE = {
    "workbuddy-growth": "workbuddy",
    "zcode-claim": "zcode",
    "qoder-claim": "qoder",
}


def account_source(plugin_id: str) -> str:
    return ACCOUNT_SOURCE.get(plugin_id, "workbuddy")


def list_accounts(plugin_id: str) -> list[dict]:
    source = account_source(plugin_id)
    if source == "zcode":
        return zc.list_accounts()
    if source == "qoder":
        return qd.list_accounts()
    return wb.list_accounts()


def resolve_account_ids(plugin_id: str,
                        all_accounts: bool, account_ids: list[str] | None) -> list[str]:
    accs = list_accounts(plugin_id)
    valid = {a["id"] for a in accs}
    if all_accounts:
        return [a["id"] for a in accs]
    return [i for i in (account_ids or []) if i in valid]


def _inject_account(params: dict, plugin_id, aid) -> dict:
    """按账号源把账号凭据注入执行 params（与 run-batch 完全一致）。"""
    params = dict(params)
    source = account_source(plugin_id)
    if source == "zcode":
        params["key_id"] = int(aid) if str(aid).isdigit() else aid
    elif source == "qoder":
        platform, account_id = qd.parse_id(aid)
        params["platform"] = platform
        params["account_id"] = account_id
    else:
        creds = wb.resolve_credentials(aid)
        params["token"] = creds["token"]
        params["uid"] = creds["uid"]
        if creds.get("domain"):
            params["domain"] = creds["domain"]
        if creds.get("enterprise_id"):
            params["enterprise_id"] = creds["enterprise_id"]
    return params


def _account_name(plugin_id, aid) -> str:
    try:
        acc = next((a for a in list_accounts(plugin_id) if a["id"] == aid), None)
        return acc["name"] if acc else aid[:8]
    except Exception:
        return aid[:8]


def _trunc_result(res) -> str:
    """把插件结果压缩为可落库的短文本（避免超大 JSON 撑爆日志表）。"""
    try:
        text = json.dumps(res, ensure_ascii=False)
    except Exception:
        text = str(res)
    return text[:1500]


async def execute_task(task_id: int, plugin_id: str, name: str,
                       params: dict, account_ids: list[str], all_accounts: bool,
                       trigger: str, account_ids_override: list[str] | None = None) -> dict:
    """执行一个定时任务：逐账号串行运行插件，逐条落 ScheduledTaskRun 日志，
    并更新 ScheduledTask 的 last_run_at / last_ok / next_run_at。

    account_ids_override 用于手动触发时前端传入的临时账号集合（None 表示用任务保存的）。
    返回 {ok_count, failed_count, total, results[]}。
    """
    loop = asyncio.get_running_loop()
    ids = (resolve_account_ids(plugin_id, all_accounts, account_ids_override)
           if account_ids_override is not None
           else resolve_account_ids(plugin_id, all_accounts, account_ids))
    base_params = dict(params or {})
    base_params["__main_loop__"] = loop

    results: list[dict] = []
    fired_at = time.time()
    for aid in ids:
        aname = _account_name(plugin_id, aid)
        try:
            run_params = _inject_account(base_params, plugin_id, aid)
        except Exception as e:
            results.append({"account_id": aid[:8], "account_name": aname,
                            "ok": False, "error": f"账号凭据解析失败: {str(e)[:120]}"})
            continue
        try:
            res = await asyncio.wait_for(
                asyncio.to_thread(mgr.run_plugin, plugin_id, run_params),
                timeout=mgr.RUN_TIMEOUT,
            )
            results.append({
                "account_id": aid[:8], "account_name": aname,
                "ok": bool(res.get("ok")),
                "result": _trunc_result(res.get("result")),
                "error": str(res.get("error") or "")[:300],
                "elapsed": float(res.get("elapsed") or 0),
            })
        except asyncio.TimeoutError:
            results.append({"account_id": aid[:8], "account_name": aname,
                            "ok": False, "error": f"执行超时（>{mgr.RUN_TIMEOUT}s）"})
        except Exception as e:
            results.append({"account_id": aid[:8], "account_name": aname,
                            "ok": False, "error": str(e)[:300]})
        await asyncio.sleep(0)  # 让出事件循环，避免长任务阻塞心跳

    ok_count = sum(1 for r in results if r["ok"])
    failed_count = len(results) - ok_count

    # 落库：逐账号日志 + 任务状态
    try:
        async with async_session() as db:
            for r in results:
                db.add(ScheduledTaskRun(
                    task_id=task_id, task_name=name, plugin_id=plugin_id,
                    trigger=trigger, account_id=r.get("account_id", ""),
                    account_name=r.get("account_name", ""), ok=bool(r.get("ok")),
                    result=r.get("result", ""), error=r.get("error", ""),
                    elapsed=float(r.get("elapsed") or 0),
                ))
            task = await db.get(ScheduledTask, task_id)
            if task:
                from app.services import script_scheduler
                task.last_run_at = script_scheduler._dt_from_ts(fired_at)
                task.last_ok = failed_count == 0 and bool(results)
                task.next_run_at = script_scheduler.compute_next_run(task)
            await db.commit()
    except Exception as _e:
        # 日志落库失败不应影响执行结果返回
        import traceback
        print(f"[sched-runner] 日志落库失败 task={task_id}: {_e}")
        traceback.print_exc()

    return {"ok_count": ok_count, "failed_count": failed_count,
            "total": len(results), "results": results}
