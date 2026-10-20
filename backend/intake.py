"""账单自动收取通道 —— 让账单自己跑进来，不用手动点导入。

三条管道：

  1. 收件目录（推荐，最省心）
     把支付宝/微信/银行导出的文件丢进 data/inbox/，后台每隔几分钟扫一次，
     能认出来的自动入库，文件挪到 data/inbox/imported/，认不出的挪到
     data/inbox/failed/ 并留下原因。

  2. 邮箱（IMAP）
     支付宝和微信的账单都是发到你邮箱的。配好 IMAP 账号后，后台定时去收，
     自动挑出 CSV/ZIP 附件导入，微信那个加密 zip 用你给的密码自动解开。
     只认未读邮件，处理完标记已读，不会重复收。

  3. 手动上传（界面上那个「导入账单」按钮）
     走的也是同一套解析和去重逻辑。

不管哪条管道进来，重复的账单都会被 external_id 挡住，不会重复记账。
"""

from __future__ import annotations

import email
import email.header
import imaplib
import os
from datetime import datetime
from email.message import Message
from pathlib import Path

import db
import importer

DATA_DIR = Path(os.environ.get("DATA_DIR", Path(__file__).resolve().parent.parent / "data"))

INBOX_DIR = Path(os.environ.get("ASSETBOOK_INBOX", DATA_DIR / "inbox"))
DONE_DIR = INBOX_DIR / "imported"
FAILED_DIR = INBOX_DIR / "failed"

BILL_SUFFIXES = {".csv", ".txt", ".zip"}
MAX_ATTACHMENT_BYTES = 32 * 1024 * 1024


def ensure_dirs() -> None:
    for d in (INBOX_DIR, DONE_DIR, FAILED_DIR):
        d.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------- 收件目录

def scan_inbox() -> dict:
    """扫一遍收件目录。返回 {seen, imported, skipped, failed, details}"""
    ensure_dirs()
    result = {"seen": 0, "imported": 0, "skipped": 0, "failed": 0, "details": []}

    for path in sorted(INBOX_DIR.iterdir()):
        if not path.is_file() or path.name.startswith("."):
            continue
        if path.suffix.lower() not in BILL_SUFFIXES:
            continue
        result["seen"] += 1
        try:
            raw = path.read_bytes()
            got = importer.import_bytes(
                raw, path.name,
                zip_password=os.environ.get("ASSETBOOK_WECHAT_ZIP_PASSWORD", ""),
                dry_run=False, auto_commit=True,
            )
            if not got["ok"]:
                _park(path, FAILED_DIR, got.get("error", "解析失败"))
                result["failed"] += 1
                result["details"].append({"file": path.name, "ok": False, "msg": got.get("error")})
                continue
            c = got["committed"] or {}
            if c.get("imported"):
                result["imported"] += c["imported"]
            result["skipped"] += c.get("skipped", 0)
            note = f"{got['source_label']}：新增 {c.get('imported', 0)} 笔"
            if c.get("skipped"):
                note += f"，重复跳过 {c['skipped']} 笔"
            _park(path, DONE_DIR, note)
            result["details"].append({"file": path.name, "ok": True, "msg": note})
        except Exception as e:                          # noqa: BLE001 — 一个文件坏了不影响其它
            _park(path, FAILED_DIR, f"{type(e).__name__}: {e}")
            result["failed"] += 1
            result["details"].append({"file": path.name, "ok": False, "msg": str(e)})

    return result


def _park(path: Path, target_dir: Path, note: str) -> None:
    """把处理过的文件挪走，并写一个同名的 .txt 说明。"""
    ensure_dirs()
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = target_dir / f"{stamp}-{path.name}"
    try:
        path.rename(dest)
        dest.with_suffix(dest.suffix + ".log.txt").write_text(
            f"{datetime.now():%Y-%m-%d %H:%M:%S}  {note}\n", encoding="utf-8",
        )
    except OSError:
        pass


def inbox_status() -> dict:
    ensure_dirs()
    return {
        "dir": str(INBOX_DIR),
        "pending": sum(1 for p in INBOX_DIR.iterdir()
                       if p.is_file() and p.suffix.lower() in BILL_SUFFIXES),
        "imported": sum(1 for p in DONE_DIR.iterdir() if p.is_file()),
        "failed": sum(1 for p in FAILED_DIR.iterdir() if p.suffix.lower() in BILL_SUFFIXES),
        "recent": [
            {"file": p.name, "size": p.stat().st_size}
            for p in sorted(DONE_DIR.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True)[:5]
            if p.is_file() and not p.name.endswith(".log.txt")
        ],
    }


