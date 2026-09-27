from datetime import date, datetime, time, timedelta, timezone
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user
from ..models import Client, User
from ..schemas_reports import SalesReportOut
from ..services.restaurant_orders import restaurant_access
from ..services.restaurant_reports import build_sales_pdf, build_sales_report


router = APIRouter(prefix="/restaurants", tags=["Restaurant Reports"])


def _period(from_date: date | None, to_date: date | None) -> tuple[datetime, datetime]:
    start_date = from_date or date.today()
    end_date = to_date or start_date
    if end_date < start_date:
        raise HTTPException(status_code=422, detail="The report end date cannot be before its start date")
    start = datetime.combine(start_date, time.min, tzinfo=timezone.utc)
    end = datetime.combine(end_date + timedelta(days=1), time.min, tzinfo=timezone.utc)
    return start, end


def _report_access(db: Session, user: User, client_id: uuid.UUID) -> Client:
    client, role = restaurant_access(db, user, client_id, {"admin"})
    if role != "admin":
        raise HTTPException(status_code=403, detail="Only restaurant administrators can view sales reports")
    return client


@router.get("/{client_id}/reports/sales", response_model=SalesReportOut)
def sales_report(
    client_id: uuid.UUID,
    from_date: date | None = Query(default=None, alias="from"),
    to_date: date | None = Query(default=None, alias="to"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _report_access(db, user, client_id)
    start, end = _period(from_date, to_date)
    return build_sales_report(db, client_id, start, end)


@router.get("/{client_id}/reports/sales.pdf")
def sales_report_pdf(
    client_id: uuid.UUID,
    from_date: date | None = Query(default=None, alias="from"),
    to_date: date | None = Query(default=None, alias="to"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    client = _report_access(db, user, client_id)
    start, end = _period(from_date, to_date)
    report = build_sales_report(db, client_id, start, end)
    return Response(
        content=build_sales_pdf(report, client.name),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="reporte-ventas-{start:%Y%m%d}-{end - timedelta(days=1):%Y%m%d}.pdf"'},
    )
