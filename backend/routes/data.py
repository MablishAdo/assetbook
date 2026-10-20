"""分类、规则、流水、预算、导入导出。账户功能已在 v2.0 移除。"""

from __future__ import annotations

import asyncio
import csv
import io
import os
from datetime import date

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import StreamingResponse

import db
import importer
import security
import services as svc
from models import CategoryIn, ClassifyIn, ImportCommitIn, RuleIn, TxIn

router = APIRouter(prefix="/api", tags=["data"], dependencies=[Depends(security.require_auth)])


# ---------- 分类 ----------

@router.get("/categories")
def list_categories():
    return {"items": [dict(r) for r in db.q("SELECT * FROM categories ORDER BY kind, sort, id")]}


@router.post("/categories", status_code=201)
def create_category(payload: CategoryIn):
    try:
        cur = db.execute(
            "INSERT INTO categories (name, kind, color, sort) VALUES (?, ?, ?, ?)",
            (payload.name, payload.kind, payload.color or "#6f6a5e", payload.sort),
        )
    except Exception as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"分类已存在：{exc}") from exc
    return {"id": cur.lastrowid}


@router.delete("/categories/{category_id}", status_code=204)
def delete_category(category_id: int):
    db.execute("DELETE FROM categories WHERE id = ?", (category_id,))
    return None


# ---------- 规则 ----------

@router.get("/rules")
def list_rules():
    rows = db.q(
        "SELECT r.*, c.name AS category_name FROM rules r "
        "LEFT JOIN categories c ON c.id = r.category_id ORDER BY r.priority, r.id",
    )
    return {"items": [dict(r) for r in rows]}


@router.post("/rules", status_code=201)
def create_rule(payload: RuleIn):
    cur = db.execute(
        "INSERT INTO rules (keyword, category_id, priority, enabled) VALUES (?, ?, ?, ?)",
        (payload.keyword, payload.category_id, payload.priority, payload.enabled),
    )
    return {"id": cur.lastrowid}


@router.delete("/rules/{rule_id}", status_code=204)
def delete_rule(rule_id: int):
    db.execute("DELETE FROM rules WHERE id = ?", (rule_id,))
    return None


# ---------- 自动分类预览 ----------

@router.post("/classify")
def classify(payload: ClassifyIn):
    cid, how = svc.classify(payload.note, payload.type, payload.use_ai)
    name = None
    if cid:
        row = db.q1("SELECT name FROM categories WHERE id = ?", (cid,))
        name = row["name"] if row else None
    return {"category_id": cid, "category_name": name, "by": how}


# ---------- 流水 ----------

@router.get("/transactions")
def list_transactions(
    limit: int = Query(100, ge=1, le=1000),
    offset: int = 0,
    q: str | None = None,
    type: str | None = None,
    category_id: int | None = None,
    month: str | None = None,
):
    where, params = ["1 = 1"], []
    if q:
        where.append("(t.note LIKE ? OR c.name LIKE ?)")
        params += [f"%{q}%", f"%{q}%"]
    if type in ("in", "out"):
        where.append("t.type = ?")
        params.append(type)
    if category_id:
        where.append("t.category_id = ?")
        params.append(category_id)
    if month:
        where.append("substr(t.date, 1, 7) = ?")
        params.append(month)
    clause = " AND ".join(where)

    rows = db.q(
        f"""
        SELECT t.*, c.name AS category_name, c.color AS category_color
        FROM transactions t
        LEFT JOIN categories c ON c.id = t.category_id
        WHERE {clause}
        ORDER BY t.date DESC, t.id DESC LIMIT ? OFFSET ?
        """,
        (*params, limit, offset),
    )
    total = db.q1(
        f"""SELECT COUNT(*) AS n,
                   COALESCE(SUM(CASE WHEN t.type='in'  THEN t.amount END),0) AS si,
                   COALESCE(SUM(CASE WHEN t.type='out' THEN t.amount END),0) AS so
            FROM transactions t LEFT JOIN categories c ON c.id = t.category_id
            WHERE {clause}""",
        tuple(params),
    )
    return {
        "items": [dict(r) for r in rows],
        "total": total["n"],
        "sum_in": round(float(total["si"]), 2),
        "sum_out": round(float(total["so"]), 2),
    }


