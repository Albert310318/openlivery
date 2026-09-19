"""Subscription administration, trial activation and commercial authorization.

The trial engine consumes durable activation evidence only. Transport adapters
remain responsible for producing that evidence in a later phase; this module
does not send messages or enforce access at any channel boundary.
"""
from __future__ import annotations

import uuid
import re
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..config import trial_activation_eligible_since
from ..models import Client, ClientSubscription, Conversation, Message, Plan, User, now_utc
from ..models_promotions import SubscriptionCharge
from ..models_subscription_activation import SubscriptionActivationDelivery, SubscriptionTestIdentity
from ..schemas_subscriptions import SubscriptionWrite


TRIAL_DURATION = timedelta(hours=72)
FREE_PLAN_CODE = "free"
FREE_PLAN_NAME = "Free"
FREE_PLAN_CURRENCY = "PEN"
FREE_PLAN_PRICE = Decimal("0.00")

_PHONE_E164 = re.compile(r"\A\+[1-9][0-9]{1,14}\Z")
_LID = re.compile(r"\A[1-9][0-9]*@lid\Z")
_QR_PHONE_JID = re.compile(r"\A([1-9][0-9]{1,14})@s\.whatsapp\.net\Z")
_CLOUD_PHONE = re.compile(r"\A[1-9][0-9]{1,14}\Z")
logger = logging.getLogger(__name__)


class TrialActivationError(ValueError):
    """Raised when activation evidence is not coherent or Free is invalid."""


@dataclass(frozen=True)
class TrialActivationResult:
    delivery_id: uuid.UUID
    client_id: uuid.UUID
    trial_started: bool
    evidence_state: str
    reason: str | None = None
    subscription_id: uuid.UUID | None = None


@dataclass(frozen=True)
class TrialExpirationResult:
    client_id: uuid.UUID
    status_before: str | None
    status_after: str | None
    expired: bool
    changed: bool
    paid_coverage: bool
    subscription_id: uuid.UUID | None = None


@dataclass(frozen=True)
class CommercialActionDecision:
    client_id: uuid.UUID
    allowed: bool
    reason: str
    status: str | None
    subscription_id: uuid.UUID | None = None
    trial_expired: bool = False


def _utc(value: datetime, *, field: str = "timestamp") -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _stored_utc(value: datetime, *, field: str) -> datetime:
    """Normalize a value loaded from a backend that dropped timezone metadata."""
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def normalize_phone_e164(value: str) -> str:
    """Return a canonical E.164 phone number without guessing a country code."""
    if not isinstance(value, str):
        raise ValueError("Phone identity must be text")
    candidate = value.strip()
    if candidate.startswith("00"):
        candidate = "+" + candidate[2:]
    candidate = re.sub(r"[\s().-]", "", candidate)
    if not _PHONE_E164.fullmatch(candidate):
        raise ValueError("Phone identity must be a valid E.164 number")
    return candidate


def normalize_lid(value: str) -> str:
    """Return a WhatsApp LID unchanged as a LID; never convert it to a phone."""
    if not isinstance(value, str):
        raise ValueError("LID identity must be text")
    candidate = value.strip()
    if not _LID.fullmatch(candidate):
        raise ValueError("LID identity must end in @lid")
    return candidate


def normalize_identity(value: str) -> str:
    candidate = value.strip() if isinstance(value, str) else value
    if isinstance(candidate, str) and candidate.endswith("@lid"):
        return normalize_lid(candidate)
    return normalize_phone_e164(candidate)


def normalize_qr_recipient(value: str) -> str:
    """Canonicalize the direct recipient form used by the Baileys channel."""
    candidate = value.strip() if isinstance(value, str) else value
    match = _QR_PHONE_JID.fullmatch(candidate) if isinstance(candidate, str) else None
    if match:
        return normalize_phone_e164("+" + match.group(1))
    return normalize_identity(candidate)


def active_test_identity(
    db: Session,
    client_id: uuid.UUID,
    identity_value: str,
) -> SubscriptionTestIdentity | None:
    canonical = normalize_identity(identity_value)
    return db.scalar(select(SubscriptionTestIdentity).where(
        SubscriptionTestIdentity.client_id == client_id,
        SubscriptionTestIdentity.identity_value == canonical,
        SubscriptionTestIdentity.is_active.is_(True),
    ))


