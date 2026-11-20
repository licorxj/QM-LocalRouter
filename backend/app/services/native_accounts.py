"""EchoBird 原生账号平台（非插件）：DeepSeek Harness / Kilo / Qoder / QwenWork。

移植 EchoBird src-tauri/src/services/{deepseek_oauth,deepseek_accounts,kilo_accounts,
qoder_accounts,qwenwork_accounts}.rs 的协议，并直接读写 EchoBird 兼容存储
（~/.echobird/...），已装 EchoBird 的账号无需重新登录即可在此展示。

- 存储加密：enc:v1 = AES-256-GCM(nonce12+ct, hex)，密钥 = HMAC-SHA256(
  file_key=~/.echobird/config/.encryption_key, "{指纹}|{用户名}")（secret_store.rs）
- DeepSeek Harness：auth_init(PKCE) → 浏览器 → 本地回环回调 → auth_exchange；
  profile=/auth-api/v0/users/current、余额=/api/v0/users/get_user_summary
- Kilo：POST /api/device-auth/codes 设备码 → GET codes/{code} 轮询 → profile/balance
- Qoder(CN)：浏览器 {web}/device/selectAccounts → 轮询 {openapi}/api/v1/deviceToken/poll
  → jobToken 交换 + userinfo → 额度 {openapi}/sash/api/v2/me/usage
- QwenWork(CN)：浏览器 {gateway}/device/selectAccounts → 经网关轮询 deviceToken/poll；
  额度 account-context（企业版 /api/v2/quota/usage，CN 个人版 wallets）。
  登录需要已装官方客户端（~/.qwenwork/machine-id），与 EchoBird 行为一致
"""
import asyncio
import base64
import hashlib
import hmac
import json
import os
import platform as _platform
import secrets
import time
import uuid
from urllib.parse import urlsplit

import httpx

from app.config import settings

ECHO_DIR = os.path.expanduser("~/.echobird")
DAY = 86400
POLL_INTERVAL = 2.0


class EchoError(Exception):
    def __init__(self, message: str, code: str = "upstream"):
        super().__init__(message)
        self.message = message
        self.code = code


# ---------------------------------------------------------------------------
# 平台注册表（id 与 account_hub 目录一致）

ECHO_PLATFORMS: dict[str, dict] = {
    "deepseek": {
        "label": "DeepSeek Harness", "store": "yaml", "claim": False,
        "origin": "https://platform.deepseek.com",
    },
    "kilo": {
        "label": "Kilo", "store": "kilo", "claim": False,
        "api": "https://api.kilo.ai",
    },
    "qoder": {
        "label": "Qoder", "tool": "qoder", "store": "qoder", "claim": False,
        "openapi": "https://openapi.qoder.sh", "gateway": "https://api3.qoder.sh",
        "web": "https://qoder.com", "client_id": "732aef47-9cf2-46a2-95fe-4cebb5d0d1fa",
        "redirect": "qoder-app://", "intl": True,
    },
    "qodercn": {
        "label": "Qoder CN", "tool": "qodercn", "store": "qoder", "claim": False,
        "openapi": "https://openapi.qoder.com.cn", "gateway": "https://gateway.qoder.com.cn",
        "web": "https://qoder.cn", "client_id": "732aef47-9cf2-46a2-95fe-4cebb5d0d1fa",
        "redirect": "", "intl": False,
    },
    "qwenwork": {
        "label": "QwenWork", "tool": "qwenwork", "store": "qwenwork", "claim": False,
        "base": "https://gateway.qwenwork.ai", "client_id": "cc65e5fc-05bd-4f5d-a4e8-0df19aa3d75a",
        "redirect": "qwenwork://", "cn": False,
    },
    "qwenworkcn": {
        "label": "QwenWork CN", "tool": "qwenworkcn", "store": "qwenwork", "claim": False,
        "base": "https://gateway.qwenwork.cn", "client_id": "e883ade2-e6e3-4d6d-adf7-f92ceff5fdcb",
        "redirect": "qwenwork-cn://", "cn": True,
    },
}

# 账号接入页展示的 EchoBird 原生平台（account_hub.PLUGIN_SURFACED_APPS 机制复用）
ECHO_SURFACED_APPS = set(ECHO_PLATFORMS)


def platform_label(platform: str) -> str:
    return ECHO_PLATFORMS.get(platform, {}).get("label") or platform


# ---------------------------------------------------------------------------
# EchoBird secret_store（enc:v1）兼容实现

def _echo_file_key() -> bytes:
    path = os.path.join(ECHO_DIR, "config", ".encryption_key")
    with open(path, "rb") as f:
        key = f.read()
    if len(key) != 32:
        raise EchoError("EchoBird 加密密钥文件无效", "keychain")
    return key


