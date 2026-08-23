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
okey report studio         # the boat as a floating studio: power, uplink, kit, return, ROI
okey report studio --assumptions --html studio.html   # every rate, and a standalone page
okey studio baseline --subscribers 120   # paying subscribers today, from the stores — where the trajectory starts
okey report insurance --pdf out.pdf   # equipment + professional install, for an insurer
okey add invoice 2414.06 --vendor "Poseidon Marine" --system professional_install \
    --date 2026-07-31 --ref 1199 --note "Service invoice" 
okey log labor 12 --system electronics_nav --note "GO9 install"
okey log nights 14 --from 2026-07-01
okey log show --date 2026-08-22 --platform youtube --unique 140 --installs 6
okey log show --kind competition --unique 300 --attendees 55 --installs 9   # a Paradise Busker night
okey report studio --events 4 --attendees 200   # a busier month of competition nights
okey report studio --traveler-share 0.5 --traveler-paid 0.1   # a what-if on who buys
okey report studio --with-hypothetical --target 10000 --target-month 3   # count the partner artist; a different target
okey studio partner church --live-viewers 3200   # a partner channel's audience, from its analytics
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

Clear it from the CLI:

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
| Recoverable vs startup investment | What a buyer plausibly pays for, and what it took to start | Declared per-system recovery rates |
| Labor avoided | Work performed instead of purchased | **Recorded** hours — never estimated |
| Capability delivered | Days of autonomy, AC runtime, $/kWh, $/W at real output | The same spec the risk checks read |
| Use value | Cost per night aboard, and break-even against the alternative | **Recorded** nights aboard |

Two deliberate constraints:

**Labor and nights are recorded, not estimated.** Guessing install hours would
manufacture return out of nothing, so both come from `okey log`. With nothing
logged, the report says so rather than inventing a figure.

**Use value amortizes the startup-investment portion only.** The recoverable portion is not
consumed by using the boat, so charging it against nights aboard would
double-count it.

Recovery rates are heuristics with wide error bars — electronics date fastest
(20–25%), documented engine work holds best (50%), and anything consumed is 0%.
They live in one declared table with a stated basis per system, visible via
`okey report reward --assumptions`, for the same reason the specification
assumptions do: a number you cannot see is a number you cannot argue with.

## Floating studio

The boat is also a stage. Songwriters perform aboard, the set is livestreamed
over the Starlink already fitted, the audio runs through an Audient iD4 mkII,
and live lyrics from **Lyric Show** — the caption and translation app — are composited
onto the stream through its OBS browser-source overlay. The stream is the
marketing: viewers watch the overlay work and install the app — and the buyer
is the traveler, anyone across a language line who sees two-language captions
on screen and wants Conversation Mode on a phone of their own.

`okey report studio` answers four questions, separately, and states which of
them it can actually answer:

| Question | Source |
|---|---|
| Can the bank power a show, and how many? | The same `load_spec` the risk checks read — arithmetic |
| Can Starlink carry the stream? | Declared upload range against encoder bitrate, with stated headroom |
| What does the studio cost beyond what is aboard? | A priced kit list, checked against the ledger |
| What does a show plausibly return? | A **model** — declared conversion rates, replaced by recorded shows |

The return comes in three figures that are **reported separately**:
subscription revenue is recurring money, paid installs displaced is a cost not
incurred, and the catalog of recorded performances is an asset the module
does not price. Adding them would put three units in one number; a test pins
the absence of a combined figure, as it does for reward.

**Who buys.** Two segments, by need, each with its own conversion rates, plan
and churn, kept apart all the way to the steady state and summed only there.
Travelers come first: the owner has been told by every traveler met so far
that the on-screen instant translation of Conversation Mode is what they would
subscribe for, and the model carries that premise as declared, arguable
assumptions rather than a fact — 70% of any audience is there for Conversation
Mode (`traveler_share`), 4% of them install after seeing two-language captions
on screen (`traveler_viewer_to_install`) and 25% of those start the featured
Base + Conversation Mode bundle within 30 days (`traveler_install_to_paid`); the
two rates multiply to the stated 1% of viewers becoming subscribers, the
report prints that product under the funnel, and `--traveler-share 0` shows
what is left without them. The dock crowd at a competition night are travelers
too: they had the two-language demo in person, so their installs pay at the
traveler rate and land on the bundle. Performers — streamers, worship teams,
the original funnel — are the second segment, on the original rates and the
original plans.

**Realistic defaults.** The performer rates are benchmarks, each noted as such
in the assumption table: 2.5% of performer-share stream viewers install after
an explicit on-screen ask (passive click-to-install runs 1–3%), 5% of installs
start a paid plan within 30 days (freemium utility apps convert 2–5%), 12% of
dock attendees install with the QR in front of them, and 35% of paying
performers are on annual billing, priced at the annual price ÷ 12 — 60% of
travelers, who plan trips ahead; travelers churn at 6% a month between trips,
performers at 8%. Plan prices are facts, not assumptions — Base $14.99/mo ·
$99.99/yr, Ultimate $49.99/mo · $399.99/yr, Pro Broadcast $39.99/mo as an
add-on, Conversation Mode $4.99/mo · $39.99/yr and the Base + Conversation Mode
bundle $19.98/mo · $139.98/yr — verified live against
<https://lyricshow.live/pricing> on 2026-08-22 and read from the app's own
tier file, so a price change belongs in one place. On the boat alone — the
partner audience below entered as 0 — 1,200 viewers a month are 840 travelers
and 360 performers, 57 installs become 12.45 new subscribers (12 of them on
the bundle), the steady state is 205.6 subscribers — 200 travelers, 5.6
performers — at a blended ARPU of $15.41 and $2,675.16/mo net, and the kit
pays back in month 3. The declared defaults add the one committed partner and
read 21,200 viewers, 2,632.7 steady subscribers and $34,519.72/mo — a headline
that rests on an estimate, and says so.

