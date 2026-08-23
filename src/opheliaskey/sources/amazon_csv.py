"""Amazon order history from the 'Request My Data' export.

This is the path that works without approval. Amazon's Business API is gated on
developer-program enrollment; the privacy-portal export is available to any
account and returns the same facts — every order, every line, ASIN, unit price,
quantity, tax, shipping and status.

The export is one row per *item*, with rows sharing an Order ID. Rows are
grouped back into orders, stored in the raw document store as JSON, and then
read by the same parser the API path uses, so both sources converge on one
code path.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

from ..db.database import Database
from .base import SyncResult

# Amazon has renamed these columns more than once. Each field lists the
# spellings seen in real exports, matched case- and space-insensitively.
COLUMNS: dict[str, tuple[str, ...]] = {
    "order_id": ("order id", "orderid"),
    "order_date": ("order date", "orderdate"),
    "status": ("order status", "orderstatus"),
    "product": ("product name", "title", "item name"),
    "asin": ("asin", "asin/isbn"),
    # 'Original Quantity' is the current export's spelling.
    "quantity": ("original quantity", "quantity", "qty"),
    "unit_price": ("unit price", "purchase price per unit", "per unit price"),
    "unit_tax": ("unit price tax", "item subtotal tax"),
    # Order matters: 'Total Amount' is the true line total, after tax and
    # discounts. 'Shipment Item Subtotal' is pre-tax and understates the line,
    # so it is only a fallback.
    "total_owed": ("total amount", "total owed", "item total"),
    "subtotal": ("shipment item subtotal",),
    "tax": ("shipment item subtotal tax",),
    "discount": ("total discounts",),
    "shipping": ("shipping charge", "shipping charge total"),
    "currency": ("currency", "currency code"),
    "website": ("website",),
}

CANCELLED = {"cancelled", "canceled"}


def _normalize(name: str) -> str:
    return " ".join(name.replace("﻿", "").strip().lower().split())


def _build_index(fieldnames: list[str]) -> dict[str, str]:
    """Map our field names onto whatever this export actually calls them."""
    seen = {_normalize(f): f for f in fieldnames if f}
    index: dict[str, str] = {}
    for field, candidates in COLUMNS.items():
        for candidate in candidates:
            if candidate in seen:
                index[field] = seen[candidate]
                break
    return index


def _status(raw: str | None) -> str:
    value = (raw or "unknown").strip().lower()
    return "cancelled" if value in CANCELLED else value


def _money_text(raw: str | None) -> str | None:
    """Amazon writes 'Not Available' where a figure is missing."""
    if raw is None:
        return None
    text = raw.strip()
    if not text or text.lower() in {"not available", "not applicable", "n/a"}:
        return None
    return text


def read_orders(path: Path) -> list[dict]:
    """Group an export's item rows back into orders."""
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            return []
        index = _build_index(list(reader.fieldnames))
        if "order_id" not in index:
            raise ValueError(
                f"{path.name} has no recognizable Order ID column. "
                f"Columns found: {', '.join(reader.fieldnames[:8])}"
            )

        orders: dict[str, dict] = {}
        for row in reader:
            def get(field: str) -> str | None:
                column = index.get(field)
                return _money_text(row.get(column)) if column else None

            order_id = get("order_id")
            if not order_id:
                continue

            order = orders.setdefault(order_id, {
                "orderId": order_id,
                "orderDate": get("order_date"),
                # Two orders in this account were cancelled for failed 3D
                # Secure. Normalizing the spelling is what keeps them out of
                # every total downstream.
                "orderStatus": _status(get("status")),
                "shippingAmount": get("shipping"),
                "currency": get("currency") or "USD",
                "website": get("website"),
                "lineItems": [],
            })

            product = get("product")
            if not product:
                continue
            try:
                quantity = float(get("quantity") or 1)
            except ValueError:
                quantity = 1.0
            order["lineItems"].append({
                "productTitle": product,
                "asin": get("asin"),
                "quantity": quantity,
                "unitPrice": get("unit_price"),
                "totalPrice": get("total_owed"),
                "unitTax": get("unit_tax"),
                "discount": get("discount"),
                "subtotal": get("subtotal"),
            })

    # Nothing is derived here. The raw store must hold what the source said,
    # not what this module computed from it — otherwise a parser fix cannot be
    # applied by re-parsing, which is the whole reason raw documents are kept.
    # The parser sums the line totals to get the order total, and emits no
    # order-level tax because "Total Amount" per line already includes it.
    return list(orders.values())


