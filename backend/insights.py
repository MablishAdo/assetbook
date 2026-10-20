"""AI 分析 —— 把真实数字交给模型，换回说人话的判断。

两条独立的分析线：
    1. 记账分析 spending_analyze()   —— 这个月钱花在哪、该不该省
    2. 持仓分析 portfolio_analyze()  —— 哪些该加仓、哪些该减仓

流程都一样：snapshot() 压成数据包 → build_prompt() → 调模型 → 缓存进 meta。
缓存按 key 分桶（spending-<月份> / portfolio-<日期>），数据变了标 stale 提示重算，
但不自动重跑 —— 花钱的事让用户点头。
"""

from __future__ import annotations

import json
from datetime import datetime

import ai
import db
import services as svc

CACHE_PREFIX = "insight_"

# 快照取几个月的趋势
TREND_MONTHS = 6
# 分类支出取前几名
TOP_CATEGORIES = 8
# 持仓取前几名
TOP_HOLDINGS = 8


# ---------------- 快照 ----------------

def _data_fingerprint() -> str:
    """账本 + 持仓的粗指纹。

    新增、删掉、改金额都会让指纹变；只改备注不会 —— 对「分析该不该重跑」够用了。
    用 created_at 不行：那里存的是日期，同一天记两笔看不出变化。
    持仓的现价与份额也要算进来：价格一变，涨跌幅和配置诊断全变。
    """
    tx = db.q1(
        "SELECT COUNT(*) AS n, COALESCE(MAX(id), 0) AS mx, "
        "COALESCE(SUM(amount), 0) AS s FROM transactions"
    )
    hd = db.q1(
        "SELECT COUNT(*) AS n, COALESCE(SUM(units * price), 0) AS v FROM holdings"
    )
    return (f"{tx['n']}:{tx['mx']}:{round(float(tx['s']), 2)}"
            f"|{hd['n']}:{round(float(hd['v']), 2)}")


def has_data() -> bool:
    """账本里有没有东西可分析。只有流水才算数 —— 光有账户名分析不出什么。"""
    return bool(db.q1("SELECT COUNT(*) AS n FROM transactions")["n"])


def snapshot(month: str | None = None) -> dict:
    """把账本压成一份给模型看的精简数据包。"""
    s = svc.compute_summary()
    month = month or s["month"]

    # 分月收支（含本月），从旧到新
    trend = svc.monthly_report(TREND_MONTHS)

    # 本月分类支出
    cats = svc.month_category_spend(month)
    cat_total = sum(c["amount"] for c in cats) or 1
    top_cats = [
        {"分类": c["name"], "金额": round(c["amount"], 2),
         "占比": round(c["amount"] / cat_total * 100, 1)}
        for c in cats[:TOP_CATEGORIES]
    ]

    # 预算执行：只报有预算的，超支的排前面
    budgets = svc.budget_report(month)
    budget_rows = sorted(budgets, key=lambda b: -b["pct"])
    budget_out = [
        {"分类": b["name"], "预算": b["budget"], "已花": b["spent"],
         "用掉": f"{b['pct']}%", "状态": "超支" if b["pct"] > 100 else ("接近上限" if b["pct"] > 80 else "正常")}
        for b in budget_rows[:12]
    ]

    # 持仓：市值 + 盈亏（记账分析只需要概览，不需要逐只）
    holds = sorted(svc.holdings_with_value(), key=lambda h: -h["market_value"])
    inv = s["invest"]

    return {
        "月份": month,
        "总览": {
            "投资市值": inv["market_value"], "投资成本": inv["cost_value"],
            "投资盈亏": inv["profit"], "投资盈亏率": f"{inv['profit_pct']}%",
            "持仓只数": inv["count"],
            "本月收入": s["month_in"], "本月支出": s["month_out"], "本月结余": s["month_net"],
            "储蓄率": f"{s['savings_rate']}%",
            "近3个完整月平均支出": s["avg_month_expense"],
        },
        "近几个月收支": [{"月份": t["month"], "收入": t["in"], "支出": t["out"], "结余": t["net"]}
                         for t in trend],
        "本月支出构成": top_cats,
        "预算执行": budget_out,
        "持仓种类": [{"品种": k["name"], "市值": k["market_value"], "占比": f"{k['ratio']}%"}
                     for k in svc.holdings_by_kind()],
    }


# ---------------- 提示词 ----------------

SYSTEM_PROMPT = """你是一位务实的个人财务顾问，服务对象是会记账、想省钱、有房贷的普通人。

你会收到一份真实的账本数据（单位：人民币元）。请严格按下面的要求输出。

输出格式（Markdown，四段，顺序固定）：

## 一句话总结
用一句话点出这个月最值得注意的事。

## 钱花在哪了
2-4 句。指出最大的开销类别、和上个月相比的变化、有没有异常或突然变多的地方。

## 风险提醒
2-4 句。看预算有没有超、应急金够不够、负债压力大不大、储蓄率是否偏低。没有问题就直说没有问题。

## 接下来做什么
2-3 条能立刻执行的具体动作。要给出对象和数字，例如「把餐饮预算从 520 压到 450」，
不要写「要节约开支」这种空话。

硬性规则：
- 只用给出的数据说话。数据里没有的，绝对不要编造，也不要用「可能」「大概」去猜。
- 提到的每个数字都必须能在数据里找到。
- 金额写成 ¥1,234 的样子；超过一万可以用「约 1.2 万」。
- 数据里没有的板块（比如没有持仓、没有预算）直接跳过，不要硬凑。
- 全文控制在 500 字以内。不要寒暄，不要说「作为 AI」，不要重复数据本身。"""


def build_prompt(snap: dict) -> list[dict]:
    data = json.dumps(snap, ensure_ascii=False, separators=(",", ":"))
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"这是账本数据：\n{data}"},
    ]


# ---------------- 生成与缓存 ----------------

def _cache_key(month: str) -> str:
    return f"{CACHE_PREFIX}{month}"


def cached(month: str | None = None) -> dict | None:
    month = month or svc.month_of(svc.today())
    raw = db.get_meta(_cache_key(month))
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    # 账本在本月生成之后又有新流水 → 标个「有点旧」
    data["stale"] = bool(data.get("data_ts")) and data["data_ts"] != _data_fingerprint()
    return data


def spending_analyze(month: str | None = None, force: bool = False) -> dict:
    """生成（或读取缓存）记账分析结果。"""
    month = month or svc.month_of(svc.today())

    if not force:
        hit = cached(month)
        if hit:
            return {**hit, "cached": True}

    if not has_data():
        raise ai.AIError("账本还是空的，先记几笔或者导入账单，再来看分析。")

    snap = snapshot(month)
    result = ai.chat(build_prompt(snap), temperature=0.3)
    now = datetime.now().isoformat(timespec="seconds")
    payload = {
        "month": month,
        "content": result["content"],
        "model": result["model"],
        "usage": result.get("usage"),
        "at": now,
        "data_ts": _data_fingerprint(),
        "stale": False,
    }
    db.set_meta(_cache_key(month), json.dumps(payload, ensure_ascii=False))
    return {**payload, "cached": False}