def _resolve_category(payload: TxIn) -> int | None:
    if payload.category_id:
        return payload.category_id
    if not payload.autocategorize:
        return None
    cid, _ = svc.classify(payload.note or "", payload.type)
    return cid


@router.post("/transactions", status_code=201)
def create_transaction(payload: TxIn):
    cid = _resolve_category(payload)
    cur = db.execute(
        "INSERT INTO transactions (date, type, amount, category_id, note, source, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (payload.date, payload.type, payload.amount, cid, payload.note,
         payload.source, svc.today()),
    )
    return {"id": cur.lastrowid, "category_id": cid}


@router.patch("/transactions/{tx_id}")
def update_transaction(tx_id: int, payload: TxIn):
    db.execute(
        "UPDATE transactions SET date = ?, type = ?, amount = ?, category_id = ?, note = ? "
        "WHERE id = ?",
        (payload.date, payload.type, payload.amount, _resolve_category(payload),
         payload.note, tx_id),
    )
    return {"ok": True}


@router.delete("/transactions/{tx_id}", status_code=204)
def delete_transaction(tx_id: int):
    db.execute("DELETE FROM transactions WHERE id = ?", (tx_id,))
    return None


# ---------- 导入 ----------

@router.get("/import/sources")
def import_sources():
    """支持的账单来源 + 收件目录状态。"""
    import intake
    import parsers

    return {
        "sources": [{"value": k, "label": v} for k, v in parsers.SOURCES.items()],
        "batches": importer.list_batches(),
        "inbox": intake.inbox_status(),
        "mail_configured": intake.imap_config() is not None,
    }


@router.post("/import/preview")
async def import_preview(
    file: UploadFile = File(...),
    zip_password: str = Form(""),
):
    """上传账单，解析并预演：识别来源、自动分类、标出重复。"""
    raw = await file.read()
    if not raw:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "空文件")
    got = importer.import_bytes(raw, file.filename or "bill.csv",
                                zip_password=zip_password, dry_run=True)
    if not got["ok"]:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, got.get("error", "解析失败"))
    return got


@router.post("/import/commit")
def import_commit(payload: ImportCommitIn):
    """把确认过的行写进流水（重复单号会自动跳过）。"""
    rows = [r.model_dump() for r in payload.rows]
    return importer.commit(rows, payload.label)


@router.delete("/import/batch/{batch}")
def import_undo(batch: str):
    """撤销一整次导入。"""
    return {"removed": importer.undo_batch(batch)}


@router.post("/import/inbox/scan")
async def import_scan_inbox():
    """立刻扫一次收件目录。"""
    import intake

    return await asyncio.to_thread(intake.scan_inbox)


@router.post("/import/mail/fetch")
async def import_fetch_mail():
    """立刻去邮箱收一次账单。"""
    import intake

    return await asyncio.to_thread(intake.fetch_mail)


# ---------- 导出 ----------

@router.get("/export/transactions.csv")
def export_transactions():
    rows = db.q(
        """
        SELECT t.date, t.type, t.amount, c.name AS category, t.note
        FROM transactions t
        LEFT JOIN categories c ON c.id = t.category_id
        ORDER BY t.date DESC, t.id DESC
        """,
    )
    buf = io.StringIO()
    buf.write("\ufeff")
    writer = csv.writer(buf)
    writer.writerow(["日期", "类型", "金额", "分类", "备注"])
    for r in rows:
        writer.writerow([r["date"], {"in": "收入", "out": "支出"}.get(r["type"], r["type"]),
                         r["amount"], r["category"] or "", r["note"] or ""])
    buf.seek(0)
    fname = f"assetbook-transactions-{date.today().isoformat()}.csv"
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": f'attachment; filename="{fname}"'})