**Reach and the target.** The boat is not the only stage the overlay is on.
Partner channels are data — one `PARTNERS` row each, committed first: the
partner church, which already has the overlay and will caption its four Sunday
live streams a month, and the partner artist, a Spanish-language artist with 6.19M
YouTube subscribers who *might* stream a live set through it. A committed
partner is counted by default; a prospective one is off until
`--with-hypothetical` counts it. An artist's viewers a stream are subscribers ×
`partner_live_share` (2%); a church's are its live viewers a stream — the partner
church's figure is not on this machine, so it is a labelled **ESTIMATE**
(5,000 a stream) until `okey studio partner church --live-viewers N`
enters the channel's own number; the headline, the Reach panel and the row all
say ESTIMATE until then. Partner viewers join the stream audience before the
traveler/performer split and convert at the same rates; each row's abroad
figure is its declared international share — declared, not measured — and the
languages line (20 base · 80 in all) is why one stream reaches every country
its audience is in. The target — 3,000 paying subscribers by month 3
(`--target`, `--target-month`) — is read against the trajectory: where the book
stands that month, when (if ever) it is reached, the shortfall, and what it
would take at the current rates — new paid a month, viewers a month, and what
that is per show at the current cadence. At the defaults it is 2,548.8 short;
with the partner artist counted, 145,000 viewers a month reach it in month 3 — on track.

**Baseline and ROI.** Today's paying subscribers live in App Store Connect,
Google Play and the Firestore entitlements, not on this machine, so they are
declared rather than read: `okey studio baseline --subscribers N` writes the
figure, `--clear` forgets it, and 0 reads as *not entered*, not zero. The
trajectory starts at the baseline, but kit payback, slip coverage, project
payback and the ROI multiples are read off the **show-driven** columns — the
baseline's own decaying remainder subtracted — because the studio cannot claim
return from subscribers it did not bring; a large baseline moves the book,
never the kit. The ROI panel prints the kit's multiple at month 12 and at the
horizon, its share of project spend, net per show and per viewer, what the kit
costs per year-one install against the $3.50 paid CPI, and the payback months.

**Recorded beats modeled.** Every funnel input states whether it is `assumed`,
`observed`, an `override`, or a `project_meta` correction. From the first show
logged, the observed viewers per set, the observed stream install rate, the
observed competition multiple and the counted dock crowd replace the assumed
ones — each drawn only from the rows that support it (a set's
install rate needs both counts on the same set; a competition night's audience
is kept out of the per-set average), and each naming how many rows that was:

```bash
okey log show --date 2026-08-22 --platform youtube --title "Set one" \
    --minutes 110 --peak 80 --unique 140 --installs 6
```

Leave a count out when nobody wrote it down — a missing number is not a zero,
and the report keeps the difference; a negative count is refused at the prompt,
and an observation outside the range an override is held to is listed as
ignored and left out of the funnel. Every conversion rate lives in one declared
table, visible with `--assumptions`, overridable per run (`--viewers`, `--shows`,
`--install-rate`, `--paid-rate`, `--churn`, `--events`, `--attendees`,
`--traveler-share`, `--traveler-paid`, `--with-hypothetical`,
`--partner-live-share`, `--target`, `--target-month`) or
permanently via `studio.<key>` in `project_meta`. The headline says which
observable inputs are observed, modeled or overridden, so it can never disagree
with the funnel's source column; the kit prints as a priced list with whatever
ledger lines match it and the inherited A/V and connectivity spend once the
ledger attributes it. The dashboard's **/studio** page shows the same report
with sliders for the same inputs, and `--html studio.html` writes it as a
standalone page that needs no server.

**Competition nights.** The boat also hosts Paradise Busker song competitions.
Acts rotate across three stages — the cockpit deck under the hardtop, the swim
platform at the waterline, and the rear dock where the crowd stands, scans the
QR and votes — and the same Lyric Show overlay that captions the stream
captions the dock screen. One show, two audiences: viewers online and attendees
on the pier, each converting at its own declared rate and kept apart all the
way to installs. The report prints the night's flow — from the blind two-version
round through the vote, the tip, proof of presence and the Codex score to the
KEY WEST, PARADISE BUSKER coin — with the steps where Lyric Show is on screen
marked, and Paradise Busker's facts cited to their sources. What the competition
itself earns is its economy, not the studio's, and none of it is added to the
return. Log a night with `okey log show --kind competition --attendees 55`; the
counted crowd replaces the assumed one, and `--events` / `--attendees` are the
per-run what-ifs.

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

The dashboard is read-only; nothing in the browser can change the ledger.
