"""免登录分享页：/s/{slug}

打开即展示文件名与大小。图片与视频会直接内嵌预览，
其余类型给出下载按钮。链接永久有效，直到文件被删除。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.errors import not_found
from app.models.node import Node
from app.services import node_service as svc
from app.services import preview as pv

router = APIRouter(tags=["share"])


def _fmt_size(n: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    f = float(n)
    for u in units:
        if f < 1024 or u == units[-1]:
            return f"{int(f)} {u}" if u == "B" else f"{f:.1f} {u}"
        f /= 1024
    return f"{f:.1f} TB"


def _html_escape(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
             .replace('"', "&quot;").replace("'", "&#x27;"))


@router.get("/s/{slug}", response_class=HTMLResponse)
def share_page(slug: str, request: Request, db: Session = Depends(get_db)):
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
    kind = pv.preview_kind(node)
    mime = pv.guess_mime(node)

    # 中文文件名走 Content-Disposition，HTML头部用 UTF-8 直接输出更可靠
    meta_desc = "文件夹" if is_folder else _html_escape(mime or "文件")

    raw_url = f"/api/public/{slug}/raw"
    preview_url = f"/p/{slug}"

    # ---- 可预览类型：直接内嵌，无需二次点击 ----
    if kind == "image":
        stage = (
            f'<div class="viewer">'
            f'<img src="{raw_url}" alt="{title}" id="shareImg">'
            f"</div>"
        )
        hint = "点击图片切换背景虚化 · 右键可另存为"
        icon = "img"
    elif kind == "video":
        stage = (
            f'<div class="viewer">'
            f'<video src="{raw_url}" controls autoplay playsinline preload="metadata"></video>'
            f"</div>"
        )
        hint = "支持拖动进度条与全屏播放"
        icon = "vid"
    elif kind == "audio":
        stage = (
            f'<div class="viewer audio"><div class="ico">'
            f'<svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor"'
            f' stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">'
            f'<path d="M9 18V5l11-2v13"/><circle cx="6" cy="18" r="3"/>'
            f'<circle cx="17" cy="16" r="3"/></svg></div>'
            f'<audio src="{raw_url}" controls autoplay style="width:100%"></audio></div>'
        )
        hint = "点播放按钮开始收听"
        icon = "aud"
    elif kind == "pdf":
        stage = f'<div class="viewer pdf"><iframe src="{raw_url}" title="{title}"></iframe></div>'
        hint = "若未自动显示，点击下方按钮打开"
        icon = "doc"
    elif kind == "text":
        stage = (
            f'<div class="viewer text"><pre id="shareText">加载中…</pre></div>'
        )
        hint = "文本内容已截断显示，完整内容请下载"
        icon = "doc"
    elif is_folder:
        detail = f"{count} 个文件 · {_fmt_size(size)}"
        action_text = "打包下载 (ZIP)"
        # 走公开路由：凭 slug 校验，无需登录
        action_url = f"/api/public/{slug}/download-zip"
        icon = "folder"
        stage = ""
        hint = ""
    else:
        detail = _fmt_size(size)
        action_text = "立即下载"
        action_url = f"/api/public/{slug}/download"
        icon = "file"
        stage = ""
        hint = ""

    if stage:
        detail = _fmt_size(size)
        action_text = "下载原文件"
        action_url = f"/api/public/{slug}/download"
        # 文件夹的 detail 在上面已算好，其余直接用大小

    ICONS = {
        "folder": ('<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7z"/>', "#d97706", "#fef3e2"),
        "img": ('<rect x="4" y="5" width="16" height="14" rx="2"/><circle cx="9" cy="10" r="1.6"/><path d="m4 16 4.5-4.5 3 3L15 11l5 5"/>', "#0b76d9", "#eef6ff"),
        "vid": ('<rect x="3" y="6" width="12" height="12" rx="2"/><path d="m15 10.5 5-3v9l-5-3z"/>', "#d63f7a", "#fdeef4"),
        "aud": ('<path d="M9 18V5l11-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="17" cy="16" r="3"/>', "#7c3aed", "#f3eeff"),
        "doc": ('<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/>', "#0f9d58", "#eaf7f1"),
        "file": ('<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/>', "#2563eb", "#e8f0fe"),
    }
    path, color, bg = ICONS.get(icon, ICONS["file"])

    # 直链区：让用户能直接复制地址去别处引用
    link_block = ""
    if not is_folder:
        link_block = f"""
<div class="links">
  <div class="link-row">
    <span class="lk">直链</span>
    <input type="text" id="lnkRaw" readonly value="">
    <button class="cp" data-copy="lnkRaw">复制</button>
  </div>
  <div class="link-row">
    <span class="lk">预览页</span>
    <input type="text" id="lnkPage" readonly value="">
    <button class="cp" data-copy="lnkPage">复制</button>
  </div>
