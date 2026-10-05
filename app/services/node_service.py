"""节点业务逻辑：目录树、重命名、移动、递归删除、容量统计。"""
from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.node import Node, NodeType

# 保留路径分隔符，其余危险字符替换
_SAFE = str.maketrans({"/": "_", "\\": "_", ":": "_", "*": "_", "?": "_", '"': "_", "<": "_", ">": "_", "|": "_"})


def sanitize(name: str) -> str:
    name = (name or "").strip().replace("\x00", "")
    return name.translate(_SAFE)[:255] or "untitled"


def storage_path_for(node_id: str, is_folder: bool = False) -> str:
    """存储路径与文件名解耦，使重命名为O(1) 数据库操作。

    文件改名时无需搬运字节 —— 对 S3 尤其关键（move 实为 copy+delete）。
    """
    return ("d" if is_folder else "f") + f"/{node_id}"


def get_node(db: Session, node_id: str) -> Node | None:
    return db.get(Node, node_id)


def get_descendants(db: Session, node_id: str) -> list[Node]:
    """一次性取出整棵子树（递归 CTE），避免 N+1 查询。"""
    fields = (Node.id, Node.name, Node.type, Node.parent_id,
              Node.storage_path, Node.storage_root, Node.size, Node.mime_type)

    stmt = select(*fields).where(Node.id == node_id).cte(recursive=True)
    stmt = stmt.union_all(select(*fields).join(stmt, Node.parent_id == stmt.c.id))

    rows = db.execute(select(
        stmt.c.id, stmt.c.name, stmt.c.type, stmt.c.parent_id,
        stmt.c.storage_path, stmt.c.storage_root, stmt.c.size, stmt.c.mime_type,
    )).all()

    return [
        Node(id=r[0], name=r[1], type=r[2], parent_id=r[3],
             storage_path=r[4], storage_root=r[5], size=r[6] or 0, mime_type=r[7])
        for r in rows
    ]


def child_nodes(db: Session, parent_id: str | None) -> list[Node]:
    stmt = select(Node).where(Node.parent_id == parent_id).order_by(Node.type.desc(), Node.name)
    return list(db.execute(stmt).scalars())


def breadcrumb(db: Session, node: Node) -> list[dict]:
    """从根到当前节点的面包屑。"""
    chain: list[Node] = []
    cur: Node | None = node
    # 防御性上限，防止数据异常导致死循环
    for _ in range(64):
        if cur is None:
            break
        chain.append(cur)
        cur = db.get(Node, cur.parent_id) if cur.parent_id else None
    chain.reverse()
    return [{"id": n.id, "name": n.name} for n in chain]


def unique_name(db: Session, parent_id: str | None, name: str, node_type: str,
                exclude_id: str | None = None) -> str:
    """同级同名检查，重名时自动追加 (1)(2) 后缀。"""
    base = sanitize(name)
    stem, dot, ext = base.rpartition(".")
    if not dot:
        stem, ext = base, ""
    else:
        stem = stem or base

    candidate, n = base, 1
    while True:
        stmt = select(Node.id).where(Node.parent_id == parent_id, Node.name == candidate, Node.type == node_type)
        if exclude_id:
            stmt = stmt.where(Node.id != exclude_id)
        if db.execute(stmt).first() is None:
            return candidate
        suffix = f"({n})" if node_type == NodeType.FOLDER.value else f"_{n}"
        candidate = f"{stem}{suffix}.{ext}" if ext else f"{stem}{suffix}"
        n += 1
        if n > 9999:
            candidate = f"{stem}_{uuid.uuid4().hex[:8]}"
            break
    return candidate


def subtree_size(db: Session, folder_id: str) -> tuple[int, int]:
    """返回 (子孙文件数, 子孙总字节数)。"""
    rows = get_descendants(db, folder_id)
    files = [r for r in rows if r.type == NodeType.FILE.value and r.id != folder_id]
    return len(files), sum(r.size or 0 for r in files)


def is_descendant(db: Session, candidate_id: str, target_id: str) -> bool:
    """candidate 是否为 target 的子孙（用于阻止把文件夹拖进自己的子目录）。"""
    if candidate_id == target_id:
        return True
    for n in get_descendants(db, candidate_id):
        if n.id == target_id:
            return True
    return False


def total_usage(db: Session) -> dict:
    files = db.execute(
        select(func.count(Node.id), func.coalesce(func.sum(Node.size), 0))
        .where(Node.type == NodeType.FILE.value)
    ).one()
    folders = db.execute(
        select(func.count(Node.id)).where(Node.type == NodeType.FOLDER.value)
    ).scalar_one()
    return {"file_count": files[0] or 0, "total_size": int(files[1] or 0), "folder_count": folders or 0}


def cleanup_stale_uploads(db: Session, storage, max_age_seconds: int = 7200) -> int:
    """清理未完成的上传（节点已建但文件未落盘）。

    上传中断（网络断开、页面关闭）会留下"幽灵节点"：数据库有记录、磁盘无文件。
    这些节点无法下载也无法使用，需定期回收，同时清理残留分片。
    返回清理数量。
    """
    import time
    from pathlib import Path

    root = Path(storage.root_path())
    now = time.time()
    removed = 0

    stale = db.execute(
        select(Node).where(
            Node.type == NodeType.FILE.value,
            Node.storage_path.isnot(None),
        )
    ).scalars().all()

    for node in stale:
        node_root = Path(node.storage_root) if node.storage_root else root
        try:
            target = node_root / node.storage_path
            # 文件真实存在且非空 -> 正常文件，跳过
            if target.exists() and target.stat().st_size > 0:
                continue
        except OSError:
            pass

        # 判断是否超时：节点创建至今超过 max_age_seconds
        try:
            created = node.created_at
            if created is None:
                continue
            if created.tzinfo is None:
                created = created.replace(tzinfo=__import__("datetime").timezone.utc)
            age = now - created.timestamp()
        except Exception:
            continue

        if age < max_age_seconds:
            continue

        # 超时未完成 -> 清理
        try:
            parts = node_root / ".parts" / node.id
            if parts.exists():
                import shutil

                shutil.rmtree(parts, ignore_errors=True)
            db.delete(node)
            removed += 1
        except Exception:
            continue

    if removed:
        db.commit()
    return removed