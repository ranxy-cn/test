"""dynamic menu rbac: menus + role_menus + seed

新增菜单/按钮资源树（menus）与角色菜单授权表（role_menus），
写入 2 个新权限点、菜单树种子与三个内置角色的初始授权。
列定义与 app/models.py 一致（均带 comment）；种子与 app/menus_seed.py 共用，保证一致。

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-20
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.orm import Session

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        # 本地/测试 sqlite 不跑迁移（create_all + seed_if_empty 覆盖）
        return

    op.create_table(
        "menus",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False, comment="自增主键"),
        sa.Column(
            "parent_id",
            sa.Integer(),
            sa.ForeignKey("menus.id", ondelete="CASCADE"),
            nullable=True,
            comment="父菜单 ID（空=顶级）",
        ),
        sa.Column("code", sa.String(length=64), nullable=False, comment="资源唯一编码，如 menu:tickets / btn:ticket-approve"),
        sa.Column("name", sa.String(length=64), nullable=False, comment="显示名称"),
        sa.Column("type", sa.String(length=16), nullable=False, comment="类型：dir 目录 / menu 菜单 / button 按钮"),
        sa.Column("path", sa.String(length=128), nullable=True, comment="前端路由路径（menu 型必填）"),
        sa.Column("perm_code", sa.String(length=64), nullable=True, comment="所需权限点编码（关联 permissions.code）"),
        sa.Column("icon", sa.String(length=64), nullable=False, server_default="", comment="图标名称（前端图标映射）"),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0", comment="排序值（小的在前）"),
        sa.Column("visible", sa.Boolean(), nullable=False, server_default=sa.text("1"), comment="是否在侧边栏显示：1 显示 / 0 隐藏"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="enabled", comment="状态：enabled 启用 / disabled 停用"),
        sa.Column("remark", sa.String(length=256), nullable=False, server_default="", comment="备注"),
        sa.Column("created_at", sa.DateTime(), nullable=False, comment="创建时间（UTC）"),
        sa.Column("updated_at", sa.DateTime(), nullable=False, comment="更新时间（UTC）"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="ix_menus_code"),
        comment="菜单/路由/按钮资源树",
    )
    op.create_index(op.f("ix_menus_code"), "menus", ["code"], unique=True)

    op.create_table(
        "role_menus",
        sa.Column(
            "role_id",
            sa.Integer(),
            sa.ForeignKey("roles.id", ondelete="CASCADE"),
            nullable=False,
            comment="角色 ID（fk → roles.id，级联删除）",
        ),
        sa.Column(
            "menu_id",
            sa.Integer(),
            sa.ForeignKey("menus.id", ondelete="CASCADE"),
            nullable=False,
            comment="菜单 ID（fk → menus.id，级联删除）",
        ),
        sa.PrimaryKeyConstraint("role_id", "menu_id"),
        comment="角色-菜单授权关联",
    )

    # ===== 种子：新权限点 + 菜单树 + 内置角色授权（与 seed_menus 同源） =====
    from app.models import Permission, Role
    from app.menus_seed import grant_tree_to_role, role_default_menu_codes, seed_menus

    db = Session(bind=bind)
    try:
        for code, name, desc in [
            ("roles:manage", "角色权限管理", "角色增删、菜单授权与权限点分配"),
            ("menus:manage", "菜单管理", "菜单/路由/按钮资源树的维护"),
        ]:
            if db.scalar(sa.select(Permission.id).where(Permission.code == code)) is None:
                db.add(Permission(code=code, name=name, description=desc))
        db.flush()

        menus = seed_menus(db)
        roles = {r.code: r for r in db.scalars(sa.select(Role)).all()}
        for role_code in ("admin", "operator", "viewer"):
            role = roles.get(role_code)
            if role is not None:
                grant_tree_to_role(db, role.id, menus, role_default_menu_codes(role_code))
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        return
    op.drop_table("role_menus")
    op.drop_index(op.f("ix_menus_code"), table_name="menus")
    op.drop_table("menus")
    op.execute(
        "DELETE FROM role_permissions WHERE permission_id IN "
        "(SELECT id FROM permissions WHERE code IN ('roles:manage','menus:manage'))"
    )
    op.execute("DELETE FROM permissions WHERE code IN ('roles:manage','menus:manage')")
