# Architecture

## Pipeline

```
  Gmail API ─┐
Amazon B. API ├─► raw_documents ──► parse ──► orders ──► classify ──► reports
  Plaid API ─┘   (immutable,        stage    line_items    (systems)   cost
                  compressed)                                          risk
                                                    │
                              transactions ─────────┴──► reconcile
```

Four stages, each independently re-runnable:

1. **Ingest** — sources fetch bytes and call `store_raw`. They never write to
   `orders` or `line_items`.
2. **Parse** — raw documents become orders, line items and transactions.
3. **Classify** — two independent questions per line item: is it project spend
   (relevance), and which system does it belong to. Three passes: rules, then
   LLM with vessel context, then human review.
4. **Analyze** — cost, risk and reconciliation reports read the normalized
   tables.

## Why raw capture is separate

Email parsers are never finished. A new vendor, a changed template, or a bug
found six months in would otherwise mean re-walking the mailbox — slow, rate
limited, and impossible for data since deleted. Because the raw bytes are kept,
`okey parse --reparse` rebuilds every derived row from scratch in seconds.

`raw_documents` is keyed on `(source, external_id, content_hash)`. Re-fetching an
unchanged document is a no-op; a *changed* one is stored as a new version beside
the old. Nothing is ever overwritten, so an order that shipped, a transaction
that settled, and a price that was adjusted are all recoverable.

## Refusal as a design principle

Three places deliberately decline rather than guess:

- **Classification.** Below the confidence floor, `system_id` stays `NULL` and
  the amount appears under `unclassified` in the risk report.
- **Relevance.** When keywords indicate both boat and personal, or neither, the
  rules return nothing rather than picking a side. The item defers to the LLM,
  and from there to a human. Undecided spend is reported as its own figure, so
  the project total always states how far it could move.
- **Reconciliation.** Two candidate charges of the same amount in the same week
  produce no link. A wrong link would corrupt both the unreconciled-orders and
  spend-without-receipt signals simultaneously.
- **Money parsing.** `money()` returns `None` for anything it cannot parse
  exactly, rather than coercing to zero.

In each case the unresolved amount is reported as a number, so the gap is
visible rather than absorbed.

## Vendor identity

One merchant appears as `orders@westmarine.com` in email, `West Marine` in an
API payload, and `WESTMARINE #0231 WATSONVILLE CA` on a card statement. Without
collapsing these, every per-vendor total fragments across store locations.

`vendor_aliases` maps each observed form to one vendor row. Card descriptors are
first matched against known-merchant patterns, and only fall back to a
normalized two-token key when the merchant is unrecognized.

## Money

Integer cents throughout — schema, parsing, analysis. `money()` is the single
coercion point and `fmt_money()` the single rendering point. No float ever
touches a dollar figure.

## Schema layers

| Layer | Tables | Rebuildable |
|---|---|---|
| Raw capture | `raw_documents` | No — this is the source of truth |
| Commerce | `orders`, `line_items`, `shipments`, `refunds`, `item_windows` | Yes |
| Money | `accounts`, `transactions`, `reconciliations` | Yes |
| Planning | `boat_systems`, `budget_lines`, `project_meta` | Seeded / user-entered |
| Bookkeeping | `sync_state`, `vendors`, `vendor_aliases` | Partly |

## Two kinds of risk

`risk_report` returns two separate lists, deliberately not merged:

- **Purchase risk** comes from receipts — budget overruns, unreconciled
  charges, refunds that never landed, spend not yet reviewed.
- **Specification risk** comes from the installed spec — whether the BMS can
  feed the inverter, whether the array can carry the load.

They answer different questions from different data sources. Blending them
would let "your compressor may not start" render as a spending problem, and
would put an engineering constraint in a table with a dollar column where it
does not belong.

Specification checks follow the same refusal principle as the rest of the
system: assumptions are declared in one table rather than buried in the
arithmetic, each finding names the assumptions it rests on, and a check with
nothing to report returns an empty list rather than a reassuring one. Severity
distinguishes "fails under every estimate" from "fails under the pessimistic
one" — collapsing those would either overstate certainty or hide a real risk
behind a favourable assumption.

## Human review

The queue is the gate every downstream figure depends on. It is cleared from the
CLI (`okey review`, `okey review --item N --mark boat --system …`); manual
verdicts are final, and neither the rules nor the LLM pass will overwrite them.
The web dashboard is read-only — it shows the queue but does not change the
ledger.

## Known limitations

- **Amazon Business API access is gated** and may not be granted. The CSV export
  path exists for that reason and feeds the same parser.
- **Heuristic email parsing yields totals without itemization.** These orders
  are counted in net spend but contribute nothing to system-level breakdowns —
  the `coverage_gap` finding quantifies exactly how much.
- **Rule-based classification has a ceiling.** It handles distinctive marine
  vocabulary well, but generic hardware is genuinely undecidable from keywords —
  which is what the LLM pass, with the vessel spec as context, exists to resolve.
- **The LLM pass costs money and needs credentials.** It is opt-in
  (`--llm`) rather than part of the default classify run.
- **Reward analysis is not yet implemented.** See below.
- **Specification checks are estimates, not measurements.** They flag what to
  verify; they do not replace a meter. Several explicitly ask for a number that
  is missing from the spec (panel Vmp/Voc, compressor LRA, inverter surge
  rating and duration) rather than guessing it.

