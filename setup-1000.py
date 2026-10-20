#!/usr/bin/env python3
"""AssetBook「每月只花 1000」一键配置。

v2.0 起账户功能已下线，本脚本只建跟钱有关的三样：
  1. 每月通用预算合计 1000（餐饮 520 / 交通 100 / 居住 120 / 通讯 40 / 购物 120 / 医疗 60 / 其他 40）
  2. 补齐分类（含不占预算的「房贷」）
  3. 分类规则：备注里含「买菜 / 水电气 / 话费…」的自动归类

可以重复跑，不会重复建。

用法：
    python3 setup-1000.py                          # 连 http://127.0.0.1:8000，密码自动找
    python3 setup-1000.py 你的密码                  # 连本机 8000
    python3 setup-1000.py 你的密码 http://ip:8000   # 指定地址（比如部署在别的机器上）

密码的查找顺序：命令行参数 → 环境变量 ASSETBOOK_PASSWORD → 同目录 .env 里的 ASSETBOOK_PASSWORD → 交互输入。

想改预算或规则，直接改下面两个列表再跑一次（预算会覆盖，分类与规则不会重复建）。
"""

from __future__ import annotations

import getpass
import http.cookiejar
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

# 需要用到、但【不设预算】的分类（一设预算就会占那 1000，所以单独列）
EXTRA_CATEGORIES: list[str] = [
    "房贷",   # 月供：固定大支出，按「支出」记，不算在生活费 1000 里
]

# 每月通用预算，合计 1000 —— 想改就改数字
BUDGET: list[tuple[str, float]] = [
    ("餐饮", 520),   # 吃饭，自己做为主
    ("交通", 100),   # 通勤
    ("居住", 120),   # 水电燃气
    ("通讯", 40),    # 话费/流量
    ("购物", 120),   # 日用品、洗护
    ("医疗", 60),    # 常备药
    ("其他", 40),    # 机动，别花掉，留给意外
]

# 记账备注关键词 → 分类：备注里包含左边这个词就自动归到右边分类（已有的不会重复建）
RULES: list[tuple[str, str]] = [
    ("买菜", "餐饮"), ("菜市场", "餐饮"), ("食堂", "餐饮"), ("早餐", "餐饮"),
    ("午饭", "餐饮"), ("晚饭", "餐饮"), ("水果", "餐饮"), ("面条", "餐饮"),
    ("水费", "居住"), ("电费", "居住"), ("燃气", "居住"), ("水电气", "居住"),
    ("话费", "通讯"), ("流量", "通讯"), ("手机费", "通讯"),
    ("地铁", "交通"), ("公交", "交通"), ("单车", "交通"), ("加油", "交通"),
    ("日用品", "购物"), ("纸巾", "购物"), ("洗发", "购物"), ("牙膏", "购物"),
    ("药店", "医疗"), ("挂号", "医疗"), ("体检", "医疗"),
    ("房贷", "房贷"), ("月供", "房贷"),
]


class Api:
    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar),
        )

    def call(self, method: str, path: str, body: dict | None = None) -> tuple[int, object]:
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json"} if data else {}
        req = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        try:
            with self.opener.open(req, timeout=20) as res:
                raw = res.read()
                return res.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                return e.code, json.loads(raw)
            except Exception:  # noqa: BLE001
                return e.code, raw.decode("utf-8", "replace")
        except urllib.error.URLError as e:
            print(f"× 连不上 {self.base}：{e.reason}")
            print("  先把服务跑起来（docker compose up -d，或 make dev）再执行本脚本。")
            sys.exit(1)

    def get(self, path: str):
        return self.call("GET", path)

    def post(self, path: str, body: dict):
        return self.call("POST", path, body)


def find_password() -> str:
    pw = os.environ.get("ASSETBOOK_PASSWORD", "").strip()
    if pw:
        return pw
    env_file = Path(__file__).resolve().parent / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("ASSETBOOK_PASSWORD="):
                value = line.split("=", 1)[1].strip().strip('"').strip("'")
                if value and value != "change-me-please":
                    return value
    return getpass.getpass("AssetBook 登录密码：")


def main() -> None:
    args = [a for a in sys.argv[1:]]
    password = args[0] if args else find_password()
    base = args[1] if len(args) > 1 else os.environ.get("ASSETBOOK_URL", "http://127.0.0.1:8000")

    api = Api(base)
    print(f"→ 连接 {base}")

    # 1. 登录
    code, res = api.get("/api/auth/state")
    if code != 200:
        print(f"× 读不到登录状态（HTTP {code}），确认地址对不对。")
        sys.exit(1)
    if not res.get("has_password"):
        code, _ = api.post("/api/auth/setup", {"password": password})
        if code not in (200, 201):
            print("× 设置密码失败。")
            sys.exit(1)
        print("· 已设置登录密码")
    else:
        code, res = api.post("/api/auth/login", {"password": password})
        if code != 200:
            print("× 密码不对，登录失败。")
            sys.exit(1)
        print("· 已登录")

    # 2. 分类（复用内置的，缺哪个补哪个）
    code, res = api.get("/api/categories")
    cats = {c["name"]: c["id"] for c in (res.get("items", []) if isinstance(res, dict) else [])}
    for name in [n for n, _ in BUDGET] + EXTRA_CATEGORIES:
        if name not in cats:
            code, res = api.post("/api/categories", {"name": name, "kind": "expense", "color": "#1c6b52"})
            if code in (200, 201):
                cats[name] = res["id"]
                print(f"· 已建分类「{name}」")

    # 3. 分类规则（备注里含关键词就自动归类）
    code, res = api.get("/api/rules")
    keywords = {r["keyword"] for r in (res.get("items", []) if isinstance(res, dict) else [])}
    added = 0
    for keyword, cat_name in RULES:
        cid = cats.get(cat_name)
        if not cid or keyword in keywords:
            continue
        api.post("/api/rules", {"keyword": keyword, "category_id": cid, "priority": 100})
        added += 1
    if added:
        print(f"· 补了 {added} 条分类规则（买菜、水电气、话费…）")

    # 4. 预算（month='*' = 每月通用，重复跑会覆盖成同样的值）
    total = 0.0
    for name, amount in BUDGET:
        cid = cats.get(name)
        if not cid:
            print(f"× 找不到分类「{name}」，跳过")
            continue
        code, res = api.post("/api/budgets", {"month": "*", "category_id": cid, "amount": amount})
        if code not in (200, 201):
            print(f"× 设预算失败（{name}）：{res}")
            continue
        total += amount
        print(f"· 预算 {name:<4} {amount:>6.0f}")

    print(f"\n√ 好了。每月预算合计 {total:.0f}，分类规则补了 {added} 条。")
    print("  「房贷」是分类：月供按「支出」记，分类选它，不占那 1000。")
    print("  预算按分类合计，超没超 1000 在「设置 → 月度预算」里一眼看得出。")


if __name__ == "__main__":
    main()
