"""磁盘容量统计：读取真实设备数据，不做任何模拟。

区分两个概念：
- **磁盘总量/可用**：存储目录所在分区的真实容量（shutil.disk_usage）
- **已用**：CloudRive 自身占用的空间（遍历统计或数据库汇总）

跨盘符/挂载点场景下，磁盘总量必须按"存储目录实际落在哪个分区"来算，
所以统一用存储目录本身去查，而不是写死某个盘。
"""
from __future__ import annotations

import os
import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class DiskInfo:
    """磁盘容量信息（单位：字节）。"""

    total: int = 0# 分区总容量
    free: int = 0         # 分区剩余可用
    used: int = 0         # 分区已用（total - free）
    # CloudRive 占用
    drive_used: int = 0   # 存储目录内实际文件总大小
    file_count: int = 0   # 文件数
    dir_count: int = 0# 文件夹数
    # 配额（可选）
    quota_total: int | None = None   # 存储配额上限
    quota_used: int = 0              # 配额内已用

    @property
    def quota_free(self) -> int | None:
        """配额剩余。返回 None 表示未设配额。"""
        if self.quota_total is None:
            return None
        return max(0, self.quota_total - self.quota_used)

    @property
    def usage_ratio(self) -> float:
        """存储目录占用磁盘的百分比（0-100）。"""
        if self.total <= 0:
            return 0.0
        return min(100.0, self.used / self.total * 100)

    @property
    def quota_ratio(self) -> float | None:
        """配额使用百分比。未设配额返回 None。"""
        if not self.quota_total:
            return None
        return min(100.0, self.quota_used / self.quota_total * 100)


def disk_usage_of(path: Path) -> tuple[int, int, int]:
    """查询某路径所在分区的 (total, used, free)。

    Windows/Linux/macOS 通用。路径不存在时向上找最近的存在的父目录。
    """
    p = Path(path)
    while not p.exists() and p.parent != p:
        p = p.parent
    try:
        usage = shutil.disk_usage(p)
        return usage.total, usage.used, usage.free
    except Exception:
        return 0, 0, 0


def _dir_size(root: Path) -> tuple[int, int]:
    """递归统计目录内文件总大小与数量。跳过 .parts 临时目录。"""
    total = 0
    count = 0
    if not root.exists():
        return 0, 0

    for dirpath, dirnames, filenames in os.walk(root, onerror=lambda e: None):
        # 跳过临时分片目录（未完成上传，不算正式占用）
        dirnames[:] = [d for d in dirnames if d != ".parts"]
        for name in filenames:
            fp = Path(dirpath) / name
            try:
                st = fp.stat()
                # 只统计普通文件，排除符号链接
                if fp.is_symlink() or not fp.is_file():
                    continue
                total += st.st_size
                count += 1
            except (OSError, ValueError):
                continue
    return total, count


class CapacityMonitor:
    """容量监测器，带缓存。

    遍历磁盘统计比较慢（大目录几秒），因此：
    - 短时间内重复请求走缓存
    - 超过 TTL 才重新扫描
    """

    def __init__(self, ttl: float = 8.0):
        self._lock = threading.Lock()
        self._cache: dict[str, tuple[float, DiskInfo]] = {}
        self.ttl = ttl

    def get(
        self,
        root: Path,
        db_used: int = 0,
        db_files: int = 0,
        db_folders: int = 0,
        quota_total: int | None = None,
        force: bool = False,
    ) -> DiskInfo:
        """获取容量信息。

        root        存储根目录（决定查哪个分区的容量）
        db_used     数据库记录的已用字节数（快速来源）
        quota_total 配额上限，None 表示用磁盘剩余空间
        """
        key = str(Path(root).resolve()) if Path(root).exists() else str(root)

        now = time.time()
        if not force:
            with self._lock:
                hit = self._cache.get(key)
                if hit and now - hit[0] < self.ttl:
                    return hit[1]

        total, used, free = disk_usage_of(Path(root))

        # 已用容量：优先用数据库汇总（快），否则扫磁盘（准）
        # 两者差异来自"已删除但未清理的残留"，数据库为准
        drive_used = db_used
        if drive_used == 0:
            drive_used, _ = _dir_size(Path(root))

        info = DiskInfo(
            total=total,
            free=free,
            used=used,
            drive_used=drive_used,
            file_count=db_files,
            dir_count=db_folders,
            quota_total=quota_total,
            quota_used=drive_used,
        )

        with self._lock:
            self._cache[key] = (now, info)
        return info

    def invalidate(self, root: Path | None = None) -> None:
        """清除缓存。文件增删后调用，立即反映新容量。"""
        with self._lock:
            if root is None:
                self._cache.clear()
            else:
                key = str(Path(root).resolve()) if Path(root).exists() else str(root)
                self._cache.pop(key, None)

    def scan(self, root: Path) -> DiskInfo:
        """强制扫描磁盘实际占用（忽略数据库，用于校准）。"""
        used, count = _dir_size(Path(root))
        with self._lock:
            self._cache.pop(str(Path(root).resolve()), None)
        return DiskInfo(
            total=disk_usage_of(Path(root))[0],
            free=disk_usage_of(Path(root))[2],
            used=disk_usage_of(Path(root))[1],
            drive_used=used,
            file_count=count,
        )


# 全局单例
monitor = CapacityMonitor(ttl=8.0)