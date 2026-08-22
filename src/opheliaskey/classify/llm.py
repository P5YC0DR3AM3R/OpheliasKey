"""LLM classification pass.

Handles what the rules deliberately refuse: items whose relevance or system is
genuinely ambiguous from keywords alone. A 'TP-Link 8-port PoE switch' is an
ordinary household purchase in the abstract; against a vessel running six 4K
PoE cameras it is obviously part of the camera system. That context is the
whole reason this pass exists, so the vessel spec goes into the system prompt.

The system prompt is stable across batches and marked for prompt caching, so
only the item list is billed at full rate after the first request.
"""

from __future__ import annotations

import os
from typing import Literal

from pydantic import BaseModel, Field

from ..db.database import Database, utcnow
from .taxonomy import system_catalog, vessel_context

MODEL = "claude-opus-5"
BATCH_SIZE = 25
MAX_TOKENS = 8000
DEFAULT_EFFORT = "medium"

# Below this, the verdict is recorded but the item still goes to human review.
AUTO_ACCEPT = 0.75


class ItemVerdict(BaseModel):
    index: int = Field(description="The item's index from the input list.")
    relevance: Literal["boat", "personal", "ambiguous"] = Field(
        description=(
            "'boat' if the purchase is plausibly for the vessel, 'personal' if it "
            "clearly is not, 'ambiguous' if it could genuinely be either."
        )
    )
    system_key: str = Field(
        description=(
            "The boat system key from the catalog, or 'unknown' when relevance is "
            "not 'boat' or the system cannot be determined."
        )
    )
    confidence: float = Field(description="0.0 to 1.0.", ge=0.0, le=1.0)
    reasoning: str = Field(description="One short sentence. Cite the deciding detail.")


class BatchVerdict(BaseModel):
    results: list[ItemVerdict]


SYSTEM_TEMPLATE = """You classify purchase line items for a specific boat project.

## The vessel

{vessel}

## Boat system catalog

{catalog}

## Your task

For each numbered item, decide two things independently:

1. **relevance** — is this purchase for the vessel?
   - `boat`: plausibly for this vessel, given its specification above.
   - `personal`: clearly a household, food, apparel, automotive or entertainment
     purchase with no plausible connection to the vessel.
   - `ambiguous`: genuinely could be either, and no detail settles it.

2. **system_key** — when relevance is `boat`, the system it belongs to. Use
   `unknown` if relevance is not `boat`, or if the item is clearly for the boat
   but you cannot tell which system.

## How to judge

Reason from the vessel specification, not from whether an item sounds nautical.
Generic hardware is frequently part of this boat: a PoE network switch fits a
vessel with six 4K cameras; a mini PC fits one running Orca Core 2; heavy gauge
cable and lugs fit a 15.36 kWh battery bank. Equally, do not force an item to be
boat-related just because it could theoretically go on a boat — a pair of
sandals is personal even though people wear sandals on boats.

Be honest about uncertainty. `ambiguous` with low confidence is a correct and
useful answer; it routes the item to a human instead of silently adding or
removing a real dollar amount from the project total. Do not guess to avoid it.
"""


def _client():
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "The `anthropic` package is required for LLM classification. "
            "Install it with: pip install anthropic"
        ) from exc
    return anthropic.Anthropic()


def _format_items(rows) -> str:
    lines = []
    for index, row in enumerate(rows):
        price = f"${(row['total_cents'] or 0) / 100:,.2f}"
        vendor = row["vendor"] or "unknown vendor"
        # Quantity is real evidence: eight identical solar panels is an array,
        # not a spare. Include it whenever it is not 1.
        qty = row["quantity"] if "quantity" in row.keys() else 1
        qty_note = f" (qty {qty:g})" if qty and qty != 1 else ""
        lines.append(f"{index}. {row['description']}{qty_note} — {price} from {vendor}")
    return "\n".join(lines)


def classify_batch(client, system_prompt: str, rows, effort: str = DEFAULT_EFFORT):
    """Classify one batch. Returns a list of ItemVerdict."""
    response = client.messages.parse(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=[
            {
                "type": "text",
                "text": system_prompt,
                # Stable across every batch — cache it rather than re-billing
                # the vessel spec and catalog on each request.
                "cache_control": {"type": "ephemeral"},
            }
        ],
        output_config={"effort": effort},
        messages=[
            {
                "role": "user",
                "content": (
                    "Classify each item.\n\n" + _format_items(rows)
                ),
            }
        ],
        output_format=BatchVerdict,
    )
    return response.parsed_output.results


def apply_llm(
    db: Database,
    *,
    limit: int = 500,
    effort: str = DEFAULT_EFFORT,
    dry_run: bool = False,
) -> dict:
    """Classify items the rules could not place.

    Only touches rows where relevance or system is still unresolved; a manual
    verdict is never overwritten.
    """
    if not os.environ.get("ANTHROPIC_API_KEY") and not os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        # The SDK also resolves `ant auth login` profiles, so an unset key is
        # not proof of missing credentials — let the SDK try before failing.
        pass

    rows = db.query(
        """SELECT li.id, li.description, li.total_cents, li.quantity,
                  v.canonical_name AS vendor
           FROM line_items li
           JOIN orders o ON o.id = li.order_id
           LEFT JOIN vendors v ON v.id = o.vendor_id
           WHERE (li.relevance IS NULL OR li.system_id IS NULL)
             AND COALESCE(li.relevance_by, '') != 'manual'
           ORDER BY li.total_cents DESC
           LIMIT ?""",
        (limit,),
    )
    stats = {
        "examined": len(rows), "boat": 0, "personal": 0, "ambiguous": 0,
        "systems_set": 0, "needs_review": 0, "batches": 0, "errors": [],
    }
    if not rows or dry_run:
        stats["dry_run"] = dry_run
        return stats

    system_prompt = SYSTEM_TEMPLATE.format(
        vessel=vessel_context(db), catalog=system_catalog(db)
    )
    client = _client()

    for start in range(0, len(rows), BATCH_SIZE):
        batch = rows[start : start + BATCH_SIZE]
        try:
            verdicts = classify_batch(client, system_prompt, batch, effort=effort)
        except Exception as exc:
            stats["errors"].append(f"batch at {start}: {type(exc).__name__}: {exc}")
            continue
        stats["batches"] += 1

        with db.tx():
            for verdict in verdicts:
                if not 0 <= verdict.index < len(batch):
                    continue  # model returned an index outside the batch
                row = batch[verdict.index]
                db.execute(
                    """UPDATE line_items
                       SET relevance=?, relevance_by='llm', relevance_conf=?,
                           relevance_note=?
                       WHERE id=? AND COALESCE(relevance_by,'') != 'manual'""",
                    (verdict.relevance, verdict.confidence, verdict.reasoning, row["id"]),
                )
                stats[verdict.relevance] += 1
                if verdict.confidence < AUTO_ACCEPT or verdict.relevance == "ambiguous":
                    stats["needs_review"] += 1

                if verdict.relevance == "boat" and verdict.system_key != "unknown":
                    sys_row = db.one(
                        "SELECT id FROM boat_systems WHERE key=?", (verdict.system_key,)
                    )
                    if sys_row:
                        db.execute(
                            """UPDATE line_items
                               SET system_id=?, classified_by='llm', classify_conf=?,
                                   classified_at=?
                               WHERE id=? AND COALESCE(classified_by,'') != 'manual'""",
                            (sys_row["id"], verdict.confidence, utcnow(), row["id"]),
                        )
                        stats["systems_set"] += 1
    return stats
