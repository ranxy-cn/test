"""子机 Agent 部署器：经 SSH 下发自研采集探针（Python 脚本或 Go 二进制）。

流程：建 SSH → 检测/补齐 python3（仅 py 版）→ sftp 上传 agent 与 config.json
→ 停旧进程 → nohup 拉起 → 平台侧等待首帧上报验证。进度写 asset.extra["agent_deploy"]。
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Any

from sqlalchemy.orm import Session

from app.models import Asset, utcnow
from app.routers.agent_api import LATEST, _LATEST_LOCK
from app.services.provision import ProvisionError, _connect_ssh, _detect_sudo, _run

AGENT_DIR = "/opt/devops-agent"
# backend/agent/ 下资产文件
ASSET_FILES = {
    "py": ("agent.py", "agent.py"),
    "go": ("dist/agent-linux-amd64", "agent"),
}


def _asset_dir() -> str:
    return os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "agent")


def _step(asset_id: str, db: Session, steps: list[str], text: str) -> None:
    steps.append(text)
    asset = db.get(Asset, asset_id)
    if asset:
        asset.extra = {**(asset.extra or {}), "agent_deploy": {**(asset.extra or {}).get("agent_deploy", {}), "steps": steps[-30:]}}
        db.commit()


def deploy_to_host(
    db: Session,
    asset_id: str,
    *,
    ip: str,
    lang: str,
    ssh_user: str,
    ssh_password: str,
    token: str,
    server_url: str,
    cfg: dict,
    port: int = 22,
    requested_by: str = "",
) -> None:
    steps: list[str] = []
    asset = db.get(Asset, asset_id)
    if not asset:
        raise ProvisionError("资产不存在")
    # 首帧确认基准：部署前记录平台侧 last_seen，部署后必须出现"新"上报（防历史残留误判）
    report_t0 = time.time()
    old_seen = ((asset.extra or {}).get("agent") or {}).get("last_seen") or ""

    src_name, dst_name = ASSET_FILES["py" if lang == "py" else "go"]
    src = os.path.join(_asset_dir(), src_name)
    if not os.path.exists(src):
        raise ProvisionError(f"agent 文件缺失：{src_name}（go 版需先构建 dist/agent-linux-amd64）")

    _step(asset_id, db, steps, f"连接 {ssh_user}@{ip}:{port}")
    ssh: Any = _connect_ssh(ip, port, ssh_user or "root", ssh_password or "")
    try:
        sudo = _detect_sudo(ssh, steps)
        if lang == "py":
            _step(asset_id, db, steps, "检测 python3（需 3.7+）")
            code, out = _run(ssh, "command -v python3 && python3 --version", 20, steps)
            m = re.search(r"Python 3\.(\d+)", out) if code == 0 else None
            if not (m and int(m.group(1)) >= 7):
                # 缺失或过低（CentOS 7 自带 3.6 不兼容）都尝试安装后复验
                _step(asset_id, db, steps, "python3 缺失或版本过低，尝试安装")
                pkg = "apt-get install -y python3" if _has_cmd(ssh, "apt-get") else "yum install -y python3"
                code, _ = _run(ssh, f"{sudo} {pkg}".strip(), 300, steps)
                code2, out2 = _run(ssh, "python3 --version", 20, steps)
                m2 = re.search(r"Python 3\.(\d+)", out2) if code2 == 0 else None
                if code != 0 or not (m2 and int(m2.group(1)) >= 7):
                    raise ProvisionError("目标机 python3 需要 3.7+（3.6 不兼容），建议改用 Go 版 agent（零依赖静态二进制）")

        _step(asset_id, db, steps, f"上传 {dst_name} 与配置")
        _run(ssh, f"{sudo} mkdir -p {AGENT_DIR}".strip(), 20, steps)
        sftp = ssh.open_sftp()
        try:
            remote = f"{AGENT_DIR}/{dst_name}"
            sftp.put(src, remote + ".tmp")
            # posix_rename 原子覆盖旧文件（SFTPv3 rename 目标已存在会报 Failure，重装必挂）
            try:
                sftp.posix_rename(remote + ".tmp", remote)
            except (AttributeError, OSError, IOError):
                try:
                    sftp.remove(remote)
                except (OSError, IOError):
                    pass
                sftp.rename(remote + ".tmp", remote)
            if lang == "go":
                _run(ssh, f"chmod +x {remote}", 15, steps)
            cfg_data = {
                "server": server_url,
                "token": token,
                "asset_id": asset_id,
                "agent_config": cfg,
            }
            with sftp.open(f"{AGENT_DIR}/config.json", "w") as fh:
                fh.write(json.dumps(cfg_data))
        finally:
            sftp.close()

        _step(asset_id, db, steps, "停止旧进程并拉起")
        runner = (
            f"python3 {AGENT_DIR}/agent.py --config {AGENT_DIR}/config.json"
            if lang == "py"
            else f"{AGENT_DIR}/agent --config {AGENT_DIR}/config.json"
        )
        _run(ssh, f"pkill -f 'devops-agent/agent' 2>/dev/null; true", 15, steps)
        code, out = _run(
            ssh,
            f"{sudo} sh -c 'nohup {runner} >> {AGENT_DIR}/agent.log 2>&1 & echo $!'".strip(),
            20,
            steps,
        )
        pid = out.strip().splitlines()[-1] if out.strip() else ""
        if code != 0 or not pid.isdigit():
            raise ProvisionError(f"agent 拉起失败：{out[-200:]}")
        _step(asset_id, db, steps, f"已启动 pid={pid}，等待首帧上报")

        # 平台侧验证：10 秒内出现"新"上报即成功（last_seen 变化 或 新样本 ts > 部署开始）
        deadline = time.time() + 10
        ok = False
        while time.time() < deadline:
            db.expire_all()  # 强制重读最新 extra.agent.last_seen（上报端点在另一会话写入）
            cur = db.get(Asset, asset_id)
            now_seen = (((cur.extra or {}).get("agent") or {}).get("last_seen") or "") if cur else ""
            if now_seen and now_seen != old_seen:
                ok = True
                break
            with _LATEST_LOCK:
                latest_ts = float((LATEST.get(asset_id) or {}).get("ts") or 0)
            if latest_ts > report_t0:
                ok = True
                break
            time.sleep(1)
        state = "success" if ok else "partial"
        _step(asset_id, db, steps, "首帧上报确认" if ok else "已启动但暂未收到上报（检查网络回连）")
        asset = db.get(Asset, asset_id)
        if asset:
            # 部署收尾：同步回写纳管状态（卡片"安装中→已纳管"依赖此字段）
            prov = (asset.extra or {}).get("provision") or {}
            asset.extra = {
                **(asset.extra or {}),
                "provision": {**prov, "status": "registered"},
                "agent_deploy": {
                    "state": state,
                    "lang": lang,
                    "pid": pid,
                    "requested_by": requested_by,
                    "finished": utcnow().isoformat(),
                    "steps": steps[-30:],
                },
            }
            db.commit()
    finally:
        try:
            ssh.close()
        except Exception:  # noqa: BLE001
            pass


def uninstall_via_ssh(ssh: Any, logs: list[str] | None = None) -> None:
    """停止目标机上的自研 agent 进程并删除安装目录（删除资产时的远程清理）。"""
    out: list[str] = logs if logs is not None else []
    sudo = _detect_sudo(ssh, out)
    _run(ssh, "pkill -f 'devops-agent/agent' 2>/dev/null; true", 15, out)
    code, text = _run(ssh, f"{sudo} rm -rf {AGENT_DIR} && echo REMOVED".strip(), 30, out)
    if code != 0 or "REMOVED" not in text:
        raise ProvisionError(f"清理安装目录失败：{text[-200:]}")
    out.append(f"已停止 agent 进程并清理 {AGENT_DIR}")


def _has_cmd(ssh: Any, cmd: str) -> bool:
    try:
        code, _ = _run(ssh, f"command -v {cmd}", 10, [])
        return code == 0
    except ProvisionError:
        return False
