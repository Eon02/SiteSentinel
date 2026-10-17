"""Outgoing SMS through Twilio's REST API. Configure with the TWILIO_* settings in .env.

https://www.twilio.com/docs/messaging/api/message-resource#create-a-message-resource
"""

import asyncio
from dataclasses import dataclass

import httpx

from app.config import Settings

CONCURRENT_SENDS = 4
RETRIES = 3  # timeouts, 429 and 5xx only; Twilio rejections (bad number etc.) aren't retried
TIMEOUT = httpx.Timeout(20.0, connect=10.0)


@dataclass
class DeliveryResult:
    to: str  # E.164 phone number
    status: str  # 'sent' | 'failed' | 'skipped'
    error: str | None = None


@dataclass
class OutgoingSms:
    to: str  # E.164
    body: str


def sms_configured(settings: Settings) -> bool:
    return bool(
        settings.twilio_account_sid
        and settings.twilio_auth_token
        and (settings.twilio_from_number or settings.twilio_messaging_service_sid)
    )


async def send_sms(
    client: httpx.AsyncClient, settings: Settings, messages: list[OutgoingSms]
) -> list[DeliveryResult]:
    """Send each text separately. A failure for one recipient doesn't stop the rest.

    With TWILIO_BODY_TEMPLATE set (Twilio trial accounts), the template name is sent as the
    body instead of each message's own text.
    """
    if not messages:
        return []
    if not sms_configured(settings):
        reason = (
            "SMS is not configured (set TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN and "
            "TWILIO_FROM_NUMBER or TWILIO_MESSAGING_SERVICE_SID)"
        )
        return [_result(m, "skipped", reason) for m in messages]

    url = (
        f"{settings.twilio_api_url.rstrip('/')}/2010-04-01/Accounts/"
        f"{settings.twilio_account_sid}/Messages.json"
    )
    auth = (settings.twilio_account_sid, settings.twilio_auth_token)
    sender = (
        {"MessagingServiceSid": settings.twilio_messaging_service_sid}
        if settings.twilio_messaging_service_sid
        else {"From": settings.twilio_from_number}
    )
    limit = asyncio.Semaphore(CONCURRENT_SENDS)

    async def one(message: OutgoingSms) -> DeliveryResult:
        body = settings.twilio_body_template or message.body
        data = {"To": message.to, "Body": body, **sender}
        last_error = ""
        async with limit:
            for attempt in range(RETRIES):
                try:
                    response = await client.post(url, data=data, auth=auth, timeout=TIMEOUT)
                except httpx.HTTPError as exc:
                    last_error = f"Twilio unreachable: {exc!r}"
                else:
                    if response.is_success:
                        return _result(message, "sent")
                    last_error = _twilio_error(response)
                    if response.status_code != 429 and response.status_code < 500:
                        break  # e.g. invalid or unverified number: retrying won't help
                if attempt < RETRIES - 1:
                    await asyncio.sleep(2**attempt)
        return _result(message, "failed", last_error)

    return list(await asyncio.gather(*(one(m) for m in messages)))


def _twilio_error(response: httpx.Response) -> str:
    try:
        data = response.json()
        return f"Twilio error {data.get('code')}: {data.get('message')}"
    except ValueError:
        return f"Twilio returned HTTP {response.status_code}"


def _result(message: OutgoingSms, status: str, error: str | None = None) -> DeliveryResult:
    return DeliveryResult(message.to, status, error)
