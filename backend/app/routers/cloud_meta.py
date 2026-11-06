"""云端软件元数据路由：仪表盘的软件公告与赞助商席位。"""
from fastapi import APIRouter

from app.services import cloud_meta

router = APIRouter(prefix="/api/cloud", tags=["cloud-meta"])


@router.get("/meta")
async def get_cloud_meta(force: bool = False):
    """软件公告 + 软件数据（含赞助商席位），5 分钟内存缓存，force 跳过。"""
    return await cloud_meta.fetch_cloud_meta(force=force)
