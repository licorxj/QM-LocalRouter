"""脚本插件 API：在线目录 / 安装任务 / 启停 / 卸载 / 执行 / 审计。"""
import asyncio
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services import script_plugin_manager as mgr

router = APIRouter(prefix="/api/script-plugins", tags=["script-plugins"])


class InstallBody(BaseModel):
    plugin_id: str
    # 可选：直接从给定 URL 安装（自定义源 / 本地测试）
    download_url: str | None = None
    sha256: str | None = None


class RunBody(BaseModel):
    params: dict | None = None
    account_ids: list[str] | None = None  # 选定账号 id；为空/不传则走插件自身凭据
    all_accounts: bool = False           # 全选项目内已登录 workbuddy 账号
    accounts_only: bool = False          # 仅返回账号列表（不执行）


class SettingsBody(BaseModel):
    values: dict | None = None


class TaskState:
    def __init__(self):
        self.phase = "idle"   # idle|running|success|error
        self.plugin_id = ""
        self.message = ""
        self.error = ""
        self.updated_at = 0.0

    def as_dict(self) -> dict:
        return {"phase": self.phase, "plugin_id": self.plugin_id,
                "message": self.message, "error": self.error,
                "updated_at": self.updated_at}


_task = TaskState()
_task_runner: asyncio.Task | None = None


@router.get("")
async def list_plugins():
    """本地已装 + 在线可用 + 目录同步状态。"""
    installed = mgr.installed_plugins()
    installed_ids = {p["id"] for p in installed}
    try:
        entries = await mgr.fetch_catalog()
        online = mgr.online_plugins(entries)
        # 在线列表里排除已装的同 id（前端「在线」页显示未安装项）
        online_available = [p for p in online if p["id"] not in installed_ids]
    except Exception:
        online = []
        online_available = []
    from app.services import apiserver_manager as svc
    svc_status = {
        p["id"]: svc.status(p["id"])
        for p in installed if p.get("type") == "apiserver"
    }
    return {
        "installed": installed,
        "online": online,
        "online_available": online_available,
        "catalog": mgr.catalog_info(),
        "task": _task.as_dict(),
        "service_status": svc_status,
    }


@router.post("/check")
async def check_catalog():
    """强制刷新在线目录。"""
    try:
        entries = await mgr.fetch_catalog(force=True)
    except Exception as e:
        raise HTTPException(502, f"拉取在线插件目录失败: {str(e)[:150]}")
    return {"ok": True, "plugins": mgr.online_plugins(entries), "catalog": mgr.catalog_info()}


@router.post("/install", status_code=202)
async def install_plugin(body: InstallBody):
    """安装插件（后台任务）。指定 download_url 时直接从该 URL 安装。"""
    global _task_runner
    if _task.phase == "running":
        raise HTTPException(409, "已有插件任务进行中，请稍候")
    _task.phase = "running"
    _task.plugin_id = body.plugin_id
    _task.message = ""
    _task.error = ""
    _task.updated_at = asyncio.get_running_loop().time()

    async def _run():
        try:
            url = (body.download_url or "").strip()
            sha = (body.sha256 or "").strip()
            if not url:
                entries = await mgr.fetch_catalog(force=True)
                entries = mgr._filter_entries_for_platform(entries)
                entry = next((e for e in entries if e.get("id") == body.plugin_id), None)
                if not entry:
                    raise ValueError(f"在线目录中未找到插件: {body.plugin_id}（或当前系统平台不支持）")
                url = str(entry.get("download_url") or "")
                sha = sha or str(entry.get("sha256") or "")
            if not url:
                raise ValueError("该插件没有下载地址")
            rec = await mgr.install_from_url(url, expected_sha256=sha, plugin_id=body.plugin_id)
            _task.phase = "success"
            _task.message = f"{rec['name']} v{rec['version']}"
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
        rec = mgr.set_enabled(plugin_id, True)
    except ValueError as e:
        raise HTTPException(404, str(e))
    svc_status = None
    if rec.get("type") == "apiserver":
        from app.services import apiserver_manager as svc
        try:
            full = mgr.get_installed(plugin_id)
            await svc.start(full)
            svc_status = svc.status(plugin_id)
        except Exception as e:
            svc_status = {"running": False, "error": str(e)[:200]}
    return {"ok": True, "plugin": rec, "service": svc_status}


