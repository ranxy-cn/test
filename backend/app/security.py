"""密码哈希、密码策略与 JWT 签发/校验（登录鉴权核心）。"""

from __future__ import annotations

import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from app.config import get_settings

JWT_ISSUER = "devops-agent"
JWT_AUDIENCE = "devops-web"

USERNAME_RE = re.compile(r"^[a-zA-Z0-9_.-]{3,32}$")


def jwt_secret() -> str:
    secret = get_settings().jwt_secret
    if secret:
        return secret
    # 未配置时退化为进程级随机密钥：所有 token 在进程重启后失效（宁可失效也不伪造）
    if not hasattr(jwt_secret, "_fallback"):
        jwt_secret._fallback = secrets.token_urlsafe(48)  # type: ignore[attr-defined]
    return jwt_secret._fallback  # type: ignore[attr-defined]


# ===== 密码 =====


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        return False


def validate_password(plain: str) -> list[str]:
    """密码策略：8-64 位，必须含大写、小写、数字。返回错误列表（空=通过）。"""
    errors: list[str] = []
    if not (8 <= len(plain) <= 64):
        errors.append("密码长度必须为 8-64 位")
    if not re.search(r"[A-Z]", plain):
        errors.append("密码必须包含大写字母")
    if not re.search(r"[a-z]", plain):
        errors.append("密码必须包含小写字母")
    if not re.search(r"\d", plain):
        errors.append("密码必须包含数字")
    return errors


def validate_username(username: str) -> list[str]:
    if not USERNAME_RE.match(username):
        return ["用户名须为 3-32 位字母/数字/._-"]
    return []


# ===== 可逆加密（资产 SSH 密码等落库密文，密钥口径与 AI 配置加密一致） =====

_FALLBACK_RAW_KEY = "devops-agent-fallback-key"


def _secret_raw_candidates() -> list[str]:
    """候选原始密钥（有序去重）：SECRET_ENCRYPT_KEY（专用，推荐）→ JWT_SECRET → 内置兜底。

    历史上密文可能由旧口径（JWT_SECRET 或兜底常量）加密；解密时逐个候选尝试，
    避免「改过 JWT_SECRET / 本地与服务器共用同一数据库但密钥环境不同」时静默解密失败。
    """
    s = get_settings()
    candidates: list[str] = []
    for raw in (getattr(s, "secret_encrypt_key", ""), s.jwt_secret):
        raw = (raw or "").strip()
        if raw and raw not in candidates:
            candidates.append(raw)
    # 内置兜底常量永远排在最后（历史默认口径的密文仍可解）
    if _FALLBACK_RAW_KEY not in candidates:
        candidates.append(_FALLBACK_RAW_KEY)
    return candidates


def _secret_fernets() -> list:
    import base64
    import hashlib

    from cryptography.fernet import Fernet

    return [
        Fernet(base64.urlsafe_b64encode(hashlib.sha256(raw.encode()).digest()))
        for raw in _secret_raw_candidates()
    ]


def _secret_fernet():
    """加密用主密钥（候选列表第一个）。"""
    return _secret_fernets()[0]


def encrypt_secret(plain: str) -> str:
    """明文加密为可落库密文；空串原样返回（表示未配置）。"""
    if not plain:
        return ""
    return _secret_fernet().encrypt(plain.encode("utf-8")).decode("utf-8")


def decrypt_secret(enc: str) -> str:
    """密文解密为明文；空串原样返回。全部候选密钥均失败抛 InvalidToken，由调用方兜底。"""
    if not enc:
        return ""
    from cryptography.fernet import InvalidToken

    last_exc: Exception | None = None
    for fernet in _secret_fernets():
        try:
            return fernet.decrypt(enc.encode("utf-8")).decode("utf-8")
        except InvalidToken as exc:  # noqa: PERF203 密钥不匹配则继续尝试下一候选
            last_exc = exc
    assert last_exc is not None
    raise last_exc


# ===== JWT =====


def create_access_token(user_id: int, ip: str = "", user_agent: str = "") -> tuple[str, str, datetime]:
    """签发访问令牌。返回 (token, jti, expires_at)。"""
    settings = get_settings()
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=settings.access_token_expire_minutes)
    jti = str(uuid.uuid4())
    payload = {
        "sub": str(user_id),
        "jti": jti,
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
        "iss": JWT_ISSUER,
        "aud": JWT_AUDIENCE,
    }
    token = jwt.encode(payload, jwt_secret(), algorithm=settings.jwt_algorithm)
    return token, jti, expires_at


class TokenError(Exception):
    """token 无效（过期/签名错误/吊销等），由调用方转成 401。"""


def decode_access_token(token: str) -> dict:
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            jwt_secret(),
            algorithms=[settings.jwt_algorithm],
            audience=JWT_AUDIENCE,
            issuer=JWT_ISSUER,
            leeway=5,
        )
    except jwt.PyJWTError as exc:
        raise TokenError(str(exc)) from exc
    if payload.get("jti") is None or payload.get("sub") is None:
        raise TokenError("token 缺少必要声明")
    return payload
