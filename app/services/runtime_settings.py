"""运行时配置：可在页面中修改存储路径等参数，无需重启服务。

持久化到 data/runtime_settings.json，重启后仍生效。
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from app.core.config import settings

_LOCK = threading.Lock()
_RUNTIME_FILE = Path("data/runtime_settings.json")

# 允许通过页面修改的键（白名单，防止任意写入）
ALLOWED_KEYS = {
    "local_storage_dir",   # 存储根目录
    "storage_backend",     # local | s3
    "multipart_chunk_size",
    "direct_upload_threshold",
}

_DEFAULTS: dict[str, Any] = {
    "local_storage_dir": str(settings.local_storage_dir),
    "storage_backend": settings.storage_backend,
    "multipart_chunk_size": settings.multipart_chunk_size,
    "direct_upload_threshold": settings.direct_upload_threshold,
}


def _read() -> dict[str, Any]:
    if not _RUNTIME_FILE.exists():
        return dict(_DEFAULTS)
    try:
        raw = json.loads(_RUNTIME_FILE.read_text(encoding="utf-8"))
        return {k: v for k, v in raw.items() if k in ALLOWED_KEYS}
    except Exception:
        return dict(_DEFAULTS)


def _write(data: dict[str, Any]) -> None:
    _RUNTIME_FILE.parent.mkdir(parents=True, exist_ok=True)
    _RUNTIME_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def get_runtime(key: str) -> Any:
    """按键读取当前生效值（运行时配置优先，回落默认值）。"""
    with _LOCK:
        data = _read()
    return data.get(key, _DEFAULTS.get(key))


def get_all() -> dict[str, Any]:
    with _LOCK:
        return {**_DEFAULTS, **_read()}


def update(new: dict[str, Any]) -> dict[str, Any]:
    """校验并写入运行时配置。返回合并后的完整配置。"""
    with _LOCK:
        cur = _read()

        for k, v in new.items():
            if k not in ALLOWED_KEYS:
                continue

            if k in ("multipart_chunk_size", "direct_upload_threshold"):
                # S3 协议要求分片 >= 5 MiB
                v = int(v)
                if k == "multipart_chunk_size" and v < 5 * 1024 * 1024:
                    raise ValueError("分片大小不能小于 5 MiB（S3 协议限制）")
                if v < 1024:
                    raise ValueError("阈值过小")

            if k == "storage_backend":
                v = str(v).lower()
                if v not in ("local", "s3"):
                    raise ValueError("存储后端只能是 local 或 s3")

            if k == "local_storage_dir":
                v = str(v).strip()
                if not v:
                    raise ValueError("存储路径不能为空")
                # 展开 ~ 与相对路径，转绝对路径
                p = Path(v).expanduser().resolve()
                p.mkdir(parents=True, exist_ok=True)
                v = str(p)

            cur[k] = v

        _write(cur)
        return {**_DEFAULTS, **cur}


def reset() -> dict[str, Any]:
    with _LOCK:
        _write({})
        return dict(_DEFAULTS)