@router.post("/{plugin_id}/disable")
async def disable_plugin(plugin_id: str):
    try:
        rec = mgr.set_enabled(plugin_id, False)
    except ValueError as e:
        raise HTTPException(404, str(e))
    svc_status = None
    if rec.get("type") == "apiserver":
        from app.services import apiserver_manager as svc
        svc_status = await svc.stop(plugin_id)
    return {"ok": True, "plugin": rec, "service": svc_status}


@router.post("/{plugin_id}/service-restart")
async def plugin_service_restart(plugin_id: str):
    """重启 apiserver 型插件的常驻服务（先停后起）。"""
    rec = mgr.get_installed(plugin_id)
    if not rec:
        raise HTTPException(404, "插件未安装")
    if rec.get("type") != "apiserver":
        raise HTTPException(400, "该插件不是 apiserver 服务型")
    from app.services import apiserver_manager as svc
    await svc.stop(plugin_id)
    try:
        return await svc.start(mgr.get_installed(plugin_id))
    except Exception as e:
        raise HTTPException(500, f"重启服务失败: {str(e)[:200]}")


@router.get("/{plugin_id}/service-status")
async def plugin_service_status(plugin_id: str):
    """apiserver 型插件的运行状态（普通插件返回 running=false）。"""
    rec = mgr.get_installed(plugin_id)
    if not rec:
        raise HTTPException(404, "插件未安装")
    if rec.get("type") != "apiserver":
        return {"running": False, "url": "", "platform": ""}
    from app.services import apiserver_manager as svc
    return svc.status(plugin_id)


@router.delete("/{plugin_id}")
async def uninstall_plugin(plugin_id: str):
    try:
        mgr.uninstall(plugin_id)
    except ValueError as e:
        raise HTTPException(404, str(e))
    return {"ok": True}


@router.get("/{plugin_id}/settings")
async def get_plugin_settings(plugin_id: str):
    """返回插件设置 schema 与当前值。"""
    try:
        return {"ok": True, **mgr.get_settings(plugin_id)}
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.put("/{plugin_id}/settings")
async def save_plugin_settings(plugin_id: str, body: SettingsBody):
    """按 schema 校验并保存插件设置。"""
    try:
        return {"ok": True, **mgr.save_settings(plugin_id, body.values)}
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/{plugin_id}/run")
async def run_plugin(plugin_id: str, body: RunBody | None = None):
    """执行插件脚本（线程池 + 超时控制），返回结构化结果。"""
    params = (body.params if body else None) or {}
    # 注入主事件循环引用：插件内若需在共享 httpx client 上跑协程，必须回到其绑定的 loop
    # （否则 asyncio.run 新 loop 复用共享 client 会报 "Event loop is closed"）
    params = {**params, "__main_loop__": asyncio.get_running_loop()}
    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(mgr.run_plugin, plugin_id, params),
            timeout=mgr.RUN_TIMEOUT,
        )
    except asyncio.TimeoutError:
        raise HTTPException(504, f"插件执行超时（>{mgr.RUN_TIMEOUT}s）")
    return result


