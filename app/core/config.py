"""应用配置：全部通过环境变量注入，敏感项不落代码。"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # ---- 基础 ----
    app_name: str = "CloudRive"
    # 后端对外地址，用于拼接分享链接与直传签名回调
    public_base_url: str = "http://127.0.0.1:8000"

    # ---- 存储后端: local | s3 ----
    storage_backend: Literal["local", "s3"] = "local"

    # ---- 本地存储 ----
    # 默认存到项目目录下的 document 文件夹（而非data/storage）
    local_storage_dir: Path = Path("./document")

    # ---- S3 / MinIO 兼容存储 ----
    s3_endpoint: str | None = None            # MinIO 形如 http://minio:9000
    s3_region: str = "us-east-1"
    s3_bucket: str = "cloudrive"
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    # 是否使用自签名证书（MinIO 默认是 http，http 时保持 False）
    s3_use_path_style: bool = True

    # ---- 分片上传 ----
    # 分片大小需 >= 5 MiB（S3 协议硬性要求），默认 32 MiB
    multipart_chunk_size: int = 32 * 1024 * 1024
    # 小于该阈值的文件走后端中转（单请求上传），更大的走直传
    direct_upload_threshold: int = 8 * 1024 * 1024

    # ---- 数据库 ----
    database_url: str = "sqlite:///./data/cloudrive.db"

    # ---- 跨域 ----
    cors_origins: str = "*"

    # ---- 打包下载 ----
    # 文件夹打包为 ZIP 时，超过该大小阈值将提示前端分卷
    zip_stream_threshold: int = 2 * 1024 * 1024 * 1024

    @property
    def cors_origin_list(self) -> list[str]:
        raw = (self.cors_origins or "").strip()
        if raw == "*":
            return ["*"]
        return [x.strip() for x in raw.split(",") if x.strip()]


settings = Settings()