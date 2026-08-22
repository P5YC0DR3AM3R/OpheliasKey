"""RFC-822 order-email parser.

Strategy, in priority order:

  1. schema.org JSON-LD. Many large retailers (Amazon included) embed a
     machine-readable `Order` object in confirmation emails so Google can show
     package tracking. When present it is exact — order number, per-item names,
     quantities and prices — and beats any amount of HTML scraping.
  2. Heuristic fallback. Regex for an order number and a grand total out of the
     text body. This yields an order with no line items, which is still useful
     for totals and reconciliation, and is flagged so it can be improved later.

Anything that yields neither is left unparsed with a reason recorded, never
silently dropped.
"""

from __future__ import annotations

import email
import json
import re
from dataclasses import dataclass, field
from email.header import decode_header, make_header
from email.message import Message
from typing import Any, Iterator

from ..db.database import money

JSONLD_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.I | re.S,
)
# Longest labels first, so 'Order Number: X' does not match on 'Order' and
# then capture the word 'Number'. The lookahead requires a digit somewhere in
# the token, which rules out capturing an English word under re.I.
ORDER_NO_RE = re.compile(
    r"(?:order\s+number|order\s*#|confirmation\s+number|confirmation|invoice|order)"
    r"[\s#:]*"
    r"((?=[A-Za-z0-9-]*\d)[A-Za-z0-9][A-Za-z0-9-]{4,24})",
    re.I,
)
TOTAL_RE = re.compile(
    r"(?:order\s+total|grand\s+total|total\s+charged|total)[\s:]*\$?\s*([0-9][0-9,]*\.\d{2})", re.I
)
TAG_RE = re.compile(r"<[^>]+>")


@dataclass
class ParsedItem:
    description: str
    quantity: float = 1.0
    unit_price_cents: int | None = None
    total_cents: int = 0
    sku: str | None = None
    asin: str | None = None
    url: str | None = None


@dataclass
class ParsedOrder:
    external_order_id: str
    vendor_name: str | None = None
    vendor_domain: str | None = None
    ordered_at: str | None = None
    total_cents: int | None = None
    subtotal_cents: int | None = None
    tax_cents: int | None = None
    shipping_cents: int | None = None
    currency: str = "USD"
    status: str = "unknown"
    items: list[ParsedItem] = field(default_factory=list)
    method: str = "jsonld"


# Quoted reply chains carry stale numbers from earlier in a negotiation — an
# estimate that was later revised, a total that was corrected. Scanning them
# with the heuristic regex reliably picks the wrong figure.
QUOTE_MARKERS = (
    re.compile(r"^\s*>", re.M),
    re.compile(r"^\s*On .{5,120}\bwrote:\s*$", re.M | re.I),
    re.compile(r"^\s*-+\s*Original Message\s*-+\s*$", re.M | re.I),
    re.compile(r"^\s*From:.*\n(?:\s*Sent:.*\n)?\s*To:", re.M | re.I),
)

DOCUMENT_EXTENSIONS = (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".csv")
INVOICE_WORDS = re.compile(r"\b(invoice|estimate|quote|statement|receipt|bill)\b", re.I)


def strip_quoted(text: str) -> str:
    """Cut a message body at the first quoted-reply marker."""
    earliest = len(text)
    for pattern in QUOTE_MARKERS:
        found = pattern.search(text)
        if found and found.start() < earliest:
            earliest = found.start()
    return text[:earliest]


def _attachments(msg: Message) -> list[str]:
    names = []
    for part in msg.walk():
        name = part.get_filename()
        if name:
            try:
                names.append(str(make_header(decode_header(name))))
            except Exception:
                names.append(str(name))
    return names


def unparsed_reason(raw: bytes) -> str:
    """Explain why a message yielded no order.

    An invoice whose amount lives only in a PDF attachment is not 'not an order
    email' — it is real spend the pipeline cannot price. Saying so keeps it
    visible instead of filing it under noise.
    """
    try:
        msg = email.message_from_bytes(raw)
    except Exception:
        return "unreadable message"

    subject = _header(msg, "Subject")
    documents = [n for n in _attachments(msg)
                 if n.lower().endswith(DOCUMENT_EXTENSIONS)]
    if documents and INVOICE_WORDS.search(subject or ""):
        return f"invoice amount is only in the attachment: {', '.join(documents[:3])}"
    if documents:
        return f"no amount in body; attachments present: {', '.join(documents[:3])}"
    return "not an order email"


def _header(msg: Message, name: str) -> str:
    raw = msg.get(name)
    if not raw:
        return ""
    try:
        return str(make_header(decode_header(raw)))
    except Exception:
        return str(raw)


def _bodies(msg: Message) -> tuple[str, str]:
    """Return (html, text) bodies, concatenating all matching parts."""
    html_parts: list[str] = []
    text_parts: list[str] = []
    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue
        if part.get_filename():
            continue
        try:
            payload = part.get_payload(decode=True)
            if payload is None:
                continue
            charset = part.get_content_charset() or "utf-8"
            decoded = payload.decode(charset, errors="replace")
        except Exception:
            continue
        if part.get_content_type() == "text/html":
            html_parts.append(decoded)
        elif part.get_content_type() == "text/plain":
            text_parts.append(decoded)
    return "\n".join(html_parts), "\n".join(text_parts)


