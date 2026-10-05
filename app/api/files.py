"""上传 API。

设计目标：上传带宽不占服务器。
-小文件（< threshold）：直接 PUT 到后端，落盘 / 转发，一次请求搞定
- 大文件：分片直传。S3 后端 -> 前端直连对象存储；local 后端 -> 前端分片 PUT 到后端
  两种模式字节都不做无谓中转，且支持并发 + 断点续传。
"""
from __future__ import annotations

import json
import os
import tempfile
import uuid as uuid_mod
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import get_db
from app.core.errors import bad_request, not_found
from app.models.node import Node, NodeType
from app.schemas import DirectUploadPrepareIn, DirectUploadPrepareOut, MultipartCompleteIn
from app.services import node_service as svc
from app.services.runtime_settings import get_runtime
from app.services.storage import get_storage

router = APIRouter(prefix="/api/files", tags=["files"])

# ---- 分片上传会话 ----
# 不能存内存 dict：开发模式 --reload 会重载进程导致会话丢失，
# 多 worker 部署时各进程内存也不共享。改为用 JSON 文件持久化。
_UPLOAD_SESSIONS: dict[str, dict] = {}
_SESSION_FILE = Path("data/upload_sessions.json")


def _load_sessions() -> dict[str, dict]:
    """从磁盘加载会话，兼容多进程与重启。"""
    global _UPLOAD_SESSIONS
    if _UPLOAD_SESSIONS:
        return _UPLOAD_SESSIONS
    if _SESSION_FILE.exists():
        try:
            _UPLOAD_SESSIONS = json.loads(_SESSION_FILE.read_text(encoding="utf-8"))
        except Exception:
            _UPLOAD_SESSIONS = {}
    return _UPLOAD_SESSIONS


def _save_sessions() -> None:
    _SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    _SESSION_FILE.write_text(
        json.dumps(_UPLOAD_SESSIONS, ensure_ascii=False), encoding="utf-8"
    )


def storage_root() -> Path:
    """当前生效的本地存储根目录（可在运行时修改）。"""
    return Path(get_runtime("local_storage_dir"))


def _invalidate_capacity() -> None:
    """上传/删除后让容量缓存失效，下次读取即为最新。"""
    try:
        from app.services.capacity import monitor

        monitor.invalidate()
    except Exception:
        pass


def _register(db: Session, parent_id: str | None, name: str, size: int, mime: str | None) -> Node:
    """先落库拿到 id 与存储路径，前端再把字节传到该路径。"""
    final_name = svc.unique_name(db, parent_id, svc.sanitize(name), NodeType.FILE.value)
    node = Node(name=final_name, type=NodeType.FILE.value, parent_id=parent_id, size=size,
                mime_type=mime or "application/octet-stream")
    db.add(node)
    db.flush()
    node.storage_path = svc.storage_path_for(node.id)
    # 记录上传时使用的存储根目录，切换路径后历史文件仍可定位
    if get_runtime("storage_backend") == "local":
        node.storage_root = str(storage_root())
    db.commit()
    db.refresh(node)
    return node


