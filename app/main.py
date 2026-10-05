"""FastAPI 应用入口。"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api import download, files, nodes, share
from app.api import settings as settings_api
from app.core.config import settings
from app.core.db import Base, engine
from app.services.runtime_settings import get_runtime

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 建表（生产建议改用 Alembic 迁移）
    if get_runtime("storage_backend") == "local":
        Path(get_runtime("local_storage_dir")).mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(
    title=settings.app_name,
    description="不限速云端存储 · 直传直下· 支持 GB 级文件与文件夹打包",
    version="1.0.0",
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

    @app.get("/")
    def index():
        return FileResponse(FRONTEND_DIR / "index.html")