"""账单导入引擎 —— 把解析出来的行安全地写进流水表。

三件事要做好，否则「自动导入」就是个坑：

  去重    每条账单行都带一个 external_id（原始单号，或兜底哈希）。
          入库前先查一遍，已有就跳过 —— 这样同一份账单反复导入、
          或者两份时间范围重叠的账单，都不会把账目翻倍。

  对账   账单里的「收/付款方式」长这样：招商银行(1234)、工商银行8888、
          余额宝、花呗、零钱。用 payee_rules 里的关键词匹配，自动落到
          对应的 AssetBook 账户；没配规则的行用导入时选的默认账户。

  可回滚 每行写进去时都记下 source 和 external_id，导入完返回批次摘要，
          后悔了可以按 batch 删掉。
"""

from __future__ import annotations

import json
from datetime import datetime

import db
import parsers
import services as svc


# ---------------------------------------------------------------- 账户映射

def list_payee_rules() -> list[dict]:
    rows = db.q(
        "SELECT r.*, a.name AS account_name FROM payee_rules r "
        "LEFT JOIN accounts a ON a.id = r.account_id "
        "ORDER BY r.priority, r.id",
    )
    return [dict(r) for r in rows]


def add_payee_rule(pattern: str, account_id: int, priority: int = 100, source: str | None = None) -> int:
    cur = db.execute(
        "INSERT INTO payee_rules (pattern, account_id, priority, source) VALUES (?, ?, ?, ?)",
        (pattern.strip(), account_id, priority, source or None),
    )
    return cur.lastrowid


def delete_payee_rule(rule_id: int) -> None:
    db.execute("DELETE FROM payee_rules WHERE id = ?", (rule_id,))


def match_account(pay_method: str, source: str, rules: list[dict] | None = None) -> dict | None:
    """按「收/付款方式」找账户。返回命中的规则（含 account_id / pattern）。"""
    pm = (pay_method or "").strip()
    if not pm:
        return None
    for r in (rules if rules is not None else list_payee_rules()):
        if r.get("source") and r["source"] != source:
            continue
        if r["pattern"] and r["pattern"] in pm:
            return r
    return None


# ---------------------------------------------------------------- 预览

def prepare(items: list[dict], default_account_id: int | None = None,
            source: str = "generic") -> dict:
    """给解析出来的行补上：账户归属、分类、是否重复。

    返回 {"rows": [...], "stats": {...}}，rows 里每行都带了决定好的
    account_id / category_id / duplicate，供前端展示和确认。
    """
    rules = list_payee_rules()
    seen_in_file: set[str] = set()
    rows = []

    for it in items:
        ext = it.get("external_id") or ""
        duplicate = False
        if ext:
            if ext in seen_in_file:
                duplicate = True          # 同一文件内重复
            else:
                seen_in_file.add(ext)
                hit = db.q1("SELECT id FROM transactions WHERE external_id = ?", (ext,))
                duplicate = hit is not None

        matched = match_account(it.get("pay_method") or "", it.get("source") or source, rules)
        account_id = matched["account_id"] if matched else default_account_id

        category_id, how = (None, "skip")
        if it["type"] in ("in", "out"):
            category_id, how = svc.classify(it.get("note") or "", it["type"])

        rows.append({
            **it,
            "account_id": account_id,
            "account_by": matched["pattern"] if matched else None,
            "category_id": category_id,
            "category_by": how,
            "duplicate": duplicate,
            "selected": not duplicate and it["type"] != "skip" and account_id is not None,
        })

    done = [r for r in rows if not r["duplicate"] and r["type"] != "skip"]
    stats = {
        "total": len(rows),
        "new": len(done),
        "duplicate": sum(1 for r in rows if r["duplicate"]),
        "skipped": sum(1 for r in rows if r["type"] == "skip"),
        "no_account": sum(1 for r in done if r["account_id"] is None),
        "sum_in": round(sum(r["amount"] for r in done if r["type"] == "in"), 2),
        "sum_out": round(sum(r["amount"] for r in done if r["type"] == "out"), 2),
        "unmatched_pay_methods": sorted({
            r["pay_method"] for r in done
            if r.get("pay_method") and not r.get("account_by")
        }),
    }
    return {"rows": rows, "stats": stats}


