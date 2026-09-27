"""Structured restaurant setup data owned by a Client.

This module intentionally contains setup/configuration only. It does not model
orders, payment transactions, kitchen notifications, or delivery execution.
"""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, JSON, LargeBinary, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base
from .models import new_uuid, now_utc


class RestaurantProfile(Base):
    __tablename__ = "restaurant_profiles"
    __table_args__ = (UniqueConstraint("client_id", name="uq_restaurant_profiles_client_id"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    client_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), index=True)
    address: Mapped[str] = mapped_column(Text, default="", server_default="")
    phone: Mapped[str] = mapped_column(String(80), default="", server_default="")
    currency: Mapped[str] = mapped_column(String(3), default="PEN", server_default="PEN")
    opening_hours: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)


class RestaurantWelcomeFlyer(Base):
    __tablename__ = "restaurant_welcome_flyers"
    __table_args__ = (UniqueConstraint("client_id", name="uq_restaurant_welcome_flyers_client_id"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    client_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), index=True)
    image_data: Mapped[bytes] = mapped_column(LargeBinary)
    mime_type: Mapped[str] = mapped_column(String(40))
    filename: Mapped[str] = mapped_column(String(255), default="welcome-flyer")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    message: Mapped[str] = mapped_column(Text, default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)


class MenuCategory(Base):
    __tablename__ = "restaurant_menu_categories"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    client_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(180))
    position: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)

    products: Mapped[list["MenuProduct"]] = relationship(
        back_populates="category", cascade="all, delete-orphan", order_by="MenuProduct.position"
    )


class MenuProduct(Base):
    __tablename__ = "restaurant_menu_products"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    client_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), index=True)
    category_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("restaurant_menu_categories.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(180))
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0, server_default="0")
    image_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_available: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    position: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)

    category: Mapped[MenuCategory] = relationship(back_populates="products")
    variants: Mapped[list["MenuProductVariant"]] = relationship(
        back_populates="product", cascade="all, delete-orphan", order_by="MenuProductVariant.position"
    )
    extras: Mapped[list["MenuProductExtra"]] = relationship(
        back_populates="product", cascade="all, delete-orphan", order_by="MenuProductExtra.position"
    )


class MenuProductVariant(Base):
    __tablename__ = "restaurant_menu_product_variants"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    client_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("restaurant_menu_products.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(180))
    price_delta: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0, server_default="0")
    is_available: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    position: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    product: Mapped[MenuProduct] = relationship(back_populates="variants")

    @property
    def price(self) -> Decimal:
        return self.price_delta


class MenuProductExtra(Base):
    __tablename__ = "restaurant_menu_product_extras"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    client_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("restaurant_menu_products.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(180))
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0, server_default="0")
    is_available: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    position: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    product: Mapped[MenuProduct] = relationship(back_populates="extras")


class RestaurantModality(Base):
    __tablename__ = "restaurant_modalities"
    __table_args__ = (UniqueConstraint("client_id", name="uq_restaurant_modalities_client_id"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    client_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), index=True)
    dine_in_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    pickup_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    delivery_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    delivery_whatsapp: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)


class DeliveryZone(Base):
    __tablename__ = "restaurant_delivery_zones"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    client_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(180))
    fee: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0, server_default="0")
    minimum_order: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0, server_default="0")
    estimated_minutes: Mapped[int] = mapped_column(Integer, default=45, server_default="45")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)


class RestaurantPaymentMethod(Base):
    __tablename__ = "restaurant_payment_methods"
    __table_args__ = (
        UniqueConstraint("client_id", "method", name="uq_restaurant_payment_methods_client_method"),
        CheckConstraint(
            "method IN ('cash', 'yape', 'plin', 'transfer', 'card', 'other')",
            name="ck_restaurant_payment_methods_method",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    client_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), index=True)
    method: Mapped[str] = mapped_column(String(30))
    display_name: Mapped[str] = mapped_column(String(180), default="", server_default="")
    instructions: Mapped[str] = mapped_column(Text, default="", server_default="")
    account_name: Mapped[str] = mapped_column(String(180), default="", server_default="")
    account_number: Mapped[str] = mapped_column(String(180), default="", server_default="")
    notification_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    qr_image_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    receipt_required: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)


class RestaurantPaymentMailbox(Base):
    __tablename__ = "restaurant_payment_mailboxes"
    __table_args__ = (UniqueConstraint("client_id", name="uq_restaurant_payment_mailboxes_client_id"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    client_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), index=True)
    email: Mapped[str] = mapped_column(String(320))
    imap_host: Mapped[str] = mapped_column(String(255), default="imap.gmail.com", server_default="imap.gmail.com")
    imap_port: Mapped[int] = mapped_column(Integer, default=993, server_default="993")
    imap_ssl: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    encrypted_app_password: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    connection_status: Mapped[str] = mapped_column(String(20), default="not_tested", server_default="not_tested")
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)


class RestaurantStaff(Base):
    __tablename__ = "restaurant_staff"
    __table_args__ = (
        UniqueConstraint("client_id", "user_id", name="uq_restaurant_staff_client_user"),
        CheckConstraint(
            "role IN ('admin', 'cashier', 'waiter', 'kitchen', 'delivery')",
            name="ck_restaurant_staff_role",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    client_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(30))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)

    user: Mapped["User"] = relationship("User")