def _machine_fingerprint() -> str:
    if os.name == "nt":
        try:
            import subprocess
            _kw = ({"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
                   if os.name == "nt" else {})
            out = subprocess.run(
                ["reg", "query", r"HKLM\SOFTWARE\Microsoft\Cryptography", "/v", "MachineGuid"],
                capture_output=True, text=True, timeout=10, check=False,
                **_kw,
            ).stdout
            for line in out.splitlines():
                if "MachineGuid" in line:
                    value = line.split()[-1].strip()
                    if value:
                        return value
        except Exception:
            pass
    return os.environ.get("COMPUTERNAME") or os.environ.get("HOSTNAME") or "echobird-fallback"


def _echo_derived_key() -> bytes:
    username = os.environ.get("USERNAME") or os.environ.get("USER") or "default"
    identity = f"{_machine_fingerprint()}|{username}".encode()
    return hmac.new(_echo_file_key(), identity, hashlib.sha256).digest()


def echo_decrypt(stored: str) -> str:
    """EchoBird enc:v1（AES-256-GCM，hex(nonce+ct)）→ 明文；非密文原样返回。"""
    if not isinstance(stored, str) or not stored.startswith("enc:v1:"):
        return stored
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    raw = bytes.fromhex(stored[len("enc:v1:"):])
    nonce, ct = raw[:12], raw[12:]
    return AESGCM(_echo_derived_key()).decrypt(nonce, ct, None).decode("utf-8")


def echo_encrypt(plain: str) -> str:
    if not plain or plain.startswith("enc:v1:"):
        return plain
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    nonce = os.urandom(12)
    ct = AESGCM(_echo_derived_key()).encrypt(nonce, plain.encode(), None)
    return "enc:v1:" + (nonce + ct).hex()


def _read_json(path: str):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _write_json(path: str, data) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# EchoBird 存储读取（各平台 Saved 结构 → 统一账号摘要）

def _sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _qoder_creds_from_store(entry: dict) -> dict:
    """Qoder Saved.credential(enc:v1) → Credentials JSON。"""
    raw = echo_decrypt(entry.get("credential") or "")
    return json.loads(raw)


def list_echo_accounts(platform: str) -> list[dict]:
    """读取 EchoBird 存储中的账号摘要（解密凭据但不外泄任何 token）。"""
    cfg = ECHO_PLATFORMS.get(platform) or {}
    store_kind = cfg.get("store")
    out = []
    try:
        if platform == "deepseek":
            root = os.path.join(ECHO_DIR, "deepseek-accounts")
            if os.path.isdir(root):
                for name in sorted(os.listdir(root)):
                    if not name.endswith((".yaml", ".yml", ".json")):
                        continue
                    data = _read_json(os.path.join(root, name)) or {}
                    summary = data.get("summary") or {}
                    if not summary.get("id"):
                        continue
                    out.append({
                        "account_id": summary["id"],
                        "identity": summary.get("name") or summary["id"][:10],
                        "plan": None,
                        "extra": {"token": data.get("token") or "", "device": data.get("device") or ""},
                        "active": bool(summary.get("active")),
                    })
            return out
        if store_kind == "kilo":
            data = _read_json(os.path.join(ECHO_DIR, "kilo-accounts.json")) or {}
            for key, entry in (data.items() if isinstance(data, dict) else []):
                if not isinstance(entry, dict):
                    continue
                token = echo_decrypt(entry.get("credential") or "")
                out.append({
                    "account_id": entry.get("email") or key,
                    "identity": entry.get("email") or key[:10],
                    "plan": None,
                    "extra": {"token": token, "expires_at": entry.get("expiresAt") or entry.get("expires_at") or 0,
                              "balance": entry.get("balance")},
                    "active": False,
                })
            return out
        if store_kind == "qoder":
            fname = "qoder-accounts.json" if platform == "qoder" else "qodercn-accounts.json"
            data = _read_json(os.path.join(ECHO_DIR, fname)) or {}
            for key, entry in (data.items() if isinstance(data, dict) else []):
                if not isinstance(entry, dict):
                    continue
                try:
                    creds = _qoder_creds_from_store(entry)
                except Exception:
                    continue
                account = entry.get("account") or {}
                out.append({
                    "account_id": account.get("id") or key,
                    "identity": account.get("name") or creds.get("email") or key[:10],
                    "plan": account.get("plan"),
                    "extra": {"creds": creds, "summary": account},
                    "active": bool(account.get("active")),
                    "metrics": entry.get("metrics") or [],
                    "metrics_fetched_at": int(entry.get("metrics_fetched_at") or 0),
                })
            return out
        if store_kind == "qwenwork":
            fname = "qwenwork-accounts.json" if platform == "qwenwork" else "qwenworkcn-accounts.json"
            data = _read_json(os.path.join(ECHO_DIR, fname)) or {}
            for key, entry in (data.items() if isinstance(data, dict) else []):
                if not isinstance(entry, dict):
                    continue
                try:
                    creds = json.loads(echo_decrypt(entry.get("credential") or ""))
                except Exception:
                    continue
                account = entry.get("account") or {}
                out.append({
                    "account_id": account.get("id") or key,
                    "identity": account.get("name") or (creds.get("auth", {}).get("user", {}) or {}).get("email") or key[:10],
                    "plan": account.get("planName") or account.get("plan"),
                    "extra": {"creds": creds, "summary": account},
                    "active": bool(account.get("active")),
                    "metrics": entry.get("metrics") or [],
                    "metrics_fetched_at": int(entry.get("metrics_fetched_at") or 0),
                })
            return out
    except EchoError:
        raise
    except Exception:
        return out
    return out


# ---------------------------------------------------------------------------
# 登录会话

_sessions: dict[str, dict] = {}
_login_tasks: dict[str, asyncio.Task] = {}


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _http(timeout: float = 20.0) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=timeout, trust_env=False, follow_redirects=False)


async def _json_request(client: httpx.AsyncClient, method: str, url: str, *,
                        token: str | None = None, body: dict | None = None,
                        headers: dict | None = None) -> dict:
    merged = {"Accept": "application/json", **(headers or {})}
    if token:
        merged["Authorization"] = "Bearer " + token
    resp = await client.request(method, url, json=body, headers=merged)
    if resp.status_code in (401, 403):
        raise EchoError("登录已失效，请重新登录该账号", "login_required")
    if resp.status_code >= 400:
        raise EchoError(f"上游返回 HTTP {resp.status_code}", "network")
    try:
        return resp.json()
    except ValueError:
        raise EchoError("上游返回非 JSON", "format")


def _login_redirect_uri(login_id: str) -> str:
    return f"http://127.0.0.1:{settings.BACKEND_PORT}/api/accounts/dsh/callback/{login_id}"


def _dsh_unwrap(value):
    """DeepSeek 平台双层信封 {code, data: {biz_data: {...}}} → biz_data（兼容直返）。"""
    if isinstance(value, dict) and isinstance(value.get("data"), dict):
        inner = value["data"]
        if isinstance(inner.get("biz_data"), (dict, str)):
            return inner["biz_data"]
        return inner
    return value


