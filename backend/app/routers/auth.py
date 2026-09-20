"""登录 / 登出 / 当前用户 / 修改密码。

安全策略（从严）：
- 统一错误文案，不暴露"用户是否存在"
- 连续失败 N 次锁定账号 lockout_minutes 分钟
- 同一 IP 窗口期内失败过多返回 429
- 登录成功/失败均写 login_logs 审计
- token jti 白名单落库：登出、改密后立即失效
"""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import LoginLog, User, UserToken, utcnow
from app.routers.deps import CurrentUser, get_current_user, get_request_ip
from app.security import (
    create_access_token,
    hash_password,
    validate_password,
    verify_password,
)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=64)


class ChangePasswordIn(BaseModel):
    old_password: str = Field(min_length=1, max_length=64)
    new_password: str = Field(min_length=1, max_length=64)


def _user_payload(db: Session, user: User) -> dict:
    perms: set[str] = set()
    for role in user.roles:
        perms.update(p.code for p in role.permissions)
    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "email": user.email,
        "roles": user.role_codes,
        "permissions": sorted(perms),
    }


def _log(db: Session, *, username: str, user_id: int | None, success: bool,
         reason: str, ip: str, user_agent: str) -> None:
    db.add(
        LoginLog(
            username=username[:64],
            user_id=user_id,
            success=success,
            fail_reason="" if success else reason[:64],
            ip=ip,
            user_agent=user_agent[:255],
        )
    )


@router.post("/login")
def login(body: LoginIn, request: Request, db: Session = Depends(get_db)):
    settings = get_settings()
    ip = get_request_ip(request)
    user_agent = request.headers.get("user-agent", "")[:255]
    username = body.username.strip()

    # 1) 同 IP 防爆破
    window_start = utcnow() - timedelta(minutes=settings.ip_fail_window_minutes)
    ip_fails = len(
        db.scalars(
            select(LoginLog.id).where(
                LoginLog.ip == ip,
                LoginLog.success.is_(False),
                LoginLog.created_at >= window_start,
            )
        ).all()
    )
    if ip_fails >= settings.ip_fail_max:
        _log(db, username=username, user_id=None, success=False,
             reason="ip_rate_limited", ip=ip, user_agent=user_agent)
        db.commit()
        raise HTTPException(429, "尝试过于频繁，请稍后再试")

    # 2) 统一失败出口（不区分用户名/密码错误，避免用户枚举）
    def fail(reason: str, user: User | None = None) -> HTTPException:
        _log(db, username=username, user_id=user.id if user else None,
             success=False, reason=reason, ip=ip, user_agent=user_agent)
        if user is not None:
            user.failed_attempts = (user.failed_attempts or 0) + 1
            if user.failed_attempts >= settings.lockout_max_attempts:
                user.locked_until = utcnow() + timedelta(minutes=settings.lockout_minutes)
                user.failed_attempts = 0
        db.commit()
        return HTTPException(401, "用户名或密码错误")

    user = db.scalar(select(User).where(User.username == username))
    if user is None:
        # 仍执行一次哈希，抹平"用户不存在"与"密码错误"的响应时间差
        verify_password(body.password, hash_password("timing-equalizer"))
        raise fail("no_such_user")

    if not user.is_active:
        _log(db, username=username, user_id=user.id, success=False,
             reason="disabled", ip=ip, user_agent=user_agent)
        db.commit()
        raise HTTPException(403, "账号已停用，请联系管理员")

    if user.locked_until is not None and user.locked_until > utcnow():
        _log(db, username=username, user_id=user.id, success=False,
             reason="locked", ip=ip, user_agent=user_agent)
        db.commit()
        retry_after = int((user.locked_until - utcnow()).total_seconds() // 60) + 1
        raise HTTPException(423, f"账号已锁定，请约 {retry_after} 分钟后重试或联系管理员")

    if not verify_password(body.password, user.password_hash):
        raise fail("bad_password", user)

    # 3) 登录成功：发放 token + 白名单落库
    token, jti, expires_at = create_access_token(user.id, ip=ip, user_agent=user_agent)
    db.add(UserToken(jti=jti, user_id=user.id, expires_at=expires_at, ip=ip, user_agent=user_agent))
    user.failed_attempts = 0
    user.locked_until = None
    user.last_login_at = utcnow()
    user.last_login_ip = ip
    _log(db, username=username, user_id=user.id, success=True, reason="", ip=ip, user_agent=user_agent)
    db.commit()
    db.refresh(user)

    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in": settings.access_token_expire_minutes * 60,
        "user": _user_payload(db, user),
    }


@router.post("/logout")
def logout(
    current: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = db.get(UserToken, current.jti)
    if row is not None and row.revoked_at is None:
        row.revoked_at = utcnow()
        db.commit()
    return {"ok": True}


@router.get("/me")
def me(current: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    user = db.get(User, current.id)
    if user is None:
        raise HTTPException(401, "账号不可用")
    return _user_payload(db, user)


@router.post("/change-password")
def change_password(
    body: ChangePasswordIn,
    current: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    user = db.get(User, current.id)
    if user is None:
        raise HTTPException(401, "账号不可用")
    if not verify_password(body.old_password, user.password_hash):
        raise HTTPException(400, "原密码不正确")
    errors = validate_password(body.new_password)
    if errors:
        raise HTTPException(422, "；".join(errors))
    if verify_password(body.new_password, user.password_hash):
        raise HTTPException(422, "新密码不能与旧密码相同")

    user.password_hash = hash_password(body.new_password)
    # 从严：改密后吊销该用户全部令牌，强制重新登录
    for token in db.scalars(
        select(UserToken).where(UserToken.user_id == user.id, UserToken.revoked_at.is_(None))
    ).all():
        token.revoked_at = utcnow()
    db.commit()
    return {"ok": True, "message": "密码已修改，请重新登录"}