def is_test_identity(db: Session, client_id: uuid.UUID, identity_value: str) -> bool:
    """Return whether an active test identity owns this canonical recipient."""
    return active_test_identity(db, client_id, identity_value) is not None


def prepare_activation_delivery(
    db: Session,
    *,
    client_id: uuid.UUID,
    message_id: uuid.UUID,
    whatsapp_channel_id: uuid.UUID,
    recipient: str,
    trusted_recipient: str | None = None,
    source_message_timestamp: datetime | None = None,
) -> SubscriptionActivationDelivery:
    """Build durable QR evidence before transport starts.

    The caller must add this object and commit it in the same transaction as
    the assistant Message. The database trigger rechecks message/channel
    ownership and the test identity classification.
    """
    canonical = normalize_qr_recipient(recipient)
    client = db.scalar(select(Client).where(Client.id == client_id).with_for_update())
    if client is None:
        raise TrialActivationError("Activation client not found")
    candidates = [canonical]
    # Baileys may expose an opaque LID as the transport destination and a
    # verified phone JID as its alternate. The alternate is usable only when
    # supplied by that trusted bridge relationship; never derive a phone from
    # the LID itself.
    if canonical.endswith("@lid") and trusted_recipient:
        try:
            trusted = normalize_qr_recipient(trusted_recipient)
        except ValueError:
            trusted = None
        if trusted and trusted.startswith("+") and trusted not in candidates:
            candidates.append(trusted)
    identities = db.scalars(select(SubscriptionTestIdentity).where(
        SubscriptionTestIdentity.client_id == client_id,
        SubscriptionTestIdentity.identity_value.in_(candidates),
        SubscriptionTestIdentity.is_active.is_(True),
    )).all()
    identity = next((item for candidate in candidates for item in identities if item.identity_value == candidate), None)
    return SubscriptionActivationDelivery(
        client_id=client_id,
        message_id=message_id,
        channel="whatsapp_qr",
        whatsapp_channel_id=whatsapp_channel_id,
        origin="automatic_ai_reply",
        recipient_identity=canonical,
        environment="test" if identity else "production",
        test_identity_id=identity.id if identity else None,
        transport_state="PREPARED",
        trial_state="PENDING",
        source_message_timestamp=source_message_timestamp,
    )


def _canonical_delivery_recipient(delivery: SubscriptionActivationDelivery, conversation: Conversation) -> str:
    destination = conversation.external_chat_id
    if not destination:
        raise TrialActivationError("Activation conversation has no recipient")
    if delivery.channel == "whatsapp_qr":
        phone = _QR_PHONE_JID.fullmatch(destination)
        if phone:
            return normalize_phone_e164("+" + phone.group(1))
        return normalize_lid(destination) if _LID.fullmatch(destination) else normalize_identity(destination)
    if _CLOUD_PHONE.fullmatch(destination):
        return normalize_phone_e164("+" + destination)
    return normalize_identity(destination)


def _validate_delivery_coherence(db: Session, delivery: SubscriptionActivationDelivery, client: Client) -> None:
    row = db.execute(select(Message, Conversation).join(
        Conversation, Conversation.id == Message.conversation_id,
    ).where(Message.id == delivery.message_id)).one_or_none()
    if row is None:
        raise TrialActivationError("Activation message not found")
    message, conversation = row
    if (
        message.role != "assistant"
        or message.sender_type != "ai"
        or conversation.client_id != client.id
        or conversation.agency_id != client.agency_id
    ):
        raise TrialActivationError("Activation requires an AI assistant message owned by the client")
    if delivery.channel == "whatsapp_qr":
        coherent = conversation.channel == "whatsapp" and conversation.whatsapp_channel_id == delivery.whatsapp_channel_id
    elif delivery.channel == "whatsapp_cloud":
        coherent = conversation.channel == "whatsapp_cloud" and conversation.whatsapp_cloud_channel_id == delivery.whatsapp_cloud_channel_id
    else:
        coherent = False
    if not coherent:
        raise TrialActivationError("Activation channel and message do not match")
    if _canonical_delivery_recipient(delivery, conversation) != normalize_identity(delivery.recipient_identity):
        raise TrialActivationError("Activation recipient does not match the message")


