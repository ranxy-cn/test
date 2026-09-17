from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import router
from app.routers.ops import router as ops_router
from app import database as dbmod
from app.seed import seed_if_empty


@asynccontextmanager
async def lifespan(_: FastAPI):
    dbmod.ensure_schema()
    db = dbmod.SessionLocal()
    try:
        seed_if_empty(db)
        db.commit()
    finally:
        db.close()
    yield


app = FastAPI(
    title="DevOpsAgent",
    description="智能运维数字员工。LLM 只做分析与建议，策略引擎做决定，执行器做动作，证据链做证明。",
    version="0.2.0",
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
