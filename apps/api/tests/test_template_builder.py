"""The template builder: what goes to Meta for each part, and what Meta
would refuse is refused here first, with the field named."""

import pytest
from fastapi import HTTPException

from app.services.whatsapp_templates import build_components, normalize, rendered_text, send_components


def _build(**kwargs):
    defaults = {"header": None, "body": "Hola {{nombre}}, tu pedido va en camino.", "footer": "", "buttons": [], "examples": {"nombre": "Ana"}}
    return build_components(**{**defaults, **kwargs})


def _refused(**kwargs) -> str:
    with pytest.raises(HTTPException) as caught:
        _build(**kwargs)
    assert caught.value.status_code == 422
    return caught.value.detail


def test_named_variables_travel_with_their_examples():
    components, fmt = _build(
        header={"format": "TEXT", "text": "Pedido {{numero}}"},
        footer="Responde para continuar.",
        examples={"nombre": "Ana", "numero": "8841"},
    )
    assert fmt == "NAMED"
    assert components == [
        {"type": "HEADER", "format": "TEXT", "text": "Pedido {{numero}}",
         "example": {"header_text_named_params": [{"param_name": "numero", "example": "8841"}]}},
        {"type": "BODY", "text": "Hola {{nombre}}, tu pedido va en camino.",
         "example": {"body_text_named_params": [{"param_name": "nombre", "example": "Ana"}]}},
        {"type": "FOOTER", "text": "Responde para continuar."},
    ]


def test_numbered_variables_keep_the_older_shape():
    components, fmt = _build(
        header={"format": "TEXT", "text": "Pedido {{1}}"},
        body="Hola {{1}}, te escribimos de {{2}}.",
        examples={"1": "Ana", "2": "Tienda"},
    )
    assert fmt == "POSITIONAL"
    assert components[0]["example"] == {"header_text": ["Ana"]}
    assert components[1]["example"] == {"body_text": [["Ana", "Tienda"]]}
    assert "gaps" in _refused(body="Hola {{1}}, ve a {{3}}.", examples={"1": "a", "3": "b"})


def test_the_message_rules_meta_enforces_are_checked_first():
    assert "start or end" in _refused(body="{{nombre}}, tu pedido va en camino.")
    assert "start or end" in _refused(body="Tu pedido va en camino, {{nombre}}")
    assert "lowercase" in _refused(body="Hola {{Nombre}}, tu pedido.", examples={})
    assert "mixes" in _refused(body="Hola {{nombre}}, pedido {{1}}.", examples={"nombre": "a", "1": "b"})
    assert "example" in _refused(examples={})
    assert "longer than 1024" in _refused(body="a" * 1025)
    assert "longer than 60" in _refused(footer="x" * 61)
    assert "variables" in _refused(footer="Hola {{nombre}}")
    assert "one variable" in _refused(header={"format": "TEXT", "text": "{{a}} {{b}}"}, examples={"nombre": "x", "a": "1", "b": "2"})
    assert "same kind" in _refused(header={"format": "TEXT", "text": "Pedido {{1}}"}, examples={"nombre": "x", "1": "8"})
    assert "sample" in _refused(header={"format": "IMAGE"})


def test_media_and_location_headers():
    components, _ = _build(header={"format": "VIDEO", "handle": "4:abc"})
    assert components[0] == {"type": "HEADER", "format": "VIDEO", "example": {"header_handle": ["4:abc"]}}
    components, _ = _build(header={"format": "LOCATION"})
    assert components[0] == {"type": "HEADER", "format": "LOCATION"}


def test_buttons_are_built_and_bounded():
    components, _ = _build(buttons=[
        {"type": "QUICK_REPLY", "text": "Sí, confirmo"},
        {"type": "QUICK_REPLY", "text": "Cambiar fecha"},
        {"type": "URL", "text": "Ver pedido", "url": "https://tienda.co/pedidos/{{1}}", "example": "8841"},
        {"type": "PHONE_NUMBER", "text": "Llamar", "phone_number": "+57 (300) 111-2233"},
        {"type": "COPY_CODE", "example": "VERANO25"},
    ])
    assert components[-1] == {"type": "BUTTONS", "buttons": [
        {"type": "QUICK_REPLY", "text": "Sí, confirmo"},
        {"type": "QUICK_REPLY", "text": "Cambiar fecha"},
        {"type": "URL", "text": "Ver pedido", "url": "https://tienda.co/pedidos/{{1}}", "example": ["8841"]},
        {"type": "PHONE_NUMBER", "text": "Llamar", "phone_number": "+573001112233"},
        {"type": "COPY_CODE", "example": "VERANO25"},
    ]}
    assert "label" in _refused(buttons=[{"type": "QUICK_REPLY", "text": ""}])
    assert "25 characters" in _refused(buttons=[{"type": "QUICK_REPLY", "text": "x" * 26}])
    assert "https://" in _refused(buttons=[{"type": "URL", "text": "Ir", "url": "tienda.co"}])
    assert "end with {{1}}" in _refused(buttons=[{"type": "URL", "text": "Ir", "url": "https://t.co/{{1}}/x"}])
    assert "example" in _refused(buttons=[{"type": "URL", "text": "Ir", "url": "https://t.co/{{1}}"}])
    assert "country code" in _refused(buttons=[{"type": "PHONE_NUMBER", "text": "Llamar", "phone_number": "abc"}])
    assert "website buttons at most" in _refused(buttons=[{"type": "URL", "text": "a", "url": "https://a.co"}] * 3)
    assert "one call button" in _refused(buttons=[{"type": "PHONE_NUMBER", "text": "a", "phone_number": "+573001112233"}] * 2)
    assert "10 buttons" in _refused(buttons=[{"type": "QUICK_REPLY", "text": "a"}] * 11)
    # Quick replies sit together, at one end.
    assert "together" in _refused(buttons=[
        {"type": "QUICK_REPLY", "text": "a"}, {"type": "URL", "text": "b", "url": "https://b.co"}, {"type": "QUICK_REPLY", "text": "c"},
    ])
    assert "together" in _refused(buttons=[
        {"type": "URL", "text": "b", "url": "https://b.co"}, {"type": "QUICK_REPLY", "text": "a"}, {"type": "PHONE_NUMBER", "text": "c", "phone_number": "+573001112233"},
    ])


