"""分享服务：生成永久有效的免登录直链。

slug 一旦生成就固定不变，链接永久有效，直到节点被删除。
"""
from __future__ import annotations

import secrets

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.node import Node


def ensure_slug(db: Session, node: Node) -> str:
    """确保节点有 slug，幂等。"""
    if not node.share_slug:
        for _ in range(10):
            candidate = secrets.token_urlsafe(9)[:12]
            exists = db.query(Node.id).filter(Node.share_slug == candidate).first()
            if exists is None:
                node.share_slug = candidate
                break
        else:
            raise RuntimeError("无法生成唯一 slug")
    node.share_enabled = True
    return node.share_slug


def share_url(slug: str) -> str:
    return f"{settings.public_base_url.rstrip('/')}/s/{slug}"