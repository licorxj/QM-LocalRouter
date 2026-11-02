"""Desktop shell entry point for the frozen LocalRouter app.

Starts the same FastAPI app as launcher.py on a background thread, then
opens a native WebView2 window on the local UI, so the packaged app behaves
like a desktop program instead of a console service. Closing the window
shuts the server down and exits. launcher.py stays the console/server-mode
entry point (apirouter.exe + run.bat).
"""
import logging
import os
import socket
import sys
import threading
import time
import urllib.request
import webbrowser
from ctypes import windll

import uvicorn


def _message_box(text: str) -> None:
    # console=False build: a native message box is the only way to reach the user
    windll.user32.MessageBoxW(None, text, "LocalRouter", 0x10)


def _port_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def _serving(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=3):
            return True
    except Exception:
        return False


def main() -> int:
    print("[desktop] launcher starting", flush=True)
    host = os.environ.get("HOST", "127.0.0.1")
    try:
        port = int(os.environ.get("BACKEND_PORT", "12002"))
    except ValueError:
        port = 12002
    url = f"http://{host}:{port}"

    if not _port_free(host, port):
        print("[desktop] port busy", flush=True)
        if _serving(url):
            # another LocalRouter instance already owns the port
            webbrowser.open(url)
            return 0
        _message_box(
            f"端口 {port} 已被其他程序占用，无法启动。\n"
            f"Port {port} is already in use by another application.\n\n"
            f"可通过安装目录下的 .env 修改 BACKEND_PORT。"
        )
        return 1

    # Frozen build has no console: keep a file log next to the user data.
    from app.config import DATA_DIR

    print("[desktop] app.config imported", flush=True)
    logging.basicConfig(
        filename=str(DATA_DIR / "desktop.log"),
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    from app.main import app

    config = uvicorn.Config(app, host=host, port=port, log_config=None)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    for _ in range(150):  # up to 30s: onefile extraction + app startup
        if server.started:
            break
        if not thread.is_alive():
            _message_box("服务启动失败，详见安装目录 data\\desktop.log。")
            return 1
        time.sleep(0.2)
    if not server.started:
        _message_box("服务启动超时，详见安装目录 data\\desktop.log。")
        return 1
    print("[desktop] server started", flush=True)

    try:
        import webview

        print("[desktop] opening webview window", flush=True)
        webview.create_window(
            "LocalRouter", url, width=1280, height=820, min_size=(960, 640)
        )
        webview.start()
    except Exception as exc:  # e.g. WebView2 runtime missing
        logging.getLogger(__name__).exception("webview failed")
        _message_box(
            f"桌面窗口启动失败（{exc}），已在默认浏览器中打开。\n"
            f"请安装 Microsoft Edge WebView2 Runtime 后重试。"
        )
        webbrowser.open(url)
        return 1

    server.should_exit = True
    thread.join(timeout=10)
    return 0


if __name__ == "__main__":
    sys.exit(main())
