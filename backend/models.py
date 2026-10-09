"""请求/响应数据模型（Pydantic）。"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

TxType = Literal["in", "out", "transfer"]
AccountKind = Literal["cash", "bank", "payment", "broker", "credit", "loan", "other"]
HoldingKind = Literal["fund", "stock", "bond", "gold", "cash", "other"]


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
    account_id: Optional[int] = None
    units: float = 0
    cost_price: float = 0
    price: float = 0
    price_date: Optional[str] = None
    note: Optional[str] = None


class PriceIn(BaseModel):
    price: float = Field(ge=0)
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
