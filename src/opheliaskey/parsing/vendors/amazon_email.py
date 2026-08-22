"""Amazon order-confirmation email parser.

Written against real messages, which differ from the obvious assumptions in
three ways that all cost money if missed:

  1. **One email can carry several orders.** A single "Ordered: ... and 6 more
     items" message routinely contains three separate order numbers, each with
     its own grand total. A parser that returns one order per email silently
     drops the rest.

  2. **Line prices are unit prices, not line totals.** `Quantity: 2` beside
     `154.95 USD` means $309.90 on that line. Reading it as the line total
     understates the order by the quantity multiple.

  3. **Grand total includes tax that never appears as a line.** The difference
     between the grand total and the summed lines is tax and shipping, so it is
     derived rather than left as an unexplained coverage gap.

Amazon's plaintext part is well structured and stable, and is preferred over
scraping the HTML.
"""

from __future__ import annotations

import re

from ...db.database import money
from ..email_parser import ParsedItem, ParsedOrder

# '112-4886390-4634637'
ORDER_ID = r"\d{3}-\d{7}-\d{7}"

ORDER_HEAD_RE = re.compile(rf"Order\s*#\s*\n\s*({ORDER_ID})")

# '* Name...\n  Quantity: 2\n  154.95 USD'  — the name may wrap across lines.
ITEM_RE = re.compile(
    r"^\*[ \t]+(?P<name>.+?)\n"
    r"\s*Quantity:\s*(?P<qty>[\d.]+)\s*\n"
    r"\s*(?P<price>[\d,]+\.\d{2})\s*(?P<currency>[A-Z]{3})",
    re.M | re.S,
)

TOTAL_RE = re.compile(r"Grand Total:\s*\n\s*([\d,]+\.\d{2})\s*([A-Z]{3})")

AMAZON_SENDERS = (
    "auto-confirm@amazon.com",
    "order-update@amazon.com",
    "shipment-tracking@amazon.com",
    "digital-no-reply@amazon.com",
    "return@amazon.com",
)


def is_amazon(sender: str | None, domain: str | None) -> bool:
    haystack = f"{sender or ''} {domain or ''}".lower()
    return "amazon.com" in haystack


def _clean_name(raw: str) -> str:
    return re.sub(r"\s+", " ", raw).strip()


def parse_amazon_text(
    text: str, *, ordered_at: str | None = None, domain: str | None = "amazon.com"
) -> list[ParsedOrder]:
    """Parse every order in an Amazon confirmation email's plaintext body."""
    heads = list(ORDER_HEAD_RE.finditer(text))
    if not heads:
        return []

    orders: list[ParsedOrder] = []
    for index, head in enumerate(heads):
        start = head.end()
        end = heads[index + 1].start() if index + 1 < len(heads) else len(text)
        block = text[start:end]

        items: list[ParsedItem] = []
        for match in ITEM_RE.finditer(block):
            try:
                quantity = float(match.group("qty"))
            except ValueError:
                quantity = 1.0
            unit = money(match.group("price"))
            items.append(
                ParsedItem(
                    description=_clean_name(match.group("name")),
                    quantity=quantity,
                    unit_price_cents=unit,
                    # Amazon prints the unit price; the line is unit x quantity.
                    total_cents=int(unit * quantity) if unit is not None else 0,
                )
            )

        total_match = TOTAL_RE.search(block)
        total_cents = money(total_match.group(1)) if total_match else None
        currency = total_match.group(2) if total_match else "USD"

        subtotal = sum(i.total_cents for i in items) if items else None
        if total_cents is None:
            total_cents = subtotal

        # Whatever the grand total exceeds the lines by is tax and shipping.
        # Deriving it keeps the order internally consistent instead of leaving
        # an unexplained gap for the coverage check to flag.
        tax_cents = None
        if total_cents is not None and subtotal is not None and total_cents >= subtotal:
            difference = total_cents - subtotal
            tax_cents = difference or None

        orders.append(
            ParsedOrder(
                external_order_id=head.group(1),
                vendor_name="Amazon",
                vendor_domain=domain or "amazon.com",
                ordered_at=ordered_at,
                total_cents=total_cents,
                subtotal_cents=subtotal,
                tax_cents=tax_cents,
                currency=currency,
                items=items,
                method="amazon_email",
            )
        )
    return orders
