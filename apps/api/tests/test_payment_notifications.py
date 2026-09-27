from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.models import Agency, Client
from app.models_orders import PaymentNotification, RestaurantOrder
from app.models_restaurant import RestaurantPaymentMailbox, RestaurantPaymentMethod
from app.services.payment_mail_parsers import _labeled_value, parse_payment_email
from app.services import payment_mailbox
from app.services.payment_mailbox import _select_payment_method, poll_mailbox
from app.services.payment_notifications import (
    DuplicatePaymentNotificationError,
    reconcile_payment_notifications,
    record_payment_notification,
)
from conftest import TestingSession


LEMON_TEMPLATE = """From: notificaciones@lemoncash.com.ar
Message-ID: <{message_id}@example.test>
Date: Thu, 24 Sep 2026 15:30:00 +0000
Subject: Recibiste S/ {amount} 🙌
Content-Type: text/plain; charset=utf-8

¡recibiste S/ {amount}.0000!
Nombre del pagador: Juan Perez
CCI: 002123456789
Entidad: {origin_entity}
Mensaje: YAPE
ID de la operación: {operation_id}
ID externo: EXT-{operation_id}
Fecha y hora: 24/09/2026 15:29:11
"""


def _create_client(db, name: str) -> Client:
    agency = Agency(name=f"Agencia {name}", slug=f"agencia-{name.lower()}")
    db.add(agency)
    db.flush()
    client = Client(
        agency_id=agency.id,
        name=name,
        industry="restaurante",
        description="",
        general_context="",
        portal_slug=f"portal-{name.lower()}",
    )
    db.add(client)
    db.flush()
    return client


def _create_yape_method(db, client: Client) -> RestaurantPaymentMethod:
    method = RestaurantPaymentMethod(
        client_id=client.id,
        method="yape",
        display_name="Yape",
        is_active=True,
    )
    db.add(method)
    db.flush()
    return method


def _create_order(
    db,
    client: Client,
    *,
    subtotal: str = "20.00",
    delivery_fee: str = "0.00",
    order_number: str = "ORD-1",
    created_at: datetime | None = None,
    payment_method: str | None = "yape",
) -> RestaurantOrder:
    order = RestaurantOrder(
        client_id=client.id,
        order_number=order_number,
        source="whatsapp",
        modality="delivery" if Decimal(delivery_fee) else "pickup",
        subtotal=Decimal(subtotal),
        delivery_fee=Decimal(delivery_fee),
        total=Decimal(subtotal) + Decimal(delivery_fee),
        order_status="pending_payment",
        payment_status="pending",
        payment_method=payment_method,
        created_at=created_at or datetime(2026, 9, 24, 15, 20, tzinfo=timezone.utc),
    )
    db.add(order)
    db.flush()
    return order


def _parsed_notification(raw: bytes):
    parsed = parse_payment_email(raw)
    assert parsed.provider == "lemon_dale"
    assert parsed.payment_method_hint == "yape"
    return parsed


def _raw(*, message_id="message-1", amount="20", operation_id="operation-1", origin_entity="BCP") -> bytes:
    return LEMON_TEMPLATE.format(
        message_id=message_id,
        amount=amount,
        operation_id=operation_id,
        origin_entity=origin_entity,
    ).encode()


def _record(db, client, method, parsed, **kwargs):
    reported = kwargs.pop("reported", True)
    if reported:
        pending_orders = db.query(RestaurantOrder).filter(
            RestaurantOrder.client_id == client.id,
            RestaurantOrder.payment_status == "pending",
            RestaurantOrder.order_status == "pending_payment",
        ).all()
        for order in pending_orders:
            # Mailbox tests represent the preceding customer report.  The
            # dedicated no-report regression below opts out explicitly.
            if not order.payment_reference:
                order.payment_reference = parsed.external_operation_id
            order.reported_payment_amount = parsed.amount
            order.receipt_submitted = True
        db.flush()
    kwargs.setdefault("received_at", parsed.occurred_at)
    return record_payment_notification(
        db,
        client_id=client.id,
        payment_method=method,
        notification_email="payments@example.test",
        external_operation_id=parsed.external_operation_id,
        source_message_id=parsed.source_message_id,
        amount=parsed.amount,
        occurred_at=parsed.occurred_at,
        sender=parsed.sender,
        subject=parsed.subject,
        metadata=parsed.metadata,
        **kwargs,
    )


