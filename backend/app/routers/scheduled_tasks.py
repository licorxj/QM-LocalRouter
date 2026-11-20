"""脚本插件定时任务 API：任务 CRUD / 启停 / 手动触发 / 执行日志。"""
import asyncio
import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, delete, func

from app.database import async_session
from app.models.scheduled_task import ScheduledTask, ScheduledTaskRun
from app.services import script_scheduler as sched
from app.services import script_task_runner as runner
from app.services import script_plugin_manager as mgr

router = APIRouter(prefix="/api/scheduled-tasks", tags=["scheduled-tasks"])


class TaskIn(BaseModel):
    name: str = ""
    plugin_id: str
    params: dict | None = None         # 执行参数（JSON 对象）
    account_ids: list[str] | None = None
    all_accounts: bool = False
    schedule_type: str = "cron"        # cron | interval | once
    cron_expr: str = ""
    interval_minutes: int = 60
    run_at: str = ""
    enabled: bool = True


class RunIn(BaseModel):
    account_ids: list[str] | None = None   # 手动触发临时账号集合（可选）
    all_accounts: bool | None = None


def _validate(body: TaskIn):
    if not body.plugin_id:
        raise HTTPException(400, "缺少插件 id")
    if not mgr.get_installed(body.plugin_id):
        raise HTTPException(400, f"插件未安装: {body.plugin_id}")
    _pi = mgr.get_installed(body.plugin_id) or {}
    if _pi.get("type") == "apiserver":
        raise HTTPException(400, "apiserver 服务型插件不支持定时任务")
    if body.schedule_type not in ("cron", "interval", "once"):
        raise HTTPException(400, "排程类型必须为 cron / interval / once")
    if body.schedule_type == "cron" and not sched.validate_cron(body.cron_expr):
        raise HTTPException(400, "cron 表达式无效（需要 5 段：分 时 日 月 周）")
    if body.schedule_type == "interval" and int(body.interval_minutes or 0) < 1:
        raise HTTPException(400, "间隔分钟数必须 >= 1")
    if body.schedule_type == "once":
        from datetime import datetime
        try:
            datetime.strptime(body.run_at or "", "%Y-%m-%d %H:%M")
        except ValueError:
            raise HTTPException(400, "一次性任务时间格式应为 YYYY-MM-DD HH:MM")


def _dump(task: ScheduledTask, body: TaskIn):
    task.name = body.name or body.plugin_id
    task.plugin_id = body.plugin_id
    task.params_json = json.dumps(body.params or {}, ensure_ascii=False)
    task.account_ids_json = json.dumps(body.account_ids or [], ensure_ascii=False)
    task.all_accounts = bool(body.all_accounts)
    task.schedule_type = body.schedule_type
    task.cron_expr = body.cron_expr or ""
    task.interval_minutes = int(body.interval_minutes or 60)
    task.run_at = body.run_at or ""
    task.enabled = bool(body.enabled)
    task.next_run_at = sched.compute_next_run(task)


@router.get("")
async def list_tasks():
    async with async_session() as db:
        rows = (await db.execute(select(ScheduledTask).order_by(ScheduledTask.id.desc()))).scalars().all()
        return {"ok": True, "tasks": [r.as_dict() for r in rows]}


@router.post("", status_code=201)
async def create_task(body: TaskIn):
    _validate(body)
    async with async_session() as db:
        task = ScheduledTask()
        _dump(task, body)
        db.add(task)
        await db.commit()
        await db.refresh(task)
        return {"ok": True, "task": task.as_dict()}


@router.put("/{task_id}")
async def update_task(task_id: int, body: TaskIn):
    _validate(body)
    async with async_session() as db:
        task = await db.get(ScheduledTask, task_id)
        if not task:
            raise HTTPException(404, "任务不存在")
        _dump(task, body)
        # 改排程后重置触发槽位，使新配置立即生效
        sched._fired.pop(task_id, None)
        await db.commit()
        await db.refresh(task)
        return {"ok": True, "task": task.as_dict()}


@router.delete("/{task_id}")
async def delete_task(task_id: int):
    async with async_session() as db:
        task = await db.get(ScheduledTask, task_id)
        if not task:
            raise HTTPException(404, "任务不存在")
        await db.execute(delete(ScheduledTaskRun).where(ScheduledTaskRun.task_id == task_id))
        await db.delete(task)
        await db.commit()
    sched._fired.pop(task_id, None)
    return {"ok": True}


@router.post("/{task_id}/toggle")
async def toggle_task(task_id: int):
    async with async_session() as db:
        task = await db.get(ScheduledTask, task_id)
        if not task:
            raise HTTPException(404, "任务不存在")
        task.enabled = not bool(task.enabled)
        sched._fired.pop(task_id, None)
        await db.commit()
        await db.refresh(task)
        return {"ok": True, "task": task.as_dict()}


@router.post("/{task_id}/run", status_code=202)
async def run_task_now(task_id: int, body: RunIn | None = None):
    """手动立即触发一次（异步执行，不阻塞）。可用 body 临时覆盖账号集合。"""
    body = body or RunIn()
    async with async_session() as db:
        task = await db.get(ScheduledTask, task_id)
        if not task:
            raise HTTPException(404, "任务不存在")
        snapshot = task.as_dict()
    try:
        params = json.loads(snapshot["params"] or "{}") if snapshot["params"] else {}
        saved_accs = json.loads(snapshot["account_ids"] or "[]") if snapshot["account_ids"] else []
    except Exception:
        params, saved_accs = {}, []
    override = body.account_ids if body.account_ids is not None else None
    all_accs = body.all_accounts if body.all_accounts is not None else snapshot["all_accounts"]
    if override is None and not (all_accs or saved_accs):
        raise HTTPException(400, "任务未配置账号，请在请求中提供 account_ids 或 all_accounts")

    async def _run():
        await runner.execute_task(
            task_id=task_id, plugin_id=snapshot["plugin_id"], name=snapshot["name"],
            params=params, account_ids=saved_accs, all_accounts=bool(all_accs),
            trigger="manual", account_ids_override=override,
        )

    asyncio.create_task(_run())
    return {"ok": True, "task_id": task_id, "message": "已触发，执行中"}


@router.get("/runs")
async def list_runs(task_id: int | None = None, limit: int = 100):
    """执行日志（可按任务筛选，倒序）。"""
    async with async_session() as db:
        stmt = select(ScheduledTaskRun)
        if task_id:
            stmt = stmt.where(ScheduledTaskRun.task_id == task_id)
        stmt = stmt.order_by(ScheduledTaskRun.id.desc()).limit(max(1, min(limit, 500)))
        rows = (await db.execute(stmt)).scalars().all()
        return {"ok": True, "runs": [r.as_dict() for r in rows]}


@router.get("/stats")
async def stats():
    async with async_session() as db:
        total = (await db.execute(select(func.count(ScheduledTask.id)))).scalar() or 0
        enabled = (await db.execute(select(func.count(ScheduledTask.id)).where(ScheduledTask.enabled == True))).scalar() or 0  # noqa: E712
        runs = (await db.execute(select(func.count(ScheduledTaskRun.id)))).scalar() or 0
        ok = (await db.execute(select(func.count(ScheduledTaskRun.id)).where(ScheduledTaskRun.ok == True))).scalar() or 0  # noqa: E712
        return {"ok": True, "total": total, "enabled": enabled, "runs": runs, "ok": ok}
