"""add scheduled tasks and run logs

Revision ID: a7c3e91f4d20
Revises: 5d9471683c15
Create Date: 2026-11-20
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "a7c3e91f4d20"
down_revision = "5d9471683c15"
branch_labels = None
depends_on = None


def _has(table: str) -> bool:
    bind = op.get_bind()
    return inspect(bind).has_table(table)


def upgrade():
    """幂等：已有库会先 create_all 建表，这里只在缺失时补建。"""
    if not _has("scheduled_tasks"):
        op.create_table(
            "scheduled_tasks",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("name", sa.String(120), nullable=False, server_default=""),
            sa.Column("plugin_id", sa.String(120), nullable=False, server_default=""),
            sa.Column("params_json", sa.Text(), nullable=False, server_default=""),
            sa.Column("account_ids_json", sa.Text(), nullable=False, server_default=""),
            sa.Column("all_accounts", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("schedule_type", sa.String(16), nullable=False, server_default="cron"),
            sa.Column("cron_expr", sa.String(120), nullable=False, server_default=""),
            sa.Column("interval_minutes", sa.Integer(), nullable=False, server_default="60"),
            sa.Column("run_at", sa.String(32), nullable=False, server_default=""),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("last_run_at", sa.DateTime(), nullable=True),
            sa.Column("next_run_at", sa.String(64), nullable=False, server_default=""),
            sa.Column("last_ok", sa.Boolean(), nullable=True),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
        )
        op.create_index("ix_scheduled_tasks_plugin_id", "scheduled_tasks", ["plugin_id"])
    if not _has("scheduled_task_runs"):
        op.create_table(
            "scheduled_task_runs",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("task_id", sa.Integer(), nullable=False),
            sa.Column("task_name", sa.String(120), nullable=False, server_default=""),
            sa.Column("plugin_id", sa.String(120), nullable=False, server_default=""),
            sa.Column("trigger", sa.String(16), nullable=False, server_default="cron"),
            sa.Column("account_id", sa.String(120), nullable=False, server_default=""),
            sa.Column("account_name", sa.String(120), nullable=False, server_default=""),
            sa.Column("ok", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("result", sa.Text(), nullable=False, server_default=""),
            sa.Column("error", sa.String(500), nullable=False, server_default=""),
            sa.Column("elapsed", sa.Float(), nullable=False, server_default="0"),
            sa.Column("fired_at", sa.DateTime(), server_default=sa.func.now()),
        )
        op.create_index("ix_scheduled_task_runs_task_id", "scheduled_task_runs", ["task_id"])


def downgrade():
    if _has("scheduled_task_runs"):
        op.drop_table("scheduled_task_runs")
    if _has("scheduled_tasks"):
        op.drop_table("scheduled_tasks")
