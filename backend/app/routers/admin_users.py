"""用户与角色管理（admin 专用）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Role, User, UserRole, utcnow
from app.routers.deps import CurrentUser, require_perm
from app.security import hash_password, validate_password, validate_username

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


class UserCreateIn(BaseModel):
    username: str = Field(min_length=1, max_length=32)
    password: str = Field(min_length=1, max_length=64)
    display_name: str = Field(default="", max_length=64)
    email: str | None = Field(default=None, max_length=128)
    role_codes: list[str] = Field(default_factory=list)


class UserPatchIn(BaseModel):
    display_name: str | None = Field(default=None, max_length=64)
    email: str | None = Field(default=None, max_length=128)
    is_active: bool | None = None
    password: str | None = Field(default=None, max_length=64)
    role_codes: list[str] | None = None


def _user_out(db: Session, user: User) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "email": user.email,
        "is_active": user.is_active,
        "roles": user.role_codes,
        "last_login_at": user.last_login_at,
        "last_login_ip": user.last_login_ip,
        "locked_until": user.locked_until,
        "created_at": user.created_at,
    }


def _get_role_map(db: Session, codes: list[str]) -> dict[str, Role]:
    rows = db.scalars(select(Role).where(Role.code.in_(codes))).all() if codes else []
    found = {r.code: r for r in rows}
    missing = [c for c in codes if c not in found]
    if missing:
        raise HTTPException(404, f"角色不存在：{', '.join(missing)}")
    return found


@router.get("/users", dependencies=[Depends(require_perm("users:manage"))])
def list_users(db: Session = Depends(get_db)):
    rows = db.scalars(select(User).order_by(User.id)).all()
    return {"items": [_user_out(db, u) for u in rows]}


@router.get("/roles", dependencies=[Depends(require_perm("users:manage"))])
def list_roles(db: Session = Depends(get_db)):
    roles = db.scalars(select(Role).order_by(Role.id)).all()
    return {
        "items": [
            {
                "code": r.code,
                "name": r.name,
                "description": r.description,
                "permissions": sorted(p.code for p in r.permissions),
            }
            for r in roles
        ]
    }


@router.post("/users", dependencies=[Depends(require_perm("users:manage"))])
def create_user(body: UserCreateIn, db: Session = Depends(get_db)):
    errors = validate_username(body.username)
    errors += validate_password(body.password)
    if errors:
        raise HTTPException(422, "；".join(errors))

    username = body.username.strip()
    if db.scalar(select(User.id).where(User.username == username)) is not None:
        raise HTTPException(409, "用户名已存在")
    if body.email and db.scalar(select(User.id).where(User.email == body.email)) is not None:
        raise HTTPException(409, "邮箱已被使用")

    role_map = _get_role_map(db, body.role_codes)
    user = User(
        username=username,
        password_hash=hash_password(body.password),
        display_name=body.display_name or username,
        email=body.email or None,
        is_active=True,
    )
    db.add(user)
    db.flush()
    for code, role in role_map.items():
        db.add(UserRole(user_id=user.id, role_id=role.id))
    db.commit()
    db.refresh(user)
    return _user_out(db, user)


@router.patch("/users/{user_id}", dependencies=[Depends(require_perm("users:manage"))])
def patch_user(user_id: int, body: UserPatchIn, current: CurrentUser = Depends(require_perm("users:manage")),
               db: Session = Depends(get_db)):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(404, "用户不存在")

    # 从严：不允许自我停用/自我降权，避免锁死最后一个管理员
    if user.id == current.id:
        if body.is_active is False:
            raise HTTPException(409, "不能停用当前登录账号")
        if body.role_codes is not None and "admin" not in body.role_codes:
            raise HTTPException(409, "不能移除当前登录账号的 admin 角色")

    if body.password is not None:
        errors = validate_password(body.password)
        if errors:
            raise HTTPException(422, "；".join(errors))
        user.password_hash = hash_password(body.password)
    if body.display_name is not None:
        user.display_name = body.display_name
    if body.email is not None:
        user.email = body.email or None
    if body.is_active is not None:
        user.is_active = body.is_active
        if not body.is_active:
            user.locked_until = None
            user.failed_attempts = 0
    if body.role_codes is not None:
        role_map = _get_role_map(db, body.role_codes)
        for row in list(db.scalars(select(UserRole).where(UserRole.user_id == user.id)).all()):
            db.delete(row)
        db.flush()
        for role in role_map.values():
            db.add(UserRole(user_id=user.id, role_id=role.id))
    user.updated_at = utcnow()
    db.commit()
    db.refresh(user)
    return _user_out(db, user)


@router.post("/users/{user_id}/unlock", dependencies=[Depends(require_perm("users:manage"))])
def unlock_user(user_id: int, db: Session = Depends(get_db)):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(404, "用户不存在")
    user.locked_until = None
    user.failed_attempts = 0
    db.commit()
    return {"ok": True, "username": user.username}
