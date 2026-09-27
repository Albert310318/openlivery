"""Tenant-scoped sales reports for confirmed restaurant payments."""

import io
import unicodedata
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session, selectinload

from ..models import User, now_utc
from ..models_orders import RestaurantOrder
from ..schemas_reports import ProductSales, SalesMovement, SalesPaymentTotals, SalesReportOut


PAYMENT_KEYS = ("cash", "yape", "plin", "transfer", "card", "other")


def _origin(order: RestaurantOrder) -> str:
    if order.modality == "dine_in":
        return "Mesa"
    if order.modality == "delivery":
        return "Delivery"
    return "WhatsApp/Recojo"


def _payment_method(order: RestaurantOrder) -> str:
    method = (order.payment_method or "other").strip().casefold()
    return method if method in PAYMENT_KEYS else "other"


def build_sales_report(db: Session, client_id, start: datetime, end: datetime) -> SalesReportOut:
    orders = db.scalars(
        select(RestaurantOrder)
        .options(selectinload(RestaurantOrder.items))
        .where(
            RestaurantOrder.client_id == client_id,
            or_(
                and_(RestaurantOrder.created_at >= start, RestaurantOrder.created_at < end),
                and_(RestaurantOrder.payment_confirmed_at >= start, RestaurantOrder.payment_confirmed_at < end),
            ),
        )
        .order_by(RestaurantOrder.created_at.desc())
    ).unique().all()
    confirmed = [
        order for order in orders
        if order.payment_status == "confirmed"
        and order.payment_confirmed_at is not None
        and order.order_status != "cancelled"
        and start <= order.payment_confirmed_at < end
    ]
    payment_totals = {key: Decimal("0") for key in PAYMENT_KEYS}
    origin_totals: dict[str, Decimal] = {"Mesa": Decimal("0"), "WhatsApp/Recojo": Decimal("0"), "Delivery": Decimal("0")}
    product_totals: dict[str, ProductSales] = {}
    movements: list[SalesMovement] = []
    for order in confirmed:
        method = _payment_method(order)
        origin = _origin(order)
        amount = Decimal(order.total)
        payment_totals[method] += amount
        origin_totals[origin] += amount
        user_name = None
        if order.payment_confirmed_by_user_id:
            user = db.get(User, order.payment_confirmed_by_user_id)
            user_name = user.name if user else None
        item_data = []
        for item in order.items:
            if item.is_cancelled:
                continue
            item_data.append({"product_name": item.product_name, "quantity": item.quantity, "amount": Decimal(item.line_subtotal)})
            current = product_totals.get(item.product_name)
            if current:
                current.quantity += item.quantity
                current.amount += Decimal(item.line_subtotal)
            else:
                product_totals[item.product_name] = ProductSales(
                    product_name=item.product_name,
                    quantity=item.quantity,
                    amount=Decimal(item.line_subtotal),
                )
        movements.append(SalesMovement(
            order_id=order.id,
            order_number=order.order_number,
            paid_at=order.payment_confirmed_at,
            origin=origin,
            table_or_customer=(f"Mesa {order.table_number}" if order.table_number else order.customer_name or "—"),
            amount=amount,
            payment_method=method,
            status=order.order_status,
            items=item_data,
            confirmed_by=user_name,
        ))
    pending = [order for order in orders if order.payment_status != "confirmed" and order.order_status == "pending_payment"]
    cancelled = [order for order in orders if order.order_status == "cancelled"]
    return SalesReportOut(
        client_id=client_id,
        from_datetime=start,
        to_datetime=end,
        generated_at=now_utc(),
        total_sales=sum((row.amount for row in movements), Decimal("0")),
        paid_orders=len(movements),
        pending_orders=len(pending),
        cancelled_orders=len(cancelled),
        payment_totals=SalesPaymentTotals(**payment_totals),
        origin_totals=origin_totals,
        movements=movements,
        products=list(product_totals.values()),
    )


def _pdf_text(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode("ascii")
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")[:150]


def build_sales_pdf(report: SalesReportOut, restaurant_name: str) -> bytes:
    """Build a dependency-free, readable PDF for local reporting."""
    lines = [
        "REPORTE DE VENTAS",
        f"Restaurante: {restaurant_name}",
        f"Periodo: {report.from_datetime:%Y-%m-%d %H:%M} a {report.to_datetime:%Y-%m-%d %H:%M}",
        f"Generado: {report.generated_at:%Y-%m-%d %H:%M}",
        "",
        f"Ventas totales: S/ {report.total_sales:.2f}",
        f"Pedidos cobrados: {report.paid_orders}",
        f"Pedidos pendientes: {report.pending_orders}",
        f"Cancelados: {report.cancelled_orders}",
        "",
        "METODOS DE PAGO",
    ]
    for key, value in report.payment_totals.model_dump().items():
        lines.append(f"{key}: S/ {value:.2f}")
    lines += ["", "ORIGEN"]
    lines += [f"{key}: S/ {value:.2f}" for key, value in report.origin_totals.items()]
    lines += ["", "DETALLE DE PEDIDOS"]
    for movement in report.movements:
        lines.append(f"{movement.order_number} | {movement.paid_at:%Y-%m-%d %H:%M} | {movement.origin} | {movement.table_or_customer} | S/ {movement.amount:.2f} | {movement.payment_method}")
        for item in movement.items:
            lines.append(f"  {item['quantity']} x {item['product_name']} | S/ {item['amount']:.2f}")
    lines += ["", "PRODUCTOS VENDIDOS"]
    for item in report.products:
        lines.append(f"{item.product_name}: {item.quantity} unidad(es) | S/ {item.amount:.2f}")

    page_size = 48
    pages = [lines[index:index + page_size] for index in range(0, len(lines), page_size)] or [[]]
    objects: list[bytes] = []
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(b"")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    page_numbers = []
    for page_lines in pages:
        stream_lines = ["BT", "/F1 9 Tf", "40 760 Td"]
        for index, line in enumerate(page_lines):
            if index:
                stream_lines.append("0 -14 Td")
            stream_lines.append(f"({_pdf_text(line)}) Tj")
        stream_lines.append("ET")
        stream = "\n".join(stream_lines).encode("latin-1")
        content_number = len(objects) + 1
        objects.append(f"<< /Length {len(stream)} >>\nstream\n".encode("latin-1") + stream + b"\nendstream")
        page_number = len(objects) + 1
        objects.append(f"<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 3 0 R >> >> /MediaBox [0 0 595 842] /Contents {content_number} 0 R >>".encode("latin-1"))
        page_numbers.append(page_number)
    objects[1] = f"<< /Type /Pages /Kids [{' '.join(f'{number} 0 R' for number in page_numbers)}] /Count {len(page_numbers)} >>".encode("latin-1")
    output = io.BytesIO()
    output.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for number, obj in enumerate(objects, start=1):
        offsets.append(output.tell())
        output.write(f"{number} 0 obj\n".encode("latin-1"))
        output.write(obj)
        output.write(b"\nendobj\n")
    xref = output.tell()
    output.write(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode("latin-1"))
    for offset in offsets[1:]:
        output.write(f"{offset:010d} 00000 n \n".encode("latin-1"))
    output.write(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode("latin-1"))
    return output.getvalue()
