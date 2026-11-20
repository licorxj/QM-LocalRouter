"""脚本插件定时任务调度器：进程内后台循环，周期扫描到期任务并触发。

排程表达式为 5 段 cron（分 时 日 月 周），解析逻辑移植自项目内已验证的
_qoder2api_research/qoder_scheduler.py，无第三方依赖。任务与执行日志落库，
重启后靠 last_run_at + 触发槽位去重，避免同一点重复执行。
"""
import asyncio
import json
import re
import time
from datetime import datetime, timedelta

# 触发槽位：{task_id: slot}，防止同一排程周期重复触发
_fired: dict[int, str] = {}
_stop_event: asyncio.Event | None = None
_loop_task: asyncio.Task | None = None
TICK_SECONDS = 20


# ---------- 时间 / cron 工具 ----------
def _dt_from_ts(ts: float) -> datetime:
    return datetime.fromtimestamp(ts)


def _cron_field(spec, lo: int, hi: int) -> set[int] | None:
    out: set[int] = set()
    for part in str(spec or "").split(","):
        part = part.strip()
        if not part:
            continue
        step = 1
        has_step = "/" in part
        if has_step:
            part, _, s = part.partition("/")
            try:
                step = max(1, int(s))
            except Exception:
                return None
        if part == "*":
            rng = range(lo, hi + 1)
        elif "-" in part:
            a, _, b = part.partition("-")
            try:
                a, b = int(a), int(b)
            except Exception:
                return None
            if a > b:
                a, b = b, a
            rng = range(max(lo, a), min(hi, b) + 1)
        else:
            try:
                v = int(part)
            except Exception:
                return None
            if v < lo or v > hi:
                return None
            rng = range(v, hi + 1) if has_step else [v]
        out.update(x for x in rng if (x - lo) % step == 0)
    return out or None


def _cron_fields(expr: str) -> tuple | None:
    parts = str(expr or "").split()
    if len(parts) != 5:
        return None
    minute = _cron_field(parts[0], 0, 59)
    hour = _cron_field(parts[1], 0, 23)
    dom = _cron_field(parts[2], 1, 31)
    mon = _cron_field(parts[3], 1, 12)
    dow = _cron_field(parts[4], 0, 6)
    if None in (minute, hour, dom, mon, dow):
        return None
    return minute, hour, dom, mon, dow


def _cron_matches(expr, dt: datetime) -> bool:
    f = _cron_fields(expr)
    if not f:
        return False
    minute, hour, dom, mon, dow = f
    if dt.minute not in minute or dt.hour not in hour or dt.month not in mon:
        return False
    if dt.day not in dom:
        return False
    return ((dt.weekday() + 1) % 7) in dow  # Python 0=周一 -> cron 0=周日


def validate_cron(expr: str) -> bool:
    return _cron_fields(expr) is not None


def compute_next_run(task) -> str:
    """根据任务排程算出下次运行时间字符串（展示用，失败返回空）。"""
    try:
        stype = task.schedule_type
        if stype == "once":
            return task.run_at or ""
        if stype == "interval":
            base = task.last_run_at or task.created_at or datetime.now()
            nxt = base + timedelta(minutes=int(task.interval_minutes or 60))
            return nxt.strftime("%Y-%m-%d %H:%M")
        if stype == "cron":
            f = _cron_fields(task.cron_expr)
            if not f:
                return ""
            minute, hour, *_ = f
            now = datetime.now()
            # 最多向前找 366 天，按分钟步进过快，直接按天枚举候选时刻
            for d in range(0, 367):
                day = (now + timedelta(days=d)).date()
                for h in sorted(hour):
                    for mi in sorted(minute):
                        cand = datetime.combine(day, datetime.min.time()).replace(hour=h, minute=mi)
                        if cand > now and _cron_matches(task.cron_expr, cand):
                            return cand.strftime("%Y-%m-%d %H:%M")
            return ""
    except Exception:
        return ""
    return ""


def _slot_for(task, now: datetime) -> str:
    """当前排程周期的唯一槽位；同一槽位只触发一次。"""
    stype = task.schedule_type
    if stype == "cron":
        return "cron:" + now.strftime("%Y-%m-%dT%H:%M")
    if stype == "interval":
        return "interval:" + str(int(now.timestamp()) // max(1, int(task.interval_minutes or 60)) * 60)
    if stype == "once":
        return "once:" + (task.run_at or "")
    return "manual:" + str(int(now.timestamp()))


def _is_due(task, now: datetime) -> bool:
    stype = task.schedule_type
    if stype == "cron":
        return _cron_matches(task.cron_expr, now)
    if stype == "interval":
        base = task.last_run_at or task.created_at
        if base is None:
            return False
        return (now - base) >= timedelta(minutes=int(task.interval_minutes or 60))
    if stype == "once":
        if not task.run_at:
            return False
        try:
            target = datetime.strptime(task.run_at, "%Y-%m-%d %H:%M")
        except ValueError:
            return False
        # 到点且本任务尚未成功执行过（last_run_at 为空或早于目标时间视为未执行）
        return now >= target and (task.last_run_at is None or task.last_run_at < target)
    return False


# ---------- 生命周期 ----------
async def start():
    global _stop_event, _loop_task
    if _loop_task and not _loop_task.done():
        return
    _stop_event = asyncio.Event()
    _loop_task = asyncio.create_task(_run_loop(), name="script-scheduler")
    print("[script-scheduler] 定时任务调度器已启动")


async def stop():
    global _stop_event, _loop_task
    if _stop_event:
        _stop_event.set()
    if _loop_task:
        try:
            await asyncio.wait_for(_loop_task, timeout=5)
        except Exception:
            _loop_task.cancel()
    _loop_task = None
    print("[script-scheduler] 定时任务调度器已停止")


async def _run_loop():
    assert _stop_event is not None
    while not _stop_event.is_set():
        try:
            await _tick()
        except Exception as e:
            print(f"[script-scheduler] tick 异常: {str(e)[:200]}")
        try:
            await asyncio.wait_for(_stop_event.wait(), timeout=TICK_SECONDS)
        except asyncio.TimeoutError:
            pass


async def _tick():
    from sqlalchemy import select
    from app.database import async_session
    from app.models.scheduled_task import ScheduledTask

    now = datetime.now()
    # 清理过期槽位（仅保留近 2 天）
    if len(_fired) > 500:
        for k in list(_fired)[: len(_fired) // 2]:
            _fired.pop(k, None)

    async with async_session() as db:
        result = await db.execute(select(ScheduledTask).where(ScheduledTask.enabled == True))  # noqa: E712
        tasks = list(result.scalars().all())

    from app.services import script_task_runner as runner
    for task in tasks:
        slot = _slot_for(task, now)
        if _fired.get(task.id) == slot:
            continue
        if not _is_due(task, now):
            continue
        # mark-before-act：先记槽位再执行，避免执行中重启导致重放
        _fired[task.id] = slot
        try:
            params = json.loads(task.params_json or "{}") if task.params_json else {}
            accs = json.loads(task.account_ids_json or "[]") if task.account_ids_json else []
        except Exception:
            params, accs = {}, []
        asyncio.create_task(runner.execute_task(
            task_id=task.id, plugin_id=task.plugin_id, name=task.name,
            params=params, account_ids=accs, all_accounts=bool(task.all_accounts),
            trigger=task.schedule_type,
        ))
