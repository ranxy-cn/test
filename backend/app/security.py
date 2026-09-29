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
