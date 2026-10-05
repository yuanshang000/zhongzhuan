"""API 请求 / 响应模型。"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


# ---------------- 节点 ----------------
class NodeOut(BaseModel):
    id: str
    name: str
    type: str
    parent_id: str | None = None
    size: int = 0
    mime_type: str | None = None
    share_slug: str | None = None
    share_enabled: bool = False
    etag: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class FolderCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    parent_id: str | None = None


class RenameIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)


class MoveIn(BaseModel):
    parent_id: str | None = None


class BreadcrumbItem(BaseModel):
    id: str
    name: str


class NodeDetail(BaseModel):
    node: NodeOut
    breadcrumb: list[BreadcrumbItem]
    # 仅文件夹返回直属子项
    children: list[NodeOut] = []
    child_count: int = 0
    folder_size: int = 0


# ---------------- 批量操作 ----------------
class BatchDeleteIn(BaseModel):
    ids: list[str] = Field(min_length=1)


class BatchMoveIn(BaseModel):
    ids: list[str] = Field(min_length=1)
    parent_id: str | None = None


class BatchResult(BaseModel):
    succeeded: list[str] = []
    failed: list[dict] = []


# ---------------- 上传 ----------------
class DirectUploadPrepareIn(BaseModel):
    filename: str
    size: int
    mime_type: str | None = None
    parent_id: str | None = None
    folder_id: str | None = None  # 上传到指定文件夹（新建文件夹上传）


class DirectUploadPrepareOut(BaseModel):
    mode: str                      # "direct" | "multipart"
    node_id: str
    upload_url: str | None = None  # 单次直传签名 URL
    upload_id: str | None = None   # 分片上传 ID
    chunk_size: int = 0
    part_count: int = 0
    storage_path: str = ""


class MultipartCompleteIn(BaseModel):
    parts: list[dict] = Field(default_factory=list)  # [{partNumber, etag}]


# ---------------- 分享 ----------------
class ShareOut(BaseModel):
    slug: str
    url: str
    enabled: bool


class ShareToggleIn(BaseModel):
    enabled: bool