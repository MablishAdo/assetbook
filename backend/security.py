"""极简单用户鉴权：密码存 PBKDF2 哈希，会话用 HMAC 签名 Cookie。

设计取舍：这是自托管的个人账本，不做多用户体系，但公网部署必须有门。
- 首次访问时由用户在界面上设置密码（或通过 ASSETBOOK_PASSWORD 环境变量预设）
- 会话令牌 = HMAC(secret, password_hash)，改密码即全体登出
- 令牌有效期 30 天，存 HttpOnly Cookie
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time

from fastapi import Cookie, HTTPException, Response, status

import db

COOKIE_NAME = "assetbook_session"
SESSION_DAYS = 30
_ITERATIONS = 120_000


# ---------- 密码 ----------

def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), _ITERATIONS)
    return f"pbkdf2_sha256${_ITERATIONS}${salt}${digest.hex()}"


def verify_password(password: str, stored: str | None) -> bool:
    if not stored:
        return False
    try:
        _, iters, salt, digest = stored.split("$")
        calc = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(iters))
    except (ValueError, AttributeError):
        return False
    return hmac.compare_digest(calc.hex(), digest)


def has_password() -> bool:
    return db.get_meta("password_hash") is not None


def set_password(password: str) -> None:
    db.set_meta("password_hash", hash_password(password))


# ---------- 会话 ----------

def secret() -> str:
    s = db.get_meta("secret")
    if not s:
        s = os.environ.get("ASSETBOOK_SECRET") or secrets.token_hex(32)
        db.set_meta("secret", s)
    return s


def make_token() -> str:
    payload = db.get_meta("password_hash", "") or ""
    return hmac.new(secret().encode(), payload.encode(), hashlib.sha256).hexdigest()


def token_valid(token: str | None) -> bool:
    if not token or not has_password():
        return False
    return hmac.compare_digest(token, make_token())


def set_session_cookie(response: Response) -> None:
    response.set_cookie(
        COOKIE_NAME,
        make_token(),
        max_age=SESSION_DAYS * 24 * 3600,
        httponly=True,
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/")


def require_auth(session: str | None = Cookie(default=None, alias=COOKIE_NAME)) -> None:
    """依赖注入：保护需要登录的接口。"""
    if not has_password():
        # 还没设密码 = 首次使用，放行初始化的接口
        return
    if not token_valid(session):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="请先登录")


def login_ts() -> int:
    return int(time.time())
