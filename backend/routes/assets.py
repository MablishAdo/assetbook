"""持仓、预算、概览、报表（资产管理视角）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

import db
import security
import services as svc
from models import BudgetIn, HoldingIn, PriceIn

router = APIRouter(prefix="/api", tags=["assets"], dependencies=[Depends(security.require_auth)])


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
    cur = db.execute(
        "INSERT INTO holdings (code, name, kind, account_id, units, cost_price, price, price_date, note) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (payload.code, payload.name, payload.kind, payload.account_id, payload.units,
         payload.cost_price, payload.price or payload.cost_price, payload.price_date or svc.today(),
         payload.note),
    )
    db.snapshot_networth()
    return {"id": cur.lastrowid}


@router.patch("/holdings/{holding_id}")
def update_holding(holding_id: int, payload: HoldingIn):
    db.execute(
        "UPDATE holdings SET code = ?, name = ?, kind = ?, account_id = ?, units = ?, "
        "cost_price = ?, price = ?, price_date = ?, note = ? WHERE id = ?",
        (payload.code, payload.name, payload.kind, payload.account_id, payload.units,
         payload.cost_price, payload.price, payload.price_date or svc.today(), payload.note, holding_id),
    )
    db.snapshot_networth()
    return {"ok": True}


@router.post("/holdings/{holding_id}/price")
def update_price(holding_id: int, payload: PriceIn):
    """更新最新价（手动填，或由脚本/爬虫调用 API 批量推）。"""
    row = db.q1("SELECT id FROM holdings WHERE id = ?", (holding_id,))
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "没这个持仓")
    db.execute(
        "UPDATE holdings SET price = ?, price_date = ? WHERE id = ?",
        (payload.price, payload.price_date or svc.today(), holding_id),
    )
    db.snapshot_networth()
    return {"ok": True}


@router.delete("/holdings/{holding_id}", status_code=204)
def delete_holding(holding_id: int):
    db.execute("DELETE FROM holdings WHERE id = ?", (holding_id,))
    db.snapshot_networth()
    return None


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
