-- 0015: 资产 SSH 密码加密落库（assets.ssh_password_enc）
-- 与 app/models.py Asset.ssh_password_enc、alembic 0015 保持一致；可手工执行（幂等不适用，MySQL 8 无 ADD COLUMN IF NOT EXISTS，重复执行会报 1060）。

ALTER TABLE `assets`
  ADD COLUMN `ssh_password_enc` varchar(512) NOT NULL DEFAULT '' COMMENT 'SSH 密码密文（Fernet；配置入口：新增/编辑子机页面）';
