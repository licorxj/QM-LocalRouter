"""Project self-update endpoints (/api/update).

* GET  /check  — current vs latest (GitHub Releases for frozen installs,
                 git behind-count for source checkouts)
* POST /apply  — download + apply (release mode) or git pull (git mode);
                 ``dry_run`` returns the plan without touching anything
* GET  /status — live download/apply progress
"""
from __future__ import annotations

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services import app_update as svc

router = APIRouter(prefix="/api/update", tags=["update"])


class ApplyBody(BaseModel):
    asset: str | None = None
    dry_run: bool = False


@router.get("/check")
async def check_update(fetch: bool = True):
    """Unified check. ``mode`` decides which fields the UI should present.

    Git-mode failures (missing remote access, dirty tree) degrade gracefully:
    the release comparison still answers "is there a newer version".
    """
    mode = svc.get_mode()
    info = svc.get_version_info()
    result: dict = {
        "mode": mode,
        "current_version": info.get("version"),
        "build_type": info.get("build_type"),
        "git_tag": info.get("git_tag"),
        "check_failed": False,
        "error": None,
    }
    git_error = None

    if mode == "git":
        # Source checkout: git status gives the authoritative behind-count.
        # When it fails (offline / no remote access) fall back to comparing
        # the release tag so the card still reports available updates.
        from .settings import _repository_status

        try:
            git = await _repository_status(fetch=fetch)
            result["git"] = git
            result["update_available"] = git["update_available"]
        except HTTPException as exc:
            git_error = str(exc.detail)
        except Exception as exc:  # noqa: BLE001
            git_error = str(exc)
        if git_error:
            result["git"] = None

    # Release metadata (display in both modes; drives fallback comparison).
    latest_version = None
    release_url = None
    try:
        release = await svc.fetch_latest_release()
        latest_version = release.get("version")
        result["latest_version"] = latest_version
        result["release_url"] = release.get("url")
        result["release_notes"] = release.get("notes")
        result["published_at"] = release.get("published_at")
        result["prerelease"] = release.get("prerelease")
        if mode == "release":
            asset = svc.pick_asset(release)
            result["asset"] = asset
    except Exception as exc:  # noqa: BLE001
        result["release_error"] = f"{type(exc).__name__}: {exc}"

    if mode == "release":
        result["update_available"] = svc.is_newer(latest_version, info.get("version"))
    elif "update_available" not in result:
        # git status failed — answer from the release tag instead.
        result["update_available"] = svc.is_newer(latest_version, info.get("version"))
        result["git_degraded"] = git_error
        if git_error:
            result["error"] = git_error

    return result


@router.post("/apply")
async def apply_update(body: ApplyBody):
    mode = svc.get_mode()

    if mode == "git":
        from .settings import _repository_status, update_repository

        if body.dry_run:
            try:
                git = await _repository_status(fetch=True)
                return {"mode": "git", "dry_run": True,
                        "plan": ["git pull --ff-only origin " + git["branch"],
                                 "npm install + npm run build (when frontend changed)",
                                 "restart backend (when backend code changed)"],
                        "behind_count": git["behind_count"],
                        "working_tree_clean": git["working_tree_clean"],
                        "update_available": git["update_available"]}
            except HTTPException as exc:
                raise HTTPException(exc.status_code, exc.detail) from exc

        return await update_repository()

    if body.dry_run:
        try:
            return await svc.start_apply(asset_name=body.asset, dry_run=True)
        except httpx.HTTPError as exc:
            raise HTTPException(502, f"GitHub unreachable: {exc}") from exc
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc

    if svc._state.phase in ("checking", "downloading", "applying"):
        raise HTTPException(409, "An update is already in progress")
    try:
        return await svc.start_apply(asset_name=body.asset, dry_run=False)
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"GitHub unreachable: {exc}") from exc
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/status")
async def update_status():
    return svc._state.as_dict()
