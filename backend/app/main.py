from contextlib import asynccontextmanager
import logging
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api import router
from app.logging_setup import request_id_var, setup_logging

setup_logging("devops-api")
log = logging.getLogger("devops.http")
from app.routers.admin_perms import router as admin_perms_router
from app.routers.admin_users import router as admin_users_router
from app.routers.auth import router as auth_router
from app.routers.dict import router as dict_router
from app.routers.ops import router as ops_router
from app.routers.dashboard import router as dashboard_router
from app.routers.knowledge import router as knowledge_router
from app.routers.system_metrics import router as system_metrics_router
from app.routers.agent_api import router as agent_api_router
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
    # 系统资源实时采样：启动即记录（不回填历史），7 天保留
    from app.services import system_live

    system_live.start_sampler()
    try:
        yield
    finally:
        system_live.stop_sampler()


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


@app.middleware("http")
async def request_logging_middleware(request: Request, call_next):
    """每请求注入 request_id 并输出结构化访问日志；未捕获异常记录完整堆栈。

    agent 高频上报/拉配置路径（每 5s 一次）跳过 INFO 访问日志，异常仍记录。
    """
    rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
    token = request_id_var.set(rid)
    client = request.client.host if request.client else "-"
    t0 = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        log.exception(
            "请求未捕获异常 %s %s client=%s", request.method, request.url.path, client
        )
        request_id_var.reset(token)
        raise
    request_id_var.reset(token)
    cost_ms = int((time.perf_counter() - t0) * 1000)
    if not request.url.path.startswith("/api/v1/agent/"):
        log.info(
            "http %s %s -> %s %dms client=%s",
            request.method, request.url.path, response.status_code, cost_ms, client,
        )
    response.headers["X-Request-ID"] = rid
    return response


app.include_router(router)
app.include_router(ops_router)
app.include_router(auth_router)
app.include_router(admin_users_router)
app.include_router(admin_perms_router)
app.include_router(dict_router)
app.include_router(dashboard_router)
app.include_router(knowledge_router)
app.include_router(system_metrics_router)
app.include_router(agent_api_router)
