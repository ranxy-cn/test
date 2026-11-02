"""进程监听（Process Watch）：资产卡片上的进程看护配置与状态合并服务。

监听配置持久化在 Asset.extra["process_watch"]（不建表、无迁移），经子机自研
Agent 的配置拉取接口（GET /agent/config，config_refresh 周期热生效）下发；
Agent 在子机本地采集进程信息（/proc 扫描 + docker CLI），随上报帧带回，
平台从最新帧（agent_api.LATEST）读取并合并展示——全程不经平台 SSH 直连。

识别与采集适配（Agent 侧实现，见 backend/agent/agent.py / agent.go）：
- 外部独立程序 / Tomcat 服务：按 PID 直查或按程序名称扫描 /proc cmdline
  子串匹配；运行状态取 /proc/<pid>/stat 状态位；监听端口经 /proc/net/tcp(6)
  LISTEN inode 与 /proc/<pid>/fd socket 反查；Tomcat 尽力回读 CATALINA_BASE。
- Docker 容器内部进程：docker ps -a 匹配容器名/ID（或按主进程 PID 经
  /proc/<pid>/cgroup 反查容器），状态取容器 State，端口取发布映射。
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from app.models import BEIJING_TZ

PTYPE_LABELS = {"external": "外部程序", "tomcat": "Tomcat 服务", "docker": "Docker 容器"}
PTYPES = set(PTYPE_LABELS)
_MATCHES = {"pid", "name", "container"}

_PID_RE = re.compile(r"^\d{1,16}$")

# host ps stat 首字母 / docker State.Status → (status, 中文文案)
_HOST_STATUS: dict[str, tuple[str, str]] = {
    "R": ("running", "运行中"),
    "S": ("running", "运行中"),
    "D": ("running", "运行中"),
    "I": ("running", "运行中"),
    "W": ("running", "运行中"),
    "T": ("stopped", "已暂停"),
    "t": ("stopped", "已暂停"),
    "Z": ("zombie", "僵尸进程"),
    "X": ("dead", "已退出"),
}

_DOCKER_STATUS: dict[str, tuple[str, str]] = {
    "running": ("running", "运行中"),
    "exited": ("exited", "已退出"),
    "paused": ("paused", "已暂停"),
    "restarting": ("restarting", "重启中"),
    "created": ("created", "未启动"),
    "dead": ("dead", "异常退出"),
    "removing": ("removing", "移除中"),
}


def host_status(stat: str) -> tuple[str, str]:
    s = (stat or "").strip()
    if not s:
        return "unknown", "未知"
    return _HOST_STATUS.get(s[:1], ("abnormal", f"异常({s})"))


def docker_status(raw: str) -> tuple[str, str]:
    s = (raw or "").strip()
    if not s:
        return "unknown", "未知"
    return _DOCKER_STATUS.get(s, ("abnormal", f"异常({s})"))


# ---------------------------------------------------------------------------
# 新增配置：入参收敛（平台侧只做格式校验，进程存在性由 Agent 采集回填）
# ---------------------------------------------------------------------------

def normalize_new(ptype: str, match: str, pid: str, name: str, container: str) -> tuple[dict[str, str], str]:
    """收敛新增入参 → (entry, err)。err 非空表示参数不合法。"""
    ptype = (ptype or "").strip()
    if ptype not in PTYPES:
        return {}, "进程类型不合法（external/tomcat/docker）"
    pid = (pid or "").strip()
    name = (name or "").strip()
    container = (container or "").strip()
    entry: dict[str, str] = {"ptype": ptype, "match": "", "pid": "", "name": "", "container": ""}
    if match == "pid" and _PID_RE.fullmatch(pid):
        entry["match"] = "pid"
        entry["pid"] = pid
        return entry, ""
    if ptype == "docker":
        if container:
            entry["match"] = "container"
            entry["container"] = container[:128]
            return entry, ""
        return {}, "请填写容器名称/ID，或容器主进程 PID"
    if name:
        entry["match"] = "name"
        entry["name"] = name[:128]
        return entry, ""
    return {}, "请填写进程 PID 或程序名称"


# ---------------------------------------------------------------------------
# 配置 + Agent 最新帧 → 卡片展示条目
# ---------------------------------------------------------------------------

def _display_name(entry: dict[str, Any], report: dict[str, Any] | None) -> str:
    if (entry.get("ptype") or "") == "docker":
        return (
            (report or {}).get("name")
            or str(entry.get("container") or "").lstrip("/")
            or "容器进程"
        )
    if entry.get("name"):
        return str(entry["name"])
    if report:
        comm = str(report.get("comm") or "")
        if comm:
            return comm
        args = str(report.get("args") or "")
        if args:
            return args.split(" ")[0]
    return str(entry.get("pid") or "进程")


def _ports_of(report: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for p in (report or {}).get("ports") or []:
        s = str(p)
        if s.isdigit() and s not in out:
            out.append(s)
    return out[:32]


def entry_item(entry: dict[str, Any], report: dict[str, Any] | None, frame_ts: float = 0) -> dict[str, Any]:
    """合并配置条目与 Agent 最新帧采集结果。

    report=None 表示 Agent 尚未上报该条目（待采集）；report.found=False 表示
    Agent 采集过但进程/容器不存在。
    """
    ptype = entry.get("ptype") or "external"
    checked_at = ""
    if frame_ts > 0:
        try:
            checked_at = datetime.fromtimestamp(float(frame_ts), BEIJING_TZ).isoformat(timespec="seconds")
        except (ValueError, OSError, OverflowError):
            checked_at = ""
    item: dict[str, Any] = {
        "id": entry.get("id", ""),
        "ptype": ptype,
        "ptype_label": PTYPE_LABELS.get(ptype, ptype),
        "match": entry.get("match", ""),
        "config_pid": entry.get("pid", ""),
        "config_name": entry.get("name", ""),
        "container": entry.get("container", ""),
        "pid": "",
        "display_name": _display_name(entry, report),
        "status": "pending",
        "status_text": "待采集",
        "ports": [],
        "elapsed": "",
        "comm": "",
        "args": "",
        "image": "",
        "catalina": "",
        "error": str((report or {}).get("error") or ""),
        "checked_at": checked_at,
    }
    if report is None:
        return item
    item["display_name"] = _display_name(entry, report)
    item["args"] = str(report.get("args") or "")[:300]
    item["ports"] = _ports_of(report)
    if ptype == "docker":
        item["image"] = str(report.get("image") or "")
        item["pid"] = str(report.get("pid") or "")
        if not report.get("found"):
            item["status"], item["status_text"] = "not_found", "未找到"
            return item
        item["status"], item["status_text"] = docker_status(str(report.get("status_raw") or ""))
        return item
    # host 进程（external / tomcat）
    item["pid"] = str(report.get("pid") or "")
    item["elapsed"] = str(report.get("elapsed") or "")
    item["comm"] = str(report.get("comm") or "")
    item["catalina"] = str(report.get("catalina") or "")
    if not report.get("found"):
        item["status"], item["status_text"] = "not_found", "未找到"
        return item
    item["status"], item["status_text"] = host_status(str(report.get("stat") or ""))
    return item


def merge_items(entries: list[dict[str, Any]], frame: dict[str, Any] | None) -> list[dict[str, Any]]:
    """资产配置条目 + Agent 最新帧的 process_watch 采集结果 → 展示条目列表。"""
    reports: dict[str, dict[str, Any]] = {}
    frame_ts = 0.0
    if isinstance(frame, dict):
        try:
            frame_ts = float(frame.get("ts") or 0)
        except (TypeError, ValueError):
            frame_ts = 0.0
        for r in frame.get("process_watch") or []:
            if isinstance(r, dict) and r.get("id"):
                reports[str(r["id"])] = r
    return [entry_item(e, reports.get(str(e.get("id") or "")), frame_ts) for e in entries]
