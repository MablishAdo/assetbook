"""行情与汇率抓取 —— 只依赖标准库 urllib，不引入 requests / aiohttp。

数据源（都是公开接口，不用注册、不用 API Key）：

  主源  腾讯行情  http://qt.gtimg.cn/q=sh510300,hk00700
        · 一次请求可以拉几十个标的，价格自带小数位不用猜
        · 覆盖沪深、场内基金 ETF/LOF、港股
  备源  东方财富  https://push2.eastmoney.com/api/qt/stock/get
        · 腾讯查不到时单只兜底
  场外基金  东方财富 f10 历史净值（申购赎回的那种，代码如 161725）
  加密货币  Binance 现货价
  汇率      open.er-api.com

单个标的失败不会影响其它：每只单独 try，结果里只放成功的。
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
TIMEOUT = 8
BATCH_SIZE = 50

MARKETS = {
    "auto": "自动识别",
    "cn": "沪深股票/ETF",
    "fund": "场外基金",
    "hk": "港股",
    "crypto": "加密货币",
}

_CRYPTO_BASE = {
    "BTC", "ETH", "BNB", "SOL", "XRP", "DOGE", "ADA", "TON", "AVAX",
    "DOT", "LINK", "MATIC", "LTC", "TRX", "SHIB", "BCH", "ATOM", "ETC",
}

_DATE_RE = re.compile(r"(\d{4})[-/]?(\d{2})[-/]?(\d{2})")


class QuoteError(Exception):
    """抓行情失败（网络、接口变更、代码不存在等）。"""


# ---------------------------------------------------------------- 基础请求

def _fetch_raw(url: str, encoding: str = "utf-8", referer: str | None = None,
               retries: int = 2) -> str:
    last: Exception | None = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(url)
        req.add_header("User-Agent", UA)
        req.add_header("Accept", "*/*")
        if referer:
            req.add_header("Referer", referer)
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return resp.read().decode(encoding, errors="replace")
        except urllib.error.HTTPError as e:
            last = QuoteError(f"HTTP {e.code}")
        except urllib.error.URLError as e:
            last = QuoteError(f"网络不通（{e.reason}）")
        except (TimeoutError, ConnectionError, OSError) as e:
            last = QuoteError(f"连接被断开（{type(e).__name__}）")
        if attempt < retries:
            time.sleep(0.6 * (attempt + 1))     # 被限流时退避一下
    raise last or QuoteError("请求失败")


def _fetch_json(url: str, referer: str | None = None) -> dict:
    text = _fetch_raw(url, referer=referer)
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise QuoteError("返回的不是 JSON") from e


def _today_from(stamp: str) -> str:
    m = _DATE_RE.search(stamp or "")
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return date.today().isoformat()


# ---------------------------------------------------------------- 代码归一

def guess_market(code: str, kind: str = "other") -> str | None:
    """按代码形态猜市场；猜不准就让用户在界面上手选。

    六位数字统一当沪深标的（股票 / ETF / LOF），因为绝大多数 6 位代码都是场内的；
    纯场外基金（如 110022）在沪深查不到，fetch_one 会自动回退到场外基金净值。
    """
    c = (code or "").strip().upper()
    if not c:
        return None
    if c.endswith("USDT") or c in _CRYPTO_BASE:
        return "crypto"
    if c.isdigit():
        if len(c) == 5:
            return "hk"
        if len(c) == 6:
            return "cn"
    return None


def _is_sh(code: str) -> bool:
    """5=沪市ETF/LOF，6=沪主板，9=沪B；其余（0/1/2/3/4/8）走深市与北交所。"""
    return code[:1] in "569"


def _tencent_symbol(market: str, code: str) -> str | None:
    c = code.strip().upper()
    if market == "cn":
        return ("sh" if _is_sh(c) else "sz") + c
    if market == "hk":
        return "hk" + c.zfill(5)
    return None


def _eastmoney_secid(market: str, code: str) -> str | None:
    c = code.strip().upper()
    if market == "cn":
        return ("1." if _is_sh(c) else "0.") + c
    if market == "hk":
        return "116." + c.zfill(5)
    return None


# ---------------------------------------------------------------- 腾讯（主源）

_TENCENT_HOSTS = (
    "https://qt.gtimg.cn/q={syms}",
    "http://qt.gtimg.cn/q={syms}",        # 少数环境只放行 80 端口
)
_TENCENT_LINE = re.compile(r'v_([A-Za-z0-9_]+)="([^"]*)"')
_SYM_PREFIXES = ("sh", "sz", "hk", "us")


def _sym_to_code(sym: str) -> str:
    """v_usAAPL → AAPL；v_sh510300 → 510300。

    不能直接拿返回体里的代码字段：美股会带交易所后缀（AAPL.OQ、JPM.N）。
    """
    low = sym.lower()
    for pre in _SYM_PREFIXES:
        if low.startswith(pre):
            return sym[len(pre):].upper()
    return sym.upper()


def _parse_tencent(text: str) -> dict[str, dict]:
    """把 v_sh510300="1~名称~代码~现价~昨收~..." 解析成 {代码: {...}}"""
    out: dict[str, dict] = {}
    for sym, payload in _TENCENT_LINE.findall(text):
        f = payload.split("~")
        if len(f) < 5:
            continue
        code = _sym_to_code(sym)
        raw = (f[3] or "").strip()
        if not code or not raw:
            continue
        try:
            price = float(raw)
        except ValueError:
            continue
        if price <= 0:
            continue
        out[code] = {
            "price": round(price, 6),
            "name": (f[1] or "").strip() or None,
            "date": _today_from(f[30] if len(f) > 30 else ""),
        }
    return out


def fetch_tencent(pairs: list[tuple[str, str]]) -> dict[str, dict]:
    """pairs: [(market, code)]，market 取 cn / hk。"""
    targets = [(m, c.strip().upper()) for m, c in pairs if _tencent_symbol(m, c)]
    if not targets:
        return {}

    out: dict[str, dict] = {}
    for i in range(0, len(targets), BATCH_SIZE):
        chunk = targets[i : i + BATCH_SIZE]
        syms = urllib.parse.quote(
            ",".join(_tencent_symbol(m, c) or "" for m, c in chunk), safe=","
        )
        text = None
        for tpl in _TENCENT_HOSTS:
            try:
                text = _fetch_raw(tpl.format(syms=syms), encoding="gbk")
                break
            except QuoteError:
                continue
        if not text:
            continue
        got = _parse_tencent(text)
        for market, code in chunk:
            if code in got:
                out[code] = {**got[code], "market": market}
    return out


# ---------------------------------------------------------------- 东财（备源）

_EM_ONE = (
    "https://push2.eastmoney.com/api/qt/stock/get"
    "?secid={secid}&fields=f43,f57,f58,f59"
)
_MAX_WORKERS = 4


def _fallback_digits(market: str, code: str) -> int:
    """f59 缺失时的兜底小数位：沪深股票 0.01 → 2 位；ETF/LOF 0.001 → 3 位。"""
    if market == "cn":
        c = code.strip()
        return 2 if not c.startswith(("5", "15", "16", "18")) else 3
    return 3


def _eastmoney_one(market: str, code: str) -> dict | None:
    code = code.strip().upper()
    secid = _eastmoney_secid(market, code)
    if not secid:
        return None
    try:
        data = _fetch_json(_EM_ONE.format(secid=urllib.parse.quote(secid)))
    except QuoteError:
        return None
    d = (data or {}).get("data")
    if not d:
        return None
    raw, digits = d.get("f43"), d.get("f59")
    if raw in (None, "-", ""):
        return None
    try:
        scale = 10 ** int(digits) if digits not in (None, 0) else 10 ** _fallback_digits(market, code)
        price = float(raw) / scale
    except (TypeError, ValueError):
        return None
    if price <= 0:
        return None
    return {
        "price": round(price, 6),
        "name": (d.get("f58") or "").strip() or None,
        "date": date.today().isoformat(),
        "market": market,
    }


def fetch_eastmoney(pairs: list[tuple[str, str]]) -> dict[str, dict]:
    targets = [(m, c.strip().upper()) for m, c in pairs if _eastmoney_secid(m, c)]
    if not targets:
        return {}
    out: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=min(_MAX_WORKERS, len(targets))) as pool:
        futures = {pool.submit(_eastmoney_one, m, c): c for m, c in targets}
        for fut in as_completed(futures):
            code = futures[fut]
            try:
                got = fut.result()
            except Exception:            # noqa: BLE001 — 单只失败不影响其它
                continue
            if got:
                out[code] = got
    return out


def fetch_market(pairs: list[tuple[str, str]]) -> dict[str, dict]:
    """沪深 / 港股：腾讯批量先上，缺的用东财单只补。"""
    out = fetch_tencent(pairs)
    missing = [(m, c) for m, c in pairs if c.strip().upper() not in out]
    if missing:
        out.update(fetch_eastmoney(missing))
    return out


# ---------------------------------------------------------------- 场外基金

_FUND_NAV = (
    "https://api.fund.eastmoney.com/f10/lsjz"
    "?fundCode={code}&pageIndex=1&pageSize=1&_={ts}"
)


def fetch_fund_nav(code: str) -> dict:
    """场外基金单位净值。"""
    url = _FUND_NAV.format(code=urllib.parse.quote(code.strip()),
                           ts=int(time.time() * 1000))
    data = _fetch_json(url, referer="https://fundf10.eastmoney.com/")
    rows = ((data or {}).get("Data") or {}).get("LSJZList") or []
    if not rows:
        raise QuoteError("查不到该基金净值")
    row = rows[0]
    try:
        price = float(row.get("DWJZ"))
    except (TypeError, ValueError) as e:
        raise QuoteError("净值字段异常") from e
    return {
        "price": round(price, 6),
        "date": (row.get("FSRQ") or date.today().isoformat())[:10],
        "name": None,
        "market": "fund",
    }


# ---------------------------------------------------------------- 加密货币

_BINANCE = "https://api.binance.com/api/v3/ticker/price?symbol={sym}"


def _crypto_symbol(code: str) -> str:
    c = code.strip().upper()
    return c if c.endswith("USDT") else c + "USDT"


def fetch_crypto(codes: list[str]) -> dict[str, dict]:
    """币安现货价。逐个拉，持仓里通常没几个币。"""
    out: dict[str, dict] = {}
    for code in codes:
        sym = _crypto_symbol(code)
        try:
            data = _fetch_json(_BINANCE.format(sym=urllib.parse.quote(sym)))
            price = float(data.get("price"))
        except (QuoteError, TypeError, ValueError):
            continue
        if price > 0:
            out[code.strip().upper()] = {
                "price": round(price, 6),
                "name": None,
                "date": date.today().isoformat(),
                "market": "crypto",
            }
    return out


# ---------------------------------------------------------------- 汇率

_RATE_SOURCES = (
    "https://open.er-api.com/v6/latest/{base}",
    "https://api.exchangerate-api.com/v4/latest/{base}",
)


def fetch_rates(base: str = "CNY") -> dict:
    """拉汇率，统一成「1 单位外币 = 多少 base」。

    返回 {"base": "CNY", "rates": {"USD": 7.12, ...}, "date": "YYYY-MM-DD"}
    """
    last: Exception | None = None
    for tpl in _RATE_SOURCES:
        try:
            data = _fetch_json(tpl.format(base=base))
        except QuoteError as e:
            last = e
            continue
        raw = data.get("rates") or {}
        if not raw or base not in raw:
            continue
        inv: dict[str, float] = {}
        for code, val in raw.items():
            try:
                v = float(val)
            except (TypeError, ValueError):
                continue
            if v > 0:
                inv[code.upper()] = round(1.0 / v, 8)
        inv[base] = 1.0
        return {
            "base": base,
            "rates": inv,
            "date": date.today().isoformat(),
            "source_time": str(data.get("time_last_update_utc") or data.get("date") or "")[:32],
        }
    raise QuoteError(f"汇率接口都失败了（{last}）")


# ---------------------------------------------------------------- 统一入口

def fetch_one(market: str, code: str, kind: str = "") -> dict:
    """按指定市场拉单个标的。market 传 auto 时按代码形态猜。

    注意：六位数字代码在沪深和场外基金里是同一片号段，
    所以 auto / cn 拉不到沪深行情时会自动再试一次场外基金净值。
    """
    code = (code or "").strip().upper()
    if not code:
        raise QuoteError("没填代码")

    m = market if market in MARKETS and market != "auto" else guess_market(code, kind)

    if m == "hk":
        got = fetch_market([("hk", code)])
        if code in got:
            return got[code]
        raise QuoteError(f"港股里查不到 {code}")

    if m == "crypto":
        got = fetch_crypto([code])
        if code in got:
            return got[code]
        raise QuoteError("币安查不到该交易对，代码写成 BTC 或 BTCUSDT 都行")

    if m == "fund":
        return fetch_fund_nav(code)

    # cn（含 auto 猜出来的）：先沪深，再场外基金
    got = fetch_market([("cn", code)])
    if code in got:
        return got[code]
    try:
        return fetch_fund_nav(code)
    except QuoteError:
        pass
    if m == "auto":
        raise QuoteError("自动识别失败，编辑持仓时把「市场」手动指定一下")
    raise QuoteError(f"沪深和场外基金里都查不到 {code}")
