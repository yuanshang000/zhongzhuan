"""预览 API：面板内预览 + 公开直链 + 公开预览页。

路由分三类：
- ``/api/files/{id}/preview``      需登录，面板内预览用
- ``/api/public/{slug}/raw``       免登录，inline 直链（图床/视频引用）
- ``/p/{slug}``                    免登录，带排版的预览页

``raw`` 这条是关键：它返回正确的 Content-Type 与 inline 头，
可以直接贴到任意第三方页面的 ``<img>`` / ``<video>`` 里。
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

router = APIRouter(tags=["preview"])


# ---------------------------------------------------------------- 面板内预览
@router.get("/api/files/{node_id}/preview")
def preview_file(node_id: str, request: Request, db: Session = Depends(get_db)):
    """需登录的inline 输出。面板预览层与视频拖动进度条都走这里。"""
    node = db.get(Node, node_id)
    if node is None:
        raise not_found()
    return pv.serve_inline(node, request)


@router.get("/api/files/{node_id}/preview-info")
def preview_info(node_id: str, db: Session = Depends(get_db)):
    node = db.get(Node, node_id)
    if node is None:
        raise not_found()
    return pv.meta_of(node)


# ---------------------------------------------------------------- 公开直链
@router.get("/api/public/{slug}/raw")
def public_raw(slug: str, request: Request, db: Session = Depends(get_db)):
    """凭分享码取文件内容，inline 语义 —— 可直接当图床 / 视频直链使用。

    与 ``/download`` 的区别仅在Content-Disposition：
    这里是 inline，浏览器会直接渲染而不是弹下载框。
    """
    return pv.serve_inline(pv.node_by_slug(db, slug), request)


@router.get("/api/public/{slug}/meta")
def public_meta(slug: str, db: Session = Depends(get_db)):
    node = pv.node_by_slug(db, slug)
    meta = pv.meta_of(node)
    if node.is_folder:
        meta["size"], meta["file_count"] = svc.subtree_size(db, node.id)
    meta["raw_url"] = f"/api/public/{slug}/raw"
    # 文件夹没有预览页与直链，置空而不是省略字段 ——
    # 前端与外部调用方可以无条件读取，不用判断键是否存在
    meta["preview_url"] = None if node.is_folder else f"/p/{slug}"
    meta["download_url"] = (
        f"/api/public/{slug}/download-zip" if node.is_folder else f"/api/public/{slug}/download"
    )
    return meta


# ---------------------------------------------------------------- 公开预览页
def _esc(s: str) -> str:
    return (
        s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        .replace('"', "&quot;").replace("'", "&#x27;")
    )


def _fmt_size(n: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    f = float(n)
    for u in units:
        if f < 1024 or u == units[-1]:
            return f"{int(f)} {u}" if u == "B" else f"{f:.1f} {u}"
        f /= 1024
    return f"{f:.1f} TB"


@router.get("/p/{slug}", response_class=HTMLResponse)
def preview_page(slug: str, db: Session = Depends(get_db)):
    """公开预览页：视频可播、图片可看、文本可读，其余提示下载。"""
    node = pv.node_by_slug(db, slug)
    kind = pv.preview_kind(node)
    name = _esc(node.name)
    raw = f"/api/public/{slug}/raw"
    size = _fmt_size(node.size or 0)

    if kind == "image":
        # 图片页同时给出可直接引用的地址，方便当图床用
        body = f"""
<div class="stage img-stage">
  <img src="{raw}" alt="{name}" id="pvImg">
</div>
<div class="tip">点击图片可切换背景虚化 · 右键另存为</div>"""
    elif kind == "video":
        body = f"""
<div class="stage">
  <video src="{raw}" controls autoplay playsinline preload="metadata"
         style="max-width:100%;max-height:70vh;border-radius:10px;display:block"></video>
</div>
<div class="tip">支持拖动进度条与全屏播放</div>"""
    elif kind == "audio":
        body = f"""
<div class="stage audio-stage">
  <div class="ico">
    <svg width="34" height="34" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
      <path d="M9 18V5l11-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="17" cy="16" r="3"/></svg>
  </div>
  <audio src="{raw}" controls autoplay style="width:100%;margin-top:18px"></audio>
</div>"""
    elif kind == "pdf":
        body = f"""
<div class="stage pdf-stage">
  <iframe src="{raw}" title="{name}"></iframe>
</div>
<div class="tip">若未自动显示，可点击下方按钮打开</div>"""
    elif kind == "text":
        body = f"""
<div class="stage text-stage">
  <pre id="pvText">加载中…</pre>
</div>"""
    elif kind == "folder":
        count, fsize = svc.subtree_size(db, node.id)
        body = f"""
<div class="stage folder-stage">
  <div class="ico folder">
    <svg width="34" height="34" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
      <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7z"/></svg>
  </div>
  <p class="sub">{count} 个文件 · {_fmt_size(fsize)}</p>