## On reward analysis

Refit spend does not return dollar-for-dollar at resale, and most of it never
does. That is the premise the reward module is built on, not a caveat appended
to it.

Four lenses, returned as four separate keys and never summed into a headline
"total return". Recoverable-vs-sunk, labor avoided, capability delivered, and
use value measure genuinely different things; adding them would double-count
the same dollars under different names. A test pins the absence of a combined
figure, because that is exactly the number a future edit would be tempted to
add.

Two components are **recorded rather than estimated**. Labor hours and nights
aboard cannot be derived from receipts, and estimating them would manufacture
return out of nothing — so they come from `okey log`, and the report states
plainly when nothing has been logged instead of filling the gap with a default.

Use value amortizes the **sunk** portion only. The recoverable portion is not
consumed by using the boat; charging it against nights aboard would count it
twice, once as resale value and once as use.

Capability figures read the same `load_spec` the risk checks use, so the
capability panel and the risk findings are incapable of disagreeing about the
vessel.

## The floating studio

`analysis/studio.py` treats the vessel as a production stage: a livestreamed
set, audio through an Audient iD4 mkII, live lyrics from Lyric Show composited
through its OBS overlay, and the stream as the app's marketing. It follows the
reward module's rules rather than inventing its own.

Power and uplink are arithmetic from the installed specification — the same
`load_spec` and efficiency assumptions the risk checks and the capability lens
read, so a show's power figures cannot disagree with the rest of the project
about the same battery. The kit is a price list checked against the ledger.
The return is a *model*: a chain of conversion rates declared in one
`STUDIO_ASSUMPTIONS` table, every one of them arguable, resolved through the
same ladder as the spec (declared → `project_meta` → explicit override), with
each funnel input reporting where its value came from.

Three return lenses, returned as three keys and never summed. Subscription
revenue is recurring money; acquisition displaced is a cost not incurred; the
catalog of recorded performances is an asset nobody here can price, so it is
counted in songs and marked unpriced. A "total return" would add a revenue
stream to an avoided cost to a thing with no price — three units in one number
— and a test pins its absence, for the same reason the reward test does.

**Recorded beats modeled.** `show_log` holds what the shows actually did; from
the first row, the observed viewers per set, the observed stream install rate,
the observed competition multiple and the counted dock crowd replace the
assumed inputs — each drawn only from the rows that can support it (the rate
from sets with both counts on the same set; the per-set average without the
competition nights' multiplied audience) and each saying how many rows that
was. NULL counts stay NULL, because "no installs were attributed" and "nobody
wrote the number down" are different statements; an observation outside the
range an override is held to is reported and ignored, never modeled. Inherited
capital — Starlink, the cameras, the sound system — is reported as unpriced
until the ledger attributes spend to A/V or connectivity, rather than guessed
at.

**Attendees are a second audience, not more viewers.** A competition night puts
a crowd on the rear dock, and a dock is not a stream: the attendee stands in
front of the captions with the QR on the overlay and a phone in hand, so their
install rate is its own declared assumption and the two audiences stay apart all
the way to installs, summed only then — a blended rate would hide which audience
a number came from. What the competition itself earns is Paradise Busker's
economy: reported as the setting, never summed into the return.

**Two segments by need.** `traveler_share` of any audience is there for
Conversation Mode and the rest are performers, the original funnel; each has
its own install rate, paid rate, plan (the Base + Conversation Mode bundle
against the performer mix), annual share and churn, runs to its own steady
state and is summed only there, with plan shares derived from the volumes. The
traveler rates carry the stated 1%-of-viewers premise as declared
assumptions. The dock counts as travelers: an attendee had the two-language
demo in person, so dock installs pay at the traveler rate and land on the
bundle, and a test pins that `traveler_share` 0 leaves the performer funnel.

**Reach and the target.** Partners are rows of data in `PARTNERS`, committed
first; each names the one assumption holding its audience figure, its streams a
month and a declared international share. Committed partners are counted by
default, hypothetical ones only when `partners_include_hypothetical` is 1;
counted viewers join the stream audience before the traveler/performer split,
so `reach` and `funnel` cannot disagree. A figure the machine cannot read is a
labelled estimate (`placeholder` True on the row) until `okey studio partner`
writes `studio.partner_<key>_…` to `project_meta`. `target` reads the stated
target against the rounded trajectory — standing, reached month or None,
shortfall, and the closed-form new paid and viewers a month it would take at
the traveler churn and current yield; a target of 0 is no target.

**Payback counts show-driven revenue only.** The trajectory can start at a
declared baseline — today's paying subscribers, entered with `okey studio
baseline` because the stores and Firestore hold the real figure and 0 reads as
"not entered" — but the baseline, a mixed book nobody has split by need,
decays at the performer churn and earns the blended ARPU (stated, not hidden),
its remainder is subtracted from every row, and kit payback, slip coverage,
project payback and the ROI multiples are read off the show-driven columns.
The studio cannot claim to have paid for itself with subscribers it did not
bring: a large baseline moves the book, never the kit's return, and a test pins
that the breakeven months do not move when the baseline does.
