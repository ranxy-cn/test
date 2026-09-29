"""业务字典查询：给前端提供 code → 中文名 的枚举映射。

登录用户均可读（列表展示用），维护暂由种子/DBA 负责。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import DictEntry
from app.routers.deps import get_current_user

router = APIRouter(prefix="/api/v1", tags=["dict"])


class DictItemOut(BaseModel):
    dict_type: str
    code: str
    label: str
    sort_order: int


@router.get("/dict", response_model=list[DictItemOut])
def list_dict(
    dict_type: str | None = None,
    _current=Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[DictItemOut]:
    stmt = select(DictEntry).where(DictEntry.status == "enabled")
    if dict_type:
        stmt = stmt.where(DictEntry.dict_type == dict_type)
    stmt = stmt.order_by(DictEntry.dict_type, DictEntry.sort_order, DictEntry.id)
    rows = db.scalars(stmt).all()
    return [
        DictItemOut(
            dict_type=r.dict_type,
            code=r.code,
            label=r.label,
            sort_order=r.sort_order,
        )
        for r in rows
    ]
