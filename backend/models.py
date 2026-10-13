"""请求/响应数据模型（Pydantic）。"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

TxType = Literal["in", "out", "transfer"]
AccountKind = Literal["cash", "bank", "payment", "broker", "credit", "loan", "other"]
HoldingKind = Literal["fund", "stock", "bond", "gold", "cash", "other"]
Market = Literal["auto", "cn", "fund", "hk", "crypto"]


class LoginIn(BaseModel):
    password: str = Field(min_length=1)


class SetupIn(BaseModel):
    password: str = Field(min_length=6, description="至少 6 位")


class AccountIn(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    kind: AccountKind = "bank"
    currency: str = "CNY"
    base_balance: float = 0
    base_date: Optional[str] = None
    sort: int = 0


class AccountPatch(BaseModel):
    name: Optional[str] = None
    kind: Optional[AccountKind] = None
    currency: Optional[str] = None
    base_balance: Optional[float] = None
    base_date: Optional[str] = None
    sort: Optional[int] = None
    archived: Optional[int] = None


class CategoryIn(BaseModel):
    name: str = Field(min_length=1, max_length=30)
    kind: Literal["expense", "income"] = "expense"
    color: Optional[str] = None
    sort: int = 0


class RuleIn(BaseModel):
    keyword: str = Field(min_length=1, max_length=60)
    category_id: int
    priority: int = 100
    enabled: int = 1


class TxIn(BaseModel):
    date: str
    type: TxType
    amount: float = Field(gt=0)
    account_id: int
    to_account_id: Optional[int] = None
    category_id: Optional[int] = None
    note: Optional[str] = None
    source: str = "manual"
    # 未显式给类别时，是否用规则引擎自动分类
    autocategorize: bool = True


class HoldingIn(BaseModel):
    code: Optional[str] = None
    name: str = Field(min_length=1, max_length=60)
    kind: HoldingKind = "fund"
    market: Market = "auto"
    account_id: Optional[int] = None
    units: float = 0
    amount: float = Field(default=0, description="场外基金按金额录入时填持有金额")
    cost_price: float = 0
    price: float = 0
    price_date: Optional[str] = None
    note: Optional[str] = None
    # 新建时是否顺便拉一次现价
    fetch_price: bool = True


class HoldingPatch(BaseModel):
    code: Optional[str] = None
    name: Optional[str] = None
    kind: Optional[HoldingKind] = None
    market: Optional[Market] = None
    account_id: Optional[int] = None
    units: Optional[float] = None
    amount: Optional[float] = None
    cost_price: Optional[float] = None
    price: Optional[float] = None
    price_date: Optional[str] = None
    note: Optional[str] = None


class AutoSyncIn(BaseModel):
    enabled: Optional[bool] = None
    quote_interval_min: Optional[int] = Field(default=None, ge=5, le=1440)
    snapshot_hour: Optional[int] = Field(default=None, ge=0, le=23)
    rates_interval_hour: Optional[int] = Field(default=None, ge=1, le=168)
    inbox_interval_min: Optional[int] = Field(default=None, ge=1, le=1440)
    mail_interval_min: Optional[int] = Field(default=None, ge=5, le=1440)


class PayeeRuleIn(BaseModel):
    pattern: str = Field(min_length=1, max_length=60, description="支付方式里包含这个词就命中")
    account_id: int
    priority: int = 100
    source: Optional[Literal["alipay", "wechat", "bank", "generic"]] = None


class AiSettingsIn(BaseModel):
    """AI 接口配置。key 留空表示「不改动已保存的」。"""
    base: Optional[str] = Field(default=None, max_length=300,
                                description="OpenAI 兼容接口地址，填到 /v1 这一层")
    key: Optional[str] = Field(default=None, max_length=300)
    model: Optional[str] = Field(default=None, max_length=80)
    clear_key: bool = False


class JevSettingsIn(BaseModel):
    """Jev（TypeSafe System One 决策模型）配置。key 留空表示「不改动已保存的」。"""
    model: Optional[str] = Field(default=None, max_length=80,
                                 description="默认 jev-latest")
    key: Optional[str] = Field(default=None, max_length=300)
    clear_key: bool = False


class AnalyzeIn(BaseModel):
    """生成 AI 财务分析。"""
    month: Optional[str] = Field(default=None, pattern=r"^\d{4}-\d{2}$")
    force: bool = False


class ImportRow(BaseModel):
    """用户在预览界面上确认过的行。"""
    date: str
    type: TxType
    amount: float
    account_id: int
    category_id: Optional[int] = None
    note: Optional[str] = None
    external_id: Optional[str] = None
    source: Optional[str] = None
    selected: bool = True


class ImportCommitIn(BaseModel):
    rows: list[ImportRow]
    label: str = "import"


class PriceIn(BaseModel):
    # 传 0（或不传）表示「去行情接口自动拉一次」
    price: float = Field(default=0, ge=0)
    price_date: Optional[str] = None


class BudgetIn(BaseModel):
    month: str = Field(description="YYYY-MM，或 * 表示每月通用")
    category_id: int
    amount: float = Field(ge=0)


class SnapshotIn(BaseModel):
    balance: float
    date: Optional[str] = None


class ClassifyIn(BaseModel):
    note: str
    type: TxType = "out"
    amount: float = 0
    use_ai: bool = False
