"""AI 接入：配置存取 + 通用对话调用。

配置优先级：数据库（界面可改） > 环境变量 > 代码默认值。
这样「AI 兜底分类」和「AI 财务分析」共用同一份配置，配一次两处都能用。

只依赖标准库 urllib，不引入任何 SDK。
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

import db

# 数据库 meta 里的键名 / 对应的环境变量名
META = {"base": "ai_base", "key": "ai_key", "model": "ai_model"}
ENV = {
    "base": "ASSETBOOK_AI_BASE",
    "key": "ASSETBOOK_AI_KEY",
    "model": "ASSETBOOK_AI_MODEL",
}
DEFAULT_MODEL = "gpt-4o-mini"

# 分析类请求比分类慢很多，给足时间
DEFAULT_TIMEOUT = 120


class AIError(RuntimeError):
    """调用模型失败，消息可直接展示给用户。"""


def config() -> dict:
    """读配置。数据库里有值就优先用，否则退回环境变量。"""
    out: dict[str, str] = {}
    for field, meta_key in META.items():
        db_value = (db.get_meta(meta_key) or "").strip()
        out[field] = db_value or os.environ.get(ENV[field], "").strip()
    out["model"] = out["model"] or DEFAULT_MODEL
    return out


def configured() -> bool:
    c = config()
    return bool(c["base"] and c["key"])


def source() -> str:
    """当前配置是从哪来的，界面上给个提示。"""
    if (db.get_meta(META["base"]) or "").strip():
        return "database"
    if os.environ.get(ENV["base"], "").strip():
        return "env"
    return ""


def public() -> dict:
    """给前端看的配置 —— 绝不回显完整 key。"""
    c = config()
    key = c["key"]
    return {
        "base": c["base"],
        "model": c["model"],
        "configured": bool(c["base"] and key),
        "key_set": bool(key),
        "key_hint": ("••••" + key[-4:]) if len(key) >= 4 else ("••••" if key else ""),
        "source": source(),
        "env_locked": not (db.get_meta(META["base"]) or "").strip() and bool(
            os.environ.get(ENV["base"], "").strip()
        ),
    }


def save(base: str | None = None, key: str | None = None,
         model: str | None = None, clear_key: bool = False) -> dict:
    """保存配置。

    - base/model：传了就覆盖（空串等于清空该字段）
    - key：传了非空才覆盖；传空或 None 表示「保持原样」，避免前端留空把 key 抹掉
    - clear_key=True：显式清空 key
    """
    if base is not None:
        db.set_meta(META["base"], base.strip())
    if model is not None:
        db.set_meta(META["model"], model.strip())
    if clear_key:
        db.set_meta(META["key"], "")
    elif key:
        db.set_meta(META["key"], key.strip())
    return public()


def chat(messages: list[dict], *, model: str | None = None,
         temperature: float = 0.3, timeout: int = DEFAULT_TIMEOUT) -> dict:
    """调一次 OpenAI 兼容的 /chat/completions。

    返回 {"content": 文本, "model": 实际模型, "usage": dict|None}。
    失败抛 AIError（消息可直接给用户看）。
    """
    c = config()
    if not c["base"] or not c["key"]:
        raise AIError("还没有配置 AI 接口：去「设置 → AI 分析」填上接口地址和 Key。")

    body = json.dumps({
        "model": model or c["model"],
        "messages": messages,
        "temperature": temperature,
    }, ensure_ascii=False).encode()

    req = urllib.request.Request(
        c["base"].rstrip("/") + "/chat/completions",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {c['key']}",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = json.loads(e.read()).get("error", {}).get("message", "")
        except Exception:
            pass
        if e.code in (401, 403):
            raise AIError(f"AI 接口拒绝了这把 Key（HTTP {e.code}）。检查一下 Key 有没有填错或过期。{detail}") from e
        if e.code == 404:
            raise AIError(f"接口地址不对（HTTP 404）。base 要填到 /v1 这一层，例如 https://api.deepseek.com/v1。{detail}") from e
        if e.code == 429:
            raise AIError(f"AI 接口限流或余额不足（HTTP 429）。{detail}") from e
        raise AIError(f"AI 接口返回 HTTP {e.code}。{detail}") from e
    except urllib.error.URLError as e:
        raise AIError(f"连不上 AI 接口：{e.reason}。检查 base 地址和网络。") from e
    except json.JSONDecodeError as e:
        raise AIError("AI 接口返回的不是合法 JSON，可能填到了错误的地址。") from e

    try:
        content = data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, AttributeError) as e:
        raise AIError("AI 接口返回的格式看不懂，可能不是 OpenAI 兼容接口。") from e

    return {
        "content": content,
        "model": data.get("model") or (model or c["model"]),
        "usage": data.get("usage"),
    }