def _free_plan(db: Session) -> Plan:
    plan = db.scalar(select(Plan).where(Plan.code == FREE_PLAN_CODE).with_for_update())
    if plan is None:
        raise TrialActivationError("Free plan is missing")
    if (
        plan.name != FREE_PLAN_NAME
        or plan.monthly_price != FREE_PLAN_PRICE
        or plan.currency != FREE_PLAN_CURRENCY
        or not plan.is_active
        or plan.available_for_new_subscriptions
    ):
        raise TrialActivationError("Free plan configuration is invalid")
    return plan


def _mark_delivery_ignored(delivery: SubscriptionActivationDelivery, reason: str, processed_at: datetime) -> None:
    delivery.trial_state = "IGNORED"
    delivery.trial_reason = reason
    delivery.processed_at = processed_at


def _activation_context(db: Session, delivery: SubscriptionActivationDelivery):
    return db.execute(select(Message, Conversation, Client).join(
        Conversation, Conversation.id == Message.conversation_id,
    ).join(
        Client, Client.id == Conversation.client_id,
    ).where(Message.id == delivery.message_id)).one_or_none()


def _validate_activation_callback(
    db: Session,
    delivery: SubscriptionActivationDelivery,
    *,
    message_id: uuid.UUID,
    whatsapp_channel_id: uuid.UUID,
) -> tuple[Message, Conversation, Client]:
    context = _activation_context(db, delivery)
    if context is None:
        raise TrialActivationError("Activation message not found")
    message, conversation, client = context
    if (
        delivery.message_id != message_id
        or delivery.whatsapp_channel_id != whatsapp_channel_id
        or conversation.whatsapp_channel_id != whatsapp_channel_id
        or conversation.client_id != delivery.client_id
        or client.id != delivery.client_id
        or message.role != "assistant"
        or message.sender_type != "ai"
    ):
        raise TrialActivationError("Activation callback does not match its message or channel")
    return message, conversation, client


def confirm_activation_delivery(
    db: Session,
    *,
    delivery_id: uuid.UUID,
    message_id: uuid.UUID,
    whatsapp_channel_id: uuid.UUID,
    external_message_id: str,
    accepted_at: datetime,
) -> bool:
    """Persist one QR send confirmation; return whether it changed evidence."""
    accepted_at = _utc(accepted_at, field="accepted_at")
    delivery = db.scalar(select(SubscriptionActivationDelivery).where(
        SubscriptionActivationDelivery.id == delivery_id,
    ).with_for_update())
    if delivery is None:
        raise TrialActivationError("Activation evidence not found")
    message, _, _ = _validate_activation_callback(
        db,
        delivery,
        message_id=message_id,
        whatsapp_channel_id=whatsapp_channel_id,
    )
    if delivery.transport_state == "ACCEPTED":
        if delivery.external_message_id != external_message_id or message.external_message_id not in (None, external_message_id):
            raise TrialActivationError("Conflicting ACCEPTED activation callback")
        if message.external_message_id is None:
            message.external_message_id = external_message_id
        db.commit()
        return False
    if delivery.transport_state != "PREPARED":
        raise TrialActivationError("Activation evidence is no longer confirmable")
    if message.external_message_id not in (None, external_message_id):
        raise TrialActivationError("Activation message already has a different external ID")
    message.external_message_id = external_message_id
    delivery.transport_state = "ACCEPTED"
    delivery.external_message_id = external_message_id
    delivery.confirmation_type = "BAILEYS_SEND_RESULT"
    delivery.accepted_at = accepted_at
    delivery.recorded_at = now_utc()
    db.commit()
    return True


