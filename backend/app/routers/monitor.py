"""可视化监控（Monitor 页）后端。

- 内存事件总线：转发链路每写入一条请求日志就广播一个事件，前端 SSE 实时消费。
- GET /api/monitor/recent   近 5 小时日志 + 每个模型的 5 小时 token 聚合。
- GET /api/monitor/stream   SSE 实时推送新日志（30s 心跳防代理断连）。

广播在 log_request 的后台任务里做，失败静默，不影响主链路。
"""
import asyncio
from datetime import datetime, timedelta, timezone
from typing import AsyncGenerator

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, async_session
from app.models.log import RequestLog
from app.models.api_key import ApiKey

router = APIRouter(prefix="/api/monitor", tags=["monitor"])

WINDOW_HOURS = 5  # 与"模型 5 小时请求 token"口径一致


class LogBus:
    """进程内日志事件总线：SSE 订阅者的队列集合；无订阅者时发布零成本。"""

    def __init__(self):
        self._queues: set[asyncio.Queue] = set()

    def publish(self, event: dict) -> None:
        for q in list(self._queues):
            try:
                q.put_nowait(event)
            except Exception:
                pass


bus = LogBus()


def _fmt_event(log: RequestLog, key_alias: str = "") -> dict:
    return {
        "id": log.id,
        "client_ip": log.client_ip or "-",
        "strategy_id": log.strategy_id,
        "provider_id": log.provider_id,
        "model": log.model_used or "-",
        "status_code": log.status_code,
        "latency_ms": log.latency_ms,
        "total_tokens": log.total_tokens or 0,
        "key_alias": key_alias,
        "error_message": log.error_message,
        "created_at": log.created_at.isoformat() if log.created_at else None,
    }


async def _query_row(log_id: int) -> dict | None:
    """独立 session 里重查刚落的日志（含 key 别名），组装成监控事件。"""
    try:
        async with async_session() as session:
            result = await session.execute(
                select(RequestLog, ApiKey.alias)
                .outerjoin(ApiKey, RequestLog.api_key_id == ApiKey.id)
                .where(RequestLog.id == log_id)
            )
            row = result.first()
            if not row:
                return None
            return _fmt_event(row[0], row[1] or "")
    except Exception:
        return None


def broadcast_start(event: dict) -> None:
    """转发链路在“已选定平台/模型、尚未请求上游”时调用，广播进行中事件。

    同步 put_nowait，无 I/O；前端据此点亮脉冲，收到同 rid 的 done 后熄灭。
    """
    try:
        bus.publish(dict(event))
    except Exception:
        pass


def broadcast_log(log_id: int, rid: str | None = None) -> None:
    """转发链路调用：落库后异步广播（无事件循环时静默放弃）。"""
    async def _emit():
        event = await _query_row(log_id)
        if event:
            if rid:
                event["rid"] = rid
            bus.publish(event)

    try:
        task = asyncio.create_task(_emit())
        from app.services.forwarder import _bg_tasks
        _bg_tasks.add(task)
        task.add_done_callback(_bg_tasks.discard)
    except RuntimeError:
        pass


@router.get("/recent")
async def recent_logs(db: AsyncSession = Depends(get_db)):
    """近 5 小时日志（最多 200 条，倒序）+ 按模型聚合的 5 小时 token。"""
    since = datetime.now(timezone.utc) - timedelta(hours=WINDOW_HOURS)
    result = await db.execute(
        select(RequestLog, ApiKey.alias)
        .outerjoin(ApiKey, RequestLog.api_key_id == ApiKey.id)
        .where(RequestLog.created_at >= since)
        .order_by(RequestLog.id.desc())
        .limit(200)
    )
    logs = [_fmt_event(log, alias or "") for log, alias in result.all()]

    model_result = await db.execute(
        select(
            RequestLog.model_used,
            func.count(RequestLog.id),
            func.coalesce(func.sum(RequestLog.total_tokens), 0),
        )
        .where(RequestLog.created_at >= since, RequestLog.model_used != "")
        .group_by(RequestLog.model_used)
    )
    model_stats = [
        {"model": m, "requests": c, "tokens": int(t or 0)}
        for m, c, t in model_result.all()
    ]
    return {"window_hours": WINDOW_HOURS, "logs": logs, "model_stats": model_stats}


@router.get("/stream")
async def stream_logs() -> StreamingResponse:
    """SSE：每条新日志一帧；30s 无事件则发心跳注释保活。"""

    async def generate() -> AsyncGenerator[str, None]:
        import json

        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        bus._queues.add(q)
        try:
            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=30.0)
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            bus._queues.discard(q)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