async def start_login(platform: str) -> dict:
    cfg = ECHO_PLATFORMS.get(platform)
    if not cfg:
        raise EchoError(f"未知平台: {platform}")
    session_id = uuid.uuid4().hex
    now = int(time.time())

    if platform == "deepseek":
        redirect = _login_redirect_uri(session_id)
        state = _b64url(os.urandom(32))
        verifier = _b64url(os.urandom(32))
        challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
        origin = cfg["origin"]
        async with _http() as client:
            init = _dsh_unwrap(await _json_request(client, "POST", origin + "/auth-api/v0/dsh/auth_init", body={
                "code_challenge": challenge, "code_challenge_method": "S256",
                "state": state, "redirect_uri": redirect,
                "locale": "zh_CN", "login_source": "web",
            }))
        authorize_url = init.get("authorize_url") or ""
        host = urlsplit(authorize_url)
        if host.scheme != "https" or host.netloc != "platform.deepseek.com" or not host.path.startswith("/dsh/authorize"):
            raise EchoError(f"授权地址校验失败: {str(authorize_url)[:120]}", "auth")
        _sessions[session_id] = {
            "platform": platform, "flow": "dsh", "state": state, "verifier": verifier,
            "redirect": redirect, "authorize_id": init.get("authorize_id") or "",
            "status": "pending", "account": None, "error": "", "created": now,
            "expires": now + int(init.get("expires_in") or 60),
        }
        return {"session_id": session_id, "verification_uri": authorize_url,
                "expires_at": _sessions[session_id]["expires"]}

    if platform == "kilo":
        async with _http() as client:
            value = await _json_request(client, "POST", cfg["api"] + "/api/device-auth/codes", body={})
        code = value.get("code") or ""
        uri = value.get("verificationUrl") or ""
        host = urlsplit(uri)
        if host.scheme != "https" or not (host.netloc == "kilo.ai" or host.netloc.endswith(".kilo.ai")):
            raise EchoError("授权地址校验失败", "auth")
        expires = now + min(int(value.get("expiresIn") or 60), 60)
        _sessions[session_id] = {"platform": platform, "flow": "kilo", "code": code,
                                 "status": "pending", "account": None, "error": "",
                                 "created": now, "expires": expires}
        return {"session_id": session_id, "verification_uri": uri, "expires_at": expires}

    if platform in ("qoder", "qodercn"):
        verifier = _b64url(os.urandom(64))
        nonce = str(uuid.uuid4())
        machine = str(uuid.uuid4())
        query = {"challenge": _b64url(hashlib.sha256(verifier.encode()).digest()),
                 "challenge_method": "S256", "nonce": nonce, "machine_id": machine,
                 "client_id": cfg["client_id"]}
        if cfg.get("intl"):
            query["redirect_uri"] = cfg["redirect"]
        from urllib.parse import urlencode
        verification = f"{cfg['web']}/device/selectAccounts?" + urlencode(query)
        _sessions[session_id] = {"platform": platform, "flow": "qoder", "verifier": verifier,
                                 "nonce": nonce, "machine": machine, "status": "pending",
                                 "account": None, "error": "", "created": now, "expires": now + 60}
        task = asyncio.get_running_loop().create_task(_qoder_authorize(session_id))
        _login_tasks[session_id] = task
        return {"session_id": session_id, "verification_uri": verification, "expires_at": now + 60}

    if platform in ("qwenwork", "qwenworkcn"):
        # 与 EchoBird 一致：登录需要已装官方客户端（machine-id 与数据目录）
        tool = cfg["tool"]
        data_dir = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~/Library/Application Support"),
                                "QwenWork" if platform == "qwenwork" else "QwenWorkCN")
        machine_path = os.path.expanduser(f"~/.{tool}/machine-id")
        if not (os.path.isdir(data_dir) and os.path.isfile(machine_path)):
            raise EchoError("需要先安装 QwenWork 官方客户端再登录", "initialize_client")
        try:
            machine = str(uuid.UUID(open(machine_path, encoding="utf-8").read().strip()))
        except Exception:
            raise EchoError("客户端 machine-id 无效，请重新安装官方客户端", "initialize_client")
        verifier = _b64url(os.urandom(64))
        nonce = str(uuid.uuid4())
        challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
        from urllib.parse import urlencode
        verification = (f"{cfg['base']}/device/selectAccounts?" + urlencode({
            "challenge": challenge, "challenge_method": "S256", "nonce": nonce,
            "machine_id": machine, "client_id": cfg["client_id"], "redirect_uri": cfg["redirect"]}))
        _sessions[session_id] = {"platform": platform, "flow": "qwenwork", "verifier": verifier,
                                 "nonce": nonce, "machine": machine, "status": "pending",
                                 "account": None, "error": "", "created": now, "expires": now + 60}
        task = asyncio.get_running_loop().create_task(_qwenwork_authorize(session_id))
        _login_tasks[session_id] = task
        return {"session_id": session_id, "verification_uri": verification, "expires_at": now + 60}

    raise EchoError(f"平台 {platform} 不支持在线登录")


async def _qoder_authorize(session_id: str) -> None:
    """后台轮询 deviceToken/poll → jobToken 交换 → 完成会话（EchoBird oauth.rs）。"""
    session = _sessions.get(session_id) or {}
    cfg = ECHO_PLATFORMS[session["platform"]]
    openapi, base = cfg["openapi"], cfg["openapi"]
    deadline = session["expires"]
    try:
        device = None
        async with _http() as client:
            while time.time() < deadline:
                try:
                    resp = await client.get(f"{openapi}/api/v1/deviceToken/poll", params={
                        "nonce": session["nonce"], "verifier": session["verifier"],
                        "challenge_method": "S256"})
                    if resp.status_code == 200:
                        value = resp.json()
                        if isinstance(value, dict) and value.get("token"):
                            device = value
                            break
                except Exception:
                    pass
                await asyncio.sleep(POLL_INTERVAL)
            if not device:
                raise EchoError("授权超时，请重试", "expired")
            access = device.get("token") or ""
            refresh = device.get("refresh_token") or ""
            user_id = device.get("user_id") or ""
            chat_access, chat_refresh = access, refresh
            try:
                value = await _json_request(client, "POST", f"{base}/api/v1/me/jobToken",
                                            token=access, body={"clientId": cfg["client_id"]})
                chat_access = value.get("token") or access
                chat_refresh = value.get("refresh_token") or refresh
            except EchoError:
                if cfg["intl"]:
                    raise
            name = device.get("user_name") or ""
            email = ""
            try:
                info = await _json_request(client, "GET", f"{base}/api/v1/userinfo", token=access)
                email = info.get("email") or ""
                name = info.get("name") or name
            except Exception:
                pass
        creds = {"edition": session["platform"], "access_token": chat_access,
                 "refresh_token": chat_refresh, "expires_at": int(time.time() * 1000) + 86_400_000,
                 "user_id": user_id, "name": name, "email": email,
                 "machine_id": session["machine"], "device_token": access,
                 "device_refresh": refresh, "device_expires_at": int(time.time() * 1000) + 86_400_000,
                 "device_chat": False}
        account_id = _sha256_hex(f"{cfg['tool']}\0{user_id}")
        identity = email or name or user_id
        account = {"id": account_id, "edition": session["platform"], "name": identity,
                   "email": email or None, "plan": None, "active": False,
                   "remaining": None, "total": None, "expiresAt": None}
        _save_echo_account(session["platform"], account_id, account, creds)
        session.update({"status": "success", "account": account})
    except Exception as e:
        session.update({"status": "failed", "error": str(getattr(e, "message", e))[:200]})


