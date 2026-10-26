from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Resource = Literal["customers", "orders", "stock", "invoices"]
OrderStatus = Literal["confirmed", "fulfilled", "cancelled"]
InvoiceStatus = Literal["draft", "issued", "paid"]


class Query(BaseModel):
    model_config = ConfigDict(extra="forbid")
    q: str = Field(default="", max_length=150)
    customer_id: str | None = None
    status: str | None = None
    date_from: date | None = None
    date_to: date | None = None
    due_before: date | None = None
    overdue: bool = False
    low_stock: bool = False
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0, le=100000)

    @model_validator(mode="after")
    def check_dates(self):
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("date_from must be on or before date_to")
        return self


class ChatInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(min_length=1, max_length=4000)


class PrepareInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order_id: str = Field(min_length=1, max_length=60)
