"""认证中间件：拦截未登录的 API 与面板请求。

免登录白名单（分享功能必须保持可访问）：
- POST /api/auth/login登录
- GET  /api/auth/status         登录状态
- GET  /api/health              健康检查
- GET  /login登录页本身（否则用户看不到登录表单）
- GET  /s/{slug}                分享页
- GET  /api/share/{slug}        分享信息
- GET  /api/public/{slug}/...   公开下载（凭 slug 校验，非任意文件）
- GET  /static/*                静态资源（登录页要靠它加载样式）

安全要点：/api/files/{id}/download **不在白名单**。
下载必须走 /api/public/{slug}/download，服务端校验 slug 存在且已开启分享，
否则知道 node_id 就能绕过密码下载任意文件。
"""
from __future__ import annotations

import re

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, RedirectResponse

from app.core.auth import is_authenticated
from app.core.config import settings

# ---- 免登录路径（正则）----
PUBLIC_PATTERNS = [
    r"^/api/auth/login$",
    r"^/api/auth/status$",
    r"^/api/health$",
    r"^/login/?$",                 # 登录页本身
    r"^/s/[^/]+$",                 # 分享页
    r"^/api/share/[^/]+$",         # 分享信息
    r"^/api/public/[^/]+/download(-zip)?$",  # 公开下载（凭 slug）
    r"^/static/",
    r"^/favicon\.ico$",
]

_COMPILED = [re.compile(p) for p in PUBLIC_PATTERNS]


def is_public_path(path: str, method: str = "GET") -> bool:
    """判断该请求是否无需登录。

    写操作（POST/PUT/DELETE）一律需登录，白名单只对 GET 生效，
    防止有人用 POST 调/public 路由绕过。
    """
    if not any(p.match(path) for p in _COMPILED):
        return False

    # 认证相关接口本身就是公开的
    if path.startswith("/api/auth/"):
        return True

    # 其余仅 GET 可免登录
    return method.upper() in ("GET", "HEAD", "OPTIONS")


class AuthMiddleware(BaseHTTPMiddleware):
    """面板与 API 的访问控制。

    行为：
    - 未启用认证 -> 全部放行
    - 已登录     -> 全部放行
    - 未登录     -> 页面请求 302 跳 /login；API 请求返回 401 JSON
    """

    async def dispatch(self, request, call_next):
        if not settings.auth_enabled:
            return await call_next(request)

        path = request.url.path
        method = request.method

        if is_public_path(path, method):
            return await call_next(request)

        if is_authenticated(request):
            return await call_next(request)

        # API 请求：返回 401 JSON，由前端跳登录页
        if path.startswith("/api/"):
            return JSONResponse(
                status_code=401,
                content={
                    "detail": {
                        "code": "unauthorized",
                        "message": "请先登录",
                    }
                },
                headers={"Cache-Control": "no-store"},
            )

        # 页面请求：302 重定向到登录页
        return RedirectResponse("/login", status_code=302)