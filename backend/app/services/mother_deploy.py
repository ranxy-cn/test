"""二期自动部署：在登记的母机（Zabbix Server 部署地）上一键安装整套 Zabbix 栈。

SSH(密码) 直连目标机 →
  [1/5] 预检：端口占用（10051/Web 端口）
  [2/5] 检查/安装 Docker + Compose（get.docker.com 阿里云镜像源，国内可达）
  [3/5] 渲染 docker-compose.yml + .env（bundled=含 MySQL 容器 / external=复用已有 MySQL）
  [4/5] docker compose up -d（首拉镜像较慢，最长 15 分钟）
  [5/5] 健康检查 apiinfo.version（Zabbix 首次启动需导入库表，最长 6 分钟）
成功后把 url + 默认管理员凭据写回 assets.extra["zabbix"]，监控大盘自动切换到新母机实例。
SSH 密码仅在安装期间使用，不落库、不写日志。
"""

from __future__ import annotations

import secrets
import time
from typing import Any

from app.config import get_settings
from app.models import Asset, utcnow
from app.services.audit import add_audit
from app.services.provision import ProvisionError, _connect_ssh, _detect_sudo, _run

STACK_DIR = "devops-zabbix"
DEFAULT_WEB_PORT = 8081
DEFAULT_TRAPPER_PORT = 10051


def stack_name_for(web_port: int) -> str:
    """实例隔离的编排目录 / compose 项目名。

    同一母机可并行部署多个 Zabbix 实例，靠端口区分：默认端口沿用旧目录名
    （兼容存量 ~/devops-zabbix 与单实例升级），非默认端口按端口生成独立目录，
    避免第二个实例覆盖第一个实例的编排文件与容器（compose 项目名冲突）。
    """
    return STACK_DIR if int(web_port) == DEFAULT_WEB_PORT else f"{STACK_DIR}-{int(web_port)}"


COMPOSE_UP_TIMEOUT = 900  # 首次拉镜像 + 启动
WEB_READY_TIMEOUT = 360  # Zabbix 首次启动要导入库表
# 国内可用的 Docker Hub 镜像加速（get.docker.com 的 --mirror Aliyun 只加速安装包，
# 不加速镜像拉取；zabbix/mysql 镜像在 Docker Hub，国内直拉极易超时——部署失败最常见根源）
REGISTRY_MIRRORS = ["https://docker.m.daocloud.io", "https://docker.1ms.run", "https://hub.rat.dev"]


def generate_db_password() -> str:
    """MySQL 口令：token_urlsafe 去掉 -/_，避免个别安装环境的转义问题。"""
    return secrets.token_urlsafe(18).replace("-", "a").replace("_", "Z")


def render_stack_env(db_password: str, root_password: str) -> str:
    return (
        f"MYSQL_PASSWORD={db_password}\n"
        f"MYSQL_ROOT_PASSWORD={root_password}\n"
    )


