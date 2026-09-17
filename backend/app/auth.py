"""Workbench JWT/HMAC (HS256) bearer auth. Webhook and health stay public."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any

from fastapi import HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.config import get_settings

PUBLIC_API_PREFIXES = (
    "/api/v1/auth/login",
    "/api/v1/webhooks",
)
PUBLIC_EXACT = {
    "/health",
    "/docs",
    "/redoc",
    "/openapi.json",
}


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def _secret_eq(left: str, right: str) -> bool:
    return hmac.compare_digest(
        hashlib.sha256(left.encode("utf-8")).digest(),
        hashlib.sha256(right.encode("utf-8")).digest(),
    )


def is_public_path(path: str) -> bool:
    normalized = path.rstrip("/") or "/"
    if normalized in PUBLIC_EXACT or path in PUBLIC_EXACT:
        return True
    if path.startswith("/docs") or path.startswith("/redoc"):
        return True
    for prefix in PUBLIC_API_PREFIXES:
        if path == prefix or path.startswith(prefix + "/") or normalized == prefix:
            return True
    return False


def mint_access_token(username: str, *, now: int | None = None) -> tuple[str, int]:
    settings = get_settings()
    algorithm = (settings.auth_token_algorithm or "HS256").strip().upper()
    if algorithm != "HS256":
        raise HTTPException(500, "AUTH_TOKEN_ALGORITHM 仅支持 HS256")
    ttl = max(int(settings.auth_token_ttl_seconds), 60)
    issued = int(now if now is not None else time.time())
    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    payload = _b64url(
        json.dumps(
            {"sub": username, "iat": issued, "exp": issued + ttl, "typ": "access"},
            separators=(",", ":"),
        ).encode()
    )
    signing = f"{header}.{payload}".encode("ascii")
    sig = hmac.new(settings.auth_token_secret.encode("utf-8"), signing, hashlib.sha256).digest()
    return f"{header}.{payload}.{_b64url(sig)}", ttl


def verify_access_token(token: str) -> dict[str, Any]:
    settings = get_settings()
    parts = (token or "").split(".")
    if len(parts) != 3:
        raise HTTPException(401, "未登录或令牌无效")
    header_b64, payload_b64, sig_b64 = parts
    signing = f"{header_b64}.{payload_b64}".encode("ascii")
    expected = hmac.new(settings.auth_token_secret.encode("utf-8"), signing, hashlib.sha256).digest()
    try:
        given = _b64url_decode(sig_b64)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(401, "未登录或令牌无效") from exc
    if not hmac.compare_digest(given, expected):
        raise HTTPException(401, "未登录或令牌无效")
    try:
        header = json.loads(_b64url_decode(header_b64))
        payload = json.loads(_b64url_decode(payload_b64))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(401, "未登录或令牌无效") from exc
    if str(header.get("alg") or "").upper() != "HS256":
        raise HTTPException(401, "未登录或令牌无效")
    if int(payload.get("exp") or 0) < int(time.time()):
        raise HTTPException(401, "登录已过期")
    sub = str(payload.get("sub") or "")
    if not sub:
        raise HTTPException(401, "未登录或令牌无效")
    return payload


def authenticate(username: str, password: str) -> tuple[str, int, str]:
    settings = get_settings()
    expected_user = settings.admin_username
    expected_password = settings.admin_password
    if not expected_user or not expected_password:
        raise HTTPException(503, "未配置 ADMIN_USERNAME / ADMIN_PASSWORD")
    if not (_secret_eq(username, expected_user) and _secret_eq(password, expected_password)):
        raise HTTPException(401, "用户名或密码错误")
    token, ttl = mint_access_token(expected_user)
    return token, ttl, expected_user


def bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, remainder = authorization.partition(" ")
    if scheme.lower() != "bearer" or not remainder.strip():
        return None
    return remainder.strip()


class BearerAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        if request.method == "OPTIONS":
            return await call_next(request)
        path = request.url.path
        if is_public_path(path):
            return await call_next(request)
        if not path.startswith("/api/"):
            return await call_next(request)
        token = bearer_token(request.headers.get("authorization"))
        if not token:
            return JSONResponse({"detail": "未登录或令牌无效"}, status_code=401)
        try:
            payload = verify_access_token(token)
        except HTTPException as exc:
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
        request.state.user = payload.get("sub")
        return await call_next(request)
