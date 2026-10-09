#!/usr/bin/env python3
"""AssetBook「每月只花 1000」一键配置。

跑完会建好：
  1. 账户「邮政储蓄」——当生活费钱包用
  2. 每月通用预算合计 1000（餐饮 520 / 交通 100 / 居住 120 / 通讯 40 / 购物 120 / 医疗 60 / 其他 40）
  3. 一条映射规则：账单里支付方式含「邮政」的，自动归到邮储账户

可以重复跑，不会重复建。

用法：
    python3 setup-1000.py                          # 连 http://127.0.0.1:8000，密码自动找
    python3 setup-1000.py 你的密码                  # 连本机 8000
    python3 setup-1000.py 你的密码 http://ip:8000   # 指定地址（比如部署在别的机器上）

密码的查找顺序：命令行参数 → 环境变量 ASSETBOOK_PASSWORD → 同目录 .env 里的 ASSETBOOK_PASSWORD → 交互输入。
"""

from __future__ import annotations

import getpass
import http.cookiejar
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# 合计 1000 —— 想改就直接改这里的数字，改完重跑一次即可
BUDGET: list[tuple[str, float]] = [
    ("餐饮", 520),   # 吃饭，自己做为主
    ("交通", 100),   # 通勤
    ("居住", 120),   # 水电燃气
    ("通讯", 40),    # 话费/流量
    ("购物", 120),   # 日用品、洗护
    ("医疗", 60),    # 常备药
    ("其他", 40),    # 机动，别花掉，留给意外
]
ACCOUNT = "邮政储蓄"
ACCOUNT_KIND = "bank"
PAYEE_PATTERN = "邮政"       # 支付宝/微信账单里的「邮政储蓄卡」认到邮储账户


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

    # 2. 账户
    code, res = api.get("/api/accounts?include_archived=false")
    accounts = res.get("items", []) if isinstance(res, dict) else []
    account = next((a for a in accounts if a["name"] == ACCOUNT), None)
    if account:
        account_id = account["id"]
        print(f"· 账户「{ACCOUNT}」已存在")
    else:
        code, res = api.post("/api/accounts", {
            "name": ACCOUNT, "kind": ACCOUNT_KIND, "currency": "CNY",
            "base_balance": 0, "sort": 0,
        })
        if code not in (200, 201):
            print(f"× 建账户失败：{res}")
            sys.exit(1)
        account_id = res["id"]
        print(f"· 已建账户「{ACCOUNT}」")

    # 3. 分类（复用内置的，缺哪个补哪个）
    code, res = api.get("/api/categories")
    cats = {c["name"]: c["id"] for c in (res.get("items", []) if isinstance(res, dict) else [])}
    color = "#1c6b52"
    for name, _amount in BUDGET:
        if name not in cats:
            code, res = api.post("/api/categories", {"name": name, "kind": "expense", "color": color})
            if code in (200, 201):
                cats[name] = res["id"]
                print(f"· 已建分类「{name}」")

    # 4. 预算（month='*' = 每月通用，重复跑会覆盖同样的值）
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

    # 5. 映射规则：账单里「邮政储蓄卡」→ 邮储账户
    code, res = api.get("/api/payee-rules")
    rules = res.get("items", []) if isinstance(res, dict) else []
    if not any(r["pattern"] == PAYEE_PATTERN and r["account_id"] == account_id for r in rules):
        api.post("/api/payee-rules", {"pattern": PAYEE_PATTERN, "account_id": account_id, "priority": 10})
        print(f"· 映射规则：「{PAYEE_PATTERN}」→ {ACCOUNT}")

    print(f"\n√ 好了。每月预算合计 {total:.0f}")
    print("  接下来：每月转 1000 进邮储，花钱尽量走这张卡；")
    print("  月末在界面「设置 → 账户 → 校准」把余额写成实际值，剩多少一目了然。")


if __name__ == "__main__":
    main()