def test_legacy_mailbox_notification_is_retained_without_confirming_order():
    with TestingSession() as db:
        client = _create_client(db, "Lemon Uno")
        method = _create_yape_method(db, client)
        order = _create_order(db, client)
        parsed = _parsed_notification(_raw())

        notification = _record(db, client, method, parsed)

        assert notification.status == "unmatched"
        assert notification.matched_order_id is None
        assert order.payment_status == "pending"
        assert order.order_status == "pending_payment"
        assert notification.notification_metadata["provider"] == "lemon_dale"
        assert notification.notification_metadata["external_id"] == "EXT-operation-1"


def test_single_payment_number_mailbox_does_not_confirm_without_manual_admin_action():
    with TestingSession() as db:
        client = _create_client(db, "Número único")
        order = _create_order(db, client, payment_method=None)
        parsed = _parsed_notification(_raw(operation_id="single-number-operation"))

        notification = _record(db, client, None, parsed)

        assert notification.payment_method_id is None
        assert notification.status == "unmatched"
        assert notification.matched_order_id is None
        assert order.payment_status == "pending"
        assert order.order_status == "pending_payment"


def test_imap_notification_alone_does_not_confirm_without_customer_operation_report():
    with TestingSession() as db:
        client = _create_client(db, "Sin comprobante reportado")
        order = _create_order(db, client, payment_method=None)
        parsed = _parsed_notification(_raw(operation_id="image-only-operation"))

        notification = _record(db, client, None, parsed, reported=False)

        assert notification.status == "unmatched"
        assert notification.matched_order_id is None
        assert order.payment_status == "pending"
        assert order.order_status == "pending_payment"


def test_imap_poll_matches_single_payment_number_and_exposes_order_to_kitchen(monkeypatch):
    with TestingSession() as db:
        client = _create_client(db, "Polling IMAP")
        order = _create_order(db, client, payment_method=None)
        order.payment_reference = "imap-operation"
        order.reported_payment_amount = Decimal("20.00")
        order.receipt_submitted = True
        mailbox = RestaurantPaymentMailbox(
            client_id=client.id,
            email="payments@example.test",
            encrypted_app_password="encrypted-test-secret",
        )
        db.add(mailbox)
        db.commit()
        mailbox_id = mailbox.id
        raw_message = _raw(message_id="imap-message", operation_id="imap-operation")

    class FakeConnection:
        def __init__(self):
            self.stored = []

        def uid(self, *args):
            self.stored.append(args)

        def logout(self):
            return None

    connection = FakeConnection()
    searched_since = []
    monkeypatch.setattr(payment_mailbox, "decrypt_secret", lambda value: "test-password")
    monkeypatch.setattr(payment_mailbox, "_connect", lambda mailbox, password: connection)
    monkeypatch.setattr(payment_mailbox, "_fetch_since", lambda connection, since: (searched_since.append(since) or [(b"1", raw_message)]))

    assert poll_mailbox(mailbox_id) == 1

    with TestingSession() as db:
        refreshed = db.get(RestaurantOrder, order.id)
        assert searched_since == [datetime(2026, 9, 24).date()]
        assert refreshed.payment_method is None
        assert refreshed.payment_status == "pending"
        assert refreshed.order_status == "pending_payment"
        assert any(item[0] == "store" for item in connection.stored)


def test_origin_entity_does_not_select_or_reject_the_receiving_method():
    with TestingSession() as db:
        client = _create_client(db, "Origen Distinto")
        method = _create_yape_method(db, client)
        order = _create_order(db, client)
        parsed = replace(_parsed_notification(_raw(operation_id="operation-origin")), payment_method_hint="transfer")

        selected_method = _select_payment_method([method], parsed)
        notification = _record(db, client, selected_method, parsed)

        assert selected_method == method
        assert notification.status == "unmatched"
        assert notification.matched_order_id is None