def render_stack_compose(
    *,
    db_mode: str,
    db_host: str,
    db_port: int,
    web_port: int,
    trapper_port: int = DEFAULT_TRAPPER_PORT,
) -> str:
    """渲染 Zabbix 栈编排。镜像与 186 现网一致（Zabbix 5.0 LTS alpine）。"""
    if db_mode == "external":
        db_service = {
            "DB_SERVER_HOST": db_host,
            "DB_SERVER_PORT": str(db_port),
        }
        services = []
    else:
        db_service = {"DB_SERVER_HOST": "mysql", "MYSQL_DATABASE": "zabbix"}
        services = [
            """  mysql:
    image: mysql:5.7
    restart: unless-stopped
    command: --character-set-server=utf8 --collation-server=utf8_bin
    environment:
      MYSQL_ROOT_PASSWORD: ${MYSQL_ROOT_PASSWORD}
      MYSQL_DATABASE: zabbix
      MYSQL_USER: zabbix
      MYSQL_PASSWORD: ${MYSQL_PASSWORD}
    volumes:
      - ./mysql-data:/var/lib/mysql
"""
        ]

    common_env = "\n".join(f"      {k}: {v}" for k, v in db_service.items()) + """
      MYSQL_DATABASE: zabbix
      MYSQL_USER: zabbix
      MYSQL_PASSWORD: ${MYSQL_PASSWORD}"""

    return f"""services:
{''.join(services)}  zabbix-server:
    image: zabbix/zabbix-server-mysql:alpine-5.0-latest
    restart: unless-stopped
    environment:
{common_env}
      MYSQL_ROOT_PASSWORD: ${{MYSQL_ROOT_PASSWORD}}
    ports:
      - "{trapper_port}:10051"
  zabbix-web:
    image: zabbix/zabbix-web-nginx-mysql:alpine-5.0-latest
    restart: unless-stopped
    environment:
{common_env}
      MYSQL_ROOT_PASSWORD: ${{MYSQL_ROOT_PASSWORD}}
      ZBX_SERVER_HOST: zabbix-server
      PHP_TZ: Asia/Shanghai
    ports:
      - "{web_port}:8080"
  zabbix-agent:
    image: zabbix/zabbix-agent:alpine-5.0-latest
    restart: unless-stopped
    environment:
      ZBX_HOSTNAME: "Zabbix server"
      ZBX_SERVER_HOST: zabbix-server
      ZBX_SERVER_ACTIVE: zabbix-server
"""


def _sudo_wrap(sudo: str, password: str, cmd: str) -> str:
    """root 直跑；sudo 账号用 `sudo -S` 从 stdin 注入密码（heredoc 引号形式，不回显不进日志）。

    注意：整条命令包进 `bash -c`，密码行仅被 sudo 消费；命令内的 ~ 会展开为 root 家目录，
    因此涉及工作目录的命令必须使用绝对路径。
    """
    if not sudo:
        return cmd
    import shlex

    return f"sudo -S -p '' bash -c {shlex.quote(cmd)} <<'PW'\n{password}\nPW"


def _sudo_run(
    ssh: Any,
    sudo: str,
    password: str,
    cmd: str,
    timeout: float,
    logs: list[str],
    *,
    secret_hint: str = "",
) -> tuple[int, str]:
    return _run(ssh, _sudo_wrap(sudo, password, cmd), timeout, logs, secret_hint=secret_hint)


def _has_compose(ssh: Any, sudo: str, password: str, logs: list[str]) -> bool:
    # 注意：老版本 docker（如 18.06）对 `docker compose version` 返回码为 0 但无任何输出，
    # 不能只看退出码，必须校验输出非空。
    _, out = _sudo_run(
        ssh,
        sudo,
        password,
        "command -v docker-compose >/dev/null 2>&1 && echo BIN; "
        "docker compose version 2>/dev/null | grep -q . && echo PLUGIN",
        20,
        logs,
    )
    ok = "PLUGIN" in out or "BIN" in out
    if not ok:
        logs.append("未检测到 docker compose / docker-compose")
    return ok


