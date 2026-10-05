"""认证相关接口：登录、登出、状态查询。"""
from __future__ import annotations

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel

from app.core import auth
from app.core.config import settings
from app.core.errors import ApiError

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginIn(BaseModel):
    password: str
    remember: bool = False


class StatusOut(BaseModel):
    enabled: bool
    authenticated: bool
    # 供前端判断是否需要显示登录页
    must_login: bool


@router.post("/login")
def login(payload: LoginIn, request: Request, response: Response):
    """校验密码并签发令牌。

    未配置密码时视为无需认证，直接放行。
    """
    if not settings.auth_enabled:
        return {"ok": True, "enabled": False, "message": "未启用认证"}

    ip = auth.client_ip(request)

    # 防暴力破解
    if auth._attempts.is_locked(ip):
        wait = settings.lockout_seconds // 60
        raise ApiError(
            429,
            f"尝试次数过多，请 {wait} 分钟后再试",
            "locked",
        )

    if not auth.verify_password(payload.password):
        left = auth._attempts.fail(ip)
        tip = f"还可尝试 {left} 次" if left > 0 else "账号已临时锁定"
        raise ApiError(401, f"密码错误，{tip}", "bad_password")

    # 登录成功，清除失败计数
    auth._attempts.reset(ip)

    ttl = settings.remember_days * 24 * 3600 if payload.remember else settings.session_max_age
    token, ttl = auth.issue_token(ttl)

    # 同时写入 Cookie（HttpOnly 防XSS 窃取）与返回体（供前端存localStorage）
    response.set_cookie(
        key="cr_token",
        value=token,
        max_age=ttl,
        httponly=True,
        samesite="lax",
        secure=False,          # 部署到 HTTPS 后可在 .env 开启
        path="/",
    )

    return {
        "ok": True,
        "token": token,
        "expires_in": ttl,
        "remember": payload.remember,
    }


@router.post("/logout", response_model=StatusOut)
def logout(response: Response):
    """清除 Cookie。前端同时清除本地存储。"""
    response.delete_cookie(key="cr_token", path="/")
    return StatusOut(
        enabled=settings.auth_enabled,
        authenticated=False,
        must_login=settings.auth_enabled,
    )


@router.get("/status", response_model=StatusOut)
def status(request: Request):
    """查询认证状态。前端启动时调用决定显示登录页还是面板。"""
    enabled = settings.auth_enabled
    ok = auth.is_authenticated(request)
    return StatusOut(enabled=enabled, authenticated=ok, must_login=enabled and not ok)