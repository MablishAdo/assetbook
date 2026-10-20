"""业务计算：记账收支、持仓收益、预算执行、报表、自动分类。

所有计算都从流水与持仓实时推导，不存冗余数字，避免对不上账。
账户（银行卡余额）功能已在 v2.0 整体移除。
"""

from __future__ import annotations

import time
from datetime import date, datetime, timedelta

import ai
import db
import jev
import quotes

HOLDING_LABELS = {
    "fund": "基金", "stock": "股票", "bond": "债券",
    "gold": "黄金", "cash": "现金类", "other": "其他",
}

def today() -> str:
    return date.today().isoformat()


def month_of(d: str) -> str:
    return d[:7]


# ---------- 持仓 ----------

def holdings_with_value() -> list[dict]:
    rows = db.q("SELECT * FROM holdings ORDER BY kind, id")
    out = []
    for r in rows:
        d = dict(r)
        d["market_value"] = round(d["units"] * d["price"], 2)
        d["cost_value"] = round(d["units"] * d["cost_price"], 2)
        d["profit"] = round(d["market_value"] - d["cost_value"], 2)
        d["profit_pct"] = round(d["profit"] / d["cost_value"] * 100, 2) if d["cost_value"] else 0.0
        d["kind_label"] = HOLDING_LABELS.get(d["kind"], d["kind"])
        d["market"] = d.get("market") or "auto"
        d["market_label"] = quotes.MARKETS.get(d["market"], d["market"])
        out.append(d)
    return out


def holdings_totals() -> dict:
    """持仓总市值 / 总成本 / 总盈亏。"""
    items = holdings_with_value()
    market = round(sum(h["market_value"] for h in items), 2)
    cost = round(sum(h["cost_value"] for h in items), 2)
    profit = round(market - cost, 2)
    return {
        "market_value": market,
        "cost_value": cost,
        "profit": profit,
        "profit_pct": round(profit / cost * 100, 2) if cost else 0.0,
        "count": len(items),
        "winners": sum(1 for h in items if h["profit"] > 0),
        "losers": sum(1 for h in items if h["profit"] < 0),
    }


def holdings_total() -> float:
    return holdings_totals()["market_value"]


def holdings_by_kind() -> list[dict]:
    """按品种聚合并给出占比 —— 配置集中度就靠这个。"""
    buckets: dict[str, dict] = {}
    for h in holdings_with_value():
        key = h["kind_label"]
        b = buckets.setdefault(key, {"kind": h["kind"], "name": key,
                                     "market_value": 0.0, "cost_value": 0.0})
        b["market_value"] += h["market_value"]
        b["cost_value"] += h["cost_value"]
    total = sum(b["market_value"] for b in buckets.values()) or 1
    out = []
    for b in buckets.values():
        out.append({
            "kind": b["kind"], "name": b["name"],
            "market_value": round(b["market_value"], 2),
            "cost_value": round(b["cost_value"], 2),
            "profit": round(b["market_value"] - b["cost_value"], 2),
            "ratio": round(b["market_value"] / total * 100, 1),
        })
    return sorted(out, key=lambda x: -x["market_value"])


# ---------- 记账概览 ----------

def compute_summary() -> dict:
    m = month_of(today())
    row = db.q1(
        """
        SELECT
          COALESCE(SUM(CASE WHEN type = 'in'  THEN amount END), 0) AS mi,
          COALESCE(SUM(CASE WHEN type = 'out' THEN amount END), 0) AS mo
        FROM transactions WHERE substr(date, 1, 7) = ?
        """,
        (m,),
    )
    month_in, month_out = float(row["mi"]), float(row["mo"])

    # 近 3 个完整月平均支出
    avg_expense = float(db.q1(
        """
        SELECT COALESCE(AVG(s), 0) AS a FROM (
          SELECT SUM(amount) AS s FROM transactions
          WHERE type = 'out' AND substr(date, 1, 7) < ?
          GROUP BY substr(date, 1, 7) ORDER BY substr(date, 1, 7) DESC LIMIT 3
        )
        """,
        (m,),
    )["a"] or 0)

    return {
        "month": m,
        "month_in": round(month_in, 2),
        "month_out": round(month_out, 2),
        "month_net": round(month_in - month_out, 2),
        "savings_rate": round((month_in - month_out) / month_in * 100, 1) if month_in else 0.0,
        "avg_month_expense": round(avg_expense, 2),
        "invest": holdings_totals(),
    }


