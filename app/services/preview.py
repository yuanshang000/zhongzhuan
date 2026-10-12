"""文件预览服务。

目标是让系统能直接当图床 / 视频仓库用，因此提供两种"独立链接"：

1. **预览链接**（/p/{slug} 或面板内预览层使用）
   打开是一个排版好的观看页：视频播放器、图片查看器、文本渲染。
2. **直链**（/api/public/{slug}/raw）
   Content-Disposition: inline，浏览器 <img>/<video> 可直接引用，
   可嵌到第三方页面里。

两个链接都凭share_slug 访问，无需登录，因此也走公开白名单。
安全边界与下载一致：只有开启分享的节点才有 slug，猜node_id 无效。
"""
from __future__ import annotations

import mimetypes
from pathlib import Path

from fastapi import Request
from fastapi.responses import Response, StreamingResponse
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.errors import not_found
from app.models.node import Node
from app.services.runtime_settings import get_runtime
from app.services.storage import get_storage

# ---------------------------------------------------------------- 类型判定

IMAGE_EXT = {
    "jpg", "jpeg", "png", "gif", "webp", "svg", "bmp", "ico", "avif", "heic", "heif",
}
VIDEO_EXT = {
    "mp4", "webm", "ogv", "mov", "m4v", "mkv", "avi", "flv", "wmv", "3gp",
}
AUDIO_EXT = {"mp3", "wav", "ogg", "oga", "m4a", "aac", "flac", "opus"}
TEXT_EXT = {
    "txt", "md", "markdown", "json", "csv", "log", "xml", "yml", "yaml",
    "py", "js", "ts", "tsx", "jsx", "html", "css", "scss", "go", "rs",
    "java", "c", "h", "cpp", "hpp", "sh", "bat", "ps1", "ini", "toml", "sql",
}
PDF_EXT = {"pdf"}


def _ext(name: str) -> str:
    return (name.rsplit(".", 1)[-1] if "." in name else "").lower()


def guess_mime(node: Node) -> str:
    """优先用上传时记录的 MIME，缺失则按扩展名推断。

    前端上传小文件时后端会覆盖 mime_type，但历史数据可能是
    application/octet-stream，这里兜底避免浏览器拿到无法渲染的类型。
    """
    mime = (node.mime_type or "").strip()
    ext = _ext(node.name)

    if ext in IMAGE_EXT or ext in VIDEO_EXT or ext in AUDIO_EXT or ext in PDF_EXT:
        fallback = {
            **({e: f"image/{'svg+xml' if e == 'svg' else e}" for e in IMAGE_EXT}),
            **{e: "video/mp4" for e in VIDEO_EXT},
            **{e: "audio/mpeg" for e in AUDIO_EXT},
            **{e: "application/pdf" for e in PDF_EXT},
        }
        # 已知扩展名直接用映射值，避免 octet-stream 让浏览器拒绝渲染
        if not mime or mime == "application/octet-stream":
            return fallback[ext]

    if not mime:
        guessed, _ = mimetypes.guess_type(node.name)
        mime = guessed or "application/octet-stream"
    return mime


def preview_kind(node: Node) -> str:
    """返回 preview 类型标识，供前端与分享页决定渲染方式。"""
    if node.is_folder:
        return "folder"
    ext = _ext(node.name)
    if ext in IMAGE_EXT:
        return "image"
    if ext in VIDEO_EXT:
        return "video"
    if ext in AUDIO_EXT:
        return "audio"
    if ext in PDF_EXT:
        return "pdf"
    if ext in TEXT_EXT:
        return "text"
    return "none"


def can_preview(node: Node) -> bool:
    return preview_kind(node) in ("image", "video", "audio", "pdf", "text")


# ---------------------------------------------------------------- Range 解析

