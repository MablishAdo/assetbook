"""AssetBook · 记账与投资 —— 应用入口。

本地开发：
    uvicorn main:app --reload --app-dir backend
Docker：
    docker compose up -d
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import db
import scheduler
from routes import assets, auth, data

APP_VERSION = "2.0.0"
STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(_: FastAPI):
    """启动时建表、按环境变量预设密码、记录当日快照，并拉起后台自动同步。"""
    db.init_db()
    db.set_meta("app_version", APP_VERSION)
    preset = os.environ.get("ASSETBOOK_PASSWORD")
    if preset:
        import security

        if not security.has_password():
            security.set_password(preset)
    db.snapshot_portfolio()
    scheduler.scheduler.start()
    try:
        yield
    finally:
        await scheduler.scheduler.stop()


app = FastAPI(
    title="AssetBook API",
    description="记账与投资 · 自托管 · 数据完全归你",
    version=APP_VERSION,
    docs_url="/api/docs",
    redoc_url=None,
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)

# 本地开发前端可能跑在别的端口；生产同源部署不需要它
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("ASSETBOOK_CORS", "http://localhost:5173").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(auth.router)
app.include_router(data.router)
app.include_router(assets.router)


@app.get("/healthz", include_in_schema=False)
def healthz():
    return {"ok": True, "version": APP_VERSION}


@app.get("/", include_in_schema=False)
def index():
    """index.html 动态注入版本号做缓存穿透。

    页面里静态资源都带 `?v=版本号`，每次发版 URL 就变了，浏览器自然重新拉，
    不会再出现「页面是新的、脚本是旧的」导致按钮点不动的情况。
    """
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    return Response(
        content=html.replace("__ASSETBOOK_VERSION__", APP_VERSION),
        media_type="text/html; charset=utf-8",
        headers={"Cache-Control": "no-cache"},
    )


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    icon = STATIC_DIR / "favicon.svg"
    if icon.exists():
        return FileResponse(icon, media_type="image/svg+xml")
    return JSONResponse({}, status_code=204)


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
