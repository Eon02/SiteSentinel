"""Outgoing email over SMTP. Configure with the SMTP_* settings in .env."""

from dataclasses import dataclass
from email.message import EmailMessage

import aiosmtplib

from app.config import Settings


@dataclass
class OutgoingEmail:
    to: str
    subject: str
    body: str  # plain text


@dataclass
class DeliveryResult:
    to: str
    subject: str
    status: str  # 'sent' | 'failed' | 'skipped'
    error: str | None = None


def email_configured(settings: Settings) -> bool:
    return bool(settings.smtp_host and (settings.smtp_from or settings.smtp_username))


async def send_emails(settings: Settings, emails: list[OutgoingEmail]) -> list[DeliveryResult]:
    """Send each email separately over one SMTP connection, so recipients never see each other.

    A failure for one recipient doesn't stop the rest.
    """
    if not emails:
        return []
    if not email_configured(settings):
        reason = "Email is not configured (set SMTP_HOST and SMTP_FROM or SMTP_USERNAME)"
        return [DeliveryResult(e.to, e.subject, "skipped", reason) for e in emails]

    sender = settings.smtp_from or settings.smtp_username
    implicit_tls = settings.smtp_port == 465
    smtp = aiosmtplib.SMTP(
        hostname=settings.smtp_host,
        port=settings.smtp_port,
        use_tls=implicit_tls,
        start_tls=settings.smtp_starttls if not implicit_tls else False,
        timeout=30,
    )
    try:
        await smtp.connect()
        if settings.smtp_username and settings.smtp_password:
            await smtp.login(settings.smtp_username, settings.smtp_password)
    except (aiosmtplib.SMTPException, OSError) as exc:
        return [
            DeliveryResult(e.to, e.subject, "failed", f"SMTP connection failed: {exc!r}")
            for e in emails
        ]

    results = []
    try:
        for email in emails:
            message = EmailMessage()
            message["From"] = sender
            message["To"] = email.to
            message["Subject"] = email.subject
            message.set_content(email.body)
            try:
                await smtp.send_message(message)
                results.append(DeliveryResult(email.to, email.subject, "sent"))
            except aiosmtplib.SMTPException as exc:
                results.append(DeliveryResult(email.to, email.subject, "failed", repr(exc)))
    finally:
        try:
            await smtp.quit()
        except aiosmtplib.SMTPException:
            pass
    return results
