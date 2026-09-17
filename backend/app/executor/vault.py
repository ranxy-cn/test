"""兼容一期导入：MockVault 现为适配层 MockVaultClient。"""

from app.integrations.vault.mock import MockVaultClient as MockVault

__all__ = ["MockVault"]
