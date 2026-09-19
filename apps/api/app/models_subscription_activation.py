"""Durable transport evidence only; no trial activation or delivery hooks.

PostgreSQL cross-row and history guards are installed by migration 0024.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column, validates

from .database import Base
from .models import new_uuid, now_utc


def _utc(value):
    if value is not None:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Activation timestamps must be timezone-aware")
        return value.astimezone(timezone.utc)
    return value


class SubscriptionTestIdentity(Base):
    __tablename__ = "subscription_test_identities"
    __table_args__ = (
        UniqueConstraint("client_id", "identity_type", "identity_value", name="uq_sti_identity"),
        CheckConstraint("identity_type IN ('whatsapp_phone', 'whatsapp_lid')", name="ck_sti_type"),
        CheckConstraint("(identity_type = 'whatsapp_phone' AND identity_value ~ '^[+][1-9][0-9]{1,14}$') OR (identity_type = 'whatsapp_lid' AND identity_value ~ '^[1-9][0-9]*@lid$')", name="ck_sti_canonical").ddl_if(dialect="postgresql"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    client_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clients.id", ondelete="RESTRICT"))
    identity_type: Mapped[str] = mapped_column(String(24))
    identity_value: Mapped[str] = mapped_column(String(255))
    label: Mapped[str] = mapped_column(String(180), default="", server_default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)


    @validates("created_at", "updated_at")
    def validate_timestamp(self, key, value):
        return _utc(value)


class SubscriptionActivationDelivery(Base):
    __tablename__ = "subscription_activation_deliveries"
    __table_args__ = (
        UniqueConstraint("message_id", name="uq_sad_message"),
        CheckConstraint("channel IN ('whatsapp_qr', 'whatsapp_cloud')", name="ck_sad_channel"),
        CheckConstraint("(channel = 'whatsapp_qr' AND whatsapp_channel_id IS NOT NULL AND whatsapp_cloud_channel_id IS NULL) OR (channel = 'whatsapp_cloud' AND whatsapp_cloud_channel_id IS NOT NULL AND whatsapp_channel_id IS NULL)", name="ck_sad_channel_fk"),
        CheckConstraint("origin = 'automatic_ai_reply'", name="ck_sad_origin"),
        CheckConstraint("environment IN ('production', 'test')", name="ck_sad_environment"),
        CheckConstraint("recipient_identity ~ '^[+][1-9][0-9]{1,14}$' OR (channel = 'whatsapp_qr' AND recipient_identity ~ '^[1-9][0-9]*@lid$')", name="ck_sad_recipient").ddl_if(dialect="postgresql"),
        CheckConstraint("test_identity_id IS NULL OR environment = 'test'", name="ck_sad_test"),
        CheckConstraint("transport_state IN ('PREPARED', 'ACCEPTED', 'FAILED', 'UNKNOWN')", name="ck_sad_transport"),
        CheckConstraint("external_message_id IS NULL OR (length(trim(external_message_id)) > 0 AND external_message_id = trim(external_message_id))", name="ck_sad_external_id"),
        CheckConstraint("external_message_id IS NULL OR external_message_id !~ '[[:space:]]'", name="ck_sad_external_whitespace").ddl_if(dialect="postgresql"),
        CheckConstraint("(transport_state = 'ACCEPTED' AND external_message_id IS NOT NULL AND confirmation_type IS NOT NULL AND accepted_at IS NOT NULL AND recorded_at IS NOT NULL) OR (transport_state <> 'ACCEPTED' AND external_message_id IS NULL AND confirmation_type IS NULL AND accepted_at IS NULL AND recorded_at IS NULL)", name="ck_sad_confirmation"),
        CheckConstraint("confirmation_type IS NULL OR (channel = 'whatsapp_qr' AND confirmation_type = 'BAILEYS_SEND_RESULT') OR (channel = 'whatsapp_cloud' AND confirmation_type = 'META_MESSAGES_ID')", name="ck_sad_confirmation_type"),
        CheckConstraint("trial_state IN ('PENDING', 'STARTED', 'IGNORED')", name="ck_sad_trial"),
        CheckConstraint("(trial_state = 'PENDING' AND trial_reason IS NULL AND processed_at IS NULL) OR (trial_state = 'STARTED' AND trial_reason IS NOT NULL AND trial_reason = 'FIRST_ACCEPTED_REPLY' AND processed_at IS NOT NULL AND transport_state = 'ACCEPTED' AND environment = 'production' AND test_identity_id IS NULL) OR (trial_state = 'IGNORED' AND processed_at IS NOT NULL AND trial_reason IS NOT NULL AND trial_reason IN ('TEST_IDENTITY', 'TEST_ENVIRONMENT', 'ALREADY_STARTED', 'EXISTING_SUBSCRIPTION', 'TRANSPORT_FAILED'))", name="ck_sad_trial_result"),
        CheckConstraint("trial_reason IS NULL OR (trial_reason <> 'TEST_IDENTITY' OR (environment = 'test' AND test_identity_id IS NOT NULL))", name="ck_sad_test_reason"),
        CheckConstraint("trial_reason IS NULL OR trial_reason <> 'TEST_ENVIRONMENT' OR environment = 'test'", name="ck_sad_environment_reason"),
        CheckConstraint("trial_reason IS NULL OR trial_reason <> 'TRANSPORT_FAILED' OR transport_state = 'FAILED'", name="ck_sad_failed_reason"),
        Index("uq_sad_qr_external", "whatsapp_channel_id", "external_message_id", unique=True, postgresql_where=text("external_message_id IS NOT NULL"), sqlite_where=text("external_message_id IS NOT NULL")),
        Index("uq_sad_cloud_external", "whatsapp_cloud_channel_id", "external_message_id", unique=True, postgresql_where=text("external_message_id IS NOT NULL"), sqlite_where=text("external_message_id IS NOT NULL")),
        Index("uq_sad_started_client", "client_id", unique=True, postgresql_where=text("trial_state = 'STARTED'"), sqlite_where=text("trial_state = 'STARTED'")),
        Index("ix_sad_pending", "client_id", "accepted_at", "id", postgresql_where=text("transport_state = 'ACCEPTED' AND trial_state = 'PENDING'"), sqlite_where=text("transport_state = 'ACCEPTED' AND trial_state = 'PENDING'")),
        Index("ix_sad_unresolved", "created_at", "id", postgresql_where=text("transport_state IN ('PREPARED', 'UNKNOWN')"), sqlite_where=text("transport_state IN ('PREPARED', 'UNKNOWN')")),
        Index("ix_sad_client", "client_id"),
        Index("ix_sad_test_identity", "test_identity_id"),
        Index("ix_sad_qr_channel", "whatsapp_channel_id"),
        Index("ix_sad_cloud_channel", "whatsapp_cloud_channel_id"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)
    client_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clients.id", ondelete="RESTRICT"))
    message_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("messages.id", ondelete="RESTRICT"))
    channel: Mapped[str] = mapped_column(String(24))
    whatsapp_channel_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("whatsapp_channels.id", ondelete="RESTRICT"))
    whatsapp_cloud_channel_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("whatsapp_cloud_channels.id", ondelete="RESTRICT"))
    origin: Mapped[str] = mapped_column(String(24))
    recipient_identity: Mapped[str] = mapped_column(String(255))
    environment: Mapped[str] = mapped_column(String(12))
    test_identity_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("subscription_test_identities.id", ondelete="RESTRICT"))
    transport_state: Mapped[str] = mapped_column(String(16))
    external_message_id: Mapped[str | None] = mapped_column(String(255))
    confirmation_type: Mapped[str | None] = mapped_column(String(32))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    recorded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_message_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    trial_state: Mapped[str] = mapped_column(String(16))
    trial_reason: Mapped[str | None] = mapped_column(String(40))
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


    @validates("accepted_at", "recorded_at", "source_message_timestamp", "processed_at", "created_at")
    def validate_timestamp(self, key, value):
        return _utc(value)
