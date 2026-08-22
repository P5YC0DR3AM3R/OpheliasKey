"""Gmail ingestion — the broad net.

Amazon is one vendor; a refit is many. Gmail is what catches Defender, West
Marine, Fisheries Supply, Jamestown, the yard invoice and the surveyor. Full
RFC-822 messages are stored raw and compressed so vendor parsers can be added
or fixed later without ever re-walking the mailbox.
"""

from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..config import get_settings
from ..db.database import Database
from .base import SyncResult

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

# Gmail's own classifier is a good prefilter, but it misses plenty, so we OR it
# together with explicit vendor domains and receipt-shaped subject lines.
DEFAULT_QUERY = " OR ".join(
    [
        "category:purchases",
        "from:amazon.com",
        "from:defender.com",
        "from:westmarine.com",
        "from:fisheriessupply.com",
        "from:jamestowndistributors.com",
        "from:homedepot.com",
        "from:lowes.com",
        "from:harborfreight.com",
        'subject:"order confirmation"',
        'subject:"your order"',
        'subject:"order #"',
        'subject:"invoice"',
        'subject:"receipt"',
        'subject:"has shipped"',
        'subject:"proof of purchase"',
    ]
)


def build_service(credentials_path: Path | None = None, token_path: Path | None = None):
    """Build an authorized Gmail client, running the consent flow on first use."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    settings = get_settings()
    credentials_path = Path(credentials_path or settings.gmail_client_secret_file)
    token_path = Path(token_path or settings.gmail_token_file)

    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not credentials_path.exists():
                raise FileNotFoundError(
                    f"Gmail OAuth client secret not found at {credentials_path}. "
                    "Create a Desktop-app OAuth client in Google Cloud Console with the "
                    "Gmail API enabled, download the JSON, and save it there."
                )
            flow = InstalledAppFlow.from_client_secrets_file(str(credentials_path), SCOPES)
            creds = flow.run_local_server(port=0)
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(creds.to_json())

    return build("gmail", "v1", credentials=creds, cache_discovery=False)


class GmailSource:
    name = "gmail"

    def __init__(self, query: str | None = None):
        self.query = query or DEFAULT_QUERY
        self._service = None

    @property
    def service(self):
        if self._service is None:
            self._service = build_service()
        return self._service

    def _build_query(self, db: Database, full: bool) -> str:
        settings = get_settings()
        if full:
            since = settings.gmail_since
        else:
            cursor = db.get_cursor(self.name)
            if cursor:
                # Overlap by a day; Gmail date filters are day-granular and
                # timezone-fuzzy, so a hard boundary can drop messages.
                since_dt = datetime.fromisoformat(cursor) - timedelta(days=1)
                since = since_dt.strftime("%Y/%m/%d")
            else:
                since = settings.gmail_since
        since = since.replace("-", "/")
        return f"({self.query}) after:{since}"

    def sync(self, db: Database, *, full: bool = False) -> SyncResult:
        result = SyncResult(source=self.name)
        query = self._build_query(db, full)
        messages = self.service.users().messages()

        page_token = None
        ids: list[str] = []
        while True:
            response = messages.list(
                userId="me", q=query, pageToken=page_token, maxResults=500
            ).execute()
            ids.extend(m["id"] for m in response.get("messages", []))
            page_token = response.get("nextPageToken")
            if not page_token:
                break

        for message_id in ids:
            try:
                # 'raw' preserves the full MIME message including HTML parts and
                # any embedded JSON-LD order markup, which the parsers rely on.
                msg = messages.get(userId="me", id=message_id, format="raw").execute()
                payload = base64.urlsafe_b64decode(msg["raw"])
                internal_ms = int(msg.get("internalDate", 0))
                occurred = (
                    datetime.fromtimestamp(internal_ms / 1000, tz=timezone.utc)
                    .strftime("%Y-%m-%dT%H:%M:%SZ")
                    if internal_ms
                    else None
                )
                result.fetched += 1
                _, is_new = db.store_raw(
                    self.name,
                    message_id,
                    payload,
                    content_type="message/rfc822",
                    occurred_at=occurred,
                )
                result.new += int(is_new)
                result.skipped += int(not is_new)
            except Exception as exc:  # one bad message must not end the run
                result.errors.append(f"{message_id}: {exc}")

        result.cursor = datetime.now(timezone.utc).isoformat()
        db.set_sync_state(self.name, result.cursor, "ok", result.summary())
        return result