async def _qwenwork_authorize(session_id: str) -> None:
    """经网关轮询 deviceToken/poll → account-context 用户 → 完成会话。"""
    session = _sessions.get(session_id) or {}
    cfg = ECHO_PLATFORMS[session["platform"]]
    base, version = cfg["base"], "1.0.0"
    deadline = session["expires"]
    try:
        device = None
        async with _http() as client:
            headers = _qwenwork_headers(cfg, version)
            while time.time() < deadline:
                try:
                    resp = await client.get(f"{base}/api/v1/deviceToken/poll", params={
                        "nonce": session["nonce"], "verifier": session["verifier"],
                        "challenge_method": "S256"}, headers=headers)
                    if resp.status_code == 200:
                        value = resp.json()
                        if isinstance(value, dict) and value.get("token"):
                            device = value
                            break
                except Exception:
                    pass
                await asyncio.sleep(1.0)
            if not device:
                raise EchoError("授权超时，请重试", "expired")
            token = device.get("token") or ""
            refresh = device.get("refresh_token") or ""
            headers["Authorization"] = "Bearer " + token
            resp = await client.get(base + "/api/v1/adapter/user/account-context?include=user,plan,quota,page,data_sharing",
                                    headers=headers)
            env = resp.json() if resp.status_code == 200 else {}
        user = (env.get("data") or {}).get("user") or {}
        identity = user.get("email") or user.get("name") or user.get("nickname") or "QwenWork"
        account_id = _sha256_hex(f"{cfg['tool']}\0{user.get('id') or identity}")
        auth = {"schemaVersion": 2, "authRealm": "standard", "token": token,
                "refreshToken": refresh,
                "expiresAt": device.get("expires_at") or int(time.time() * 1000) + 86_400_000,
                "user": user, "identityVersion": 0, "loginMethod": "browser",
                "refreshStrategy": "device_token", "loginDeviceId": session["machine"],
                "loginTimestamp": int(time.time() * 1000), "oauthClientId": cfg["client_id"],
                "oauthRedirectUri": cfg["redirect"]}
        account = {"id": account_id, "edition": session["platform"], "name": identity,
                   "email": user.get("email"), "plan": user.get("planName") or user.get("plan"),
                   "active": False}
        _save_echo_account(session["platform"], account_id, account, {"auth": auth})
        session.update({"status": "success", "account": account})
    except Exception as e:
        session.update({"status": "failed", "error": str(getattr(e, "message", e))[:200]})


def _os_platform() -> str:
    if os.name == "nt":
        return "windows"
    if _platform.system().lower() == "darwin":
        return "macos"
    return "linux"


def _os_arch() -> str:
    machine = (_platform.machine() or "").lower()
    return "arm64" if machine in ("arm64", "aarch64") else "x64"


def _qwenwork_headers(cfg: dict, version: str) -> dict:
    headers = {
        "Accept": "application/json",
        "User-Agent": f"qoderwork/{version}",
        "X-Request-Id": str(uuid.uuid4()),
        "X-QwenWork-Version": version,
        "X-QwenWork-Release-Version": version,
        "X-QwenWork-Build": "unknown",
        "X-QwenWork-Platform": _os_platform(),
        "X-QwenWork-Arch": _os_arch(),
        "X-QwenWork-Channel": "stable",
    }
    if cfg.get("cn"):
        headers["X-QwenWork-App-Mode"] = "standard"
    return headers


def _save_echo_account(platform: str, account_id: str, account: dict, creds: dict) -> None:
    """按 EchoBird 的存储格式落盘（与其官方客户端互通）。"""
    cfg = ECHO_PLATFORMS[platform]
    kind = cfg.get("store")
    if platform == "deepseek":
        path = os.path.join(ECHO_DIR, "deepseek-accounts", f"{account_id}.yaml")
        payload = {"summary": account, "token": creds.get("token", ""),
                   "device": creds.get("device", "")}
        _write_json(path, payload)  # JSON 是合法 YAML，serde_yaml 可读
        return
    if kind == "kilo":
        path = os.path.join(ECHO_DIR, "kilo-accounts.json")
        data = _read_json(path) or {}
        data[account_id] = {"email": account.get("identity") or account.get("email") or account_id,
                            "credential": echo_encrypt(creds.get("token", "")),
                            "expiresAt": creds.get("expires_at", 0),
                            "balance": None}
        _write_json(path, data)
        return
    if kind == "qoder":
        fname = "qoder-accounts.json" if platform == "qoder" else "qodercn-accounts.json"
        path = os.path.join(ECHO_DIR, fname)
        data = _read_json(path) or {}
        data[account_id] = {"account": account, "credential": echo_encrypt(json.dumps(creds))}
        _write_json(path, data)
        return
    if kind == "qwenwork":
        fname = "qwenwork-accounts.json" if platform == "qwenwork" else "qwenworkcn-accounts.json"
        path = os.path.join(ECHO_DIR, fname)
        data = _read_json(path) or {}
        data[account_id] = {"account": account, "credential": echo_encrypt(json.dumps(creds))}
        _write_json(path, data)
        return


async def complete_deepseek(login_id: str, code: str) -> None:
    """DSH 回调：code 换 token → profile → 存储完整会话（deepseek_oauth.rs exchange）。"""
    session = _sessions.get(login_id)
    if not session or session["flow"] != "dsh":
        return
    origin = ECHO_PLATFORMS["deepseek"]["origin"]
    device = str(uuid.uuid4())
    try:
        async with _http() as client:
            value = _dsh_unwrap(await _json_request(client, "POST", origin + "/auth-api/v0/dsh/auth_exchange", body={
                "code": code, "code_verifier": session["verifier"], "redirect_uri": session["redirect"],
                "device_id": device, "device_model": f"{_os_platform()}-{_os_arch()}", "os_version": _os_platform(),
            }))
        token = value.get("token") or ""
        if not token:
            raise EchoError("授权响应缺少 token", "auth")
        async with _http() as client:
            profile = _dsh_unwrap(await _json_request(client, "GET", origin + "/auth-api/v0/users/current",
                                                      headers={"x-dsh-auth-token": token}))
        uid = profile.get("id") or ""
        if not uid:
            raise EchoError("授权响应缺少用户 id", "auth")
        name = (profile.get("id_profile") or {}).get("name") or profile.get("mobile") \
            or profile.get("mobile_number") or profile.get("email") or uid
        account_id = _sha256_hex(f"{origin}:{uid}")
        account = {"id": account_id, "name": name, "balances": None, "active": False}
        _save_echo_account("deepseek", account_id, account,
                           {"token": token, "device": device})
        session.update({"status": "success", "account": account})
    except Exception as e:
        session.update({"status": "failed", "error": str(getattr(e, "message", e))[:200]})


