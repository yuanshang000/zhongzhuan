"""FastAPI 应用入口。"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.api import auth_api, capacity_api, download, files, nodes, share
from app.api import settings as settings_api
from app.core.config import settings
from app.core.db import Base, engine
from app.core.middleware import AuthMiddleware
from app.services.runtime_settings import get_runtime

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 建表（生产建议改用 Alembic 迁移）
    if get_runtime("storage_backend") == "local":
        Path(get_runtime("local_storage_dir")).mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(bind=engine)

    if settings.auth_enabled:
        print(f"[auth] 面板密码保护已启用{'（未设置 AUTH_SECRET，重启后需重新登录）' if not settings.auth_secret else ''}")
    else:
        print("[auth] 未配置 PANEL_PASSWORD，任何人可直接访问面板")

    yield


app = FastAPI(
    title=settings.app_name,
    description="不限速云端存储 · 直传直下· 支持 GB 级文件与文件夹打包",
    version="1.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition", "X-Accel-Redirect"],
)

# 认证中间件：拦截未登录访问（分享链接已在白名单中放行）
app.add_middleware(AuthMiddleware)

app.include_router(auth_api.router)
app.include_router(capacity_api.router)
app.include_router(nodes.router)
app.include_router(files.router)
app.include_router(download.router)
app.include_router(share.router)
app.include_router(settings_api.router)


@app.get("/api/health")
def health():
    return {"status": "ok", "app": settings.app_name, "storage": get_runtime("storage_backend"), "root": get_runtime("local_storage_dir")}


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    """统一错误响应，避免前端拿到 HTML 错误页。"""
    return JSONResponse(
        status_code=500,
        content={"detail": {"code": "internal_error", "message": str(exc) or "服务器内部错误"}},
    )


# 前端静态资源（放在最后，避免抢占 API 路由）
if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

    # 说明：未登录时AuthMiddleware 已直接返回 401，
    # 能走到这里的都是已登录用户，无需重复判断。
    @app.get("/")
    def index():
        return FileResponse(FRONTEND_DIR / "index.html")

    @app.get("/login")
    def login_page():
        """登录页。未启用认证时直接进面板。"""
        if not settings.auth_enabled:
            return RedirectResponse("/", status_code=302)
        return FileResponse(FRONTEND_DIR / "login.html")