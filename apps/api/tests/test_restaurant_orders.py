import asyncio
import json
import uuid
from decimal import Decimal
from unittest.mock import AsyncMock

from conftest import TestingSession
import pytest

from sqlalchemy import select

from app.models import Agent, Client, Conversation, User, WhatsAppChannel, now_utc
from app.services.leads import lead_context_from_conversation
from app.services.tools import loop as tool_loop
from app.models_orders import RestaurantDeliveryNotification, RestaurantOrder, RestaurantOrderItem, RestaurantTableAccount
from app.models_restaurant import RestaurantModality
from app.services.restaurant_fulfillment import _delivery_card, notify_delivery_ready
from app.services.tools.internal import execute_internal_tool


@pytest.fixture(autouse=True)
def skip_email_delivery(monkeypatch):
    monkeypatch.setattr("app.services.portal_verification.send_verification_email", lambda *args: None)


@pytest.fixture
def restaurant_client(client, monkeypatch):
    sent = []
    monkeypatch.setattr("app.services.portal_verification.send_verification_email", lambda email, code: sent.append(code))
    response = client.post(
        "/api/auth/register",
        json={"agency_name": "Agencia Pedidos", "industry": "restaurante", "name": "Admin Pedidos", "email": "pedidos@test.com", "password": "contrasena-segura"},
    )
    assert response.status_code == 201
    assert client.post("/api/portal/email-verification/confirm", json={"code": sent[-1]}).status_code == 200
    return client


