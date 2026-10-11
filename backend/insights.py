"""AI 财务分析 —— 把你账本里的真实数字交给模型，换回一段说人话的分析。

流程：
    1. snapshot()  把账本压成一份小而全的数据包（只放数字和名字，省 token）
    2. build_prompt() 拼成中文财务顾问的提示词
    3. analyze()   调模型，结果缓存进 meta 表（同一个月份不重复烧钱）

缓存策略：按月份缓存。账本有新流水时标记 stale，界面提示「建议重新生成」，
但不会自动重跑 —— 花钱的事让用户点头。
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
    """账本内容的粗指纹：流水条数 + 最大 id + 金额合计。

    新增、删掉、改金额都会让指纹变；只改备注不会 —— 对「分析该不该重跑」够用了。
    用 created_at 不行：那里存的是日期，同一天记两笔看不出变化。
    """
    row = db.q1(
        "SELECT COUNT(*) AS n, COALESCE(MAX(id), 0) AS mx, "
        "COALESCE(SUM(amount), 0) AS s FROM transactions"
    )
    return f"{row['n']}:{row['mx']}:{round(float(row['s']), 2)}"


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

    # 账户：资产与负债分开列
    accts = svc.accounts_with_balance()
    assets = [{"名称": a["name"], "类型": a["kind_label"], "余额": round(a["balance"], 2)}
              for a in accts if not a["is_liability"] and abs(a["balance"]) >= 0.01]
    debts = [{"名称": a["name"], "欠款": round(-a["balance"], 2)}
             for a in accts if a["is_liability"] and abs(a["balance"]) >= 0.01]

    # 持仓：市值 + 盈亏
    holds = sorted(svc.holdings_with_value(), key=lambda h: -h["market_value"])
    hold_out = [
        {"名称": h["name"], "类型": h["kind_label"], "市值": round(h["market_value"], 2),
         "盈亏": round(h["profit"], 2), "盈亏率": f"{h['profit_pct']}%"}
        for h in holds[:TOP_HOLDINGS] if h["market_value"] > 0
    ]

    return {
        "月份": month,
        "总览": {
            "净资产": s["net"], "总资产": s["assets"], "总负债": s["liabilities"],
            "投资市值": s["invest"], "流动资产": s["liquid"],
            "本月收入": s["month_in"], "本月支出": s["month_out"], "本月结余": s["month_net"],
            "储蓄率": f"{s['savings_rate']}%",
            "近3个完整月平均支出": s["avg_month_expense"],
            "应急金可支撑月数": s["emergency_months"],
        },
        "近几个月收支": [{"月份": t["month"], "收入": t["in"], "支出": t["out"], "结余": t["net"]}
                         for t in trend],
        "本月支出构成": top_cats,
        "预算执行": budget_out,
        "资产账户": assets,
        "负债账户": debts,
        "持仓": hold_out,
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


def analyze(month: str | None = None, force: bool = False) -> dict:
    """生成（或读取缓存）分析结果。"""
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