def test_a_meta_template_is_read_whole():
    template = normalize({
        "id": "9", "name": "envio", "language": "es_CO", "category": "UTILITY", "status": "APPROVED", "parameter_format": "NAMED",
        "components": [
            {"type": "HEADER", "format": "IMAGE", "example": {"header_handle": ["4:x"]}},
            {"type": "BODY", "text": "Hola {{nombre}}, tu pedido {{pedido}} salió. {{nombre}}, gracias."},
            {"type": "FOOTER", "text": "Tienda"},
            {"type": "BUTTONS", "buttons": [
                {"type": "URL", "text": "Rastrear", "url": "https://t.co/{{1}}", "example": ["https://t.co/8841"]},
                {"type": "COPY_CODE", "example": "VERANO25"},
                {"type": "QUICK_REPLY", "text": "Gracias"},
            ]},
        ],
    })
    assert template["header"] == {"format": "IMAGE", "text": "", "parameters": []}
    assert template["parameters"] == ["nombre", "pedido"] and template["variables"] == 2
    assert [b["type"] for b in template["buttons"]] == ["URL", "COPY_CODE", "QUICK_REPLY"]
    assert [b["dynamic"] for b in template["buttons"]] == [True, True, False]
    assert template["buttons"][0]["example"] == "https://t.co/8841"

    components = send_components(template, body_values=["Ana", "8841"], header_value="https://cdn.co/a.jpg", button_values=["8841", "VERANO25", ""])
    assert components == [
        {"type": "header", "parameters": [{"type": "image", "image": {"link": "https://cdn.co/a.jpg"}}]},
        {"type": "body", "parameters": [
            {"type": "text", "text": "Ana", "parameter_name": "nombre"},
            {"type": "text", "text": "8841", "parameter_name": "pedido"},
        ]},
        {"type": "button", "sub_type": "url", "index": 0, "parameters": [{"type": "text", "text": "8841"}]},
        {"type": "button", "sub_type": "copy_code", "index": 1, "parameters": [{"type": "coupon_code", "coupon_code": "VERANO25"}]},
    ]
    assert rendered_text(template, body_values=["Ana", "8841"]) == "Hola Ana, tu pedido 8841 salió. Ana, gracias.\n\nTienda"

    with pytest.raises(HTTPException, match="2 values"):
        send_components(template, body_values=["Ana"])
    with pytest.raises(HTTPException, match="https://"):
        send_components(template, body_values=["Ana", "8841"], header_value="", button_values=["8841", "VERANO25"])
    with pytest.raises(HTTPException, match="buttons"):
        send_components(template, body_values=["Ana", "8841"], header_value="https://cdn.co/a.jpg", button_values=["8841"])


def test_text_and_location_headers_at_send_time():
    text = normalize({"name": "t", "language": "es", "components": [
        {"type": "HEADER", "format": "TEXT", "text": "Pedido {{1}}"}, {"type": "BODY", "text": "Hola {{1}}, listo."},
    ]})
    assert text["parameter_format"] == "POSITIONAL" and text["header"]["parameters"] == ["1"]
    assert send_components(text, body_values=["Ana"], header_value="8841") == [
        {"type": "header", "parameters": [{"type": "text", "text": "8841"}]},
        {"type": "body", "parameters": [{"type": "text", "text": "Ana"}]},
    ]
    assert rendered_text(text, body_values=["Ana"], header_value="8841") == "Pedido 8841\n\nHola Ana, listo."

    place = normalize({"name": "t", "language": "es", "components": [{"type": "HEADER", "format": "LOCATION"}, {"type": "BODY", "text": "Aquí estamos."}]})
    assert send_components(place, body_values=[], location={"latitude": 4.65, "longitude": -74.05, "name": "Tienda", "address": "Cra 7"}) == [
        {"type": "header", "parameters": [{"type": "location", "location": {"latitude": "4.65", "longitude": "-74.05", "name": "Tienda", "address": "Cra 7"}}]},
    ]
    with pytest.raises(HTTPException, match="location"):
        send_components(place, body_values=[])