async def poll_login(login_id: str) -> dict:
    session = _sessions.get(login_id)
    if not session:
        return {"status": "expired"}
    if session["status"] == "pending" and time.time() > session["expires"]:
        session["status"] = "failed"
        session["error"] = "授权超时，请重试"
    if session["status"] == "pending":
        # Kilo 无本地回调，靠轮询驱动
        if session["flow"] == "kilo":
            await _poll_kilo_once(session)
    if session["status"] == "success":
        return {"status": "success", "account": session.get("account") or {}}
    if session["status"] == "failed":
        return {"status": "failed", "error": session.get("error") or "授权失败"}
    return {"status": "pending"}


async def _poll_kilo_once(session: dict) -> None:
    cfg = ECHO_PLATFORMS["kilo"]
    try:
        async with _http() as client:
            resp = await client.get(f"{cfg['api']}/api/device-auth/codes/{session['code']}",
                                    headers={"Accept": "application/json"})
        if resp.status_code == 202:
            return
        if resp.status_code == 403:
            session.update({"status": "failed", "error": "授权被拒绝"})
            return
        if resp.status_code == 410:
            session.update({"status": "failed", "error": "授权已过期，请重试"})
            return
        value = resp.json()
        status = value.get("status")
        if status == "pending":
            return
        if status == "denied":
            session.update({"status": "failed", "error": "授权被拒绝"})
            return
        if status == "expired":
            session.update({"status": "failed", "error": "授权已过期，请重试"})
            return
        token = value.get("token") or ""
        if not token:
            session.update({"status": "failed", "error": "授权响应缺少 token"})
            return
        async with _http() as client:
            profile = await _json_request(client, "GET", cfg["api"] + "/api/profile", token=token)
        email = profile.get("email") or ""
        account_id = email or _sha256_hex(f"kilo:{token[:16]}")
        account = {"id": account_id, "email": email, "active": False, "plan": profile.get("plan")}
        expires_at = int(time.time() + 30 * DAY)
        _save_echo_account("kilo", account_id, account,
                           {"token": token, "expires_at": expires_at})
        session.update({"status": "success", "account": account})
    except EchoError as e:
        session.update({"status": "failed", "error": e.message})
    except Exception as e:
        session.update({"status": "failed", "error": str(e)[:200]})


def cancel_login(login_id: str) -> None:
    session = _sessions.pop(login_id, None)
    task = _login_tasks.pop(login_id, None)
    if task and not task.done():
        task.cancel()
    if session and session.get("flow") == "dsh":
        # 通知上游取消授权（best-effort）
        async def _cancel():
            try:
                async with _http() as client:
                    await client.post(ECHO_PLATFORMS["deepseek"]["origin"] + "/auth-api/v0/dsh/auth_cancel",
                                      json={"authorize_id": session.get("authorize_id"),
                                            "code_verifier": session.get("verifier")})
            except Exception:
                pass
        try:
            asyncio.get_running_loop().create_task(_cancel())
        except RuntimeError:
            pass


# ---------------------------------------------------------------------------
# 额度刷新

def _fmt(value) -> str:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return ""
    if abs(v - round(v)) < 1e-9:
        return str(int(round(v)))
    return f"{v:,.2f}".rstrip("0").rstrip(".")


def _pct(remaining, total):
    try:
        remaining = float(remaining)
        total = float(total)
    except (TypeError, ValueError):
        return None
    if total <= 0:
        return None
    return round(max(0.0, min(100.0, remaining / total * 100)), 1)


def account_card(platform: str, entry: dict) -> dict:
    """EchoBird 存储摘要 → 账号接入页卡片。"""
    label = platform_label(platform)
    summary = (entry.get("extra") or {}).get("summary") or {}
    daily_claimed_at = int(summary.get("daily_claimed_at") or 0)
    checkin = None
    if daily_claimed_at:
        checkin = {"claimed": True, "claimed_at": daily_claimed_at}
    supports_claim = platform in ("qoder", "qodercn")
    return {
        "id": f"echo:{platform}:{entry['account_id']}",
        "platform": platform,
        "platform_label": label,
        "provider_id": 0,
        "provider_name": "echo",
        "identity": str(entry.get("identity") or label),
        "alias": "",
        "plan": entry.get("plan"),
        "status": "active",
        "token_expires_at": int((entry.get("extra") or {}).get("expires_at") or 0),
        "quota": {"metrics": entry.get("metrics") or [], "checkin": checkin,
                  "fetched_at": entry.get("metrics_fetched_at") or 0},
        "quota_fetched_at": entry.get("metrics_fetched_at") or 0,
        "supports_quota": platform in ("deepseek", "kilo", "qoder", "qodercn", "qwenwork", "qwenworkcn"),
        "supports_claim": supports_claim,
        "via_echo": True,
        "application": platform,
        "account_id": entry["account_id"],
        "daily_claimed_at": daily_claimed_at,
        "last_error": "",
    }