def allocation() -> list[dict]:
    """资产配置：只按持仓品种聚合（账户已下线）。"""
    return holdings_by_kind()


def month_category_spend(month: str) -> list[dict]:
    rows = db.q(
        """
        SELECT COALESCE(c.name, '未分类') AS name, COALESCE(c.color, '#6f6a5e') AS color,
               SUM(t.amount) AS amount
        FROM transactions t LEFT JOIN categories c ON c.id = t.category_id
        WHERE t.type = 'out' AND substr(t.date, 1, 7) = ?
        GROUP BY t.category_id ORDER BY amount DESC
        """,
        (month,),
    )
    return [dict(r) for r in rows]


def portfolio_series_30d() -> list[dict]:
    """近 30 天投资市值曲线。"""
    return _portfolio_series(30, by_day=True)


def portfolio_series_months(months: int = 12) -> list[dict]:
    return _portfolio_series(months, by_day=False)


def _portfolio_series(n: int, by_day: bool) -> list[dict]:
    rows = {r["date"]: r for r in db.q("SELECT * FROM portfolio_history ORDER BY date")}
    if by_day:
        keys, day = [], date.today() - timedelta(days=n - 1)
        for _ in range(n):
            keys.append(day.isoformat())
            day += timedelta(days=1)
    else:
        y, m = date.today().year, date.today().month
        keys = []
        for _ in range(n):
            keys.append(f"{y:04d}-{m:02d}")
            m -= 1
            if m == 0:
                y, m = y - 1, 12
        keys.reverse()

    field = "date" if by_day else "month"
    out, last = [], 0.0
    for k in keys:
        hit = rows.get(k)
        if hit:
            last = hit["market_value"]
        out.append({field: k, "net": round(last, 2)})
    return out


def monthly_report(months: int = 12) -> list[dict]:
    rows = db.q(
        """
        SELECT substr(date, 1, 7) AS month,
               SUM(CASE WHEN type = 'in'  THEN amount END) AS mi,
               SUM(CASE WHEN type = 'out' THEN amount END) AS mo
        FROM transactions GROUP BY month ORDER BY month DESC LIMIT ?
        """,
        (months,),
    )
    return [
        {"month": r["month"], "in": round(float(r["mi"] or 0), 2),
         "out": round(float(r["mo"] or 0), 2),
         "net": round(float(r["mi"] or 0) - float(r["mo"] or 0), 2)}
        for r in rows
    ][::-1]


# ---------- 预算 ----------

def budget_report(month: str) -> list[dict]:
    rows = db.q(
        """
        SELECT b.id, b.month, b.amount, b.category_id, c.name, c.color
        FROM budgets b JOIN categories c ON c.id = b.category_id
        WHERE b.month IN (?, '*') ORDER BY c.sort, c.id
        """,
        (month,),
    )
    spends = {r["category_id"]: float(r["amount"]) for r in db.q(
        """SELECT category_id, SUM(amount) AS amount FROM transactions
           WHERE type = 'out' AND substr(date, 1, 7) = ? AND category_id IS NOT NULL
           GROUP BY category_id""",
        (month,),
    )}
    used: dict[int, float] = {}
    out = []
    for r in rows:
        cid = r["category_id"]
        amount = float(r["amount"])
        spent = spends.get(cid, 0.0)
        if r["month"] == "*" and cid in used:
            continue
        used[cid] = amount
        out.append({
            "id": r["id"], "month": r["month"], "category_id": cid, "name": r["name"],
            "color": r["color"], "budget": round(amount, 2), "spent": round(spent, 2),
            "remaining": round(amount - spent, 2),
            "pct": round(spent / amount * 100, 1) if amount else 0.0,
        })
    return out


