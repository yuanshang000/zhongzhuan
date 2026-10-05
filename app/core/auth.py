"""面板访问认证。

设计要点：
1. **无状态令牌**：用 HMAC-SHA256 签名，不存session。多进程、重启都有效。
2. **密码比对**：使用恒定时间比较，防止时序侧信道。
3. **防暴力破解**：按 IP 记录失败次数，超限临时锁定。
4. **分享链接不受影响**：分享页与分享下载走独立路由，不经过此校验。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import time
from collections import defaultdict
from threading import Lock

from app.core.config import settings

# ---------------------------------------------------------------- 密钥

_secret_cache: str = ""
_secret_lock = Lock()


def _get_secret() -> str:
    """获取签名密钥。未配置时随机生成并保持在进程内。

    生产环境应在 .env 配置固定值，否则重启后需重新登录。
    """
    global _secret_cache
    with _secret_lock:
        if settings.auth_secret:
            return settings.auth_secret
        if not _secret_cache:
            _secret_cache = secrets.token_urlsafe(48)
        return _secret_cache


# ---------------------------------------------------------------- 密码

def verify_password(candidate: str) -> bool:
    """恒定时间比较密码，避免时序侧信道。"""
    expected = settings.panel_password
    if not expected:
        return True
    return hmac.compare_digest(
        (candidate or "").encode("utf-8"), expected.encode("utf-8")
    )


# ---------------------------------------------------------------- 令牌

def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _b64d(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def issue_token(ttl_seconds: int | None = None) -> tuple[str, int]:
    """签发登录令牌。返回 (token, 有效期秒数)。

    载荷只含过期时间，不含密码，任何人无法反推。
    """
    ttl = int(ttl_seconds or settings.session_max_age)
    expire_at = int(time.time()) + ttl
    payload = _b64e(str(expire_at).encode())

    sig = hmac.new(
        _get_secret().encode(), payload.encode(), hashlib.sha256
    ).digest()

    return f"{payload}.{_b64e(sig)}", ttl


def verify_token(token: str | None) -> bool:
    """校验令牌签名与有效期。"""
    if not token:
        return False

    parts = token.split(".")
    if len(parts) != 2:
        return False

    payload, sig_b64 = parts
    try:
        expect = hmac.new(
            _get_secret().encode(), payload.encode(), hashlib.sha256
        ).digest()
        if not hmac.compare_digest(_b64d(sig_b64), expect):
            return False
        expire_at = int(_b64d(payload).decode())
    except Exception:
        return False

    return time.time() < expire_at


# ---------------------------------------------------------------- 防爆破

class _AttemptTracker:
    """按 IP 记录登录失败次数，超限锁定。"""

    def __init__(self) -> None:
        self._lock = Lock()
        self._fails: dict[str, list[float]] = defaultdict(list)

    def _clean(self, ip: str) -> list[float]:
        """剔除窗口外的失败记录。"""
        now = time.time()
        window = settings.lockout_seconds
        hits = [t for t in self._fails.get(ip, []) if now - t < window]
        self._fails[ip] = hits
        return hits

    def is_locked(self, ip: str) -> bool:
        with self._lock:
            return len(self._clean(ip)) >= settings.max_login_attempts

    def fail(self, ip: str) -> int:
        """记录一次失败，返回剩余可尝试次数。"""
        with self._lock:
            hits = self._clean(ip)
            hits.append(time.time())
            self._fails[ip] = hits
            return max(0, settings.max_login_attempts - len(hits))

    def reset(self, ip: str) -> None:
        with self._lock:
            self._fails.pop(ip, None)


_attempts = _AttemptTracker()

# 用于测试重置
def reset_attempts() -> None:
    _attempts._fails.clear()


# ---------------------------------------------------------------- 请求辅助

def client_ip(request) -> str:
    """取客户端 IP。反向代理场景优先读 X-Forwarded-For。"""
    fwd = request.headers.get("X-Forwarded-For")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def extract_token(request) -> str | None:
    """依次从 Authorization 头、Cookie 中提取令牌。"""
    auth = request.headers.get("Authorization")
    if auth and auth.lower().startswith("bearer "):
        return auth[7:].strip()

    cookie = request.cookies.get("cr_token")
    if cookie:
        return cookie.strip()

    return None


def is_authenticated(request) -> bool:
    """未启用认证时一律放行。"""
    if not settings.auth_enabled:
        return True
    return verify_token(extract_token(request))