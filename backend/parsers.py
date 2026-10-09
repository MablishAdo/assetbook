"""账单解析 —— 认得出支付宝 / 微信 / 银行导出的 CSV，统一成一种行。

三家的真实格式（2024 年后的导出）：

  支付宝  编码 GBK，前面约 24 行是账号、起止日期之类的头部，之后才是表头
          列：交易时间 交易分类 交易对方 对方账号 商品说明 收/支 金额
              收/付款方式 交易状态 交易订单号 商家订单号 备注
          收/付款方式长这样：工商银行8888、余额宝、花呗
          老版本格式是「支付宝交易记录明细查询」，列名不同但语义一致

  微信    编码 UTF-8（带 BOM），前面约 16 行头部
          列：交易时间 交易类型 交易对方 商品 收/支 金额(元) 支付方式
              当前状态 交易单号 商户单号 备注
          文件末尾还有几行统计，空值统一写成 "/"

  银行    各家不同，没有统一单号，用列名别名兜。
          工行/招行/建行/中行的常见叫法都覆盖了，识别不到就让用户手动指列。

解析出来的行统一长这样：
    {date, time, type, amount, note, counterparty, category_hint,
     pay_method, status, external_id, source}
其中 type 取 in / out / skip（skip = 不计收支，比如余额宝内部划转）。
external_id 是幂等键：同一份账单反复导入不会重复记账。
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
import zipfile

SOURCES = {
    "alipay": "支付宝",
    "wechat": "微信支付",
    "bank": "银行流水",
    "generic": "通用 CSV",
}

# 支付宝/微信里表示「空」的占位符
_EMPTY_TOKENS = {"", "/", "-", "--", "—", "N/A", "n/a", "无"}

_AMOUNT_RE = re.compile(r"-?[\d,]+(?:\.\d+)?")
_DATE_RE = re.compile(r"(\d{4})[-/年.]?(\d{1,2})[-/月.]?(\d{1,2})")


# ---------------------------------------------------------------- 小工具

def _clean(v) -> str:
    if v is None:
        return ""
    s = str(v).strip().strip('"').strip()
    return "" if s in _EMPTY_TOKENS else s


def parse_amount(v) -> float | None:
    """从「¥1,234.56」「-1,234.56」「1234.56元」里抠出数字。"""
    s = _clean(v).replace("¥", "").replace("￥", "").replace("元", "").replace(",", "").replace(" ", "")
    if not s:
        return None
    m = _AMOUNT_RE.search(s)
    if not m:
        return None
    try:
        return round(float(m.group()), 2)
    except ValueError:
        return None


def parse_date(v) -> str:
    """把各种日期写法归一到 YYYY-MM-DD。"""
    s = _clean(v)
    m = _DATE_RE.search(s)
    if not m:
        return ""
    y, mo, d = m.group(1), int(m.group(2)), int(m.group(3))
    return f"{y}-{mo:02d}-{d:02d}"


def _digits(v) -> str:
    """只留字母数字，用来比对单号（有些导出会给单号加引号或空格）。"""
    return re.sub(r"[^0-9A-Za-z]", "", _clean(v))


def make_external_id(source: str, date: str, tx_type: str, amount: float, note: str) -> str:
    """没有单号时的兜底幂等键，同一天同金额同摘要视为同一笔。"""
    raw = f"{source}|{date}|{tx_type}|{amount:.2f}|{note}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]


# ---------------------------------------------------------------- 读文件

def _read_text(raw: bytes) -> tuple[str, str]:
    """返回 (文本, 实际用的编码)。三家账单的编码不统一。"""
    for enc in ("utf-8-sig", "utf-8", "gbk", "gb18030"):
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), "utf-8(替换非法字节)"


def _find_header_line(lines: list[str], must_have: tuple[str, ...]) -> int:
    """在文件前 60 行里找真正的表头行。"""
    for i, line in enumerate(lines[:60]):
        if all(k in line for k in must_have) and line.count(",") >= 3:
            return i
    return -1


def _csv_rows(text: str, skip: int) -> list[dict]:
    reader = csv.DictReader(io.StringIO("\n".join(text.splitlines()[skip:])))
    out = []
    for row in reader:
        # 微信账单每行末尾跟着一堆逗号，会解析出 None 键，清掉
        clean = {k: v for k, v in row.items() if k is not None}
        if any(_clean(v) for v in clean.values()):
            out.append(clean)
    return out


def _first(row: dict, *names: str) -> str:
    """按候选列名取值，先精确匹配再模糊包含。"""
    keys = list(row.keys())
    for name in names:
        for k in keys:
            if (k or "").strip() == name:
                return _clean(row[k])
    for name in names:
        for k in keys:
            if name and name in (k or ""):
                return _clean(row[k])
    return ""


def sniff(header_line: str) -> str:
    """看表头认来源。"""
    h = header_line.replace(" ", "")
    if "交易单号" in h or "微信支付账单" in h or ("当前状态" in h and "商户单号" in h):
        return "wechat"
    if "交易订单号" in h or "支付宝" in h or ("收/付款方式" in h and "交易分类" in h):
        return "alipay"
    if "交易号" in h and "商家订单号" in h:
        return "alipay"          # 老版支付宝「支付宝交易记录明细查询」
    return "bank" if any(k in h for k in ("摘要", "借方", "贷方", "余额", "对方户名", "对手信息")) else "generic"


# ---------------------------------------------------------------- 各家解析

_ALIPAY_SKIP_STATUS = ("交易关闭", "已关闭", "关闭交易")


def parse_alipay(rows: list[dict]) -> tuple[list[dict], list[str]]:
    items, warnings = [], []
    closed = 0
    for r in rows:
        date = parse_date(_first(r, "交易时间", "交易创建时间", "付款时间", "最近修改时间"))
        amount = parse_amount(_first(r, "金额", "金额（元）", "金额(元)"))
        if not date or amount is None or amount == 0:
            continue
        status = _first(r, "交易状态", "资金状态")
        if any(s in status for s in _ALIPAY_SKIP_STATUS):
            closed += 1
            continue
        raw_dir = _first(r, "收/支", "收支")
        if raw_dir == "不计收支":
            tx_type = "skip"
        elif "收入" in raw_dir or raw_dir == "收":
            tx_type = "in"
        elif "支出" in raw_dir or raw_dir == "支":
            tx_type = "out"
        else:
            tx_type = "out"
        counterparty = _first(r, "交易对方")
        goods = _first(r, "商品说明", "商品名称")
        note = " ".join(p for p in (counterparty, goods) if p)
        oid = _digits(_first(r, "交易订单号", "交易号"))
        items.append({
            "date": date,
            "time": _first(r, "交易时间", "交易创建时间"),
            "type": tx_type,
            "amount": abs(amount),
            "note": note[:120],
            "counterparty": counterparty,
            "category_hint": _first(r, "交易分类", "类型"),
            "pay_method": _first(r, "收/付款方式"),
            "status": status,
            "external_id": f"alipay:{oid}" if oid else make_external_id("alipay", date, tx_type, abs(amount), note),
            "source": "alipay",
        })
    if closed:
        warnings.append(f"跳过 {closed} 笔已关闭的交易")
    return items, warnings


def parse_wechat(rows: list[dict]) -> tuple[list[dict], list[str]]:
    items, warnings = [], []
    refunded = 0
    for r in rows:
        date = parse_date(_first(r, "交易时间"))
        amount = parse_amount(_first(r, "金额(元)", "金额（元）", "金额"))
        if not date or amount is None or amount == 0:
            continue
        status = _first(r, "当前状态", "交易状态")
        if "已全额退款" in status or "已退款" in status:
            refunded += 1
        raw_dir = _first(r, "收/支", "收支")
        if "收入" in raw_dir or raw_dir == "收":
            tx_type = "in"
        elif "支出" in raw_dir or raw_dir == "支":
            tx_type = "out"
        else:
            tx_type = "skip"
        counterparty = _first(r, "交易对方")
        goods = _first(r, "商品", "商品说明")
        note = " ".join(p for p in (counterparty, goods) if p)
        oid = _digits(_first(r, "交易单号", "交易订单号"))
        items.append({
            "date": date,
            "time": _first(r, "交易时间"),
            "type": tx_type,
            "amount": abs(amount),
            "note": note[:120],
            "counterparty": counterparty,
            "category_hint": _first(r, "交易类型"),
            "pay_method": _first(r, "支付方式", "收/付款方式"),
            "status": status,
            "external_id": f"wechat:{oid}" if oid else make_external_id("wechat", date, tx_type, abs(amount), note),
            "source": "wechat",
        })
    if refunded:
        warnings.append(f"其中 {refunded} 笔是退款/已退款，请留意方向")
    return items, warnings


_BANK_DATE = ("交易日期", "交易时间", "记账日期", "日期", "交易发生日", "交易日")
_BANK_OUT = ("支出金额", "借方发生额", "借方金额", "支取金额", "付款金额", "支出")
_BANK_IN = ("收入金额", "贷方发生额", "贷方金额", "存入金额", "收款金额", "收入")
_BANK_AMOUNT = ("交易金额", "发生额", "金额", "交易额", "交易金额(元)", "本次交易金额")
_BANK_NOTE = ("摘要", "交易摘要", "交易说明", "备注", "交易类型", "交易描述", "用途")
_BANK_CP = ("对方户名", "对手信息", "交易对方", "对方账户名", "对方账号", "收款人", "付款人")
_BANK_FLAG = ("借贷标志", "收支标志", "收付标志", "资金方向")


def parse_bank(rows: list[dict]) -> tuple[list[dict], list[str]]:
    items, warnings = [], []
    for r in rows:
        date = parse_date(_first(r, *_BANK_DATE))
        if not date:
            continue

        out_amt = parse_amount(_first(r, *_BANK_OUT))
        in_amt = parse_amount(_first(r, *_BANK_IN))
        if out_amt or in_amt:
            tx_type = "in" if in_amt else "out"
            amount = abs(in_amt or out_amt or 0)
        else:
            raw = parse_amount(_first(r, *_BANK_AMOUNT))
            if raw is None or raw == 0:
                continue
            flag = _first(r, *_BANK_FLAG)
            if "贷" in flag or "收" in flag or "进" in flag:
                tx_type = "in"
            elif "借" in flag or "付" in flag or "出" in flag:
                tx_type = "out"
            else:
                tx_type = "in" if raw > 0 else "out"
            amount = abs(raw)
        if amount == 0:
            continue

        cp = _first(r, *_BANK_CP)
        note = " ".join(p for p in (_first(r, *_BANK_NOTE), cp) if p)
        oid = _digits(_first(r, "交易流水号", "流水号", "交易单号", "凭证号", "交易参考号"))
        items.append({
            "date": date,
            "time": _first(r, *_BANK_DATE),
            "type": tx_type,
            "amount": amount,
            "note": note[:120],
            "counterparty": cp,
            "category_hint": _first(r, *_BANK_NOTE),
            "pay_method": _first(r, "卡号", "账号", "本方账号"),
            "status": _first(r, "交易状态", "状态"),
            "external_id": f"bank:{oid}" if oid else make_external_id("bank", date, tx_type, amount, note),
            "source": "bank",
        })
    if not items:
        warnings.append("没解析出银行流水，可能是列名不常见 —— 可以把文件发我，我加上对应别名")
    return items, warnings


def parse_generic(rows: list[dict]) -> tuple[list[dict], list[str]]:
    """兜底：按通用中文列名尽力解析。"""
    items, warnings = [], []
    for r in rows:
        date = parse_date(_first(r, "日期", "时间", "交易日期", "交易时间", "date"))
        amount = parse_amount(_first(r, "金额", "金额(元)", "交易金额", "发生金额", "amount"))
        if not date or amount is None or amount == 0:
            continue
        raw_dir = _first(r, "收/支", "收支", "类型", "交易类型", "方向")
        if "不计收支" in raw_dir:
            tx_type = "skip"
        elif any(h in raw_dir for h in ("收入", "收", "入账")):
            tx_type = "in"
        else:
            tx_type = "out"
        note = " ".join(p for p in (_first(r, "交易对方", "商品说明", "商品", "摘要"),
                                    _first(r, "备注", "说明")) if p)
        items.append({
            "date": date,
            "time": _first(r, "时间", "交易时间"),
            "type": tx_type,
            "amount": abs(amount),
            "note": note[:120],
            "counterparty": _first(r, "交易对方"),
            "category_hint": _first(r, "交易分类", "分类", "类别"),
            "pay_method": _first(r, "支付方式", "收/付款方式", "账户"),
            "status": _first(r, "交易状态", "当前状态", "状态"),
            "external_id": make_external_id("generic", date, tx_type, abs(amount), note),
            "source": "generic",
        })
    if not items:
        warnings.append("没识别出可导入的行，检查一下是否包含日期列和金额列")
    return items, warnings


_PARSERS = {
    "alipay": parse_alipay,
    "wechat": parse_wechat,
    "bank": parse_bank,
    "generic": parse_generic,
}

# 找表头时的关键字
_HEADER_HINTS = {
    "alipay": ("交易时间", "收/支"),
    "wechat": ("交易时间", "交易单号"),
    "bank": ("日期", "金额"),
    "generic": ("金额",),
}


# ---------------------------------------------------------------- 对外入口

def parse_csv_bytes(raw: bytes, filename: str = "") -> dict:
    """解析一份账单，返回 {source, source_label, rows, warnings, encoding, total_rows}"""
    text, encoding = _read_text(raw)
    lines = text.splitlines()
    warnings: list[str] = []

    source = "generic"
    skip = -1
    # 先靠特征认来源，再用该来源的关键字找表头
    head_guess = sniff(" ".join(lines[:40]))
    order = [head_guess] + [s for s in ("alipay", "wechat", "bank", "generic") if s != head_guess]
    for cand in order:
        idx = _find_header_line(lines, _HEADER_HINTS[cand])
        if idx >= 0:
            source, skip = cand, idx
            break
    if skip < 0:
        return {"source": "generic", "source_label": SOURCES["generic"], "rows": [],
                "warnings": ["认不出这个文件的表头，可能不是账单 CSV"], "encoding": encoding,
                "total_rows": 0}

    rows = _csv_rows(text, skip)
    items, extra = _PARSERS[source](rows)
    warnings += extra
    if skip > 0:
        warnings.insert(0, f"识别为{SOURCES[source]}账单，已跳过前 {skip} 行说明")

    return {
        "source": source,
        "source_label": SOURCES[source],
        "rows": items,
        "warnings": warnings,
        "encoding": encoding,
        "total_rows": len(rows),
        "filename": filename,
    }


def open_bill(filename: str, raw: bytes, zip_password: str = "") -> tuple[str, bytes]:
    """如果是 zip（微信账单的常见形态）就解开，返回 (真实文件名, 内容)。

    微信导出的 zip 是标准 ZipCrypto 加密，密码在微信的「账单下载」通知里。
    """
    if not zipfile.is_zipfile(io.BytesIO(raw)):
        return filename, raw
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        names = [n for n in zf.namelist() if not n.startswith("__MACOSX") and not n.endswith("/")]
        if not names:
            raise ValueError("压缩包里没有文件")
        target = names[0]
        pwd = zip_password.encode("utf-8") if zip_password else None
        try:
            data = zf.read(target, pwd=pwd)
        except RuntimeError as e:
            if "password" in str(e).lower() or "encrypted" in str(e).lower():
                raise ValueError("这个压缩包有密码。微信账单的密码在微信「账单下载」的通知里，"
                                 "填到「压缩包密码」再试一次") from e
            raise
        return target, data
