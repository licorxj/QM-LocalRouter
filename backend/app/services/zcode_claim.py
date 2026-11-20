"""ZCode 本地客户端签到（backend/data/zcode-autoclaim/claim-now.mjs）。

脚本通过 CDP 连接本地 ZCode 桌面客户端，调用其原生 UI 上的「领取」入口完成
赠送 token 签到：脚本自身不发 HTTP 请求、不读写 token，投放刷新 / 阿里云验证码 /
claim / 上报全部由 ZCode 原生组件完成。

整合约定：
- 脚本目录：backend/data/zcode-autoclaim/（随项目入库，零 npm 依赖）
- 环境要求：Node.js >= 22（脚本使用内置 WebSocket / fetch）
- 运行前提：系统已安装 ZCode 桌面客户端，且以 --remote-debugging-port 启动
  （默认 9222；见脚本目录 start-debug.cmd）。

脚本退出码（README）：
  0 成功 / 1 当前无可领投放 / 2 领取失败 / 3 需人工验证码
  4 环境错误（连不上调试端口）/ 5 结果未确认
"""
import asyncio
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time

# Windows 下调用控制台工具（reg/tasklist/taskkill/netstat/node）时禁止弹出黑色
# cmd 窗口；frozen（console=False）桌面版尤其需要，否则每次签到检测都会闪窗。
_NO_WINDOW_KW: dict = (
    {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    if sys.platform == "win32" else {}
)

# backend/app/services/zcode_claim.py -> backend/data/zcode-autoclaim
SERVICE_DIR = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR = os.path.dirname(os.path.dirname(SERVICE_DIR))
SCRIPT_DIR = os.path.join(BACKEND_DIR, "data", "zcode-autoclaim")
CLAIM_SCRIPT = os.path.join(SCRIPT_DIR, "claim-now.mjs")

DEFAULT_PORT = 9222

EXIT_STATUS = {
    0: ("success", "领取成功"),
    1: ("no_delivery", "当前没有可领的赠送投放（活动未投放或已领取）"),
    2: ("failure", "领取失败"),
    3: ("captcha", "需要人工完成阿里云验证码，请在 ZCode 窗口操作后再试"),
    4: ("env_error", "连不上调试端口：请先完全退出 ZCode，再带 --remote-debugging-port 启动"),
    5: ("unconfirmed", "结果未确认，请到 ZCode「设置 → 用量 / 套餐」核对"),
}

_detect_cache: dict = {"at": 0.0, "data": None}
CACHE_TTL = 60


def _candidate_paths() -> list[str]:
    """返回可能的 ZCode.exe 路径（去重保序，不校验存在性）。"""
    out: list[str] = []
    env = (os.environ.get("ZCODE_EXE") or "").strip().strip('"')
    if env:
        out.append(env)
    if sys.platform == "win32":
        roots = [
            os.environ.get("ProgramFiles"),
            os.environ.get("ProgramFiles(x86)"),
            os.environ.get("ProgramW6432"),
            os.environ.get("LOCALAPPDATA"),
            r"D:\Program files",
            r"C:\Program Files",
        ]
        for root in roots:
            if root:
                out.append(os.path.join(root, "ZCode", "ZCode.exe"))
        userprofile = os.environ.get("USERPROFILE")
        if userprofile:
            out.append(os.path.join(userprofile, "AppData", "Local", "Programs",
                                    "ZCode", "ZCode.exe"))
    else:
        out.append(os.path.expanduser("~/ZCode/ZCode.exe"))
    seen: set[str] = set()
    uniq: list[str] = []
    for p in out:
        key = os.path.normcase(p)
        if key not in seen:
            seen.add(key)
            uniq.append(p)
    return uniq


def _registry_exe() -> str:
    """从注册表卸载项里找 ZCode 安装位置（Windows）。"""
    if sys.platform != "win32":
        return ""
    roots = [
        r"HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
        r"HKLM\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
        r"HKCU\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
    ]
    for root in roots:
        try:
            proc = subprocess.run(
                ["reg", "query", root, "/s", "/f", "ZCode"],
                capture_output=True, text=True, timeout=8, check=False,
                **_NO_WINDOW_KW,
            )
        except Exception:
            continue
        install_dir = ""
        display_icon = ""
        for line in proc.stdout.splitlines():
            s = line.strip()
            m = re.match(r"InstallLocation\s+REG_SZ\s+(.+)$", s, re.I)
            if m:
                install_dir = m.group(1).strip().strip('"')
            elif not display_icon:
                m = re.match(r"DisplayIcon\s+REG_SZ\s+(.+)$", s, re.I)
                if m:
                    display_icon = m.group(1).strip().strip('"')
        guess = display_icon or (os.path.join(install_dir, "ZCode.exe") if install_dir else "")
        if guess and os.path.isfile(guess):
            return guess
    return ""


def _launch_hint(exe: str, port: int = DEFAULT_PORT) -> str:
    if not exe:
        return "请先安装 ZCode 桌面客户端"
    return (f'完全退出 ZCode（含托盘）后执行："{exe}" '
            f'--remote-debugging-port={port} --remote-allow-origins=*')


def _debug_port_open(port: int = DEFAULT_PORT, timeout: float = 0.5) -> bool:
    """实时探测本机 CDP 调试端口是否可连（端口状态变化快，不纳入安装检测缓存）。"""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def detect_installation(force: bool = False) -> dict:
    """检测 ZCode 客户端与 Node 运行时。安装信息缓存 60 秒，调试端口实时探测。"""
    now = time.time()
    if not force and _detect_cache["data"] and now - _detect_cache["at"] < CACHE_TTL:
        data = dict(_detect_cache["data"])
    else:
        exe = ""
        source = ""
        for path in _candidate_paths():
            if os.path.isfile(path):
                exe, source = path, "path"
                break
        if not exe:
            exe = _registry_exe()
            if exe:
                source = "registry"

        node = shutil.which("node")
        node_version = ""
        if node:
            try:
                proc = subprocess.run([node, "--version"], capture_output=True,
                                      text=True, timeout=6, check=False,
                                      **_NO_WINDOW_KW)
                node_version = (proc.stdout or "").strip()
            except Exception:
                node_version = ""
        try:
            node_ok = int(node_version.lstrip("v").split(".")[0]) >= 22 if node_version else False
        except Exception:
            node_ok = False

        data = {
            "installed": bool(exe),
            "exe_path": exe or None,
            "source": source,
            "node_available": bool(node),
            "node_version": node_version,
            "node_ok": node_ok,
            "script_available": os.path.isfile(CLAIM_SCRIPT),
            "launch_hint": _launch_hint(exe),
        }
        _detect_cache.update(at=now, data=data)

    # 端口状态每次实时探测：客户端可能随时带/不带调试端口启动
    data["debug_port"] = DEFAULT_PORT
    data["debug_port_open"] = _debug_port_open(DEFAULT_PORT)
    return data


async def run_claim(port: int = DEFAULT_PORT, extra_args: list[str] | None = None,
                    timeout: int = 240) -> dict:
    """执行一次性签到脚本；返回结构化结果（不抛异常）。"""
    st = detect_installation()
    if not st["installed"]:
        return {"ok": False, "status": "not_installed",
                "message": "未安装 ZCode 客户端，不支持签到"}
    if not st["node_ok"]:
        return {"ok": False, "status": "node_unavailable",
                "message": f"签到脚本需要 Node.js >= 22（当前：{st['node_version'] or '未安装'}）"}
    if not st["script_available"]:
        return {"ok": False, "status": "script_missing", "message": "签到脚本缺失，请检查安装"}

    node = shutil.which("node")
    args = [node or "node", CLAIM_SCRIPT, "--json", "--port", str(port)]
    if extra_args:
        args += [str(a) for a in extra_args]

    kwargs: dict = {
        "cwd": SCRIPT_DIR,
        "stdout": asyncio.subprocess.PIPE,
        "stderr": asyncio.subprocess.PIPE,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    try:
        proc = await asyncio.create_subprocess_exec(*args, **kwargs)
    except OSError as e:
        return {"ok": False, "status": "spawn_error",
                "message": f"启动签到脚本失败：{str(e)[:160]}"}

    try:
        out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            pass
        return {"ok": False, "status": "timeout",
                "message": f"签到超时（{timeout} 秒），请检查 ZCode 窗口状态"}

    stdout = (out_b or b"").decode("utf-8", "replace").strip()
    stderr = (err_b or b"").decode("utf-8", "replace").strip()
    code = proc.returncode if proc.returncode is not None else -1

    detail: dict = {}
    if stdout:
        try:
            parsed = json.loads(stdout)
            if isinstance(parsed, dict):
                detail = parsed
        except Exception:
            pass

    status, default_msg = EXIT_STATUS.get(code, ("unknown", f"脚本退出码 {code}"))
    message = str(detail.get("message") or "").strip() or default_msg
    if status in ("env_error", "unknown") and not detail and stderr:
        # 脚本在启动阶段失败时只写 stderr（无 JSON），把最后一行有效信息作为提示
        lines = [ln.strip() for ln in stderr.splitlines() if ln.strip()]
        if lines:
            message = lines[-1][:400]
    if status == "no_delivery":
        message = default_msg
    if status == "success" and detail.get("ticket"):
        message = f"领取成功：{detail['ticket']}"

    return {
        "ok": code == 0,
        "status": status,
        "message": message[:400],
        "exit_code": code,
        "port": port,
        "detail": detail,
        "stderr": stderr[-1200:],
    }


# ---------------------------------------------------------------- 托管签到
# 点击签到时由后端自动拉起带调试端口的 ZCode，签到结束（成功/失败）后关闭
# 本次拉起的进程树。用户自己已经开着 ZCode 时：若其已带调试端口则复用且不关闭；
# 若未带调试端口，则必须先关闭（单实例锁会吞掉第二个实例的调试参数）再由我们重启。
_zcode_lock = asyncio.Lock()


def _zcode_pids() -> list[int]:
    """列出当前 ZCode.exe 进程 PID（Windows，tasklist）。"""
    if sys.platform != "win32":
        return []
    try:
        proc = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq ZCode.exe", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=10, check=False,
            **_NO_WINDOW_KW,
        )
    except Exception:
        return []
    pids: list[int] = []
    for line in proc.stdout.splitlines():
        m = re.search(r'"ZCode\.exe"\s*,\s*"(\d+)"', line, re.I)
        if m:
            pids.append(int(m.group(1)))
    return pids


def _kill_zcode_tree(pid: int):
    """按进程树强杀（ZCode 会派生多个子进程），忽略已退出。"""
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                       capture_output=True, text=True, timeout=15, check=False,
                       **_NO_WINDOW_KW)
    else:
        try:
            os.killpg(os.getpgid(pid), 9)
        except Exception:
            pass