def install_docker_via_ssh(ssh: Any, sudo: str, password: str, logs: list[str]) -> None:
    """检查并安装 Docker 与 Compose（官方脚本 + 阿里云镜像源；已装则跳过）。"""
    _, out = _run(ssh, "command -v docker >/dev/null 2>&1 && echo YES || echo NO", 15, logs)
    has_docker = "YES" in out
    has_compose = _has_compose(ssh, sudo, password, logs) if has_docker else False

    if has_docker and has_compose:
        logs.append("[2/5] Docker 与 Compose 已就绪，跳过安装")
    else:
        logs.append("[2/5] 安装 Docker + Compose（阿里云源，约 1~3 分钟）...")
        code, _ = _sudo_run(
            ssh,
            sudo,
            password,
            "curl -fsSL https://get.docker.com | sh -s -- --mirror Aliyun",
            600,
            logs,
            secret_hint="安装 Docker 超时",
        )
        if code != 0:
            raise ProvisionError("Docker 安装失败（退出码非 0），详见上方日志")

    code, _ = _sudo_run(
        ssh,
        sudo,
        password,
        "systemctl enable --now docker 2>/dev/null || systemctl start docker",
        60,
        logs,
    )
    if code != 0:
        raise ProvisionError("Docker 服务启动失败")
    _, out = _sudo_run(ssh, sudo, password, "docker info >/dev/null 2>&1 && echo READY || echo NOTREADY", 30, logs)
    if "READY" not in out:
        raise ProvisionError("docker daemon 未就绪（可能需要重启服务器或查看 journalctl -u docker）")
    if not has_compose and not _has_compose(ssh, sudo, password, logs):
        raise ProvisionError("Docker 已安装但缺少 compose（可手动安装 docker-compose-plugin 后重试）")

    ensure_registry_mirrors(ssh, sudo, password, logs, fresh_install=not has_docker)


def ensure_registry_mirrors(ssh: Any, sudo: str, password: str, logs: list[str], *, fresh_install: bool) -> None:
    """确保 Docker 配置了国内镜像加速，避免拉取 zabbix/mysql 镜像超时（部署失败最常见根源）。

    - 已配置加速：跳过。
    - 全新安装的 Docker：直接写入并重启（机器上无业务容器，安全）。
    - 已有 Docker：若 daemon.json 从未自定义（不存在或为空 {}）才写入；有运行中的业务容器时不重启，仅提示。
    """
    _, out = _sudo_run(ssh, sudo, password, "docker info 2>/dev/null | grep -A3 'Registry Mirrors' || true", 20, logs)
    if "https://" in out:
        logs.append("Docker 已配置镜像加速，跳过")
        return

    mirrors_json = ",".join(f'"{m}"' for m in REGISTRY_MIRRORS)
    _, wout = _sudo_run(
        ssh,
        sudo,
        password,
        "if [ -f /etc/docker/daemon.json ] && grep -q registry-mirrors /etc/docker/daemon.json 2>/dev/null; then echo EXISTS; "
        "elif [ -s /etc/docker/daemon.json ]; then echo HASCONF; else "
        f"mkdir -p /etc/docker && printf '{{\"registry-mirrors\": [{mirrors_json}]}}' > /etc/docker/daemon.json && echo WROTE; fi",
        20,
        logs,
    )
    if "WROTE" not in wout:
        logs.append(
            "未自动配置镜像加速（daemon.json 已有自定义配置或存在运行中的容器）；"
            "若后续拉取镜像超时，请手工在 /etc/docker/daemon.json 配置 registry-mirrors 后重启 docker"
        )
        return

    restart = fresh_install
    if not restart:
        _, containers = _sudo_run(ssh, sudo, password, "docker ps -q 2>/dev/null | head -3", 15, logs)
        restart = not containers.strip()
    if restart:
        logs.append(f"已写入国内镜像加速（{len(REGISTRY_MIRRORS)} 个源），重启 docker 生效 ...")
        _sudo_run(ssh, sudo, password, "systemctl restart docker 2>/dev/null || service docker restart", 90, logs)
        _, out2 = _sudo_run(ssh, sudo, password, "docker info >/dev/null 2>&1 && echo READY || echo NOTREADY", 30, logs)
        if "READY" not in out2:
            raise ProvisionError("配置镜像加速后 docker 未就绪，请查看 journalctl -u docker")
    else:
        logs.append("已写入镜像加速配置；检测到机器上有运行中的容器，未自动重启 docker（新配置在下次重启后生效）")


