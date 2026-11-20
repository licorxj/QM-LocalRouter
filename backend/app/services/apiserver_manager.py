"""apiserver 型脚本插件的常驻进程管理。

「apiserver」是脚本插件的一种特殊类型（manifest.type == "apiserver"）：
包内自带一个编译好的 API 服务二进制（如 opencode2api）+ config 模板，
启用插件时由本模块拉起常驻进程，禁用时停止；后端重启后自动恢复所有已启用
的服务型插件。对外提供 OpenAI 兼容网关（默认 http://127.0.0.1:12005/v1），
与 free_models 的 opencode-free 渠道天然对接。

进程内状态：_procs[{plugin_id}] = {"proc": Popen, "pid", "url", "started_at", "error"}。
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from app.config import DATA_DIR

RUN_DIR = DATA_DIR / "script_plugins" / "apiserver-run"
RUN_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_PORT = 12005
_procs: dict[str, dict] = {}
_lock = None  # 延迟创建，绑定运行 loop


def _lock_obj() -> asyncio.Lock:
    global _lock
    if _lock is None:
        _lock = asyncio.Lock()
    return _lock


def _platform() -> str:
    """windows.x64 / windows.arm64 / macos.x64 / macos.arm64 / linux.x64 / linux.arm64"""
    if os.name == "nt":
        return "windows.arm64" if _is_arm() else "windows.x64"
    import platform as _p
    system = _p.system().lower()
    is_arm = _p.machine().lower() in ("arm64", "aarch64")
    if system == "darwin":
        return f"macos.{'arm64' if is_arm else 'x64'}"
    if system == "linux":
        return f"linux.{'arm64' if is_arm else 'x64'}"
    return "unknown"


def _is_arm() -> bool:
    import platform as _p
    return _p.machine().lower() in ("arm64", "aarch64")


def _parse_port(listen: str) -> int:
    try:
        return int(str(listen).rsplit(":", 1)[-1])
    except Exception:
        return DEFAULT_PORT


async def _probe(url: str) -> bool:
    """探活：/healthz 2xx/<500 视为就绪。"""
    try:
        from app.utils import ssl_relax
        resp = await ssl_relax.request("GET", url.rstrip("/").replace("/v1", "/healthz"), timeout=2.0)
        return resp.status_code < 500
    except Exception:
        return False


def _terminate_port_holder(port):
    """释放端口上残留的 opencode2api 实例（旧网关/上次崩溃残留）。

    仅终止进程名含 opencode2api 的监听者，避免误杀其他占用该端口的程序；
    返回被终止的 pid 列表。
    """
    import psutil
    freed = []
    for conn in psutil.net_connections(kind="inet"):
        if conn.status != psutil.CONN_LISTEN or not conn.laddr or conn.laddr.port != port:
            continue
        pid = conn.pid
        if not pid:
            continue
        try:
            p = psutil.Process(pid)
            if "opencode2api" not in p.name().lower():
                continue
            freed.append(pid)
            p.terminate()
            try:
                p.wait(timeout=5)
            except Exception:
                p.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return freed


def is_running(plugin_id: str) -> bool:
    st = _procs.get(plugin_id)
    if not st or not st.get("proc"):
        return False
    return st["proc"].poll() is None


def status(plugin_id: str) -> dict:
    st = _procs.get(plugin_id) or {}
    proc = st.get("proc")
    alive = bool(proc and proc.poll() is None)
    return {
        "plugin_id": plugin_id,
        "running": alive,
        "pid": int(proc.pid) if alive else None,
        "url": st.get("url") or "",
        "key": st.get("key") or "",
        "started_at": st.get("started_at"),
        "error": st.get("error") or "",
        "platform": _platform(),
    }


async def start(rec: dict) -> dict:
    """根据已装插件记录拉起服务。rec 来自 script_plugin_manager.get_installed()。"""
    plugin_id = rec["id"]
    async with _lock_obj():
        if is_running(plugin_id):
            return status(plugin_id)
        root = Path(rec["path"])
        svc = rec.get("service") or {}
        binary_rel = svc.get("binary") or ""
        config_rel = svc.get("config") or "config.json"
        if not binary_rel:
            raise RuntimeError("插件未声明 service.binary")
        binary = root / binary_rel
        if not binary.is_file():
            raise RuntimeError(f"服务二进制不存在: {binary_rel}（当前平台 {_platform()}）")

        # 配置落到插件包目录（可写即可），首次从模板复制
        cfg = root / config_rel
        cfg_tpl = root / (config_rel + ".example")
        if not cfg.is_file() and cfg_tpl.is_file():
            shutil.copyfile(cfg_tpl, cfg)
        port = DEFAULT_PORT
        key = ""
        listen = ""
        if cfg.is_file():
            try:
                data = json.loads(cfg.read_text(encoding="utf-8-sig"))
                listen = str(data.get("listen") or "")
                port = _parse_port(listen)
                keys = data.get("server_keys") or []
                key = str(keys[0]).strip() if keys else ""
            except Exception as e:
                raise RuntimeError(f"服务配置解析失败: {str(e)[:120]}")

        # 让 free_models.read_gateway_config 能发现网关：仅当仓库根没有 config.json
        # 时，把插件配置拷过去（不覆盖用户已有配置，避免丢失其 zen_keys 等）。
        try:
            repo_root = Path(__file__).resolve().parent.parent.parent.parent
            root_cfg = repo_root / "config.json"
            if not root_cfg.exists() and cfg.is_file():
                shutil.copyfile(cfg, root_cfg)
        except Exception:
            pass

        url = f"http://127.0.0.1:{port}/v1"
        # 释放端口上残留的 opencode2api 实例（例如旧的根目录网关），避免
        # 插件二进制因 "address already in use" 立即退出却被 healthz 误判成功。
        try:
            freed = await asyncio.to_thread(_terminate_port_holder, port)
            if freed:
                print(f"[apiserver] freed port {port} from stale pids: {freed}")
        except Exception:
            pass
        logf = open(RUN_DIR / f"{plugin_id}.log", "ab")
        kwargs: dict = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        try:
            proc = subprocess.Popen(
                [str(binary), "-config", str(cfg)],
                cwd=str(root), stdout=logf, stderr=subprocess.STDOUT, **kwargs,
            )
        except Exception as e:
            logf.close()
            raise RuntimeError(f"启动服务失败: {str(e)[:150]}")

        _procs[plugin_id] = {
            "proc": proc, "pid": proc.pid, "url": url, "key": key,
            "started_at": time.strftime("%Y-%m-%d %H:%M:%S"), "error": "",
        }

    # 等待就绪（不持锁）：必须先确认是「自己的」进程存活，再探活，
    # 否则会探到残留旧网关的 healthz 200 而误判成功。
    for _ in range(20):
        await asyncio.sleep(0.5)
        if proc.poll() is not None:
            _procs[plugin_id]["error"] = f"进程提前退出（code {proc.returncode}），查看服务日志"
            break
        if await _probe(url):
            _procs[plugin_id]["url"] = url
            return status(plugin_id)
    return status(plugin_id)


async def stop(plugin_id: str) -> dict:
    async with _lock_obj():
        st = _procs.pop(plugin_id, None)
        if not st:
            return status(plugin_id)
        proc = st.get("proc")
        if proc and proc.poll() is None:
            try:
                proc.terminate()
                try:
                    await asyncio.wait_for(asyncio.to_thread(proc.wait), timeout=5)
                except Exception:
                    proc.kill()
            except Exception:
                pass
    return status(plugin_id)


async def recover_enabled() -> None:
    """后端启动时恢复所有已启用且已安装的服务型插件。"""
    from app.services import script_plugin_manager as mgr
    try:
        installed = mgr._load_installed()
    except Exception:
        return
    for pid, rec in installed.items():
        if rec.get("type") == "apiserver" and rec.get("enabled", True):
            try:
                await start(rec)
            except Exception:
                pass


async def shutdown_all() -> None:
    for pid in list(_procs):
        await stop(pid)