async def refresh_account(platform: str, account_id: str) -> dict:
    entries = list_echo_accounts(platform)
    entry = next((e for e in entries if e["account_id"] == account_id), None)
    if not entry:
        raise EchoError("账号不存在或存储不可读", "invalid")
    cfg = ECHO_PLATFORMS[platform]
    extra = entry.get("extra") or {}
    metrics = []

    if platform == "deepseek":
        token = extra.get("token") or ""
        async with _http() as client:
            summary = _dsh_unwrap(await _json_request(client, "GET",
                                                      cfg["origin"] + "/api/v0/users/get_user_summary",
                                                      headers={"x-dsh-auth-token": token}))
        amounts: dict[str, float] = {}
        for key in ("normal_wallets", "bonus_wallets"):
            for wallet in summary.get(key) or []:
                currency = str(wallet.get("currency") or "")
                try:
                    amounts[currency] = amounts.get(currency, 0.0) + float(wallet.get("balance") or 0)
                except (TypeError, ValueError):
                    pass
        for currency, amount in amounts.items():
            metrics.append({"key": "balance", "label": currency, "value": _fmt(amount),
                            "percent": None, "reset_at": None})
        entry["metrics_fetched_at"] = int(time.time())

    elif platform == "kilo":
        token = extra.get("token") or ""
        async with _http() as client:
            balance = await _json_request(client, "GET", cfg["api"] + "/api/profile/balance", token=token)
        value = balance if isinstance(balance, (int, float)) else (balance.get("balance") if isinstance(balance, dict) else None)
        if value is not None:
            metrics.append({"key": "balance", "value": _fmt(value), "percent": None, "reset_at": None})
        entry["metrics_fetched_at"] = int(time.time())

    elif platform in ("qoder", "qodercn"):
        creds = extra.get("creds") or {}
        from app.services import qoder_quota

        # 跨区探测 + 套餐名 + 共享额度归一（qoder2api-hub 思路）。
        # 凭据失效时先续期再查一次。
        usage = await qoder_quota.fetch_usage(creds, platform, timeout=20)
        if usage.get("login_required") or (usage.get("error") and not usage.get("windows")):
            await _qoder_renew(cfg, extra, account_id)
            creds = (extra.get("creds") or {})
            usage = await qoder_quota.fetch_usage(creds, platform, timeout=20)

        plan = str(usage.get("plan") or "").strip()
        if plan:
            entry["plan"] = plan
            _save_echo_summary(platform, account_id, {"plan": plan})

        windows = usage.get("windows") or []
        # 退化守卫：上游返回全 0 且本地已有非空快照时保留旧值，避免额度被瞬时异常清零
        degraded = bool(usage.get("degraded"))
        old_metrics = entry.get("metrics") or []
        if degraded and old_metrics and any(
            m.get("value") not in (None, "", "0") for m in old_metrics
        ):
            windows = []
        for w in windows:
            remaining = w.get("remaining")
            metrics.append({
                "key": "quota", "label": w.get("name") or "Credits",
                "value": "—" if remaining is None else _fmt(remaining),
                "percent": _pct(remaining, w.get("total")),
                "reset_at": w.get("expires_at"),
            })
        if not windows and degraded and old_metrics:
            metrics = list(old_metrics)
        if not metrics and usage.get("error"):
            metrics = [{"key": "quota", "label": "额度查询失败", "value": usage["error"][:60],
                        "percent": None, "reset_at": None}]
        entry["metrics_fetched_at"] = int(time.time())
        # 签到状态：拉取 campaign 资格，已签则回写 summary 的 daily_claimed_at（跨刷新持久化）
        try:
            from app.services import qoder_campaign
            status = await qoder_campaign.status_with_creds(creds, platform)
            if status and status.get("claimed"):
                _save_echo_summary(platform, account_id, {"daily_claimed_at": status["claimed_at"]})
                extra.setdefault("summary", {})["daily_claimed_at"] = status["claimed_at"]
        except Exception:
            pass

    elif platform in ("qwenwork", "qwenworkcn"):
        creds = extra.get("creds") or {}
        auth = creds.get("auth") or {}
        headers = _qwenwork_headers(cfg, "1.0.0")
        headers["Authorization"] = "Bearer " + (auth.get("token") or "")
        try:
            async with _http() as client:
                env = await _json_request(client, "GET",
                                          cfg["base"] + "/api/v1/adapter/user/account-context?include=user,plan,quota,page,data_sharing",
                                          headers=headers)
        except EchoError:
            await _qwenwork_renew(cfg, extra, account_id)
            headers["Authorization"] = "Bearer " + (auth.get("token") or "")
            async with _http() as client:
                env = await _json_request(client, "GET",
                                          cfg["base"] + "/api/v1/adapter/user/account-context?include=user,plan,quota,page,data_sharing",
                                          headers=headers)
        user = (env.get("data") or {}).get("user") or {}
        quota_env = (env.get("data") or {}).get("quota") or env.get("quota") or {}
        buckets = []
        for a, b, name in (("user_quota", "userQuota", "Credits"), ("daily_quota", "dailyQuota", "Daily"),
                           ("monthly_quota", "monthlyQuota", "Monthly"), ("other_quota", "otherQuota", "Long-term"),
                           ("add_on_quota", "addOnQuota", "Add-on")):
            node = quota_env.get(a) if quota_env.get(a) is not None else quota_env.get(b)
            if isinstance(node, dict) and (node.get("remaining") is not None or node.get("total") is not None):
                buckets.append({"label": name, "remaining": node.get("remaining"), "total": node.get("total")})
        if not buckets and isinstance(quota_env, dict) and (quota_env.get("remaining") is not None or quota_env.get("total") is not None):
            buckets.append({"label": "Credits", "remaining": quota_env.get("remaining"), "total": quota_env.get("total")})
        for w in buckets:
            metrics.append({"key": "quota", "label": w["label"], "value": _fmt(w.get("remaining")),
                            "percent": _pct(w.get("remaining"), w.get("total")), "reset_at": None})
        entry["metrics_fetched_at"] = int(time.time())

    entry["metrics"] = metrics
    # 持久化额度快照到 echo 存储，供账号列表快照读取（否则页面始终显示"尚未查询额度"）
    if platform in ("qoder", "qodercn", "qwenwork", "qwenworkcn"):
        _save_echo_metrics(platform, account_id, metrics, int(entry["metrics_fetched_at"]))
    return account_card(platform, entry)


def _save_echo_metrics(platform: str, account_id: str, metrics: list, fetched_at: int) -> None:
    """持久化 echo 账号额度快照到存储（与 account/credential 平级，不动凭据）。"""
    fname = {"qoder": "qoder-accounts.json", "qodercn": "qodercn-accounts.json",
             "qwenwork": "qwenwork-accounts.json", "qwenworkcn": "qwenworkcn-accounts.json"}.get(platform)
    if not fname:
        return
    path = os.path.join(ECHO_DIR, fname)
    data = _read_json(path) or {}
    entry = data.get(account_id)
    if isinstance(entry, dict):
        entry["metrics"] = metrics
        entry["metrics_fetched_at"] = fetched_at
        _write_json(path, data)


def _save_echo_summary(platform: str, account_id: str, patch: dict) -> None:
    """合并更新 EchoBird 存储 summary 字段（plan 等），不影响凭据。"""
    fname = {"qoder": "qoder-accounts.json", "qodercn": "qodercn-accounts.json",
             "qwenwork": "qwenwork-accounts.json", "qwenworkcn": "qwenworkcn-accounts.json"}.get(platform)
    if not fname:
        return
    path = os.path.join(ECHO_DIR, fname)
    data = _read_json(path) or {}
    entry = data.get(account_id)
    if isinstance(entry, dict) and isinstance(entry.get("account"), dict):
        entry["account"].update({k: v for k, v in patch.items() if v is not None})
        _write_json(path, data)


def _save_echo_credential(platform: str, account_id: str, creds: dict) -> None:
    """更新 EchoBird 存储中某账号的加密凭据（续期后写回）。"""
    cfg = ECHO_PLATFORMS[platform]
    fname = {"qoder": "qoder-accounts.json", "qodercn": "qodercn-accounts.json",
             "qwenwork": "qwenwork-accounts.json", "qwenworkcn": "qwenworkcn-accounts.json"}.get(platform)
    if not fname:
        return
    path = os.path.join(ECHO_DIR, fname)
    data = _read_json(path) or {}
    entry = data.get(account_id)
    if isinstance(entry, dict):
        entry["credential"] = echo_encrypt(json.dumps(creds))
        _write_json(path, data)