def test_banbif_origin_is_allowed_when_restaurant_receives_by_yape():
    with TestingSession() as db:
        client = _create_client(db, "BanBif a Yape")
        method = _create_yape_method(db, client)
        order = _create_order(db, client)
        parsed = _parsed_notification(_raw(operation_id="operation-banbif", origin_entity="BanBif"))

        selected_method = _select_payment_method([method], parsed)
        notification = _record(db, client, selected_method, parsed)

        assert parsed.metadata["origin_entity"] == "BanBif"
        assert parsed.payment_method_hint == "yape"
        assert selected_method == method
        assert notification.status == "unmatched"
        assert notification.matched_order_id is None


def test_amount_mismatch_does_not_confirm_order():
    with TestingSession() as db:
        client = _create_client(db, "Monto Distinto")
        method = _create_yape_method(db, client)
        order = _create_order(db, client, subtotal="20.00")
        parsed = _parsed_notification(_raw(amount="21", operation_id="operation-2"))

        notification = _record(db, client, method, parsed)

        assert notification.status == "unmatched"
        assert notification.matched_order_id is None
        assert order.payment_status == "pending"
        assert order.order_status == "pending_payment"


def test_two_same_amount_orders_remain_unmatched_for_manual_review():
    with TestingSession() as db:
        client = _create_client(db, "Ambiguo")
        method = _create_yape_method(db, client)
        _create_order(db, client, order_number="ORD-A")
        _create_order(db, client, order_number="ORD-B")
        parsed = _parsed_notification(_raw(operation_id="operation-3"))

        notification = _record(db, client, method, parsed)

        assert notification.status == "unmatched"
        assert notification.matched_order_id is None


def test_duplicate_message_id_is_not_processed_twice():
    with TestingSession() as db:
        client = _create_client(db, "Mensaje Duplicado")
        method = _create_yape_method(db, client)
        _create_order(db, client)
        parsed = _parsed_notification(_raw(message_id="same-message", operation_id="operation-4"))
        _record(db, client, method, parsed)

        with pytest.raises(DuplicatePaymentNotificationError):
            _record(db, client, method, parsed)
        assert db.query(PaymentNotification).count() == 1


def test_duplicate_operation_is_not_processed_twice_even_with_new_message_id():
    with TestingSession() as db:
        client = _create_client(db, "Operacion Duplicada")
        method = _create_yape_method(db, client)
        _create_order(db, client)
        first = _parsed_notification(_raw(message_id="message-5a", operation_id="stable-operation"))
        second = _parsed_notification(_raw(message_id="message-5b", operation_id="stable-operation"))
        _record(db, client, method, first)

        with pytest.raises(DuplicatePaymentNotificationError):
            _record(db, client, method, second)
        assert db.query(PaymentNotification).count() == 1


def test_order_from_another_client_can_never_match():
    with TestingSession() as db:
        client_a = _create_client(db, "Empresa A")
        client_b = _create_client(db, "Empresa B")
        method_a = _create_yape_method(db, client_a)
        order_b = _create_order(db, client_b)
        parsed = _parsed_notification(_raw(operation_id="operation-6"))

        notification = _record(db, client_a, method_a, parsed)

        assert notification.status == "unmatched"
        assert notification.matched_order_id is None
        assert order_b.payment_status == "pending"


def test_unparseable_email_requires_review():
    with TestingSession() as db:
        client = _create_client(db, "No Parseable")
        method = _create_yape_method(db, client)
        _create_order(db, client)
        parsed = parse_payment_email(
            b"From: notificaciones@lemoncash.com.ar\nMessage-ID: <bad@example.test>\n"
            b"Subject: Recibiste S/ 20\n\nRecibiste S/ 20"
        )
        assert parsed.parse_ready is False

        notification = _record(db, client, method, parsed, review_required=True)

        assert notification.status == "review_required"
        assert notification.matched_order_id is None


