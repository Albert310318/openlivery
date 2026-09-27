import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel


class SalesPaymentTotals(BaseModel):
    cash: Decimal = Decimal("0")
    yape: Decimal = Decimal("0")
    plin: Decimal = Decimal("0")
    transfer: Decimal = Decimal("0")
    card: Decimal = Decimal("0")
    other: Decimal = Decimal("0")


class SalesMovement(BaseModel):
    order_id: uuid.UUID
    order_number: str
    paid_at: datetime
    origin: str
    table_or_customer: str
    amount: Decimal
    payment_method: str
    status: str
    items: list[dict]
    confirmed_by: str | None


class ProductSales(BaseModel):
    product_name: str
    quantity: int
    amount: Decimal


class SalesReportOut(BaseModel):
    client_id: uuid.UUID
    from_datetime: datetime
    to_datetime: datetime
    generated_at: datetime
    total_sales: Decimal
    paid_orders: int
    pending_orders: int
    cancelled_orders: int
    payment_totals: SalesPaymentTotals
    origin_totals: dict[str, Decimal]
    movements: list[SalesMovement]
    products: list[ProductSales]
