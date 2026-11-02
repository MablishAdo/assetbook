"""快捷录入解析 —— 把一句人话拆成「买/卖 + 代码 + 份额 + 价格」。

支持的写法（大小写不敏感，全角自动转半角）：

    000001 买入 100 股 成本价 100      买 100 股，每股 100
    600519 买 100股 @1200               同上
    00700 买入 100 股 成本价 400
    510300 买入 1000 元                 按金额买，份额按净值/现价折算
    BTC 买入 0.5 个 成本价 60000
    000001 清仓                        全部卖出
    平安银行 买入 100 股 12.5          用中文名找已有持仓

歧义靠关键词消歧：

    成本价 / 单价 / 价格 / 每股 / @ / 价   →  每股价格
    成本 / 金额 / 总额 / 共 / 花 / 元       →  总金额
    100 股 / 100份 / 3 手 / 份额 100       →  份额（3 手按 A 股 = 300 股）

没有关键词的裸数字：先当份额、再当价格；5~6 位整数优先当证券代码。
价格可以不写 —— 服务端会用当时行情价补上，并在预览里标出来。
"""

from __future__ import annotations

import re
from datetime import date

_NUM = r"\d+(?:\.\d+)?"

# 方向词（长的排前面，避免「买入」被「买」抢先命中）
_BUY_WORDS = ("买入", "买进", "加仓", "补仓", "buy", "买")
_SELL_WORDS = ("卖出", "卖掉", "减仓", "sell", "卖")
_CLEAR_WORDS = ("清仓", "全卖", "清掉")

# 每股价格关键词
_PRICE_WORDS = ("成本价", "成本单价", "单价", "价格", "每股", "price", "@", "价")
# 总金额关键词（「成本」单独出现按总花费理解，「成本价」优先按每股算）
_AMOUNT_WORDS = ("总金额", "金额", "总额", "共", "花了", "花费", "花", "成本", "rmb", "cny")

# 份额单位
_UNITS_WORDS = ("股", "份", "手", "个", "股数", "份额")
# 金额单位
_CASH_WORDS = ("元", "块", "人民币", "usd")

# 「100 股」：数字在前、单位在后
_UNITS_RE = re.compile(rf"(?P<num>{_NUM})\s*(?P<key>股数|份额|股|份|手|个)", re.I)
# 「股数 100」：单位在前、数字在后
_UNITS_REV_RE = re.compile(rf"(?P<key>股数|份额)\s*[:=]?\s*(?P<num>{_NUM})", re.I)
# 「1000 元」
_CASH_RE = re.compile(rf"(?P<num>{_NUM})\s*(?P<key>元|块|人民币|usd)", re.I)
# 「成本价 100」
_PRICE_RE = re.compile(
    rf"(?P<key>成本价|成本单价|单价|价格|每股|price|@|价)\s*[:=]?\s*(?P<num>{_NUM})", re.I)
# 「金额 1000」
_AMOUNT_RE = re.compile(
    rf"(?P<key>总金额|金额|总额|共|花了|花费|花|成本|rmb|cny)\s*[:=]?\s*(?P<num>{_NUM})", re.I)
# 裸数字
_BARE_RE = re.compile(r"(?<![A-Za-z0-9.])(\d+(?:\.\d+)?)(?![A-Za-z0-9.])")
# 交易日期：2026-11-02 / 2026/11/02 / 11-02
_DATE_RE = re.compile(r"(?<![\d.])(?P<date>\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}[-/]\d{1,2})(?![\d.])")
# 代码：5~6 位数字，可带 sh/sz/bj/hk 前缀
_CODE_RE = re.compile(r"(?<![A-Za-z0-9])(?:(sh|sz|bj|hk)\s*)?(\d{5,6})(?![0-9])", re.I)
# 字母代码（加密货币等）
_ALPHA_RE = re.compile(r"(?<![A-Za-z0-9])([A-Z][A-Z0-9]{1,9})(?![A-Za-z0-9])")
# 残留中文，可能就是「平安银行」这种名字
_CJK_RE = re.compile(r"[一-鿿]{2,}")

# 这些词在文本里只是动作或单位，不该被当成代码 / 名字
_STOP_WORDS = {"买入", "买进", "加仓", "补仓", "卖出", "卖掉", "减仓", "清仓", "全卖", "清掉",
               "成本价", "成本单价", "成本", "单价", "价格", "每股", "份额", "股数", "金额",
               "总额", "总金额", "花了", "花费", "花", "共", "股", "份", "手", "个", "元", "块",
               "人民币"}


def _normalize(text: str) -> str:
    """全角转半角 + 空白压成一个空格。"""
    out = []
    for ch in text or "":
        code = ord(ch)
        if 0xFF01 <= code <= 0xFF5E:
            out.append(chr(code - 0xFEE0))
        elif code == 0x3000:
            out.append(" ")
        else:
            out.append(ch)
    return re.sub(r"\s+", " ", "".join(out)).strip()