def _walk_json(node: Any) -> Iterator[dict]:
    """Yield every dict in an arbitrarily nested JSON structure."""
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk_json(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk_json(value)


def _type_of(node: dict) -> set[str]:
    raw = node.get("@type") or node.get("type") or ""
    if isinstance(raw, list):
        return {str(t).lower() for t in raw}
    return {str(raw).lower()}


def extract_jsonld_orders(html: str) -> list[dict]:
    """Pull every schema.org Order object out of an HTML body."""
    orders: list[dict] = []
    for block in JSONLD_RE.findall(html):
        try:
            data = json.loads(block.strip())
        except json.JSONDecodeError:
            continue
        for node in _walk_json(data):
            types = _type_of(node)
            if "order" in types:
                orders.append(node)
            elif "parceldelivery" in types and isinstance(node.get("partOfOrder"), dict):
                orders.append(node["partOfOrder"])
    return orders


def _offer_items(order: dict) -> list[ParsedItem]:
    offers = order.get("acceptedOffer") or order.get("orderedItem") or []
    if isinstance(offers, dict):
        offers = [offers]
    items: list[ParsedItem] = []
    for offer in offers:
        if not isinstance(offer, dict):
            continue
        product = offer.get("itemOffered")
        if isinstance(product, list):
            product = product[0] if product else {}
        if not isinstance(product, dict):
            product = {}

        name = product.get("name") or offer.get("name") or product.get("description")
        if not name:
            continue

        qty_node = offer.get("eligibleQuantity") or {}
        quantity = 1.0
        if isinstance(qty_node, dict) and qty_node.get("value") is not None:
            try:
                quantity = float(qty_node["value"])
            except (TypeError, ValueError):
                quantity = 1.0

        unit = money(offer.get("price"))
        total = int(unit * quantity) if unit is not None else 0
        items.append(
            ParsedItem(
                description=str(name).strip(),
                quantity=quantity,
                unit_price_cents=unit,
                total_cents=total,
                sku=product.get("sku"),
                url=product.get("url") or offer.get("url"),
            )
        )
    return items


def parse_email(raw: bytes) -> list[ParsedOrder]:
    """Parse a raw RFC-822 message into zero or more orders.

    Returns a *list* because one email routinely carries several orders —
    Amazon bundles multiple order numbers into a single confirmation, and
    returning only the first silently drops the rest.
    """
    msg = email.message_from_bytes(raw)
    html, text = _bodies(msg)
    sender = _header(msg, "From")
    date_hdr = _header(msg, "Date")

    domain = None
    match = re.search(r"@([\w.\-]+)", sender)
    if match:
        domain = match.group(1).lower()

    ordered_at = None
    if date_hdr:
        try:
            ordered_at = email.utils.parsedate_to_datetime(date_hdr).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
        except Exception:
            ordered_at = None

    # --- path 0: vendor-specific parsers ---
    # Amazon's plaintext part is structured, stable, and — unlike the generic
    # paths — handles several orders in one message.
    from .vendors.amazon_email import is_amazon, parse_amazon_text

    if is_amazon(sender, domain):
        body_text = text or TAG_RE.sub(" ", html)
        amazon_orders = parse_amazon_text(
            body_text, ordered_at=ordered_at, domain=domain or "amazon.com"
        )
        if amazon_orders:
            return amazon_orders

    # --- path 1: structured markup ---
    found: list[ParsedOrder] = []
    for node in extract_jsonld_orders(html):
        order_no = node.get("orderNumber") or node.get("confirmationNumber")
        if not order_no:
            continue
        merchant = node.get("merchant") or node.get("seller") or {}
        merchant_name = merchant.get("name") if isinstance(merchant, dict) else None
        total = money(node.get("price")) or money(
            (node.get("priceSpecification") or {}).get("price")
            if isinstance(node.get("priceSpecification"), dict)
            else None
        )
        items = _offer_items(node)
        if total is None and items:
            total = sum(i.total_cents for i in items)
        found.append(ParsedOrder(
            external_order_id=str(order_no).strip(),
            vendor_name=merchant_name,
            vendor_domain=domain,
            ordered_at=node.get("orderDate") or ordered_at,
            total_cents=total,
            currency=node.get("priceCurrency") or "USD",
            status=str(node.get("orderStatus") or "unknown").split("/")[-1].lower(),
            items=items,
            method="jsonld",
        ))
    if found:
        return found

    # --- path 2: heuristic fallback ---
    body = strip_quoted(text or TAG_RE.sub(" ", html))
    body = re.sub(r"\s+", " ", body)
    subject = _header(msg, "Subject")
    haystack = f"{subject} {body}"

    order_match = ORDER_NO_RE.search(haystack)
    total_match = TOTAL_RE.search(haystack)
    if not (order_match and total_match):
        return []

    return [ParsedOrder(
        external_order_id=order_match.group(1).strip(),
        vendor_name=None,
        vendor_domain=domain,
        ordered_at=ordered_at,
        total_cents=money(total_match.group(1)),
        items=[],
        method="heuristic",
    )]