def _launch_zcode(exe: str, port: int) -> int | None:
    """带调试端口启动 ZCode，返回主进程 PID。"""
    flags = 0
    if sys.platform == "win32":
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        proc = subprocess.Popen(
            [exe, f"--remote-debugging-port={port}", "--remote-allow-origins=*"],
            cwd=os.path.dirname(exe), creationflags=flags,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return proc.pid
    except Exception:
        return None


async def _wait_cdp_ready(port: int, timeout: float = 30.0) -> bool:
    """等 CDP 端口就绪且出现 ZCode 渲染页（脚本 discover 依赖它）。"""
    import urllib.request
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _debug_port_open(port, timeout=0.4):
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/json/list", timeout=2
                ) as resp:
                    pages = json.loads(resp.read().decode("utf-8", "replace"))
                if any(p.get("type") == "page" and
                       ("/renderer/index.html" in p.get("url", "") or
                        p.get("url", "").startswith("file://")) for p in pages):
                    return True
            except Exception:
                pass
        await asyncio.sleep(0.7)
    return False


async def run_managed_claim(port: int = DEFAULT_PORT, timeout: int = 240,
                            extra_args: list[str] | None = None) -> dict:
    """托管签到：按需自动拉起带调试端口的 ZCode，签到后关闭本次拉起的进程。

    流程：
      1. 端口已开 → 复用现有实例（不拉起、不关闭）。
      2. 端口未开 → 关闭所有现有 ZCode（单实例锁）→ 带调试端口启动 → 等 CDP 就绪。
      3. 执行签到脚本（默认带 --any，兼容横幅文案为「打开」的活动）。
      4. finally 中关闭「本次拉起」的进程树；复用实例保持不动。
    """
    async with _zcode_lock:
        st = detect_installation(force=True)
        if not st["installed"]:
            return {"ok": False, "status": "not_installed",
                    "message": "未安装 ZCode 客户端，不支持签到"}
        if not st["node_ok"]:
            return {"ok": False, "status": "node_unavailable",
                    "message": f"签到脚本需要 Node.js >= 22（当前：{st['node_version'] or '未安装'}）"}
        if not st["script_available"]:
            return {"ok": False, "status": "script_missing", "message": "签到脚本缺失，请检查安装"}

        launched_pid: int | None = None
        reused = _debug_port_open(port, timeout=0.5)
        try:
            if reused:
                # 已有带调试端口的实例（可能用户自己开的），复用且不关闭
                pass
            else:
                # 先关现有实例（不带调试端口时单实例锁会吞参数）
                for pid in _zcode_pids():
                    _kill_zcode_tree(pid)
                await asyncio.sleep(1.0)
                launched_pid = _launch_zcode(st["exe_path"], port)
                if not launched_pid:
                    return {"ok": False, "status": "launch_failed",
                            "message": "自动启动 ZCode 失败，请检查安装路径或手动启动一次"}
                if not await _wait_cdp_ready(port, timeout=30):
                    return {"ok": False, "status": "cdp_timeout",
                            "message": "ZCode 已启动但调试端口未就绪，请稍后重试"}

            # 冷启动时投放横幅需要时间出现：放宽文案(--any) + 延长等待(--wait)。
            # 用户已打开带调试端口的实例时同样适用，不影响。
            args = list(extra_args) if extra_args is not None else ["--any", "--wait", "35"]
            result = await run_claim(port=port, extra_args=args, timeout=timeout)
            result["zcode_reused"] = reused
            result["zcode_auto_launched"] = launched_pid is not None
            return result
        finally:
            if launched_pid is not None:
                _kill_zcode_tree(launched_pid)
                await asyncio.sleep(0.4)
                # 兜底：清理可能残留的同名进程（只在我们刚拉起过的情况下）
                for pid in _zcode_pids():
                    _kill_zcode_tree(pid)


