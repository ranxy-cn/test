from __future__ import annotations

import multiprocessing as mp
import os
import time
from typing import Any

from app.config import get_settings

"""真实 CPU 压测工具。

用途：让 Zabbix agent 采集到真实的 system.cpu.util 攀升，从而触发
「High CPU utilization (over {$CPU.UTIL.CRIT}% for 5m)」告警并自动立案。

机制：在 API 进程内按可用核数拉起死循环子进程（fork），把整机 CPU 打满。
- 子进程自带 deadline，到期自动退出；即使 API 进程被杀，子进程也会在到期后自杀，不会永久占用
- STRESS_TOOLS_ENABLED=false（默认，本地开发）时接口层直接拒绝
- duration 上限 settings.stress_max_seconds，避免误操作长时间拖垮宿主机
"""

_state: dict[str, Any] = {"processes": [], "deadline": 0.0, "started_at": 0.0, "cores": 0}


def _burn(deadline: float) -> None:
    """单核打满直到 deadline。分段自旋，减少取时间的开销。"""
    while True:
        now = time.time()
        if now >= deadline:
            return
        spin_end = min(deadline, now + 60.0)
        while time.time() < spin_end:
            pass


def _core_count() -> int:
    try:
        return max(1, len(os.sched_getaffinity(0)))
    except AttributeError:  # 非 Linux 平台兜底
        return max(1, os.cpu_count() or 1)


def _spawn(cores: int, deadline: float) -> list[mp.process.BaseProcess]:
    try:
        ctx = mp.get_context("fork")
    except ValueError:
        ctx = mp.get_context()
    procs: list[mp.process.BaseProcess] = []
    for _ in range(cores):
        p = ctx.Process(target=_burn, args=(deadline,), daemon=True)
        p.start()
        procs.append(p)
    return procs


def start(duration_seconds: int) -> dict[str, Any]:
    settings = get_settings()
    duration = max(10, min(int(duration_seconds), int(settings.stress_max_seconds)))
    stop()
    cores = _core_count()
    deadline = time.time() + duration
    procs = _spawn(cores, deadline)
    _state.update(processes=procs, deadline=deadline, started_at=time.time(), cores=cores)
    return status()


def stop() -> dict[str, Any]:
    procs = list(_state.get("processes") or [])
    for p in procs:
        try:
            p.terminate()
        except Exception:  # noqa: BLE001
            pass
    for p in procs:
        try:
            p.join(timeout=2)
        except Exception:  # noqa: BLE001
            pass
    stopped = len(procs)
    _state.update(processes=[], deadline=0.0, started_at=0.0, cores=0)
    return {"stopped": stopped, "running": False}


def status() -> dict[str, Any]:
    procs = [p for p in (_state.get("processes") or []) if p.is_alive()]
    deadline = float(_state.get("deadline") or 0.0)
    running = bool(procs) and time.time() < deadline
    if not procs and deadline:
        # 全部退出（正常到期），清空状态
        _state.update(processes=[], deadline=0.0, started_at=0.0, cores=0)
        running = False
    else:
        _state["processes"] = procs
    return {
        "running": running,
        "cores": int(_state.get("cores") or 0),
        "started_at": _state.get("started_at") or None,
        "deadline": _state.get("deadline") or None,
        "seconds_remaining": int(deadline - time.time()) if running else 0,
    }