def test_labeled_value_does_not_backtrack_on_long_email_without_label():
    text = ("texto sin etiquetas " * 2_000) + "\nMensaje: YAPE\n"

    assert _labeled_value(text, "nombre del pagador", "nombre") is None
    assert _labeled_value(text, "mensaje", "canal", "medio") == "YAPE"


def test_delivery_fee_does_not_change_reported_mailbox_data():
    with TestingSession() as db:
        client = _create_client(db, "Delivery Separado")
        method = _create_yape_method(db, client)
        order = _create_order(db, client, delivery_fee="7.00")
        parsed = _parsed_notification(_raw(operation_id="operation-8"))

        notification = _record(db, client, method, parsed)

        assert notification.status == "unmatched"
        assert notification.matched_order_id is None
        assert order.subtotal == Decimal("20.00")
        assert order.delivery_fee == Decimal("7.00")


def test_notification_received_twenty_minutes_before_order_remains_manual_review():
    with TestingSession() as db:
        client = _create_client(db, "Correo Antes del Pedido")
        method = _create_yape_method(db, client)
        order = _create_order(db, client, created_at=datetime(2026, 9, 24, 15, 20, tzinfo=timezone.utc))
        parsed = _parsed_notification(_raw(operation_id="before-order"))

        notification = _record(
            db,
            client,
            method,
            parsed,
            received_at=datetime(2026, 9, 24, 15, 0, tzinfo=timezone.utc),
        )

        assert notification.status == "unmatched"
        assert notification.matched_order_id is None
        assert order.payment_status == "pending"


def test_notification_received_thirty_one_minutes_before_order_is_not_used():
    with TestingSession() as db:
        client = _create_client(db, "Correo Fuera de Ventana")
        method = _create_yape_method(db, client)
        order = _create_order(db, client, created_at=datetime(2026, 9, 24, 15, 20, tzinfo=timezone.utc))
        parsed = _parsed_notification(_raw(operation_id="too-early"))

        notification = _record(
            db,
            client,
            method,
            parsed,
            received_at=datetime(2026, 9, 24, 14, 49, tzinfo=timezone.utc),
        )

        assert notification.status == "unmatched"
        assert order.payment_status == "pending"
        assert order.order_status == "pending_payment"


def test_operation_mismatch_does_not_confirm_order():
    with TestingSession() as db:
        client = _create_client(db, "Operacion Distinta")
        method = _create_yape_method(db, client)
        order = _create_order(db, client)
        order.payment_reference = "operation-from-receipt"
        parsed = _parsed_notification(_raw(operation_id="operation-from-email"))

        notification = _record(db, client, method, parsed)

        assert notification.status == "unmatched"
        assert notification.matched_order_id is None
        assert order.payment_status == "pending"


def test_notification_without_matching_email_keeps_order_pending():
    with TestingSession() as db:
        client = _create_client(db, "Sin Correo Coincidente")
        _create_yape_method(db, client)
        order = _create_order(db, client)

        assert order.payment_status == "pending"
        assert order.order_status == "pending_payment"


def test_unmatched_notification_is_retried_after_order_becomes_available():
    with TestingSession() as db:
        client = _create_client(db, "Reintento")
        method = _create_yape_method(db, client)
        parsed = _parsed_notification(_raw(operation_id="retry-operation"))
        notification = _record(
            db,
            client,
            method,
            parsed,
            received_at=datetime(2026, 9, 24, 15, 0, tzinfo=timezone.utc),
        )
        assert notification.status == "unmatched"

        order = _create_order(
            db,
            client,
            created_at=datetime(2026, 9, 24, 15, 20, tzinfo=timezone.utc),
        )
        order.payment_reference = "retry-operation"
        order.reported_payment_amount = Decimal("20.00")
        order.receipt_submitted = True
        assert reconcile_payment_notifications(db, client_id=client.id) == 0
        assert notification.status == "unmatched"
        assert order.payment_status == "pending"