# ---------------------------------------------------------------- 按账号隔离签到
# 每个 zcode 账号用独立的 ZCode profile（隔离 credentials / userData / 端口），
# 签到前把该账号的明文 JWT 注入其 credentials.json —— ZCode 启动时会用 DPAPI
# 自动把它重新加密（enc:v2:os:）并识别为该账号，因此每个账号签的是自己的账号。

PROFILE_PORT_BASE = 9230          # 每账号端口 = BASE + (key_id % 100)，避免与默认 9222 冲突
PROFILE_DIR_ROOT = os.path.join(os.path.dirname(BACKEND_DIR), "data", "zcode-profiles")


def account_profile_dir(key_id: int) -> str:
    """返回该账号的隔离 profile 根目录（持久化，重复签到复用登录态）。"""
    return os.path.join(PROFILE_DIR_ROOT, f"acct_{key_id}")


def _account_profile_port(key_id: int) -> int:
    return PROFILE_PORT_BASE + (int(key_id) % 100)


def _write_profile_credentials(profile_dir: str, jwt: str, email: str = "") -> bool:
    """在该 profile 的 credentials.json 注入明文 JWT；ZCode 启动会自动加密接管。

    返回是否写入成功。除 zcodejwttoken 外，尽量保留 user_info（客户端据此显示账号）。
    """
    try:
        zdir = os.path.join(profile_dir, ".zcode", "v2")
        os.makedirs(zdir, exist_ok=True)
        for sub in ("home", "user", "electron-user-data", "electron-session-data"):
            os.makedirs(os.path.join(profile_dir, sub), exist_ok=True)
        creds_path = os.path.join(zdir, "credentials.json")
        creds = {"access_token": jwt}
        if email:
            creds["oauth:zai:user_info"] = json.dumps({"email": email}, ensure_ascii=False)
        with open(creds_path, "w", encoding="utf-8") as f:
            json.dump(creds, f, ensure_ascii=False)
        return True
    except Exception:
        return False


