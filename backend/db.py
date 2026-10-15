"""数据库连接与表结构。

只用 Python 标准库 sqlite3，零 ORM 依赖，方便自部署与二次开发。
数据文件默认放在 DATA_DIR/assetbook.db（Docker 中挂载 /data 卷即可持久化）。
"""

from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Iterator

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("DATA_DIR", BASE_DIR / "data"))
DB_PATH = Path(os.environ.get("ASSETBOOK_DB", DATA_DIR / "assetbook.db"))

_local = threading.local()

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS accounts (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  name          TEXT    NOT NULL UNIQUE,
  kind          TEXT    NOT NULL DEFAULT 'bank',   -- cash/bank/payment/broker/credit/loan/other
  currency      TEXT    NOT NULL DEFAULT 'CNY',
  base_balance  REAL    NOT NULL DEFAULT 0,
  base_date     TEXT,
  sort          INTEGER NOT NULL DEFAULT 0,
  archived      INTEGER NOT NULL DEFAULT 0,
  created_at    TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS categories (
  id     INTEGER PRIMARY KEY AUTOINCREMENT,
  name   TEXT    NOT NULL UNIQUE,
  kind   TEXT    NOT NULL DEFAULT 'expense',       -- expense / income
  color  TEXT,
  sort   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS transactions (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  date          TEXT    NOT NULL,
  type          TEXT    NOT NULL,                  -- in / out / transfer
  amount        REAL    NOT NULL,
  account_id    INTEGER NOT NULL,
  to_account_id INTEGER,
  category_id   INTEGER,
  note          TEXT,
  source        TEXT    NOT NULL DEFAULT 'manual', -- manual / import / inbox / mail / api
  external_id   TEXT,                              -- 账单原始单号，用来去重
  batch         TEXT,                              -- 导入批次号，用来整批撤销
  raw           TEXT,                              -- 原始账单行（JSON），排查用
  created_at    TEXT    NOT NULL,
  FOREIGN KEY (account_id)    REFERENCES accounts (id) ON DELETE CASCADE,
  FOREIGN KEY (to_account_id) REFERENCES accounts (id) ON DELETE SET NULL,
  FOREIGN KEY (category_id)   REFERENCES categories (id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_tx_date    ON transactions (date);
CREATE INDEX IF NOT EXISTS idx_tx_account ON transactions (account_id);
-- 去重索引见 INDEX_PATCHES：必须等 external_id 列的迁移跑完才能建

CREATE TABLE IF NOT EXISTS rules (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  keyword     TEXT    NOT NULL,
  category_id INTEGER NOT NULL,
  priority    INTEGER NOT NULL DEFAULT 100,
  enabled     INTEGER NOT NULL DEFAULT 1,
  FOREIGN KEY (category_id) REFERENCES categories (id) ON DELETE CASCADE
);

-- 账单里的「收/付款方式」→ 账户。比如「招商银行(1234)」记到招行卡
CREATE TABLE IF NOT EXISTS payee_rules (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  pattern    TEXT    NOT NULL,                     -- 支付方式里包含这个词就命中
  account_id INTEGER NOT NULL,
  priority   INTEGER NOT NULL DEFAULT 100,
  source     TEXT,                                 -- 限定来源（alipay/wechat/bank），空=不限
  FOREIGN KEY (account_id) REFERENCES accounts (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS holdings (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  code       TEXT,
  name       TEXT NOT NULL,
  kind       TEXT NOT NULL DEFAULT 'fund',         -- fund/stock/bond/gold/cash/other
  market     TEXT NOT NULL DEFAULT 'auto',         -- auto/cn/fund/hk/us/crypto
  account_id INTEGER,
  units      REAL NOT NULL DEFAULT 0,
  cost_price REAL NOT NULL DEFAULT 0,
  price      REAL NOT NULL DEFAULT 0,
  price_date TEXT,
  note       TEXT,
  FOREIGN KEY (account_id) REFERENCES accounts (id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS budgets (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  month       TEXT    NOT NULL,                    -- 'YYYY-MM'，'*' 表示每月通用
  category_id INTEGER NOT NULL,
  amount      REAL    NOT NULL,
  UNIQUE (month, category_id),
  FOREIGN KEY (category_id) REFERENCES categories (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS networth (
  date        TEXT PRIMARY KEY,
  assets      REAL NOT NULL,
  liabilities REAL NOT NULL,
  net         REAL NOT NULL
);
"""

DEFAULT_CATEGORIES = [
    ("餐饮", "expense", "#b2612e"), ("交通", "expense", "#4a6fa5"),
    ("购物", "expense", "#a85d75"), ("居住", "expense", "#7d5ba6"),
    ("通讯", "expense", "#3e8f83"), ("医疗", "expense", "#b23c2c"),
    ("教育", "expense", "#c08a2d"), ("娱乐", "expense", "#8fae4f"),
    ("人情", "expense", "#a0522d"), ("其他", "expense", "#6f6a5e"),
    ("工资", "income", "#1c6b52"), ("奖金", "income", "#2f8f6f"),
    ("投资收益", "income", "#4f9d7a"), ("其他收入", "income", "#7fb397"),
]

DEFAULT_RULES = [
    ("麦当劳", "餐饮"), ("肯德基", "餐饮"), ("外卖", "餐饮"), ("美团", "餐饮"),
    ("饿了么", "餐饮"), ("咖啡", "餐饮"), ("超市", "购物"), ("淘宝", "购物"),
    ("京东", "购物"), ("地铁", "交通"), ("公交", "交通"), ("打车", "交通"),
    ("滴滴", "交通"), ("加油", "交通"), ("话费", "通讯"), ("宽带", "通讯"),
    ("房租", "居住"), ("水电", "居住"), ("物业", "居住"), ("医院", "医疗"),
    ("药", "医疗"), ("书", "教育"), ("电影", "娱乐"), ("工资", "工资"),
]


def get_conn() -> sqlite3.Connection:
    """每个线程一个连接（sqlite3 默认不允许跨线程共享）。"""
    conn = getattr(_local, "conn", None)
    if conn is None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA)
        _local.conn = conn
    return conn


@contextmanager
def tx() -> Iterator[sqlite3.Connection]:
    conn = get_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def q(sql: str, params: tuple | list = ()) -> list[sqlite3.Row]:
    return get_conn().execute(sql, params).fetchall()


def q1(sql: str, params: tuple | list = ()) -> sqlite3.Row | None:
    return get_conn().execute(sql, params).fetchone()


def execute(sql: str, params: tuple | list = ()) -> sqlite3.Cursor:
    with tx() as conn:
        return conn.execute(sql, params)


def get_meta(key: str, default: str | None = None) -> str | None:
    row = q1("SELECT value FROM meta WHERE key = ?", (key,))
    return row["value"] if row else default


def set_meta(key: str, value: str) -> None:
    execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) "
        "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


SCHEMA_VERSION = "3"

# 老库升级用的补丁：列名 → 建表语句里的定义。加新列写这里就行。
MIGRATIONS: list[tuple[str, str, str]] = [
    ("holdings", "market", "TEXT NOT NULL DEFAULT 'auto'"),
    ("transactions", "external_id", "TEXT"),
    ("transactions", "batch", "TEXT"),
    ("transactions", "raw", "TEXT"),
]

# 建表之后要补的索引（老库没有的）
INDEX_PATCHES = [
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_tx_ext ON transactions (external_id) "
    "WHERE external_id IS NOT NULL",
]


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
    except sqlite3.Error:
        return set()


def _migrate(conn: sqlite3.Connection) -> list[str]:
    """对老库补齐缺失的列与索引，返回实际做了哪些改动（用于日志）。"""
    done: list[str] = []
    for table, column, ddl in MIGRATIONS:
        cols = _columns(conn, table)
        if not cols or column in cols:
            continue                       # 表不存在（建表语句已含新列）或列已存在
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
        done.append(f"{table}.{column}")
    for ddl in INDEX_PATCHES:
        try:
            conn.execute(ddl)
        except sqlite3.Error as e:
            done.append(f"索引补建失败：{e}")
    return done


def init_db() -> None:
    """建表 + 升级老库 + 首次运行灌入默认分类与规则。"""
    conn = get_conn()
    with tx() as c:
        for change in _migrate(c):
            print(f"[db] 已升级字段 {change}")

    if q1("SELECT COUNT(*) AS n FROM categories")["n"] == 0:
        with tx() as c:
            c.executemany(
                "INSERT OR IGNORE INTO categories (name, kind, color) VALUES (?, ?, ?)",
                DEFAULT_CATEGORIES,
            )
            cat = {r["name"]: r["id"] for r in c.execute("SELECT id, name FROM categories")}
            c.executemany(
                "INSERT INTO rules (keyword, category_id, priority) VALUES (?, ?, 100)",
                [(kw, cat[name]) for kw, name in DEFAULT_RULES if name in cat],
            )
    set_meta("schema_version", SCHEMA_VERSION)


def snapshot_networth() -> None:
    """把今天的资产/负债/净值写进 networth 表（同日覆盖）。"""
    from services import compute_summary  # 延迟导入避免循环依赖

    s = compute_summary()
    execute(
        "INSERT INTO networth (date, assets, liabilities, net) VALUES (?, ?, ?, ?) "
        "ON CONFLICT (date) DO UPDATE SET assets = excluded.assets, "
        "liabilities = excluded.liabilities, net = excluded.net",
        (date.today().isoformat(), s["assets"], s["liabilities"], s["net"]),
    )