def check_ports_free(ssh: Any, ports: list[int], logs: list[str]) -> None:
    """端口预检：被占用则中止，避免和目标机已有服务冲突。

    双通道检测：ss 可能不存在或无权限（此时静默无输出导致误放行），
    因此叠加 bash /dev/tcp 主动连接探测，任一通道报占用即中止。
    """
    plist = "|".join(str(p) for p in ports)
    _, out = _run(ssh, f"ss -lnt 2>/dev/null | awk '{{print $4}}' | grep -E '(:({plist}))$' | head -5", 15, logs)
    busy = [ln for ln in out.splitlines() if any(f":{p}" in ln for p in ports)]
    probe = " ".join(str(p) for p in ports)
    _, probe_out = _run(
        ssh,
        f"for p in {probe}; do if timeout 2 bash -c \"exec 3<>/dev/tcp/127.0.0.1/$p\" 2>/dev/null; then echo BUSY:$p; fi; done",
        20,
        logs,
    )
    for ln in probe_out.splitlines():
        ln = ln.strip()
        if ln.startswith("BUSY:") and ln[5:] not in [b.split(":")[-1] for b in busy]:
            busy.append(f"127.0.0.1:{ln[5:]}")
    if busy:
        raise ProvisionError(f"端口已被占用：{busy}，请先释放端口或改用其他端口/机器")


def host_internal_ip(ssh: Any, logs: list[str]) -> str:
    code, out = _run(ssh, "hostname -I 2>/dev/null | awk '{print $1}'", 15, logs)
    ip = out.strip().splitlines()[-1].strip() if out.strip() else ""
    if not ip:
        raise ProvisionError("无法获取母机内网 IP（hostname -I 为空），external 模式需手工指定可达地址")
    return ip


def upload_stack(ssh: Any, compose: str, env: str, logs: list[str], stack_name: str = STACK_DIR) -> str:
    sftp = ssh.open_sftp()
    try:
        home = sftp.normalize(".")
        target = f"{home}/{stack_name}"
        try:
            sftp.stat(target)
        except FileNotFoundError:
            sftp.mkdir(target)
        for name, content in (("docker-compose.yml", compose), (".env", env)):
            with sftp.open(f"{target}/{name}", "w") as f:
                f.write(content)
        logs.append(f"编排文件已上传 → ~/{stack_name}/")
        return target
    finally:
        sftp.close()


def compose_up_cmd(sudo: str, password: str, target_dir: str, project_name: str) -> str:
    """compose up 命令（绝对路径 cd，root/sudo 通吃）。

    优先用 docker-compose 二进制：老版本 docker（18.06 等）的 `docker compose version`
    退出码为 0 但实际没有 compose 插件，插件可用性必须以输出非空为准。
    project_name 显式指定 compose 项目名，保证同一母机多实例的容器/网络互不冲突。
    """
    return _sudo_wrap(
        sudo,
        password,
        f"cd {target_dir} && if command -v docker-compose >/dev/null 2>&1; then docker-compose -p {project_name} up -d; "
        f"elif docker compose version 2>/dev/null | grep -q .; then docker compose -p {project_name} up -d; "
        "else echo '未找到可用的 compose（docker-compose 或 docker compose 插件）' >&2; exit 127; fi",
    )


