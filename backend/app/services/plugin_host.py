"""echobird-account-engine 插件进程宿主（移植 EchoBird plugin_accounts.rs）。

协议（与 EchoBird 宿主逐字段对齐）：
1. 启动包内 bin/echobird-account-engine(.exe)，cwd=包根，环境白名单
2. 向 stdin 写一行 JSON：{"provider":<application>,"secret":<32hex>,"tools":{...}}
   （tools 为 Map：application id → {executable, launchUri, roamingDataDir, metadata}，
   四字段可为 null；roamingDataDir 仅 Windows 推导）
3. 从 stdout 读一行（≤4KB、20s 超时）：{"protocol":1,"port":<int>}
4. 调用：POST http://127.0.0.1:{port}/operation
   头 authorization: <secret>（无 Bearer 前缀），body {"operation","params"}，
   超时 120s；连接失败/401 停掉 worker，下次调用自动重建
5. stdin EOF 时引擎自行退出；宿主维护 loginId → application 映射
"""
import asyncio
import json
import os
import secrets
import sys
import time
import uuid

import httpx

from app.services import plugin_manager

HANDSHAKE_TIMEOUT = 20.0
CALL_TIMEOUT = 120.0
MAX_RESPONSE_BYTES = 1024 * 1024

# Windows 下引擎可能需要系统目录与用户目录定位本地客户端凭据
_ENV_WHITELIST = ["SystemRoot", "windir", "PATH", "HOME", "USERPROFILE", "APPDATA",
                  "LOCALAPPDATA", "PROGRAMDATA", "TEMP", "TMP", "COMSPEC", "CLAUDE_CONFIG_DIR",
                  "LANG", "LC_ALL", "TERM"]


class PluginError(Exception):
    def __init__(self, message: str, status: int = 0, code: str = ""):
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code


class Worker:
    def __init__(self, application: str, process: asyncio.subprocess.Process,
                 port: int, secret: str, binary: str):
        self.application = application
        self.process = process
        self.port = port
        self.secret = secret
        self.binary = binary
        self.created_at = time.time()

    async def call(self, operation: str, params: dict) -> dict:
        if self.process.returncode is not None:
            raise PluginError("plugin worker exited", code="worker_dead")
        url = f"http://127.0.0.1:{self.port}/operation"
        body = {"operation": operation, "params": params or {}}
        try:
            async with httpx.AsyncClient(timeout=CALL_TIMEOUT, trust_env=False) as client:
                resp = await client.post(url, json=body, headers={"authorization": self.secret})
        except httpx.HTTPError as e:
            raise PluginError(f"plugin transport failed: {str(e)[:150]}", code="network")
        if resp.status_code == 401:
            raise PluginError("Extension authentication failed", status=401, code="auth")
        if resp.status_code >= 400:
            detail = ""
            try:
                data = resp.json()
                err = data.get("error")
                detail = (err.get("message") if isinstance(err, dict) else str(err)) or str(data)[:150]
            except Exception:
                detail = f"HTTP {resp.status_code}"
            raise PluginError(str(detail)[:300], status=resp.status_code, code="upstream")
        try:
            data = resp.json()
        except ValueError:
            raise PluginError("plugin returned non-JSON response", code="invalid")
        if isinstance(data, dict) and isinstance(data.get("error"), dict):
            err = data["error"]
            raise PluginError(str(err.get("message") or "plugin error")[:300],
                              status=int(err.get("status") or 0),
                              code=str(err.get("code") or ""))
        return data


_workers: dict[str, Worker] = {}
_starting: set[str] = set()
_logins: dict[str, str] = {}  # loginId -> application（EchoBird LOGINS 表对应物）


