"""统一节点模型：文件与文件夹共用一张表。

这样设计的好处：
- 递归查询（子树、面包屑）只需一套 SQL
- 移动 / 重命名 / 批量删除逻辑统一，无需区分类型分支
- 分享链接指向任意节点即可，天然支持"分享整个文件夹"
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


class NodeType(str, enum.Enum):
    FILE = "file"
    FOLDER = "folder"


def _uuid() -> str:
    return uuid.uuid4().hex


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Node(Base):
    __tablename__ = "nodes"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    type: Mapped[str] = mapped_column(String(16), nullable=False, default=NodeType.FILE.value)

    # 根目录的 parent_id 为 NULL
    parent_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("nodes.id", ondelete="CASCADE"), nullable=True, index=True
    )

    # 物理存储的相对路径（local 后端为磁盘路径，s3 后端为 object key）
    storage_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    # 该文件上传时使用的存储根目录（local 后端）。
    # 运行时切换存储路径后，历史文件仍能被正确定位与下载。
    storage_root: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    size: Mapped[int] = mapped_column(Integer, default=0)
    mime_type: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # 分享短码
    share_slug: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True, index=True)
    share_enabled: Mapped[bool] = mapped_column(Boolean, default=False)

    # 文件内容哈希，用于秒传与去重（可空）
    etag: Mapped[str | None] = mapped_column(String(128), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=utcnow
    )

    children: Mapped[list["Node"]] = relationship(
        "Node", back_populates="parent", cascade="all, delete-orphan", lazy="selectin"
    )
    parent: Mapped["Node | None"] = relationship("Node", back_populates="children", remote_side=[id])

    __table_args__ = (
        # 同一父目录下同名同类型不允许，保证文件名唯一
        Index("ix_nodes_parent_name", "parent_id", "name"),
    )

    @property
    def is_folder(self) -> bool:
        return self.type == NodeType.FOLDER.value

    @property
    def is_file(self) -> bool:
        return self.type == NodeType.FILE.value