def test_playground_restaurant_order_uses_polleria_campos_context_and_stays_pending_payment(
    restaurant_client, monkeypatch
):
    client = restaurant_client
    with TestingSession() as db:
        user = db.scalar(select(User).where(User.email == "pedidos@test.com"))
        user.is_vendiq_admin = True
        db.commit()
    client_id = client.get("/api/clients/current").json()["id"]
    renamed = client.patch(f"/api/clients/{client_id}", json={"name": "Pollería Campos"})
    assert renamed.status_code == 200, renamed.text
    category = client.post(f"/api/restaurants/{client_id}/menu/categories", json={"name": "Bebidas"}).json()
    product = client.post(
        f"/api/restaurants/{client_id}/menu/products",
        json={"category_id": category["id"], "name": "Pepsi", "price": "10.00"},
    ).json()
    assert product["name"] == "Pepsi"
    assert client.patch(
        f"/api/restaurants/{client_id}/modalities",
        json={"pickup_enabled": True},
    ).status_code == 200
    payment_config = client.put(
        f"/api/restaurants/{client_id}/payment-methods/other",
        json={
            "display_name": "Datos para pagos",
            "account_number": "999888777",
            "account_name": "Pollería Campos SAC",
            "instructions": "Abona y envía el comprobante.",
            "receipt_required": True,
            "is_active": True,
        },
    )
    assert payment_config.status_code == 200, payment_config.text
    client.put("/api/providers/openai", json={"api_key": "secret"})
    agent = client.post(
        "/api/agents",
        json={
            "client_id": client_id,
            "provider": "openai",
            "model": "gpt-5",
            "name": "Sophia",
            "description": "",
            "instructions": "Ayuda con pedidos.",
            "personality": "Cercana",
            "is_active": True,
        },
    ).json()
    conversation_response = client.post("/api/conversations", json={"agent_id": agent["id"]})
    assert conversation_response.status_code == 201, conversation_response.text
    conversation = conversation_response.json()
    assert conversation["client_id"] == client_id
    assert conversation["agent_id"] == agent["id"]
    assert conversation["channel"] == "playground"

    provider_calls = [
        {"output": [{"type": "message", "content": [{"type": "output_text", "text": "¿Cuál es tu nombre?"}]}]},
        {"output": [{"type": "message", "content": [{"type": "output_text", "text": "¿Confirmas tu pedido?"}]}]},
        {
            "output": [{
                "type": "function_call",
                "name": "create_restaurant_order",
                "call_id": "call_order_1",
                "arguments": json.dumps({
                    "modality": "pickup",
                    "customer_name": "Nino",
                    "customer_phone": "",
                    "address": "",
                    "address_reference": "",
                    "delivery_zone": "",
                    # Legacy model output must not make the order depend on an
                    # inactive/removed Yape/Plin/etc. method.
                    "payment_method": "yape",
                    "items": [{
                        "product_name": "Pepsi",
                        "quantity": 1,
                        "variant_names": [],
                        "extra_names": [],
                        "observations": "",
                    }],
                }),
            }],
        },
        {"output": [{"type": "message", "content": [{"type": "output_text", "text": "Pedido registrado."}]}]},
        {"output": [{"type": "message", "content": [{"type": "output_text", "text": "Gracias, hasta luego."}]}]},
        {
            "output": [{
                "type": "function_call",
                "name": "report_restaurant_payment",
                "call_id": "call_payment_1",
                "arguments": json.dumps({"operation_id": "customer-operation-1", "amount": 10}),
            }],
        },
        {"output": [{"type": "message", "content": [{"type": "output_text", "text": "Pago reportado."}]}]},
    ]
    fake_post = AsyncMock(side_effect=provider_calls)
    monkeypatch.setattr(tool_loop, "_post_json", fake_post)

    assert client.post(
        f"/api/conversations/{conversation['id']}/messages",
        json={"content": "Hola, quiero pedir 1 Pepsi para recoger en el local."},
    ).status_code == 200
    assert client.post(
        f"/api/conversations/{conversation['id']}/messages",
        json={"content": "Nino"},
    ).status_code == 200
    final = client.post(
        f"/api/conversations/{conversation['id']}/messages",
        json={"content": "ok"},
    )
    assert final.status_code == 200, final.text
    final_text = final.json()["messages"][-1]["content"].casefold()
    assert "pendiente de pago" in final_text
    assert "total exacto: s/ 10.00" in final_text
    assert "número para pagos: 999888777" in final_text
    assert "titular: pollería campos sac" in final_text
    assert "comprobante" in final_text
    assert "puedes recoger" not in final_text
    tool_call = final.json()["messages"][-1]["tool_calls"][-1]
    assert tool_call["name"] == "create_restaurant_order"
    assert tool_call["is_error"] is False, tool_call

    orders = client.get(f"/api/restaurants/{client_id}/orders?status=pending_payment")
    assert orders.status_code == 200, orders.text
    assert len(orders.json()) == 1
    order = orders.json()[0]
    assert order["client_id"] == client_id
    assert order["conversation_id"] == conversation["id"]
    assert order["source"] == "whatsapp"
    assert order["modality"] == "pickup"
    assert order["subtotal"] == "10.00"
    assert order["order_status"] == "pending_payment"
    assert order["payment_status"] == "pending"
    assert order["payment_method"] is None
    assert order["items"][0]["product_name"] == "Pepsi"

    follow_up = client.post(
        f"/api/conversations/{conversation['id']}/messages",
        json={"content": "ok gracias"},
    )
    assert follow_up.status_code == 200, follow_up.text
    follow_up_text = follow_up.json()["messages"][-1]["content"].casefold()
    assert "pendiente de pago" in follow_up_text
    assert "número para pagos: 999888777" in follow_up_text
    assert "hasta luego" not in follow_up_text

    reported = client.post(
        f"/api/conversations/{conversation['id']}/messages",
        json={"content": "Ya pagué, operación customer-operation-1 por S/ 10.00."},
    )
    assert reported.status_code == 200, reported.text
    payment_tool_call = reported.json()["messages"][-1]["tool_calls"][-1]
    assert payment_tool_call["name"] == "report_restaurant_payment"
    assert payment_tool_call["is_error"] is False, payment_tool_call

    with TestingSession() as db:
        stored = db.get(RestaurantOrder, order["id"])
        assert stored is not None
        assert stored.client_id == uuid.UUID(client_id)
        assert stored.conversation_id == uuid.UUID(conversation["id"])
        db.refresh(stored)
        assert stored.payment_reference == "customer-operation-1"
        assert stored.reported_payment_amount == 10
        assert stored.receipt_submitted is True
        assert stored.payment_status == "pending"
        assert stored.order_status == "pending_payment"
        assert stored.payment_review_status == "review"

    confirmed = client.post(
        f"/api/restaurants/{client_id}/orders/{order['id']}/payment",
        json={},
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["payment_status"] == "confirmed"
    assert confirmed.json()["order_status"] == "confirmed"


def test_admin_rejects_voucher_without_sending_order_to_kitchen(restaurant_client):
    client_id = restaurant_client.get("/api/clients/current").json()["id"]
    with TestingSession() as db:
        order = RestaurantOrder(
            client_id=uuid.UUID(client_id),
            order_number="ORD-REJECT-1",
            source="whatsapp",
            modality="pickup",
            subtotal=Decimal("10.00"),
            total=Decimal("10.00"),
            order_status="pending_payment",
            payment_status="pending",
            payment_review_status="review",
            reported_payment_amount=Decimal("10.00"),
            payment_reference="reject-operation",
        )
        db.add(order)
        db.commit()
        order_id = str(order.id)

    rejected = restaurant_client.post(
        f"/api/restaurants/{client_id}/orders/{order_id}/payment/reject",
        json={"reason": "Monto no coincide"},
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["payment_status"] == "pending"
    assert rejected.json()["payment_review_status"] == "rejected"
    assert rejected.json()["order_status"] == "pending_payment"


def test_pickup_ready_notifies_and_uses_pickup_completion_not_delivery_status(restaurant_client, monkeypatch):
    client_id = restaurant_client.get("/api/clients/current").json()["id"]
    with TestingSession() as db:
        order = RestaurantOrder(
            client_id=uuid.UUID(client_id),
            order_number="ORD-PICKUP-READY",
            source="whatsapp",
            modality="pickup",
            customer_name="Cliente Recojo",
            subtotal=Decimal("20.00"),
            total=Decimal("20.00"),
            order_status="confirmed",
            payment_status="confirmed",
        )
        db.add(order)
        db.commit()
        order_id = str(order.id)

    notified = AsyncMock(return_value=True)
    monkeypatch.setattr("app.routers.orders.notify_pickup_ready", notified)
    ready = restaurant_client.patch(f"/api/restaurants/{client_id}/orders/{order_id}/status", json={"status": "ready"})
    assert ready.status_code == 200, ready.text
    notified.assert_awaited_once()
    blocked = restaurant_client.patch(f"/api/restaurants/{client_id}/orders/{order_id}/status", json={"status": "on_the_way"})
    assert blocked.status_code == 409
    picked_up = restaurant_client.patch(f"/api/restaurants/{client_id}/orders/{order_id}/status", json={"status": "delivered"})
    assert picked_up.status_code == 200, picked_up.text


def test_delivery_dispatch_card_excludes_restaurant_financial_data():
    order = RestaurantOrder(
        order_number="ORD-DELIVERY-CARD",
        customer_name="Albert",
        customer_phone="965396982",
        address="Calle Las Hiedras 120",
        address_reference="Frente al módulo de serenazgo",
        total=Decimal("76.00"),
        payment_method="yape",
        payment_reference="secret-operation",
        reported_payment_amount=Decimal("76.00"),
    )
    order.items = [RestaurantOrderItem(product_name="Pollo a la brasa", quantity=1)]
    card = _delivery_card(order)
    assert "ORD-DELIVERY-CARD" in card
    assert "Albert" in card and "965396982" in card
    assert "1 x Pollo a la brasa" in card
    assert "Costo de delivery: coordinar directamente con el cliente." in card
    assert "76" not in card
    assert "yape" not in card.casefold()
    assert "secret-operation" not in card


def test_delivery_notification_records_provider_confirmation_and_is_idempotent(restaurant_client, monkeypatch):
    client_id = uuid.UUID(restaurant_client.get("/api/clients/current").json()["id"])
    with TestingSession() as db:
        client = db.get(Client, client_id)
        agent = Agent(agency_id=client.agency_id, client_id=client.id, name="Delivery Agent", model="test")
        db.add(agent)
        db.flush()
        db.add(RestaurantModality(client_id=client.id, delivery_enabled=True, delivery_whatsapp="965396982"))
        db.add(WhatsAppChannel(agency_id=client.agency_id, client_id=client.id, agent_id=agent.id, status="connected", is_enabled=True))
        order = RestaurantOrder(client_id=client.id, order_number="ORD-DELIVERY-NOTIFY", source="whatsapp", modality="delivery", customer_name="Albert", customer_phone="965396982", address="Calle Las Hiedras 120", address_reference="Frente al parque", order_status="ready", payment_status="confirmed", ready_at=now_utc())
        db.add(order)
        db.commit()
        order_id = order.id

    sent = AsyncMock(return_value={"external_message_id": "wamid-delivery-1"})
    monkeypatch.setattr("app.services.restaurant_fulfillment.bridge_command", sent)
    with TestingSession() as db:
        order = db.get(RestaurantOrder, order_id)
        assert asyncio.run(notify_delivery_ready(db, order)) is True
        assert asyncio.run(notify_delivery_ready(db, order)) is True
        row = db.scalar(select(RestaurantDeliveryNotification).where(RestaurantDeliveryNotification.order_id == order_id))
        assert row.status == "sent"
        assert row.external_message_id == "wamid-delivery-1"
        assert sent.await_count == 1


def test_delivery_notification_history_backfill_is_idempotent(restaurant_client):
    client_id = uuid.UUID(restaurant_client.get("/api/clients/current").json()["id"])
    with TestingSession() as db:
        db.add(RestaurantModality(client_id=client_id, delivery_enabled=True, delivery_whatsapp="965396982"))
        db.add(
            RestaurantOrder(
                client_id=client_id,
                order_number="ORD-DELIVERY-HISTORY",
                source="whatsapp",
                modality="delivery",
                order_status="ready",
                payment_status="confirmed",
                ready_at=now_utc(),
            )
        )
        db.commit()

    first = restaurant_client.get(f"/api/restaurants/{client_id}/delivery-notifications")
    second = restaurant_client.get(f"/api/restaurants/{client_id}/delivery-notifications")
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    first_rows = [row for row in first.json() if row["order_number"] == "ORD-DELIVERY-HISTORY"]
    second_rows = [row for row in second.json() if row["order_number"] == "ORD-DELIVERY-HISTORY"]
    assert len(first_rows) == len(second_rows) == 1
    assert first_rows[0]["id"] == second_rows[0]["id"]


def test_whatsapp_uses_the_same_manual_payment_report_tool(restaurant_client):
    client_id = restaurant_client.get("/api/clients/current").json()["id"]
    with TestingSession() as db:
        client_row = db.get(Client, uuid.UUID(client_id))
        agent = Agent(
            agency_id=client_row.agency_id,
            client_id=client_row.id,
            name="Sophia WhatsApp",
            provider="openai",
            model="gpt-5",
            is_active=True,
        )
        db.add(agent)
        db.flush()
        conversation = Conversation(
            agency_id=client_row.agency_id,
            client_id=client_row.id,
            agent_id=agent.id,
            channel="whatsapp",
            external_chat_id="51999999999@s.whatsapp.net",
        )
        db.add(conversation)
        db.flush()
        order = RestaurantOrder(
            client_id=client_row.id,
            order_number="ORD-WA-REVIEW",
            source="whatsapp",
            modality="pickup",
            subtotal=Decimal("25.00"),
            total=Decimal("25.00"),
            order_status="pending_payment",
            payment_status="pending",
            conversation_id=conversation.id,
        )
        db.add(order)
        db.commit()
        result, is_error = asyncio.run(execute_internal_tool(
            db,
            "report_restaurant_payment",
            {"operation_id": "wa-operation-1", "amount": 25},
            lead_context_from_conversation(conversation),
        ))
        assert is_error is False, result
        db.refresh(order)
        assert order.payment_status == "pending"
        assert order.payment_review_status == "review"
        assert order.payment_reference == "wa-operation-1"

        second_order = RestaurantOrder(
            client_id=client_row.id,
            order_number="ORD-WA-REVIEW-2",
            source="whatsapp",
            modality="pickup",
            subtotal=Decimal("30.00"),
            total=Decimal("30.00"),
            order_status="pending_payment",
            payment_status="pending",
            conversation_id=conversation.id,
        )
        db.add(second_order)
        db.commit()
        duplicate_result, duplicate_is_error = asyncio.run(execute_internal_tool(
            db,
            "report_restaurant_payment",
            {"operation_id": "wa-operation-1", "amount": 30},
            lead_context_from_conversation(conversation),
        ))
        assert duplicate_is_error is True
        assert "already been used" in duplicate_result
        db.refresh(second_order)
        assert second_order.payment_status == "pending"
        assert second_order.payment_review_status == "pending"


def test_waiter_order_calculates_totals_and_closes_cumulative_table_account(restaurant_client):
    client_id = restaurant_client.get("/api/clients/current").json()["id"]
    category = restaurant_client.post(f"/api/restaurants/{client_id}/menu/categories", json={"name": "Pollos"}).json()
    product = restaurant_client.post(
        f"/api/restaurants/{client_id}/menu/products",
        json={
            "category_id": category["id"],
            "name": "Pollo clásico",
            "price": "20.00",
            "variants": [{"name": "Grande", "price": "5.00"}],
            "extras": [{"name": "Papas", "price": "3.00"}],
        },
    ).json()
    assert restaurant_client.patch(
        f"/api/restaurants/{client_id}/modalities", json={"dine_in_enabled": True}
    ).status_code == 200

    order = restaurant_client.post(
        f"/api/restaurants/{client_id}/orders",
        json={
            "table_number": "4",
            "items": [{"product_id": product["id"], "quantity": 2, "variant_ids": [product["variants"][0]["id"]], "extra_ids": [product["extras"][0]["id"]]}],
            "idempotency_key": "test-table-4-order-1",
        },
    )
    assert order.status_code == 201
    assert order.json()["subtotal"] == "56.00"
    assert order.json()["total"] == "56.00"
    assert order.json()["order_status"] == "confirmed"

    duplicate = restaurant_client.post(
        f"/api/restaurants/{client_id}/orders",
        json={"table_number": "4", "items": [{"product_id": product["id"], "quantity": 2}], "idempotency_key": "test-table-4-order-1"},
    )
    assert duplicate.status_code == 201
    assert duplicate.json()["id"] == order.json()["id"]

    assert restaurant_client.patch(
        f"/api/restaurants/{client_id}/orders/{order.json()['id']}/status", json={"status": "preparing"}
    ).status_code == 200
    assert restaurant_client.patch(
        f"/api/restaurants/{client_id}/orders/{order.json()['id']}/status", json={"status": "ready"}
    ).status_code == 200
    paid = restaurant_client.post(
        f"/api/restaurants/{client_id}/orders/{order.json()['id']}/payment",
        json={"payment_method": "cash"},
    )
    assert paid.status_code == 200
    assert paid.json()["payment_status"] == "confirmed"
    assert paid.json()["order_status"] == "closed"

    with TestingSession() as db:
        stored = db.get(RestaurantOrder, order.json()["id"])
        account = db.get(RestaurantTableAccount, order.json()["table_account_id"])
        assert stored is not None and stored.client_id == uuid.UUID(client_id)
        assert account is not None and account.is_open is False and account.status == "closed"


def test_waiter_marks_ready_table_order_served_without_closing_the_table(restaurant_client):
    client_id = restaurant_client.get("/api/clients/current").json()["id"]
    category = restaurant_client.post(f"/api/restaurants/{client_id}/menu/categories", json={"name": "Cocina"}).json()
    product = restaurant_client.post(
        f"/api/restaurants/{client_id}/menu/products",
        json={"category_id": category["id"], "name": "Pollo de prueba", "price": "25.00"},
    ).json()
    assert restaurant_client.patch(f"/api/restaurants/{client_id}/modalities", json={"dine_in_enabled": True}).status_code == 200
    staff = restaurant_client.post(
        f"/api/restaurants/{client_id}/staff",
        json={"name": "Mesero de prueba", "email": "waiter-served@test.com", "password": "clave-segura-1", "role": "waiter"},
    )
    assert staff.status_code == 201

    restaurant_client.post("/api/auth/logout")
    assert restaurant_client.post("/api/auth/login", json={"email": "waiter-served@test.com", "password": "clave-segura-1"}).status_code == 200
    order = restaurant_client.post(
        f"/api/restaurants/{client_id}/orders",
        json={"table_number": "8", "items": [{"product_id": product["id"], "quantity": 1}]},
    )
    assert order.status_code == 201
    order_id = order.json()["id"]
    assert restaurant_client.patch(f"/api/restaurants/{client_id}/orders/{order_id}/status", json={"status": "served"}).status_code == 409

    restaurant_client.post("/api/auth/logout")
    assert restaurant_client.post("/api/auth/login", json={"email": "pedidos@test.com", "password": "contrasena-segura"}).status_code == 200
    assert restaurant_client.patch(f"/api/restaurants/{client_id}/orders/{order_id}/status", json={"status": "preparing"}).status_code == 200
    assert restaurant_client.patch(f"/api/restaurants/{client_id}/orders/{order_id}/status", json={"status": "ready"}).status_code == 200

    restaurant_client.post("/api/auth/logout")
    assert restaurant_client.post("/api/auth/login", json={"email": "waiter-served@test.com", "password": "clave-segura-1"}).status_code == 200
    served = restaurant_client.patch(f"/api/restaurants/{client_id}/orders/{order_id}/status", json={"status": "served"})
    assert served.status_code == 200
    assert served.json()["order_status"] == "served"

    accounts = restaurant_client.get(f"/api/restaurants/{client_id}/table-accounts")
    assert accounts.status_code == 200
    table = next(account for account in accounts.json() if account["table_number"] == "8")
    assert table["is_open"] is True
    assert table["total"] == "25.00"
    assert table["orders"][0]["order_status"] == "served"
    assert table["orders"][0]["payment_status"] == "pending"

    restaurant_client.post("/api/auth/logout")
    assert restaurant_client.post("/api/auth/login", json={"email": "pedidos@test.com", "password": "contrasena-segura"}).status_code == 200
    pending = restaurant_client.get(f"/api/restaurants/{client_id}/orders?status=pending_payment")
    assert pending.status_code == 200, pending.text
    assert any(row["id"] == order_id and row["order_status"] == "served" for row in pending.json())


def test_cashier_pays_one_cumulative_table_account_and_waiter_cannot_pay(restaurant_client):
    client_id = restaurant_client.get("/api/clients/current").json()["id"]
    category = restaurant_client.post(f"/api/restaurants/{client_id}/menu/categories", json={"name": "Caja"}).json()
    pollo = restaurant_client.post(
        f"/api/restaurants/{client_id}/menu/products",
        json={"category_id": category["id"], "name": "Pollo", "price": "35.00"},
    ).json()
    bebida = restaurant_client.post(
        f"/api/restaurants/{client_id}/menu/products",
        json={"category_id": category["id"], "name": "Pepsi", "price": "8.00"},
    ).json()
    assert restaurant_client.patch(f"/api/restaurants/{client_id}/modalities", json={"dine_in_enabled": True}).status_code == 200
    assert restaurant_client.post(
        f"/api/restaurants/{client_id}/staff",
        json={"name": "Caja de prueba", "email": "cashier-table@test.com", "password": "clave-segura-1", "role": "cashier"},
    ).status_code == 201
    assert restaurant_client.post(
        f"/api/restaurants/{client_id}/staff",
        json={"name": "Mesero de prueba", "email": "waiter-table@test.com", "password": "clave-segura-2", "role": "waiter"},
    ).status_code == 201

    first = restaurant_client.post(
        f"/api/restaurants/{client_id}/orders",
        json={"table_number": "4", "items": [{"product_id": pollo["id"], "quantity": 1}]},
    ).json()
    second = restaurant_client.post(
        f"/api/restaurants/{client_id}/orders",
        json={"table_number": "4", "items": [{"product_id": bebida["id"], "quantity": 2}]},
    ).json()
    assert first["total"] == "35.00"
    assert second["total"] == "16.00"

    restaurant_client.post("/api/auth/logout")
    assert restaurant_client.post("/api/auth/login", json={"email": "waiter-table@test.com", "password": "clave-segura-2"}).status_code == 200
    forbidden = restaurant_client.post(
        f"/api/restaurants/{client_id}/orders/{first['id']}/payment",
        json={"payment_method": "cash"},
    )
    assert forbidden.status_code == 403

    restaurant_client.post("/api/auth/logout")
    assert restaurant_client.post("/api/auth/login", json={"email": "cashier-table@test.com", "password": "clave-segura-1"}).status_code == 200
    accounts = restaurant_client.get(f"/api/restaurants/{client_id}/table-accounts")
    assert accounts.status_code == 200
    assert len(accounts.json()) == 1
    account = accounts.json()[0]
    assert account["table_number"] == "4"
    assert account["total"] == "51.00"
    assert {order["id"] for order in account["orders"]} == {first["id"], second["id"]}

    paid = restaurant_client.post(
        f"/api/restaurants/{client_id}/orders/{first['id']}/payment",
        json={"payment_method": "cash"},
    )
    assert paid.status_code == 200
    assert restaurant_client.get(f"/api/restaurants/{client_id}/table-accounts").json() == []
    for order_id in (first["id"], second["id"]):
        stored = restaurant_client.get(f"/api/restaurants/{client_id}/orders/{order_id}")
        assert stored.status_code == 200
        assert stored.json()["payment_status"] == "confirmed"
        assert stored.json()["order_status"] == "closed"