def _take(text: str, rx: re.Pattern) -> tuple[float | None, str, str | None]:
    """在 text 里抓 rx 的第一个数字，并把它连同前缀词一起挖成空格。

    返回 (数值, 挖掉之后的剩余文本, 命中的前缀词)。
    挖空而不是替换成 0，是为了让后面的「裸数字」扫描不会把用过的数字再当成代码。
    """
    m = rx.search(text)
    if not m:
        return None, text, None
    try:
        val = float(m.group("num"))
    except (TypeError, ValueError):
        return None, text, None
    rest = text[:m.start()] + " " * len(m.group(0)) + text[m.end():]
    return val, rest, (m.group("key") or "")


def _find_word(text: str, words: tuple[str, ...]) -> bool:
    low = text.lower()
    return any(w.lower() in low for w in words)


def _name_hint(rest: str) -> str | None:
    """从残留中文里挑一个像证券名字的短语。"""
    for frag in _CJK_RE.findall(rest):
        if frag in _STOP_WORDS:
            continue
        if all(w in _STOP_WORDS for w in [frag]):
            continue
        return frag
    return None


def parse(text: str) -> dict:
    """解析一句快捷录入。永远返回 dict；ok=False 时带 error 与 hint。"""
    raw = _normalize(text)
    if not raw:
        return {"ok": False, "error": "没输入内容",
                "hint": "例：000001 买入 100 股 成本价 100"}

    # ---- 方向 ----
    side = None
    mode = "units"
    if _find_word(raw, _CLEAR_WORDS):
        side, mode = "sell", "clear"
    elif _find_word(raw, _SELL_WORDS):
        side = "sell"
    elif _find_word(raw, _BUY_WORDS):
        side = "buy"

    rest = raw

    # ---- 交易日期（先挖掉，免得被当成代码或份额）----
    trade_date = None
    m = _DATE_RE.search(rest)
    if m:
        raw_date = m.group("date").replace("/", "-")
        parts = raw_date.split("-")
        if len(parts) == 3:
            trade_date = f"{int(parts[0]):04d}-{int(parts[1]):02d}-{int(parts[2]):02d}"
        else:
            today = date.today()
            mm, dd = int(parts[0]), int(parts[1])
            year = today.year
            try:
                date(year, mm, dd)
            except ValueError:
                trade_date = None
            else:
                trade_date = f"{year:04d}-{mm:02d}-{dd:02d}"
        if trade_date:
            rest = rest[:m.start()] + " " * len(m.group(0)) + rest[m.end():]

    # ---- 份额 / 金额 / 价格（按优先级依次挖）----
    units, rest, unit_word = _take(rest, _UNITS_RE)
    if units is None:
        units, rest, unit_word = _take(rest, _UNITS_REV_RE)
    if units is not None and unit_word == "手":
        units *= 100                      # A 股一手 = 100 股

    amount, rest, _ = _take(rest, _CASH_RE)
    price, rest, _ = _take(rest, _PRICE_RE)
    if amount is None:
        amount, rest, _ = _take(rest, _AMOUNT_RE)

    # ---- 代码 ----
    code = None
    market_hint = None
    m = _CODE_RE.search(rest)
    if m:
        code = m.group(2)
        market_hint = (m.group(1) or "").lower() or None
        rest = rest[:m.start()] + " " * len(m.group(0)) + rest[m.end():]
    else:
        m = _ALPHA_RE.search(rest)
        if m:
            code = m.group(1).upper()
            rest = rest[:m.start()] + " " * len(m.group(0)) + rest[m.end():]

    name_hint = _name_hint(rest)

    # ---- 剩下的裸数字：先补份额，再补价格 ----
    bare = [float(x) for x in _BARE_RE.findall(rest)]

    if code is None and bare:
        # 没抓到代码时，裸数字里 5~6 位整数最可能是代码
        for v in bare:
            if v == int(v) and 5 <= len(str(int(v))) <= 6:
                code = str(int(v))
                bare.remove(v)
                break

    if units is None and bare:
        units = bare.pop(0)
    if price is None and bare:
        price = bare.pop(0)

    # ---- 校验 ----
    if side is None:
        return {"ok": False, "error": "看不出是买入还是卖出",
                "hint": "补上「买入」或「卖出」两个字"}
    if not code and not name_hint:
        return {"ok": False, "error": "没认出证券代码",
                "hint": "例：000001 买入 100 股 成本价 100"}

    if mode == "clear":
        return {"ok": True, "side": side, "mode": "clear", "code": code,
                "name_hint": name_hint, "market_hint": market_hint,
                "units": None, "price": price, "amount": None,
                "date": trade_date, "text": raw}

    if amount is not None and units is None:
        mode = "amount"

    if units is None and amount is None:
        return {"ok": False, "error": "没写买多少",
                "hint": "补上份额（100 股）或金额（1000 元）"}
    if units is not None and units <= 0:
        return {"ok": False, "error": "份额要大于 0", "hint": "重写一下份额"}
    if amount is not None and amount <= 0:
        return {"ok": False, "error": "金额要大于 0", "hint": "重写一下金额"}
    if price is None and amount is None:
        # 价格可以省略 —— 服务端会用当时行情价补，但会在预览里标出来
        price = None

    return {
        "ok": True,
        "side": side,
        "mode": mode,
        "code": code,
        "name_hint": name_hint,
        "market_hint": market_hint,
        "units": units,
        "price": price,
        "amount": amount,
        "date": trade_date,
        "text": raw,
    }
