"""Create the single Strava webhook subscription for this application."""

from __future__ import annotations

import asyncio
from urllib.parse import urljoin

import httpx

from app.config import get_settings

SUBSCRIPTIONS_URL = "https://www.strava.com/api/v3/push_subscriptions"


async def create_subscription() -> None:
    """Create a subscription without printing credentials."""

    settings = get_settings()
    if not settings.strava_client_id or not settings.strava_client_secret:
        raise SystemExit("STRAVA_CLIENT_ID and STRAVA_CLIENT_SECRET are required.")
    if not settings.strava_webhook_verify_token:
        raise SystemExit("STRAVA_WEBHOOK_VERIFY_TOKEN is required.")
    if not settings.public_base_url.startswith("https://"):
        raise SystemExit("PUBLIC_BASE_URL must be a publicly reachable HTTPS URL.")

    callback_url = urljoin(
        settings.public_base_url.rstrip("/") + "/",
        "integrations/strava/webhook",
    )
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(
            SUBSCRIPTIONS_URL,
            data={
                "client_id": settings.strava_client_id,
                "client_secret": (settings.strava_client_secret.get_secret_value()),
                "callback_url": callback_url,
                "verify_token": (
                    settings.strava_webhook_verify_token.get_secret_value()
                ),
            },
        )

    if response.status_code not in {200, 201}:
        raise SystemExit(
            f"Strava rejected the subscription request (HTTP {response.status_code})."
        )
    payload = response.json()
    print(f"Strava webhook subscription created with ID {payload.get('id')}.")


if __name__ == "__main__":
    asyncio.run(create_subscription())
