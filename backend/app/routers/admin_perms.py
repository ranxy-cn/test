"""菜单资源树管理 + 角色/权限动态授权（自主维护能力）。

- 菜单 CRUD：menus:manage
- 角色增删查、角色菜单授权、角色权限点分配、新增权限点：roles:manage
- 从严保护：admin 角色的菜单/权限授权不允许通过接口削减，保证最高管理员满权限。
"""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Menu, Permission, Role, RoleMenu, RolePermission, utcnow
from app.menus_seed import grant_tree_to_role
from app.routers.deps import require_perm

router = APIRouter(prefix="/api/v1/admin", tags=["admin-perms"])

MENU_TYPES = ("dir", "menu", "button")
CODE_RE = re.compile(r"^[a-z][a-z0-9:_-]*$")


class MenuCreateIn(BaseModel):
    parent_id: int | None = None
    code: str = Field(min_length=2, max_length=64)
    name: str = Field(min_length=1, max_length=64)
    type: str = Field(pattern="^(dir|menu|button)$")
    path: str | None = Field(default=None, max_length=128)
    perm_code: str | None = Field(default=None, max_length=64)
    icon: str = Field(default="", max_length=64)
    sort_order: int = 0
    visible: bool = True
    status: str = Field(default="enabled", pattern="^(enabled|disabled)$")
    remark: str = Field(default="", max_length=256)


class MenuPatchIn(BaseModel):
    parent_id: int | None = None
    name: str | None = Field(default=None, max_length=64)
    path: str | None = Field(default=None, max_length=128)
    perm_code: str | None = Field(default=None, max_length=64)
    icon: str | None = Field(default=None, max_length=64)
    sort_order: int | None = None
    visible: bool | None = None
    status: str | None = Field(default=None, pattern="^(enabled|disabled)$")
    remark: str | None = Field(default=None, max_length=256)


class RoleMenuIn(BaseModel):
    menu_codes: list[str] = Field(default_factory=list)


class RolePermIn(BaseModel):
    permission_codes: list[str] = Field(default_factory=list)


class RoleCreateIn(BaseModel):
    code: str = Field(min_length=2, max_length=32)
    name: str = Field(min_length=1, max_length=64)
    description: str = Field(default="", max_length=256)
    menu_codes: list[str] = Field(default_factory=list)
    permission_codes: list[str] = Field(default_factory=list)


class PermissionCreateIn(BaseModel):
    code: str = Field(min_length=3, max_length=64)
    name: str = Field(min_length=1, max_length=64)
    description: str = Field(default="", max_length=256)


def _menu_out(m: Menu) -> dict:
    return {
        "id": m.id,
        "parent_id": m.parent_id,
        "code": m.code,
        "name": m.name,
        "type": m.type,
        "path": m.path,
        "perm_code": m.perm_code,
        "icon": m.icon,
        "sort_order": m.sort_order,
        "visible": m.visible,
        "status": m.status,
        "remark": m.remark,
    }


def _menu_tree_all(db: Session) -> list[dict]:
    rows = db.scalars(select(Menu).order_by(Menu.sort_order, Menu.id)).all()
    by_id = {m.id: m for m in rows}
    nodes = {m.id: {**_menu_out(m), "children": []} for m in rows}
    roots = []
    for mid, m in by_id.items():
        parent = nodes.get(m.parent_id) if m.parent_id else None
        if parent is not None and m.parent_id != mid:
            parent["children"].append(nodes[mid])
        else:
            roots.append(nodes[mid])
    return roots


# ===== 菜单管理 =====


@router.get("/menus", dependencies=[Depends(require_perm("menus:manage"))])
def list_menus(db: Session = Depends(get_db)):
    return {"items": _menu_tree_all(db)}


@router.post("/menus", dependencies=[Depends(require_perm("menus:manage"))])
def create_menu(body: MenuCreateIn, db: Session = Depends(get_db)):
    code = body.code.strip()
    if not CODE_RE.match(code):
        raise HTTPException(422, "编码仅允许小写字母/数字/:/_/-，且以字母开头")
    if db.scalar(select(Menu.id).where(Menu.code == code)) is not None:
        raise HTTPException(409, "菜单编码已存在")
    if body.parent_id is not None:
        parent = db.get(Menu, body.parent_id)
        if parent is None:
            raise HTTPException(404, "父菜单不存在")
        if parent.type == "button":
            raise HTTPException(422, "按钮节点下不能挂子节点")
    if body.type == "menu" and not (body.path or "").strip():
        raise HTTPException(422, "菜单类型必须提供路由 path")
    menu = Menu(
        parent_id=body.parent_id,
        code=code,
        name=body.name.strip(),
        type=body.type,
        path=(body.path or "").strip() or None,
        perm_code=(body.perm_code or "").strip() or None,
        icon=body.icon,
        sort_order=body.sort_order,
        visible=body.visible,
        status=body.status,
        remark=body.remark,
    )
    db.add(menu)
    db.commit()
    db.refresh(menu)
    return _menu_out(menu)


@router.patch("/menus/{menu_id}", dependencies=[Depends(require_perm("menus:manage"))])
def patch_menu(menu_id: int, body: MenuPatchIn, db: Session = Depends(get_db)):
    menu = db.get(Menu, menu_id)
    if menu is None:
        raise HTTPException(404, "菜单不存在")
    if body.parent_id is not None:
        if body.parent_id == menu.id:
            raise HTTPException(422, "父节点不能是自身")
        parent = db.get(Menu, body.parent_id)
        if parent is None:
            raise HTTPException(404, "父菜单不存在")
        if parent.type == "button":
            raise HTTPException(422, "按钮节点下不能挂子节点")
        menu.parent_id = body.parent_id
    for field in ("name", "path", "perm_code", "icon", "sort_order", "visible", "status", "remark"):
        val = getattr(body, field)
        if val is not None:
            setattr(menu, field, val)
    menu.updated_at = utcnow()
    db.commit()
    db.refresh(menu)
    return _menu_out(menu)


