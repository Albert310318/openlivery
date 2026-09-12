"""One voice: a reply written by a person is still the business speaking.

The agent reads it as its own earlier turn, labelled, so it continues from
there instead of answering over the person who just wrote.
"""

from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.services.knowledge import llm_turns


def message(role: str, sender_type: str, content: str):
    return SimpleNamespace(role=role, sender_type=sender_type, content=content, llm_content=None)


def test_a_persons_reply_stays_an_assistant_turn_and_is_labelled():
    turns = llm_turns([
        message("user", "visitor", "how do I pay?"),
        message("assistant", "human", "Transfer to 1234567890 and send me the receipt."),
        message("user", "visitor", "done"),
    ], "en")

    assert [turn["role"] for turn in turns] == ["user", "assistant", "user"]
    assert turns[1]["content"].startswith("[Written by a person from the business")
    assert "Transfer to 1234567890" in turns[1]["content"], "the text itself is never altered"


def test_the_label_carries_the_instruction_when_the_person_spoke_last():
    """This is the case that fails without it: the business writes from the
    phone, the customer answers "hi", and the agent greets back as if it had
    just walked in. A rule in the system prompt is thousands of characters away
    from the answer; the label is two lines above it."""
    turns = llm_turns([
        message("assistant", "human", "Almost ready — could you send us your location?"),
        message("user", "visitor", "hi"),
    ], "en")

    assert "do not greet again" in turns[0]["content"]
    assert "Pick up whatever that person left open" in turns[0]["content"]


def test_once_the_agent_has_answered_the_short_label_is_enough():
    turns = llm_turns([
        message("assistant", "human", "Could you send us your location?"),
        message("user", "visitor", "hi"),
        message("assistant", "ai", "Hi! Could you share your location, please?"),
        message("user", "visitor", "sure"),
    ], "en")

    assert turns[0]["content"].startswith("[Written by a person from the business]")
    assert "do not greet again" not in turns[0]["content"]


def test_the_agents_own_turns_are_never_labelled():
    turns = llm_turns([
        message("assistant", "ai", "That would be $12."),
        message("user", "visitor", "ok"),
    ], "en")

    assert turns[0]["content"] == "That would be $12."


def test_the_label_follows_the_prompt_language():
    history = [message("assistant", "human", "Ya casi sale su pedido."), message("user", "visitor", "va")]
    assert llm_turns(history, "es")[0]["content"].startswith("[Escrito por una persona del negocio")
    assert llm_turns(history, "en")[0]["content"].startswith("[Written by a person from the business")
    # An unknown language falls back to the default rather than dropping the label.
    assert llm_turns(history, "fr")[0]["content"].startswith("[Escrito por una persona del negocio")


def test_an_operator_reply_reaches_the_agent_labelled(authenticated_client: TestClient, monkeypatch):
    """End to end: someone answers from the inbox, and the next turn the agent
    is given carries that message as the business's own, labelled."""
    from unittest.mock import AsyncMock

    from app.routers import conversations as conversations_router
    from app.services import ai as ai_service
    from conftest import customer_conversation

    client = authenticated_client
    customer = client.post(
        "/api/clients",
        json={"name": "Bakery", "industry": "restaurants_food", "business_type": "restaurant", "is_active": True},
    ).json()
    client.put("/api/providers/openai", json={"api_key": "sk-test"})
    agent = client.post(
        "/api/agents",
        json={"client_id": customer["id"], "provider": "openai", "model": "gpt-4.1-mini", "prompt_language": "en",
              "name": "Orders", "instructions": "Take orders.", "personality": "Warm", "is_active": True},
    ).json()
    conversation = customer_conversation(client, agent["id"])

    seen: list[list[dict]] = []

    async def capture(*args, **kwargs):
        seen.append(kwargs.get("messages") or args[4])
        return ai_service.Completion(text="On its way.")

    monkeypatch.setattr(conversations_router, "run_completion", AsyncMock(side_effect=capture))

    # A person takes over and answers by hand, then the customer writes again.
    client.patch(f"/api/conversations/{conversation['id']}/mode", json={"mode": "human"})
    replied = client.post(f"/api/conversations/{conversation['id']}/reply", json={"content": "Your order is out for delivery."})
    assert replied.status_code in (200, 201), replied.text
    client.patch(f"/api/conversations/{conversation['id']}/mode", json={"mode": "ai"})
    answered = client.post(f"/api/conversations/{conversation['id']}/messages", json={"content": "thanks!"})
    assert answered.status_code in (200, 201), answered.text

    turns = seen[-1]
    written_by_hand = next(turn for turn in turns if "out for delivery" in turn["content"])
    assert written_by_hand["role"] == "assistant", "it is still the business speaking"
    assert written_by_hand["content"].startswith("[Written by a person from the business")
