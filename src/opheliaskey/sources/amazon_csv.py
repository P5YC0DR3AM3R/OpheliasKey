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
    "quantity": ("quantity", "qty"),
    "unit_price": ("unit price", "purchase price per unit", "per unit price"),
    "unit_tax": ("unit price tax", "item subtotal tax"),
    "total_owed": ("total owed", "item total", "shipment item subtotal"),
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
            })

    # The export has no order-total column, so the order total is the sum of
    # its rows. Left unset when no row carried a figure, rather than zeroed.
    from ..db.database import money

    for order in orders.values():
        totals = [money(i["totalPrice"]) for i in order["lineItems"]]
        totals = [t for t in totals if t is not None]
        if totals:
            order["totalAmount"] = sum(totals) / 100
        taxes = [money(i.get("unitTax")) for i in order["lineItems"]]
        taxes = [t for t in taxes if t is not None]
        if taxes:
            order["taxAmount"] = sum(taxes) / 100
    return list(orders.values())


class AmazonCsvSource:
    name = "amazon_csv"

    def __init__(self, directory: Path | str | None = None):
        from ..config import get_settings

        self.directory = Path(directory or get_settings().amazon_csv_dir)

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