def resolve_activation_transport_failure(
    db: Session,
    *,
    delivery_id: uuid.UUID,
    message_id: uuid.UUID,
    whatsapp_channel_id: uuid.UUID,
    state: str,
) -> bool:
    """Resolve an unconfirmed send without ever granting a trial."""
    if state not in {"FAILED", "UNKNOWN"}:
        raise TrialActivationError("Invalid activation transport outcome")
    delivery = db.scalar(select(SubscriptionActivationDelivery).where(
        SubscriptionActivationDelivery.id == delivery_id,
    ).with_for_update())
    if delivery is None:
        raise TrialActivationError("Activation evidence not found")
    _validate_activation_callback(
        db,
        delivery,
        message_id=message_id,
        whatsapp_channel_id=whatsapp_channel_id,
    )
    if delivery.transport_state == "ACCEPTED":
        raise TrialActivationError("Accepted activation evidence cannot be downgraded")
    if delivery.transport_state == state:
        db.commit()
        return False
    if delivery.transport_state != "PREPARED":
        raise TrialActivationError("Activation evidence has a conflicting transport outcome")
    delivery.transport_state = state
    if state == "FAILED":
        _mark_delivery_ignored(delivery, "TRANSPORT_FAILED", now_utc())
    db.commit()
    return True


def ensure_trial_started(db: Session, delivery_id: uuid.UUID) -> TrialActivationResult:
    """Consume one accepted activation evidence row exactly once.

    The client row is the serialization point for all deliveries belonging to
    a business. The database unique constraint on client subscriptions is a
    second guard against accidental duplicate trials.
    """
    delivery = db.scalar(select(SubscriptionActivationDelivery).where(
        SubscriptionActivationDelivery.id == delivery_id,
    ).with_for_update())
    if delivery is None:
        raise TrialActivationError("Activation evidence not found")

    client = db.scalar(select(Client).where(Client.id == delivery.client_id).with_for_update())
    if client is None:
        raise TrialActivationError("Activation client not found")
    if delivery.trial_state != "PENDING":
        db.commit()
        return TrialActivationResult(
            delivery_id=delivery.id,
            client_id=client.id,
            trial_started=delivery.trial_state == "STARTED",
            evidence_state=delivery.trial_state,
            reason=delivery.trial_reason,
            subscription_id=db.scalar(select(ClientSubscription.id).where(ClientSubscription.client_id == client.id)),
        )
    if delivery.transport_state != "ACCEPTED":
        db.commit()
        return TrialActivationResult(delivery.id, client.id, False, delivery.trial_state, "TRANSPORT_NOT_ACCEPTED")
    if delivery.environment != "production":
        if delivery.test_identity_id is not None:
            processed_at = now_utc()
            _mark_delivery_ignored(delivery, "TEST_IDENTITY", processed_at)
            db.commit()
            return TrialActivationResult(delivery.id, client.id, False, delivery.trial_state, delivery.trial_reason)
        db.commit()
        return TrialActivationResult(delivery.id, client.id, False, delivery.trial_state, "TEST_ENVIRONMENT")
    if delivery.origin != "automatic_ai_reply" or delivery.test_identity_id is not None:
        db.commit()
        return TrialActivationResult(delivery.id, client.id, False, delivery.trial_state, "TEST_IDENTITY" if delivery.test_identity_id else "INVALID_ORIGIN")
    if delivery.accepted_at is None:
        raise TrialActivationError("Accepted evidence has no accepted_at timestamp")

    _validate_delivery_coherence(db, delivery, client)
    if is_test_identity(db, client.id, delivery.recipient_identity):
        # A production evidence row cannot normally coexist with a registered
        # test identity because migration 0024 guards that invariant. If an
        # identity was registered after delivery creation, leave the evidence
        # pending rather than writing an invalid TEST_IDENTITY classification.
        db.commit()
        return TrialActivationResult(delivery.id, client.id, False, delivery.trial_state, "TEST_IDENTITY")

    cutoff = trial_activation_eligible_since()
    source_timestamp = delivery.source_message_timestamp
    if cutoff is None:
        logger.warning("Trial activation held pending: no valid eligibility cutoff for delivery %s", delivery.id)
        db.commit()
        return TrialActivationResult(delivery.id, client.id, False, delivery.trial_state, "INELIGIBLE_CUTOFF")
    if source_timestamp is None:
        logger.warning("Trial activation held pending: delivery %s has no trusted source timestamp", delivery.id)
        db.commit()
        return TrialActivationResult(delivery.id, client.id, False, delivery.trial_state, "INELIGIBLE_TIMESTAMP")
    if source_timestamp.astimezone(timezone.utc) < cutoff:
        logger.info("Trial activation held pending: delivery %s predates eligibility cutoff", delivery.id)
        db.commit()
        return TrialActivationResult(delivery.id, client.id, False, delivery.trial_state, "INELIGIBLE_TIMESTAMP")

    subscription = db.scalar(select(ClientSubscription).where(
        ClientSubscription.client_id == client.id,
    ).with_for_update())
    started_delivery = db.scalar(select(SubscriptionActivationDelivery.id).where(
        SubscriptionActivationDelivery.client_id == client.id,
        SubscriptionActivationDelivery.trial_state == "STARTED",
    ).limit(1))
    if subscription is not None or started_delivery is not None:
        processed_at = now_utc()
        _mark_delivery_ignored(delivery, "ALREADY_STARTED", processed_at)
        db.commit()
        return TrialActivationResult(
            delivery.id, client.id, False, delivery.trial_state, delivery.trial_reason,
            subscription.id if subscription else None,
        )

    plan = _free_plan(db)
    accepted_at = _stored_utc(delivery.accepted_at, field="accepted_at")
    subscription = ClientSubscription(
        client_id=client.id,
        plan_id=plan.id,
        status="TRIAL",
        first_activated_at=accepted_at,
        trial_started_at=accepted_at,
        trial_ends_at=accepted_at + TRIAL_DURATION,
    )
    db.add(subscription)
    delivery.trial_state = "STARTED"
    delivery.trial_reason = "FIRST_ACCEPTED_REPLY"
    delivery.processed_at = now_utc()
    db.commit()
    return TrialActivationResult(delivery.id, client.id, True, delivery.trial_state, delivery.trial_reason, subscription.id)


