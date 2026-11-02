#!/usr/bin/env python3
"""新知AI-智能巡检 子机采集探针（Python 实现，零第三方依赖，纯标准库）。

兼容 python3.6+（CentOS 7 等老系统自带 3.6：复杂类型标注一律字符串化，
运行时不求值；禁用 from __future__ import annotations）。

常驻运行：按配置间隔采集本机 /proc 指标（CPU/内存/磁盘/负载/网络速率），
推送到平台 /api/v1/agent/report；断网时本地环形缓存，恢复后按序补发；
定期拉取 /api/v1/agent/config 同步平台侧配置。

用法：
  python3 agent.py --server http://<母机IP>:8000 --token <令牌> --asset-id <资产ID>
也可用 --config /opt/devops-agent/config.json 承载以上参数。
"""

import argparse
import collections
import json
import os
import signal
import sys
import threading
import time
import urllib.error
import urllib.request

AGENT_VERSION = "1.0.0-py"
PROC = "/proc"
_SKIP_IFACES = ("lo", "docker", "veth", "br-", "virbr", "tun", "tap", "flannel", "cni", "cali", "calico")

_stop = threading.Event()
_state_lock = threading.Lock()
_state = {"cpu": None, "net": None}  # 差值基准快照


def _read(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def _primary_iface() -> str:
    try:
        for line in _read(PROC + "/net/route").splitlines()[1:]:
            cols = line.split()
            if len(cols) > 7 and cols[0] and cols[1] == "00000000":
                return cols[0]
    except OSError:
        pass
    try:
        for line in _read(PROC + "/net/dev").splitlines()[2:]:
            name = line.split(":")[0].strip()
            if not any(name.startswith(p) for p in _SKIP_IFACES):
                return name
    except OSError:
        pass
    return "eth0"


def _cpu() -> "float | None":
    line = _read(PROC + "/stat").splitlines()[0]
    vals = [float(x) for x in line.split()[1:]]
    # user nice system idle iowait irq softirq steal ...
    idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
    total = sum(vals[:8])
    with _state_lock:
        prev = _state["cpu"]
        _state["cpu"] = (total, idle)
    if not prev:
        return None
    dt, di = total - prev[0], idle - prev[1]
    if dt <= 0:
        return None
    return round(max(0.0, min(100.0, (1 - di / dt) * 100)), 2)


def _mem() -> "float | None":
    info = {}
    for line in _read(PROC + "/meminfo").splitlines():
        parts = line.split(":")
        if len(parts) == 2:
            info[parts[0].strip()] = float(parts[1].strip().split()[0])
    total, avail = info.get("MemTotal"), info.get("MemAvailable")
    if not total or avail is None or total <= 0:
        return None
    return round((total - avail) / total * 100, 2)


def _disk() -> "float | None":
    import shutil  # 标准库

    for path in ("/",):
        try:
            total, _used, free = shutil.disk_usage(path)
            if total > 0:
                return round((total - free) / total * 100, 2)
        except OSError:
            continue
    return None


def _load() -> "float | None":
    try:
        return float(_read(PROC + "/loadavg").split()[0])
    except (OSError, ValueError, IndexError):
        return None


def _net() -> "tuple[float, float] | None":
    iface = _primary_iface()
    rx = tx = None
    for line in _read(PROC + "/net/dev").splitlines()[2:]:
        if line.split(":")[0].strip() != iface:
            continue
        fields = line.split(":")[1].split()
        rx, tx = float(fields[0]), float(fields[8])
        break
    if rx is None:
        return None
    now = time.time()
    with _state_lock:
        prev = _state["net"]
        _state["net"] = (now, rx, tx)
    if not prev:
        return None
    dt = now - prev[0]
    if dt <= 0:
        return None
    return round(max(0.0, rx - prev[1]) / dt, 1), round(max(0.0, tx - prev[2]) / dt, 1)


def _uptime_sec() -> "float | None":
    try:
        return float(_read(PROC + "/uptime").split()[0])
    except (OSError, ValueError, IndexError):
        return None


def _fmt_elapsed(sec: float) -> str:
    """秒 → ps 风格运行时长（[[dd-]hh:]mm:ss）。"""
    s = int(sec)
    d, h, m, ss = s // 86400, (s % 86400) // 3600, (s % 3600) // 60, s % 60
    if d > 0:
        return f"{d}d {h:02d}:{m:02d}:{ss:02d}"
    if h > 0:
        return f"{h:02d}:{m:02d}:{ss:02d}"
    return f"{m:02d}:{ss:02d}"


def _mem_total_kb() -> float:
    try:
        for line in _read(PROC + "/meminfo").splitlines():
            if line.startswith("MemTotal:"):
                return float(line.split(":")[1].strip().split()[0])
    except (OSError, ValueError, IndexError):
        pass
    return 0.0


_uid_cache = {}
_uid_cache_lock = threading.Lock()


def _uid_to_user(uid: str) -> str:
    with _uid_cache_lock:
        if not _uid_cache:
            try:
                for line in _read("/etc/passwd").splitlines():
                    parts = line.split(":")
                    if len(parts) >= 3:
                        _uid_cache[parts[2]] = parts[0]
            except OSError:
                pass
    return _uid_cache.get(uid, uid)


def _proc_user(pid: str) -> str:
    try:
        for line in _read(PROC + "/" + pid + "/status").splitlines():
            if line.startswith("Uid:"):
                f = line.split()
                if len(f) >= 2:
                    return _uid_to_user(f[1])
                break
    except OSError:
        pass
    return ""


def _proc_args(pid: str) -> str:
    try:
        with open(PROC + "/" + pid + "/cmdline", "rb") as fh:
            data = fh.read()
        return data.replace(b"\x00", b" ").decode("utf-8", errors="replace").strip()
    except OSError:
        return ""


def _load_full():
    """返回 (load1, load5, load15, ncpu) 或 None。

    ncpu 用 os.cpu_count（loadavg 第 4 字段的 total 是线程总数，不是核数）。
    """
    try:
        fields = _read(PROC + "/loadavg").split()
        if len(fields) < 3:
            return None
        l1, l5, l15 = float(fields[0]), float(fields[1]), float(fields[2])
        ncpu = os.cpu_count() or 0
        return round(l1, 2), round(l5, 2), round(l15, 2), ncpu
    except (OSError, ValueError, IndexError):
        return None


def _mem_kb():
    """返回 (total_kb, used_kb) 或 None。"""
    info = {}
    try:
        lines = _read(PROC + "/meminfo").splitlines()
    except OSError:
        return None
    for line in lines:
        parts = line.split(":")
        if len(parts) == 2:
            info[parts[0].strip()] = float(parts[1].strip().split()[0])
    total, avail = info.get("MemTotal"), info.get("MemAvailable")
    if not total or avail is None or total <= 0:
        return None
    return total, max(0.0, total - avail)


def _procs() -> "list[dict]":
    """进程归因：CPU TOP5 + 内存 TOP5 并集（按 pid 去重，最多 10 条）。

    每条 {pid, ppid, comm, user, args, etime, cpu, mem}；cpu 为自进程启动的
    平均占用%（(utime+stime)/CLK_TCK / elapsed），mem 为 RSS 占物理内存%。
    """
    up = _uptime_sec()
    if not up:
        return []
    try:
        page_kb = os.sysconf("SC_PAGE_SIZE") / 1024.0
        hz = os.sysconf("SC_CLK_TCK") or 100
    except (ValueError, OSError):
        page_kb, hz = 4.0, 100
    total_kb = _mem_total_kb()
    infos: "list[dict]" = []
    for name in os.listdir(PROC):
        if not name.isdigit():
            continue
        try:
            stat = _read(PROC + "/" + name + "/stat")
            l, r = stat.index("("), stat.rindex(")")
            comm = stat[l + 1:r]
            rest = stat[r + 2:].split()  # 从 state（第 3 字段）开始
            if len(rest) < 20:
                continue
            utime, stime, start = float(rest[11]), float(rest[12]), float(rest[19])
            ppid = int(rest[1]) if rest[1].isdigit() else 0  # 第 4 字段
            cpu = 0.0
            etime = ""
            elapsed = up - start / hz
            if elapsed > 0:
                cpu = (utime + stime) / hz / elapsed * 100
                etime = _fmt_elapsed(elapsed)
            mem = 0.0
            if total_kb > 0:
                st = _read(PROC + "/" + name + "/statm").split()
                if len(st) > 1:
                    mem = float(st[1]) * page_kb / total_kb * 100
            infos.append({"pid": int(name), "ppid": ppid, "comm": comm, "user": _proc_user(name),
                          "args": _proc_args(name), "etime": etime, "cpu": round(cpu, 2), "mem": round(mem, 2)})
        except (OSError, ValueError, IndexError):
            continue
    by_cpu = sorted(infos, key=lambda p: p["cpu"], reverse=True)[:5]
    by_mem = sorted(infos, key=lambda p: p["mem"], reverse=True)[:5]
    out: "list[dict]" = []
    seen = set()
    for p in by_cpu + by_mem:
        if p["pid"] in seen or len(out) >= 10:
            continue
        seen.add(p["pid"])
        out.append(p)
    return out


# ---------------------------------------------------------------------------
# 进程监听：平台经 /agent/config 下发监听条目（资产 extra["process_watch"]），
# 本地 /proc 扫描 + docker CLI 采集后随帧带回（sample["process_watch"]），无 SSH。
# ---------------------------------------------------------------------------

_PW_TTL = 30.0  # 采集结果缓存秒数（配置变更立即失效）
_pw_cache = {"key": None, "ts": 0.0, "data": None}
_docker_ps_cache = {"ts": 0.0, "rows": None, "err": ""}


def _pw_listen_ports(pid: str) -> "list[str]":
    """反查进程监听端口：/proc/net/tcp(6) LISTEN inode ↔ /proc/<pid>/fd socket。"""
    inodes = set()
    try:
        for name in os.listdir(PROC + "/" + pid + "/fd"):
            try:
                link = os.readlink(PROC + "/" + pid + "/fd/" + name)
            except OSError:
                continue
            if link.startswith("socket:[") and link.endswith("]"):
                inodes.add(link[8:-1])
    except OSError:
        return []
    if not inodes:
        return []
    ports: "list[str]" = []
    for table in ("/net/tcp", "/net/tcp6"):
        try:
            lines = _read(PROC + table).splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            f = line.split()
            # 第 4 列 0A = TCP_LISTEN；第 2 列地址:十六进制端口；第 10 列 inode
            if len(f) < 10 or f[3] != "0A" or f[9] not in inodes:
                continue
            try:
                port = str(int(f[1].split(":")[1], 16))
            except (ValueError, IndexError):
                continue
            if port and port not in ports:
                ports.append(port)
    return ports[:32]


def _pw_host_entry(entry: dict) -> dict:
    """host 进程（外部程序/Tomcat）单条采集：PID 直查或 /proc cmdline 子串匹配。"""
    out = {"id": entry.get("id", ""), "found": False, "error": ""}
    match = str(entry.get("match") or "")
    pid = str(entry.get("pid") or "").strip()
    name = str(entry.get("name") or "").strip()
    if match == "pid":
        if not pid or not os.path.isdir(PROC + "/" + pid):
            return out
        cands = [pid]
    else:
        if not name:
            out["error"] = "配置缺少程序名称"
            return out
        try:
            ids = sorted((n for n in os.listdir(PROC) if n.isdigit()), key=int)
        except OSError:
            return out
        # cmdline 子串匹配（等价 pgrep -f）；跳过自身防自匹配，pid 升序取最早启动的主进程
        cands = [n for n in ids if n != str(os.getpid()) and name in _proc_args(n)]
    if not cands:
        return out
    pid = cands[0]
    out.update(found=True, pid=pid, args=_proc_args(pid), ports=_pw_listen_ports(pid))
    try:
        stat = _read(PROC + "/" + pid + "/stat")
        l, r = stat.index("("), stat.rindex(")")
        out["comm"] = stat[l + 1:r]
        rest = stat[r + 2:].split()  # 从 state（第 3 字段）开始
        out["stat"] = rest[0] if rest else ""
        try:
            hz = float(os.sysconf("SC_CLK_TCK")) or 100.0
        except (ValueError, OSError):
            hz = 100.0
        if len(rest) > 19:
            elapsed = (_uptime_sec() or 0.0) - float(rest[19]) / hz
            if elapsed > 0:
                out["elapsed"] = _fmt_elapsed(elapsed)
    except (OSError, ValueError, IndexError):
        pass
    if str(entry.get("ptype") or "") == "tomcat":
        try:
            with open(PROC + "/" + pid + "/environ", "rb") as fh:
                env = fh.read().replace(b"\x00", b"\n").decode("utf-8", errors="replace")
            for line in env.splitlines():
                if line.startswith("CATALINA_BASE="):
                    out["catalina"] = line.split("=", 1)[1].strip()
                    break
        except OSError:
            pass
    return out


def _docker_ps_lines() -> "tuple[list[str], str]":
    """docker ps -a 行（30s 缓存）。docker 不可用时返回 ([], 错误文案)。"""
    now = time.time()
    if _docker_ps_cache["rows"] is not None and now - _docker_ps_cache["ts"] < _PW_TTL:
        return _docker_ps_cache["rows"], _docker_ps_cache["err"]
    import subprocess

    rows: "list[str]" = []
    err = ""
    try:
        out = subprocess.run(
            ["docker", "ps", "-a", "--format",
             "{{.ID}}|{{.Names}}|{{.State}}|{{.Image}}|{{.Ports}}"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=8,
        )
        text = out.stdout.decode("utf-8", errors="replace") if out.stdout else ""
        rows = [ln for ln in text.splitlines() if "|" in ln]
        if not rows:
            err = "docker ps 无输出（docker 未安装或当前用户无权限）"
    except FileNotFoundError:
        err = "docker 命令不可用"
    except (OSError, ValueError, subprocess.SubprocessError):
        err = "docker ps 执行失败"
    _docker_ps_cache.update(ts=now, rows=rows, err=err)
    return rows, err


def _cgroup_container_id(pid: str) -> str:
    """从 /proc/<pid>/cgroup 提取容器 ID（v1 docker/<id>；v2 docker-<id>.scope）。"""
    try:
        for line in _read(PROC + "/" + pid + "/cgroup").splitlines():
            for seg in line.split("/"):
                if seg.startswith("docker-") and seg.endswith(".scope"):
                    return seg[7:-6]
                if seg.startswith("docker/") and len(seg) > 7:
                    return seg[7:]
    except OSError:
        pass
    return ""


def _container_main_pid(cid: str) -> str:
    """容器主进程 PID：与该容器 cgroup 相同的所有进程中取最小 pid。"""
    best = ""
    try:
        for n in os.listdir(PROC):
            if not n.isdigit():
                continue
            try:
                cg = _read(PROC + "/" + n + "/cgroup")
            except OSError:
                continue
            if ("docker/" + cid) in cg or ("docker-" + cid) in cg:
                if not best or int(n) < int(best):
                    best = n
    except OSError:
        pass
    return best


def _parse_docker_ports(s: str) -> "list[str]":
    """docker ps Ports 列 → 宿主侧发布端口（"0.0.0.0:8080->80/tcp" → "8080"）。"""
    ports: "list[str]" = []
    for part in (s or "").split(","):
        if "->" not in part:
            continue
        hp = part.split("->")[0].strip().rsplit(":", 1)[-1]
        if hp.isdigit() and hp not in ports:
            ports.append(hp)
    return ports[:32]


def _pw_docker_entry(entry: dict, rows: "list[str]", err: str) -> dict:
    """Docker 容器单条采集：docker ps 匹配容器名/ID（或 PID 经 cgroup 反查容器）。"""
    out = {"id": entry.get("id", ""), "found": False, "error": ""}
    if err and not rows:
        out["error"] = err
        return out
    want = str(entry.get("container") or "").strip().lstrip("/")
    if str(entry.get("match") or "") == "pid":
        want = _cgroup_container_id(str(entry.get("pid") or ""))
        if not want:
            return out
    if not want:
        out["error"] = "配置缺少容器名称/ID"
        return out
    row = ""
    for ln in rows:
        f = ln.split("|")
        if len(f) >= 4 and (f[1] == want or f[0].startswith(want) or want.startswith(f[0])):
            row = ln
            break
    if not row:
        return out
    f = row.split("|")
    out.update(found=True, name=f[1], status_raw=f[2], image=f[3],
               ports=_parse_docker_ports(f[4] if len(f) > 4 else ""))
    if f[2] == "running":
        out["pid"] = _container_main_pid(f[0])
    return out


def _watch_key(entries: "list[dict]") -> tuple:
    return tuple(sorted(
        "|".join(str(e.get(k) or "") for k in ("id", "match", "pid", "name", "container"))
        for e in entries
    ))


def _process_watch(entries: "list[dict]") -> "list[dict]":
    """进程监听采集（配置未变更时 TTL 缓存），结果随帧带回 sample["process_watch"]。"""
    if not entries:
        return []
    key = _watch_key(entries)
    now = time.time()
    if _pw_cache["data"] is not None and _pw_cache["key"] == key and now - _pw_cache["ts"] < _PW_TTL:
        return _pw_cache["data"]
    rows, err = _docker_ps_lines()
    out: "list[dict]" = []
    for e in entries:
        if str(e.get("ptype") or "") == "docker":
            out.append(_pw_docker_entry(e, rows, err))
        else:
            out.append(_pw_host_entry(e))
    _pw_cache.update(key=key, ts=now, data=out)
    return out


def collect(items: "list[str]", pw_entries: "list[dict] | None" = None) -> dict:
    sample: dict = {"ts": time.time()}
    if "cpu" in items:
        sample["cpu"] = _cpu()
    if "mem" in items:
        sample["mem"] = _mem()
        kb = _mem_kb()
        if kb:
            sample["mem_total_mb"] = round(kb[0] / 1024, 2)
            sample["mem_used_mb"] = round(kb[1] / 1024, 2)
    if "disk" in items:
        sample["disk"] = _disk()
    if "load" in items:
        lf = _load_full()
        if lf:
            sample["load1"], sample["load5"], sample["load15"] = lf[0], lf[1], lf[2]
            if lf[3] > 0:
                sample["ncpu"] = lf[3]
    if "net" in items:
        rates = _net()
        if rates:
            sample["net_rx_bps"], sample["net_tx_bps"] = rates
    procs = _procs()  # 进程归因：所有告警场景通用
    if procs:
        sample["procs"] = procs
    wd = _process_watch(pw_entries or [])  # 进程监听：随帧带回
    if wd:
        sample["process_watch"] = wd
    return sample


# 服务器详情：本地一次采集全景（与后端 inspector.parse_sysinfo_raw 配套解析，替代 SSH 上机）。
_SYSINFO_CMD = r"""
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
echo @@AGENT@@; ps -eo args 2>/dev/null | grep -E 'devops-agent' | grep -v grep | head -n 3; /opt/devops-agent/agent --version 2>/dev/null | head -n 1;
echo @@END@@
""".replace("\n", " ")


def _sysinfo_raw() -> str:
    """本地 shell 一次采集服务器全景原始文本（失败返回空串）。"""
    try:
        import subprocess

        out = subprocess.run(
            _SYSINFO_CMD, shell=True,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=20,
        )
        return out.stdout.decode("utf-8", errors="replace") if out.stdout else ""
    except (OSError, ValueError, subprocess.SubprocessError):
        return ""


class Agent:
    def __init__(self, server: str, token: str, asset_id: str, cfg: dict):
        self.server = server.rstrip("/")
        self.token = token
        self.asset_id = asset_id
        self.cfg = dict(cfg)
        self.buffer: collections.deque = collections.deque(maxlen=int(cfg.get("buffer_max", 600)))
        self.pending = 0  # 已入队待发条数（含补发）
        self.sysinfo_pending = ""  # 低频附带的服务器详情原始文本
        self.pw_ver = ""  # 进程监听配置指纹（上报响应回带，变更即热拉配置）
        self.cfg_dirty = False

    # ---- HTTP ----
    def _req(self, method: str, path: str, body: "dict | None" = None, timeout: float = 5.0):
        req = urllib.request.Request(
            self.server + path,
            data=json.dumps(body).encode() if body is not None else None,
            method=method,
            headers={"Content-Type": "application/json", "X-Agent-Token": self.token},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode() or "{}")

    def sync_config(self):
        try:
            data = self._req("GET", f"/api/v1/agent/config?asset_id={self.asset_id}")
            cfg = data.get("config") or {}
            if cfg:
                self.cfg = {**self.cfg, **cfg}
                self.buffer = collections.deque(self.buffer, maxlen=int(self.cfg.get("buffer_max", 600)))
            self.pw_ver = str(data.get("pw_ver") or "")
            self.cfg_dirty = False
        except (urllib.error.URLError, OSError, ValueError):
            pass

    def flush(self, extra: "list[dict] | None" = None):
        """把缓存与即时样本一次性上报；失败则回填缓存。"""
        samples = list(self.buffer)
        if extra:
            samples += extra
        if not samples:
            return
        payload = {"asset_id": self.asset_id, "agent_version": AGENT_VERSION, "samples": samples}
        sysinfo = self.sysinfo_pending
        if sysinfo:
            payload["sysinfo"] = sysinfo
        try:
            resp = self._req("POST", "/api/v1/agent/report", payload)
            self.buffer.clear()
            self.pending = 0
            if sysinfo:
                self.sysinfo_pending = ""
            # 监听配置指纹变化 → 下轮立即热拉配置（平台增删监听后秒级生效）
            if str(resp.get("pw_ver") or "") != self.pw_ver:
                self.cfg_dirty = True
        except (urllib.error.URLError, OSError, ValueError):
            for s in extra or []:
                self.buffer.append(s)
            self.pending = len(self.buffer)

    def run(self):
        self.sync_config()
        next_config = time.time() + int(self.cfg.get("config_refresh", 300))
        next_sysinfo = time.time() + 10
        collect_iv = max(1, int(self.cfg.get("collect_interval", 5)))
        next_collect = 0.0
        next_report = 0.0
        ready: "list[dict]" = []
        print(f"[agent] v{AGENT_VERSION} start -> {self.server} asset={self.asset_id}", flush=True)
        while not _stop.is_set():
            now = time.time()
            if now >= next_collect:
                ready.append(collect(
                    list(self.cfg.get("collect_items", ["cpu", "mem", "disk", "load", "net"])),
                    self.cfg.get("process_watch") or [],
                ))
                next_collect = now + collect_iv
            if now >= next_report:
                self.flush(ready)
                ready = []
                next_report = now + max(1, int(self.cfg.get("report_interval", 5)))
            if self.cfg_dirty or now >= next_config:
                dirty = self.cfg_dirty
                self.sync_config()
                next_config = now + max(30, int(self.cfg.get("config_refresh", 300)))
                if dirty:
                    next_collect = 0.0  # 新监听配置到手，立即采集一轮
            if now >= next_sysinfo:
                raw = _sysinfo_raw()
                if raw:
                    self.sysinfo_pending = raw
                next_sysinfo = now + 120
            time.sleep(0.2)


def load_config(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def main():
    ap = argparse.ArgumentParser(description="新知AI-智能巡检 子机采集探针")
    ap.add_argument("--server", default=os.environ.get("AGENT_SERVER", ""))
    ap.add_argument("--token", default=os.environ.get("AGENT_TOKEN", ""))
    ap.add_argument("--asset-id", default=os.environ.get("AGENT_ASSET_ID", ""))
    ap.add_argument("--config", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json"))
    args = ap.parse_args()

    cfg = load_config(args.config)
    server = args.server or cfg.get("server") or ""
    token = args.token or cfg.get("token") or ""
    asset_id = args.asset_id or str(cfg.get("asset_id") or "")
    if not (server and token and asset_id):
        print("[agent] 缺少 server/token/asset-id 配置，退出", file=sys.stderr, flush=True)
        sys.exit(2)

    signal.signal(signal.SIGTERM, lambda *_: _stop.set())
    signal.signal(signal.SIGINT, lambda *_: _stop.set())
    Agent(server, token, asset_id, cfg.get("agent_config") or cfg).run()


if __name__ == "__main__":
    main()