_DEAD_SESSION_MARKERS = ("TOKEN_EXPIRE", "12153", "OFFLINE USER SESSION NOT FOUND",
                         "OFFLINE_USER_SESSION_NOT_FOUND")


def _is_dead_session(body: str) -> bool:
    upper = (body or "").upper()
    return any(m in upper for m in _DEAD_SESSION_MARKERS)


def _token_family(token: str) -> str:
    """按 refresh token 前缀判断凭证族（qoder2api-hub token_family）。"""
    t = str(token or "")
    if t.startswith("drt-"):
        return "device"
    if t.startswith("jrt-"):
        return "job"
    if t.startswith("pt-"):
        return "pat"
    # 旧凭据无前缀：能续即续，按调用位置推断
    return "legacy"


async def _qoder_renew(cfg: dict, extra: dict, account_id: str) -> None:
    """Qoder 令牌续期（对齐 qoder2api-hub refresh 的前缀路由策略）。

    - device 族（drt-）：POST /api/v1/deviceToken/refresh（usage/账户接口鉴权主体）
    - job 族（jrt-）：POST /api/v1/jobToken/refresh（对话网关用）
    - PAT（pt-）：POST /api/v1/jobToken/exchange 换 jt-（最终兜底，不覆盖活跃 OAuth）
    - legacy（旧凭据无前缀）：deviceToken 与 jobToken 都尽力续，兼容历史账号

    CN 版 jobToken 端点不存在（400/404）属正常；401/403 且响应含死会话标记
    时禁用账号（写 summary.active=false），提示重新登录。
    """
    creds = extra.get("creds") or {}
    base = cfg["openapi"]
    dead = False

    def _apply_device(v: dict):
        creds["device_token"] = v.get("token") or v.get("device_token") or creds.get("device_token")
        if v.get("refresh_token"):
            creds["device_refresh"] = v["refresh_token"]
        if v.get("expires_in"):
            try:
                creds["device_expires_at"] = int(time.time() * 1000) + int(v["expires_in"])
            except (TypeError, ValueError):
                pass

    def _apply_job(v: dict):
        creds["access_token"] = v.get("token") or creds.get("access_token")
        if v.get("refresh_token"):
            creds["refresh_token"] = v["refresh_token"]
        if v.get("expires_in"):
            try:
                creds["expires_at"] = int(time.time() * 1000) + int(v["expires_in"])
            except (TypeError, ValueError):
                pass

    async def _post(path: str, body: dict, apply):
        nonlocal dead
        try:
            resp = await client.post(base + path, json=body)
        except httpx.HTTPError:
            return False
        if resp.status_code == 200:
            try:
                apply(resp.json())
            except ValueError:
                return False
            return True
        text = ""
        try:
            text = resp.text[:300]
        except Exception:
            pass
        if resp.status_code in (401, 403) and _is_dead_session(text):
            dead = True
        return False

    async with _http() as client:
        d_refresh = str(creds.get("device_refresh") or "")
        j_refresh = str(creds.get("refresh_token") or "")
        pat = str(creds.get("personal_token") or creds.get("pat") or "")
        d_family = _token_family(d_refresh)
        j_family = _token_family(j_refresh)

        if d_family in ("device", "legacy"):
            await _post("/api/v1/deviceToken/refresh", {"refresh_token": d_refresh}, _apply_device)
        elif d_family in ("job", "pat") and d_refresh:
            # refresh token 落在 job 端点（少数账号刷新字段混用）
            await _post("/api/v1/jobToken/refresh", {"refresh_token": d_refresh}, _apply_job)

        job_done = False
        if j_family in ("job", "legacy") and j_refresh:
            job_done = await _post("/api/v1/jobToken/refresh", {"refresh_token": j_refresh}, _apply_job)
        elif j_family == "device" and j_refresh:
            await _post("/api/v1/deviceToken/refresh", {"refresh_token": j_refresh}, _apply_device)

        # PAT 兜底：jrt 刷新失败且存在 PAT 时用 PAT 重新换 jt-
        if not job_done and pat:
            await _post("/api/v1/jobToken/exchange", {"personal_token": pat}, _apply_job)

    if dead:
        # 死会话：禁用账号，卡片上提示重新登录（不删除历史凭据）
        try:
            _save_echo_summary(cfg_key(cfg), account_id, {"active": False})
            (extra.get("summary") or {})["active"] = False
        except Exception:
            pass
        raise EchoError("会话已失效（TOKEN_EXPIRE），请在右侧重新登录该平台账号", "login_required")
    _save_echo_credential(cfg_key(cfg), account_id, creds)


def cfg_key(cfg: dict) -> str:
    for key, value in ECHO_PLATFORMS.items():
        if value is cfg:
            return key
    return ""


async def _qwenwork_renew(cfg: dict, extra: dict, account_id: str) -> None:
    """QwenWork 令牌续期（deviceToken/refresh，按用户 isBiz 决定 target）。"""
    creds = extra.get("creds") or {}
    auth = creds.get("auth") or {}
    user = auth.get("user") or {}
    body = {"refresh_token": auth.get("refreshToken") or "",
            "target": "biz" if user.get("isBiz") is True else "c",
            "client_id": cfg["client_id"], "redirect_uri": cfg["redirect"]}
    if user.get("isBiz") is True:
        body["team_id"] = user.get("teamId")
    async with _http() as client:
        value = await _json_request(client, "POST", cfg["base"] + "/api/v1/deviceToken/refresh", body=body)
    inner = value.get("data") if isinstance(value.get("data"), dict) else value
    auth["token"] = inner.get("device_token") or inner.get("token") or auth["token"]
    auth["refreshToken"] = inner.get("refresh_token") or auth["refreshToken"]
    _save_echo_credential(cfg_key(cfg), account_id, creds)