</div>"""
    else:
        body = f"""
<div class="stage folder-stage">
  <div class="ico gen">
    <svg width="34" height="34" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/></svg>
  </div>
  <p class="sub">该格式暂不支持在线预览</p>
</div>"""

    dl = f"/api/public/{slug}/download-zip" if node.is_folder else f"/api/public/{slug}/download"
    dl_text = "打包下载 (ZIP)" if node.is_folder else "下载原文件"

    # 引用代码在 Python 侧生成。必须提前算好：模板是 f-string，
    # 直接写 {html_tag} 会被当成变量求值而报 NameError。
    html_tag = _html_tag(kind, raw, name)
    md = _markdown(kind, raw, name)
    # 放进 JS 单引号字符串里，引号与换行需转义
    html_js = _js_str(html_tag)
    md_js = _js_str(md)

    page = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{name} · CloudRive</title>
<style>
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
background:#0f1319;color:#e8ecf2;min-height:100vh;padding:28px 20px 40px;
display:flex;flex-direction:column;align-items:center}}
.wrap{{width:100%;max-width:1000px}}
.head{{display:flex;align-items:flex-start;gap:14px;margin-bottom:20px;flex-wrap:wrap}}
.ico{{width:44px;height:44px;border-radius:12px;flex-shrink:0;display:flex;
align-items:center;justify-content:center;background:#1c2430;color:#8ab4ff}}
.ico.folder{{background:#2a2318;color:#e0a458}}
.ico.gen{{background:#1c2430;color:#7d8796}}
.meta{{min-width:0;flex:1}}
h1{{font-size:17px;font-weight:600;word-break:break-all;line-height:1.4}}
.sub{{font-size:12.5px;color:#8b95a5;margin-top:3px}}
.stage{{background:#161c25;border:1px solid #232c39;border-radius:14px;padding:14px;
display:flex;align-items:center;justify-content:center;min-height:220px;overflow:hidden}}
.img-stage{{padding:0;background:#0b0e13}}
.img-stage img{{max-width:100%;max-height:74vh;display:block;cursor:zoom-in;
transition:filter .2s}}
.img-stage.blur img{{filter:blur(18px);transform:scale(1.04)}}
.audio-stage,.folder-stage,.pdf-stage{{flex-direction:column;gap:0;padding:36px}}
.pdf-stage iframe{{width:100%;height:74vh;border:none;border-radius:8px;background:#fff}}
.text-stage{{display:block;padding:0;max-height:74vh;overflow:auto}}
.text-stage pre{{padding:20px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;
font-size:12.5px;line-height:1.7;color:#c9d3e0;white-space:pre-wrap;word-break:break-word}}
.tip{{font-size:12px;color:#6b7482;text-align:center;margin-top:12px}}
.acts{{display:flex;gap:9px;margin-top:18px;flex-wrap:wrap;justify-content:center}}
.btn{{display:inline-flex;align-items:center;gap:7px;height:38px;padding:0 18px;
border-radius:9px;font-size:13.5px;font-weight:500;text-decoration:none;cursor:pointer;
border:1px solid #2b3542;background:#1a212c;color:#dbe2ec;transition:background .15s}}
.btn:hover{{background:#222b37}}
.btn.pri{{background:#2f6df6;border-color:#2f6df6;color:#fff}}
.btn.pri:hover{{background:#2559d8}}
.fields{{margin-top:22px;background:#161c25;border:1px solid #232c39;border-radius:12px;
padding:14px 16px;display:none}}
.fields.on{{display:block}}
.fields h3{{font-size:12px;font-weight:600;color:#8b95a5;letter-spacing:.4px;
text-transform:uppercase;margin-bottom:11px}}
.field{{display:flex;align-items:center;gap:9px;margin-bottom:9px}}
.field:last-child{{margin-bottom:0}}
.field label{{font-size:12px;color:#8b95a5;width:66px;flex-shrink:0}}
.field input{{flex:1;height:34px;padding:0 11px;background:#0f141b;border:1px solid #262f3c;
border-radius:7px;color:#c9d3e0;font-size:11.5px;font-family:ui-monospace,Menlo,monospace;
outline:none;min-width:0}}
.field button{{height:34px;padding:0 13px;border-radius:7px;border:1px solid #2b3542;
background:#1a212c;color:#dbe2ec;font-size:12.5px;cursor:pointer;flex-shrink:0}}
.field button:hover{{background:#222b37}}
.linkbtn{{margin-top:14px;background:none;border:none;color:#6f8fd8;font-size:12.5px;
cursor:pointer;text-decoration:underline;text-underline-offset:2px}}
@media (max-width:560px){{.field{{flex-wrap:wrap}}.field label{{width:auto}}}}
</style>
</head>
<body>
<div class="wrap">
  <div class="head">
    <div class="ico {'folder' if kind == 'folder' else ('gen' if kind == 'none' else '')}">
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor"
        stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
        {'<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7z"/>' if kind == 'folder' else '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/>'}
      </svg>
    </div>
    <div class="meta">
      <h1>{name}</h1>
      <div class="sub">{size} · CloudRive</div>
    </div>
  </div>

  {body}

  <div class="acts">
    <a class="btn pri" href="{dl}">{dl_text}</a>
    <a class="btn" href="/s/{slug}">分享页</a>
    <button class="btn" id="btnFields">复制直链</button>
  </div>

  <div class="fields" id="fields">
    <h3>可复制的链接</h3>
    <div class="field">
      <label>直链</label>
      <input type="text" id="fRaw" readonly value="">
      <button data-copy="fRaw">复制</button>
    </div>
    <div class="field">
      <label>预览页</label>
      <input type="text" id="fPage" readonly value="">
      <button data-copy="fPage">复制</button>
    </div>
    <div class="field">
      <label>下载地址</label>
      <input type="text" id="fDl" readonly value="">
      <button data-copy="fDl">复制</button>
    </div>
    <div class="field">
      <label>HTML</label>
      <input type="text" id="fHtml" readonly value="">
      <button data-copy="fHtml">复制</button>
    </div>
    <div class="field">
      <label>Markdown</label>
      <input type="text" id="fMd" readonly value="">
      <button data-copy="fMd">复制</button>
    </div>
  </div>
</div>

<script>
(function () {{
  var origin = location.origin;
  var raw = origin + '{raw}';
  var page = origin + '/p/{slug}';
  var dl = origin + '{dl}';
  document.getElementById('fRaw').value = raw;
  document.getElementById('fPage').value = page;
  document.getElementById('fDl').value = dl;
  document.getElementById('fHtml').value = '{html_js}';
  document.getElementById('fMd').value = '{md_js}';

  var fields = document.getElementById('fields');
  document.getElementById('btnFields').onclick = function () {{
    fields.classList.toggle('on');
    if (fields.classList.contains('on')) {{
      document.getElementById('fRaw').select();
    }}
  }};

  document.querySelectorAll('[data-copy]').forEach(function (b) {{
    b.onclick = function () {{
      var el = document.getElementById(b.dataset.copy);
      el.focus(); el.select(); el.setSelectionRange(0, el.value.length);
      var done = false;
      try {{ done = document.execCommand('copy'); }} catch (e) {{}}
      if (!done && navigator.clipboard) {{
        navigator.clipboard.writeText(el.value).then(function () {{ ok(b); }}, function () {{ fail(b); }});
      }} else {{ done ? ok(b) : fail(b); }}
    }};
  }});

  function ok(b) {{
    var o = b.textContent; b.textContent = '已复制';
    setTimeout(function () {{ b.textContent = o; }}, 1500);
  }}
  function fail(b) {{
    var o = b.textContent; b.textContent = '请手动复制';
    setTimeout(function () {{ b.textContent = o; }}, 1800);
  }}

  // 图片：点击切换背景虚化，便于查看纯色图的细节
  var img = document.getElementById('pvImg');
  if (img) {{
    img.onclick = function () {{
      img.parentElement.classList.toggle('blur');
    }};
  }}

  // 文本：只取前 512 KB，避免超大日志卡死页面
  var pre = document.getElementById('pvText');
  if (pre) {{
    fetch('{raw}').then(function (r) {{ return r.text(); }}).then(function (t) {{
      pre.textContent = t.length > 512 * 1024
        ? t.slice(0, 512 * 1024) + '\\n\\n... 内容过长，已截断'
        : (t || '（空文件）');
    }}).catch(function () {{
      pre.textContent = '加载失败，请点击下方按钮下载查看';
    }});
  }}
}})();
</script>
</body>
</html>"""

    return HTMLResponse(page)


def _js_str(s: str) -> str:
    """转义成可安全嵌入 JS 单引号字符串的字面量。

    用于把 HTML 引用代码之类含引号的文本塞进模板里的 JS。
    """
    return (
        s.replace("\\", "\\\\")
        .replace("'", "\\'")
        .replace("\r", "\\r")
        .replace("\n", "\\n")
    )


def _html_tag(kind: str, raw: str, name: str) -> str:
    if kind == "image":
        return f'<img src="{raw}" alt="{name}">'
    if kind == "video":
        return f'<video src="{raw}" controls></video>'
    if kind == "audio":
        return f'<audio src="{raw}" controls></audio>'
    return f'<a href="{raw}">{name}</a>'


def _markdown(kind: str, raw: str, name: str) -> str:
    if kind == "image":
        return f"![{name}]({raw})"
    if kind in ("video", "audio"):
        return f"[{name}]({raw})"
    return f"[{name}]({raw})"
