"""Operational restaurant orders. Kept separate from restaurant setup data."""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, JSON, LargeBinary, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base
from .models import new_uuid, now_utc


class RestaurantTableAccount(Base):
    __tablename__ = "restaurant_table_accounts"
    __table_args__ = (
        CheckConstraint("status IN ('open', 'paid', 'closed')", name="ck_restaurant_table_accounts_status"),
        Index("ix_restaurant_table_accounts_open", "client_id", "table_number", unique=True, postgresql_where="is_open = true"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    client_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), index=True)
    table_number: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), default="open", server_default="open")
    is_open: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cashier_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    orders: Mapped[list["RestaurantOrder"]] = relationship(back_populates="table_account")


class RestaurantOrder(Base):
    __tablename__ = "restaurant_orders"
    __table_args__ = (
        UniqueConstraint("client_id", "order_number", name="uq_restaurant_orders_client_number"),
        UniqueConstraint("client_id", "idempotency_key", name="uq_restaurant_orders_client_idempotency"),
        CheckConstraint("source IN ('whatsapp', 'waiter')", name="ck_restaurant_orders_source"),
        CheckConstraint("modality IN ('dine_in', 'pickup', 'delivery')", name="ck_restaurant_orders_modality"),
        CheckConstraint("order_status IN ('pending_payment', 'confirmed', 'preparing', 'ready', 'on_the_way', 'delivered', 'served', 'closed', 'cancelled')", name="ck_restaurant_orders_status"),
        CheckConstraint("payment_status IN ('pending', 'confirmed', 'not_required')", name="ck_restaurant_orders_payment_status"),
        Index("ix_restaurant_orders_client_status", "client_id", "order_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    client_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), index=True)
    order_number: Mapped[str] = mapped_column(String(24))
    source: Mapped[str] = mapped_column(String(20))
    modality: Mapped[str] = mapped_column(String(20))
    table_account_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("restaurant_table_accounts.id", ondelete="SET NULL"), nullable=True, index=True)
    table_number: Mapped[str | None] = mapped_column(String(40), nullable=True)
    customer_name: Mapped[str | None] = mapped_column(String(180), nullable=True)
    customer_phone: Mapped[str | None] = mapped_column(String(80), nullable=True)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    address_reference: Mapped[str | None] = mapped_column(Text, nullable=True)
    delivery_zone_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("restaurant_delivery_zones.id", ondelete="SET NULL"), nullable=True)
    delivery_zone_name: Mapped[str | None] = mapped_column(String(180), nullable=True)
    subtotal: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0, server_default="0")
    delivery_fee: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0, server_default="0")
    total: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0, server_default="0")
    order_status: Mapped[str] = mapped_column(String(30), default="confirmed", server_default="confirmed")
    payment_status: Mapped[str] = mapped_column(String(20), default="pending", server_default="pending")
    payment_method: Mapped[str | None] = mapped_column(String(30), nullable=True)
    receipt_submitted: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    payment_reference: Mapped[str | None] = mapped_column(String(180), nullable=True)
    reported_payment_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    payment_reported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    payment_review_status: Mapped[str] = mapped_column(String(20), default="pending", server_default="pending")
    payment_rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    payment_rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    payment_receipt_data: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    payment_receipt_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    payment_receipt_mime: Mapped[str | None] = mapped_column(String(120), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True, index=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(180), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)
    payment_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    payment_confirmed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    @property
    def payment_receipt_available(self) -> bool:
        return bool(self.payment_receipt_data)

    items: Mapped[list["RestaurantOrderItem"]] = relationship(back_populates="order", cascade="all, delete-orphan", order_by="RestaurantOrderItem.position")
    table_account: Mapped[RestaurantTableAccount | None] = relationship(back_populates="orders")


class RestaurantOrderItem(Base):
    __tablename__ = "restaurant_order_items"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("restaurant_orders.id", ondelete="CASCADE"), index=True)
    client_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("restaurant_menu_products.id", ondelete="RESTRICT"))
    product_name: Mapped[str] = mapped_column(String(180))
    base_unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0, server_default="0")
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0, server_default="0")
    quantity: Mapped[int] = mapped_column()
    line_subtotal: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0, server_default="0")
    variants: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    extras: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    observations: Mapped[str | None] = mapped_column(Text, nullable=True)
    position: Mapped[int] = mapped_column(default=0, server_default="0")
    is_cancelled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    order: Mapped[RestaurantOrder] = relationship(back_populates="items")


class PaymentNotification(Base):
    """A parsed payment notification, ready for a future mailbox adapter."""

    __tablename__ = "payment_notifications"
    __table_args__ = (
        UniqueConstraint(
            "client_id",
            "external_operation_id",
            name="uq_payment_notifications_client_operation",
        ),
        CheckConstraint(
            "status IN ('unmatched', 'ambiguous', 'matched', 'review_required')",
            name="ck_payment_notifications_status",
        ),
        Index("ix_payment_notifications_client_status", "client_id", "status"),
        UniqueConstraint("client_id", "source_message_id", name="uq_payment_notifications_client_message"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    client_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), index=True)
    payment_method_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("restaurant_payment_methods.id"), nullable=True, index=True)
    notification_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    external_operation_id: Mapped[str | None] = mapped_column(String(180), nullable=True)
    source_message_id: Mapped[str | None] = mapped_column(String(500), nullable=True)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    sender: Mapped[str | None] = mapped_column(String(320), nullable=True)
    subject: Mapped[str | None] = mapped_column(String(500), nullable=True)
    notification_metadata: Mapped[dict] = mapped_column("metadata", JSON, default=dict, server_default="{}")
    status: Mapped[str] = mapped_column(String(20), default="unmatched", server_default="unmatched")
    matched_order_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("restaurant_orders.id", ondelete="SET NULL"), nullable=True, index=True
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RestaurantDeliveryNotification(Base):
    __tablename__ = "restaurant_delivery_notifications"
    __table_args__ = (UniqueConstraint("order_id", name="uq_restaurant_delivery_notifications_order"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    client_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), index=True)
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("restaurant_orders.id", ondelete="CASCADE"), index=True)
    recipient: Mapped[str] = mapped_column(String(40))
    provider: Mapped[str | None] = mapped_column(String(30), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending", server_default="pending")
    external_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)
