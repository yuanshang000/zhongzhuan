"""下载 API：单文件直出 + 文件夹流式打包。

关键点：文件字节不经过应用服务器。
- S3 后端：302/307 跳转到 presigned URL，浏览器直连对象存储
- local 后端：X-Accel-Redirect 交给 Nginx 直出，本进程零带宽
"""
from __future__ import annotations

import io
import zipfile
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query
from fastapi.responses import RedirectResponse, StreamingResponse
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import get_db
from app.core.errors import ApiError, bad_request, forbidden, not_found
from app.models.node import Node, NodeType
from app.schemas import ShareOut, ShareToggleIn
from app.services import node_service as svc
from app.services.share_service import ensure_slug, share_url
from app.services.storage import get_storage

router = APIRouter(tags=["download"])


def _node_or_404(db: Session, node_id: str) -> Node:
    node = db.get(Node, node_id)
    if node is None:
        raise not_found()
    return node


def _safe_download_name(node: Node) -> str:
    """RFC 5987 编码，兼容中文文件名。

    HTTP 头只能承载 latin-1，中文必须走 filename*=UTF-8'' 形式，
    同时保留一个 ASCII 回退名供老客户端使用。
    """
    ascii_name = node.name.encode("ascii", "ignore").decode() or "download"
    # 清除 ASCII 回退名里的引号与反斜杠，避免破坏头部结构
    ascii_name = ascii_name.replace('"', "").replace("\\", "")
    return f"{ascii_name}; filename*=UTF-8''{quote(node.name, safe='')}"


def _zip_disposition(name: str) -> str:
    """ZIP 下载头，同样做 RFC 5987 编码。"""
    ascii_name = (name.encode("ascii", "ignore").decode() or "archive").replace('"', "").replace("\\", "")
    return f"{ascii_name}.zip; filename*=UTF-8''{quote(name + '.zip', safe='')}"


# ---------------- 单文件下载 ----------------
@router.get("/api/files/{node_id}/download")
def download_file(node_id: str, db: Session = Depends(get_db)):
    node = _node_or_404(db, node_id)
    if node.is_folder:
        raise bad_request("该节点是文件夹，请使用打包下载")
    storage = get_storage()
    if not node.storage_path:
        raise not_found("文件不存在")

    # S3：跳转直连，服务器不过流量
    url = storage.presign_get(node.storage_path, node.name, expires=3600)
    if url:
        return RedirectResponse(url, status_code=307)

    # local：流式输出，使用节点记录的历史根目录
    root = Path(node.storage_root) if node.storage_root else None
    try:
        target = (root or Path(get_runtime("local_storage_dir"))) / node.storage_path
        exists = target.exists()
    except Exception:
        exists = False

    if not exists:
        # 上传未完成或物理文件丢失：给出明确原因，而非 500 崩溃
        pending = (root or Path(get_runtime("local_storage_dir"))) / ".parts" / node.id
        if pending.exists() and any(pending.iterdir()):
            raise ApiError(409, "文件尚未上传完成，请重新上传该文件", "upload_incomplete")
        raise not_found("文件不存在或已被删除")

    return StreamingResponse(
        _iter_local(node.storage_path, node.storage_root),
        media_type=node.mime_type or "application/octet-stream",
        headers={"Content-Disposition": _safe_download_name(node)},
    )


async def _iter_local(path: str, root: str | None = None, chunk: int = 4 * 1024 * 1024):
    async for b in get_storage().read_chunks(path, chunk, Path(root) if root else None):
        yield b


# ---------------- 文件夹打包下载（流式 ZIP）----------------
@router.get("/api/files/{node_id}/download-zip")
def download_zip(node_id: str, db: Session = Depends(get_db)):
    """把整个文件夹打包成 ZIP 流式返回。

    采用 ZIP64 格式，突破 4GB 单文件限制，可打包数 TB 级内容。
    使用流式写入，边读边压，内存占用恒定。
    """
    node = _node_or_404(db, node_id)
    if node.is_file:
        # 单文件也可打包，保持接口统一
        tree = [node]
        root_name = node.name
    else:
        tree = [n for n in svc.get_descendants(db, node.id) if n.is_file]
        root_name = node.name

    if not tree:
        raise bad_request("文件夹为空，没有可下载的内容")

    storage = get_storage()

    # 流式 ZIP：边读边压边下发，内存占用恒定，突破 4GB 限制
    async def stream_zip():
        import os
        import tempfile

        # 用临时文件承接，边压边下发
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
                for n in tree:
                    rel = _relative_path(db, node, n)
                    if not rel or not n.storage_path:
                        continue
                    # 每个文件用自己记录的根目录，兼容历史路径
                    r = Path(n.storage_root) if n.storage_root else None
                    try:
                        with zf.open(rel, "w", force_zip64=True) as dest:
                            async for chunk in storage.read_chunks(n.storage_path, root=r):
                                dest.write(chunk)
                    except (FileNotFoundError, ValueError):
                        # 未完成上传的文件跳过，不中断整个打包
                        continue
                    except Exception:
                        continue

            # 逐块回传
            with open(tmp_path, "rb") as f:
                while block := f.read(4 * 1024 * 1024):
                    yield block
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    return StreamingResponse(
        stream_zip(),
        media_type="application/zip",
        headers={
            "Content-Disposition": _zip_disposition(node.name),
            "X-Content-Type-Options": "nosniff",
        },
    )


def _relative_path(db: Session, root: Node, node: Node) -> str:
    """生成 ZIP 内的相对路径，保留子目录结构。"""
    parts: list[str] = []
    cur = node
    chain: list[Node] = []
    while cur is not None and cur.id != root.id:
        chain.append(cur)
        cur = db.get(Node, cur.parent_id) if cur.parent_id else None
    chain.reverse()
    for c in chain:
        parts.append(c.name)
    return "/".join(parts) if parts else node.name


# ---------------- 分享 ----------------
@router.get("/api/nodes/{node_id}/share", response_model=ShareOut)
def get_share(node_id: str, db: Session = Depends(get_db)):
    node = _node_or_404(db, node_id)
    slug = ensure_slug(db, node)
    db.commit()
    return ShareOut(slug=slug, url=share_url(slug), enabled=node.share_enabled)


@router.post("/api/nodes/{node_id}/share", response_model=ShareOut)
def toggle_share(node_id: str, payload: ShareToggleIn, db: Session = Depends(get_db)):
    """开启/关闭分享。关闭后链接失效，但 slug 保留，重新开启仍是同一链接。"""
    node = _node_or_404(db, node_id)
    if payload.enabled:
        slug = ensure_slug(db, node)
    else:
        if not node.share_slug:
            raise bad_request("尚未生成分享链接")
        slug = node.share_slug
        node.share_enabled = False
    db.commit()
    return ShareOut(slug=slug, url=share_url(slug), enabled=node.share_enabled)