def _has_paid_coverage(db: Session, client_id: uuid.UUID, now: datetime) -> bool:
    """Conservative paid coverage check; only settled PAID charges qualify."""
    return db.scalar(select(SubscriptionCharge.id).where(
        SubscriptionCharge.client_id == client_id,
        SubscriptionCharge.status == "PAID",
        SubscriptionCharge.paid_at.is_not(None),
        SubscriptionCharge.paid_at <= now,
        SubscriptionCharge.period_start <= now,
        SubscriptionCharge.period_end > now,
    ).limit(1)) is not None


def has_paid_coverage(
    db: Session,
    client_id: uuid.UUID,
    now: datetime | None = None,
) -> bool:
    """Public coverage policy: only a settled, current charge is payment."""
    effective_now = _utc(now, field="now") if now is not None else now_utc()
    return _has_paid_coverage(db, client_id, effective_now)


def evaluate_trial_expiration(
    db: Session,
    client_id: uuid.UUID,
    now: datetime | None = None,
) -> TrialExpirationResult:
    """Expire a trial at its fixed deadline, using only confirmed coverage."""
    effective_now = _utc(now, field="now") if now is not None else now_utc()
    client = db.scalar(select(Client).where(Client.id == client_id).with_for_update())
    if client is None:
        raise TrialActivationError("Client not found")
    subscription = db.scalar(select(ClientSubscription).where(
        ClientSubscription.client_id == client_id,
    ).with_for_update())
    if subscription is None:
        db.commit()
        return TrialExpirationResult(client_id, None, None, False, False, False)
    before = subscription.status
    if subscription.status != "TRIAL" or subscription.trial_ends_at is None:
        db.commit()
        return TrialExpirationResult(client_id, before, before, False, False, False, subscription.id)
    trial_ends_at = _stored_utc(subscription.trial_ends_at, field="trial_ends_at")
    if effective_now < trial_ends_at:
        db.commit()
        return TrialExpirationResult(client_id, before, before, False, False, False, subscription.id)

    paid_coverage = has_paid_coverage(db, client_id, effective_now)
    subscription.status = "ACTIVE" if paid_coverage else "SUSPENDED"
    db.commit()
    return TrialExpirationResult(client_id, before, subscription.status, True, True, paid_coverage, subscription.id)


