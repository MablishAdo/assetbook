"""快捷交易逻辑的端到端自测（不碰真实数据库，跑在临时库上）。

跑法：
    cd backend && ../.venv/bin/python -m tests.test_trade
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 临时数据库 —— 别把测试数据写进真账本
_tmp = tempfile.mkdtemp(prefix="assetbook-test-")
os.environ["ASSETBOOK_DB"] = str(Path(_tmp) / "test.db")

import db          # noqa: E402
import trade_service as ts   # noqa: E402

FAIL = []


def check(label, got, want):
    if got != want:
        FAIL.append(f"{label}: 得到 {got!r}，期望 {want!r}")
        print(f"  ✗ {label}: 得到 {got!r}，期望 {want!r}")
    else:
        print(f"  ✓ {label} = {got!r}")


def approx(label, got, want, tol=1e-6):
    if abs(got - want) > tol:
        FAIL.append(f"{label}: 得到 {got!r}，期望 ≈{want!r}")
        print(f"  ✗ {label}: 得到 {got!r}，期望 ≈{want!r}")
    else:
        print(f"  ✓ {label} ≈ {got!r}")


def main():
    db.init_db()

    print("\n[1] 第一次买入 000001 100 股 @100 → 新建持仓")
    r = ts.apply("000001 买入 100 股 成本价 100")
    check("新建", r["is_new"], True)
    # 有网时行情接口会带出中文名；断网时兜底用代码，两种都算数
    check("名称非空", bool(r["name"]), True)
    approx("份额", r["after"]["units"], 100)
    approx("成本价", r["after"]["cost_price"], 100)

    row = db.q1("SELECT * FROM holdings WHERE code = '000001'")
    approx("库里份额", row["units"], 100)
    approx("库里成本价", row["cost_price"], 100)

    print("\n[2] 再买 000001 100 股 @90 → 合并，份额 200，成本价 95")
    r = ts.apply("000001 买入 100 股 成本价 90")
    check("合并（不是新建）", r["is_new"], False)
    approx("份额", r["after"]["units"], 200)
    approx("成本价", r["after"]["cost_price"], 95)

    row = db.q1("SELECT * FROM holdings WHERE code = '000001'")
    approx("库里份额", row["units"], 200)
    approx("库里成本价", row["cost_price"], 95)
    check("持仓条数没变", db.q1("SELECT COUNT(*) n FROM holdings")["n"], 1)

    print("\n[3] 再买 000001 300 股 @96 → 份额 500，成本价 (200×95+300×96)/500 = 95.6")
    r = ts.apply("000001 买入 300 股 成本价 96")
    approx("份额", r["after"]["units"], 500)
    approx("成本价", r["after"]["cost_price"], 95.6)

    print("\n[4] 卖出 000001 200 股 @110 → 份额 300，成本价不变 95.6，实现盈亏 (110-95.6)×200")
    r = ts.apply("000001 卖出 200 股 110")
    approx("份额", r["after"]["units"], 300)
    approx("成本价", r["after"]["cost_price"], 95.6)
    approx("实现盈亏", r["realized"], (110 - 95.6) * 200)

    row = db.q1("SELECT * FROM holdings WHERE code = '000001'")
    approx("库里份额", row["units"], 300)
    approx("库里成本价", row["cost_price"], 95.6)

    print("\n[5] 卖超了要拦下来")
    try:
        ts.apply("000001 卖出 9999 股 110")
        FAIL.append("卖超了没被拦住")
        print("  ✗ 卖超了没被拦住")
    except ts.TradeError as e:
        print(f"  ✓ 拦住了：{e}")

    print("\n[6] 流水记了 4 笔（1 新建 + 2 加仓 + 1 卖出）")
    trades = ts.recent_trades(50)
    check("流水条数", len(trades), 4)
    check("最新一笔方向", trades[0]["side"], "sell")
    approx("最新一笔实现盈亏", trades[0]["realized"], (110 - 95.6) * 200)

    print("\n[7] 清仓 000001 → 持仓被删，流水还在")
    r = ts.apply("000001 清仓")
    approx("卖光份额", r["after"]["units"], 0)
    check("持仓已删", db.q1("SELECT COUNT(*) n FROM holdings WHERE code='000001'")["n"], 0)
    check("流水保留", len(ts.recent_trades(50)), 5)

    print("\n[8] 清仓后再买 → 重新新建")
    r = ts.apply("000001 买入 50 股 20")
    check("重新新建", r["is_new"], True)
    approx("份额", r["after"]["units"], 50)
    approx("成本价", r["after"]["cost_price"], 20)

    print("\n[9] 解析器边界")
    p = ts.preview("000001 买入")
    check("缺数量被拦", p["ok"], False)
    p = ts.preview("买入 100 股 12")
    check("缺代码被拦", p["ok"], False)
    p = ts.preview("000001 买入 100 股")
    check("没写价格 → 用现价兜底", p["ok"], True)

    print("\n[9b] 成本价与现价是两回事")
    # 买入价 20，现价（行情）可能是别的数 —— 成本价必须记 20，不能被现价覆盖
    ts.apply("600519 买入 100 股 成本价 20")
    row = db.q1("SELECT * FROM holdings WHERE code = '600519'")
    approx("成本价记成交价", row["cost_price"], 20)
    check("现价不等于成本价（用的是行情价）", row["price"] != 20, True)
    print(f"  · 成本价 {row['cost_price']}，现价 {row['price']}")

    print("\n[10] 名称匹配已有持仓")
    db.execute("UPDATE holdings SET code = NULL WHERE code = '000001'")
    p = ts.preview("000001 买入 100 股 12")   # code 查不到（已置空），名字也没有
    print(f"  · code 被置空后 → ok={p['ok']} {p.get('error', '')}")

    print("\n" + "=" * 52)
    if FAIL:
        print(f"❌ {len(FAIL)} 项不通过：")
        for f in FAIL:
            print("  - " + f)
        return 1
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