@router.post("/{plugin_id}/run-async", status_code=202)
async def run_plugin_async(plugin_id: str, body: RunBody | None = None):
    """后台单次执行插件（长超时），提交立即返回 task_id。

    用于本地 UI 自动化类插件（如 zcode 签到，单次可达 240s），
    普通 /run 端点 120s 会超时。进度复用 GET /run-task/{task_id} 轮询。
    """
    params = (body.params if body else None) or {}
    task_id = uuid.uuid4().hex
    state = {
        "task_id": task_id,
        "plugin_id": plugin_id,
        "phase": "running",
        "total": 1,
        "done": 0,
        "current": "",
        "results": [],
        "error": "",
        "mode": "single",
        "updated_at": asyncio.get_running_loop().time(),
    }
    _batch_tasks[task_id] = state

    async def _run():
        loop = asyncio.get_running_loop()
        try:
            res = await asyncio.wait_for(
                asyncio.to_thread(mgr.run_plugin, plugin_id, params),
                timeout=360,
            )
            state["results"].append({
                "name": plugin_id,
                "ok": bool(res.get("ok")),
                "result": res.get("result"),
                "error": res.get("error"),
                "logs": res.get("logs"),
            })
        except asyncio.TimeoutError:
            state["results"].append({"name": plugin_id, "ok": False, "error": "执行超时（>360s）"})
        except Exception as e:
            state["results"].append({"name": plugin_id, "ok": False, "error": str(e)[:200]})
        state["done"] = 1
        state["phase"] = "success"
        state["current"] = ""
        state["updated_at"] = loop.time()

    asyncio.get_running_loop().create_task(_run())
    return {"ok": True, "task_id": task_id, "total": 1}


@router.get("/audit")
async def get_audit(limit: int = 100):
    """最近的审计日志（倒序）。"""
    return {"records": mgr.read_audit(limit)}


# --- 项目内已登录账号（批量执行源：WorkBuddy / ZCode / Qoder） -------------
from app.services import workbuddy_accounts as _wb_accounts
from app.services import zcode_accounts as _zc_accounts
from app.services import qoder_accounts as _qd_accounts

# 批量执行任务表：task_id -> state dict（进程内，重启后清空）
_batch_tasks: dict[str, dict] = {}

# 各插件使用的账号源：workbuddy-growth -> workbuddy；zcode-claim -> zcode；qoder-claim -> qoder echo
_ACCOUNT_SOURCE = {
    "workbuddy-growth": "workbuddy",
    "zcode-claim": "zcode",
    "qoder-claim": "qoder",
}


def _account_source(plugin_id: str) -> str:
    return _ACCOUNT_SOURCE.get(plugin_id, "workbuddy")


def _list_plugin_accounts(plugin_id: str) -> list[dict]:
    source = _account_source(plugin_id)
    if source == "zcode":
        return _zc_accounts.list_accounts()
    if source == "qoder":
        return _qd_accounts.list_accounts()
    return _wb_accounts.list_accounts()


@router.get("/workbuddy-accounts")
async def list_workbuddy_accounts():
    """返回项目内已登录的 WorkBuddy 账号（脱敏，无 token）。"""
    try:
        accounts = _wb_accounts.list_accounts()
    except Exception as e:
        raise HTTPException(500, f"读取本地账号失败: {str(e)[:150]}")
    return {"ok": True, "accounts": accounts}


@router.get("/zcode-accounts")
async def list_zcode_accounts():
    """返回项目内已登录的 ZCode 账号（脱敏，无 token）。"""
    try:
        accounts = _zc_accounts.list_accounts()
    except Exception as e:
        raise HTTPException(500, f"读取 ZCode 账号失败: {str(e)[:150]}")
    return {"ok": True, "accounts": accounts}


@router.get("/plugin-accounts")
async def list_plugin_accounts(plugin_id: str):
    """按插件返回其对应的账号源（脱敏），供执行弹窗动态展示。"""
    try:
        accounts = _list_plugin_accounts(plugin_id)
    except Exception as e:
        raise HTTPException(500, f"读取账号失败: {str(e)[:150]}")
    return {"ok": True, "source": _account_source(plugin_id), "accounts": accounts}


