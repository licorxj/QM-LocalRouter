from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, case
from datetime import datetime
from app.database import get_db
from app.models.log import RequestLog
from app.models.strategy import Strategy
from app.models.provider import Provider
from app.models.api_key import ApiKey
from app.schemas.schemas import DashboardStats

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/stats", response_model=DashboardStats)
async def get_stats(db: AsyncSession = Depends(get_db)):
    today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)

    # 单条 SQL 聚合全部今日指标：此前为 10 次串行 db.scalar()（每次一次往返），
    # 前端 Dashboard 每 5s 轮询一次，串行查询在并发下会放大延迟。合并为一次往返。
    today_logs = select(RequestLog).where(RequestLog.created_at >= today).subquery()
    row = (await db.execute(
        select(
            func.count(today_logs.c.id).label("total"),
            func.coalesce(func.sum(case((today_logs.c.status_code.between(200, 299), 1), else_=0)), 0).label("success"),
            func.coalesce(func.avg(today_logs.c.latency_ms), 0).label("avg_lat"),
            func.coalesce(func.sum(today_logs.c.prompt_tokens), 0).label("prompt_sum"),
            func.coalesce(func.sum(today_logs.c.completion_tokens), 0).label("completion_sum"),
            func.coalesce(func.sum(today_logs.c.total_tokens), 0).label("total_tok"),
            select(func.count()).select_from(Strategy).where(Strategy.is_active == True).scalar_subquery().label("active_strats"),
            select(func.count()).select_from(Provider).where(Provider.is_active == True).scalar_subquery().label("active_provs"),
            select(func.count()).select_from(ApiKey).where(ApiKey.status == "active").scalar_subquery().label("active_keys"),
        ).select_from(today_logs)
    )).one()

    total = row.total or 0
    success = row.success or 0
    errors = total - success

    return DashboardStats(
        total_requests=total,
        success_count=success,
        error_count=errors,
        success_rate=round(success / total * 100, 1) if total > 0 else 0.0,
        avg_latency_ms=round(row.avg_lat or 0, 1),
        total_prompt_tokens=row.prompt_sum or 0,
        total_completion_tokens=row.completion_sum or 0,
        total_tokens=row.total_tok or 0,
        active_strategies=row.active_strats or 0,
        active_providers=row.active_provs or 0,
        active_keys=row.active_keys or 0,
    )


@router.post("/clear-today")
async def clear_today_stats(db: AsyncSession = Depends(get_db)):
    today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    from sqlalchemy import delete
    result = await db.execute(delete(RequestLog).where(RequestLog.created_at >= today))
    await db.commit()
    return {"deleted": result.rowcount, "message": "Today's stats cleared"}
