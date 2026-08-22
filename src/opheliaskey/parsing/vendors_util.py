"""Vendor identity resolution.

The same merchant shows up as 'orders@westmarine.com' in email, 'West Marine'
in an Amazon-style payload, and 'WESTMARINE #0231 WATSONVILLE CA' on a card
statement. All three must collapse to one vendor row or every per-vendor total
is wrong.
"""

from __future__ import annotations

import re

from ..db.database import Database

# Domain -> (canonical name, kind)
KNOWN_VENDORS: dict[str, tuple[str, str]] = {
    "amazon.com": ("Amazon", "general"),
    "defender.com": ("Defender Industries", "marine"),
    "westmarine.com": ("West Marine", "marine"),
    "fisheriessupply.com": ("Fisheries Supply", "marine"),
    "jamestowndistributors.com": ("Jamestown Distributors", "marine"),
    "hodgesmarine.com": ("Hodges Marine", "marine"),
    "homedepot.com": ("Home Depot", "hardware"),
    "lowes.com": ("Lowe's", "hardware"),
    "harborfreight.com": ("Harbor Freight", "hardware"),
    "mcmaster.com": ("McMaster-Carr", "hardware"),
    "grainger.com": ("Grainger", "hardware"),
    "signsbytomorrow.com": ("Signs By Tomorrow", "service"),
}

_NOISE = re.compile(
    r"\b(inc|llc|ltd|co|corp|company|the|store|shop|online|payments?|purchase|"
    r"mktp|mkt|bill|us|usa)\b",
    re.I,
)
_NON_ALNUM = re.compile(r"[^a-z0-9 ]+")
_MIXED_TOKEN = re.compile(r"^(?=.*[a-z])(?=.*\d)[a-z0-9]+$")

# Card descriptors are noisy and location-specific ('WESTMARINE #0231
# WATSONVILLE CA'). Matching a known merchant pattern first is far more robust
# than trying to normalize every descriptor into the same shape.
MERCHANT_PATTERNS: list[tuple[str, str]] = [
    (r"amzn|amazon", "amazon.com"),
    (r"west\s*marine", "westmarine.com"),
    (r"defender", "defender.com"),
    (r"fisheries\s*supply", "fisheriessupply.com"),
    (r"jamestown", "jamestowndistributors.com"),
    (r"hodges\s*marine", "hodgesmarine.com"),
    (r"home\s*depot", "homedepot.com"),
    (r"lowe'?s", "lowes.com"),
    (r"harbor\s*freight", "harborfreight.com"),
    (r"mcmaster", "mcmaster.com"),
    (r"grainger", "grainger.com"),
    (r"signs\s*by\s*tomorrow", "signsbytomorrow.com"),
]
_COMPILED_MERCHANTS = [(re.compile(p, re.I), domain) for p, domain in MERCHANT_PATTERNS]

# Two tokens is enough to identify a merchant while dropping the trailing city.
MAX_KEY_TOKENS = 2


def match_known_merchant(text: str) -> str | None:
    """Return the canonical domain for a descriptor matching a known merchant."""
    for pattern, domain in _COMPILED_MERCHANTS:
        if pattern.search(text):
            return domain
    return None


def normalize_descriptor(text: str) -> str:
    """Reduce a card descriptor to a matchable key.

    'WESTMARINE #0231 WATSONVILLE CA 04/12' -> 'westmarine'
    """
    lowered = text.lower()
    lowered = re.sub(r"\d{2}/\d{2}", " ", lowered)      # trailing dates
    lowered = re.sub(r"#?\d{3,}", " ", lowered)          # store/order numbers
    lowered = _NON_ALNUM.sub(" ", lowered)
    lowered = _NOISE.sub(" ", lowered)

    tokens = [t for t in lowered.split() if t]
    # Drop opaque alphanumeric reference tokens like '2k4tr9'.
    tokens = [t for t in tokens if not _MIXED_TOKEN.match(t)]
    # Drop a trailing 2-letter state code.
    if len(tokens) > 1 and len(tokens[-1]) == 2:
        tokens = tokens[:-1]
    return " ".join(tokens[:MAX_KEY_TOKENS]).strip()


def resolve_vendor(
    db: Database, *, name: str | None = None, domain: str | None = None, alias_kind: str = "display_name"
) -> int | None:
    """Find or create a vendor, recording the alias that led us to it."""
    if domain:
        domain = domain.lower().removeprefix("www.")
        # Reduce 'shipment.amazon.com' or 'email.westmarine.com' to the root.
        for known in KNOWN_VENDORS:
            if domain == known or domain.endswith("." + known):
                domain = known
                break

    lookup_alias = (domain or name or "").strip()
    if not lookup_alias:
        return None

    # A descriptor that matches a known merchant is promoted to that merchant's
    # domain, so 'WESTMARINE #0231 ...' and 'orders@westmarine.com' collapse to
    # one vendor row instead of two.
    if not domain:
        matched = match_known_merchant(lookup_alias)
        if matched:
            domain = matched

    kind = "email_domain" if domain else alias_kind
    key = lookup_alias.lower() if domain else normalize_descriptor(lookup_alias)
    if not key:
        return None

    row = db.one(
        "SELECT vendor_id FROM vendor_aliases WHERE alias=? AND alias_kind=?", (key, kind)
    )
    if row:
        return row["vendor_id"]

    if domain and domain in KNOWN_VENDORS:
        canonical, vkind = KNOWN_VENDORS[domain]
    elif name:
        canonical, vkind = name.strip(), "general"
    else:
        canonical, vkind = domain, "general"

    existing = db.one("SELECT id FROM vendors WHERE canonical_name=?", (canonical,))
    if existing:
        vendor_id = existing["id"]
    else:
        cur = db.execute(
            "INSERT INTO vendors (canonical_name, domain, kind) VALUES (?, ?, ?)",
            (canonical, domain, vkind),
        )
        vendor_id = int(cur.lastrowid)

    db.execute(
        "INSERT OR IGNORE INTO vendor_aliases (vendor_id, alias, alias_kind) VALUES (?, ?, ?)",
        (vendor_id, key, kind),
    )
    return vendor_id