def _resolve_batch_account_ids(body: "RunBody", plugin_id: str) -> list[str]:
    """根据 all_accounts / account_ids 解析最终账号列表（按插件账号源）。"""
    all_accs = _list_plugin_accounts(plugin_id)
    valid = {a["id"] for a in all_accs}
    if body.all_accounts:
        return [a["id"] for a in all_accs]
    ids = body.account_ids or []
    return [i for i in ids if i in valid]


@router.post("/{plugin_id}/run-batch", status_code=202)
async def run_plugin_batch(plugin_id: str, body: RunBody | None = None):
    """后台串行执行：对每个选定账号注入其 token/uid 后依次运行插件。

    提交立即返回 task_id，前端轮询 GET /run-task/{task_id} 看进度。
    """
    body = body or RunBody()
    if not (body.all_accounts or (body.account_ids)):
        raise HTTPException(400, "未选择任何账号")
    ids = _resolve_batch_account_ids(body, plugin_id)
    if not ids:
        raise HTTPException(400, "所选账号无效或已登出")
    source = _account_source(plugin_id)
    task_id = uuid.uuid4().hex
    base_params = dict(body.params or {})
    base_params["__main_loop__"] = asyncio.get_running_loop()
    state = {
        "task_id": task_id,
        "plugin_id": plugin_id,
        "phase": "running",
        "total": len(ids),
        "done": 0,
        "current": "",
        "results": [],
        "error": "",
        "updated_at": asyncio.get_running_loop().time(),
    }
    _batch_tasks[task_id] = state

    async def _run():
        loop = asyncio.get_running_loop()
        for aid in ids:
            state["current"] = aid[:8]
            state["updated_at"] = loop.time()
            try:
                acc = next((a for a in _list_plugin_accounts(plugin_id) if a["id"] == aid), None)
                name = acc["name"] if acc else aid[:8]
            except Exception:
                name = aid[:8]
            try:
                params = dict(base_params)
                if source == "zcode":
                    # zcode-claim 插件通过 credentials.read 自行读 JWT，这里只传 key_id
                    params["key_id"] = int(aid) if str(aid).isdigit() else aid
                elif source == "qoder":
                    # qoder-claim 插件在进程内通过 native_accounts 读 echo 存储；传 platform|account_id
                    platform, account_id = _qd_accounts.parse_id(aid)
                    params["platform"] = platform
                    params["account_id"] = account_id
                else:
                    creds = _wb_accounts.resolve_credentials(aid)
                    params["token"] = creds["token"]
                    params["uid"] = creds["uid"]
                    if creds.get("domain"):
                        params["domain"] = creds["domain"]
                    if creds.get("enterprise_id"):
                        params["enterprise_id"] = creds["enterprise_id"]
                try:
                    res = await asyncio.wait_for(
                        asyncio.to_thread(mgr.run_plugin, plugin_id, params),
                        timeout=mgr.RUN_TIMEOUT,
                    )
                except asyncio.TimeoutError:
                    res = {"ok": False, "error": f"执行超时（>{mgr.RUN_TIMEOUT}s）"}
                state["results"].append({
                    "account_id": aid[:8], "name": name,
                    "ok": bool(res.get("ok")), "result": res.get("result"),
                    "error": res.get("error"), "logs": res.get("logs"),
                })
            except Exception as e:
                state["results"].append({
                    "account_id": aid[:8], "name": name,
                    "ok": False, "error": str(e)[:200],
                })
            state["done"] += 1
            state["updated_at"] = loop.time()
        state["phase"] = "success"
        state["current"] = ""
        state["updated_at"] = loop.time()

    asyncio.get_running_loop().create_task(_run())
    return {"ok": True, "task_id": task_id, "total": len(ids)}


@router.get("/run-task/{task_id}")
async def get_run_task(task_id: str):
    """查询批量执行任务进度与逐账号结果。"""
    state = _batch_tasks.get(task_id)
    if not state:
        raise HTTPException(404, "任务不存在或已过期")
    return dict(state)
