from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import router
from app.routers.admin_perms import router as admin_perms_router
from app.routers.admin_users import router as admin_users_router
from app.routers.auth import router as auth_router
from app.routers.ops import router as ops_router
from app import database as dbmod
from app.seed import seed_if_empty


@asynccontextmanager
async def lifespan(_: FastAPI):
    # 生产 MySQL：执行 Alembic 迁移；本地/测试 sqlite：走 create_all
    dbmod.run_migrations()
    if not get_jwt_secret_configured():
        import logging

        logging.getLogger("uvicorn.error").warning(
            "未设置 JWT_SECRET 环境变量：已使用进程级随机密钥，重启后所有登录令牌将失效"
        )
    db = dbmod.SessionLocal()
    try:
        seed_if_empty(db)
        db.commit()
    finally:
        db.close()
    yield


def get_jwt_secret_configured() -> bool:
    from app.config import get_settings

    return bool(get_settings().jwt_secret)


app = FastAPI(
    title="DevOpsAgent",
    description="智能运维数字员工。LLM 只做分析与建议，策略引擎做决定，执行器做动作，证据链做证明。",
    version="0.3.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)
app.include_router(ops_router)
app.include_router(auth_router)
app.include_router(admin_users_router)
app.include_router(admin_perms_router)
