"""存储容量 API：返回真实磁盘数据。"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import get_db
from app.models.node import Node, NodeType
from app.services.capacity import monitor
from app.services.runtime_settings import get_runtime

router = APIRouter(prefix="/api/capacity", tags=["capacity"])


class CapacityOut(BaseModel):
    """容量信息（单位：字节）。"""

    # 磁盘（存储目录所在分区）
    disk_total: int = 0
    disk_free: int = 0
    disk_used: int = 0
    disk_percent: float = 0.0

    # CloudRive 占用
    drive_used: int = 0
    file_count: int = 0
    folder_count: int = 0

    # 配额（未设则回退为磁盘可用空间）
    quota_total: int = 0
    quota_used: int = 0
    quota_free: int = 0
    quota_percent: float = 0.0

    # 来源信息，便于前端展示
    storage_path: str = ""
    backend: str = "local"


def _db_usage(db: Session) -> tuple[int, int, int]:
    """数据库汇总：(文件数, 文件夹数, 字节数)。比扫盘快得多。"""
    files = db.execute(
        select(func.count(Node.id), func.coalesce(func.sum(Node.size), 0))
        .where(Node.type == NodeType.FILE.value)
    ).one()
    folders = db.execute(
        select(func.count(Node.id)).where(Node.type == NodeType.FOLDER.value)
    ).scalar_one()
    return files[0] or 0, folders or 0, int(files[1] or 0)


def _quota_total() -> int | None:
    """存储配额上限。未配置时返回 None，表示用磁盘剩余空间。

    支持写法：纯字节数字、10GB、500MB、2TB、1.5GB（不区分大小写）。
    """
    raw = (settings.storage_quota or "").strip()
    if not raw:
        return None

    text = raw.upper().replace(" ", "")

    units = {
        "KB": 1024, "MB": 1024 ** 2, "GB": 1024 ** 3, "TB": 1024 ** 4,
        "PB": 1024 ** 5,
        "K": 1024, "M": 1024 ** 2, "G": 1024 ** 3, "T": 1024 ** 4, "P": 1024 ** 5,
        "B": 1,
    }

    # 先剥离单位后缀，再解析数字
    for suffix in ("PB", "TB", "GB", "MB", "KB"):
        if text.endswith(suffix):
            num = text[: -len(suffix)]
            try:
                return int(float(num) * units[suffix])
            except ValueError:
                return None

    for suffix in ("P", "T", "G", "M", "K"):
        if text.endswith(suffix):
            num = text[:-1]
            try:
                return int(float(num) * units[suffix])
            except ValueError:
                return None

    # 无后缀，纯字节
    try:
        return int(float(text))
    except ValueError:
        return None


@router.get("", response_model=CapacityOut)
def capacity(
    refresh: bool = Query(False, description="强制刷新，跳过缓存"),
    db: Session = Depends(get_db),
):
    """获取存储容量。

    数据全部来自真实设备：
    - 磁盘总量/可用：shutil.disk_usage 查询存储目录所在分区
    - 已用：数据库汇总的文件字节数
    """
    backend = get_runtime("storage_backend")
    files, folders, used = _db_usage(db)

    # S3 后端没有本地磁盘概念，磁盘字段置0，由前端显示对象存储
    if backend != "local":
        return CapacityOut(
            disk_total=0, disk_free=0, disk_used=0, disk_percent=0.0,
            drive_used=used, file_count=files, folder_count=folders,
            quota_total=0, quota_used=used, quota_free=0, quota_percent=0.0,
            storage_path=get_runtime("s3_bucket") or "", backend=backend,
        )

    root = Path(get_runtime("local_storage_dir"))
    quota = _quota_total()

    info = monitor.get(
        root,
        db_used=used,
        db_files=files,
        db_folders=folders,
        quota_total=quota,
        force=refresh,
    )

    # 未设配额时，用磁盘剩余空间作为"可用上限"
    if quota is None:
        quota_total = info.total
        quota_free = info.free
    else:
        quota_total = quota
        quota_free = info.quota_free or 0

    quota_used = info.drive_used
    quota_percent = min(100.0, quota_used / quota_total * 100) if quota_total else 0.0

    return CapacityOut(
        disk_total=info.total,
        disk_free=info.free,
        disk_used=info.used,
        disk_percent=round(info.usage_ratio, 2),
        drive_used=info.drive_used,
        file_count=files,
        folder_count=folders,
        quota_total=quota_total,
        quota_used=quota_used,
        quota_free=quota_free,
        quota_percent=round(quota_percent, 2),
        storage_path=str(root),
        backend=backend,
    )


@router.post("/refresh")
def capacity_refresh(db: Session = Depends(get_db)):
    """强制刷新（清缓存后重新统计）。文件增删后前端调用。"""
    if get_runtime("storage_backend") == "local":
        monitor.invalidate(Path(get_runtime("local_storage_dir")))
    return capacity(refresh=True, db=db)