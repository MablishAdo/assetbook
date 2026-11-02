"""快捷交易 —— 把一句话变成一笔买卖，并合并到持仓里。

核心规则（加权平均成本法）：

  买入合并   新份额 = 原份额 + 买入份额
             新成本 = (原份额 × 原成本 + 买入份额 × 成交价) ÷ 新份额
  卖出       份额减少，成本价不变（加权平均法下卖出不改变单位成本）
             已实现盈亏 = (成交价 − 成本价) × 卖出份额

每一笔都会记进 holding_trades 表，卖出才知道当时赚没赚。
"""

from __future__ import annotations

from datetime import date, datetime

import db
import quotes
import trade_parser

# 场外基金申购费默认值：查不到费率时按 0.12% 估算
FUND_FEE_DEFAULT = 0.0012

# 场外基金按金额买入时的取整方式
_LOT_SIZES = {"cn": 100}          # A 股 / 场内基金：100 股一手


class TradeError(Exception):
    """交易无法执行 —— 消息直接给用户看。"""


# ---------------------------------------------------------------- 工具

def _now() -> str:
    return date.today().isoformat()


def _round4(x: float) -> float:
    return round(x + 0.0, 4)


def _find_holding(code: str | None, name_hint: str | None) -> dict | None:
    """按代码找已有持仓；没代码就按名字模糊找。"""
    if code:
        row = db.q1(
            "SELECT * FROM holdings WHERE UPPER(TRIM(code)) = UPPER(?) ORDER BY id LIMIT 1",
            (code.strip(),),
        )
        if row:
            return dict(row)
    if name_hint:
        row = db.q1(
            "SELECT * FROM holdings WHERE name LIKE ? ORDER BY id LIMIT 1",
            (f"%{name_hint}%",),
        )
        if row:
            return dict(row)
    return None


def _holding_by_id(holding_id: int | None) -> dict | None:
    """按 id 取持仓 —— 预演和落库必须操作同一条，不能各查各的。"""
    if not holding_id:
        return None
    row = db.q1("SELECT * FROM holdings WHERE id = ?", (holding_id,))
    return dict(row) if row else None


def _kind_from_market(market: str) -> str:
    return {"fund": "fund", "crypto": "other"}.get(market, "stock")


def _quote(market: str, code: str, kind: str) -> dict | None:
    """拉一次行情，失败返回 None（不阻断录入）。"""
    try:
        return quotes.fetch_one(market or "auto", code, kind)
    except quotes.QuoteError:
        return None
    except Exception:                      # noqa: BLE001 — 网络层什么错都可能来
        return None


