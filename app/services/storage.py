"""存储抽象层。

设计要点：上传 / 下载的字节流**不经过应用服务器**。
- local 后端：返回 Nginx X-Accel-Redirect 头，由 Nginx 直出文件，零带宽占用
- s3 后端：返回 presigned URL，浏览器直连对象存储

应用服务器只负责签发凭证与维护元数据，因此不受单机带宽限制。
"""
from __future__ import annotations

import os
import shutil
from abc import ABC, abstractmethod
from pathlib import Path
from typing import AsyncIterator

from app.core.config import settings
from app.services.runtime_settings import get_runtime


class StorageBackend(ABC):
    """统一存储接口。"""

    # ---------- 预签名 / 直传 ----------
    @abstractmethod
    def presign_put(self, path: str, expires: int = 3600, content_type: str | None = None) -> str | None:
        """返回直传上传 URL；local 后端返回 None（走 Nginx 中转）。"""

    @abstractmethod
    def presign_get(self, path: str, filename: str, expires: int = 3600) -> str | None:
        """返回直连下载 URL；local 后端返回 None。"""

    # ---------- 服务端读写（供打包/ 小文件使用）----------
    # root 仅 local 后端使用：指定历史存储根目录以读取切换路径前的文件
    @abstractmethod
    def size_of(self, path: str, root: Path | None = None) -> int: ...

    @abstractmethod
    def exists(self, path: str, root: Path | None = None) -> bool: ...

    @abstractmethod
    def read_chunks(
        self, path: str, chunk_size: int = 1024 * 1024, root: Path | None = None
    ) -> AsyncIterator[bytes]:
        """分块读取，用于流式打包 ZIP，避免整文件进内存。"""

    @abstractmethod
    def write_stream(self, path: str, stream, size: int = 0, root: Path | None = None) -> None:
        """接收可读流写入存储。"""

    @abstractmethod
    def delete(self, path: str, root: Path | None = None) -> None: ...

    @abstractmethod
    def move(self, src: str, dst: str, root: Path | None = None) -> None: ...

    # ---------- 分片（仅 S3 需要，local 走分片上传端点）----------
    def create_multipart(self, path: str) -> tuple[str | None, list[dict]]:
        """返回 (upload_id, [{partNumber, url}])"""

    def presign_part(self, path: str, upload_id: str, part_number: int, expires: int = 3600) -> str:
        raise NotImplementedError

    def complete_multipart(self, path: str, upload_id: str, parts: list[dict]) -> None: ...

    def abort_multipart(self, path: str, upload_id: str) -> None: ...