class AmazonCsvSource:
    name = "amazon_csv"

    def __init__(self, directory: Path | str | None = None, since: str | None = None):
        from ..config import get_settings

        self.directory = Path(directory or get_settings().amazon_csv_dir)
        # The export is the full account history. Scoping keeps years of
        # unrelated purchases out of the review queue without discarding them
        # from the file — re-run with a wider window to pull them in.
        self.since = since

    def sync(self, db: Database, *, full: bool = False) -> SyncResult:
        result = SyncResult(source=self.name)
        if not self.directory.exists():
            result.errors.append(f"no such directory: {self.directory}")
            return result

        files = sorted(
            p for p in self.directory.rglob("*.csv")
            if "orderhistory" in p.name.lower().replace(".", "").replace("_", "")
            or "order" in p.name.lower()
        )
        if not files:
            result.errors.append(
                f"no order-history CSV found in {self.directory}. Look for "
                f"Retail.OrderHistory.1.csv inside the Amazon data export."
            )
            return result

        for path in files:
            try:
                orders = read_orders(path)
            except Exception as exc:
                result.errors.append(f"{path.name}: {exc}")
                continue
            for order in orders:
                if self.since and (order.get("orderDate") or "") < self.since:
                    result.skipped += 1
                    continue
                result.fetched += 1
                _, is_new = db.store_raw(
                    self.name,
                    str(order["orderId"]),
                    json.dumps(order, sort_keys=True).encode(),
                    occurred_at=order.get("orderDate"),
                )
                result.new += int(is_new)
                result.skipped += int(not is_new)

        db.set_sync_state(self.name, None, "ok", result.summary())
        return result


# --- refunds ---------------------------------------------------------------

REFUND_COLUMNS: dict[str, tuple[str, ...]] = {
    "order_id": ("order id", "orderid"),
    "amount": ("refund amount",),
    "date": ("refund date", "creation date"),
    "status": ("reversal status", "payment status"),
    "reason": ("reversal reason",),
    "quantity": ("quantity",),
}


def read_refunds(path: Path) -> list[dict]:
    """Read the refund export. Refunds are real money back and net spend is
    wrong without them."""
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            return []
        seen = {_normalize(f): f for f in reader.fieldnames if f}
        index = {}
        for field, candidates in REFUND_COLUMNS.items():
            for candidate in candidates:
                if candidate in seen:
                    index[field] = seen[candidate]
                    break
        if "order_id" not in index or "amount" not in index:
            raise ValueError(f"{path.name} is not a recognizable refund export")

        out = []
        for row in reader:
            def get(field):
                col = index.get(field)
                return _money_text(row.get(col)) if col else None
            order_id, amount = get("order_id"), get("amount")
            if not order_id or not amount:
                continue
            out.append({
                "orderId": order_id, "amount": amount, "date": get("date"),
                "status": (get("status") or "").lower(), "reason": get("reason"),
            })
        return out


def import_refunds(db: Database, directory: Path | str) -> dict:
    """Attach refunds to the orders they belong to."""
    from ..db.database import money

    directory = Path(directory)
    files = [p for p in directory.rglob("*.csv") if "refund" in p.name.lower()]
    stats = {"files": len(files), "read": 0, "linked": 0, "unmatched": 0, "amount_cents": 0}

    for path in files:
        try:
            refunds = read_refunds(path)
        except ValueError:
            continue
        with db.tx():
            for refund in refunds:
                stats["read"] += 1
                order = db.one(
                    "SELECT id FROM orders WHERE external_order_id=?", (refund["orderId"],)
                )
                if order is None:
                    # A refund for an order outside the ingested range. Counting
                    # it would credit spend that was never recorded.
                    stats["unmatched"] += 1
                    continue
                cents = money(refund["amount"])
                if cents is None:
                    continue
                existing = db.one(
                    """SELECT id FROM refunds WHERE order_id=? AND amount_cents=?
                       AND COALESCE(occurred_at,'')=?""",
                    (order["id"], cents, refund["date"] or ""),
                )
                if existing:
                    continue
                db.execute(
                    """INSERT INTO refunds (order_id, kind, amount_cents, occurred_at,
                         status, reason) VALUES (?, 'refund', ?, ?, ?, ?)""",
                    (order["id"], cents, refund["date"],
                     "completed" if refund["status"] == "completed" else refund["status"],
                     refund["reason"]),
                )
                stats["linked"] += 1
                stats["amount_cents"] += cents
    return stats
