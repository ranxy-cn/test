"""结构化日志体系：单行日志 + 进程/线程标识 + 异常堆栈 + 业务上下文 + 系统环境。

每条日志自动携带：
- 时间戳（UTC ISO8601，毫秒）
- 级别 / logger 名 / 代码位置（文件:行号）
- 进程 PID / 线程 ID / 线程名
- 系统环境参数：服务名、主机名、部署环境（APP_ENV）
- 业务上下文：request_id（HTTP 请求）、asset_id（资产范围）、task（Celery 任务），
  由 FastAPI 中间件 / 任务入口写入 contextvar，自动附加到该范围内所有日志
- 异常记录（log.exception / exc_info=True）附带异常类型与完整堆栈

格式由环境变量 LOG_FORMAT 控制：json（默认，结构化便于采集检索）或
text（key=value 便于人眼直接 tail）。
"""
from __future__ import annotations

import contextvars
import json
import logging
import os
import sys
import threading
import traceback
from datetime import datetime, timezone

# 请求/任务范围的业务上下文（FastAPI 中间件、Celery 任务写入，自动随日志输出）
request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="")
asset_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("asset_id", default="")
task_var: contextvars.ContextVar[str] = contextvars.ContextVar("task", default="")

_SERVICE = {"name": "devops-agent"}


def _ctx() -> dict:
    out = {}
    for key, var in (("request_id", request_id_var), ("asset_id", asset_id_var), ("task", task_var)):
        v = var.get("")
        if v:
            out[key] = v
    return out


class JsonFormatter(logging.Formatter):
    """单行 JSON：结构化字段固定顺序，便于 jq / 日志平台检索。"""

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "loc": f"{record.filename}:{record.lineno}",
            "service": _SERVICE["name"],
            "host": os.uname().nodename,
            "env": os.getenv("APP_ENV", "prod"),
            "pid": record.process,
            "tid": record.thread,
            "thread": record.threadName,
            "msg": record.getMessage(),
            **_ctx(),
        }
        if record.exc_info and record.exc_info[0] is not None:
            et, ev, tb = record.exc_info
            entry["exc_type"] = et.__name__
            # 尾部 4000 字符：堆栈最深处（异常源头）优先保留
            entry["exc"] = "".join(traceback.format_exception(et, ev, tb)).strip()[-4000:]
        try:
            return json.dumps(entry, ensure_ascii=False)
        except (TypeError, ValueError):
            entry["msg"] = repr(entry["msg"])
            return json.dumps(entry, ensure_ascii=False)


class TextFormatter(logging.Formatter):
    """人读格式：tail 日志文件时无需 jq。异常只打类型行 + 源头行（完整堆栈仍可切 json）。"""

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.now(timezone.utc).strftime("%m-%d %H:%M:%S.%f")[:-3]
        base = (
            f"{ts} {record.levelname:<5} [{record.name} {record.filename}:{record.lineno} "
            f"pid={record.process} tid={record.thread} {record.threadName}]"
        )
        ctx = " ".join(f"{k}={v}" for k, v in _ctx().items())
        line = f"{base} ({ctx}) {record.getMessage()}"
        if record.exc_info and record.exc_info[0] is not None:
            lines = "".join(traceback.format_exception(*record.exc_info)).strip().splitlines()
            line += f" | {lines[0]} .. {lines[-1]}"
        return line


def setup_logging(service: str, level: int = logging.INFO) -> None:
    """进程入口调用一次：接管 root/uvicorn/celery 的 handler，并安装全局异常钩子。

    全局钩子兜底所有「未被 try/except 的异常」——主线程（sys.excepthook）与
    子线程（threading.excepthook，FastAPI 后台线程/自建线程的异常默认会被吞掉），
    确保任何线程里的未捕获异常都留下结构化堆栈。
    """
    _SERVICE["name"] = service
    fmt = os.getenv("LOG_FORMAT", "json").strip().lower()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if fmt != "text" else TextFormatter())
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers = [handler]
    # uvicorn/celery 自带 handler 移除，统一走 root（格式/上下文一致）
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "celery"):
        lg = logging.getLogger(name)
        lg.handlers.clear()
        lg.propagate = True

    def _thread_hook(args: threading.ExceptHookArgs) -> None:
        logging.getLogger("devops.uncaught").error(
            "未捕获异常（线程 %s）", args.thread.name if args.thread else "?",
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    threading.excepthook = _thread_hook

    def _sys_hook(t, v, tb) -> None:
        logging.getLogger("devops.uncaught").error("未捕获异常（主线程）", exc_info=(t, v, tb))

    sys.excepthook = _sys_hook
