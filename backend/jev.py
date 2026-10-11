"""TypeSafe Jev（System One 决策模型）接入。

和 ai.py 的分工：
  ai.py  —— 对话模型，生成文字。用于财务分析、备注兜底分类。
  jev.py —— 决策模型，**不生成文字**，直接回结构化字段。用于「选一个分类」
            「这件事成不成立」「按这个标准打几分」这类判断。

三个原语（一次请求可以混着问，并行求值）：
  noul   —— 一句话真假，回 0~1 的概率
  choice —— 从 criteria 里选一个，回选择 + 各选项概率 + 置信度
  score  —— 按 criteria 打分，回期望分 + 概率分布 + 置信度

配置优先级和 ai.py 一致：数据库（界面可改） > 环境变量 > 代码默认值。
只依赖标准库 urllib。
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

import db

META = {"key": "jev_key", "model": "jev_model"}
# 环境变量：JEV_API_KEY 优先，TYPESAFE_API_KEY 是官方文档里的写法
ENV_KEYS = ("JEV_API_KEY", "TYPESAFE_API_KEY")
ENV_MODEL = "JEV_MODEL"
ENV_BASE = "JEV_BASE"

DEFAULT_BASE = "https://api.typesafe.ai"
DEFAULT_MODEL = "jev-latest"
DEFAULT_TIMEOUT = 25


class JevError(RuntimeError):
    """调用 Jev 失败，消息可直接展示给用户。"""


def _env_key() -> str:
    for name in ENV_KEYS:
        value = (os.environ.get(name) or "").strip()
        if value:
            return value
    return ""


def config() -> dict:
    """读配置。数据库里有值就优先用，否则退回环境变量。"""
    key = (db.get_meta(META["key"]) or "").strip() or _env_key()
    model = (db.get_meta(META["model"]) or "").strip() or (os.environ.get(ENV_MODEL) or "").strip()
    base = (os.environ.get(ENV_BASE) or "").strip() or DEFAULT_BASE
    return {"base": base, "key": key, "model": model or DEFAULT_MODEL}


def configured() -> bool:
    return bool(config()["key"])


def source() -> str:
    """当前配置是从哪来的，界面上给个提示。"""
    if (db.get_meta(META["key"]) or "").strip():
        return "database"
    if _env_key():
        return "env"
    return ""


def public() -> dict:
    """给前端看的配置 —— 绝不回显完整 key。"""
    c = config()
    key = c["key"]
    return {
        "base": c["base"],
        "model": c["model"],
        "configured": bool(key),
        "key_set": bool(key),
        "key_hint": ("••••" + key[-4:]) if len(key) >= 4 else ("••••" if key else ""),
        "source": source(),
    }


def save(model: str | None = None, key: str | None = None,
         clear_key: bool = False) -> dict:
    """保存配置。key 传空 = 保持原样，clear_key=True 才清空。"""
    if model is not None:
        db.set_meta(META["model"], model.strip())
    if clear_key:
        db.set_meta(META["key"], "")
    elif key:
        db.set_meta(META["key"], key.strip())
    return public()


def ask(state, questions: dict, *, model: str | None = None,
        timeout: int = DEFAULT_TIMEOUT) -> dict:
    """调一次 POST /v1/systemone。

    state     —— 所有问题共同参照的内容，字符串或对象
    questions —— {名字: {"type": "noul"|"choice"|"score", "instructions": ..., "criteria": ...}}

    返回 {"model", "answers", "usage"}；失败抛 JevError。
    """
    c = config()
    if not c["key"]:
        raise JevError("还没有配置 Jev：去「设置 → AI 分析 → Jev 快通道」填上 Key。")

    body = json.dumps({
        "model": model or c["model"],
        "state": state,
        "questions": questions,
    }, ensure_ascii=False).encode()

    req = urllib.request.Request(
        c["base"].rstrip("/") + "/v1/systemone",
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
            payload = json.loads(e.read())
            raw = payload.get("detail") if isinstance(payload, dict) else None
            if raw is None and isinstance(payload, dict):
                raw = payload.get("error", {}).get("message", "")
            detail = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)
        except Exception:                                   # noqa: BLE001
            pass
        if e.code in (401, 403):
            raise JevError(f"Jev 拒绝了这把 Key（HTTP {e.code}）。检查 Key 有没有填错或过期。{detail}") from e
        if e.code == 422:
            raise JevError(f"问题格式不对（HTTP 422）。{detail}") from e
        if e.code == 429:
            raise JevError(f"Jev 限流或额度用尽（HTTP 429）。{detail}") from e
        raise JevError(f"Jev 返回 HTTP {e.code}。{detail}") from e
    except urllib.error.URLError as e:
        raise JevError(f"连不上 Jev：{e.reason}。检查网络。") from e
    except json.JSONDecodeError as e:
        raise JevError("Jev 返回的不是合法 JSON，可能被中间的代理改了。") from e

    if not isinstance(data, dict) or not isinstance(data.get("answers"), dict):
        raise JevError("Jev 返回的格式看不懂。")
    return data


def _answer(res: dict, name: str = "q") -> dict:
    ans = res.get("answers", {}).get(name)
    if not isinstance(ans, dict):
        raise JevError(f"Jev 没回「{name}」这个问题的答案。")
    return ans


def noul(state, instructions: str, criteria: dict | None = None, **kw) -> float:
    """问一句「这句话成立吗」，回 0~1。"""
    q: dict = {"type": "noul", "instructions": instructions}
    if criteria:
        q["criteria"] = criteria
    ans = _answer(ask(state, {"q": q}, **kw))
    return round(float(ans.get("noul") or 0.0), 4)


def choose(state, instructions: str, criteria: dict, **kw) -> dict:
    """给几个选项，让它选一个。criteria 是 {选项名: 什么情况选它}，必填。"""
    ans = _answer(ask(state, {"q": {
        "type": "choice", "instructions": instructions, "criteria": criteria,
    }}, **kw))
    return {
        "choice": ans.get("choice"),
        "confidence": round(float(ans.get("confidence") or 0.0), 4),
        "probabilities": ans.get("probabilities") or {},
    }


def score(state, instructions: str, criteria: list, **kw) -> dict:
    """给一套有序评分标准（下标即分值），让它打分。"""
    ans = _answer(ask(state, {"q": {
        "type": "score", "instructions": instructions, "criteria": criteria,
    }}, **kw))
    return {
        "score": float(ans.get("score") or 0.0),
        "confidence": round(float(ans.get("confidence") or 0.0), 4),
        "probabilities": ans.get("probabilities") or {},
        "legend": ans.get("legend") or {},
    }


def sample(timeout: int = 20) -> dict:
    """跑一次真实往返，给界面上的「测试连接」按钮用。"""
    res = ask(
        "记账备注：8 月 3 日 美团外卖 38 元",
        {"cat": {
            "type": "choice",
            "instructions": "这笔支出最该归到哪个记账分类",
            "criteria": {
                "餐饮": "吃饭、外卖、生鲜买菜",
                "交通": "打车、地铁、加油",
                "购物": "买东西、日用品",
            },
        }},
        timeout=timeout,
    )
    ans = _answer(res, "cat")
    return {
        "ok": True,
        "model": res.get("model"),
        "choice": ans.get("choice"),
        "confidence": ans.get("confidence"),
        "usage": res.get("usage"),
    }
