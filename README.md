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

> **Access is gated** and approval is not guaranteed. Use the data export
> instead — it works today with no approval and feeds the same parser:
>
> ```bash
> okey amazon              # prints the steps
> okey ingest amazon-csv   # after unzipping the export into data/imports/amazon
> ```
>
> The export is one row per item. Rows sharing an Order ID are grouped back into
> orders; `Cancelled` is normalized so cancelled orders stay out of every total;
> and `Not Available` prices are kept NULL rather than read as `0.00`, then
> reported by `okey report priceless` — an item with no price is unknown, not
> free.

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
okey amazon                # how to connect Amazon; shows what is configured
okey ingest amazon-csv     # import the "Request My Data" order export
okey ingest amazon --full  # Business API (needs developer approval)
okey ingest plaid          # pull bank transactions
okey parse                 # raw documents -> orders + line items
okey classify              # rules pass: relevance + systems
okey classify --llm        # LLM pass over what rules could not place
okey review                # clear the human review queue
okey reconcile             # match orders to bank charges
okey report cost           # spend by system, vendor, month
okey report risk           # findings, most severe first
okey report spec           # engineering risk from the installed specification
okey report reward         # what the spending actually returned
okey report insurance --pdf out.pdf   # equipment + professional install, for an insurer
okey add invoice 2414.06 --vendor "Poseidon Marine" --system professional_install \
    --date 2026-07-31 --ref 1199 --note "Service invoice" 
okey log labor 12 --system electronics_nav --note "GO9 install"
okey log nights 14 --from 2026-07-01
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

The fastest way to clear it is the browser UI — `okey serve`, then
**http://127.0.0.1:8000/review**. One item at a time, entirely keyboard-driven:

| Key | Action |
|---|---|
| <kbd>B</kbd> | Mark boat, with the selected system |
| <kbd>P</kbd> | Mark personal |
| <kbd>S</kbd> | Focus the system picker |
| <kbd>O</kbd> | Apply the next decision to every unresolved item in the same order |
| <kbd>J</kbd> / <kbd>K</kbd> | Skip forward / back |
| <kbd>Z</kbd> | Undo |

Each item shows quantity, unit and line price, vendor, order date, and the
LLM's call with its reasoning. The system picker pre-selects whatever the
classifier suggested, so a correct guess is one keystroke to confirm. Undo
restores the *exact* prior state — including which pass made the original call
— so an undone decision does not leave a `manual` marker behind that would
freeze the item against re-classification.

The CLI equivalent, for scripting:

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

## Specification risk

Receipts answer what something cost. They cannot answer whether it will work.
`okey report spec` compares the vessel's installed specification against what
the hardware can actually deliver:

| Check | Question it answers |
|---|---|
| `bms_headroom` | Can the bank deliver what the inverter will ask of it? |
| `ac_startup_surge` | Will the air conditioner actually start on inverter power? |
| `solar_nameplate_gap` | What will the array really produce, versus its rating? |
| `solar_cannot_sustain_ac` | Can solar carry the primary load, or is the generator load-bearing? |
| `battery_runtime` | How long does the primary load run on the bank alone? |
| `mppt_ceiling` | Does the controller cap the array — and does that cap actually bind? |
| `string_voltage_unverified` | Does the string stay inside the MPPT's input window when hot? |
| `house_bank_charging_unspecified` | Does the isolated 12V bank have a way to charge? |
| `generator_leg_capacity` | How much of the generator's rating is usable at 120V? |

**These are estimates from stated specifications, not measurements.** Every
judgement call lives in one `ASSUMPTIONS` table, is cited by each finding that
depends on it, and is visible with `okey report spec --assumptions`. Any spec
value can be corrected without touching code by writing `spec.<key>` into
`project_meta` — an override that makes a finding disappear is the finding
working correctly, not being silenced.

A check that finds nothing wrong returns nothing. None of them fabricate
reassurance.

## Reward

The honest starting point: **refit spend does not return dollar-for-dollar at
resale, and most of it never will.** A reward module that implied otherwise
would be flattering and useless.

`okey report reward` measures what can be defended, in four separate lenses
that are deliberately **never summed** — they measure different things, and
adding them would double-count:

| Lens | What it answers | Source |
|---|---|---|
| Recoverable vs sunk | What a buyer plausibly pays for, and what is gone | Declared per-system recovery rates |
| Labor avoided | Work performed instead of purchased | **Recorded** hours — never estimated |
| Capability delivered | Days of autonomy, AC runtime, $/kWh, $/W at real output | The same spec the risk checks read |
| Use value | Cost per night aboard, and break-even against the alternative | **Recorded** nights aboard |

Two deliberate constraints:

**Labor and nights are recorded, not estimated.** Guessing install hours would
manufacture return out of nothing, so both come from `okey log`. With nothing
logged, the report says so rather than inventing a figure.

**Use value amortizes the sunk portion only.** The recoverable portion is not
consumed by using the boat, so charging it against nights aboard would
double-count it.

Recovery rates are heuristics with wide error bars — electronics date fastest
(20–25%), documented engine work holds best (50%), and anything consumed is 0%.
They live in one declared table with a stated basis per system, visible via
`okey report reward --assumptions`, for the same reason the specification
assumptions do: a number you cannot see is a number you cannot argue with.

## Insurance schedule

`okey report insurance --pdf schedule.pdf` produces a document for an
underwriter: equipment fitted to the vessel, plus the professional labor that
fitted it.

It is deliberately narrower than the cost report. Slip fees, registration,
title, insurance premiums, transport, storage, consumables and tools are
excluded — each by a named rule the PDF prints, with its amount, so the
schedule states its own boundaries rather than quietly narrowing the picture.
Any single item can be forced in or out with `--insurable` / `--not-insurable`,
and a hand-excluded item still appears in the excluded list.

**Vessel attribution matters here.** Invoices exist in the same mailbox for a
previous boat. Orders carry a `vessel` field and the schedule filters on it, so
a prior vessel's work can never land on this one's schedule.

## Recording invoices by hand

Most real marine invoices arrive as a PDF attachment or a portal link with no
amount in the email body — Shopmonkey shops (AVC Marine, Poseidon Marine) and
marina statements both work this way. No parser can recover those, so:

```bash
okey add invoice 1526.17 --vendor "Port Royale Marina" --system moorage \
    --date 2026-08-22 --ref PR-2026-08 --note "September slip"
okey systems          # list the system keys
okey report unpriced  # invoices the parser found but could not price
```

## Committed work

Every other figure in this project is backward-looking. `okey add commitment`
records work that is authorized or scheduled but not yet invoiced, which is the
difference between *what has this cost* and *what will it cost*.

```bash
okey add commitment "Exhaust hose replacement" --vendor "Poseidon Marine" \
    --system propulsion --scheduled 2026-08-25 --ref 1228
okey report commitments
okey add invoiced 1228        # close it once the invoice is recorded
```

Commitments live in their own table, so they can never leak into spend. Omit
`--estimate` when the cost is genuinely unknown: it stays NULL rather than
becoming zero, and every report states how many commitments are unpriced, so
the estimate is never mistaken for the total.

## Tests

```bash
.venv/bin/python -m pytest tests/ -q
```

## Privacy

`data/`, `.env` and `secrets/` are gitignored. The database holds personal
purchase history and never leaves the machine; the dashboard binds to
`127.0.0.1` by default.

The review endpoints mutate the ledger, and any page open in your browser can
POST to localhost — so they reject cross-origin and cross-site requests rather
than relying on the bind address alone.