def uninstall_stack(ip: str, port: int, username: str, password: str, logs: list[str]) -> None:
    """从母机上卸载全部 Zabbix 实例：停止并移除容器、删除安装目录（含监控数据），不可恢复。

    多实例部署下每个端口一个目录（devops-zabbix / devops-zabbix-<web_port>），逐一清理。
    """
    logs.append(f"连接 {username}@{ip}:{port} 准备卸载 ...")
    ssh = _connect_ssh(ip, port, username, password)
    try:
        sudo = _detect_sudo(ssh, logs)
        _, out = _run(ssh, "echo $HOME", 10, logs)
        home = out.strip().splitlines()[-1].strip() if out.strip() else ""
        if not home:
            raise ProvisionError("无法获取目标机家目录")
        _, out = _run(ssh, f"ls -d {home}/devops-zabbix* 2>/dev/null", 10, logs)
        targets = [ln.strip() for ln in out.splitlines() if ln.strip().startswith(home)]
        if not targets:
            logs.append("未发现已安装的 Zabbix 栈（~/devops-zabbix*），跳过远程卸载")
            return
        for target in targets:
            project = target.rstrip("/").rsplit("/", 1)[-1]
            logs.append(f"卸载 Zabbix 栈 {project}：停止并移除容器 ...")
            _sudo_run(
                ssh,
                sudo,
                password,
                f"cd {target} && if command -v docker-compose >/dev/null 2>&1; then docker-compose -p {project} down -v; "
                f"elif docker compose version 2>/dev/null | grep -q .; then docker compose -p {project} down -v; fi; exit 0",
                180,
                logs,
            )
            # 安装目录（含 mysql-data 监控数据）一并删除；compose down 失败也继续清目录
            _sudo_run(ssh, sudo, password, f"rm -rf {target}", 60, logs)
            _, out = _run(ssh, f"test -d {target} && echo LEFT || echo GONE", 10, logs)
            if "GONE" not in out:
                raise ProvisionError(f"安装目录删除失败（{target}），请登录目标机手工检查")
        logs.append("远程卸载完成：容器已移除，安装目录（含监控数据）已删除")
    finally:
        ssh.close()


def wait_web_ready(web_url: str, logs: list[str]) -> dict:
    """轮询 Zabbix API（apiinfo.version），就绪即返回版本。"""
    import httpx

    logs.append(f"[5/5] 等待 Zabbix Web 就绪（{web_url}，最长 {WEB_READY_TIMEOUT}s）...")
    deadline = time.monotonic() + WEB_READY_TIMEOUT
    last_err = ""
    while time.monotonic() < deadline:
        try:
            r = httpx.post(f"{web_url}/api_jsonrpc.php", json={"jsonrpc": "2.0", "method": "apiinfo.version", "params": {}, "id": 1}, timeout=8)
            data = r.json()
            version = data.get("result")
            if isinstance(version, str) and version:
                return {"version": version}
        except Exception as exc:  # noqa: BLE001
            last_err = str(exc)[:200]
        time.sleep(8)
    raise ProvisionError(f"Zabbix Web 未就绪（超时 {WEB_READY_TIMEOUT}s）：{last_err or '服务仍在导入库表或启动失败'}")


AUTOREG_ACTION_NAME = "DevOps auto registration"
AUTOREG_META_TAG = "devops-auto"  # 与 provision.AUTOREG_META / install-zabbix-agent.sh 保持一致
LINUX_GROUP = "Linux servers"


def _fix_self_monitor_interface(client: Any, logs: list[str]) -> None:
    """修正 Zabbix 自带 "Zabbix server" 主机的 agent 接口地址。

    官方镜像 seed 的该主机 interface 指向 127.0.0.1（假设 agent 与 server 同宿主/同 host 网络），
    但本项目用独立容器部署，agent 在 compose 网络内，server 用 127.0.0.1 拉不到数据（Connection
    refused），导致母机自身监控全为 0。改为容器名 zabbix-agent（compose 网络内 DNS 稳定解析）。
    """
    hosts = client._rpc("host.get", {"filter": {"host": ["Zabbix server"]}, "selectInterfaces": "extend"})
    if not hosts:
        return
    for ifc in hosts[0].get("interfaces") or []:
        if str(ifc.get("type")) != "1":  # 仅处理 agent 接口
            continue
        if str(ifc.get("useip")) == "0" and str(ifc.get("dns")) == "zabbix-agent":
            continue  # 已正确，幂等跳过
        client._rpc(
            "hostinterface.update",
            {"interfaceid": ifc["interfaceid"], "dns": "zabbix-agent", "useip": 0, "ip": "127.0.0.1"},
        )
        logs.append("已修正 Zabbix server 自监控主机的 agent 接口 → zabbix-agent 容器")