@router.delete("/menus/{menu_id}", dependencies=[Depends(require_perm("menus:manage"))])
def delete_menu(menu_id: int, db: Session = Depends(get_db)):
    menu = db.get(Menu, menu_id)
    if menu is None:
        raise HTTPException(404, "菜单不存在")
    if db.scalar(select(Menu.id).where(Menu.parent_id == menu_id)).first() is not None:
        raise HTTPException(409, "存在子节点，请先删除子节点")
    for row in db.scalars(select(RoleMenu).where(RoleMenu.menu_id == menu_id)).all():
        db.delete(row)
    db.delete(menu)
    db.commit()
    return {"ok": True}


# ===== 角色授权 =====


@router.get("/menus-flat", dependencies=[Depends(require_perm("roles:manage"))])
def menus_flat(db: Session = Depends(get_db)):
    """扁平菜单列表（角色授权勾选用）。"""
    rows = db.scalars(select(Menu).order_by(Menu.sort_order, Menu.id)).all()
    return {"items": [_menu_out(m) for m in rows]}


@router.put("/roles/{role_id}/menus", dependencies=[Depends(require_perm("roles:manage"))])
def assign_role_menus(role_id: int, body: RoleMenuIn, db: Session = Depends(get_db)):
    role = db.get(Role, role_id)
    if role is None:
        raise HTTPException(404, "角色不存在")
    if role.code == "admin":
        raise HTTPException(409, "admin 角色授权不可修改（保持最高管理员满权限）")
    from app.menus_seed import seed_menus

    menus = seed_menus(db)
    unknown = [c for c in body.menu_codes if c not in menus]
    if unknown:
        raise HTTPException(404, f"菜单不存在：{', '.join(unknown)}")
    grant_tree_to_role(db, role.id, menus, body.menu_codes)
    db.commit()
    return {"ok": True, "role": role.code, "menu_codes": sorted(body.menu_codes)}


@router.put("/roles/{role_id}/permissions", dependencies=[Depends(require_perm("roles:manage"))])
def assign_role_permissions(role_id: int, body: RolePermIn, db: Session = Depends(get_db)):
    role = db.get(Role, role_id)
    if role is None:
        raise HTTPException(404, "角色不存在")
    if role.code == "admin":
        raise HTTPException(409, "admin 角色授权不可修改（保持最高管理员满权限）")
    perms = db.scalars(select(Permission).where(Permission.code.in_(body.permission_codes))).all()
    found = {p.code for p in perms}
    unknown = [c for c in body.permission_codes if c not in found]
    if unknown:
        raise HTTPException(404, f"权限点不存在：{', '.join(unknown)}")
    for row in db.scalars(select(RolePermission).where(RolePermission.role_id == role.id)).all():
        db.delete(row)
    db.flush()
    for p in perms:
        db.add(RolePermission(role_id=role.id, permission_id=p.id))
    db.commit()
    return {"ok": True, "role": role.code, "permission_codes": sorted(found)}


@router.post("/roles", dependencies=[Depends(require_perm("roles:manage"))])
def create_role(body: RoleCreateIn, db: Session = Depends(get_db)):
    code = body.code.strip()
    if not re.match(r"^[a-z][a-z0-9_-]*$", code):
        raise HTTPException(422, "角色编码仅允许小写字母/数字/_/-，且以字母开头")
    if db.scalar(select(Role.id).where(Role.code == code)) is not None:
        raise HTTPException(409, "角色编码已存在")
    role = Role(code=code, name=body.name.strip(), description=body.description)
    db.add(role)
    db.flush()
    if body.menu_codes:
        from app.menus_seed import seed_menus

        menus = seed_menus(db)
        grant_tree_to_role(db, role.id, menus, body.menu_codes)
    if body.permission_codes:
        perms = db.scalars(
            select(Permission).where(Permission.code.in_(body.permission_codes))
        ).all()
        for p in perms:
            db.add(RolePermission(role_id=role.id, permission_id=p.id))
    db.commit()
    return {"ok": True, "code": role.code, "name": role.name}


@router.post("/permissions", dependencies=[Depends(require_perm("roles:manage"))])
def create_permission(body: PermissionCreateIn, db: Session = Depends(get_db)):
    code = body.code.strip()
    if not CODE_RE.match(code):
        raise HTTPException(422, "权限编码仅允许小写字母/数字/:/_/-，且以字母开头")
    if db.scalar(select(Permission.id).where(Permission.code == code)) is not None:
        raise HTTPException(409, "权限编码已存在")
    perm = Permission(code=code, name=body.name.strip(), description=body.description)
    db.add(perm)
    db.commit()
    db.refresh(perm)
    return {"ok": True, "code": perm.code, "name": perm.name}


@router.get("/permissions", dependencies=[Depends(require_perm("roles:manage"))])
def list_permissions(db: Session = Depends(get_db)):
    rows = db.scalars(select(Permission).order_by(Permission.id)).all()
    return {
        "items": [
            {"code": p.code, "name": p.name, "description": p.description}
            for p in rows
        ]
    }