def _tools_payload(application: str) -> dict:
    """tools Map：覆盖该 application 的插件 providers；本机客户端未装则字段为 null。"""
    tools: dict[str, dict] = {}
    for record in plugin_manager.installed_plugins().values():
        if not record.get("enabled"):
            continue
        for app in plugin_manager.applications_of_installed(record):
            tools.setdefault(app, {
                "executable": _tool_executable(app),
                "launchUri": None,
                "roamingDataDir": _msix_roaming_dir(app) if os.name == "nt" else None,
                "metadata": None,
            })
    tools.setdefault(application, {"executable": _tool_executable(application),
                                   "launchUri": None, "roamingDataDir": None, "metadata": None})
    return tools


_TOOL_EXE_HINTS = {
    "claudecode": ["claude"],
    "claudedesktop": [],
    "workbuddy": ["codebuddy", "workbuddy"],
    "workbuddyai": ["workbuddy"],
    "trae": ["trae"],
    "traecn": ["trae"],
    "doubaowork": [],
    "dola": [],
}

# 桌面客户端 exe 检测模式（与 EchoBird tools/<id>/paths.json 的 win32 executablePaths 对齐）
_CLIENT_EXE_PATTERNS = {
    "claudedesktop": ["%LOCALAPPDATA%/AnthropicClaude/Claude.exe", "%LOCALAPPDATA%/Programs/Claude/Claude.exe"],
    "doubaowork": ["%LOCALAPPDATA%/DoubaoWork/Application/DoubaoWork.exe",
                   "%LOCALAPPDATA%/DoubaoWork/Application/app/DoubaoWork.exe"],
    "dola": ["%LOCALAPPDATA%/Cici/Application/Cici.exe", "%LOCALAPPDATA%/Cici/Application/app/Cici.exe"],
    "trae": ["%LOCALAPPDATA%/Programs/Trae/Trae.exe"],
    "traecn": ["%LOCALAPPDATA%/Programs/Trae CN/Trae CN.exe", "%LOCALAPPDATA%/Programs/Trae-CN/Trae CN.exe"],
}


def _detect_client_executable(application: str) -> str | None:
    """按 EchoBird paths.json 的 win32 模式探测已装桌面客户端。"""
    import glob as _glob
    for pattern in _CLIENT_EXE_PATTERNS.get(application, []):
        expanded = os.path.expandvars(pattern)
        matches = _glob.glob(expanded) or ([expanded] if os.path.isfile(expanded) else [])
        for path in matches:
            if os.path.isfile(path):
                return path
    return None


def _tool_executable(application: str) -> str | None:
    """本机客户端可执行路径（EchoBird get_tool_exe_path 对应物）：先 CLI 再桌面端模式。"""
    for hint in _TOOL_EXE_HINTS.get(application) or []:
        path = shutil_which(hint)
        if path:
            return path
    return _detect_client_executable(application)


def shutil_which(name: str) -> str | None:
    from shutil import which
    return which(name)


def _msix_roaming_dir(application: str) -> str | None:
    """EchoBird Windows MSIX roaming 目录推导；这里保守返回 None（引擎可自行回退）。"""
    return None


def _whitelisted_env() -> dict:
    env = {}
    for key in _ENV_WHITELIST:
        value = os.environ.get(key)
        if value is not None:
            env[key] = value
    return env


