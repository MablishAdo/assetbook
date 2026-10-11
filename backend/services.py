"""业务计算：账户余额、净资产、持仓收益、预算执行、报表、自动分类。

所有计算都从流水实时推导，不存冗余余额，避免对不上账。
账户余额 = 基准余额 + 之后的收入 − 支出 + 转入 − 转出。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import ai
import db
import quotes

LIABILITY_KINDS = {"credit", "loan"}
LIQUID_KINDS = {"cash", "bank", "payment"}
HOLDING_LABELS = {
    "fund": "基金", "stock": "股票", "bond": "债券",
    "gold": "黄金", "cash": "现金类", "other": "其他",
}
ACCOUNT_LABELS = {
    "cash": "现金", "bank": "银行卡", "payment": "支付账户",
    "broker": "券商", "credit": "信用卡", "loan": "贷款", "other": "其他",
}


def today() -> str:
    return date.today().isoformat()


def month_of(d: str) -> str:
    return d[:7]


# ---------- 账户 ----------

def accounts_with_balance(include_archived: bool = False) -> list[dict]:
    where = "" if include_archived else "WHERE a.archived = 0"
    rows = db.q(
        f"""
        SELECT a.*,
               a.base_balance
               + COALESCE((SELECT SUM(amount) FROM transactions t
                           WHERE t.account_id = a.id AND t.type = 'in'), 0)
               - COALESCE((SELECT SUM(amount) FROM transactions t
                           WHERE t.account_id = a.id AND t.type = 'out'), 0)
               + COALESCE((SELECT SUM(amount) FROM transactions t
                           WHERE t.to_account_id = a.id AND t.type = 'transfer'), 0)
               - COALESCE((SELECT SUM(amount) FROM transactions t
                           WHERE t.account_id = a.id AND t.type = 'transfer'), 0)
               AS balance
        FROM accounts a
        {where}
        ORDER BY a.sort, a.id
        """,
    )
    out = []
    for r in rows:
        d = dict(r)
        d["kind_label"] = ACCOUNT_LABELS.get(d["kind"], d["kind"])
        d["is_liability"] = d["kind"] in LIABILITY_KINDS
        out.append(d)
    return out


def account_balance(account_id: int) -> float:
    for a in accounts_with_balance(include_archived=True):
        if a["id"] == account_id:
            return a["balance"]
    return 0.0


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


def holdings_total() -> float:
    return round(sum(h["market_value"] for h in holdings_with_value()), 2)


# ---------- 净资产 ----------

def compute_summary() -> dict:
    accts = accounts_with_balance()
    assets = sum(a["balance"] for a in accts if not a["is_liability"])
    # 负债账户余额为负（欠款），对外展示为正数「欠款额」；净值 = 资产 + 负债余额
    liabilities = -sum(a["balance"] for a in accts if a["is_liability"])
    invest = holdings_total()
    assets += invest
    liquid = sum(a["balance"] for a in accts if a["kind"] in LIQUID_KINDS and not a["is_liability"])

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

    # 近 3 个完整月平均支出 → 应急金可支撑月数
    avg_expense = db.q1(
        """
        SELECT COALESCE(AVG(s), 0) AS a FROM (
          SELECT SUM(amount) AS s FROM transactions
          WHERE type = 'out' AND substr(date, 1, 7) < ?
          GROUP BY substr(date, 1, 7) ORDER BY substr(date, 1, 7) DESC LIMIT 3
        )
        """,
        (m,),
    )
    avg_expense = float(avg_expense["a"] or 0)

    return {
        "assets": round(assets, 2),
        "liabilities": round(liabilities, 2),
        "net": round(assets - liabilities, 2),
        "invest": invest,
        "liquid": round(liquid, 2),
        "month": m,
        "month_in": round(month_in, 2),
        "month_out": round(month_out, 2),
        "month_net": round(month_in - month_out, 2),
        "savings_rate": round((month_in - month_out) / month_in * 100, 1) if month_in else 0.0,
        "avg_month_expense": round(avg_expense, 2),
        "emergency_months": round(liquid / avg_expense, 1) if avg_expense else 0.0,
    }


def allocation() -> list[dict]:
    """资产配置：账户按类型聚合 + 持仓按品种聚合。"""
    buckets: dict[str, float] = {}
    for a in accounts_with_balance():
        if a["is_liability"] or a["balance"] <= 0:
            continue
        buckets[a["kind_label"]] = buckets.get(a["kind_label"], 0) + a["balance"]
    for h in holdings_with_value():
        if h["market_value"] <= 0:
            continue
        key = f"持仓·{h['kind_label']}"
        buckets[key] = buckets.get(key, 0) + h["market_value"]
    total = sum(buckets.values()) or 1
    items = [{"name": k, "amount": round(v, 2), "ratio": round(v / total * 100, 1)}
             for k, v in sorted(buckets.items(), key=lambda kv: -kv[1])]
    return items


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


def networth_series_30d() -> list[dict]:
    start = (date.today() - timedelta(days=29)).isoformat()
    rows = {r["date"]: r for r in db.q("SELECT * FROM networth ORDER BY date")}
    series, last = [], 0.0
    for r in db.q("SELECT * FROM networth WHERE date < ? ORDER BY date DESC LIMIT 1", (start,)):
        last = r["net"]
    day = date.today() - timedelta(days=29)
    for _ in range(30):
        key = day.isoformat()
        if key in rows:
            last = rows[key]["net"]
        series.append({"date": key, "net": round(last, 2)})
        day += timedelta(days=1)
    return series


def networth_series_months(months: int = 12) -> list[dict]:
    rows = db.q("SELECT * FROM networth ORDER BY date")
    by_month: dict[str, float] = {}
    for r in rows:
        by_month[month_of(r["date"])] = r["net"]
    out, cursor, last = [], date.today().replace(day=1), 0.0
    # 往前推 months 个月
    months_list = []
    y, m = cursor.year, cursor.month
    for _ in range(months):
        months_list.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    for key in reversed(months_list):
        if key in by_month:
            last = by_month[key]
        out.append({"month": key, "net": round(last, 2)})
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


def ai_classify(note: str, tx_type: str) -> int | None:
    """可选的 AI 兜底分类：配置了 OpenAI 兼容接口才会生效，否则返回 None。

    配置来自 ai.config()：数据库里的「设置 → AI 分析」优先，其次环境变量
    ASSETBOOK_AI_BASE / KEY / MODEL。
    """
    if not note or not ai.configured():
        return None
    cats = db.q("SELECT id, name FROM categories WHERE kind = ?",
                ("income" if tx_type == "in" else "expense"))
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
    cid = classify_by_rules(note)
    if cid:
        return cid, "rule"
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