def _parse_range(header: str | None, file_size: int) -> tuple[int, int] | None:
    """解析 Range: bytes=start-end，返回闭区间 (start, end)。

    只支持单区间（浏览器播放器的实际行为），多区间忽略并退化为全量。
    非法或越界返回 None，由调用方回200 全量。
    """
    if not header:
        return None
    header = header.strip()
    if not header.lower().startswith("bytes="):
        return None
    spec = header[6:].split(",")[0].strip()
    if "-" not in spec:
        return None

    raw_start, raw_end = spec.split("-", 1)
    raw_start, raw_end = raw_start.strip(), raw_end.strip()

    try:
        if not raw_start:
            # bytes=-500 → 最后 500 字节
            if not raw_end:
                return None
            suffix = int(raw_end)
            if suffix <= 0:
                return None
            start = max(0, file_size - suffix)
            end = file_size - 1
        else:
            start = int(raw_start)
            end = int(raw_end) if raw_end else file_size - 1
    except ValueError:
        return None

    if start >= file_size or start < 0:
        return None
    end = min(end, file_size - 1)
    if end < start:
        return None
    return start, end


# ---------------------------------------------------------------- 核心输出

def _inline_disposition(name: str) -> str:
    """inline 的 Content-Disposition，兼容中文文件名。"""
    from urllib.parse import quote

    ascii_name = (name.encode("ascii", "ignore").decode() or "file").replace('"', "")
    return f"inline; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name, safe='')}"


def _local_target(node: Node) -> Path:
    root = Path(node.storage_root) if node.storage_root else Path(get_runtime("local_storage_dir"))
    return root / (node.storage_path or "")


def serve_inline(node: Node, request: Request, download_fallback: bool = True):
    """以inline 方式输出文件内容，支持 Range 与断点续传。

    download_fallback=True 时，若浏览器把响应判定为"非媒体"，
    会退回 attachment 语义（当前实现不依赖该分支，保留参数便于扩展）。
    """
    if node.is_folder:
        from app.core.errors import bad_request

        raise bad_request("文件夹不支持预览，请使用打包下载")
    if not node.storage_path:
        raise not_found("文件不存在")

    storage = get_storage()
    mime = guess_mime(node)

    # S3：跳 presigned inline URL，字节完全不经过本进程
    url = storage.presign_get_inline(node.storage_path, node.name, content_type=mime)
    if url:
        from fastapi.responses import RedirectResponse

        return RedirectResponse(url, status_code=307)

    # local：自己流式输出
    target = _local_target(node)
    try:
        file_size = target.stat().st_size
    except OSError:
        raise not_found("文件不存在或已被删除")

    common = {
        "Content-Disposition": _inline_disposition(node.name),
        "Accept-Ranges": "bytes",
        "Cache-Control": "private, max-age=3600",
        "X-Content-Type-Options": "nosniff",
    }

    rng = _parse_range(request.headers.get("range"), file_size)
    if rng is None:
        async def full():
            async for b in storage.read_chunks(
                node.storage_path, 4 * 1024 * 1024, Path(node.storage_root) if node.storage_root else None
            ):
                yield b

        return StreamingResponse(full(), media_type=mime, headers=common)

    start, end = rng
    length = end - start + 1

    async def part():
        async for b in storage.read_chunks(
            node.storage_path,
            4 * 1024 * 1024,
            Path(node.storage_root) if node.storage_root else None,
            start=start,
            length=length,
        ):
            yield b

    return StreamingResponse(
        part(),
        status_code=206,
        media_type=mime,
        headers={
            **common,
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Content-Length": str(length),
        },
    )


def node_by_slug(db: Session, slug: str) -> Node:
    node = db.query(Node).filter(Node.share_slug == slug).first()
    if node is None or not node.share_enabled:
        raise not_found("链接不存在或已被删除")
    return node


def meta_of(node: Node) -> dict:
    """预览元信息：分享页与前端共用一份结构。"""
    kind = preview_kind(node)
    size = node.size or 0
    if kind == "folder":
        return {"kind": "folder", "name": node.name, "size": 0, "mime": "", "previewable": False}
    return {
        "kind": kind,
        "name": node.name,
        "size": size,
        "mime": guess_mime(node),
        "previewable": kind != "none",
    }
