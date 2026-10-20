"""数据库连接与表结构。

只用 Python 标准库 sqlite3，零 ORM 依赖，方便自部署与二次开发。
数据文件默认放在 DATA_DIR/assetbook.db（Docker 中挂载 /data 卷即可持久化）。

v2.0 重构说明：
    账户（银行卡余额）整套功能已移除。现在只有两件事——记账、投资。
    transactions 不再挂账户，holdings 也不挂账户；networth 表改成
    portfolio_history，只记投资市值曲线。
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
  type          TEXT    NOT NULL,                  -- in / out
  amount        REAL    NOT NULL,
  category_id   INTEGER,
  note          TEXT,
  source        TEXT    NOT NULL DEFAULT 'manual', -- manual / import / inbox / mail / api
  external_id   TEXT,                              -- 账单原始单号，用来去重
  batch         TEXT,                              -- 导入批次号，用来整批撤销
  raw           TEXT,                              -- 原始账单行（JSON），排查用
  created_at    TEXT    NOT NULL,
  FOREIGN KEY (category_id) REFERENCES categories (id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_tx_date ON transactions (date);
CREATE INDEX IF NOT EXISTS idx_tx_cat  ON transactions (category_id);

CREATE TABLE IF NOT EXISTS rules (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  keyword     TEXT    NOT NULL,
  category_id INTEGER NOT NULL,
  priority    INTEGER NOT NULL DEFAULT 100,
  enabled     INTEGER NOT NULL DEFAULT 1,
  FOREIGN KEY (category_id) REFERENCES categories (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS holdings (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  code       TEXT,
  name       TEXT NOT NULL,
  kind       TEXT NOT NULL DEFAULT 'fund',         -- fund/stock/bond/gold/cash/other
  market     TEXT NOT NULL DEFAULT 'auto',         -- auto/cn/fund/hk/crypto
  units      REAL NOT NULL DEFAULT 0,
  cost_price REAL NOT NULL DEFAULT 0,
  price      REAL NOT NULL DEFAULT 0,
  price_date TEXT,
  note       TEXT
);
CREATE INDEX IF NOT EXISTS idx_hold_code ON holdings (code);

CREATE TABLE IF NOT EXISTS budgets (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  month       TEXT    NOT NULL,                    -- 'YYYY-MM'，'*' 表示每月通用
  category_id INTEGER NOT NULL,
  amount      REAL    NOT NULL,
  UNIQUE (month, category_id),
  FOREIGN KEY (category_id) REFERENCES categories (id) ON DELETE CASCADE
);

-- 投资市值曲线：每天记一次，用于算持仓的时间序列
CREATE TABLE IF NOT EXISTS portfolio_history (
  date         TEXT PRIMARY KEY,
  market_value REAL NOT NULL DEFAULT 0,
  cost_value   REAL NOT NULL DEFAULT 0,
  profit       REAL NOT NULL DEFAULT 0
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


SCHEMA_VERSION = "4"


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
    except sqlite3.Error:
        return set()


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _drop_v4(conn: sqlite3.Connection) -> bool:
    """v2.0：账户功能整体下线。

    老库里 accounts / payee_rules / networth 都要删，transactions 与 holdings
    要去掉账户列 —— SQLite 不能直接 DROP COLUMN 带外键的列（3.35 以下），
    统一走「建新表 → 搬数据 → 换名」。

    注意这里刻意用 PRIMARY KEY 转 INTEGER 而不是 AUTOINCREMENT：
    sqlite_sequence 里残留的行号会让 AUTOINCREMENT 跳过历史 id，
    新插入的流水 id 会从几万开始，对外无影响但没必要留这个坑。
    """
    old = _tables(conn)
    tx_cols = _columns(conn, "transactions")
    hold_cols = _columns(conn, "holdings")
    if not tx_cols and not hold_cols:
        return False
    if "account_id" not in tx_cols and "account_id" not in hold_cols:
        return False                      # 已经迁移过了

    print("[db] 检测到 v1 账户结构，正在迁移到 v2（记账 + 投资）…")

    # PRAGMA foreign_keys 在事务里是空操作，必须先切出事务才生效。
    # 旧连接上已经开的事务要显式结束，否则 DROP TABLE 会被外键约束卡住。
    prev_isolation = conn.isolation_level
    conn.commit()
    conn.isolation_level = None
    conn.execute("PRAGMA foreign_keys=OFF")

    conn.executescript(
        """
        CREATE TABLE transactions_v4 (
          id INTEGER PRIMARY KEY,
          date TEXT NOT NULL,
          type TEXT NOT NULL,
          amount REAL NOT NULL,
          category_id INTEGER,
          note TEXT,
          source TEXT NOT NULL DEFAULT 'manual',
          external_id TEXT,
          batch TEXT,
          raw TEXT,
          created_at TEXT NOT NULL
        );
        CREATE TABLE holdings_v4 (
          id INTEGER PRIMARY KEY,
          code TEXT,
          name TEXT NOT NULL,
          kind TEXT NOT NULL DEFAULT 'fund',
          market TEXT NOT NULL DEFAULT 'auto',
          units REAL NOT NULL DEFAULT 0,
          cost_price REAL NOT NULL DEFAULT 0,
          price REAL NOT NULL DEFAULT 0,
          price_date TEXT,
          note TEXT
        );
        """
    )

    # 转账（transfer）依赖两个账户，账户没了它就没意义，直接丢掉
    conn.execute(
        "INSERT INTO transactions_v4 "
        "(id, date, type, amount, category_id, note, source, external_id, batch, raw, created_at) "
        "SELECT id, date, type, amount, category_id, note, source, external_id, batch, raw, created_at "
        "FROM transactions WHERE type IN ('in','out')"
    )
    conn.execute(
        "INSERT INTO holdings_v4 "
        "(id, code, name, kind, market, units, cost_price, price, price_date, note) "
        "SELECT id, code, name, kind, "
        "       CASE WHEN market IS NULL OR market='' THEN 'auto' ELSE market END, "
        "       units, cost_price, price, price_date, note FROM holdings"
    )

    conn.executescript(
        """
        DROP TABLE transactions;
        DROP TABLE holdings;
        DROP TABLE IF EXISTS payee_rules;
        DROP TABLE IF EXISTS networth;
        DROP TABLE IF EXISTS accounts;
        ALTER TABLE transactions_v4 RENAME TO transactions;
        ALTER TABLE holdings_v4   RENAME TO holdings;

        CREATE INDEX IF NOT EXISTS idx_tx_date ON transactions (date);
        CREATE INDEX IF NOT EXISTS idx_tx_cat  ON transactions (category_id);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_tx_ext ON transactions (external_id)
          WHERE external_id IS NOT NULL;
        CREATE INDEX IF NOT EXISTS idx_hold_code ON holdings (code);
        """
    )
    conn.execute("PRAGMA foreign_keys=ON")
    conn.isolation_level = prev_isolation
    print("[db] v2 迁移完成：已移除账户与转账，保留记账与持仓数据")
    return True


def init_db() -> None:
    """建表 + 老库升级 + 首次运行灌入默认分类与规则。"""
    conn = get_conn()
    with tx() as c:
        _drop_v4(c)
        c.executescript(SCHEMA)

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


def snapshot_portfolio() -> None:
    """把今天的投资市值写进 portfolio_history（同日覆盖）。"""
    from services import holdings_totals      # 延迟导入避免循环依赖

    t = holdings_totals()
    execute(
        "INSERT INTO portfolio_history (date, market_value, cost_value, profit) "
        "VALUES (?, ?, ?, ?) ON CONFLICT (date) DO UPDATE SET "
        "market_value = excluded.market_value, cost_value = excluded.cost_value, "
        "profit = excluded.profit",
        (date.today().isoformat(), t["market_value"], t["cost_value"], t["profit"]),
    )
