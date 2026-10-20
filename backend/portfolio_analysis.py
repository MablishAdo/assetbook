"""AI 持仓分析 —— 逐只标的给出「加仓 / 持有 / 减仓」判断。

和记账分析（insights.py）是两条独立的线：那边看钱花在哪，这边看钱投在哪。

数据来源都是公开接口，不需要任何 Key：
    场外基金  东方财富 FundMNBasicInformation 自带各区间涨跌幅（SYL_* 字段）
    场内/股票 腾讯 K 线接口，按收盘价自己算 1 月 / 3 月 / 6 月 / 1 年
    加密货币  币安 24h 涨跌（只给近 1 月一条参考）

为什么要抓涨跌幅：只看「当前盈亏」模型等于闭眼说话。给到「这个基金近 1 年
跌了 22%」「它占你整个组合 63%」这种数字，模型才判得出该加还是该减。

单只拉取失败不影响其它：抓不到就返回 None，那只标的的涨跌幅字段留空，
提示词里明确说「缺数据就别猜」。
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import ai
import db
import quotes
import services as svc

CACHE_PREFIX = "portfolio_ai_"
MAX_WORKERS = 4

# 建议输出的三档
ACTIONS = ("加仓", "持有", "减仓")


# ---------------------------------------------------------------- 涨跌幅

_FUND_PERIOD_MAP = {
    "近1周": "SYL_Z",
    "近1月": "SYL_Y",
    "近3月": "SYL_3Y",
    "近6月": "SYL_6Y",
    "近1年": "SYL_1N",
    "今年以来": "SYL_JN",
}


def _pct(v) -> float | None:
    try:
        return round(float(str(v).replace("%", "")), 2)
    except (TypeError, ValueError):
        return None


def fund_returns(code: str) -> dict | None:
    """场外基金各区间涨跌幅。接口返回 '--' 的区间自动跳过。"""
    try:
        d = quotes.fetch_fund_profile(code)
    except quotes.QuoteError:
        return None
    if not d:
        return None

    url = (
        "https://fundmobapi.eastmoney.com/FundMNewApi/FundMNBasicInformation"
        f"?FCODE={code}&deviceid=1&plat=Android&product=EFund&version=6.2.8"
    )
    try:
        raw = quotes._fetch_json(url, referer="https://fund.eastmoney.com/")
        d2 = (raw or {}).get("Datas") or {}
    except quotes.QuoteError:
        d2 = {}

    out: dict[str, float] = {}
    for label, field in _FUND_PERIOD_MAP.items():
        v = _pct(d2.get(field))
        if v is not None:
            out[label] = v
    if not out:
        return None
    out["来源"] = "东方财富"
    return out


_KLINE = (
    "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
    "?param={sym},day,,,400,qfq"
)


def _kline_returns(market: str, code: str) -> dict | None:
    """场内标的按日 K 收盘价算区间涨跌。400 个交易日约 20 个月，够算 1 年。"""
    sym = quotes._tencent_symbol(market, code)
    if not sym:
        return None
    try:
        data = quotes._fetch_json(
            _KLINE.format(sym=sym), referer="https://gu.qq.com/")
    except quotes.QuoteError:
        return None

    node = ((data or {}).get("data") or {}).get(sym) or {}
    bars = node.get("qfqday") or node.get("day") or []
    closes = []
    for b in bars:
        try:
            closes.append(float(b[2]))       # [日期, 开, 收, 高, 低, ...]
        except (TypeError, ValueError, IndexError):
            continue
    if len(closes) < 30 or closes[-1] <= 0:
        return None

    last = closes[-1]

    def chg(n: int) -> float | None:
        if len(closes) <= n or closes[-1 - n] <= 0:
            return None
        return round((last / closes[-1 - n] - 1) * 100, 2)

    out = {}
    for label, n in (("近1月", 22), ("近3月", 64), ("近6月", 128), ("近1年", 250)):
        v = chg(n)
        if v is not None:
            out[label] = v
    return out or None


_BINANCE_CHANGES = "https://api.binance.com/api/v3/ticker/24hr?symbol={sym}"
# 币安在部分网络下返回 451（地域限制），准备一个公共兜底源
_COINGECKO_MARKETS = (
    "https://api.coingecko.com/api/v3/coins/markets"
    "?vs_currency=usd&symbols={syms}&price_change_percentage=24h,7d,30d"
)


def crypto_returns(code: str) -> dict | None:
    """加密货币区间涨跌幅。

    首选币安（无需 Key、字段最全）；币安返回 451 时回退 CoinGecko 的 markets 接口。
    两个源都拿不到就返回 None —— 宁可缺数据，也不要给模型错的数字。
    """
    sym = code.strip().upper()
    if not sym.endswith("USDT"):
        sym += "USDT"

    try:
        data = quotes._fetch_json(_BINANCE_CHANGES.format(sym=sym))
        v = _pct((data or {}).get("priceChangePercent"))
        if v is not None:
            return {"近1日": v, "来源": "币安"}
    except quotes.QuoteError:
        pass

    base = sym[:-4] if sym.endswith("USDT") else sym
    try:
        rows = quotes._fetch_json(_COINGECKO_MARKETS.format(syms=base.lower()))
    except quotes.QuoteError:
        return None
    for c in rows or []:
        if (c.get("symbol") or "").upper() != base:
            continue
        out = {}
        for label, field in (("近1日", "price_change_percentage_24h_in_currency"),
                             ("近1周", "price_change_percentage_7d_in_currency"),
                             ("近1月", "price_change_percentage_30d_in_currency")):
            v = _pct(c.get(field))
            if v is not None:
                out[label] = v
        if out:
            out["来源"] = "CoinGecko"
            return out
    return None


def _is_crypto(code: str) -> bool:
    return code.strip().upper().removesuffix("USDT") in quotes._CRYPTO_BASE


def fetch_returns(holding: dict) -> dict | None:
    """按持仓的市场类型选源抓区间涨跌幅。

    market=auto 时必须先问一遍行情接口确认它到底是场内还是场外：
    六位数字里场内与场外是同一片号段，直接猜会拿场外基金代码去拼 sz/sh 前缀查 K 线，
    腾讯会返回一只不相干标的的行情，模型就会拿着错的涨跌幅下判断 —— 比缺数据更糟。
    """
    code = (holding.get("code") or "").strip()
    market = (holding.get("market") or "auto").strip() or "auto"
    if not code:
        return None

    if market == "auto":
        # 加密代码不是数字形态，先自己认出来，别去问 A 股接口
        if _is_crypto(code):
            return crypto_returns(code)
        try:
            probe = quotes.fetch_one("auto", code, holding.get("kind") or "")
        except quotes.QuoteError:
            return None
        market = probe.get("market") or "auto"
        if market == "auto":
            return None

    try:
        if market == "crypto":
            return crypto_returns(code)
        if market == "fund":
            return fund_returns(code)
        if market in ("cn", "hk"):
            return _kline_returns(market, code)
        return None
    except Exception:                      # noqa: BLE001 — 单只失败不影响其它
        return None


# ---------------------------------------------------------------- 数据包

def snapshot() -> dict:
    """持仓数据包：每只标的带上占比、盈亏、区间涨跌幅，以及品种集中度。"""
    items = svc.holdings_with_value()
    total = round(sum(h["market_value"] for h in items), 2)

    rows = []
    for h in items:
        rows.append({
            "id": h["id"], "代码": h.get("code") or "", "名称": h["name"],
            "品种": h["kind_label"], "市场": h["market_label"],
            "份额": h["units"], "成本价": round(h["cost_price"], 4),
            "现价": round(h["price"], 4),
            "市值": h["market_value"], "成本额": h["cost_value"],
            "盈亏": h["profit"], "盈亏率": f"{h['profit_pct']}%",
            "占比": round(h["market_value"] / total * 100, 1) if total else 0.0,
            "区间涨跌": None,          # 下面并发补
        })

    # 并发抓涨跌幅
    by_id = {r["id"]: r for r in rows}
    todo = [h for h in items if (h.get("code") or "").strip()]
    if todo:
        with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(todo))) as pool:
            futures = {pool.submit(fetch_returns, h): h["id"] for h in todo}
            for fut in as_completed(futures):
                hid = futures[fut]
                try:
                    got = fut.result()
                except Exception:          # noqa: BLE001
                    got = None
                if got and hid in by_id:
                    by_id[hid]["区间涨跌"] = got

    return {
        "统计日": svc.today(),
        "总市值": total,
        "汇总": svc.holdings_totals(),
        "品种集中度": [
            {"品种": k["name"], "市值": k["market_value"], "占比": f"{k['ratio']}%"}
            for k in svc.holdings_by_kind()
        ],
        "标的": rows,
    }


# ---------------------------------------------------------------- 提示词

SYSTEM_PROMPT = """你是一位只做组合管理的投资顾问，服务对象是一个自己记账、自己买基金和股票的普通个人投资者。