# ---------------------------------------------------------------- 入库

def commit(rows: list[dict], source_label: str = "") -> dict:
    """把（可能被用户改过账户归属的）行写进流水。重复的单号自动跳过。"""
    imported = skipped = failed = 0
    batch = f"{source_label or 'import'}-{datetime.now().strftime('%Y%m%d%H%M%S')}"
    errors: list[str] = []

    with db.tx() as conn:
        for r in rows:
            if not r.get("selected"):
                continue
            account_id = r.get("account_id")
            if not account_id:
                failed += 1
                continue
            ext = r.get("external_id") or None
            if ext:
                hit = conn.execute(
                    "SELECT id FROM transactions WHERE external_id = ?", (ext,),
                ).fetchone()
                if hit:
                    skipped += 1
                    continue
            try:
                conn.execute(
                    "INSERT INTO transactions (date, type, amount, account_id, category_id, "
                    "note, source, external_id, batch, raw, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (r["date"], r["type"], r["amount"], account_id,
                     r.get("category_id"), (r.get("note") or "")[:200],
                     r.get("source") or "import", ext, batch,
                     json.dumps({k: r.get(k) for k in
                                 ("pay_method", "status", "counterparty", "category_hint", "time")},
                                ensure_ascii=False),
                     svc.today()),
                )
                imported += 1
            except Exception as e:                      # noqa: BLE001 — 单行失败不拖垮整批
                failed += 1
                if len(errors) < 5:
                    errors.append(f"{r.get('date')} {r.get('note', '')[:20]}：{e}")

    if imported:
        db.snapshot_networth()
    return {"imported": imported, "skipped": skipped, "failed": failed,
            "batch": batch, "errors": errors}


def undo_batch(batch: str) -> int:
    """按批次号撤销整次导入。"""
    cur = db.execute("DELETE FROM transactions WHERE batch = ?", (batch,))
    n = cur.rowcount or 0
    if n:
        db.snapshot_networth()
    return n


def list_batches(limit: int = 20) -> list[dict]:
    """最近几次导入的批次，供「撤销」用。"""
    rows = db.q(
        "SELECT batch, COUNT(*) AS n, MIN(date) AS from_date, MAX(date) AS to_date, "
        "       MAX(source) AS source, MAX(created_at) AS created_at "
        "FROM transactions WHERE batch IS NOT NULL GROUP BY batch "
        "ORDER BY MAX(id) DESC LIMIT ?",
        (limit,),
    )
    return [dict(r) for r in rows]


# ---------------------------------------------------------------- 一步到位

def import_bytes(raw: bytes, filename: str = "", default_account_id: int | None = None,
                 zip_password: str = "", dry_run: bool = True,
                 auto_commit: bool = False) -> dict:
    """解析 + 预演（可选直接入库）。自动导入通道走的也是这个函数。"""
    try:
        real_name, data = parsers.open_bill(filename, raw, zip_password)
        parsed = parsers.parse_csv_bytes(data, real_name)
    except Exception as e:                              # noqa: BLE001 — 文件坏了也不能炸
        return {"ok": False, "error": f"{type(e).__name__}: {e}",
                "rows": [], "stats": {}, "warnings": []}

    prepared = prepare(parsed["rows"], default_account_id, parsed["source"])
    result = {
        "ok": True,
        "filename": real_name,
        "source": parsed["source"],
        "source_label": parsed["source_label"],
        "encoding": parsed["encoding"],
        "warnings": parsed["warnings"],
        "rows": prepared["rows"],
        "stats": prepared["stats"],
        "committed": None,
    }
    if auto_commit and not dry_run:
        result["committed"] = commit(prepared["rows"], parsed["source_label"])
        result["rows"] = []            # 入库后不用回传明细
    return result