# ---------- 自动分类 ----------

def classify_by_rules(note: str) -> int | None:
    if not note:
        return None
    text = note.strip()
    for r in db.q(
        "SELECT keyword, category_id FROM rules WHERE enabled = 1 ORDER BY priority, id",
    ):
        if r["keyword"] and r["keyword"] in text:
            return r["category_id"]
    return None


# Jev 分类缓存：同一段备注不重复问；连续失败就停一会儿，别让记账卡在网络里
_jev_cache: dict[str, int | None] = {}
_jev_block_until = 0.0
JEV_FAIL_COOLDOWN = 120        # 失败后停用多久（秒）
JEV_TIMEOUT = 8                # 单次调用超时


def reset_jev_state() -> None:
    """配置变了就清掉分类缓存和熔断状态 —— 换了新 Key 应该立刻生效，
    不该被上一把 Key 留下的「停用 120 秒」拖住。"""
    global _jev_block_until
    _jev_block_until = 0.0
    _jev_cache.clear()


def jev_classify(note: str, tx_type: str) -> int | None:
    """用 Jev 的 choice 原语选分类 —— 比对话模型快、稳、几乎免费。

    没配 Jev、连不上、超时，一律只返回 None，绝不抛异常打扰记账。
    """
    global _jev_block_until
    if not note or not jev.configured():
        return None
    if time.monotonic() < _jev_block_until:
        return None

    cache_key = f"{tx_type}:{note}"
    if cache_key in _jev_cache:
        return _jev_cache[cache_key]

    cats = db.q("SELECT id, name FROM categories WHERE kind = ?",
                ("income" if tx_type == "in" else "expense",))
    if not cats:
        return None

    what = "收入" if tx_type == "in" else "支出"
    criteria = {c["name"]: f"备注反映的{what}属于「{c['name']}」" for c in cats}
    try:
        res = jev.choose(
            state=f"一笔{what}的记账备注：{note}",
            instructions=f"这笔{what}最该归到哪个记账分类",
            criteria=criteria,
            timeout=JEV_TIMEOUT,
        )
    except jev.JevError:
        _jev_block_until = time.monotonic() + JEV_FAIL_COOLDOWN
        return None

    picked = (res.get("choice") or "").strip()
    cid = next((c["id"] for c in cats if c["name"] == picked), None)
    if cid is None:                       # 名字没完全对上时退一步做包含匹配
        cid = next((c["id"] for c in cats if picked and picked in c["name"]), None)
    _jev_cache[cache_key] = cid
    return cid


def ai_classify(note: str, tx_type: str) -> int | None:
    """可选的对话模型兜底分类：配置了 OpenAI 兼容接口才会生效，否则返回 None。

    配置来自 ai.config()：数据库里的「设置 → AI 分析」优先，其次环境变量
    ASSETBOOK_AI_BASE / KEY / MODEL。
    """
    if not note or not ai.configured():
        return None
    cats = db.q("SELECT id, name FROM categories WHERE kind = ?",
                ("income" if tx_type == "in" else "expense",))
    if not cats:
        return None
    names = [c["name"] for c in cats]
    prompt = (
        f"从这些记账分类里选一个最合适的，只回分类名，不要解释：{', '.join(names)}\n"
        f"交易备注：{note}"
    )
    try:
        answer = ai.chat([{"role": "user", "content": prompt}],
                         temperature=0, timeout=20)["content"]
    except ai.AIError:
        return None
    for c in cats:
        if c["name"] in answer:
            return c["id"]
    return None


