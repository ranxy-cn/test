from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./devops_agent.db"
    redis_url: str = "redis://localhost:6379/0"
    webhook_secret: str = "dev-webhook-secret"
    use_celery: bool = False
    demo_mode: bool = True

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

    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o-mini"

    policy_version: str = "policy-v1.0.0"
    mock_model_version: str = "mock-diagnoser-v1"
    tenant_id: str = "tenant-default"
    employee_id: str = "DE-OPS-001"

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
