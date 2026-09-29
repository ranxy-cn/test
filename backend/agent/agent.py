#!/usr/bin/env python3
"""DevOpsAgent 子机采集探针（Python 实现，零第三方依赖，纯标准库）。

常驻运行：按配置间隔采集本机 /proc 指标（CPU/内存/磁盘/负载/网络速率），
推送到平台 /api/v1/agent/report；断网时本地环形缓存，恢复后按序补发；
定期拉取 /api/v1/agent/config 同步平台侧配置。

用法：
  python3 agent.py --server http://<母机IP>:8000 --token <令牌> --asset-id <资产ID>
也可用 --config /opt/devops-agent/config.json 承载以上参数。
"""

from __future__ import annotations

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


def _cpu() -> float | None:
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


def _mem() -> float | None:
    info = {}
    for line in _read(PROC + "/meminfo").splitlines():
        parts = line.split(":")
        if len(parts) == 2:
            info[parts[0].strip()] = float(parts[1].strip().split()[0])
    total, avail = info.get("MemTotal"), info.get("MemAvailable")
    if not total or avail is None or total <= 0:
        return None
    return round((total - avail) / total * 100, 2)


def _disk() -> float | None:
    import shutil  # 标准库

    for path in ("/",):
        try:
            total, _used, free = shutil.disk_usage(path)
            if total > 0:
                return round((total - free) / total * 100, 2)
        except OSError:
            continue
    return None


def _load() -> float | None:
    try:
        return float(_read(PROC + "/loadavg").split()[0])
    except (OSError, ValueError, IndexError):
        return None


def _net() -> tuple[float, float] | None:
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


def collect(items: list[str]) -> dict:
    sample: dict = {"ts": time.time()}
    if "cpu" in items:
        sample["cpu"] = _cpu()
    if "mem" in items:
        sample["mem"] = _mem()
    if "disk" in items:
        sample["disk"] = _disk()
    if "load" in items:
        sample["load1"] = _load()
    if "net" in items:
        rates = _net()
        if rates:
            sample["net_rx_bps"], sample["net_tx_bps"] = rates
    return sample


class Agent:
    def __init__(self, server: str, token: str, asset_id: str, cfg: dict):
        self.server = server.rstrip("/")
        self.token = token
        self.asset_id = asset_id
        self.cfg = dict(cfg)
        self.buffer: collections.deque = collections.deque(maxlen=int(cfg.get("buffer_max", 600)))
        self.pending = 0  # 已入队待发条数（含补发）

    # ---- HTTP ----
    def _req(self, method: str, path: str, body: dict | None = None, timeout: float = 5.0):
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
        except (urllib.error.URLError, OSError, ValueError):
            pass

    def flush(self, extra: list[dict] | None = None):
        """把缓存与即时样本一次性上报；失败则回填缓存。"""
        samples = list(self.buffer)
        if extra:
            samples += extra
        if not samples:
            return
        payload = {"asset_id": self.asset_id, "agent_version": AGENT_VERSION, "samples": samples}
        try:
            self._req("POST", "/api/v1/agent/report", payload)
            self.buffer.clear()
            self.pending = 0
        except (urllib.error.URLError, OSError, ValueError):
            for s in extra or []:
                self.buffer.append(s)
            self.pending = len(self.buffer)

    def run(self):
        self.sync_config()
        next_config = time.time() + int(self.cfg.get("config_refresh", 300))
        collect_iv = max(1, int(self.cfg.get("collect_interval", 5)))
        next_collect = 0.0
        next_report = 0.0
        ready: list[dict] = []
        print(f"[agent] v{AGENT_VERSION} start -> {self.server} asset={self.asset_id}", flush=True)
        while not _stop.is_set():
            now = time.time()
            if now >= next_collect:
                ready.append(collect(list(self.cfg.get("collect_items", ["cpu", "mem", "disk", "load", "net"]))))
                next_collect = now + collect_iv
            if now >= next_report:
                self.flush(ready)
                ready = []
                next_report = now + max(1, int(self.cfg.get("report_interval", 5)))
            if now >= next_config:
                self.sync_config()
                next_config = now + max(30, int(self.cfg.get("config_refresh", 300)))
            time.sleep(0.2)


def load_config(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def main():
    ap = argparse.ArgumentParser(description="DevOpsAgent 子机采集探针")
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