@router.post("/prepare", response_model=DirectUploadPrepareOut)
def prepare(payload: DirectUploadPrepareIn, db: Session = Depends(get_db)):
    """上传前预检：建节点、分配路径，返回直传信息。"""
    storage = get_storage()

    if payload.parent_id:
        parent = db.get(Node, payload.parent_id)
        if parent is None or not parent.is_folder:
            raise bad_request("目标文件夹不存在")
    # 支持"新建文件夹并上传"
    if payload.folder_id:
        f = db.get(Node, payload.folder_id)
        if f is None or not f.is_folder:
            raise bad_request("目标文件夹不存在")
        payload.parent_id = f.id

    name = payload.filename or "unnamed"
    node = _register(db, payload.parent_id, name, payload.size, payload.mime_type)

    # 小文件直接给后端直传地址
    if payload.size <= int(get_runtime("direct_upload_threshold")):
        return DirectUploadPrepareOut(
            mode="proxy", node_id=node.id, chunk_size=0, part_count=0,
            storage_path=node.storage_path or "",
            upload_url=f"/api/files/raw/{node.id}",
        )

    # 大文件分片
    chunk = int(get_runtime("multipart_chunk_size"))
    part_count = max(1, (payload.size + chunk - 1) // chunk)

    if get_runtime("storage_backend") == "s3":
        upload_id, _ = storage.create_multipart(node.storage_path)
        _load_sessions()[node.id] = {"upload_id": upload_id, "path": node.storage_path, "parts": {}}
        _save_sessions()
        return DirectUploadPrepareOut(
            mode="multipart", node_id=node.id, upload_id=upload_id,
            chunk_size=chunk, part_count=part_count, storage_path=node.storage_path or "",
        )

    # local 后端：分片地址由前端按 part 序号自行拼接
    upload_id = f"local-{uuid_mod.uuid4().hex[:8]}"
    _load_sessions()[node.id] = {"upload_id": upload_id, "path": node.storage_path, "parts": {}}
    _save_sessions()
    return DirectUploadPrepareOut(
        mode="multipart-local", node_id=node.id, upload_id=upload_id,
        chunk_size=chunk, part_count=part_count, storage_path=node.storage_path or "",
    )


@router.put("/raw/{node_id}")
async def upload_raw(node_id: str, request: Request, db: Session = Depends(get_db)):
    """小文件直传：请求体直接落盘，不进内存。"""
    node = db.get(Node, node_id)
    if node is None or not node.storage_path:
        raise not_found()
    storage = get_storage()
    # 优先用节点记录的历史根目录，回落到当前根目录
    root = Path(node.storage_root) if node.storage_root else storage_root()
    path = root / node.storage_path if get_runtime("storage_backend") == "local" else None

    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        size = 0
        with open(path, "wb") as f:
            # 逐块写，避免大文件占内存
            async for chunk in request.stream():
                f.write(chunk)
                size += len(chunk)
        node.size = size
        node.mime_type = request.headers.get("content-type", node.mime_type)
        db.commit()
        _invalidate_capacity()
        return JSONResponse({"ok": True, "size": size})

    # S3 后端：流式转发，避免整文件进内存
    class _AsyncIter:
        def __init__(self, r: Request):
            self.r = r

        def __aiter__(self):
            return self.r.stream().__aiter__()

    storage.write_stream(node.storage_path, _AsyncIter(request), node.size)
    node.size = storage.size_of(node.storage_path)
    db.commit()
    return JSONResponse({"ok": True, "size": node.size})


@router.delete("/raw/{node_id}")
def discard_upload(node_id: str, db: Session = Depends(get_db)):
    """上传失败时清理已创建的占位节点，避免列表出现"幽灵文件"。

    只删元数据与残留分片，不影响任何已落盘的完整文件。
    """
    node = db.get(Node, node_id)
    if node is None:
        return {"discarded": True}

    # 已完成上传的文件（有真实内容）不清理
    root = Path(node.storage_root) if node.storage_root else storage_root()
    target = root / (node.storage_path or "")
    if node.storage_path and target.exists() and target.stat().st_size > 0:
        return {"discarded": False, "reason": "文件已存在"}

    import shutil

    shutil.rmtree(root / ".parts" / node_id, ignore_errors=True)
    db.delete(node)
    db.commit()
    _invalidate_capacity()

    sessions = _load_sessions()
    sessions.pop(node_id, None)
    _save_sessions()
    return {"discarded": True}


@router.put("/part/{node_id}/{part_no}")
async def upload_part(node_id: str, part_no: int, request: Request, db: Session = Depends(get_db)):
    """local 后端的分片上传端点，落盘到临时分片目录。

    分片直接写文件，不依赖内存会话 —— 即使进程重载也能继续。
    """
    node = db.get(Node, node_id)
    if node is None or not node.storage_path:
        raise not_found()

    root = Path(node.storage_root) if node and node.storage_root else storage_root()
    parts_dir = root / ".parts" / node_id
    parts_dir.mkdir(parents=True, exist_ok=True)
    target = parts_dir / f"{part_no:06d}"

    size = 0
    with open(target, "wb") as f:
        # 逐块写，不进内存
        async for chunk in request.stream():
            f.write(chunk)
            size += len(chunk)

    st = target.stat()
    etag = f'"{st.st_size:x}-{int(st.st_mtime)}"'

    # 记录分片状态（best effort，丢了也不影响合并）
    sessions = _load_sessions()
    if node_id in sessions:
        sessions[node_id].setdefault("parts", {})[str(part_no)] = {
            "size": size, "etag": etag
        }
        _save_sessions()

    return JSONResponse({"partNumber": part_no, "etag": etag, "size": size})


@router.post("/multipart/{node_id}/sign")
def sign_parts(node_id: str, part_numbers: list[int], db: Session = Depends(get_db)):
    """S3 后端：批量签发分片直传 URL，浏览器直连对象存储上传字节。

    分批签名（每次最多 100 片）避免一次生成上万条签名。
    """
    node = db.get(Node, node_id)
    if node is None:
        raise not_found()
    session = _load_sessions().get(node_id)
    if session is None or not session.get("upload_id"):
        raise bad_request("上传会话不存在或已过期")

    storage = get_storage()
    urls = [
        {"partNumber": n, "url": storage.presign_part(node.storage_path, session["upload_id"], n)}
        for n in part_numbers[:100]
    ]
    return {"urls": urls}


@router.post("/multipart/{node_id}/complete", response_model=DirectUploadPrepareOut)
def complete_multipart(node_id: str, payload: MultipartCompleteIn, db: Session = Depends(get_db)):
    """完成分片上传：合并分片，清理临时文件。"""
    node = db.get(Node, node_id)
    if node is None or not node.storage_path:
        raise not_found()

    if get_runtime("storage_backend") == "s3":
        session = _load_sessions().get(node_id)
        if session is None or not session.get("upload_id"):
            raise bad_request("上传会话不存在或已过期")
        storage = get_storage()
        parts = payload.parts or list(session.get("parts", {}).values())
        storage.complete_multipart(node.storage_path, session["upload_id"], list(parts))
        node.size = storage.size_of(node.storage_path)
    else:
        # local：按序号顺序拼接分片。
        # 不依赖内存会话 —— 只要分片文件在磁盘上就能合并。
        root = Path(node.storage_root) if node.storage_root else storage_root()
        parts_dir = root / ".parts" / node_id
        final = root / node.storage_path

        if not parts_dir.exists() or not any(parts_dir.iterdir()):
            raise bad_request("未找到已上传的分片，请重新上传")

        final.parent.mkdir(parents=True, exist_ok=True)
        total = 0
        with open(final, "wb") as out:
            for part_file in sorted(parts_dir.glob("*")):
                with open(part_file, "rb") as pf:
                    while block := pf.read(4 * 1024 * 1024):
                        out.write(block)
                        total += len(block)
        node.size = total
        import shutil
        shutil.rmtree(parts_dir, ignore_errors=True)

    db.commit()
    _load_sessions().pop(node_id, None)
    _save_sessions()
    _invalidate_capacity()
    db.refresh(node)
    return DirectUploadPrepareOut(mode="done", node_id=node.id, storage_path=node.storage_path or "")


@router.post("/multipart/{node_id}/abort")
def abort_multipart(node_id: str, db: Session = Depends(get_db)):
    """取消上传：回收已上传分片，避免残留占空间。"""
    session = _UPLOAD_SESSIONS.get(node_id)
    if session:
        storage = get_storage()
        if get_runtime("storage_backend") == "s3" and session.get("upload_id"):
            try:
                storage.abort_multipart(session["path"], session["upload_id"])
            except Exception:
                pass
        else:
            import shutil
            shutil.rmtree(storage_root() / ".parts" / node_id, ignore_errors=True)
        _UPLOAD_SESSIONS.pop(node_id, None)
    return {"aborted": True}