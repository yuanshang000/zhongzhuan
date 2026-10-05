"""运行时设置 API：页面可修改存储路径等参数，无需重启服务。"""
from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.services.runtime_settings import get_all, reset, update
from app.services.storage import get_storage

router = APIRouter(prefix="/api/settings", tags=["settings"])


class SettingsOut(BaseModel):
    local_storage_dir: str
    storage_backend: str
    multipart_chunk_size: int
    direct_upload_threshold: int
    # 附带信息，方便前端展示
    current_root: str = ""
    exists: bool = True
    free_space: int = 0
    used_space: int = 0


class SettingsIn(BaseModel):
    local_storage_dir: str | None = Field(None, description="存储根目录，留空则不修改")
    storage_backend: str | None = None
    multipart_chunk_size: int | None = None
    direct_upload_threshold: int | None = None


def _dir_info(p: Path) -> tuple[bool, int, int]:
    """返回 (是否存在, 剩余空间, 已用空间)。"""
    if not p.exists():
        return False, 0, 0
    try:
        usage = shutil.disk_usage(p)
        return True, usage.free, usage.used
    except Exception:
        return True, 0, 0


@router.get("", response_model=SettingsOut)
def read_settings():
    cfg = get_all()
    root = Path(cfg["local_storage_dir"])
    exists, free, used = _dir_info(root)
    return SettingsOut(
        **cfg,
        current_root=str(root),
        exists=exists,
        free_space=free,
        used_space=used,
    )


@router.put("", response_model=SettingsOut)
async def write_settings(payload: SettingsIn):
    """更新设置。存储路径变更立即生效，无需重启。"""
    patch = {k: v for k, v in payload.model_dump().items() if v is not None}
    if not patch:
        return read_settings()

    try:
        update(patch)
    except ValueError as e:
        from app.core.errors import ApiError

        raise ApiError(400, str(e), "invalid_setting")

    # 主动触发一次存储重建，确保新路径立即生效
    get_storage()

    return read_settings()


@router.post("/reset", response_model=SettingsOut)
async def reset_settings():
    reset()
    get_storage()
    return read_settings()


@router.post("/browse")
def browse(path: str = ""):
    """列出候选目录，供页面选择。"""
    base = Path(path).expanduser() if path else Path.home()
    if not base.exists() or not base.is_dir():
        return {"path": str(base), "exists": False, "entries": []}

    entries = []
    try:
        for item in sorted(base.iterdir(), key=lambda x: x.name.lower()):
            if item.name.startswith("."):
                continue
            entries.append({
                "name": item.name,
                "path": str(item),
                "is_dir": item.is_dir(),
            })
            if len(entries) >= 300:
                break
    except PermissionError:
        pass

    return {"path": str(base), "exists": True, "entries": entries}