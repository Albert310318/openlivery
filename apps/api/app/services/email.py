"""SMTP transport. Never log credentials, message contents or recipient addresses."""
import smtplib
import ssl
from email.message import EmailMessage

from ..config import get_settings


class EmailDeliveryError(Exception):
    pass


def send_verification_email(recipient: str, code: str) -> None:
    settings = get_settings()
    if not settings.smtp_host or not settings.smtp_from_email:
        raise EmailDeliveryError("SMTP is not configured")
    message = EmailMessage()
    message["From"] = settings.smtp_from_email
    message["To"] = recipient
    message["Subject"] = "AYV — Código de recuperación"
    message.set_content(f"Tu código de recuperación de Atiende y Vende es: {code}\n\nVence en 10 minutos. No compartas este código.")
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=settings.smtp_timeout) as smtp:
            if settings.smtp_use_tls:
                smtp.starttls(context=ssl.create_default_context())
            if settings.smtp_username:
                smtp.login(settings.smtp_username, settings.smtp_password)
            if smtp.send_message(message):
                raise EmailDeliveryError("Recipient refused")
    except (OSError, smtplib.SMTPException) as exc:
        raise EmailDeliveryError("SMTP delivery failed") from exc
