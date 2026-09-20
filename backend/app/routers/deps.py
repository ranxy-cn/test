"""FastAPI 鉴权依赖：解析 Bearer token → 校验白名单 → 加载用户 → 权限断言。"""

from __future__ import annotations

from dataclasses import dataclass, field

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User, UserToken, utcnow
from app.security import TokenError, decode_access_token


@dataclass
class CurrentUser:
    id: int
    username: str
    display_name: str
    roles: list[str]
    permissions: set[str] = field(default_factory=set)
    jti: str = ""

    def has(self, *perm_codes: str) -> bool:
        return all(p in self.permissions for p in perm_codes)


def _to_current_user(user: User, jti: str = "") -> CurrentUser:
    perms: set[str] = set()
    for role in user.roles:
        for perm in role.permissions:
            perms.add(perm.code)
    return CurrentUser(
        id=user.id,
        username=user.username,
        display_name=user.display_name or user.username,
        roles=[r.code for r in user.roles],
        permissions=perms,
        jti=jti,
    )


def get_request_ip(request: Request) -> str:
    # nginx 侧已配置 X-Forwarded-For；直连时退化为 socket 地址
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()[:64]
    return (request.client.host if request.client else "")[:64]


def _unauthorized(detail: str = "未登录或登录已过期") -> HTTPException:
    return HTTPException(status_code=401, detail=detail, headers={"WWW-Authenticate": "Bearer"})


def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
) -> CurrentUser:
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        raise _unauthorized()
    token = auth.split(" ", 1)[1].strip()
    if not token:
        raise _unauthorized()

    try:
        payload = decode_access_token(token)
    except TokenError:
        raise _unauthorized() from None

    jti = payload["jti"]
    row = db.get(UserToken, jti)
    if row is None or row.revoked_at is not None or row.expires_at <= utcnow():
        raise _unauthorized("登录状态已失效，请重新登录")

    user = db.get(User, int(payload["sub"]))
    if user is None or not user.is_active:
        raise _unauthorized("账号不可用")

    return _to_current_user(user, jti=jti)


def require_perm(*perm_codes: str):
    """权限校验依赖：当前用户必须同时拥有给定权限点。"""

    def _checker(current: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        missing = [p for p in perm_codes if p not in current.permissions]
        if missing:
            raise HTTPException(403, f"权限不足，缺少：{', '.join(missing)}")
        return current

    return _checker
