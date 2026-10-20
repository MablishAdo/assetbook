"""自动同步调度器 —— 用 asyncio 后台任务定时干活，不引入 APScheduler。

五件事：
  1. 定时刷新持仓现价（场外基金净值一天只更新一次，所以间隔不用太密）
  2. 每天定时把当天的投资市值落到 portfolio_history 表，攒出长期曲线
  3. 顺带定时刷新汇率
  4. 定时扫 data/inbox/ 收件目录，把丢进去的账单自动入库
  5. 定时去 IMAP 邮箱收账单附件（配了才跑）

配置优先级：数据库里的设置（界面可改） > 环境变量 > 代码默认值。
所有状态都放在内存里，重启即重置；这本来就是「尽力而为」的后台任务。
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from datetime import datetime, timedelta

import db

# 每轮巡检的间隔（秒）。不是为了精度，够用就行。
TICK_SECONDS = 30
# 只保留最近这么多条日志
LOG_LIMIT = 40

DEFAULTS = {
    "auto_sync": "1",
    "quote_interval_min": "30",
    "snapshot_hour": "23",
    "rates_interval_hour": "12",
    "inbox_interval_min": "10",
    "mail_interval_min": "60",
}


def _env_defaults() -> dict[str, str]:
    return {
        "auto_sync": os.environ.get("ASSETBOOK_AUTO_SYNC", DEFAULTS["auto_sync"]),
        "quote_interval_min": os.environ.get("ASSETBOOK_QUOTE_INTERVAL_MIN", DEFAULTS["quote_interval_min"]),
        "snapshot_hour": os.environ.get("ASSETBOOK_SNAPSHOT_HOUR", DEFAULTS["snapshot_hour"]),
        "rates_interval_hour": os.environ.get("ASSETBOOK_RATES_INTERVAL_HOUR", DEFAULTS["rates_interval_hour"]),
        "inbox_interval_min": os.environ.get("ASSETBOOK_INBOX_INTERVAL_MIN", DEFAULTS["inbox_interval_min"]),
        "mail_interval_min": os.environ.get("ASSETBOOK_MAIL_INTERVAL_MIN", DEFAULTS["mail_interval_min"]),
    }


def setting(key: str) -> str:
    """读设置：数据库里有就用数据库的，否则用环境变量/默认值。"""
    return db.get_meta(f"auto_{key}") or _env_defaults().get(key) or DEFAULTS[key]


def set_setting(key: str, value: str) -> None:
    db.set_meta(f"auto_{key}", str(value))


def _as_int(key: str, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(setting(key))))
    except (TypeError, ValueError):
        return int(DEFAULTS[key])


class Scheduler:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self.running = False
        self.last_quote_at: datetime | None = None
        self.last_quote_result: dict | None = None
        self.next_quote_at: datetime | None = None
        self.last_snapshot_date: str | None = None
        self.last_rates_at: datetime | None = None
        self.last_inbox_at: datetime | None = None
        self.last_mail_at: datetime | None = None
        self.log: list[dict] = []

    # ---------------- 日志 ----------------

    def _note(self, kind: str, message: str, level: str = "info") -> None:
        self.log.insert(0, {
            "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "kind": kind,
            "level": level,
            "message": message,
        })
        del self.log[LOG_LIMIT:]

    # ---------------- 生命周期 ----------------

    def start(self) -> None:
        if self._task and not self._task.done():
            return
        self.running = True
        self._task = asyncio.create_task(self._loop())
        self._note("system", "后台自动同步已启动")

    async def stop(self) -> None:
        self.running = False
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    # ---------------- 主循环 ----------------

    async def _loop(self) -> None:
        # 启动后先等一会儿，别和首屏请求抢资源
        await asyncio.sleep(8)
        self._maybe_snapshot()          # 启动时补一次当天快照
        while True:
            try:
                if setting("auto_sync") == "1":
                    if self._quote_due():
                        await self._do_quotes()
                    if self._rates_due():
                        await self._do_rates()
                    if self._due(self.last_inbox_at, "inbox_interval_min"):
                        await self._do_inbox()
                    if self._due(self.last_mail_at, "mail_interval_min"):
                        await self._do_mail()
                    self._maybe_snapshot()
            except asyncio.CancelledError:
                raise
            except Exception as e:                      # noqa: BLE001 — 后台任务不能死
                self._note("system", f"巡检异常：{type(e).__name__}: {e}", "error")
            await asyncio.sleep(TICK_SECONDS)

    def _quote_due(self) -> bool:
        if self.next_quote_at is None:
            return True
        return datetime.now() >= self.next_quote_at

    def _rates_due(self) -> bool:
        hours = _as_int("rates_interval_hour", 1, 168)
        if self.last_rates_at is None:
            return True
        return datetime.now() - self.last_rates_at >= timedelta(hours=hours)

    def _due(self, last: datetime | None, key: str) -> bool:
        if last is None:
            return True
        return datetime.now() - last >= timedelta(minutes=_as_int(key, 1, 1440))

    # ---------------- 账单调入 ----------------

    async def _do_inbox(self) -> None:
        import intake

        try:
            r = await asyncio.to_thread(intake.scan_inbox)
        except Exception as e:                          # noqa: BLE001
            self._note("inbox", f"扫收件目录失败：{e}", "warn")
            self.last_inbox_at = datetime.now()
            return
        self.last_inbox_at = datetime.now()
        if r["seen"] == 0:
            return                                      # 没新文件就不刷日志
        if r["imported"]:
            msg = f"收件目录导入 {r['imported']} 笔"
            if r["skipped"]:
                msg += f"（重复跳过 {r['skipped']}）"
            self._note("inbox", msg)
        if r["failed"]:
            self._note("inbox", f"{r['failed']} 个文件没认出来，已移到 inbox/failed", "warn")

    async def _do_mail(self) -> None:
        import intake

        if not intake.imap_config():
            self.last_mail_at = datetime.now()
            return
        try:
            r = await asyncio.to_thread(intake.fetch_mail)
        except Exception as e:                          # noqa: BLE001
            self._note("mail", f"收邮件失败：{e}", "warn")
            self.last_mail_at = datetime.now()
            return
        self.last_mail_at = datetime.now()
        if not r.get("ok"):
            self._note("mail", f"收邮件失败：{r.get('error')}", "warn")
        elif r["files"]:
            self._note("mail", f"邮件账单：读了 {r['mails']} 封、{r['files']} 个附件，"
                               f"新增 {r['imported']} 笔")

    # ---------------- 具体任务 ----------------

    async def _do_quotes(self) -> None:
        import services

        interval = _as_int("quote_interval_min", 5, 1440)
        result = await asyncio.to_thread(services.refresh_quotes)
        self.last_quote_at = datetime.now()
        self.last_quote_result = result
        self.next_quote_at = self.last_quote_at + timedelta(minutes=interval)

        if result["total"] == 0:
            self._note("quotes", "没有填了代码的持仓，跳过")
        elif result["failed"]:
            names = "、".join(f"{f['code']}({f['error']})" for f in result["failed"][:3])
            self._note("quotes", f"更新 {result['ok']}/{result['total']} 条，失败：{names}", "warn")
        else:
            self._note("quotes", f"更新 {result['ok']} 条持仓价格")

    async def _do_rates(self) -> None:
        import services

        try:
            info = await asyncio.to_thread(services.refresh_rates)
        except Exception as e:                          # noqa: BLE001
            self._note("rates", f"汇率更新失败：{e}", "warn")
            self.last_rates_at = datetime.now()
            return
        self.last_rates_at = datetime.now()
        self._note("rates", f"汇率已更新（{len(info.get('rates') or {})} 个币种）")

    def _maybe_snapshot(self) -> None:
        today = datetime.now().date().isoformat()
        if self.last_snapshot_date == today:
            return
        hour = _as_int("snapshot_hour", 0, 23)
        if datetime.now().hour < hour and self.last_snapshot_date is not None:
            return
        try:
            db.snapshot_portfolio()
            self.last_snapshot_date = today
            self._note("portfolio", f"已记录 {today} 的投资市值快照")
        except Exception as e:                          # noqa: BLE001
            self._note("portfolio", f"投资快照失败：{e}", "warn")

    # ---------------- 手动触发 ----------------

    async def run_now(self, what: str = "quotes") -> dict:
        if what == "quotes":
            await self._do_quotes()
            return self.last_quote_result or {}
        if what == "rates":
            await self._do_rates()
            return {"ok": True}
        if what == "portfolio":
            db.snapshot_portfolio()
            self.last_snapshot_date = datetime.now().date().isoformat()
            self._note("portfolio", "手动记录了投资市值快照")
            return {"ok": True}
        if what == "inbox":
            import intake

            r = await asyncio.to_thread(intake.scan_inbox)
            self.last_inbox_at = datetime.now()
            if r["seen"]:
                self._note("inbox", f"手动扫描：处理 {r['seen']} 个文件，新增 {r['imported']} 笔")
            return r
        if what == "mail":
            r = await asyncio.to_thread(__import__("intake").fetch_mail)
            self.last_mail_at = datetime.now()
            return r
        raise ValueError(f"不认识的任务：{what}")

    # ---------------- 状态 ----------------

    def status(self) -> dict:
        try:
            import intake

            inbox = intake.inbox_status()
            mail_ready = intake.imap_config() is not None
        except Exception:                               # noqa: BLE001
            inbox, mail_ready = {}, False
        return {
            "enabled": setting("auto_sync") == "1",
            "running": self.running,
            "quote_interval_min": _as_int("quote_interval_min", 5, 1440),
            "snapshot_hour": _as_int("snapshot_hour", 0, 23),
            "rates_interval_hour": _as_int("rates_interval_hour", 1, 168),
            "inbox_interval_min": _as_int("inbox_interval_min", 1, 1440),
            "mail_interval_min": _as_int("mail_interval_min", 5, 1440),
            "last_quote_at": self.last_quote_at.strftime("%Y-%m-%d %H:%M:%S") if self.last_quote_at else None,
            "next_quote_at": self.next_quote_at.strftime("%Y-%m-%d %H:%M:%S") if self.next_quote_at else None,
            "last_quote_result": self.last_quote_result,
            "last_snapshot_date": self.last_snapshot_date,
            "last_rates_at": self.last_rates_at.strftime("%Y-%m-%d %H:%M:%S") if self.last_rates_at else None,
            "last_inbox_at": self.last_inbox_at.strftime("%Y-%m-%d %H:%M:%S") if self.last_inbox_at else None,
            "last_mail_at": self.last_mail_at.strftime("%Y-%m-%d %H:%M:%S") if self.last_mail_at else None,
            "inbox": inbox,
            "mail_configured": mail_ready,
            "log": self.log[:20],
        }


scheduler = Scheduler()