# ============================ 本地存储 ============================
class LocalStorage(StorageBackend):
    """本地磁盘存储，配合 Nginx X-Accel-Redirect 实现零带宽直出。

    根目录可在运行时修改（页面设置），改动后自动重建实例。
    """

    def __init__(self, root: Path | None = None):
        self._set_root(root or Path(get_runtime("local_storage_dir")))

    def _set_root(self, root: Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def root_path(self) -> Path:
        """返回当前根目录，若运行时配置已变更则重建。"""
        want = Path(get_runtime("local_storage_dir")).expanduser().resolve()
        if want != self.root:
            self._set_root(want)
        return self.root

    def _full(self, path: str, root: Path | None = None) -> Path:
        """拼接并校验路径，防止目录穿越。

        root 为None 时使用当前生效的根目录；
        传入历史根目录可读取切换路径前的文件。
        """
        base = Path(root).expanduser().resolve() if root else self.root_path()
        target = (base / path).resolve()
        if not str(target).startswith(str(base)):
            raise ValueError("非法存储路径")
        return target

    def _ensure_parent(self, path: str, root: Path | None = None) -> Path:
        full = self._full(path, root)
        full.parent.mkdir(parents=True, exist_ok=True)
        return full

    def presign_put(self, path: str, expires: int = 3600, content_type: str | None = None) -> str | None:
        # 本地后端由 /api/fs/upload 端点接收，前端直传该端点
        return None

    def presign_get(self, path: str, filename: str, expires: int = 3600) -> str | None:
        # 由 /d/{path} 端点配合 X-Accel-Redirect 输出
        return None

    def size_of(self, path: str, root: Path | None = None) -> int:
        p = self._full(path, root)
        return p.stat().st_size if p.exists() else 0

    def exists(self, path: str, root: Path | None = None) -> bool:
        return self._full(path, root).exists()

    async def read_chunks(
        self, path: str, chunk_size: int = 1024 * 1024, root: Path | None = None
    ) -> AsyncIterator[bytes]:
        p = self._full(path, root)
        if not p.exists():
            raise FileNotFoundError(path)
        with open(p, "rb") as f:
            while chunk := f.read(chunk_size):
                yield chunk

    def write_stream(self, path: str, stream, size: int = 0, root: Path | None = None) -> None:
        full = self._ensure_parent(path, root)
        with open(full, "wb") as f:
            shutil.copyfileobj(stream, f, length=1024 * 1024)

    def delete(self, path: str, root: Path | None = None) -> None:
        p = self._full(path, root)
        if p.exists():
            if p.is_dir():
                shutil.rmtree(p)
            else:
                p.unlink()

    def move(self, src: str, dst: str, root: Path | None = None) -> None:
        s, d = self._full(src, root), self._ensure_parent(dst, root)
        shutil.move(str(s), str(d))


# ============================ S3 / MinIO ============================
class S3Storage(StorageBackend):
    """S3 兼容存储（MinIO / AWS S3 / 阿里云 OSS 等）。"""

    def __init__(self):
        import boto3
        from botocore.config import Config

        self.bucket = settings.s3_bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint,
            region_name=settings.s3_region,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        )

    def presign_put(self, path: str, expires: int = 3600, content_type: str | None = None) -> str | None:
        params = {"Bucket": self.bucket, "Key": path}
        if content_type:
            params["ContentType"] = content_type
        return self.client.generate_presigned_url(
            "put_object", Params=params, ExpiresIn=expires
        )

    def presign_get(self, path: str, filename: str, expires: int = 3600) -> str | None:
        from urllib.parse import quote

        disposition = f'attachment; filename="{filename}"'
        return self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": path, "ResponseContentDisposition": disposition},
            ExpiresIn=expires,
        )

    def size_of(self, path: str, root: Path | None = None) -> int:
        return int(self.client.head_object(Bucket=self.bucket, Key=path)["ContentLength"])

    def exists(self, path: str, root: Path | None = None) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=path)
            return True
        except Exception:
            return False

    async def read_chunks(
        self, path: str, chunk_size: int = 1024 * 1024, root: Path | None = None
    ) -> AsyncIterator[bytes]:
        # S3 场景下文件夹打包建议改用服务端生成，避免占用本机带宽
        obj = self.client.get_object(Bucket=self.bucket, Key=path)
        while chunk := obj["Body"].read(chunk_size):
            yield chunk

    def write_stream(self, path: str, stream, size: int = 0, root: Path | None = None) -> None:
        self.client.upload_fileobj(stream, self.bucket, path)

    def delete(self, path: str, root: Path | None = None) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=path)

    def move(self, src: str, dst: str, root: Path | None = None) -> None:
        self.client.copy_object(
            Bucket=self.bucket, Key=dst,
            CopySource={"Bucket": self.bucket, "Key": src},
        )
        self.client.delete_object(Bucket=self.bucket, Key=src)

    def create_multipart(self, path: str) -> tuple[str | None, list[dict]]:
        resp = self.client.create_multipart_upload(Bucket=self.bucket, Key=path)
        return resp["UploadId"], []

    def presign_part(self, path: str, upload_id: str, part_number: int, expires: int = 3600) -> str:
        return self.client.generate_presigned_url(
            "upload_part",
            Params={"Bucket": self.bucket, "Key": path, "UploadId": upload_id, "PartNumber": part_number},
            ExpiresIn=expires,
        )

    def complete_multipart(self, path: str, upload_id: str, parts: list[dict]) -> None:
        self.client.complete_multipart_upload(
            Bucket=self.bucket, Key=path, UploadId=upload_id,
            MultipartUpload={"Parts": sorted(parts, key=lambda p: p["PartNumber"])},
        )

    def abort_multipart(self, path: str, upload_id: str) -> None:
        self.client.abort_multipart_upload(Bucket=self.bucket, Key=path, UploadId=upload_id)


_storage: StorageBackend | None = None


def get_storage() -> StorageBackend:
    """获取存储实例。运行时配置变更后自动重建（如切换 local/s3）。"""
    global _storage
    backend = get_runtime("storage_backend")

    # 后端类型变了才重建实例
    if _storage is None or getattr(_storage, "backend_name", settings.storage_backend) != backend:
        _storage = S3Storage() if backend == "s3" else LocalStorage()
        _storage.backend_name = backend  # type: ignore[attr-defined]
    return _storage