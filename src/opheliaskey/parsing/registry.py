"""Parse stage: raw_documents -> orders / line_items / transactions.

Idempotent and re-runnable. `okey parse --reparse` wipes derived rows and
rebuilds everything from the raw store, which is the whole point of keeping raw
documents around.
"""

from __future__ import annotations

import json
from typing import Callable

from ..db.database import Database, money, utcnow
from .email_parser import ParsedItem, ParsedOrder, parse_email, unparsed_reason
from .vendors_util import resolve_vendor


def persist_order(db: Database, source: str, parsed: ParsedOrder, raw_id: int) -> int:
    vendor_id = resolve_vendor(db, name=parsed.vendor_name, domain=parsed.vendor_domain)
    now = utcnow()

    existing = db.one(
        "SELECT id FROM orders WHERE source=? AND external_order_id=?",
        (source, parsed.external_order_id),
    )
    if existing:
        order_id = existing["id"]
        db.execute(
            """UPDATE orders SET vendor_id=COALESCE(?, vendor_id),
                 ordered_at=COALESCE(?, ordered_at), status=?,
                 subtotal_cents=COALESCE(?, subtotal_cents),
                 tax_cents=COALESCE(?, tax_cents),
                 shipping_cents=COALESCE(?, shipping_cents),
                 total_cents=?, currency=?, raw_document_id=?, updated_at=?
               WHERE id=?""",
            (vendor_id, parsed.ordered_at, parsed.status, parsed.subtotal_cents,
             parsed.tax_cents, parsed.shipping_cents, parsed.total_cents or 0,
             parsed.currency, raw_id, now, order_id),
        )
    else:
        cur = db.execute(
            """INSERT INTO orders
                 (source, external_order_id, vendor_id, ordered_at, status,
                  subtotal_cents, tax_cents, shipping_cents, total_cents, currency,
                  raw_document_id, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (source, parsed.external_order_id, vendor_id, parsed.ordered_at, parsed.status,
             parsed.subtotal_cents, parsed.tax_cents, parsed.shipping_cents,
             parsed.total_cents or 0, parsed.currency, raw_id, now, now),
        )
        order_id = int(cur.lastrowid)

    for index, item in enumerate(parsed.items):
        db.execute(
            """INSERT INTO line_items
                 (order_id, line_no, description, sku, asin, url, quantity,
                  unit_price_cents, total_cents)
               VALUES (?,?,?,?,?,?,?,?,?)
               ON CONFLICT(order_id, line_no) DO UPDATE SET
                 description=excluded.description, sku=excluded.sku, asin=excluded.asin,
                 url=excluded.url, quantity=excluded.quantity,
                 unit_price_cents=excluded.unit_price_cents,
                 total_cents=excluded.total_cents""",
            (order_id, index, item.description, item.sku, item.asin, item.url,
             item.quantity, item.unit_price_cents, item.total_cents),
        )
    return order_id


def parse_gmail_row(db: Database, row) -> str | None:
    raw = db.load_raw(row["id"])
    orders = parse_email(raw)
    if not orders:
        return unparsed_reason(raw)
    for parsed in orders:
        persist_order(db, "gmail", parsed, row["id"])
    return None


# The Amazon Business reconciliation payload is documented loosely, and field
# names vary by account configuration. Rather than hard-code one spelling, try
# the plausible ones and record what we actually saw when none match — a
# debuggable failure beats a silently empty table.
_AMZ_ORDER_ID = ("orderId", "order_id", "amazonOrderId", "purchaseOrderNumber")
_AMZ_DATE = ("orderDate", "transactionDate", "order_date", "invoiceDate")
_AMZ_TOTAL = ("totalAmount", "transactionAmount", "amount", "grandTotal")
_AMZ_LINES = ("lineItems", "orderLineItems", "line_items", "items")


def _first(payload: dict, keys: tuple[str, ...]):
    for key in keys:
        if key in payload and payload[key] not in (None, ""):
            return payload[key]
    return None


def _amount(node) -> int | None:
    """Amazon returns money either bare or as {amount, currencyCode}."""
    if isinstance(node, dict):
        return money(node.get("amount") or node.get("value"))
    return money(node)


def parse_amazon_row(db: Database, row) -> str | None:
    payload = json.loads(db.load_raw(row["id"]))
    order_id = _first(payload, _AMZ_ORDER_ID)
    if not order_id:
        return f"no recognizable order id; keys seen: {sorted(payload)[:15]}"

    items: list[ParsedItem] = []
    for line in _first(payload, _AMZ_LINES) or []:
        if not isinstance(line, dict):
            continue
        description = (
            line.get("productTitle") or line.get("title") or line.get("productName")
            or line.get("description")
        )
        if not description:
            continue
        try:
            quantity = float(line.get("quantity") or 1)
        except (TypeError, ValueError):
            quantity = 1.0
        unit = _amount(line.get("unitPrice") or line.get("itemPrice"))
        total = _amount(line.get("totalPrice") or line.get("itemTotal"))
        if total is None and unit is not None:
            total = int(unit * quantity)
        items.append(
            ParsedItem(
                description=str(description).strip(),
                quantity=quantity,
                unit_price_cents=unit,
                total_cents=total or 0,
                asin=line.get("asin") or line.get("ASIN"),
                sku=line.get("sku"),
            )
        )

    total_cents = _amount(_first(payload, _AMZ_TOTAL))
    if total_cents is None and items:
        total_cents = sum(i.total_cents for i in items)

    parsed = ParsedOrder(
        external_order_id=str(order_id),
        vendor_name="Amazon",
        vendor_domain="amazon.com",
        ordered_at=_first(payload, _AMZ_DATE),
        total_cents=total_cents,
        tax_cents=_amount(payload.get("taxAmount")),
        shipping_cents=_amount(payload.get("shippingAmount")),
        status=str(payload.get("orderStatus") or "unknown").lower(),
        items=items,
        method="amazon_api",
    )
    persist_order(db, "amazon_business", parsed, row["id"])
    return None


def parse_plaid_row(db: Database, row) -> str | None:
    txn = json.loads(db.load_raw(row["id"]))
    account_id = None
    if txn.get("account_id"):
        acct = db.one("SELECT id FROM accounts WHERE plaid_account_id=?", (txn["account_id"],))
        if acct is None:
            cur = db.execute(
                "INSERT INTO accounts (plaid_account_id) VALUES (?)", (txn["account_id"],)
            )
            account_id = int(cur.lastrowid)
        else:
            account_id = acct["id"]

    merchant = txn.get("merchant_name") or txn.get("name")
    vendor_id = resolve_vendor(db, name=merchant, alias_kind="card_descriptor")

    # Plaid amounts are positive for money leaving the account, matching our
    # convention, so no sign flip is needed.
    db.execute(
        """INSERT INTO transactions
             (plaid_transaction_id, account_id, posted_at, authorized_at, amount_cents,
              merchant_name, name, pending, plaid_category, currency, vendor_id,
              raw_document_id)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(plaid_transaction_id) DO UPDATE SET
             posted_at=excluded.posted_at, amount_cents=excluded.amount_cents,
             pending=excluded.pending, merchant_name=excluded.merchant_name,
             vendor_id=COALESCE(excluded.vendor_id, transactions.vendor_id),
             raw_document_id=excluded.raw_document_id""",
        (txn["transaction_id"], account_id, txn.get("date"), txn.get("authorized_date"),
         money(txn.get("amount")), txn.get("merchant_name"), txn.get("name"),
         int(bool(txn.get("pending"))),
         json.dumps(txn.get("personal_finance_category") or txn.get("category")),
         txn.get("iso_currency_code") or "USD", vendor_id, row["id"]),
    )
    return None


PARSERS: dict[str, Callable] = {
    "gmail": parse_gmail_row,
    "amazon_business": parse_amazon_row,
    "amazon_csv": parse_amazon_row,
    "plaid": parse_plaid_row,
}


def parse_pending(db: Database, source: str | None = None, limit: int = 5000) -> dict:
    stats = {"parsed": 0, "skipped": 0, "failed": 0}
    for row in db.unparsed(source, limit):
        parser = PARSERS.get(row["source"])
        if parser is None:
            db.mark_parsed(row["id"], f"no parser for source {row['source']}")
            stats["failed"] += 1
            continue
        try:
            with db.tx():
                reason = parser(db, row)
                db.mark_parsed(row["id"], reason)
            if reason:
                stats["skipped"] += 1
            else:
                stats["parsed"] += 1
        except Exception as exc:
            db.mark_parsed(row["id"], f"{type(exc).__name__}: {exc}"[:500])
            stats["failed"] += 1
    return stats


def reset_derived(db: Database) -> None:
    """Drop everything derived so the next parse rebuilds from raw."""
    with db.tx():
        for table in ("reconciliations", "item_windows", "refunds", "shipments",
                      "line_items", "orders", "transactions"):
            db.execute(f"DELETE FROM {table}")
        db.execute("UPDATE raw_documents SET parsed_at=NULL, parse_error=NULL")