async def _spawn(application: str) -> Worker:
    binary = plugin_manager.find_engine_binary(application)
    if not binary:
        raise PluginError("没有已安装的账号插件引擎，请先在插件卡片安装", code="not_installed")
    secret = secrets.token_hex(16) + secrets.token_hex(16)  # 32 hex（EchoBird 双 uuid.simple 拼接等长）
    try:
        process = await asyncio.create_subprocess_exec(
            binary,
            cwd=os.path.dirname(binary) or None,
            env=_whitelisted_env(),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except OSError as e:
        raise PluginError(f"启动插件引擎失败: {str(e)[:150]}", code="spawn")

    handshake = {"provider": application, "secret": secret, "tools": _tools_payload(application)}
    try:
        assert process.stdin and process.stdout
        process.stdin.write((json.dumps(handshake, separators=(",", ":")) + "\n").encode("utf-8"))
        await process.stdin.drain()
        line = await asyncio.wait_for(process.stdout.readline(), timeout=HANDSHAKE_TIMEOUT)
        line = line[:4097]
        ready = json.loads(line.decode("utf-8", "replace"))
        if ready.get("protocol") != 1:
            raise PluginError("Unsupported account extension protocol", code="protocol")
        port = int(ready.get("port") or 0)
        if port <= 0:
            raise PluginError("Invalid account extension port", code="protocol")
    except PluginError:
        await _terminate(process)
        raise
    except asyncio.TimeoutError:
        await _terminate(process)
        raise PluginError("插件引擎握手超时", code="handshake_timeout")
    except Exception as e:
        await _terminate(process)
        raise PluginError(f"插件引擎握手失败: {str(e)[:150]}", code="handshake")
    return Worker(application, process, port, secret, binary)


async def _terminate(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    try:
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=5)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
    except ProcessLookupError:
        pass


def stop(application: str) -> None:
    """停掉某个 application 的 worker；无事件循环时 terminate 同步发出。"""
    worker = _workers.pop(application, None)
    if not worker:
        return
    try:
        worker.process.terminate()
    except ProcessLookupError:
        pass
    try:
        asyncio.get_running_loop().create_task(_terminate(worker.process))
    except RuntimeError:
        pass  # 应用退出阶段；terminate 已发出，进程由 OS 回收


async def stop_all() -> None:
    for application in list(_workers.keys()):
        stop(application)
    _logins.clear()


def worker_for(application: str) -> Worker | None:
    worker = _workers.get(application)
    if worker and worker.process.returncode is None:
        return worker
    if worker:
        _workers.pop(application, None)
    return None


async def get_worker(application: str) -> Worker:
    existing = worker_for(application)
    if existing:
        return existing
    if application in _starting:
        # 简单等待并发启动完成
        for _ in range(100):
            await asyncio.sleep(0.1)
            existing = worker_for(application)
            if existing:
                return existing
        raise PluginError("插件引擎启动超时", code="starting")
    _starting.add(application)
    try:
        worker = await _spawn(application)
        _workers[application] = worker
        return worker
    finally:
        _starting.discard(application)


async def call(application: str, operation: str, params: dict | None = None) -> dict | list:
    """调用插件 operation；传输失败/鉴权失败自动停掉 worker（下次调用重建）。"""
    try:
        worker = await get_worker(application)
        return await worker.call(operation, params or {})
    except PluginError as e:
        if e.code in ("network", "auth", "worker_dead"):
            stop(application)
        raise
    except Exception:
        stop(application)
        raise


async def raw_operation(application: str, operation: str, params: dict, timeout: float = 180.0):
    """调用插件 operation 并返回原始响应 (status, content_type, body bytes)。

    供 proxy 这类 SSE/大响应操作使用：插件返回 OpenAI 格式 JSON 或 SSE 流，
    原样中继给下游客户端（EchoBird host 同款语义）。
    """
    worker = await get_worker(application)
    if worker.process.returncode is not None:
        stop(application)
        raise PluginError("plugin worker exited", code="worker_dead")
    url = f"http://127.0.0.1:{worker.port}/operation"
    try:
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            resp = await client.post(url, json={"operation": operation, "params": params},
                                     headers={"authorization": worker.secret})
    except httpx.HTTPError as e:
        stop(application)
        raise PluginError(f"plugin transport failed: {str(e)[:150]}", code="network")
    if resp.status_code == 401:
        stop(application)
        raise PluginError("Extension authentication failed", status=401, code="auth")
    return resp.status_code, resp.headers.get("content-type", "application/json"), resp.content


def register_login(login_id: str, application: str) -> None:
    _logins[login_id] = application


def login_application(login_id: str) -> str | None:
    return _logins.get(login_id)


def forget_login(login_id: str) -> None:
    _logins.pop(login_id, None)


def status() -> dict:
    out = {}
    for application, worker in _workers.items():
        out[application] = {
            "port": worker.port,
            "binary": worker.binary,
            "alive": worker.process.returncode is None,
            "uptime": int(time.time() - worker.created_at),
        }
    return out
