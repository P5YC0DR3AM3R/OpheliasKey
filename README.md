# Ophelia's Key

Purchase intelligence for the Ophelia's Key boat project. Ingests every purchase
from Gmail, Amazon Business and the bank, separates project spend from personal
spend in a shared account, attributes each line item to a boat system, and
reports cost, budget variance and risk.

**The vessel:** a liveaboard pleasure craft on Montana permanent registration
(MT9740CA), with a substantial off-grid electrical system — a 4 kW solar array,
15.36 kWh of LiFePO4 across an isolated 24V/12V split, a 4 kW hybrid inverter and
an 8 kW backup generator, running Simrad GO9, radar, Starlink, NMEA 2000 and six
4K cameras. The system taxonomy is built for that boat, not a generic one.

## Why it is built this way

**Raw capture is separate from parsing.** Every fetched email, API response and
transaction is stored compressed and immutable in `raw_documents`. Orders and
line items are *derived*. When a vendor parser improves, `okey parse --reparse`
rebuilds everything from the raw store — the mailbox is never re-walked.

**Nothing is guessed.** A line item the classifier cannot place stays `NULL` and
is reported as unclassified spend. An order that matches two candidate bank
charges is left unreconciled. A wrong number in a cost analysis is worse than a
missing one, because a missing one announces itself.

**The project total carries its own error bar.** The account is mixed, so every
line item is gated on relevance — `boat`, `personal`, or undecided. Undecided
spend is never folded silently into either side; it is reported as its own
figure, so you always know how far the headline number could move.

**Money is integer cents everywhere.** No floats touch a dollar figure.

## Setup

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
cp .env.example .env
```

Then `okey init` to create the database and seed the boat-system taxonomy.

### Try it without credentials

```bash
okey demo && okey classify && okey report cost && okey report risk
```

Loads a realistic 22-order sample mixing boat and personal purchases, so the
relevance gate is actually exercised. The LLM pass additionally needs
`ANTHROPIC_API_KEY` (or an `ant auth login` profile). `okey demo --clear` removes it; demo rows use
the source `demo` and can never be confused with real data.

## Data sources

### Gmail — the broad net

A refit is not one vendor. Gmail catches Defender, West Marine, Fisheries
Supply, Jamestown, the yard invoice and the surveyor alongside Amazon.

Create a **Desktop app** OAuth client at
[console.cloud.google.com](https://console.cloud.google.com) with the Gmail API
enabled and the `gmail.readonly` scope, save the JSON to
`secrets/gmail_client_secret.json`, then:

```bash
okey ingest gmail --full
```

The parser prefers schema.org JSON-LD `Order` markup, which many retailers embed
for Google's package tracking. It is exact — order number, per-item names,
quantities, prices — and beats HTML scraping. Emails without it fall back to
regex for order number and total, producing an order with no itemization, which
is flagged as a coverage gap rather than passed off as complete.

### Amazon Business — the Reconciliation API

```
GET {region-host}/reconciliation/2021-01-08/transactions
    ?feedStartDate=&feedEndDate=&nextPageToken=
```

Auth is Login with Amazon: a long-lived refresh token is exchanged for a
one-hour access token. Configure `OKEY_AMAZON_CLIENT_ID`,
`OKEY_AMAZON_CLIENT_SECRET` and `OKEY_AMAZON_REFRESH_TOKEN`, then
`okey ingest amazon --full`.

> **Access is gated.** The Amazon Business account must be enrolled in the
> developer program and the app authorized before this returns any data. A `403`
> from the Reconciliation API usually means missing enrollment rather than a bad
> token. If approval does not come through, request **Your Orders** from
> Amazon's Request My Data portal and ingest the CSV instead — same schema, same
> reports, no code changes.

> Amazon's own documentation disagrees with itself on the host
> (`na.business-api.amazon.com` vs `api.business.amazon.com`) and on the rate
> limit (0.5/s vs 2/s). Both are configurable; the defaults take the
> conservative reading.

### Plaid — what actually left the account

Orders say what was bought; transactions say what was paid. The gap between them
is where the findings are: yard labor and cash purchases with no email trail,
duplicate charges, and refunds promised but never received.

Uses `/transactions/sync` with a persisted cursor. Run `okey plaid link` to
connect an institution, then `okey ingest plaid`.

## Usage

```bash
okey init                  # create database, seed taxonomy
okey ingest gmail --full   # pull order emails
okey ingest amazon --full  # pull Amazon Business data
okey ingest plaid          # pull bank transactions
okey parse                 # raw documents -> orders + line items
okey classify              # rules pass: relevance + systems
okey classify --llm        # LLM pass over what rules could not place
okey review                # clear the human review queue
okey reconcile             # match orders to bank charges
okey report cost           # spend by system, vendor, month
okey report risk           # findings, most severe first
okey report unclassified   # items awaiting attribution
okey status                # ingestion and processing state
okey serve                 # local dashboard
```

A full refresh is `okey parse --reparse`, which rebuilds every derived table
from the raw store.

## Classification

Two independent questions, answered in three passes.

**Relevance** — is this purchase for the vessel at all? **System** — which of the
26 boat systems does it belong to?

1. **Rules** (free, instant, auditable) handle what keywords settle with high
   precision. They answer only what they can answer; everything else is left
   `NULL`.
2. **LLM pass** (`okey classify --llm`) handles the rest, with the vessel
   specification in its system prompt. This is the point of the whole design: a
   TP-Link PoE switch is an ordinary household purchase in the abstract, but
   against a boat running six 4K PoE cameras it is obviously part of the camera
   system. The prompt is stable across batches and marked for prompt caching, so
   the spec and catalog are billed once.
3. **Human review** (`okey review`) clears anything still ambiguous or
   low-confidence. Manual verdicts are final — neither rules nor the LLM will
   overwrite them.

```bash
okey review                                              # see the queue
okey review --item 5 --mark boat --system solar_generation
```

## Boat systems

Twenty-six systems, built around this vessel. Power is split six ways —
`solar_generation`, `energy_storage`, `power_conversion`, `generator`,
`ac_distribution`, `dc_distribution` — because each is independently budgeted
and independently capable of overrunning. Sailing systems are absent by design.
Each system is flagged capital or consumable. See
`src/opheliaskey/classify/taxonomy.py`.

## Risk findings

| Code | Meaning |
|---|---|
| `budget_overrun` | System spend exceeds its budget line |
| `window_expiring` | A return or warranty window closes within 30 days |
| `refund_outstanding` | A refund was initiated but never completed |
| `spend_without_receipt` | A bank charge with no matching order — real spend, no itemization |
| `orders_unreconciled` | An order with no matching bank charge |
| `coverage_gap` | Order totals exceed their line items, tax and shipping |
| `unclassified` | Boat line items with no system attributed |
| `unreviewed_relevance` | Items not yet confirmed boat or personal — the total's error bar |
| `vendor_concentration` | Over half of spend with a single supplier |

## Tests

```bash
.venv/bin/python -m pytest tests/ -q
```

## Privacy

`data/`, `.env` and `secrets/` are gitignored. The database holds personal
purchase history and never leaves the machine; the dashboard binds to
`127.0.0.1` by default.