def _launch_zcode_isolated(exe: str, port: int, profile_dir: str) -> int | None:
    """用独立 profile 环境变量启动 ZCode，返回主进程 PID。"""
    env = os.environ.copy()
    env["HOME"] = os.path.join(profile_dir, "home")
    env["USERPROFILE"] = os.path.join(profile_dir, "user")
    env["ZCODE_DATA_BASE_DIR"] = profile_dir
    env["ZCODE_DESKTOP_HOME_DIR"] = os.path.join(profile_dir, "home")
    env["ZCODE_DESKTOP_USER_DATA_DIR"] = os.path.join(profile_dir, "electron-user-data")
    env["ZCODE_DESKTOP_SESSION_DATA_DIR"] = os.path.join(profile_dir, "electron-session-data")
    env["ZCODE_DESKTOP_APPLICATION_NAME"] = "ZCode-Acct-" + os.path.basename(profile_dir)
    flags = 0
    if sys.platform == "win32":
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        proc = subprocess.Popen(
            [exe, f"--remote-debugging-port={port}", "--remote-allow-origins=*",
             f"--user-data-dir={os.path.join(profile_dir, 'electron-user-data')}"],
            env=env, cwd=os.path.dirname(exe), creationflags=flags,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return proc.pid
    except Exception:
        return None


async def run_claim_for_account(jwt: str, key_id: int, account_label: str = "",
                                email: str = "", timeout: int = 240) -> dict:
    """按账号签到：用该账号的 token 注入隔离 profile，拉起专属 ZCode 实例签到后关闭。

    jwt 必须是明文 JWT（由路由层从 ApiKey 解密得到）。account_label 用于结果归因展示。
    """
    async with _zcode_lock:
        st = detect_installation(force=True)
        if not st["installed"]:
            return {"ok": False, "status": "not_installed", "account": account_label,
                    "message": "未安装 ZCode 客户端，不支持签到"}
        if not st["node_ok"]:
            return {"ok": False, "status": "node_unavailable", "account": account_label,
                    "message": f"签到脚本需要 Node.js >= 22（当前：{st['node_version'] or '未安装'}）"}
        if not st["script_available"]:
            return {"ok": False, "status": "script_missing", "account": account_label,
                    "message": "签到脚本缺失，请检查安装"}
        if not jwt or not jwt.startswith("eyJ"):
            return {"ok": False, "status": "credential_invalid", "account": account_label,
                    "message": "该账号凭据不可用（新版加密或已失效），请重新导入账号"}

        profile_dir = account_profile_dir(key_id)
        port = _account_profile_port(key_id)
        if not _write_profile_credentials(profile_dir, jwt, email):
            return {"ok": False, "status": "profile_error", "account": account_label,
                    "message": "无法准备账号隔离环境，请检查磁盘权限"}

        # 每个账号用独立端口；只需关闭占用该端口的旧实例（若有），不动其他账号
        launched_pid: int | None = None
        try:
            if _debug_port_open(port, timeout=0.4):
                # 该账号端口已被占用（上次残留）：按端口找到 PID 并清理
                for pid in _pids_listening_on(port):
                    _kill_zcode_tree(pid)
                await asyncio.sleep(0.8)

            launched_pid = _launch_zcode_isolated(st["exe_path"], port, profile_dir)
            if not launched_pid:
                return {"ok": False, "status": "launch_failed", "account": account_label,
                        "message": "自动启动 ZCode 失败，请检查安装路径"}
            if not await _wait_cdp_ready(port, timeout=30):
                return {"ok": False, "status": "cdp_timeout", "account": account_label,
                        "message": "ZCode 已启动但调试端口未就绪，请稍后重试"}

            args = ["--any", "--wait", "35", "--profile", profile_dir]
            result = await run_claim(port=port, extra_args=args, timeout=timeout)
            result["account"] = account_label
            result["key_id"] = key_id
            result["zcode_auto_launched"] = True
            return result
        finally:
            if launched_pid is not None:
                _kill_zcode_tree(launched_pid)
                await asyncio.sleep(0.4)
                for pid in _pids_listening_on(port):
                    _kill_zcode_tree(pid)


def _pids_listening_on(port: int) -> list[int]:
    """找出监听指定端口的 ZCode.exe PID（Windows），避免误杀其他账号实例。"""
    if sys.platform != "win32":
        return []
    pids: list[int] = set()
    try:
        proc = subprocess.run(["netstat", "-ano", "-p", "TCP"],
                              capture_output=True, text=True, timeout=10, check=False,
                              **_NO_WINDOW_KW)
        for line in proc.stdout.splitlines():
            if f":{port}" not in line or "LISTENING" not in line.upper():
                continue
            parts = line.split()
            if parts and parts[-1].isdigit():
                pids.add(int(parts[-1]))
    except Exception:
        return []
    # 只保留确实是该端口上的 ZCode 进程（netstat 给的是 PID，直接验证进程名）
    out: list[int] = []
    for pid in pids:
        try:
            q = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, timeout=6, check=False,
                **_NO_WINDOW_KW)
            if "ZCode.exe" in q.stdout:
                out.append(pid)
        except Exception:
            pass
    return out
