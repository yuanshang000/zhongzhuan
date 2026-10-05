"""节点管理 API：目录浏览、创建文件夹、重命名、移动、删除、批量操作。"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.errors import ApiError, bad_request, conflict, not_found
from app.models.node import Node, NodeType
from app.schemas import (
    BatchDeleteIn,
    BatchMoveIn,
    BatchResult,
    FolderCreate,
    MoveIn,
    NodeDetail,
    NodeOut,
    RenameIn,
)
from app.services import node_service as svc
from app.services.storage import get_storage

router = APIRouter(prefix="/api/nodes", tags=["nodes"])


def _require(db: Session, node_id: str) -> Node:
    node = svc.get_node(db, node_id)
    if node is None:
        raise not_found()
    return node


def _norm_parent(parent_id: str | None) -> str | None:
    """空字符串与 None 都视为根目录。

    SQL 中 `parent_id = NULL` 永远不成立，前端传空串时必须转成 None。
    """
    if parent_id is None:
        return None
    parent_id = parent_id.strip()
    return parent_id or None


def _delete_recursive(db: Session, node: Node) -> int:
    """递归删除节点及其所有子孙，返回删除的文件数。

    先收集完整子树再逐个删物理文件，最后删数据库记录。
    """
    storage = get_storage()
    tree = svc.get_descendants(db, node.id)
    file_count = 0

    # 按深度从深到浅删除，保证父目录清理时子树已空
    depth_map: dict[str, int] = {node.id: 0}
    by_depth: dict[int, list[Node]] = {}
    for n in tree:
        d = depth_map.get(n.parent_id, 0) + 1 if n.parent_id else 1
        depth_map[n.id] = d
        by_depth.setdefault(d, []).append(n)

    for d in sorted(by_depth.keys(), reverse=True):
        for n in by_depth[d]:
            if n.storage_path:
                try:
                    # 每个文件用自己记录的根目录删除，避免切换路径后删不掉
                    root = Path(n.storage_root) if n.storage_root else None
                    storage.delete(n.storage_path, root)
                except FileNotFoundError:
                    pass
                except Exception:
                    # 物理删除失败不应阻塞元数据清理，记录后继续
                    pass
            if n.type == NodeType.FILE.value:
                file_count += 1

    # 数据库层依赖 ondelete=CASCADE
    db.delete(node)
    return file_count


@router.get("", response_model=list[NodeOut])
def list_children(parent_id: str | None = Query(None), db: Session = Depends(get_db)):
    # 先回收超时的未完成上传，避免用户看到无法下载的"幽灵节点"
    try:
        svc.cleanup_stale_uploads(db, get_storage())
    except Exception:
        pass  # 清理失败不影响列表功能
    return svc.child_nodes(db, _norm_parent(parent_id))


@router.get("/detail", response_model=NodeDetail)
def node_detail(id: str = Query(...), db: Session = Depends(get_db)):
    node = _require(db, id)
    children: list[Node] = []
    count, fsize = 0, 0
    if node.is_folder:
        children = svc.child_nodes(db, node.id)
        count, fsize = svc.subtree_size(db, node.id)
    return NodeDetail(
        node=NodeOut.model_validate(node),
        breadcrumb=svc.breadcrumb(db, node),
        children=[NodeOut.model_validate(c) for c in children],
        child_count=len(children),
        folder_size=fsize,
    )


@router.get("/stats")
def stats(db: Session = Depends(get_db)):
    return svc.total_usage(db)


# ---------------- 创建文件夹 ----------------
@router.post("/folder", response_model=NodeOut)
def create_folder(payload: FolderCreate, db: Session = Depends(get_db)):
    if payload.parent_id:
        parent = _require(db, payload.parent_id)
        if not parent.is_folder:
            raise bad_request("父节点不是文件夹")
    name = svc.sanitize(payload.name)

    # 先查重名（必须在 db.add 之前，否则会把自己也算进去导致误判重名）
    final_name = svc.unique_name(db, payload.parent_id, name, NodeType.FOLDER.value)

    node = Node(
        name=final_name,
        type=NodeType.FOLDER.value,
        parent_id=payload.parent_id,
        size=0,
    )
    db.add(node)
    db.flush()  # 拿到默认生成的 id

    node.storage_path = svc.storage_path_for(node.id, is_folder=True)
    db.commit()
    db.refresh(node)
    return node


# ---------------- 重命名 ----------------
@router.patch("/{node_id}/rename", response_model=NodeOut)
def rename(node_id: str, payload: RenameIn, db: Session = Depends(get_db)):
    node = _require(db, node_id)
    new_name = svc.sanitize(payload.name)
    node.name = svc.unique_name(db, node.parent_id, new_name, node.type, exclude_id=node.id)
    db.commit()
    db.refresh(node)
    return node


# ---------------- 移动（支持多选批量）----------------
@router.post("/{node_id}/move", response_model=NodeOut)
def move(node_id: str, payload: MoveIn, db: Session = Depends(get_db)):
    node = _require(db, node_id)
    if payload.parent_id:
        parent = _require(db, payload.parent_id)
        if not parent.is_folder:
            raise bad_request("目标不是文件夹")
        # 阻止把文件夹移入自身或其子孙，形成环
        if node.is_folder and svc.is_descendant(db, node.id, parent.id):
            raise bad_request("不能将文件夹移动到自身或其子目录内")
    node.parent_id = payload.parent_id
    db.commit()
    db.refresh(node)
    return node


# ---------------- 删除（支持多选批量、文件夹递归）----------------
@router.delete("/{node_id}")
def delete(node_id: str, db: Session = Depends(get_db)):
    node = _require(db, node_id)
    removed = _delete_recursive(db, node)
    db.commit()
    return {"deleted": True, "files_removed": removed}


@router.post("/batch-delete", response_model=BatchResult)
def batch_delete(payload: BatchDeleteIn, db: Session = Depends(get_db)):
    result = BatchResult()
    for nid in payload.ids:
        try:
            node = db.get(Node, nid)
            if node is None:
                result.failed.append({"id": nid, "reason": "不存在"})
                continue
            _delete_recursive(db, node)
            result.succeeded.append(nid)
        except Exception as e:
            db.rollback()
            result.failed.append({"id": nid, "reason": str(e)})
    db.commit()
    return result


# ---------------- 批量移动 ----------------
@router.post("/batch-move", response_model=BatchResult)
def batch_move(payload: BatchMoveIn, db: Session = Depends(get_db)):
    result = BatchResult()
    if payload.parent_id:
        parent = db.get(Node, payload.parent_id)
        if parent is None or not parent.is_folder:
            raise bad_request("目标文件夹不存在")
    for nid in payload.ids:
        try:
            node = db.get(Node, nid)
            if node is None:
                result.failed.append({"id": nid, "reason": "不存在"})
                continue
            if node.is_folder and payload.parent_id and svc.is_descendant(db, node.id, payload.parent_id):
                result.failed.append({"id": nid, "reason": "不能移入自身子目录"})
                continue
            node.parent_id = payload.parent_id
            result.succeeded.append(nid)
        except Exception as e:
            result.failed.append({"id": nid, "reason": str(e)})
    db.commit()
    return result


# ---------------- 批量重命名 ----------------
class BatchRenameIn(BatchDeleteIn):
    prefix: str = ""


@router.post("/batch-rename", response_model=BatchResult)
def batch_rename(payload: BatchRenameIn, db: Session = Depends(get_db)):
    """批量重命名：加前缀 / 加序号，避免逐个弹窗。"""
    result = BatchResult()
    for idx, nid in enumerate(payload.ids, start=1):
        node = db.get(Node, nid)
        if node is None:
            result.failed.append({"id": nid, "reason": "不存在"})
            continue
        stem, dot, ext = node.name.rpartition(".")
        new = f"{payload.prefix}{stem}_{idx}{dot}{ext}" if dot else f"{payload.prefix}{node.name}_{idx}"
        node.name = svc.unique_name(db, node.parent_id, new, node.type, exclude_id=node.id)
        result.succeeded.append(nid)
    db.commit()
    return result