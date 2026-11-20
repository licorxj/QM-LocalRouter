"""Qoder 签到 / 领奖动作流水（移植 qoder2api-hub qoder_activity.py）。

账号 JSON 里只有 daily_claimed_at 与活动状态，看不出「哪一天、哪个号、做了什么、
成了没有」。这里追加一行一个事件到 ``~/.echobird/qoder-activity.jsonl``，
按时间倒序展示；流水异常一律吞掉，绝不影响签到主流程。
"""
import json
import os
import time

ECHO_DIR = os.path.expanduser("~/.echobird")
ACTIVITY_FILE = os.path.join(ECHO_DIR, "qoder-activity.jsonl")
MAX_BYTES = 5 * 1024 * 1024
KEEP_DAYS = 30


def record(kind: str, ok: bool, uid: str = "", nickname: str = "",
           realm: str = "", detail: str = "", amount: int = 0) -> None:
    """追加一条事件；任何异常都吞掉（流水不能影响主流程）。"""
    try:
        try:
            if os.path.getsize(ACTIVITY_FILE) > MAX_BYTES:
                os.replace(ACTIVITY_FILE, ACTIVITY_FILE + ".1")
        except Exception:
            pass
        row = {"ts": time.time(), "kind": str(kind or ""), "ok": bool(ok),
               "uid": (uid or "")[:8], "nickname": nickname or "",
               "realm": realm or "", "detail": str(detail or "")[:200],
               "amount": amount or 0}
        os.makedirs(ECHO_DIR, exist_ok=True)
        with open(ACTIVITY_FILE, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        pass


def recent(limit: int = 200, days: int = KEEP_DAYS) -> list[dict]:
    """读最近事件（按时间倒序）；过期与坏行跳过。"""
    cutoff = time.time() - days * 86400
    out: list[dict] = []
    seq = 0
    try:
        with open(ACTIVITY_FILE, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                    if float(row.get("ts") or 0) < cutoff:
                        continue
                except Exception:
                    continue
                seq += 1
                row["_seq"] = seq
                out.append(row)
    except FileNotFoundError:
        return []
    except Exception:
        return out
    out.sort(key=lambda r: (r.get("ts") or 0, r.get("_seq") or 0), reverse=True)
    for row in out:
        row.pop("_seq", None)
    try:
        limit = max(1, min(1000, int(limit)))
    except (TypeError, ValueError):
        limit = 200
    return out[:limit]
