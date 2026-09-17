"""兼容一期导入：MockAnsibleRunner 现为适配层 MockPlaybookRunner。"""

from app.integrations.ansible.mock import MockPlaybookRunner as MockAnsibleRunner

__all__ = ["MockAnsibleRunner"]
