from __future__ import annotations

import multiprocessing as mp
import os
import shlex
import time
from typing import Any

import paramiko

from app.config import get_settings
from app.models import Asset

"""真实 CPU / 内存压测工具。

用途：让 Zabbix agent 采集到真实的 system.cpu.util 攀升 / 内存利用率攀升，从而触发
「High CPU utilization (over {$CPU.UTIL.CRIT}% for 5m)」/「High memory utilization
(>{$MEMORY.UTIL.MAX}% for 5m)」告警并推送 DevOps。

机制：
- CPU：在 API 进程内按可用核数拉起死循环子进程（fork），把整机 CPU 打满
- 内存：拉起单个子进程按需分配并逐页触达（4K page touch），把整机内存利用率顶到目标百分比
- 子进程自带 deadline，到期自动退出；即使 API 进程被杀，子进程也会在到期后自杀，不会永久占用
- STRESS_TOOLS_ENABLED=false（默认，本地开发）时接口层直接拒绝
- duration 上限 settings.stress_max_seconds，避免误操作长时间拖垮宿主机
- 内存压测只对 Linux 生效（读 /proc/meminfo）；并保留安全余量，避免触发 OOM
- 远程压测：按资产（子机）SSH 上机执行，机制同上但以 shell 进程跑在目标机；
  timeout 兜底到期自灭 + 标记串 pkill 停止 + EXIT trap 清理临时文件
"""

_state: dict[str, Any] = {"processes": [], "deadline": 0.0, "started_at": 0.0, "cores": 0}
_mem_state: dict[str, Any] = {
    "process": None,
    "deadline": 0.0,
    "started_at": 0.0,
    "target_percent": 0,
    "allocated_mb": 0,
}


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


def _mem_read() -> tuple[int, int] | None:
    """读 /proc/meminfo，返回 (MemTotal_KB, MemAvailable_KB)；非 Linux 或解析失败返回 None。"""
    try:
        info: dict[str, int] = {}
        with open("/proc/meminfo", encoding="ascii") as fh:
            for line in fh:
                key, _, rest = line.partition(":")
                value = rest.strip().split(" ", 1)[0]
                if value.isdigit():
                    info[key] = int(value)
        total = info.get("MemTotal")
        avail = info.get("MemAvailable")
        if not total or avail is None:
            return None
        return total, avail
    except OSError:
        return None


