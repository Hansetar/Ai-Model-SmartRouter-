"""
core/auth.py
============
JWT 认证与 API Key 验证模块。

提供：
- 管理面板登录认证（JWT Token）
- /v1 接口的 API Key 认证
- 密码哈希与验证（使用 hashlib + hmac，避免 passlib 兼容性问题）
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from typing import Optional

from jose import JWTError, jwt

from .config import config


# JWT 配置
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_SECONDS = 86400 * 7  # 7 天


def _hash_password(password: str) -> str:
    """使用 PBKDF2-HMAC-SHA256 哈希密码。"""
    salt = "smartrouter-password-salt"
    iterations = 100000
    dk = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"),
        salt.encode("utf-8"), iterations
    )
    return dk.hex()


def _verify_password_hash(plain_password: str, password_hash: str) -> bool:
    """验证密码哈希。"""
    computed = _hash_password(plain_password)
    return hmac.compare_digest(computed, password_hash)


def _get_jwt_secret() -> str:
    """获取 JWT 签名密钥，优先从环境变量读取。"""
    secret = os.environ.get("SMARTROUTER_JWT_SECRET")
    if not secret:
        # 从 admin 密码派生，保证每次启动一致
        admin_pwd = config.get("admin_password", "admin")
        secret = f"smartrouter-{admin_pwd}-jwt-secret-key"
    return secret


def verify_password(plain_password: str) -> bool:
    """验证管理员密码。"""
    admin_pwd = config.get("admin_password", "admin")
    return hmac.compare_digest(plain_password, admin_pwd)


def create_access_token(subject: str = "admin") -> str:
    """创建 JWT Token。"""
    expire = time.time() + JWT_EXPIRE_SECONDS
    payload = {"sub": subject, "exp": expire}
    return jwt.encode(payload, _get_jwt_secret(), algorithm=JWT_ALGORITHM)


def verify_token(token: str) -> Optional[dict]:
    """验证 JWT Token，返回 payload 或 None。"""
    try:
        payload = jwt.decode(token, _get_jwt_secret(), algorithms=[JWT_ALGORITHM])
        if payload.get("exp", 0) < time.time():
            return None
        return payload
    except JWTError:
        return None


def verify_api_key(api_key: str) -> bool:
    """验证 /v1 接口的 API Key。

    如果配置了 api_key，则必须匹配；未配置则放行。
    """
    configured_key = config.get("api_key", "")
    if not configured_key:
        # 未配置 API Key，允许所有请求
        return True
    return secrets.compare_digest(api_key, configured_key)


def is_api_key_configured() -> bool:
    """检查是否配置了 API Key 认证。"""
    return bool(config.get("api_key", ""))