def classify(note: str, tx_type: str, use_ai: bool = False) -> tuple[int | None, str]:
    """分类链路：本地规则 → Jev 快通道 → 对话模型兜底。

    Jev 只要在设置里配了 Key 就自动生效（决策模型，快且几乎免费）；
    对话模型要显式传 use_ai 才调，避免每一次记账都等一次慢请求。
    返回值第二项标明是谁定的：rule / jev / ai / none。
    """
    cid = classify_by_rules(note)
    if cid:
        return cid, "rule"
    cid = jev_classify(note, tx_type)
    if cid:
        return cid, "jev"
    if use_ai:
        cid = ai_classify(note, tx_type)
        if cid:
            return cid, "ai"
    return None, "none"


# ---------- CSV 导入 ----------

COLUMN_ALIASES = {
    "date": ["交易时间", "交易创建时间", "交易日期", "日期", "时间", "date", "time", "记账日期"],
    "amount": ["金额", "金额(元)", "交易金额", "发生金额", "amount", "money", "收支金额"],
    "type": ["收/支", "收支", "类型", "交易类型", "type", "方向"],
    "note": ["交易对方", "商品说明", "商品", "备注", "摘要", "交易说明", "说明", "note", "memo", "desc", "description"],
    "account": ["支付方式", "账户", "account", "付款方式"],
    "category": ["交易分类", "分类", "类别", "category"],
}

INCOME_HINTS = ("收入", "收", "入账", "退款")


def normalize_rows(rows: list[dict]) -> tuple[list[dict], list[str]]:
    """把任意表头的中文账单映射成统一字段，返回 (标准化行, 警告)。"""
    if not rows:
        return [], ["文件里没有数据行"]
    headers = list(rows[0].keys())
    mapping: dict[str, str] = {}
    for field, aliases in COLUMN_ALIASES.items():
        for h in headers:
            key = (h or "").strip()
            if key in aliases or any(a in key for a in aliases):
                mapping[field] = h
                break
    warnings = []
    if "date" not in mapping:
        warnings.append("没识别出日期列，已用今天代替")
    if "amount" not in mapping:
        warnings.append("没识别出金额列，该文件无法导入")
    out = []
    for r in rows:
        raw_amount = str(r.get(mapping.get("amount", ""), "")).replace("¥", "").replace(",", "").strip()
        if raw_amount in ("", "-", "--"):
            continue
        try:
            amount = abs(float(raw_amount))
        except ValueError:
            continue
        if amount == 0:
            continue
        raw_type = str(r.get(mapping.get("type", ""), "")).strip()
        if any(h in raw_type for h in INCOME_HINTS):
            tx_type = "in"
        elif raw_type in ("支出", "支", "付款", "转出"):
            tx_type = "out"
        else:
            # 没有方向列时按金额正负判断
            tx_type = "out"
        raw_date = str(r.get(mapping.get("date", ""), "")).strip()
        d = raw_date[:10].replace("/", "-")
        note_parts = [str(r.get(mapping.get("note", ""), "") or "").strip()]
        if mapping.get("account"):
            note_parts.append(str(r.get(mapping["account"], "") or "").strip())
        out.append({
            "date": d if len(d) == 10 else today(),
            "type": tx_type,
            "amount": round(amount, 2),
            "note": " ".join(p for p in note_parts if p)[:120],
            "category_name": str(r.get(mapping.get("category", ""), "") or "").strip(),
        })
    return out, warnings


def read_csv_rows(path: str) -> list[dict]:
    """读 CSV，自动尝试 utf-8-sig / gbk。"""
    import csv

    for enc in ("utf-8-sig", "utf-8", "gbk"):
        try:
            with open(path, newline="", encoding=enc) as f:
                sample = f.read(4096)
                f.seek(0)
                if not sample.strip():
                    return []
                # 支付宝/微信账单前面常有几行说明，找到真正的表头行
                lines = sample.splitlines()
                skip = 0
                for i, line in enumerate(lines[:30]):
                    if line.count(",") >= 2:
                        skip = i
                        break
                for _ in range(skip):
                    f.readline()
                return list(csv.DictReader(f))
        except (UnicodeDecodeError, LookupError):
            continue
    return []


