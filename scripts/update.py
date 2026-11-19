#!/usr/bin/env python3
"""Download and apply the latest LocalRouter release.

Usage:
  python scripts/update.py [--dry-run] [--asset NAME] [--yes] [--repo owner/name]

Behaviour by install kind:
  * source checkout (.git exists)  → `git pull --ff-only` + hints for
    pip/npm when requirements or frontend files changed;
  * frozen installer build (Windows) → downloads the new
    LocalRouter-Setup-*.exe and launches it silently (Inno keeps data and
    restarts the app);
  * frozen portable build / plain zip layout → downloads apirouter-<os>.zip,
    extracts into `update_pending/` and spawns a detached helper script that
    swaps files after this process exits (data/ and .env are preserved).

Exit codes: 0 = up to date / done  |  1 = error  |  10 = update available
(--dry-run reports the plan without executing)
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _update_lib as lib  # noqa: E402

IS_WIN = sys.platform.startswith("win")
INNO_FLAGS = ["/VERYSILENT", "/SUPPRESSMSGBOXES", "/CLOSEAPPLICATIONS",
              "/FORCECLOSEAPPLICATIONS", "/RESTARTAPPLICATIONS"]
PROTECTED = {"data", ".env"}


def git_flow(repo_root: Path, yes: bool) -> int:
    print("检测到源码目录（.git），使用 git 更新流程")
    run = lambda *args: subprocess.run(args, cwd=str(repo_root), capture_output=True, text=True)

    branch = run("git", "branch", "--show-current").stdout.strip() or "main"
    if (code := run("git", "fetch", "origin", branch)).returncode != 0:
        print(f"[ERROR] git fetch 失败: {code.stderr or code.stdout}")
        return 1
    dirty = run("git", "status", "--porcelain").stdout.strip()
    if dirty:
        print("[ERROR] 工作区存在未提交的本地修改，请先 commit / stash 再更新")
        return 1
    behind = run("git", "rev-list", "--count", f"HEAD..FETCH_HEAD").stdout.strip()
    behind_n = int(behind or 0)
    if behind_n == 0:
        print("已是最新，无需更新")
        return 0
    if not yes:
        answer = input(f"将拉取 {behind_n} 个提交并更新依赖，继续? [y/N] ")
        if answer.strip().lower() != "y":
            print("已取消")
            return 0

    old = run("git", "rev-parse", "HEAD").stdout.strip()
    if (code := run("git", "pull", "--ff-only", "origin", branch)).returncode != 0:
        print(f"[ERROR] git pull 失败: {code.stderr or code.stdout}")
        return 1
    changed = run("git", "diff", "--name-only", old, "HEAD").stdout.splitlines()

    if any(f == "requirements.txt" or f.startswith("backend/") for f in changed):
        print("→ 后端依赖可能变化: 建议执行 pip install -r requirements.txt（venv: backend\\venv）")
    if any(f.startswith("frontend/src") or f == "frontend/package.json" for f in changed):
        print("→ 前端代码已更新: 正在构建前端 (npm install + npm run build)…")
        npm = shutil.which("npm")
        if not npm:
            print("  [WARN] 未找到 npm，请手动执行 cd frontend && npm install && npm run build")
        else:
            for cmd in ([npm, "install"], [npm, "run", "build"]):
                r = subprocess.run(cmd, cwd=str(repo_root / "frontend"))
                if r.returncode != 0:
                    print(f"  [ERROR] {' '.join(cmd)} 失败")
                    return 1
    if any(f.startswith("backend/") for f in changed):
        print("→ 后端代码已更新: 请重启后端进程使新代码生效")
    print(f"✔ 已更新到最新代码 ({behind_n} 个提交)")
    return 0


def installer_flow(asset: dict, yes: bool) -> int:
    print(f"下载安装包: {asset['name']} ({asset['size'] // 1024 // 1024} MB)")
    with tempfile.TemporaryDirectory(prefix="localrouter-update-") as tmp:
        dest = lib.download(asset["url"], Path(tmp) / asset["name"],
                            progress=lambda done, total: print(
                                f"\r  {done // 1024 // 1024} / {total // 1024 // 1024} MB",
                                end="" if done < total else "  完成\n"))
        print(f"已保存: {dest}")
        if not yes:
            answer = input("将静默运行安装器（自动关闭当前应用并在完成后重启），继续? [y/N] ")
            if answer.strip().lower() != "y":
                print(f"已取消。安装包保留在: {dest}（可手动运行）")
                return 0
        print("启动静默安装器…（应用将自动关闭并在安装完成后重启）")
        subprocess.Popen([str(dest), *INNO_FLAGS], cwd=tmp,
                         creationflags=0x00000008 | 0x00000200, close_fds=True)
    return 0


def portable_flow(asset: dict, yes: bool, repo_root: Path) -> int:
    target_dir = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else repo_root
    print(f"下载便携更新包: {asset['name']} ({asset['size'] // 1024 // 1024} MB)")
    pending = target_dir / "update_pending"
    with tempfile.TemporaryDirectory(prefix="localrouter-update-") as tmp:
        dest = lib.download(asset["url"], Path(tmp) / asset["name"])
        print(f"已保存: {dest}")
        if pending.exists():
            shutil.rmtree(pending, ignore_errors=True)
        pending.mkdir(parents=True)
        with zipfile.ZipFile(dest) as zf:
            for member in zf.infolist():
                if member.is_dir():
                    continue
                rel = Path(member.filename)
                if any(p.lower() in PROTECTED for p in rel.parts):
                    continue  # keep user data / local .env
                zf.extract(member, pending)
        print(f"已解压至: {pending}")

    exe_name = Path(sys.executable).name if getattr(sys, "frozen", False) else "apirouter.exe"
    if IS_WIN:
        helper = target_dir / "apply_update.bat"
        helper.write_text(
            "@echo off\r\n"
            "rem LocalRouter portable update helper (auto-generated)\r\n"
            f"set \"PARENT={target_dir}\"\r\n"
            f"set \"PID={os.getpid()}\"\r\n"
            ":waitloop\r\n"
            "tasklist /FI \"PID eq %PID%\" 2>NUL | find /I \"%PID%\" >NUL\r\n"
            "if not errorlevel 1 (\r\n"
            "  timeout /t 1 /nobreak >NUL\r\n"
            "  goto waitloop\r\n"
            ")\r\n"
            f"if exist \"%PARENT%\\{exe_name}\" move /y \"%PARENT%\\{exe_name}\" \"%PARENT%\\{exe_name}.old\" >NUL\r\n"
            f"xcopy /E /I /Y \"{pending}\\*\" \"%PARENT%\\\" >NUL\r\n"
            f"rd /s /q \"{pending}\"\r\n"
            f"start \"\" \"%PARENT%\\{exe_name}\"\r\n"
            "del \"%~f0\"\r\n",
            encoding="gbk", errors="replace",
        )
        cmd = ["cmd", "/c", str(helper)]
    else:
        helper = target_dir / "apply_update.sh"
        helper.write_text(
            "#!/bin/sh\n"
            f"PID={os.getpid()}\n"
            "while kill -0 \"$PID\" 2>/dev/null; do sleep 1; done\n"
            f"cd \"{target_dir}\" || exit 1\n"
            f"[ -f \"{exe_name}\" ] && mv -f \"{exe_name}\" \"{exe_name}.old\" 2>/dev/null\n"
            f"cp -rf \"{pending}/.\" \"{target_dir}/\"\n"
            f"rm -rf \"{pending}\"\n"
            f"chmod +x \"{exe_name}\" 2>/dev/null\n"
            f"nohup \"./{exe_name}\" >/dev/null 2>&1 &\n"
            "rm -f \"$0\"\n",
            encoding="utf-8",
        )
        helper.chmod(0o755)
        cmd = ["sh", str(helper)]

    print("已生成换装助手脚本，本进程退出后将自动替换文件并重启")
    if not yes:
        answer = input("确认退出当前应用并立即应用更新? [y/N] ")
        if answer.strip().lower() != "y":
            print(f"已取消。更新文件保留在 {pending}，下次运行 {helper} 可手动应用")
            return 0
    subprocess.Popen(cmd, cwd=str(target_dir), start_new_session=IS_WIN is False,
                     creationflags=0x00000008 | 0x00000200 if IS_WIN else 0)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="只打印将执行的操作")
    parser.add_argument("--asset", default=None, help="手动指定 release 资产名")
    parser.add_argument("--yes", "-y", action="store_true", help="跳过确认")
    parser.add_argument("--repo", default=None, help="override owner/name")
    args = parser.parse_args()

    repo = args.repo or lib.get_repo()
    root = Path(__file__).resolve().parent.parent
    mode = lib.get_mode(root)
    local = lib.local_version(root)

    try:
        release = lib.fetch_latest_release(repo, force=True)
    except Exception as exc:  # noqa: BLE001
        print(f"[ERROR] 无法获取最新版本: {type(exc).__name__}: {exc}")
        return 1

    newer = lib.is_newer(release["version"], local["version"])
    print(f"本地版本: {local['version']}   最新版本: {release['version']}   模式: {mode}")
    if not newer and not args.asset:
        print("已是最新，无需更新")
        return 0

    asset = lib.pick_asset(release, args.asset, root)
    if not asset:
        print("[ERROR] 该 release 没有匹配当前平台的资产: "
              + ", ".join(a["name"] for a in release["assets"]))
        return 1
    print(f"选定资产: {asset['name']}")

    if args.dry_run:
        if mode == "git":
            print("计划: git fetch + git pull --ff-only + 依赖/前端构建提示")
        else:
            print(f"计划: 下载 {asset['name']} → "
                  + ("静默运行安装器" if asset["name"].lower().endswith(".exe")
                     else "打开 pkg 安装器" if asset["name"].endswith(".pkg")
                     else "解压换装 + 助手脚本重启"))
        return 0

    if mode == "git":
        return git_flow(root, args.yes)
    if asset["name"].lower().endswith(".exe"):
        return installer_flow(asset, args.yes)
    if asset["name"].endswith(".pkg"):
        dest = lib.download(asset["url"], Path(tempfile.gettempdir()) / asset["name"])
        print(f"已下载安装包: {dest}，即将打开安装器（按提示完成安装）")
        subprocess.Popen(["open", str(dest)])
        return 0
    return portable_flow(asset, args.yes, root)


if __name__ == "__main__":
    raise SystemExit(main())