def _mem_burn(deadline: float, target_percent: int) -> None:
    """子进程：逐页分配并触达内存，把系统内存利用率顶到 target_percent，直到 deadline。

    每轮复核 /proc/meminfo，按缺口以 64MB 为单位增量分配；超配时回收一块。
    安全余量：MemAvailable 永不低于 max(5% MemTotal, 256MB)，避免触发 OOM。
    """
    target_percent = max(1, min(target_percent, 95))
    chunks: list[bytearray] = []
    try:
        while time.time() < deadline:
            reading = _mem_read()
            if reading is None:  # 非 Linux（如本地 mac 调试）：退化为一次性分配 256MB 占位
                if not chunks:
                    buf = bytearray(256 * 1024 * 1024)
                    buf[::4096] = b"\x01" * (len(buf) // 4096)
                    chunks.append(buf)
                time.sleep(1.0)
                continue
            total_kb, avail_kb = reading
            floor_kb = max(total_kb * 5 // 100, 256 * 1024)
            want_avail_kb = max(total_kb * (100 - target_percent) // 100, floor_kb)
            deficit_kb = avail_kb - want_avail_kb
            if deficit_kb > 0:
                step_kb = min(deficit_kb, 64 * 1024)
                buf = bytearray(step_kb * 1024)
                buf[::4096] = b"\x01" * (len(buf) // 4096)
                chunks.append(buf)
            elif deficit_kb < -(64 * 1024) and len(chunks) > 1:
                chunks.pop()
            time.sleep(0.2)
    finally:
        chunks.clear()


def mem_start(target_percent: int, duration_seconds: int) -> dict[str, Any]:
    settings = get_settings()
    target = max(50, min(int(target_percent), 95))
    duration = max(10, min(int(duration_seconds), int(settings.stress_max_seconds)))
    mem_stop()
    try:
        ctx = mp.get_context("fork")
    except ValueError:
        ctx = mp.get_context()
    deadline = time.time() + duration
    p = ctx.Process(target=_mem_burn, args=(deadline, target), daemon=True)
    p.start()
    _mem_state.update(
        process=p, deadline=deadline, started_at=time.time(), target_percent=target, allocated_mb=0
    )
    return mem_status()


def mem_stop() -> dict[str, Any]:
    p = _mem_state.get("process")
    stopped = 0
    if p is not None:
        try:
            p.terminate()
            stopped = 1
        except Exception:  # noqa: BLE001
            pass
        try:
            p.join(timeout=5)
        except Exception:  # noqa: BLE001
            pass
    _mem_state.update(process=None, deadline=0.0, started_at=0.0, target_percent=0, allocated_mb=0)
    return {"stopped": stopped, "running": False}


def mem_status() -> dict[str, Any]:
    p = _mem_state.get("process")
    deadline = float(_mem_state.get("deadline") or 0.0)
    alive = p is not None and p.is_alive()
    running = alive and time.time() < deadline
    if p is not None and not alive:
        _mem_state.update(process=None, deadline=0.0, started_at=0.0, target_percent=0, allocated_mb=0)
    reading = _mem_read()
    memory: dict[str, Any] | None = None
    if reading:
        total_kb, avail_kb = reading
        memory = {
            "total_mb": total_kb // 1024,
            "available_mb": avail_kb // 1024,
            "used_percent": round((total_kb - avail_kb) * 100.0 / total_kb, 1),
        }
    return {
        "running": running,
        "target_percent": int(_mem_state.get("target_percent") or 0),
        "started_at": _mem_state.get("started_at") or None,
        "deadline": _mem_state.get("deadline") or None,
        "seconds_remaining": int(deadline - time.time()) if running else 0,
        "memory": memory,
    }


# ---------------------------------------------------------------------------
# 远程压测：SSH 到指定资产（子机）上机执行
# ---------------------------------------------------------------------------

# 进程标记串：pkill -f 匹配用。模式里用 [x] 方括号技巧，避免 pkill 自身命令行被匹配
_MARK_CPU = "__devops_stress_cpu__"
_MARK_MEM_FILE = "__devops_stress_mem__"  # /dev/shm/.__devops_stress_mem__
_CPU_PKILL = "__devops_stress_cp[u]__"
_MEM_PKILL = "__devops_stress_me[m]__"

# asset_id -> {asset_id, hostname, ip, kind: cpu|mem, cores/target_percent, started_at, deadline}
_remote_state: dict[str, dict[str, Any]] = {}


def resolve_asset_target(asset: Asset) -> dict[str, Any] | None:
    """从资产 extra 解析 SSH 目标；无 SSH 信息返回 None（与诊断采集同一套约定）。

    自动纳管的子机密码“即用即弃、不落库”，纳管时已把巡检公钥下发到子机 authorized_keys，
    因此附上巡检私钥路径，供密码为空时回退免密登录（与巡检/诊断口径一致）。
    """
    from app.services.inspector import INSPECT_KEY_PATH

    extra = asset.extra or {}
    provision = extra.get("provision") or {}
    ip = provision.get("ip") or extra.get("ssh_host")
    if not ip:
        return None
    return {
        "ip": ip,
        "port": int(provision.get("port") or 22),
        "username": provision.get("username") or "root",
        "password": provision.get("password") or get_settings().diag_ssh_password,
        "key_path": INSPECT_KEY_PATH,
    }


def _connect(target: dict[str, Any]) -> paramiko.SSHClient:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    password = target.get("password") or None
    # 巡检私钥 + 密码都交给 paramiko（先公钥、后密码，任一成功即可）：
    # 纳管子机密码“即用即弃”只有私钥可用；手动登记资产可能只有密码。
    # 不能“密码为空才用私钥”——全局 DIAG_SSH_PASSWORD 兜底会让空密码资产
    # 拿着无关密码去认证，反而挤掉私钥通道（表现为 Authentication failed）。
    pkey = None
    key_path = target.get("key_path") or ""
    if key_path:
        from app.services.inspector import _load_private_key

        try:
            pkey = _load_private_key(key_path)
        except Exception:  # noqa: BLE001 私钥损坏/缺失时不阻塞密码认证
            pkey = None
    try:
        client.connect(
            target["ip"],
            port=int(target.get("port") or 22),
            username=target.get("username") or "root",
            password=password,
            pkey=pkey,
            timeout=8.0,
            banner_timeout=8.0,
            auth_timeout=8.0,
            allow_agent=False,
            look_for_keys=False,
        )
    except Exception as exc:  # noqa: BLE001
        try:
            client.close()
        except Exception:  # noqa: BLE001
            pass
        tried = "私钥+密码" if (pkey and password) else ("私钥" if pkey else ("密码" if password else "无可用凭据"))
        raise ValueError(f"SSH 连接失败 {target.get('username')}@{target.get('ip')}：{exc}（已尝试{tried}认证）") from exc
    return client


def _exec(client: paramiko.SSHClient, cmd: str, timeout: int = 15) -> tuple[int, str, str]:
    _, stdout, stderr = client.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode("utf-8", errors="replace").strip()
    err = stderr.read().decode("utf-8", errors="replace").strip()
    code = stdout.channel.recv_exit_status()
    return code, out, err


def _kill_markers(client: paramiko.SSHClient) -> None:
    """清理目标机上本工具遗留的压测进程与临时文件。"""
    _exec(
        client,
        f"pkill -f {shlex.quote(_CPU_PKILL)} 2>/dev/null;"
        f" pkill -f {shlex.quote(_MEM_PKILL)} 2>/dev/null;"
        f" rm -f /dev/shm/.{_MARK_MEM_FILE} 2>/dev/null; true",
    )


def _remote_meta(asset: Asset, target: dict[str, Any], kind: str, deadline: float, **extra: Any) -> dict[str, Any]:
    return {
        "asset_id": asset.id,
        "hostname": asset.hostname,
        "ip": target["ip"],
        "kind": kind,
        "started_at": time.time(),
        "deadline": deadline,
        **extra,
    }


def remote_cpu_start(asset: Asset, duration_seconds: int) -> dict[str, Any]:
    """在资产所在机器上把全部 CPU 核打满（每核一个 nohup 自旋进程，timeout 兜底自灭）。"""
    settings = get_settings()
    duration = max(10, min(int(duration_seconds), int(settings.stress_max_seconds)))
    target = resolve_asset_target(asset)
    if target is None:
        raise ValueError(f"资产 {asset.hostname} 未录入 SSH 信息（extra.provision），无法远程压测")
    client = _connect(target)
    try:
        _, out, _ = _exec(client, "nproc")
        cores = int(out) if out.isdigit() and int(out) > 0 else 1
        # 先清掉该机上可能残留的旧压测进程
        _kill_markers(client)
        # 每核一条后台自旋进程，标记符作为 $0 放在脚本引号外（ps 可见，供 pkill 清理）。
        # 两个历史静默失败点（stderr 均被 nohup 吞掉，表象是"启动成功"但什么都没跑）：
        # 1) 各段以 & 结尾时不能用 "; " 拼接（产生非法的 "&;"，sh 直接语法报错）；
        # 2) 标记符不能写在 done 之后（裸单词是语法错误，自旋进程瞬间退出）。
        spin = f"timeout {duration} sh -c 'while :; do :; done' {_MARK_CPU} >/dev/null 2>&1 &"
        inner = " ".join([spin] * cores) + " wait"
        _exec(client, f"nohup sh -c {shlex.quote(inner)} >/dev/null 2>&1 & sleep 0.5")
    finally:
        client.close()
    deadline = time.time() + duration
    meta = _remote_meta(asset, target, "cpu", deadline, cores=cores)
    _remote_state[asset.id] = meta
    return {**meta, "running": True, "seconds_remaining": duration}


def remote_mem_start(asset: Asset, target_percent: int, duration_seconds: int) -> dict[str, Any]:
    """在资产所在机器上用 tmpfs 文件把内存利用率顶到目标百分比（EXIT trap + rm 兜底清理）。"""
    settings = get_settings()
    target = max(50, min(int(target_percent), 95))
    duration = max(10, min(int(duration_seconds), int(settings.stress_max_seconds)))
    ssh = resolve_asset_target(asset)
    if ssh is None:
        raise ValueError(f"资产 {asset.hostname} 未录入 SSH 信息（extra.provision），无法远程压测")
    client = _connect(ssh)
    try:
        _, out, _ = _exec(client, "awk '$1==\"MemTotal:\" || $1==\"MemAvailable:\" {print $2}' /proc/meminfo")
        parts = [line for line in out.splitlines() if line.strip().isdigit()]
        if len(parts) < 2:
            raise ValueError("无法读取目标机 /proc/meminfo（仅支持 Linux）")
        total_kb, avail_kb = int(parts[0]), int(parts[1])
        floor_kb = max(total_kb * 5 // 100, 256 * 1024)
        want_avail_kb = max(total_kb * (100 - target) // 100, floor_kb)
        mb = (avail_kb - want_avail_kb) // 1024
        if mb < 64:
            raise ValueError(f"目标 {target}% 内存余量不足（可压空间 {mb}MB < 64MB），请降低目标百分比")
        _kill_markers(client)
        file_path = f"/dev/shm/.{_MARK_MEM_FILE}"
        inner = (
            f"trap 'rm -f {file_path}' EXIT; "
            f"dd if=/dev/zero of={file_path} bs=1M count={mb} 2>/dev/null; "
            f"sleep {duration + 5}"
        )
        _exec(client, f"nohup timeout {duration + 30} sh -c {shlex.quote(inner)} >/dev/null 2>&1 & sleep 0.5")
    finally:
        client.close()
    deadline = time.time() + duration
    meta = _remote_meta(asset, ssh, "mem", deadline, target_percent=target, target_mb=mb)
    _remote_state[asset.id] = meta
    return {**meta, "running": True, "seconds_remaining": duration}


def remote_stop(asset_id: str) -> dict[str, Any]:
    """停止资产上的远程压测：pkill 标记进程 + 删除 tmpfs 文件。"""
    meta = _remote_state.get(asset_id) or {}
    _remote_state.pop(asset_id, None)
    return {"stopped": True, "asset_id": asset_id, "target": meta.get("hostname") or asset_id}


def remote_stop_via_ssh(asset: Asset) -> dict[str, Any]:
    """SSH 上机清理压测进程（用于停止按钮）。"""
    ssh = resolve_asset_target(asset)
    if ssh is None:
        raise ValueError(f"资产 {asset.hostname} 未录入 SSH 信息（extra.provision），无法远程停止")
    client = _connect(ssh)
    try:
        _kill_markers(client)
    finally:
        client.close()
    return remote_stop(asset.id)


def remote_status() -> list[dict[str, Any]]:
    """远程压测状态列表（按 deadline 惰性清理过期条目）。"""
    now = time.time()
    for asset_id in [k for k, v in _remote_state.items() if now > float(v.get("deadline") or 0)]:
        _remote_state.pop(asset_id, None)
    return [
        {**v, "running": True, "seconds_remaining": int(float(v["deadline"]) - now)}
        for v in sorted(_remote_state.values(), key=lambda x: x.get("hostname") or "")
    ]
