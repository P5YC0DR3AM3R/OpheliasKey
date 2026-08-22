"""Plaid — the money-movement ledger.

Orders say what was bought; transactions say what actually left the account.
The gap between them is where the interesting findings live: yard invoices and
cash purchases with no email trail, duplicate charges, and refunds that were
promised but never landed.

Uses /transactions/sync (cursor-based). The cursor is persisted in `sync_state`
and is valid for at least a year, so incremental runs stay cheap.
"""

from __future__ import annotations

import json

import httpx

from ..config import get_settings
from ..db.database import Database
from .base import SyncResult

PLAID_HOSTS = {
    "sandbox": "https://sandbox.plaid.com",
    "production": "https://production.plaid.com",
}
MAX_PAGE = 500


class PlaidError(RuntimeError):
    pass


class PlaidSource:
    name = "plaid"

    def __init__(self):
        settings = get_settings()
        if not settings.plaid_configured:
            raise PlaidError(
                "Plaid is not configured. Set OKEY_PLAID_CLIENT_ID and OKEY_PLAID_SECRET "
                "in .env, then run `okey plaid link` to obtain an access token."
            )
        if not settings.plaid_access_token:
            raise PlaidError(
                "No Plaid access token. Run `okey plaid link` to connect an institution "
                "and write OKEY_PLAID_ACCESS_TOKEN into .env."
            )
        self.base_url = PLAID_HOSTS.get(settings.plaid_env, PLAID_HOSTS["sandbox"])
        self.client_id = settings.plaid_client_id
        self.secret = settings.plaid_secret
        self.access_token = settings.plaid_access_token

    def _post(self, path: str, body: dict) -> dict:
        payload = {"client_id": self.client_id, "secret": self.secret, **body}
        response = httpx.post(f"{self.base_url}{path}", json=payload, timeout=60)
        if response.status_code >= 400:
            raise PlaidError(f"{path} failed ({response.status_code}): {response.text[:400]}")
        return response.json()

    def sync(self, db: Database, *, full: bool = False) -> SyncResult:
        result = SyncResult(source=self.name)
        cursor = None if full else db.get_cursor(self.name)

        while True:
            body: dict = {"access_token": self.access_token, "count": MAX_PAGE}
            if cursor:
                body["cursor"] = cursor
            page = self._post("/transactions/sync", body)

            # 'added' and 'modified' are both stored; store_raw versions them by
            # content hash, so a pending charge settling later is captured as a
            # new version rather than overwriting the original.
            for txn in page.get("added", []) + page.get("modified", []):
                result.fetched += 1
                _, is_new = db.store_raw(
                    self.name,
                    txn["transaction_id"],
                    json.dumps(txn, sort_keys=True).encode(),
                    occurred_at=txn.get("date"),
                )
                result.new += int(is_new)
                result.skipped += int(not is_new)

            for removed in page.get("removed", []):
                # Plaid removes transactions that were never really posted.
                db.execute(
                    "DELETE FROM transactions WHERE plaid_transaction_id=?",
                    (removed["transaction_id"],),
                )

            cursor = page.get("next_cursor")
            if not page.get("has_more"):
                break

        result.cursor = cursor
        db.set_sync_state(self.name, cursor, "ok", result.summary())
        return result

    def create_link_token(self) -> str:
        """Step 1 of connecting an institution. The returned token drives Plaid
        Link in a browser; Link hands back a public_token to exchange below."""
        body = {
            "user": {"client_user_id": "opheliaskey"},
            "client_name": "Ophelia's Key",
            "products": ["transactions"],
            "country_codes": ["US"],
            "language": "en",
        }
        return self._post("/link/token/create", body)["link_token"]

    def exchange_public_token(self, public_token: str) -> str:
        return self._post("/item/public_token/exchange", {"public_token": public_token})[
            "access_token"
        ]