def configure_autoreg(client: Any, logs: list[str]) -> None:
    """给全新 Zabbix 库做最小可用初始化（幂等）：自监控接口修正 + 主机组 + Linux 模板 + 自动注册 Action。

    没有这一步，新装库的自动注册不会生效：Agent 装得上但永远注册不进 Zabbix，监控无数据。
    """
    _fix_self_monitor_interface(client, logs)

    groups = client._rpc("hostgroup.get", {"filter": {"name": [LINUX_GROUP]}})
    if groups:
        gid = groups[0]["groupid"]
    else:
        gid = client._rpc("hostgroup.create", {"name": LINUX_GROUP})["groupids"][0]
        logs.append(f"已创建主机组 {LINUX_GROUP}")

    tid = ""
    for name in ("Linux by Zabbix agent", "Template OS Linux by Zabbix agent", "Template OS Linux"):
        tpl = client._rpc("template.get", {"filter": {"host": [name]}})
        if tpl:
            tid = tpl[0]["templateid"]
            break
    if not tid:
        logs.append("警告：未找到 Linux 模板（Linux by Zabbix agent / Template OS Linux），自动注册将不挂模板")

    existing = client._rpc(
        "action.get", {"filter": {"name": [AUTOREG_ACTION_NAME]}, "selectOperations": "extend"}
    )
    if existing:
        ops = list(existing[0].get("operations") or [])
        if tid and not any(op.get("optemplate") for op in ops):
            ops.append({"operationtype": 6, "optemplate": [{"templateid": tid}]})
            client._rpc("action.update", {"actionid": existing[0]["actionid"], "operations": ops})
            logs.append("自动注册 Action 已存在，已补挂 Linux 模板")
        else:
            logs.append("自动注册 Action 已存在，跳过创建")
        return

    operations: list[dict[str, Any]] = [
        {"operationtype": 2},  # add host
        {"operationtype": 4, "opgroup": [{"groupid": gid}]},  # add to group
    ]
    if tid:
        operations.append({"operationtype": 6, "optemplate": [{"templateid": tid}]})  # link template
    client._rpc(
        "action.create",
        {
            "name": AUTOREG_ACTION_NAME,
            "eventsource": 2,  # 自动注册
            "status": 0,
            "filter": {
                "evaltype": 0,
                "conditions": [{"conditiontype": 22, "operator": 2, "value": AUTOREG_META_TAG}],  # metadata like
            },
            "operations": operations,
        },
    )
    logs.append(
        f"已创建自动注册 Action（HostMetadata 含 {AUTOREG_META_TAG} → 加入 {LINUX_GROUP}"
        + ("、关联 Linux 模板）" if tid else "）")
    )


def collect_stack_state(ssh: Any, sudo: str, password: str, target_dir: str, logs: list[str]) -> None:
    """部署失败时收集容器现场（状态 + 最近日志），直接把根源写进部署日志。"""
    logs.append("---- 失败现场：容器状态 ----")
    _sudo_run(
        ssh,
        sudo,
        password,
        "docker ps -a --format '{{.Names}}|{{.Status}}' 2>/dev/null | grep -iE 'zabbix|mysql' | head -8",
        20,
        logs,
    )
    if target_dir:
        logs.append("---- 失败现场：容器最近日志 ----")
        _sudo_run(
            ssh,
            sudo,
            password,
            f"cd {target_dir} && (docker-compose logs --tail 30 2>/dev/null || docker compose logs --tail 30 2>/dev/null) | tail -60",
            40,
            logs,
        )


