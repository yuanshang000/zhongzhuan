"""免登录分享页：/s/{slug}

打开即展示文件名与大小，点按钮直接下载，无需任何登录。
链接永久有效，直到文件被删除。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.errors import not_found
from app.models.node import Node, NodeType
from app.services import node_service as svc
from app.services.storage import get_storage

router = APIRouter(tags=["share"])


def _fmt_size(n: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    f = float(n)
    for u in units:
        if f < 1024 or u == units[-1]:
            return f"{f:.1f} {u}" if u != "B" else f"{int(f)} B"
        f /= 1024
    return f"{f:.1f} TB"


def _html_escape(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
             .replace('"', "&quot;").replace("'", "&#x27;"))


@router.get("/s/{slug}", response_class=HTMLResponse)
def share_page(slug: str, db: Session = Depends(get_db)):
    node = db.query(Node).filter(Node.share_slug == slug).first()
    if node is None or not node.share_enabled:
        raise not_found("分享链接不存在或已被删除")

    count, size = (0, 0)
    if node.is_folder:
        count, size = svc.subtree_size(db, node.id)
    else:
        count, size = 1, node.size or 0

    is_folder = node.is_folder
    title = _html_escape(node.name)
    #中文文件名走 Content-Disposition，HTML 头部用 UTF-8 直接输出更可靠
    meta_desc = "文件夹" if is_folder else _html_escape(node.mime_type or "文件")

    if is_folder:
        detail = f"{count} 个文件 · {_fmt_size(size)}"
        action_text = "打包下载 (ZIP)"
        # 走公开路由：凭 slug 校验，无需登录
        action_url = f"/api/public/{slug}/download-zip"
        icon = "folder"
    else:
        detail = _fmt_size(size)
        action_text = "立即下载"
        action_url = f"/api/public/{slug}/download"
        icon = "file"

    page = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
background:#f6f7f9;color:#1a1a1a;display:flex;align-items:center;justify-content:center;
min-height:100vh;padding:24px}}
.card{{background:#fff;border-radius:16px;padding:40px;max-width:460px;width:100%;
box-shadow:0 1px 3px rgba(0,0,0,.06),0 8px 24px rgba(0,0,0,.04)}}
.ico{{width:64px;height:64px;border-radius:16px;display:flex;align-items:center;
justify-content:center;margin-bottom:20px}}
.ico.folder{{background:#fef3e2}}
.ico.file{{background:#e8f0fe}}
h1{{font-size:19px;font-weight:600;line-height:1.4;word-break:break-all;margin-bottom:8px}}
.meta{{font-size:14px;color:#6b7280;margin-bottom:28px}}
.btn{{display:block;width:100%;padding:13px 20px;background:#2563eb;color:#fff;border:none;
border-radius:10px;font-size:15px;font-weight:500;cursor:pointer;text-align:center;
text-decoration:none;transition:background .15s}}
.btn:hover{{background:#1d4ed8}}
.hint{{font-size:12px;color:#9ca3af;margin-top:18px;text-align:center;line-height:1.6}}
</style>
</head>
<body>
<div class="card">
  <div class="ico {icon}">
    <svg width="30" height="30" viewBox="0 0 24 24" fill="none" stroke="#d97706" stroke-width="1.8"
    {'stroke-linecap="round" stroke-linejoin="round"' if is_folder else 'stroke="#2563eb" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"'}>
    {'<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7z"/>' if is_folder else '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/>'}
    </svg>
  </div>
  <h1>{title}</h1>
  <p class="meta">{detail}</p>
  <a class="btn" href="{action_url}">{action_text}</a>
  <p class="hint">链接永久有效，直到文件被删除</p>
</div>
</body>
</html>"""

    return HTMLResponse(page)


@router.get("/api/share/{slug}")
def share_info(slug: str, db: Session = Depends(get_db)):
    """供前端轮询/分享页使用的元信息接口。"""
    node = db.query(Node).filter(Node.share_slug == slug).first()
    if node is None or not node.share_enabled:
        raise not_found("分享链接不存在")
    count, size = (svc.subtree_size(db, node.id) if node.is_folder else (1, node.size or 0))
    suffix = "download-zip" if node.is_folder else "download"
    return {
        "name": node.name,
        "type": node.type,
        "size": size,
        "file_count": count,
        "download_url": f"/api/public/{slug}/{suffix}",
    }