def require_commercial_action(
    db: Session,
    client_id: uuid.UUID,
    *,
    now: datetime | None = None,
    action: str | None = None,
) -> CommercialActionDecision:
    """Return one central decision for new automatic commercial actions.

    ``action`` is diagnostic context only. This function does not intercept
    human support, authentication, reads, history or inbound messages.
    """
    expiration = evaluate_trial_expiration(db, client_id, now=now)
    subscription = db.scalar(select(ClientSubscription).where(ClientSubscription.client_id == client_id))
    if subscription is None:
        return CommercialActionDecision(client_id, True, "NO_SUBSCRIPTION", None, trial_expired=expiration.expired)
    status = subscription.status
    if status == "TRIAL":
        return CommercialActionDecision(client_id, True, "TRIAL_ACTIVE", status, subscription.id, expiration.expired)
    if status == "ACTIVE":
        return CommercialActionDecision(client_id, True, "ACTIVE", status, subscription.id, expiration.expired)
    if status == "PAYMENT_PENDING":
        return CommercialActionDecision(client_id, False, "PAYMENT_PENDING_NOT_CONFIRMED", status, subscription.id, expiration.expired)
    if status in {"SUSPENDED", "CANCELLED"}:
        return CommercialActionDecision(client_id, False, status, status, subscription.id, expiration.expired)
    return CommercialActionDecision(client_id, False, "UNKNOWN_SUBSCRIPTION_STATUS", status, subscription.id, expiration.expired)


def reconcile_pending_activation_evidence(
    db: Session,
    *,
    client_id: uuid.UUID | None = None,
    limit: int | None = None,
) -> list[TrialActivationResult]:
    """Reprocess accepted pending evidence; scheduling is intentionally absent."""
    query = select(SubscriptionActivationDelivery.id).where(
        SubscriptionActivationDelivery.transport_state == "ACCEPTED",
        SubscriptionActivationDelivery.trial_state == "PENDING",
    ).order_by(SubscriptionActivationDelivery.accepted_at, SubscriptionActivationDelivery.id)
    if client_id is not None:
        query = query.where(SubscriptionActivationDelivery.client_id == client_id)
    if limit is not None:
        query = query.limit(limit)
    delivery_ids = list(db.scalars(query).all())
    db.commit()
    return [ensure_trial_started(db, delivery_id) for delivery_id in delivery_ids]


def available_plans(db: Session) -> list[Plan]:
    return list(db.scalars(select(Plan).options(selectinload(Plan.modules)).where(
        Plan.is_active.is_(True), Plan.available_for_new_subscriptions.is_(True),
    ).order_by(Plan.code)).all())


def subscription_for_client(db: Session, client_id: uuid.UUID) -> ClientSubscription | None:
    return db.scalar(select(ClientSubscription).options(
        selectinload(ClientSubscription.plan).selectinload(Plan.modules),
    ).where(ClientSubscription.client_id == client_id))


def admin_client(db: Session, user: User, client_id: uuid.UUID, *, lock: bool = False) -> Client:
    query = select(Client).where(Client.id == client_id)
    if not user.is_vendiq_admin:
        query = query.where(Client.agency_id == user.agency_id)
    if lock:
        query = query.with_for_update()
    client = db.scalar(query)
    if client is None:
        raise HTTPException(status_code=404, detail="Client not found")
    return client


def save_subscription(db: Session, user: User, client_id: uuid.UUID, payload: SubscriptionWrite) -> ClientSubscription:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Administrator access required")
    # Lock the parent even when no subscription exists, serializing first assignments.
    admin_client(db, user, client_id, lock=True)
    subscription = subscription_for_client(db, client_id)
    plan = db.get(Plan, payload.plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="Plan not found")
    if (subscription is None or subscription.plan_id != plan.id) and (
        not plan.is_active or not plan.available_for_new_subscriptions
    ):
        raise HTTPException(status_code=409, detail="Plan is not available for new subscriptions")
    if subscription is None:
        subscription = ClientSubscription(client_id=client_id, plan_id=plan.id, status=payload.status)
        db.add(subscription)
    subscription.plan = plan
    subscription.status = payload.status
    subscription.next_renewal_at = payload.next_renewal_at.astimezone(timezone.utc) if payload.next_renewal_at else None
    # Never write activation/trial dates or existing operational client/agent fields.
    db.commit()
    return subscription