async def delete_account(platform: str, account_id: str) -> None:
    cfg = ECHO_PLATFORMS[platform]
    kind = cfg.get("store")
    if platform == "deepseek":
        path = os.path.join(ECHO_DIR, "deepseek-accounts", f"{account_id}.yaml")
        if os.path.isfile(path):
            os.remove(path)
        return
    if kind == "kilo":
        path = os.path.join(ECHO_DIR, "kilo-accounts.json")
        data = _read_json(path) or {}
        data.pop(account_id, None)
        _write_json(path, data)
        return
    if kind in ("qoder", "qwenwork"):
        fname = {"qoder": "qoder-accounts.json", "qodercn": "qodercn-accounts.json",
                 "qwenwork": "qwenwork-accounts.json", "qwenworkcn": "qwenworkcn-accounts.json"}[platform]
        path = os.path.join(ECHO_DIR, fname)
        data = _read_json(path) or {}
        data.pop(account_id, None)
        _write_json(path, data)
        return


def echo_ready() -> bool:
    """EchoBird 兼容存储可用（有加密密钥文件，可读写已有账号）。"""
    return os.path.isfile(os.path.join(ECHO_DIR, "config", ".encryption_key"))


# ---------------------------------------------------------------------------
# Qoder CLI 凭证扫描 / 导入（移植 qoder2api-hub scan_desktop_credentials 的 CLI 分支）
# 只读扫描 ~/.qoder/.auth、~/.qoder.cn/.auth 下的 user / user.{profile} 文件，
# AES-128-CBC（key=iv=machine_id 前 16 字节）解密，绝不外泄明文令牌。
# ---------------------------------------------------------------------------

_QODER_CLI_DIRS = {
    "qoder": os.path.expanduser("~/.qoder/.auth"),
    "qodercn": os.path.expanduser("~/.qoder.cn/.auth"),
}


def _aes_cbc_decrypt_cli(cipher_b64: str, machine_key: str) -> dict:
    """CLI user 文件解密：AES-128-CBC，key=iv=machine_id[:16]。明文或密文均可。"""
    raw = (cipher_b64 or "").strip()
    if raw.startswith("{"):
        return json.loads(raw)
    key = (machine_key or "")[:16].encode("utf-8")
    if len(key) != 16:
        raise ValueError("machine_id 不足 16 字节，无法解密 CLI 凭证")
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    dec = Cipher(algorithms.AES(key), modes.CBC(key)).decryptor()
    plain = dec.update(base64.b64decode(raw)) + dec.finalize()
    pad = plain[-1]
    if isinstance(pad, int) and 0 < pad <= 16:
        plain = plain[:-pad]
    return json.loads(plain.decode("utf-8"))


def scan_qoder_cli_credentials() -> list[dict]:
    """只读扫描本机 Qoder CLI 凭证，返回候选列表（不含任何明文令牌）。"""
    found: list[dict] = []
    for platform, auth_dir in _QODER_CLI_DIRS.items():
        if not os.path.isdir(auth_dir):
            continue
        machine_key = ""
        try:
            with open(os.path.join(auth_dir, "machine_id"), encoding="utf-8") as fh:
                machine_key = fh.read().strip()
        except Exception:
            pass
        try:
            names = [n for n in os.listdir(auth_dir) if n == "user" or n.startswith("user.")]
        except Exception:
            names = []
        for name in names:
            path = os.path.join(auth_dir, name)
            item = {"platform": platform, "path": path, "file": name,
                    "readable": False, "valid": False, "uid": "",
                    "nickname": "", "expires_at": 0, "error": ""}
            try:
                data = _aes_cbc_decrypt_cli(
                    open(path, encoding="utf-8").read(), machine_key)
                token = str(data.get("access_token") or data.get("token") or "")
                item["readable"] = True
                item["valid"] = token.startswith(("dt-", "jt-", "pt-"))
                item["uid"] = str(data.get("uid") or "")
                item["nickname"] = str(data.get("name") or "")
                exp = data.get("expire_time") or data.get("expires_at") or 0
                try:
                    exp = int(exp)
                    if exp > 10_000_000_000:
                        exp //= 1000
                except (TypeError, ValueError):
                    exp = 0
                item["expires_at"] = exp
            except Exception as e:
                item["error"] = str(e)[:160]
            found.append(item)
    return found


def import_qoder_cli_credential(path: str, platform: str | None = None) -> dict:
    """把一个 CLI 凭证文件导入 EchoBird 兼容存储（供账号接入页展示/使用）。"""
    if not os.path.isfile(path):
        raise EchoError("凭证文件不存在", "invalid")
    target = platform or ""
    if not target:
        for p, auth_dir in _QODER_CLI_DIRS.items():
            if os.path.abspath(path).startswith(os.path.abspath(auth_dir)):
                target = p
                break
    if target not in ("qoder", "qodercn"):
        raise EchoError("无法判断凭证所属平台，请指定 platform", "invalid")
    auth_dir = os.path.dirname(path)
    machine_key = ""
    try:
        with open(os.path.join(auth_dir, "machine_id"), encoding="utf-8") as fh:
            machine_key = fh.read().strip()
    except Exception:
        pass
    data = _aes_cbc_decrypt_cli(open(path, encoding="utf-8").read(), machine_key)
    token = str(data.get("access_token") or data.get("token") or "")
    if not token:
        raise EchoError("凭证中没有 access token", "invalid")
    uid = str(data.get("uid") or "") or ("d-" + uuid.uuid4().hex[:24])
    cfg = ECHO_PLATFORMS[target]
    exp = data.get("expire_time") or data.get("expires_at") or 0
    try:
        exp = int(exp)
        if exp > 10_000_000_000:
            exp //= 1000
    except (TypeError, ValueError):
        exp = 0
    exp_ms = exp * 1000 if exp else int(time.time() * 1000) + 30 * 86400_000
    nickname = str(data.get("name") or "")
    personal_token = str(data.get("personal_token") or "") if token.startswith("pt-") else ""
    creds = {
        "edition": target,
        "access_token": token, "refresh_token": str(data.get("refresh_token") or ""),
        "expires_at": exp_ms,
        "user_id": uid, "name": nickname, "email": str(data.get("email") or ""),
        "machine_id": machine_key or str(data.get("machine_id") or uuid.uuid4().hex),
        "device_token": token if token.startswith("dt-") else "",
        "device_refresh": str(data.get("refresh_token") or "") if token.startswith("dt-") else "",
        "device_expires_at": exp_ms,
        "device_chat": token.startswith("dt-"),
        "personal_token": personal_token,
    }
    account_id = _sha256_hex(f"{cfg['tool']}\0{uid}")
    account = {"id": account_id, "edition": target, "name": nickname or uid[:10],
               "email": data.get("email") or None, "plan": None, "active": True,
               "remaining": None, "total": None, "expiresAt": None}
    _save_echo_account(target, account_id, account, creds)
    return {"platform": target, "account_id": account_id,
            "identity": nickname or uid[:10], "uid": uid}