def deploy_mother(
    asset_id: str,
    *,
    ip: str,
    port: int,
    username: str,
    password: str,
    requested_by: str = "",
    web_port: int = 0,
    trapper_port: int = 0,
) -> None:
    """后台任务：在母机上安装 Zabbix 栈，进度写 assets.extra["deploy"]。

    web_port/trapper_port 为本次要部署实例的端口；传 0 表示沿用母机已登记端口。
    同一母机可按不同端口部署多个实例（独立目录/compose 项目名/端口/数据卷）。
    """
    from app.database import SessionLocal

    logs: list[str] = []
    state: dict[str, Any] = {
        "status": "running",
        "ip": ip,
        "port": port,
        "username": username,
        "requested_by": requested_by,
        "started_at": utcnow().isoformat(),
        "finished_at": "",
        "error": "",
        "version": "",
        "web_url": "",
    }

    def save(finished: bool = False) -> None:
        db = SessionLocal()
        try:
            a = db.get(Asset, asset_id)
            if a is None:
                return
            snap = dict(state)
            snap["logs"] = logs[-300:]
            if finished:
                snap["finished_at"] = utcnow().isoformat()
            a.extra = {**(a.extra or {}), "deploy": snap}
            db.add(a)
            db.commit()
        finally:
            db.close()

    try:
        save()
        db = SessionLocal()
        try:
            asset = db.get(Asset, asset_id)
        finally:
            db.close()
        if asset is None or asset.kind != "mother":
            raise ProvisionError("母机资产不存在")
        zcfg = dict((asset.extra or {}).get("zabbix") or {})
        dbcfg = dict(zcfg.get("db") or {})
        db_mode = asset.db_mode or "bundled"
        web_port = int(web_port or zcfg.get("web_port") or DEFAULT_WEB_PORT)
        trapper_port = int(trapper_port or zcfg.get("trapper_port") or DEFAULT_TRAPPER_PORT)
        stack_name = stack_name_for(web_port)
        instance_key = str(web_port)

        logs.append(f"[1/5] 连接 {username}@{ip}:{port} ...")
        ssh = _connect_ssh(ip, port, username, password)

        # 幂等重试：仅当"本次目标实例"（同端口）已可用才跳过预检/安装/启动；
        # 换端口部署是全新实例，必须走完整安装，否则会误复用第一个实例（重新接管）。
        instances = dict((asset.extra or {}).get("zabbix_instances") or {})
        inst_cfg = dict(instances.get(instance_key) or {})
        web_url = str(inst_cfg.get("url") or f"http://{ip}:{web_port}")
        already_up = False
        if inst_cfg.get("url"):
            try:
                import httpx as _httpx

                _r = _httpx.post(
                    f"{web_url}/api_jsonrpc.php",
                    json={"jsonrpc": "2.0", "method": "apiinfo.version", "params": {}, "id": 1},
                    timeout=5,
                )
                if isinstance(_r.json().get("result"), str) and _r.json().get("result"):
                    already_up = True
                    logs.append(f"检测到实例 {web_port} 已可用，跳过端口检查/安装/启动，直接校验并补齐初始化")
            except Exception:  # noqa: BLE001
                pass

        try:
            sudo = _detect_sudo(ssh, logs)
            target_dir = ""
            if not already_up:
                check_ports_free(ssh, [trapper_port, web_port], logs)
                install_docker_via_ssh(ssh, sudo, password, logs)

            if not already_up:
                db_host = str(dbcfg.get("host") or "")
                if db_mode == "external":
                    if not (db_host and dbcfg.get("database") and dbcfg.get("user")):
                        raise ProvisionError("external 模式缺少数据库信息（host/database/user）")
                    if db_host in ("127.0.0.1", "localhost"):
                        db_host = host_internal_ip(ssh, logs)
                        logs.append(f"外部数据库地址 127.0.0.1 已替换为母机内网 IP：{db_host}")
                    logs.append("[3/5] 渲染编排（external：复用已有 MySQL）...")
                else:
                    logs.append("[3/5] 渲染编排（bundled：独立 MySQL 容器）...")

                compose = render_stack_compose(
                    db_mode=db_mode,
                    db_host=db_host,
                    db_port=int(dbcfg.get("port") or 3306),
                    web_port=web_port,
                    trapper_port=trapper_port,
                )
                env = render_stack_env(generate_db_password(), generate_db_password())
                target_dir = upload_stack(ssh, compose, env, logs, stack_name=stack_name)

                logs.append("[4/5] docker compose up -d（首次拉取镜像较慢，最长 15 分钟）...")
                code, _ = _run(
                    ssh,
                    compose_up_cmd(sudo, password, target_dir, stack_name),
                    COMPOSE_UP_TIMEOUT,
                    logs,
                    secret_hint="启动 Zabbix 栈超时",
                )
                if code != 0:
                    collect_stack_state(ssh, sudo, password, target_dir, logs)
                    raise ProvisionError("容器启动失败（退出码非 0），上方已附容器状态与日志便于定位根源")
        finally:
            ssh.close()

        if not already_up:
            web_url = f"http://{ip}:{web_port}"
        try:
            info = wait_web_ready(web_url, logs)
        except ProvisionError:
            # Web 未就绪：回连母机收集容器现场（镜像拉取失败/MySQL 初始化失败等直接可见）
            try:
                ssh2 = _connect_ssh(ip, port, username, password)
                try:
                    collect_stack_state(ssh2, _detect_sudo(ssh2, logs), password, target_dir, logs)
                finally:
                    ssh2.close()
            except Exception:  # noqa: BLE001
                pass
            raise
        state["version"] = info["version"]
        state["web_url"] = web_url

        # 全新库初始化：自动注册 Action（缺失则新增节点永远注册不进 Zabbix）。
        # 失败仅告警不回滚——栈本身已就绪，可重试 deploy 前先手工处理。
        try:
            from app.integrations.zabbix.http import HttpZabbixClient

            zc = HttpZabbixClient(web_url, username="Admin", password="zabbix", retries=1, readonly=False)
            configure_autoreg(zc, logs)
            state["autoreg_configured"] = True
        except Exception as exc:  # noqa: BLE001
            state["autoreg_configured"] = False
            logs.append(f"警告：自动注册初始化失败（栈已就绪，新增节点前需处理）：{str(exc)[:200]}")

        # 就绪：登记该实例（多实例按端口隔离）；首个成功实例同时写为主实例供监控大盘使用
        db = SessionLocal()
        try:
            a = db.get(Asset, asset_id)
            if a is not None:
                extra = dict(a.extra or {})
                z = dict(extra.get("zabbix") or {})
                instances = dict(extra.get("zabbix_instances") or {})
                inst = {
                    "url": web_url,
                    "user": "Admin",
                    "password": "zabbix",
                    "web_port": web_port,
                    "trapper_port": trapper_port,
                }
                if db_mode == "bundled":
                    inst["db"] = {"mode": "bundled"}
                elif dbcfg:
                    inst["db"] = dict(dbcfg)
                instances[instance_key] = inst
                # 主实例（extra.zabbix）：首次成功部署才写入，避免第二个实例覆盖第一个
                if not z.get("url"):
                    z.update(inst)
                extra["zabbix_instances"] = instances
                extra["zabbix"] = z
                # 登记 SSH 接入信息（ip/port/username），供实时巡检免密采集使用正确用户名
                prov = dict(extra.get("provision") or {})
                prov.update({"ip": ip, "port": port, "username": username})
                extra["provision"] = prov
                a.extra = extra
                a.reachable = True
                db.add(a)
                db.commit()
        finally:
            db.close()

        state["status"] = "success"
        logs.append(f"部署完成：Zabbix {info['version']}，控制台 {web_url}（默认账号 Admin/zabbix）")
    except Exception as exc:  # noqa: BLE001
        state["status"] = "failed"
        state["error"] = str(exc)[:300]
        logs.append(f"失败：{state['error']}")
    finally:
        save(finished=True)

    db = SessionLocal()
    try:
        add_audit(
            db,
            ticket_id=None,
            event_type="mother_deploy",
            actor=requested_by or None,
            result={"asset_id": asset_id, "ip": ip, "status": state["status"], "error": state["error"]},
            params_digest=f"ip={ip},port={port},user={username}",
        )
        db.commit()
    finally:
        db.close()