你会收到一份他真实持仓的数据（单位：人民币元）。请严格按下面的要求输出。

输出格式（Markdown，四段，顺序固定）：

## 组合体检
3-5 句。讲清整体结构问题：是不是全压在单一品种或单只标的上、有没有现金比例过低的问题、
整体是偏进攻还是偏防守。指出最关键的那一到两个结构性问题。

## 逐只建议
每只标的写一段，格式严格如下，不要增减字段：

### {名称}（{代码}）—— 加仓 / 持有 / 减仓
- **理由**：一句话，必须引用数据里的具体数字（区间涨跌、占比、盈亏率至少一个）。
- **操作**：具体动作。写清楚比例或金额，例如「加 500 元」「减掉三分之一」「先不动，等站上 X」。
  不要写「建议关注」「谨慎持有」这种无法执行的话。
- **注意**：这只标的最需要注意的一件事。没有明显风险就写「无特别风险」。

## 集中度与节奏
2-4 句。从整体角度说：要不要调仓位、调整顺序是什么、大概多久动一次。

## 前提提醒
1-2 句。数据缺什么就直说缺什么（比如没抓到涨跌幅、没设止损区间），
并提醒这不构成投资建议、最终要自己判断。

硬性规则：
- 每只标的的结论只能从「加仓 / 持有 / 减仓」里选一个，不要发明第四种。
- 只能用给的数据说话。数据里没有的绝对不要编造，也不要用「大概」「可能」去猜。
- 提到的每个数字都必须能在数据里找到。「区间涨跌」为 null 的就说明「缺数据，不要据此下结论」。
- 不要出现「我作为 AI」「仅供参考所以建议您」这类套话。
- 全文控制在 900 字以内。"""


def build_prompt(snap: dict) -> list[dict]:
    data = json.dumps(snap, ensure_ascii=False, separators=(",", ":"))
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"这是我的持仓数据：\n{data}"},
    ]


# ---------------------------------------------------------------- 生成与缓存

def _cache_key() -> str:
    """按天分桶。同一天重算会覆盖，跨天才算新的一版。"""
    return f"{CACHE_PREFIX}{svc.today()}"


def has_data() -> bool:
    return bool(db.q1("SELECT COUNT(*) AS n FROM holdings WHERE units > 0")["n"])


def cached() -> dict | None:
    raw = db.get_meta(_cache_key())
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    data["stale"] = bool(data.get("data_ts")) and data["data_ts"] != _fingerprint()
    return data


def _fingerprint() -> str:
    """持仓市值指纹：份额或现价变了就该重算。"""
    row = db.q1("SELECT COUNT(*) AS n, COALESCE(SUM(units * price), 0) AS v FROM holdings")
    return f"{row['n']}:{round(float(row['v']), 2)}"


def analyze(force: bool = False) -> dict:
    """生成（或读取缓存）持仓分析。"""
    if not force:
        hit = cached()
        if hit:
            return {**hit, "cached": True}

    if not has_data():
        raise ai.AIError("还没有持仓。先到「投资」页加一只标的，再来看分析。")

    snap = snapshot()
    result = ai.chat(build_prompt(snap), temperature=0.3)
    now = datetime.now().isoformat(timespec="seconds")
    payload = {
        "date": svc.today(),
        "content": result["content"],
        "model": result["model"],
        "usage": result.get("usage"),
        "at": now,
        "data_ts": _fingerprint(),
        "stale": False,
        "holdings": len(snap["标的"]),
    }
    db.set_meta(_cache_key(), json.dumps(payload, ensure_ascii=False))
    return {**payload, "cached": False}