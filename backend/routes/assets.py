"""持仓、预算、概览、报表、行情与自动同步（资产管理视角）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

import db
import quotes
import scheduler as sched
import security
import services as svc
from models import AutoSyncIn, BudgetIn, HoldingIn, HoldingPatch, PriceIn

router = APIRouter(prefix="/api", tags=["assets"], dependencies=[Depends(security.require_auth)])


# ---------- 元信息 ----------

@router.get("/meta")
def meta():
    """前端下拉要用的枚举：市场、账户类型、持仓类型。"""
    return {
        "markets": [{"value": k, "label": v} for k, v in quotes.MARKETS.items()],
        "holding_kinds": svc.HOLDING_LABELS,
        "account_kinds": svc.ACCOUNT_LABELS,
    }


# ---------- 概览 ----------

@router.get("/overview")
def overview():
    s = svc.compute_summary()
    return {
        **s,
        "accounts": svc.accounts_with_balance(),
        "allocation": svc.allocation(),
        "month_categories": svc.month_category_spend(s["month"]),
        "networth_30d": svc.networth_series_30d(),
        "networth_12m": svc.networth_series_months(12),
        "top_holdings": sorted(svc.holdings_with_value(), key=lambda h: -h["market_value"])[:5],
        "rates": svc.get_rates(auto_fetch=False),
        "auto": {"enabled": sched.scheduler.status()["enabled"],
                 "last_quote_at": sched.scheduler.status()["last_quote_at"]},
    }


# ---------- 持仓 ----------

@router.get("/holdings")
def list_holdings():
    items = svc.holdings_with_value()
    return {
        "items": items,
        "total_market": round(sum(h["market_value"] for h in items), 2),
        "total_cost": round(sum(h["cost_value"] for h in items), 2),
        "total_profit": round(sum(h["profit"] for h in items), 2),
    }


@router.post("/holdings", status_code=201)
def create_holding(payload: HoldingIn):
    price, price_date = payload.price, payload.price_date
    name = payload.name

    # 填了代码就让行情接口顺手补个现价（失败不影响创建）
    if payload.fetch_price and payload.code:
        try:
            q = quotes.fetch_one(payload.market, payload.code, payload.kind)
            price, price_date = q["price"], q["date"]
            if not name.strip() and q.get("name"):
                name = q["name"]
        except quotes.QuoteError:
            pass

    cur = db.execute(
        "INSERT INTO holdings (code, name, kind, market, account_id, units, cost_price, "
        "price, price_date, note) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (payload.code, name, payload.kind, payload.market, payload.account_id, payload.units,
         payload.cost_price, price or payload.cost_price, price_date or svc.today(), payload.note),
    )
    db.snapshot_networth()
    return {"id": cur.lastrowid}


@router.patch("/holdings/{holding_id}")
def update_holding(holding_id: int, payload: HoldingPatch):
    row = db.q1("SELECT * FROM holdings WHERE id = ?", (holding_id,))
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "没这个持仓")

    fields = payload.model_dump(exclude_none=True)
    if not fields:
        return {"ok": True}
    sets = ", ".join(f"{k} = ?" for k in fields)
    db.execute(f"UPDATE holdings SET {sets} WHERE id = ?", (*fields.values(), holding_id))
    db.snapshot_networth()
    return {"ok": True}


@router.post("/holdings/{holding_id}/price")
def update_price(holding_id: int, payload: PriceIn):
    """手动指定现价；不传 price 就自动去行情接口拉一次。"""
    row = db.q1("SELECT * FROM holdings WHERE id = ?", (holding_id,))
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "没这个持仓")

    if payload.price > 0:
        price, pdate = payload.price, payload.price_date or svc.today()
    else:
        if not (row["code"] or "").strip():
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "这个持仓没填代码，拉不了行情")
        try:
            q = quotes.fetch_one(row["market"] or "auto", row["code"], row["kind"])
        except quotes.QuoteError as e:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e)) from e
        price, pdate = q["price"], q["date"]

    db.execute(
        "UPDATE holdings SET price = ?, price_date = ? WHERE id = ?",
        (price, pdate, holding_id),
    )
    db.snapshot_networth()
    return {"ok": True, "price": price, "price_date": pdate}


@router.delete("/holdings/{holding_id}", status_code=204)
def delete_holding(holding_id: int):
    db.execute("DELETE FROM holdings WHERE id = ?", (holding_id,))
    db.snapshot_networth()
    return None


# ---------- 行情与汇率 ----------

@router.post("/quotes/refresh")
async def refresh_quotes():
    """手动刷一次所有持仓现价。"""
    result = await sched.scheduler.run_now("quotes")
    return result


@router.get("/quotes/preview")
def preview_quote(code: str, market: str = "auto", kind: str = "stock"):
    """新增持仓前先探一下代码能不能拉到、叫什么名字。"""
    try:
        return {"ok": True, **quotes.fetch_one(market, code, kind)}
    except quotes.QuoteError as e:
        return {"ok": False, "error": str(e)}


@router.get("/rates")
def rates():
    return svc.get_rates()


@router.post("/rates/refresh")
def refresh_rates():
    try:
        return svc.refresh_rates()
    except quotes.QuoteError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e)) from e


# ---------- 自动同步 ----------

@router.get("/auto/status")
def auto_status():
    return sched.scheduler.status()


@router.put("/auto/settings")
def auto_settings(payload: AutoSyncIn):
    data = payload.model_dump(exclude_none=True)
    if "enabled" in data:
        sched.set_setting("auto_sync", "1" if data.pop("enabled") else "0")
    for key, value in data.items():
        sched.set_setting(key, value)
    out = sched.scheduler.status()
    sched.scheduler._note("system", "自动同步设置已更新")      # noqa: SLF001 — 就是记一条日志
    return out


@router.post("/auto/run/{what}")
async def auto_run(what: str):
    if what not in ("quotes", "rates", "networth", "inbox", "mail"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "只支持 quotes / rates / networth / inbox / mail")
    return await sched.scheduler.run_now(what)


# ---------- 预算 ----------

@router.get("/budgets")
def list_budgets(month: str | None = None):
    month = month or svc.month_of(svc.today())
    return {"month": month, "items": svc.budget_report(month)}


@router.post("/budgets")
def upsert_budget(payload: BudgetIn):
    db.execute(
        "INSERT INTO budgets (month, category_id, amount) VALUES (?, ?, ?) "
        "ON CONFLICT (month, category_id) DO UPDATE SET amount = excluded.amount",
        (payload.month, payload.category_id, payload.amount),
    )
    return {"ok": True}


@router.delete("/budgets/{budget_id}", status_code=204)
def delete_budget(budget_id: int):
    db.execute("DELETE FROM budgets WHERE id = ?", (budget_id,))
    return None


# ---------- 净值与报表 ----------

@router.get("/networth")
def networth(days: int = 30, months: int = 12):
    return {
        "daily": svc.networth_series_30d() if days else [],
        "monthly": svc.networth_series_months(months),
    }


@router.get("/reports/monthly")
def reports(months: int = 12):
    return {"items": svc.monthly_report(months)}


@router.post("/networth/snapshot")
def take_snapshot():
    db.snapshot_networth()
    return {"ok": True}


# ---------- 健康检查（不含鉴权，供 Docker healthcheck 用） ----------

@router.get("/ping")
def ping():
    return {"ok": True}
