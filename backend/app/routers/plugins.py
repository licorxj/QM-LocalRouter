"""插件中心 API：echobird-plugin 索引 / 安装更新 / 启停 / 设置。"""
import asyncio

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services import plugin_host
from app.services import plugin_manager

router = APIRouter(prefix="/api/plugins", tags=["plugins"])


class PluginSettings(BaseModel):
    region: str = ""        # auto / china / cnb / international
    auto_update: bool | None = None


class PluginTaskState:
    """安装/更新后台任务状态（仿 app_update.UpdateState）。"""

    def __init__(self):
        self.phase = "idle"  # idle|running|success|error
        self.plugin_id = ""
        self.action = ""     # install|update
        self.message = ""
        self.error = ""
        self.updated_at = 0.0

    def as_dict(self) -> dict:
        return {"phase": self.phase, "plugin_id": self.plugin_id, "action": self.action,
                "message": self.message, "error": self.error, "updated_at": self.updated_at}


_task = PluginTaskState()
_task_runner: asyncio.Task | None = None


def _snapshot() -> dict:
    installed = plugin_manager.installed_plugins()
    installed_out = []
    for pid, record in installed.items():
        installed_out.append({
            "id": pid,
            "name": record.get("name"),
            "display_name": record.get("display_name") or pid,
            "version": record.get("version"),
            "enabled": bool(record.get("enabled", True)),
            "installed_at": record.get("installed_at"),
            "applications": plugin_manager.applications_of_installed(record),
        })
    updates = plugin_manager.check_updates()
    return {
        "platform": plugin_manager.current_platform(),
        "installed": installed_out,
        "updates": updates,
        "sync": {**plugin_manager.last_sync_info(), "has_index": plugin_manager._index_cache is not None},
        "task": _task.as_dict(),
        "worker_status": plugin_host.status(),
    }


@router.get("")
async def list_plugins():
    """已装 + 可用 + 更新状态（索引未拉到时 available 为空但 sync.error 说明原因）。"""
    try:
        index = await plugin_manager.fetch_index()
        available = plugin_manager.available_plugins(index)
    except Exception as e:
        available = []
    snapshot = _snapshot()
    for item in available:
        record = plugin_manager.get_installed(item["id"] or "")
        item["installed_version"] = record.get("version") if record else None
        item["enabled"] = bool(record.get("enabled", True)) if record else False
        update = next((u for u in snapshot["updates"] if u["id"] == item["id"]), None)
        item["update_available"] = bool(update)
        item["latest_version"] = (update or {}).get("latest_version") or item["version"]
    snapshot["available"] = available
    from app.routers.settings import _get_settings
    settings = _get_settings()
    snapshot["region"] = settings.get("plugins_region") or "auto"
    snapshot["auto_update"] = bool(settings.get("plugins_auto_update", False))
    return snapshot


@router.post("/check")
async def check_updates():
    """强制刷新上游索引并比对版本（页面「检查更新」按钮）。"""
    try:
        await plugin_manager.fetch_index(force=True)
    except Exception as e:
        raise HTTPException(502, f"拉取插件索引失败: {str(e)[:150]}")
    return await list_plugins()


@router.post("/{plugin_id}/install", status_code=202)
async def install_plugin(plugin_id: str):
    """安装 / 更新（后台任务；同一时间只允许一个任务）。"""
    global _task_runner
    if _task.phase == "running":
        raise HTTPException(409, "已有插件任务进行中，请稍候")
    _task.phase = "running"
    _task.plugin_id = plugin_id
    _task.action = "update" if plugin_manager.get_installed(plugin_id) else "install"
    _task.message = ""
    _task.error = ""
    _task.updated_at = asyncio.get_running_loop().time()

    async def _run():
        try:
            record = await plugin_manager.install_plugin(plugin_id)
            _task.phase = "success"
            _task.message = f"{record.get('display_name') or plugin_id} {record.get('version')}"
        except Exception as e:
            _task.phase = "error"
            _task.error = str(e)[:300]
        _task.updated_at = asyncio.get_running_loop().time()

    _task_runner = asyncio.get_running_loop().create_task(_run())
    return {"ok": True, "task": _task.as_dict()}


@router.get("/task")
async def task_status():
    return _task.as_dict()


@router.post("/{plugin_id}/enable")
async def enable_plugin(plugin_id: str):
    try:
        record = plugin_manager.set_enabled(plugin_id, True)
    except ValueError as e:
        raise HTTPException(404, str(e))
    return {"ok": True, "record": {**record, "manifest": {}}}


@router.post("/{plugin_id}/disable")
async def disable_plugin(plugin_id: str):
    try:
        record = plugin_manager.set_enabled(plugin_id, False)
    except ValueError as e:
        raise HTTPException(404, str(e))
    return {"ok": True, "record": {**record, "manifest": {}}}


@router.delete("/{plugin_id}")
async def uninstall(plugin_id: str):
    try:
        plugin_manager.uninstall_plugin(plugin_id)
    except ValueError as e:
        raise HTTPException(404, str(e))
    return {"ok": True}


@router.put("/settings")
async def update_settings(data: PluginSettings):
    from app.routers.settings import _get_settings, _save_settings
    settings = _get_settings()
    if data.region and data.region in ("auto", "china", "cnb", "international"):
        settings["plugins_region"] = data.region
    if data.auto_update is not None:
        settings["plugins_auto_update"] = bool(data.auto_update)
    _save_settings(settings)
    return {"ok": True, "region": settings.get("plugins_region", "auto"),
            "auto_update": bool(settings.get("plugins_auto_update", False))}
