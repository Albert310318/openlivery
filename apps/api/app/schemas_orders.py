import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field


OrderModality = Literal["dine_in", "pickup", "delivery"]
OrderSource = Literal["whatsapp", "waiter"]
OrderStatus = Literal["pending_payment", "confirmed", "preparing", "ready", "on_the_way", "delivered", "served", "closed", "cancelled"]


class OrderItemCreate(BaseModel):
    product_id: uuid.UUID
    quantity: int = Field(ge=1, le=100)
    variant_ids: list[uuid.UUID] = Field(default_factory=list)
    extra_ids: list[uuid.UUID] = Field(default_factory=list)
    observations: str | None = Field(default=None, max_length=2000)


class WaiterOrderCreate(BaseModel):
    table_number: str = Field(min_length=1, max_length=40)
    items: list[OrderItemCreate] = Field(min_length=1, max_length=100)
    customer_name: str | None = Field(default=None, max_length=180)
    notes: str | None = Field(default=None, max_length=3000)
    idempotency_key: str | None = Field(default=None, max_length=180)


class OrderPaymentUpdate(BaseModel):
    payment_method: str | None = Field(default=None, min_length=1, max_length=30)
    payment_reference: str | None = Field(default=None, max_length=180)
    receipt_submitted: bool = False


class PaymentRejectionInput(BaseModel):
    reason: str | None = Field(default=None, max_length=2000)


class PaymentNotificationCreate(BaseModel):
    payment_method_id: uuid.UUID | None = None
    notification_email: EmailStr | None = None
    external_operation_id: str | None = Field(default=None, max_length=180)
    source_message_id: str | None = Field(default=None, max_length=500)
    amount: Decimal | None = Field(default=None, gt=0)
    occurred_at: datetime
    received_at: datetime | None = None
    sender: str | None = Field(default=None, max_length=320)
    subject: str | None = Field(default=None, max_length=500)
    metadata: dict = Field(default_factory=dict)


class PaymentNotificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    client_id: uuid.UUID
    payment_method_id: uuid.UUID | None
    notification_email: EmailStr | None
    external_operation_id: str | None
    source_message_id: str | None
    amount: Decimal | None
    occurred_at: datetime
    received_at: datetime
    sender: str | None
    subject: str | None
    metadata: dict = Field(validation_alias="notification_metadata", serialization_alias="metadata")
    status: str
    matched_order_id: uuid.UUID | None
    processed_at: datetime | None


class OrderStatusUpdate(BaseModel):
    status: OrderStatus


class OrderItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    product_id: uuid.UUID
    product_name: str
    base_unit_price: Decimal
    unit_price: Decimal
    quantity: int
    line_subtotal: Decimal
    variants: list
    extras: list
    observations: str | None
    is_cancelled: bool
    cancelled_at: datetime | None


class WaiterOrderItemUpdate(BaseModel):
    quantity: int = Field(ge=1, le=100)
    observations: str | None = Field(default=None, max_length=2000)

class RestaurantOrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    client_id: uuid.UUID
    order_number: str
    source: OrderSource
    modality: OrderModality
    table_account_id: uuid.UUID | None
    table_number: str | None
    customer_name: str | None
    customer_phone: str | None
    address: str | None
    address_reference: str | None
    delivery_zone_name: str | None
    subtotal: Decimal
    delivery_fee: Decimal
    total: Decimal
    order_status: str
    payment_status: str
    payment_verification_status: str = "pending"
    payment_method: str | None
    receipt_submitted: bool
    payment_reference: str | None
    reported_payment_amount: Decimal | None
    payment_reported_at: datetime | None
    payment_review_status: str
    payment_rejection_reason: str | None
    payment_rejected_at: datetime | None
    payment_receipt_available: bool
    payment_receipt_filename: str | None
    payment_receipt_mime: str | None
    notes: str | None
    created_by_user_id: uuid.UUID | None
    conversation_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
    payment_confirmed_at: datetime | None
    payment_confirmed_by_user_id: uuid.UUID | None
    ready_at: datetime | None
    delivered_at: datetime | None
    items: list[OrderItemOut]


class DeliveryOrderItemOut(BaseModel):
    id: uuid.UUID
    product_name: str
    quantity: int
    observations: str | None
    variants: list
    extras: list


class DeliveryOrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    client_id: uuid.UUID
    order_number: str
    modality: OrderModality
    customer_name: str | None
    customer_phone: str | None
    address: str | None
    address_reference: str | None
    order_status: str
    items: list[DeliveryOrderItemOut]
    created_at: datetime
    ready_at: datetime | None
    delivered_at: datetime | None


class DeliveryNotificationOut(BaseModel):
    id: uuid.UUID
    client_id: uuid.UUID
    order_id: uuid.UUID
    order_number: str
    recipient: str
    provider: str | None
    status: str
    external_message_id: str | None
    error_message: str | None
    attempts: int
    sent_at: datetime | None
    updated_at: datetime

class TableAccountOut(BaseModel):
    id: uuid.UUID
    client_id: uuid.UUID
    table_number: str
    status: str
    is_open: bool
    opened_at: datetime
    paid_at: datetime | None
    closed_at: datetime | None
    subtotal: Decimal = Decimal("0")
    total: Decimal = Decimal("0")
    orders: list[RestaurantOrderOut] = []
