"""dict entries: business dictionary for Chinese labels

新增业务字典表（dict_entries）：告警标题 / 资产 / 预案等业务编码 → 中文展示名。
写入触发器 / 资产 / 预案种子（与 app/dict_seed.py 共用，只补缺失项）。

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-20
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.orm import Session

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        # 本地/测试 sqlite 不跑迁移（create_all + seed_if_empty 覆盖）
        return

    # 幂等：表可能已按 ddl/0004_dict_entries.sql 手工建过
    existing_tables = set(sa.inspect(bind).get_table_names())
    if "dict_entries" not in existing_tables:
        op.create_table(
            "dict_entries",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False, comment="自增主键"),
            sa.Column("dict_type", sa.String(length=32), nullable=False, comment="字典类型：trigger 告警标题 / asset 资产 / action 预案"),
            sa.Column("code", sa.String(length=128), nullable=False, comment="业务编码（原值，如 CPU usage > 85% for 5 minutes）"),
            sa.Column("label", sa.String(length=128), nullable=False, comment="中文展示名"),
            sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0", comment="排序值（小的在前）"),
            sa.Column("status", sa.String(length=16), nullable=False, server_default="enabled", comment="状态：enabled 启用 / disabled 停用"),
            sa.Column("remark", sa.String(length=256), nullable=False, server_default="", comment="备注"),
            sa.Column("created_at", sa.DateTime(), nullable=False, comment="创建时间（UTC）"),
            sa.Column("updated_at", sa.DateTime(), nullable=False, comment="更新时间（UTC）"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("dict_type", "code", name="uq_dict_type_code"),
            comment="业务字典（枚举中文名映射）",
        )
        op.create_index(op.f("ix_dict_entries_dict_type"), "dict_entries", ["dict_type"], unique=False)

    # ===== 种子（与 dict_seed 同源，只补缺失项）=====
    from app.dict_seed import DICT_SEED
    from app.models import DictEntry

    db = Session(bind=bind)
    try:
        existing = {(d.dict_type, d.code) for d in db.scalars(sa.select(DictEntry)).all()}
        for dict_type, code, label, sort_order, remark in DICT_SEED:
            if (dict_type, code) in existing:
                continue
            db.add(
                DictEntry(
                    dict_type=dict_type,
                    code=code,
                    label=label,
                    sort_order=sort_order,
                    remark=remark,
                )
            )
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
    op.drop_index(op.f("ix_dict_entries_dict_type"), table_name="dict_entries")
    op.drop_table("dict_entries")