def _units_from_amount(amount: float, price: float, market: str, code: str) -> tuple[float, float]:
    """按金额算份额。返回 (份额, 含费成本价)。

    场外基金要扣申购费：份额 = 金额 ×(1−费率)÷ 净值，
    成本价则含进去 —— 成本价 = 金额 ÷ 份额，和手动录入的口径一致。
    """
    if price <= 0:
        raise TradeError("拿不到现价，算不出份额 —— 换种写法：写清楚股数和成本价")

    fee = 0.0
    if market == "fund":
        try:
            prof = quotes.fetch_fund_profile(code)
            fee = prof.get("rate") or FUND_FEE_DEFAULT
        except quotes.QuoteError:
            fee = FUND_FEE_DEFAULT
        fee = min(max(fee, 0.0), 0.5)
        units = (amount * (1 - fee)) / price
    else:
        units = amount / price

    if market in _LOT_SIZES:
        lot = _LOT_SIZES[market]
        units = int(units // lot) * lot
    elif market == "crypto":
        units = round(units, 8)
    else:
        units = int(units * 10000) / 10000

    if units <= 0:
        raise TradeError("金额太小，买不起最小单位")
    # 含费成本价：实付金额 ÷ 实际拿到的份额
    return units, amount / units


# ---------------------------------------------------------------- 解析 + 预览

def _resolve(parsed: dict) -> dict:
    """把解析结果补全成「准备执行的一笔交易」。

    返回：holding（已有持仓或 None）、name、kind、market、price、units、amount。
    """
    code = parsed.get("code")
    name_hint = parsed.get("name_hint")
    holding = _find_holding(code, name_hint)

    if holding:
        kind = holding["kind"]
        market = holding["market"] or "auto"
        name = holding["name"]
        if not code:
            code = holding["code"]
    else:
        kind, market, name = None, parsed.get("market_hint") or "auto", None
        if not code:
            raise TradeError(
                f"没找到「{name_hint}」，也没给代码 —— 换个写法：000001 买入 100 股 12.5"
            )

    # 行情：用来补名称、补现价、算金额模式的份额
    q = None
    if code:
        probe_market = market if market != "auto" else "auto"
        q = _quote(probe_market, code, kind or "")
        if q:
            market = q.get("market") or market
            if not kind:
                kind = _kind_from_market(market)
            if not name and q.get("name"):
                name = q["name"]
    if not kind:
        kind = _kind_from_market(market)
    if not name:
        name = code

    # 成交价（也就是这一笔的成本价）
    trade_price = parsed.get("price")
    units = parsed.get("units")
    amount = parsed.get("amount")

    # 金额模式：按现价折算份额
    if parsed["mode"] == "amount":
        if not q or not q.get("price"):
            raise TradeError("没拿到现价，算不出份额 —— 换种写法：写清楚股数和成本价")
        units, trade_price = _units_from_amount(amount, q["price"], market, code)

    # 没写价格就用现价（预览里会标出来）
    if trade_price is None:
        if q and q.get("price"):
            trade_price = q["price"]
        elif holding and holding["cost_price"]:
            trade_price = holding["cost_price"]
        else:
            raise TradeError("没写成本价，也查不到现价 —— 补上「成本价 12.5」")

    # 现价（持仓市值用的那个）：能查到行情就用行情的，查不到就拿成交价顶上
    market_price = q["price"] if (q and q.get("price")) else trade_price

    # 查不到行情的代码：名字和现价只能靠用户填，要提醒一句
    warning = None
    if code and q is None:
        warning = f"行情接口查不到 {code}，名称按代码记、现价按你说的填"

    return {
        "holding": holding,
        "code": code,
        "name": name,
        "kind": kind,
        "market": market,
        "price": float(trade_price),
        "market_price": float(market_price),
        "units": units,
        "amount": amount,
        "quote": q,
        "mode": parsed["mode"],
        "warning": warning,
    }


def preview(text: str) -> dict:
    """预演一笔交易：不写库，只告诉用户会发生什么。

    用户在输入框敲完先看这个，确认了再落库。
    """
    parsed = trade_parser.parse(text)
    if not parsed.get("ok"):
        return {"ok": False, "error": parsed.get("error"), "hint": parsed.get("hint")}

    try:
        info = _resolve(parsed)
    except TradeError as e:
        return {"ok": False, "error": str(e)}

    holding = info["holding"]
    units = info["units"]
    price = info["price"]
    side = parsed["side"]
    existing_id = holding["id"] if holding else None
    warning = info.get("warning")

    if side == "sell":
        if holding is None:
            return {"ok": False, "error": f"没有「{info['name']}」这只持仓，卖不掉"}
        held = float(holding["units"])
        if parsed["mode"] == "clear":
            units = held
        if units is None:
            return {"ok": False, "error": "没写卖多少",
                    "hint": "例：000001 卖出 100 股 12.5，或者写「000001 清仓」"}
        if units <= 0:
            return {"ok": False, "error": "份额要大于 0"}
        if units > held + 1e-9:
            return {"ok": False, "error": f"持仓只有 {held:g} 股，卖不了 {units:g} 股"}
        new_units = held - units
        realized = (price - float(holding["cost_price"])) * units
        return {
            "ok": True,
            "side": "sell",
            "code": info["code"],
            "name": info["name"],
            "kind": info["kind"],
            "market": info["market"],
            "units": units,
            "price": price,
            "market_price": info["market_price"],
            "date": parsed.get("date") or _now(),
            "is_new": False,
            "existing_id": existing_id,
            "warning": warning,
            "before": {"units": held, "cost_price": float(holding["cost_price"])},
            "after": {"units": new_units, "cost_price": float(holding["cost_price"])},
            "realized": _round4(realized),
            "amount": _round4(units * price),
            "note": "清仓" if parsed["mode"] == "clear" else None,
        }

    # ---- 买入 ----
    if holding is None:
        return {
            "ok": True,
            "side": "buy",
            "code": info["code"],
            "name": info["name"],
            "kind": info["kind"],
            "market": info["market"],
            "units": units,
            "price": price,
            "market_price": info["market_price"],
            "date": parsed.get("date") or _now(),
            "is_new": True,
            "existing_id": None,
            "warning": warning,
            "before": None,
            "after": {"units": units, "cost_price": price},
            "realized": 0.0,
            "amount": _round4(units * price),
            "note": None,
        }

    old_units = float(holding["units"])
    old_cost = float(holding["cost_price"])
    new_units = old_units + units
    new_cost = (old_units * old_cost + units * price) / new_units if new_units else price
    return {
        "ok": True,
        "side": "buy",
        "code": info["code"],
        "name": info["name"],
        "kind": info["kind"],
        "market": info["market"],
        "units": units,
        "price": price,
        "market_price": info["market_price"],
        "date": parsed.get("date") or _now(),
        "is_new": False,
        "existing_id": existing_id,
        "warning": warning,
        "before": {"units": old_units, "cost_price": old_cost},
        "after": {"units": new_units, "cost_price": _round4(new_cost)},
        "realized": 0.0,
        "amount": _round4(units * price),
        "note": None,
    }


# ---------------------------------------------------------------- 落库

def apply(text: str) -> dict:
    """执行一笔交易：合并持仓 + 记一笔流水。"""
    p = preview(text)
    if not p.get("ok"):
        raise TradeError(p.get("error") or "这笔交易没法执行")

    side = p["side"]
    units = float(p["units"])
    price = float(p["price"])          # 成交价 → 成本价
    market_price = float(p.get("market_price") or price)   # 现价 → 市值
    code = p["code"]
    name = p["name"]

    if side == "sell":
        holding = _holding_by_id(p.get("existing_id"))
        if holding is None:
            raise TradeError(f"没有「{name}」这只持仓，卖不掉")
        new_units = float(p["after"]["units"])
        if new_units <= 1e-9:
            # 清仓：直接删掉这条持仓
            db.execute("DELETE FROM holdings WHERE id = ?", (holding["id"],))
            holding_id = None
        else:
            db.execute(
                "UPDATE holdings SET units = ?, price = ? WHERE id = ?",
                (_round4(new_units), market_price, holding["id"]),
            )
            holding_id = holding["id"]
    else:
        holding = _holding_by_id(p.get("existing_id"))
        if holding is None:
            cur = db.execute(
                "INSERT INTO holdings (code, name, kind, market, units, cost_price, "
                "price, price_date, note) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (code, name, p["kind"], p["market"], units, price,
                 market_price, p["date"], None),
            )
            holding_id = cur.lastrowid
        else:
            holding_id = holding["id"]
            db.execute(
                "UPDATE holdings SET units = ?, cost_price = ?, price = ? WHERE id = ?",
                (_round4(p["after"]["units"]), p["after"]["cost_price"],
                 market_price, holding_id),
            )

    db.execute(
        "INSERT INTO holding_trades (date, holding_id, code, name, side, units, "
        "price, amount, realized, note, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (p["date"], holding_id, code, name, side, units, price,
         p["amount"], p["realized"], p.get("note"),
         datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
    )

    db.snapshot_portfolio()
    p["holding_id"] = holding_id
    return p


def recent_trades(limit: int = 50) -> list[dict]:
    """最近的交易流水。"""
    rows = db.q(
        "SELECT * FROM holding_trades ORDER BY id DESC LIMIT ?", (limit,)
    )
    return [dict(r) for r in rows]