</div>"""

    text_fetch = ""
    if kind == "text":
        text_fetch = f"""
  fetch('{raw_url}').then(function (r) {{ return r.text(); }}).then(function (t) {{
    var el = document.getElementById('shareText');
    el.textContent = t.length > 256 * 1024
      ? t.slice(0, 256 * 1024) + '\\n\\n... 内容过长，已截断'
      : (t || '（空文件）');
  }}).catch(function () {{
    document.getElementById('shareText').textContent = '加载失败，请下载查看';
  }});"""

    page = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
background:#f6f7f9;color:#1a1a1a;display:flex;align-items:center;justify-content:center;
min-height:100vh;padding:24px}
.card{background:#fff;border-radius:16px;padding:32px;max-width:560px;width:100%;
box-shadow:0 1px 3px rgba(0,0,0,.06),0 8px 24px rgba(0,0,0,.04)}
.card.wide{max-width:860px}
.ico{width:60px;height:60px;border-radius:15px;display:flex;align-items:center;
justify-content:center;margin:0 auto 18px}
h1{font-size:19px;font-weight:600;line-height:1.4;word-break:break-all;margin-bottom:8px;text-align:center}
.meta{font-size:14px;color:#6b7280;margin-bottom:22px;text-align:center}
.viewer{margin-bottom:20px;background:#0f1319;border-radius:12px;overflow:hidden;
display:flex;align-items:center;justify-content:center;min-height:180px}
.viewer img{max-width:100%;max-height:62vh;display:block;cursor:zoom-in;transition:filter .2s}
.viewer.blur img{filter:blur(20px);transform:scale(1.05)}
.viewer video{max-width:100%;max-height:62vh;display:block}
.viewer.pdf{height:66vh}
.viewer.pdf iframe{width:100%;height:100%;border:none;background:#fff}
.viewer.text{display:block;max-height:52vh;overflow:auto;background:#0f1319}
.viewer.text pre{padding:18px;font-family:ui-monospace,Menlo,monospace;font-size:12.5px;
line-height:1.7;color:#c9d3e0;white-space:pre-wrap;word-break:break-word}
.viewer.audio{flex-direction:column;padding:30px;background:#f8f9fb}
.viewer.audio .ico{width:52px;height:52px;margin:0 0 14px;background:#f3eeff;color:#7c3aed}
.btn{display:block;width:100%;padding:13px 20px;background:#2563eb;color:#fff;border:none;
border-radius:10px;font-size:15px;font-weight:500;cursor:pointer;text-align:center;
text-decoration:none;transition:background .15s}
.btn:hover{background:#1d4ed8}
.btn.ghost{background:#fff;color:#4b5563;border:1px solid #e5e7eb;margin-top:9px}
.btn.ghost:hover{background:#f3f4f6}
.links{margin-top:20px;border-top:1px solid #eef0f3;padding-top:18px}
.link-row{display:flex;align-items:center;gap:8px;margin-bottom:8px}
.link-row:last-child{margin-bottom:0}
.lk{font-size:12.5px;color:#6b7280;width:48px;flex-shrink:0}
.link-row input{flex:1;height:34px;padding:0 10px;border:1px solid #e5e7eb;border-radius:7px;
font-size:12px;font-family:ui-monospace,Menlo,monospace;color:#374151;outline:none;min-width:0}
.cp{height:34px;padding:0 13px;border-radius:7px;border:1px solid #e5e7eb;background:#fff;
color:#374151;font-size:12.5px;cursor:pointer;flex-shrink:0}
.cp:hover{background:#f3f4f6}
.hint{font-size:12px;color:#9ca3af;margin-top:16px;text-align:center;line-height:1.6}
</style>
</head>
<body>
<div class="card__CLASS__">
  <div class="ico" style="background:__BG__">
    <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="__COLOR__"
      stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">__PATH__</svg>
  </div>
  <h1>__TITLE__</h1>
  <p class="meta">__DETAIL__</p>
  __STAGE__
  <a class="btn" href="__ACTION_URL__">__ACTION_TEXT__</a>
  __LINKS__
  <p class="hint">__HINT__链接永久有效，直到文件被删除</p>
</div>
<script>
(function () {
  // 文件夹没有直链区，取元素前先判空
  var rawInput = document.getElementById('lnkRaw');
  if (rawInput) {
    rawInput.value = location.origin + '__RAW__';
    document.getElementById('lnkPage').value = location.origin + '__PREVIEW__';
  }
  document.querySelectorAll('[data-copy]').forEach(function (b) {
    b.onclick = function () {
      var el = document.getElementById(b.dataset.copy);
      el.focus(); el.select(); el.setSelectionRange(0, el.value.length);
      var done = false;
      try { done = document.execCommand('copy'); } catch (e) {}
      if (!done && navigator.clipboard) {
        navigator.clipboard.writeText(el.value);
      }
      var o = b.textContent;
      b.textContent = '已复制';
      setTimeout(function () { b.textContent = o; }, 1500);
    };
  });
  var img = document.getElementById('shareImg');
  if (img) {
    img.onclick = function () { img.parentElement.classList.toggle('blur'); };
  }
  __TEXT_FETCH__
})();
</script>
</body>
</html>"""

    page = (
        page.replace("__TITLE__", title)
        .replace("__DETAIL__", detail)
        .replace("__STAGE__", stage)
        .replace("__ACTION_URL__", action_url)
        .replace("__ACTION_TEXT__", action_text)
        .replace("__LINKS__", link_block)
        .replace("__HINT__", (hint + " · ") if hint else "")
        .replace("__RAW__", raw_url)
        .replace("__PREVIEW__", preview_url)
        .replace("__TEXT_FETCH__", text_fetch)
        .replace("__CLASS__", "card wide" if stage else "card")
        .replace("__BG__", bg)
        .replace("__COLOR__", color)
        .replace("__PATH__", path)
    )
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
        # 预览相关：前端与外部调用方都靠这三个字段
        "preview_kind": pv.preview_kind(node),
        "raw_url": f"/api/public/{slug}/raw",
        "preview_url": f"/p/{slug}",
        "mime_type": pv.guess_mime(node),
    }
