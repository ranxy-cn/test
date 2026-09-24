"""资产实时巡检：SSH 登录目标机执行只读命令，采集 top 进程排行 / 内存 / 磁盘 / 负载。

优先使用平台内置巡检私钥（免密）；密码仅用于当次连接（由前端每次请求携带），不落库、不写日志。
另提供 collect_sysinfo：一次性采集服务器全景详情（系统/硬件/内存/磁盘/网络/进程）。
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import paramiko

COLLECT_TIMEOUT = 8.0

# 容器内巡检私钥挂载路径（docker-compose: ./keys:/app/keys:ro）
INSPECT_KEY_PATH = "/app/keys/inspect_key"

# 一条 sh -c 完成全部采集，用 marker 切分各段输出，减少 SSH 往返
CMD = r"""
echo @@LOAD@@; cat /proc/loadavg; echo @@CORES@@; nproc; echo @@UPTIME@@; cat /proc/uptime;
echo @@MEM@@; free -m | sed -n '2p'; echo @@DISK@@; df -P / | tail -1; echo @@TOPCPU@@;
ps -eo pid,user,pcpu,pmem,comm,args --sort=-pcpu --no-headers | head -15; echo @@TOPMEM@@;
ps -eo pid,user,pcpu,pmem,comm,args --sort=-pmem --no-headers | head -15; echo @@END@@
""".replace("\n", " ")


def _parse_ps(block: str) -> list[dict[str, Any]]:
    rows = []
    for line in block.strip().splitlines():
        parts = line.split(None, 6)
        if len(parts) < 6:
            continue
        try:
            rows.append(
                {
                    "pid": int(parts[0]),
                    "user": parts[1],
                    "cpu": float(parts[2]),
                    "mem": float(parts[3]),
                    "comm": parts[4],
                    "args": parts[5] if len(parts) > 5 else parts[4],
                }
            )
        except ValueError:
            continue
    return rows


def _load_private_key(key_path: str):
    """按类型加载私钥（支持 OpenSSH/Ed25519/RSA）。失败返回 None。"""
    p = Path(key_path)
    if not p.is_file():
        return None
    errors: list[Exception] = []
    loaders = [getattr(paramiko, name, None) for name in ("Ed25519Key", "RSAKey", "ECDSAKey")]
    for loader in [l for l in loaders if l is not None]:
        try:
            return loader.from_private_key_file(str(p))
        except paramiko.SSHException as exc:  # 不同格式逐个尝试
            errors.append(exc)
            continue
    raise paramiko.SSHException(f"无法加载巡检私钥 {key_path}: {errors[-1] if errors else '未知错误'}")


def inspect_host(ip: str, port: int, username: str, password: str, key_path: str = "") -> dict[str, Any]:
    """SSH 采集并解析。私钥与密码同时提供（先公钥后密码，任一成功即可）。任何失败抛异常，由 API 层转成错误响应。"""
    # 回归：不能因密码非空丢弃私钥——空密码会被全局兜底密码填充，挤掉私钥必然认证失败
    pkey = _load_private_key(key_path) if key_path else None
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            ip,
            port=port,
            username=username,
            password=password or None,
            pkey=pkey,
            timeout=6.0,
            banner_timeout=6.0,
            auth_timeout=6.0,
            allow_agent=False,
            look_for_keys=False,
        )
        _, stdout, _ = client.exec_command(CMD, timeout=COLLECT_TIMEOUT)
        raw = stdout.read().decode("utf-8", errors="replace")
    finally:
        client.close()

    sections = _split_sections(raw)

    # loadavg: 0.10 0.08 0.09 1/233 12345
    load_parts = (sections.get("LOAD") or "").split()
    load = {
        "load1": float(load_parts[0]) if len(load_parts) > 0 else None,
        "load5": float(load_parts[1]) if len(load_parts) > 1 else None,
        "load15": float(load_parts[2]) if len(load_parts) > 2 else None,
        "cores": int((sections.get("CORES") or "0").strip() or 0) or None,
    }
    try:
        uptime_s = float((sections.get("UPTIME") or "").split()[0])
    except (ValueError, IndexError):
        uptime_s = None

    # free -m 第二行: Mem: 3800 1200 2100 80 500 2500
    mem = {}
    mem_parts = (sections.get("MEM") or "").split()
    if len(mem_parts) >= 3:
        total, used = float(mem_parts[1]), float(mem_parts[2])
        mem = {"total_mb": int(total), "used_mb": int(used), "pct": round(used / total * 100, 1) if total else None}

    # df -P /: /dev/vda1 41203416 6034084 33051268 16% /
    disk = {}
    disk_parts = (sections.get("DISK") or "").split()
    if len(disk_parts) >= 5:
        disk = {"fs": disk_parts[0], "pct": float(disk_parts[4].rstrip("%")), "mount": disk_parts[5] if len(disk_parts) > 5 else "/"}

    return {
        "ip": ip,
        "load": load,
        "uptime_seconds": uptime_s,
        "mem": mem,
        "disk": disk,
        "top_cpu": _parse_ps(sections.get("TOPCPU", "")),
        "top_mem": _parse_ps(sections.get("TOPMEM", "")),
    }


# ---------------------------------------------------------------------------
# 服务器详细信息（一次性全景采集：系统 / 硬件 / 内存 / 磁盘 / 网络 / 进程）
# ---------------------------------------------------------------------------

SYSINFO_CMD = r"""
echo @@BASIC@@; hostname 2>/dev/null; uname -r; uname -m; sed -n 's/^PRETTY_NAME="\{0,1\}\(.*\)"\{0,1\}$/\1/p' /etc/os-release 2>/dev/null; date '+%Y-%m-%d %H:%M:%S %z'; readlink /etc/localtime 2>/dev/null | sed 's|.*zoneinfo/||'; uptime -s 2>/dev/null;
echo @@VIRT@@; systemd-detect-virt 2>/dev/null; grep -c hypervisor /proc/cpuinfo 2>/dev/null; cat /sys/class/dmi/id/product_name 2>/dev/null; cat /sys/class/dmi/id/sys_vendor 2>/dev/null;
echo @@CPU@@; lscpu 2>/dev/null; grep -m1 'model name' /proc/cpuinfo 2>/dev/null; nproc 2>/dev/null;
echo @@MEMINFO@@; grep -E '^(MemTotal|MemFree|MemAvailable|Buffers|Cached|SwapTotal|SwapFree|SwapCached|Dirty|Writeback|Active|Inactive|Slab):' /proc/meminfo 2>/dev/null; free -m 2>/dev/null | sed -n '2,3p';
echo @@DISKS@@; df -hP 2>/dev/null;
echo @@INODES@@; df -iP 2>/dev/null;
echo @@BLOCKS@@; lsblk -P -o NAME,SIZE,TYPE,FSTYPE,MOUNTPOINT,MODEL 2>/dev/null;
echo @@IFACES@@; ip -brief address 2>/dev/null || ip -o -4 addr show 2>/dev/null;
echo @@ROUTE@@; ip route 2>/dev/null;
echo @@TCP@@; ss -s 2>/dev/null | head -n 6;
echo @@LISTEN@@; ss -tulnpH 2>/dev/null | head -n 40;
echo @@PROCS@@; ps -eo pid --no-headers 2>/dev/null | wc -l; ps -eo stat --no-headers 2>/dev/null | grep -c '^R'; ps -eo stat --no-headers 2>/dev/null | grep -c '^Z';
echo @@USERS@@; who 2>/dev/null;
echo @@AGENT@@; ps -eo args 2>/dev/null | grep -E 'zabbix[_-]agentd?' | grep -v grep | head -n 3; zabbix_agentd --version 2>/dev/null | head -n 1; zabbix_agent -V 2>/dev/null | head -n 1;
echo @@END@@
""".replace("\n", " ")

VIRT_LABELS = {
    "kvm": "KVM 虚拟机",
    "vmware": "VMware 虚拟机",
    "microsoft": "Hyper-V 虚拟机",
    "oracle": "VirtualBox 虚拟机",
    "xen": "Xen 虚拟机",
    "xen-dom0": "Xen Dom0",
    "xen-domu": "Xen DomU",
    "amazon_ec2": "AWS EC2",
    "qemu": "QEMU 虚拟机",
    "docker": "Docker 容器",
    "lxc": "LXC 容器",
    "lxc-libvirt": "LXC 容器",
    "systemd-nspawn": "systemd-nspawn 容器",
    "none": "物理机 / 独立环境",
    "0": "物理机 / 独立环境",
    "1": "虚拟机（未识别类型）",
}


def _split_sections(raw: str) -> dict[str, str]:
    """按 @@MARK@@ 行切分 SSH 输出为 dict。"""
    sections: dict[str, str] = {}
    marker = ""
    buf: list[str] = []
    for line in raw.splitlines():
        if line.startswith("@@") and line.endswith("@@") and len(line) < 40:
            if marker:
                sections[marker] = "\n".join(buf)
            marker = line.strip("@")
            buf = []
        elif marker:
            buf.append(line)
    if marker:
        sections[marker] = "\n".join(buf)
    return sections


def _parse_lscpu(block: str) -> dict[str, Any]:
    """解析 lscpu 输出；lscpu 不可用时回退 /proc/cpuinfo 与 nproc。"""
    info: dict[str, Any] = {}
    key_map = {
        "architecture": "arch",
        "model name": "model",
        "vendor id": "vendor",
        "cpu(s)": "cpus",
        "socket(s)": "sockets",
        "core(s) per socket": "cores_per_socket",
        "thread(s) per core": "threads_per_core",
        "cpu mhz": "mhz",
        "cpu max mhz": "max_mhz",
        "l3 cache": "l3_cache",
        "numa node(s)": "numa_nodes",
    }
    for line in block.splitlines():
        key, _, val = line.partition(":")
        target = key_map.get(key.strip().lower())
        if target:
            info[target] = val.strip()

    # 回退：容器/精简系统没有 lscpu 时，取 grep model name 行与 nproc 行
    if "model" not in info:
        for line in block.splitlines():
            if "model name" in line.lower() and ":" in line:
                info["model"] = line.split(":", 1)[1].strip()
                break
    if "cpus" not in info:
        for line in block.splitlines():
            if line.strip().isdigit():
                info["cpus"] = line.strip()
                break

    cores_total = info.get("cpus")
    try:
        sockets = int(info.get("sockets") or 0)
        per_socket = int(info.get("cores_per_socket") or 0)
        threads = int(info.get("threads_per_core") or 1) or 1
    except ValueError:
        sockets = per_socket = 0
        threads = 1
    physical = sockets * per_socket if sockets and per_socket else None
    info["cores_total"] = cores_total
    info["cores_physical"] = physical
    info["cores_logical"] = cores_total
    if physical:
        info["hyperthreading"] = threads > 1
    return info


def _parse_meminfo(block: str) -> dict[str, int]:
    """/proc/meminfo 行（MemTotal:  3800 kB）→ {key: kb}。"""
    info: dict[str, int] = {}
    for line in block.splitlines():
        key, _, rest = line.partition(":")
        val = rest.strip().split()
        if val and val[0].isdigit():
            info[key.strip()] = int(val[0])
    return info


def _parse_free_m(block: str) -> dict[str, Any]:
    """free -m 第 2/3 行（Mem:/Swap:）→ {total_mb, used_mb, free_mb, ...}。"""
    out: dict[str, Any] = {}
    for line in block.splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[0] in ("Mem:", "Swap:"):
            key = "mem" if parts[0] == "Mem:" else "swap"
            try:
                vals = [int(x) for x in parts[1:4]]
            except ValueError:
                continue
            total, used, free = (vals + [0, 0, 0])[:3]
            out[key] = {"total_mb": total, "used_mb": used, "free_mb": free}
            if key == "mem" and total:
                out["mem"]["pct"] = round(used / total * 100, 1)
            if key == "swap" and total:
                out["swap"]["pct"] = round(used / total * 100, 1)
    return out


def _parse_df(block: str, human: bool) -> list[dict[str, Any]]:
    """df -hP / df -iP 输出 → 行列表（跳过表头）。"""
    rows = []
    lines = [l for l in block.splitlines() if l.strip()]
    for line in lines[1:]:  # 跳过 Filesystem 表头
        parts = line.split()
        if human:
            if len(parts) < 6:
                continue
            fs, size, used, avail, pct = parts[0], parts[1], parts[2], parts[3], parts[4]
            mount = " ".join(parts[5:])
            pct_val = float(pct.rstrip("%")) if pct.rstrip("%").replace(".", "").isdigit() else None
            rows.append({"fs": fs, "size": size, "used": used, "avail": avail, "pct": pct_val, "mount": mount})
        else:
            if len(parts) < 6:
                continue
            try:
                rows.append(
                    {
                        "fs": parts[0],
                        "inodes": int(parts[1]),
                        "iused": int(parts[2]),
                        "ifree": int(parts[3]),
                        "ipct": float(parts[4].rstrip("%")) if parts[4].rstrip("%").replace(".", "").isdigit() else None,
                        "mount": " ".join(parts[5:]),
                    }
                )
            except ValueError:
                continue
    return rows


def _merge_disks(disks: list[dict[str, Any]], inodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """df -hP 与 df -iP 按 (fs, mount) 合并出 inode 使用率。"""
    inode_map = {(r["fs"], r["mount"]): r.get("ipct") for r in inodes}
    for d in disks:
        d["inode_pct"] = inode_map.get((d["fs"], d["mount"]))
    return disks


def _parse_lsblk(block: str) -> list[dict[str, str]]:
    """lsblk -P 输出（NAME="sda" SIZE="40G" ...）→ 行列表。"""
    rows = []
    for line in block.splitlines():
        if "=" not in line:
            continue
        row = dict(re.findall(r'(\w+)="([^"]*)"', line))
        if row:
            rows.append(
                {
                    "name": row.get("NAME", ""),
                    "size": row.get("SIZE", ""),
                    "type": row.get("TYPE", ""),
                    "fstype": row.get("FSTYPE", ""),
                    "mount": row.get("MOUNTPOINT", ""),
                    "model": row.get("MODEL", "").strip(),
                }
            )
    return rows


def _parse_ifaces(block: str) -> list[dict[str, Any]]:
    """ip -brief address（回退 ip -o -4 addr）→ [{iface, state, addresses}]。"""
    ifaces: dict[str, dict[str, Any]] = {}
    for line in block.splitlines():
        parts = line.split()
        if not parts:
            continue
        if parts[0].isdigit() and ":" in parts[0] and len(parts) > 1:
            # ip -o 回退格式: 2: eth0    inet 172.16.0.4/20 brd ...
            name = parts[1]
            addr = parts[3] if len(parts) > 3 and parts[2] == "inet" else ""
            item = ifaces.setdefault(name, {"iface": name, "state": "", "addresses": []})
            if addr and addr not in item["addresses"]:
                item["addresses"].append(addr)
        else:
            # ip -brief 格式: eth0 UP 172.16.0.4/20 fe80::...
            name, state = parts[0], (parts[1] if len(parts) > 1 else "")
            item = ifaces.setdefault(name, {"iface": name, "state": "", "addresses": []})
            if state in ("UP", "DOWN", "UNKNOWN"):
                item["state"] = state
            for a in (parts[2:] if state in ("UP", "DOWN", "UNKNOWN") else parts[1:]):
                if a not in item["addresses"]:
                    item["addresses"].append(a)
    return list(ifaces.values())


def _parse_tcp_summary(block: str) -> dict[str, Any]:
    """ss -s 摘要 → {total, tcp, estab, timewait, udp}。"""
    text = block

    def grab(pattern: str) -> int | None:
        m = re.search(pattern, text)
        return int(m.group(1)) if m else None

    return {
        "total": grab(r"Total:\s*(\d+)"),
        "tcp": grab(r"TCP:\s*(\d+)"),
        "estab": grab(r"estab\s+(\d+)"),
        "timewait": grab(r"timewait\s+(\d+)"),
        "udp": grab(r"UDP:\s*(\d+)"),
    }


def _parse_listen(block: str) -> list[dict[str, str]]:
    """ss -tulnpH → [{proto, local, process}]。"""
    rows = []
    for line in block.splitlines():
        parts = line.split(None, 5)
        if len(parts) < 5:
            continue
        proc = ""
        m = re.search(r'users:\(\("([^"]+)"(?:,pid=(\d+))?', line)
        if m:
            proc = m.group(1) + (f"(pid={m.group(2)})" if m.group(2) else "")
        rows.append({"proto": parts[0], "local": parts[4], "process": proc})
    return rows


def collect_sysinfo(ip: str, port: int, username: str, password: str, key_path: str = "") -> dict[str, Any]:
    """SSH 一次性采集服务器全景信息（只读命令，失败段落留空不影响整体）。"""
    # 与 inspect_host 同策略：私钥与密码并行提供，任一成功即可
    pkey = _load_private_key(key_path) if key_path else None
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            ip,
            port=port,
            username=username,
            password=password or None,
            pkey=pkey,
            timeout=6.0,
            banner_timeout=6.0,
            auth_timeout=6.0,
            allow_agent=False,
            look_for_keys=False,
        )
        _, stdout, _ = client.exec_command(SYSINFO_CMD, timeout=COLLECT_TIMEOUT * 2)
        raw = stdout.read().decode("utf-8", errors="replace")
    finally:
        client.close()

    s = _split_sections(raw)
    lines = {k: [l for l in v.splitlines() if l.strip()] for k, v in s.items()}

    # ---- 基础信息：hostname / kernel / arch / os / date / tz / boot ----
    basic_lines = lines.get("BASIC", [])
    basic = {
        "hostname": basic_lines[0] if len(basic_lines) > 0 else "",
        "kernel": basic_lines[1] if len(basic_lines) > 1 else "",
        "arch": basic_lines[2] if len(basic_lines) > 2 else "",
        "os": basic_lines[3] if len(basic_lines) > 3 else "",
        "local_time": basic_lines[4] if len(basic_lines) > 4 else "",
        "timezone": basic_lines[5] if len(basic_lines) > 5 else "",
        "boot_at": basic_lines[6] if len(basic_lines) > 6 else "",
    }

    # ---- 虚拟化 ----
    virt_lines = lines.get("VIRT", [])
    vtype = virt_lines[0].strip() if virt_lines else ""
    product = next((l for l in virt_lines[1:] if l.strip() and not l.strip().isdigit()), "")
    virt = {
        "type": vtype,
        "label": VIRT_LABELS.get(vtype, vtype or "未知"),
        "product": product,
    }

    # ---- CPU ----
    cpu = _parse_lscpu(s.get("CPU", ""))

    # ---- 内存 ----
    meminfo = _parse_meminfo(s.get("MEMINFO", ""))
    free_parsed = _parse_free_m(s.get("MEMINFO", ""))
    mem = free_parsed.get("mem", {})
    swap = free_parsed.get("swap", {})
    if not mem and meminfo.get("MemTotal"):
        total = meminfo["MemTotal"] // 1024
        avail = meminfo.get("MemAvailable", meminfo.get("MemFree", 0)) // 1024
        mem = {"total_mb": total, "used_mb": total - avail, "free_mb": avail,
               "pct": round((total - avail) / total * 100, 1) if total else None}
    if not swap and meminfo.get("SwapTotal"):
        swap = {"total_mb": meminfo["SwapTotal"] // 1024,
                "used_mb": (meminfo["SwapTotal"] - meminfo.get("SwapFree", 0)) // 1024,
                "free_mb": meminfo.get("SwapFree", 0) // 1024}
    memory = {
        "mem": mem,
        "swap": swap,
        "detail_kb": {k: meminfo[k] for k in ("MemTotal", "MemFree", "MemAvailable", "Buffers", "Cached", "Active", "Inactive", "Slab", "Dirty") if k in meminfo},
    }

    # ---- 磁盘 ----
    disks = _merge_disks(_parse_df(s.get("DISKS", ""), human=True), _parse_df(s.get("INODES", ""), human=False))
    blocks = _parse_lsblk(s.get("BLOCKS", ""))

    # ---- 网络 ----
    iface_rows = _parse_ifaces(s.get("IFACES", ""))
    routes = lines.get("ROUTE", [])
    gateway = next((re.search(r"default via (\S+)", r).group(1) for r in routes if re.search(r"default via (\S+)", r)), "")
    network = {
        "interfaces": iface_rows,
        "gateway": gateway,
        "routes": routes,
        "tcp": _parse_tcp_summary(s.get("TCP", "")),
        "listening": _parse_listen(s.get("LISTEN", "")),
    }

    # ---- 进程 ----
    proc_lines = lines.get("PROCS", [])

    def _int_at(idx: int) -> int:
        try:
            return int(proc_lines[idx].strip())
        except (IndexError, ValueError):
            return 0

    processes = {"total": _int_at(0), "running": _int_at(1), "zombie": _int_at(2)}

    # ---- 登录会话 ----
    users = lines.get("USERS", [])

    # ---- Zabbix Agent ----
    agent_lines = lines.get("AGENT", [])
    version = next((l for l in agent_lines if "Zabbix" in l and re.search(r"\d+\.\d+", l)), "")
    procs = [l for l in agent_lines if l != version]
    agent = {"running": bool(procs), "version": version, "processes": procs}

    return {
        "ip": ip,
        "username": username,
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "basic": basic,
        "virt": virt,
        "cpu": cpu,
        "memory": memory,
        "disks": disks,
        "blocks": blocks,
        "network": network,
        "processes": processes,
        "users": users,
        "zabbix_agent": agent,
    }