# ---------------------------------------------------------------- 邮箱

IMAP_ENV = {
    "host": "ASSETBOOK_IMAP_HOST",
    "port": "ASSETBOOK_IMAP_PORT",
    "user": "ASSETBOOK_IMAP_USER",
    "password": "ASSETBOOK_IMAP_PASS",
    "folder": "ASSETBOOK_IMAP_FOLDER",
}


def imap_config() -> dict | None:
    host = os.environ.get(IMAP_ENV["host"])
    user = os.environ.get(IMAP_ENV["user"])
    password = os.environ.get(IMAP_ENV["password"])
    if not (host and user and password):
        return None
    return {
        "host": host,
        "port": int(os.environ.get(IMAP_ENV["port"]) or 993),
        "user": user,
        "password": password,
        "folder": os.environ.get(IMAP_ENV["folder"]) or "INBOX",
        "ssl": (os.environ.get("ASSETBOOK_IMAP_SSL", "1") != "0"),
        "sender_filter": os.environ.get("ASSETBOOK_IMAP_FROM", ""),
    }


def _decode(value: str | None) -> str:
    if not value:
        return ""
    out = []
    for part, enc in email.header.decode_header(value):
        if isinstance(part, bytes):
            out.append(part.decode(enc or "utf-8", errors="replace"))
        else:
            out.append(part)
    return "".join(out)


def _attachments(msg: Message) -> list[tuple[str, bytes]]:
    files: list[tuple[str, bytes]] = []
    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue
        name = _decode(part.get_filename())
        if not name:
            continue
        if Path(name).suffix.lower() not in BILL_SUFFIXES:
            continue
        payload = part.get_payload(decode=True) or b""
        if 0 < len(payload) <= MAX_ATTACHMENT_BYTES:
            files.append((name, payload))
    return files


def fetch_mail(limit: int = 20) -> dict:
    """收一次邮箱：把未读邮件里的账单附件导进来。"""
    cfg = imap_config()
    if not cfg:
        return {"ok": False, "error": "没配置邮箱（ASSETBOOK_IMAP_HOST / USER / PASS）"}

    zip_pwd = os.environ.get("ASSETBOOK_WECHAT_ZIP_PASSWORD", "")
    stats = {"ok": True, "mails": 0, "files": 0, "imported": 0, "skipped": 0,
             "failed": 0, "details": []}

    try:
        if cfg["ssl"]:
            box = imaplib.IMAP4_SSL(cfg["host"], cfg["port"])
        else:
            box = imaplib.IMAP4(cfg["host"], cfg["port"])
    except Exception as e:                              # noqa: BLE001
        return {"ok": False, "error": f"连不上邮箱：{e}"}

    try:
        box.login(cfg["user"], cfg["password"])
        box.select(cfg["folder"], readonly=False)
        criteria = ["UNSEEN"]
        if cfg["sender_filter"]:
            criteria += ["FROM", cfg["sender_filter"]]
        _typ, data = box.search(None, *criteria)
        ids = (data[0] or b"").split()[-limit:]
        stats["mails"] = len(ids)

        for uid in ids:
            _t, raw = box.fetch(uid, "(RFC822)")
            if not raw or not isinstance(raw[0], tuple):
                continue
            msg = email.message_from_bytes(raw[0][1])
            subject = _decode(msg.get("Subject"))
            for name, payload in _attachments(msg):
                stats["files"] += 1
                got = importer.import_bytes(payload, name,
                                            zip_password=zip_pwd, dry_run=False, auto_commit=True)
                if not got["ok"]:
                    stats["failed"] += 1
                    stats["details"].append({"mail": subject, "file": name, "ok": False,
                                             "msg": got.get("error")})
                    continue
                c = got["committed"] or {}
                stats["imported"] += c.get("imported", 0)
                stats["skipped"] += c.get("skipped", 0)
                stats["details"].append({
                    "mail": subject, "file": name, "ok": True,
                    "msg": f"{got['source_label']}：新增 {c.get('imported', 0)} 笔",
                })
            box.store(uid, "+FLAGS", "\\Seen")
    except Exception as e:                              # noqa: BLE001
        stats["ok"] = False
        stats["error"] = f"{type(e).__name__}: {e}"
    finally:
        try:
            box.logout()
        except Exception:                               # noqa: BLE001
            pass

    return stats
