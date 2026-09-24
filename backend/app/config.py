from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "mysql+pymysql://devops_agent:devops_agent@127.0.0.1:3306/devops_agent?charset=utf8mb4"
    redis_url: str = "redis://localhost:6379/0"
    webhook_secret: str = "dev-webhook-secret"
    # Zabbix webhook 是否继续自动立案（AI 诊断/策略/预案）。
    # 默认关闭：只记录异常条目（异常/恢复两态），供「异常告警」页展示。
    webhook_auto_ticket: bool = False
    use_celery: bool = False
    demo_mode: bool = True

    # ===== 登录鉴权 / RBAC =====
    # JWT 签名密钥：生产必须通过环境变量设置（>=32 字符随机串）；
    # 未设置时每次进程启动随机生成（重启后所有 token 失效，并打印告警）。
    jwt_secret: str = ""
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 720
    # 登录防爆破：连续失败 lockout_max_attempts 次后锁定 lockout_minutes 分钟
    lockout_max_attempts: int = 5
    lockout_minutes: int = 15
    # 同一 IP 窗口期内最大失败次数（超过返回 429）
    ip_fail_window_minutes: int = 10
    ip_fail_max: int = 30
    # 初始管理员密码（首次 seed 时使用），生产必须通过环境变量覆盖
    admin_initial_password: str = "Admin@123456"
    # 是否种入演示资产/维护窗口（订单系统等示例数据）；
    # 生产环境设 SEED_DEMO_ASSETS=0 只保留真实登记的资产
    seed_demo_assets: bool = True
    # 资产页「新增节点」一键纳管：目标机 agent 指向的 Zabbix Server（IP/域名）。
    # 为空时自动从 ZABBIX_URL 解析 host；两者都没有则需在表单里手填
    provision_zabbix_server: str = ""

    observation_seconds: float = 10.0
    probe_required_passes: int = 3
    probe_interval_seconds: float = 1.0
    restart_cooldown_seconds: int = 1800
    action_fail_cooldown_seconds: int = 1800
    lock_ttl_seconds: int = 120

    integration_mode: str = "mock"
    zabbix_mode: str = ""
    ansible_mode: str = ""
    vault_mode: str = ""

    zabbix_url: str = ""
    zabbix_token: str = ""
    zabbix_user: str = ""
    zabbix_password: str = ""
    zabbix_verify_ssl: bool = True
    zabbix_timeout_seconds: float = 8.0
    zabbix_retries: int = 2
    vault_addr: str = ""
    vault_token: str = ""
    ansible_runner_enabled: bool = False
    ansible_private_data_dir: str = "/tmp/ansible-runner"
    ansible_inventory: str = ""
    ansible_roles_path: str = ""
    ansible_ssh_private_key_file: str = ""
    ansible_ssh_user: str = "devops"
    ansible_ssh_port: int = 22
    ansible_check_mode: bool = False
    ansible_timeout_seconds: int = 120
    ansible_host_key_checking: bool = True
    notify_webhook_url: str = ""
    # 真实 CPU 压测工具：仅服务器部署（有真实 Zabbix）时打开
    stress_tools_enabled: bool = False
    stress_max_seconds: int = 1200
    # 异常诊断上机采集的 SSH 密码兜底（优先用资产 extra.provision.password）
    diag_ssh_password: str = ""

    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o-mini"

    policy_version: str = "policy-v1.0.0"
    mock_model_version: str = "mock-diagnoser-v1"
    tenant_id: str = "tenant-default"
    employee_id: str = "DE-OPS-001"
    # 母机（Zabbix Server + Agent 所在的接入机器）资产 ID，总览页以它为根展示子机拓扑
    mother_asset_id: str = "devops-mother-186"

    playbooks_dir: Path = Path("/app/playbooks")
    knowledge_dir: Path = Path("/app/knowledge")
    # PLAYBOOKS_DIR / KNOWLEDGE_DIR env vars override the defaults.

    def resolved_playbooks_dir(self) -> Path:
        if self.playbooks_dir.exists():
            return self.playbooks_dir
        here = Path(__file__).resolve().parents[2] / "playbooks"
        if here.exists():
            return here
        return Path(__file__).resolve().parents[1].parent / "playbooks"

    def resolved_knowledge_dir(self) -> Path:
        if self.knowledge_dir.exists():
            return self.knowledge_dir
        here = Path(__file__).resolve().parents[2] / "knowledge"
        if here.exists():
            return here
        return Path(__file__).resolve().parents[1].parent / "knowledge"


@lru_cache
def get_settings() -> Settings:
    return Settings()
