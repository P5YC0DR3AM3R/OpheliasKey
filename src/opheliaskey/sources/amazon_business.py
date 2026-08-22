"""Amazon Business API — buyer-side purchase data.

Uses the Reconciliation API, which returns line-item-level transaction data for
an Amazon Business account:

    GET /reconciliation/2021-01-08/transactions
        ?feedStartDate=&feedEndDate=&nextPageToken=

Auth is Login with Amazon (LWA) OAuth 2.0: a long-lived refresh token is
exchanged for a one-hour access token, which is sent with every call.

Two caveats, both real:

  1. Access is gated. The Amazon Business account must be enrolled in the
     developer program and the app authorized before any of this returns data.
     If that approval does not come through, `amazon_csv.py` ingests the
     "Request My Data" order export instead and the rest of the pipeline is
     unchanged.
  2. Amazon's own docs disagree on the host (`na.business-api.amazon.com` on
     the endpoints page vs `api.business.amazon.com` on the API reference) and
     on the rate limit (0.5/s vs 2/s). Both are configurable below; the default
     is the conservative reading.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone

import httpx

from ..config import get_settings
from ..db.database import Database
from .base import RateLimiter, SyncResult

LWA_TOKEN_URL = "https://api.amazon.com/auth/o2/token"

REGION_HOSTS = {
    "na": "https://na.business-api.amazon.com",
    "eu": "https://eu.business-api.amazon.com",
    "fe": "https://jp.business-api.amazon.com",
    "jp": "https://jp.business-api.amazon.com",
}

TRANSACTIONS_PATH = "/reconciliation/2021-01-08/transactions"
DEFAULT_RATE = 0.5  # requests/second — the conservative of the two documented limits


class AmazonAuthError(RuntimeError):
    pass


class LWAClient:
    """Exchanges a refresh token for access tokens, caching until expiry."""

    def __init__(self, client_id: str, client_secret: str, refresh_token: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        self._token: str | None = None
        self._expires_at: float = 0.0

    def access_token(self) -> str:
        # Refresh 60s early so a token never expires mid-request.
        if self._token and time.time() < self._expires_at - 60:
            return self._token

        response = httpx.post(
            LWA_TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": self.refresh_token,
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=30,
        )
        if response.status_code != 200:
            raise AmazonAuthError(
                f"LWA token exchange failed ({response.status_code}): {response.text[:400]}"
            )
        payload = response.json()
        self._token = payload["access_token"]
        self._expires_at = time.time() + int(payload.get("expires_in", 3600))
        return self._token


class AmazonBusinessSource:
    name = "amazon_business"

    def __init__(self, base_url: str | None = None, rate: float = DEFAULT_RATE):
        settings = get_settings()
        if not settings.amazon_configured:
            raise AmazonAuthError(
                "Amazon Business is not configured. Set OKEY_AMAZON_CLIENT_ID, "
                "OKEY_AMAZON_CLIENT_SECRET and OKEY_AMAZON_REFRESH_TOKEN in .env, "
                "or use the CSV fallback (`okey ingest amazon-csv`)."
            )
        self.lwa = LWAClient(
            settings.amazon_client_id,
            settings.amazon_client_secret,
            settings.amazon_refresh_token,
        )
        self.base_url = base_url or REGION_HOSTS.get(settings.amazon_region, REGION_HOSTS["na"])
        self.limiter = RateLimiter(rate)

    def _headers(self) -> dict[str, str]:
        token = self.lwa.access_token()
        # The endpoints doc and the API reference specify different auth headers.
        # Sending both is harmless and works against either.
        return {
            "x-amz-access-token": token,
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def fetch_transactions(self, start: datetime, end: datetime):
        """Yield raw transaction dicts, following nextPageToken to exhaustion."""
        next_token: str | None = None
        with httpx.Client(base_url=self.base_url, timeout=60) as client:
            while True:
                params: dict[str, str] = {
                    "feedStartDate": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "feedEndDate": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                }
                if next_token:
                    params["nextPageToken"] = next_token

                self.limiter.wait()
                response = client.get(TRANSACTIONS_PATH, params=params, headers=self._headers())

                if response.status_code == 429:
                    # Throttled. Back off and retry the same page.
                    time.sleep(5)
                    continue
                if response.status_code == 403:
                    raise AmazonAuthError(
                        "403 from the Reconciliation API. The usual cause is that the "
                        "Amazon Business account is not enrolled/authorized for API "
                        "access, rather than a bad token. Verify the app authorization, "
                        "or fall back to `okey ingest amazon-csv`."
                    )
                response.raise_for_status()

                body = response.json()
                for txn in body.get("transactions", []) or []:
                    yield txn

                next_token = body.get("nextPageToken")
                if not next_token:
                    return

    def sync(self, db: Database, *, full: bool = False) -> SyncResult:
        settings = get_settings()
        result = SyncResult(source=self.name)

        end = datetime.now(timezone.utc)
        if full:
            start = datetime.fromisoformat(settings.gmail_since).replace(tzinfo=timezone.utc)
        else:
            cursor = db.get_cursor(self.name)
            if cursor:
                # Re-scan a 7-day overlap: Amazon backfills and amends
                # transactions, so a strict high-water mark loses corrections.
                start = datetime.fromisoformat(cursor) - timedelta(days=7)
            else:
                start = datetime.fromisoformat(settings.gmail_since).replace(tzinfo=timezone.utc)

        try:
            for txn in self.fetch_transactions(start, end):
                result.fetched += 1
                external_id = str(
                    txn.get("transactionId")
                    or txn.get("orderId")
                    or f"unknown-{result.fetched}"
                )
                occurred = txn.get("transactionDate") or txn.get("orderDate")
                _, is_new = db.store_raw(
                    self.name,
                    external_id,
                    json.dumps(txn, sort_keys=True).encode(),
                    occurred_at=occurred,
                )
                result.new += int(is_new)
                result.skipped += int(not is_new)
        except Exception as exc:
            result.errors.append(str(exc))
            db.set_sync_state(self.name, db.get_cursor(self.name), "error", str(exc)[:500])
            raise

        result.cursor = end.isoformat()
        db.set_sync_state(self.name, result.cursor, "ok", result.summary())
        return result
