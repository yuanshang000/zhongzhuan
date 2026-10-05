"""通用响应工具。"""
from __future__ import annotations

from fastapi import HTTPException


class ApiError(HTTPException):
    def __init__(self, status_code: int, message: str, code: str = "error"):
        super().__init__(status_code=status_code, detail={"code": code, "message": message})
        self.code = code
        self.message = message


def not_found(what: str = "文件或文件夹不存在") -> ApiError:
    return ApiError(404, what, "not_found")


def bad_request(msg: str, code: str = "bad_request") -> ApiError:
    return ApiError(400, msg, code)


def conflict(msg: str) -> ApiError:
    return ApiError(409, msg, "conflict")


def forbidden(msg: str = "禁止的操作") -> ApiError:
    return ApiError(403, msg, "forbidden")