from sqlalchemy import Column, Integer, String, Boolean, Text, Float, DateTime, func
from app.database import Base


class ScheduledTask(Base):
    """定时执行脚本插件的任务定义（带执行参数与账号）。"""
    __tablename__ = "scheduled_tasks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(120), default="", nullable=False)
    plugin_id = Column(String(120), default="", nullable=False, index=True)
    # JSON 字符串：插件执行 params（不含账号注入项）
    params_json = Column(Text, default="", nullable=False)
    # JSON 字符串：账号 id 列表
    account_ids_json = Column(Text, default="", nullable=False)
    all_accounts = Column(Boolean, default=False, nullable=False)
    # cron | interval | once
    schedule_type = Column(String(16), default="cron", nullable=False)
    cron_expr = Column(String(120), default="", nullable=False)   # 5 段 cron
    interval_minutes = Column(Integer, default=60, nullable=False)
    run_at = Column(String(32), default="", nullable=False)       # once: "YYYY-MM-DD HH:MM"
    enabled = Column(Boolean, default=True, nullable=False)
    last_run_at = Column(DateTime, nullable=True)
    next_run_at = Column(String(64), default="", nullable=False)
    last_ok = Column(Boolean, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    def as_dict(self) -> dict:
        def _fmt(v):
            return v.isoformat(sep=" ", timespec="seconds") if v else None
        return {
            "id": self.id,
            "name": self.name,
            "plugin_id": self.plugin_id,
            "params": self.params_json or "",
            "account_ids": self.account_ids_json or "",
            "all_accounts": bool(self.all_accounts),
            "schedule_type": self.schedule_type,
            "cron_expr": self.cron_expr or "",
            "interval_minutes": int(self.interval_minutes or 0),
            "run_at": self.run_at or "",
            "enabled": bool(self.enabled),
            "last_run_at": _fmt(self.last_run_at),
            "next_run_at": self.next_run_at or "",
            "last_ok": self.last_ok,
            "created_at": _fmt(self.created_at),
            "updated_at": _fmt(self.updated_at),
        }


class ScheduledTaskRun(Base):
    """定时任务每次执行（逐账号）的结果日志。"""
    __tablename__ = "scheduled_task_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(Integer, nullable=False, index=True)
    task_name = Column(String(120), default="", nullable=False)
    plugin_id = Column(String(120), default="", nullable=False)
    trigger = Column(String(16), default="cron", nullable=False)  # cron|interval|once|manual
    account_id = Column(String(120), default="", nullable=False)
    account_name = Column(String(120), default="", nullable=False)
    ok = Column(Boolean, default=False, nullable=False)
    result = Column(Text, default="", nullable=False)
    error = Column(String(500), default="", nullable=False)
    elapsed = Column(Float, default=0.0, nullable=False)
    fired_at = Column(DateTime, server_default=func.now())

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "task_id": self.task_id,
            "task_name": self.task_name,
            "plugin_id": self.plugin_id,
            "trigger": self.trigger,
            "account_id": self.account_id,
            "account_name": self.account_name,
            "ok": bool(self.ok),
            "result": self.result or "",
            "error": self.error or "",
            "elapsed": float(self.elapsed or 0),
            "fired_at": self.fired_at.isoformat(sep=" ", timespec="seconds") if self.fired_at else None,
        }
