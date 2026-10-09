"""登录 / 首次设置密码 / 登出。"""

from __future__ import annotations

import os

from fastapi import APIRouter, Cookie, HTTPException, Response, status

import db
import security
from models import LoginIn, SetupIn

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.get("/state")
def state(session: str | None = Cookie(default=None, alias=security.COOKIE_NAME)):
    return {
        "has_password": security.has_password(),
        "authed": security.token_valid(session),
        "version": db.get_meta("app_version", "1.0.0"),
    }


@router.post("/setup")
def setup(payload: SetupIn, response: Response):
    if security.has_password():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "已经设置过密码了")
    security.set_password(payload.password)
    security.set_session_cookie(response)
    return {"ok": True}


@router.post("/login")
def login(payload: LoginIn, response: Response):
    if not security.has_password():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "还没设置密码，请先初始化")
    if not security.verify_password(payload.password, db.get_meta("password_hash")):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "密码不对")
    security.set_session_cookie(response)
    return {"ok": True}


@router.post("/logout")
def logout(response: Response):
    security.clear_session_cookie(response)
    return {"ok": True}


@router.get("/bootstrap")
def bootstrap():
    """首次启动时如果环境变量里带了密码，直接初始化，方便 Docker 一键部署。"""
    preset = os.environ.get("ASSETBOOK_PASSWORD")
    if preset and not security.has_password():
        security.set_password(preset)
        return {"initialized": True}
    return {"initialized": False}