# ---------- 行情 / 汇率自动更新 ----------

RATES_KEY = "rates_cache"


def refresh_quotes(holding_ids: list[int] | None = None) -> dict:
    """把所有填了代码的持仓现价刷一遍，写回数据库。

    返回 {"total", "ok", "updated": [...], "failed": [{code, name, error}], "at"}
    批量接口能一次拉多个沪深/港股标的；场外基金和加密货币逐个拉。
    """
    rows = db.q(
        "SELECT id, code, name, kind, market FROM holdings "
        "WHERE code IS NOT NULL AND TRIM(code) <> '' ORDER BY id"
    )
    if holding_ids:
        wanted = set(holding_ids)
        rows = [r for r in rows if r["id"] in wanted]

    batch: list[tuple] = []       # (row, market) 可以走批量
    singles: list = []            # 需要逐个试的
    for r in rows:
        m = (r["market"] or "auto").strip() or "auto"
        if m in ("cn", "hk"):
            batch.append((r, m))
        elif m in ("fund", "crypto"):
            singles.append(r)
        else:                                          # auto：能归到批量就先归
            guessed = quotes.guess_market(r["code"], r["kind"])
            if guessed in ("cn", "hk"):
                batch.append((r, guessed))
            else:
                singles.append(r)

    hits: dict[int, dict] = {}
    failed: list[dict] = []

    if batch:
        try:
            got = quotes.fetch_market([(m, r["code"]) for r, m in batch])
        except Exception as e:                          # noqa: BLE001
            got = {}
            failed.append({"code": "-", "name": "批量行情", "error": str(e)})
        for r, _m in batch:
            q = got.get(r["code"].strip().upper())
            if q:
                hits[r["id"]] = q
                continue
            # 批量没命中的 auto 持仓可能其实是场外基金，单独再试一次
            if (r["market"] or "auto") == "auto":
                try:
                    hits[r["id"]] = quotes.fetch_one("auto", r["code"], r["kind"])
                    continue
                except Exception as e:                  # noqa: BLE001
                    err = str(e)
            else:
                err = "查不到行情"
            failed.append({"code": r["code"], "name": r["name"], "error": err})

    for r in singles:
        try:
            hits[r["id"]] = quotes.fetch_one(r["market"] or "auto", r["code"], r["kind"])
        except Exception as e:                          # noqa: BLE001
            failed.append({"code": r["code"], "name": r["name"], "error": str(e)})

    updated = []
    if hits:
        with db.tx() as conn:
            for hid, q in hits.items():
                conn.execute(
                    "UPDATE holdings SET price = ?, price_date = ? WHERE id = ?",
                    (q["price"], q["date"], hid),
                )
                updated.append({
                    "id": hid,
                    "price": q["price"],
                    "date": q["date"],
                    "market": q.get("market"),
                    "name": q.get("name"),
                })
    return {
        "total": len(rows),
        "ok": len(hits),
        "updated": updated,
        "failed": failed,
        "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def refresh_rates() -> dict:
    """拉最新汇率并存进 meta，返回 {"base","rates","date","updated"}"""
    import json

    info = quotes.fetch_rates("CNY")
    info["updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    db.set_meta(RATES_KEY, json.dumps(info, ensure_ascii=False))
    return info


def get_rates(auto_fetch: bool = True) -> dict:
    """读缓存的汇率；没有缓存且 auto_fetch 时现拉一次。"""
    import json

    raw = db.get_meta(RATES_KEY)
    if raw:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass
    if auto_fetch:
        try:
            return refresh_rates()
        except Exception:                               # noqa: BLE001
            pass
    return {"base": "CNY", "rates": {"CNY": 1.0}, "date": None, "updated": None}
