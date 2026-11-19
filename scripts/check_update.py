#!/usr/bin/env python3
"""Check LocalRouter's local version against the latest GitHub release.

Usage:
  python scripts/check_update.py [--json] [--repo owner/name]

Exit codes:  0 = up to date  |  10 = update available  |  1 = check failed
(stdlib only — safe to run with any Python, no venv required)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _update_lib as lib  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--repo", default=None, help="override owner/name")
    args = parser.parse_args()

    repo = args.repo or lib.get_repo()
    local = lib.local_version()

    try:
        release = lib.fetch_latest_release(repo, force=True)
    except Exception as exc:  # noqa: BLE001
        payload = {"error": f"{type(exc).__name__}: {exc}", "local": local, "repo": repo}
        if args.json:
            print(json.dumps(payload, ensure_ascii=False))
        else:
            print(f"[ERROR] 无法获取最新版本: {payload['error']}")
        return 1

    payload = {
        "local_version": local["version"],
        "local": local,
        "latest_version": release["version"],
        "update_available": lib.is_newer(release["version"], local["version"]),
        "release_url": release["url"],
        "published_at": release["published_at"],
        "prerelease": release["prerelease"],
        "repo": repo,
    }

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"本地版本: {payload['local_version']}"
              + (f"  (tag {local['git_tag']}, commit {str(local['git_commit'])[:7]})" if local.get("git_tag") else ""))
        print(f"最新版本: {release['version']}"
              + ("  (pre-release)" if release["prerelease"] else ""))
        print(f"发布时间: {release['published_at']}")
        if payload["update_available"]:
            print(f"状态: ★ 有新版本可用 → {release['url']}")
        else:
            print("状态: 已是最新")

    return 10 if payload["update_available"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
