<!-- Animated Wave Header -->
<p align="center">
  <img src="https://capsule-render.vercel.app/api?type=waving&color=0:0B1E2A,50:17C3DE,100:0B1E2A&height=220&section=header&text=Ophelia%27s%20Key&fontSize=56&fontColor=A9E8F0&animation=fadeIn&fontAlignY=35&desc=Purchase%20Intelligence%20for%20the%20Ophelia%27s%20Key%20Vessel%20Project&descSize=17&descColor=8B949E&descAlignY=55" width="100%" alt="Ophelia's Key" />
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python_3.11+-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python 3.11+" />
  <img src="https://img.shields.io/badge/Typer-1F9E89?style=for-the-badge&logo=typer&logoColor=white" alt="Typer" />
  <img src="https://img.shields.io/badge/FastAPI-009688?style=for-the-badge&logo=fastapi&logoColor=white" alt="FastAPI" />
  <img src="https://img.shields.io/badge/Pydantic_v2-E92063?style=for-the-badge&logo=pydantic&logoColor=white" alt="Pydantic v2" />
  <img src="https://img.shields.io/badge/SQLite-003B57?style=for-the-badge&logo=sqlite&logoColor=white" alt="SQLite" />
  <img src="https://img.shields.io/badge/Anthropic-191919?style=for-the-badge&logo=anthropic&logoColor=white" alt="Anthropic" />
  <img src="https://img.shields.io/badge/Google_API-EA4335?style=for-the-badge&logo=gmail&logoColor=white" alt="Google API" />
  <img src="https://img.shields.io/badge/Plaid-000000?style=for-the-badge&logo=plaid&logoColor=white" alt="Plaid" />
  <img src="https://img.shields.io/badge/Ruff-D7FF64?style=for-the-badge&logo=ruff&logoColor=black" alt="Ruff" />
</p>

<p align="center">
  <a href="https://github.com/P5YC0DR3AM3R/OpheliasKey">Repository</a>
  &nbsp;&bull;&nbsp;
  Ingest, normalize, and analyze every dollar of a liveaboard refit
</p>

---

**Ophelia's Key** (`okey`) is a command-line tool that turns the paper trail of a boat
refit into a queryable ledger. It ingests every purchase from Gmail, Amazon Business
and the bank (via Plaid), separates project spend from personal spend in a shared
account, attributes each line item to a boat system, and reports cost, budget variance
and risk.

It is built for one specific vessel — a liveaboard pleasure craft on Montana permanent
registration (**MT9740CA**) with a substantial off-grid electrical system: a 4 kW solar
array, 15.36 kWh of LiFePO4, a 4 kW hybrid inverter and an 8 kW backup generator,
running Simrad GO9, radar, Starlink, NMEA 2000 and six 4K cameras. The system taxonomy
is that boat, not a generic one.

## Below is a short description:

### What was your motivation?

A refit is a thousand purchases across a dozen vendors and one shared bank account, and
the only question that matters — *what has this actually cost?* — has no honest answer
if the numbers are guessed. The motivation was a ledger that would rather report `NULL`
than a plausible-looking wrong figure, because a missing number announces itself and a
wrong one hides.

### Why did you build this project?

Because the spreadsheet broke. A shared account mixes groceries with a $4,000 inverter;
a refit spans Gmail, Amazon, a yard invoice and a marina statement; and every "roughly"
compounds into a total nobody trusts. So the design captures the raw record — every
email, API response and transaction — compressed and immutable, and *derives* every
order and line item from it. When a vendor parser improves, `okey parse --reparse`
rebuilds history from the raw store; the mailbox is never re-walked.

### What problem does it solve?

It answers *what has this cost, and where could that number be wrong.* It gates every
line item on relevance — `boat`, `personal`, or undecided — and never folds undecided
spend silently into either side; it reports it as its own figure, an error bar on the
headline total. It reconciles orders against what actually left the bank (surfacing cash
labor, duplicate charges and refunds that never arrived), flags budget overruns and
closing return windows, and produces an insurance schedule and a resale-reward analysis
from the same data.

### What did you learn?

Storing raw capture as an immutable, compressed source of truth so that parsing is a
pure, re-runnable function of it. That schema.org JSON-LD `Order` markup embedded in
retailer emails beats HTML scraping — exact order numbers, per-item names, quantities
and prices. Integer cents everywhere, so no float ever touches a dollar. Prompt-caching
a stable vessel specification so an LLM reads a TP-Link PoE switch as camera
infrastructure rather than a household gadget. And that the load-bearing discipline is
the refusal to guess: `NULL` is a feature.

## Table of Contents

- [Getting started](#getting-started)
- [Usage](#usage)
- [Project layout](#project-layout)
- [How it works](#how-it-works)
- [Tests](#tests)
- [License](#license)
- [Contributing](#contributing)
- [Questions](#questions)

## Getting started

Python 3.11+ with a `src`-layout package and a `hatchling` build. Work inside a virtual
environment:

```bash
git clone https://github.com/P5YC0DR3AM3R/OpheliasKey.git
cd OpheliasKey
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
cp .env.example .env
```

Then create the database and seed the boat-system taxonomy:

```bash
okey init
```

### Try it without credentials

No OAuth clients, no bank link — just exercise the pipeline on a realistic 22-order
sample that mixes boat and personal purchases, so the relevance gate is actually put to
work:

```bash
okey demo && okey classify && okey report cost && okey report risk
```

The LLM classification pass additionally needs `ANTHROPIC_API_KEY` in `.env`. Demo rows
carry the source `demo` and can never be confused with real data; `okey demo --clear`
removes them.

## Usage

Everything is a subcommand of `okey`. A full refresh is `okey parse --reparse`, which
rebuilds every derived table from the raw store without re-fetching anything.

**Set up**

| Command | What it does |
| --- | --- |
| `okey init` | Create the SQLite database and seed the 30-system taxonomy |
| `okey status` | Show ingestion and processing state |

**Ingest** — pull raw records into the immutable `raw_documents` store

| Command | What it does |
| --- | --- |
| `okey ingest gmail --full` | Pull order emails (prefers JSON-LD, falls back to regex) |
| `okey amazon` | Print how to connect Amazon; show what is configured |
| `okey ingest amazon-csv` | Import the "Request My Data" order export |
| `okey ingest amazon --full` | Amazon Business Reconciliation API (needs developer approval) |
| `okey ingest plaid` | Pull bank transactions via `/transactions/sync` |

**Process** — derive and classify

| Command | What it does |
| --- | --- |
| `okey parse` | Raw documents → orders + line items (`--reparse` rebuilds all) |
| `okey classify` | Rules pass: relevance + system, high precision, no guessing |
| `okey classify --llm` | LLM pass over what the rules could not place |
| `okey review` | Clear the human review queue (final verdicts) |
| `okey reconcile` | Match orders to bank charges |

**Report**

| Command | What it does |
| --- | --- |
| `okey report cost` | Spend by system, vendor and month |
| `okey report risk` | Findings, most severe first |
| `okey report spec` | Engineering risk from the installed specification |
| `okey report reward` | What the spend actually returned, in four un-summed lenses |
| `okey report insurance --pdf out.pdf` | Equipment + professional install, for an insurer |
| `okey report unclassified` | Line items still awaiting attribution |

**Record by hand** — what no parser can recover

| Command | What it does |
| --- | --- |
| `okey add invoice <amount> --vendor … --system … --date …` | A PDF/portal invoice with no parseable body |
| `okey add commitment "<desc>" --vendor … --system … --scheduled …` | Authorized/scheduled work not yet invoiced |
| `okey log labor <hours> --system … --note …` | Record install hours — never estimated |
| `okey log nights <n> --from <date>` | Record nights aboard |

**Serve**

| Command | What it does |
| --- | --- |
| `okey serve` | Local dashboard at <http://127.0.0.1:8000>; keyboard-driven review at `/review` |

## Project layout

```
src/opheliaskey/
  cli.py            Typer entry point — the `okey` command
  config.py         Pydantic settings, read from .env
  sources/          ingestion: gmail, amazon_business, amazon_csv, plaid
  parsing/          raw documents -> orders + line items (JSON-LD + vendor parsers)
  classify/         relevance + system: rules, llm, taxonomy (26 boat systems)
  analysis/         cost, risk, spec, reward, insurance, reconcile, commitments
  db/               SQLite schema + access (integer cents throughout)
  web/              FastAPI dashboard + the keyboard-driven review UI
tests/
  test_core.py
docs/
  ARCHITECTURE.md   design notes and dashboard screenshots
data/               SQLite database, raw store, imports  (gitignored)
secrets/            OAuth client secrets and tokens       (gitignored)
.env.example        copy to .env and fill in
pyproject.toml      hatchling build, src-layout, console script `okey`
```

## How it works

Four invariants hold the whole thing together; break one and the numbers stop being
trustworthy.

### Raw capture is separate from parsing

Every fetched email, API response and bank transaction is stored compressed and
immutable in `raw_documents`. Orders and line items are *derived*. When a vendor parser
improves, `okey parse --reparse` rebuilds everything from the raw store — the mailbox is
never re-walked, and yesterday's bug does not become today's permanent record.

### Nothing is guessed

A line item the classifier cannot place stays `NULL` and is reported as unclassified
spend. An order that matches two candidate bank charges is left unreconciled. An Amazon
export price of `Not Available` stays `NULL` rather than being read as `0.00`. A wrong
number in a cost report is worse than a missing one, because the missing one is visible.

### The project total carries its own error bar

The account is mixed, so every line item is gated on relevance — `boat`, `personal`, or
undecided. Undecided spend is never folded silently into either side; it is reported as
its own figure, so you always know how far the headline number could still move.

### Money is integer cents everywhere

No float ever touches a dollar figure — not in parsing, not in analysis, not in a report.

### Classification is three passes

1. **Rules** (free, instant, auditable) settle what keywords can settle with high
   precision, and leave everything else `NULL`.
2. **The LLM pass** (`okey classify --llm`) takes the rest, with the vessel
   specification in a prompt marked for prompt caching, so the spec and catalog are
   billed once across a batch. This is the point of the design: an ordinary household
   purchase becomes obviously part of a boat system when read against *this* boat.
3. **Human review** (`okey review`, or the keyboard UI at `/review`) clears anything
   still ambiguous. Manual verdicts are final — neither the rules nor the LLM overwrite
   them.

### Data sources

- **Gmail** is the broad net — a refit is not one vendor. The parser prefers schema.org
  JSON-LD `Order` markup and falls back to regex, flagging emails with no itemization as
  a coverage gap rather than passing them off as complete.
- **Amazon** works either through the gated Business Reconciliation API or, with no
  approval needed today, the "Request My Data" CSV export, which feeds the same parser.
- **Plaid** (`/transactions/sync`, with a persisted cursor) says what actually left the
  account. The gap between what was ordered and what was paid is where the findings live.

## Tests

```bash
.venv/bin/python -m pytest tests/ -q
.venv/bin/ruff check src tests
```

The database, `.env` and `secrets/` are gitignored; the dashboard binds to `127.0.0.1`
and the review endpoints reject cross-origin and cross-site requests, so a page open in
your browser cannot POST to the local ledger.

## License

<img src="https://img.shields.io/badge/License-Proprietary-C41E3A?style=for-the-badge" alt="Proprietary" />

© 2025–2026 Phygital DevOps Inc. All rights reserved.

This repository is proprietary. It is a private tool built for the **Ophelia's Key**
vessel project, and the code, the boat-system taxonomy, the specification models and the
purchase data it operates on may not be reused, redistributed or incorporated into other
work without written permission. It holds personal purchase history and financial detail
and is not intended for public distribution. Third-party dependencies (Typer, Rich,
FastAPI, Pydantic, the Anthropic and Google API clients, Plaid access, and the rest)
remain under their own respective licenses.

## Contributing

Interested in contributing? Email and put **"Request to push to GitHub Repo"** in the
subject line and we can discuss.

Conventions that matter here:

- **The raw store is immutable.** Fetchers only ever append to `raw_documents`; every
  order and line item is derived and rebuildable with `okey parse --reparse`.
- **Never guess.** Unclassifiable stays `NULL`, an ambiguous match stays unreconciled,
  and an unknown price stays `NULL` — surfaced in a report, not filled in.
- **Money is integer cents.** No floats in a dollar path.
- **Recorded, not estimated.** Labor hours and nights aboard come from `okey log`;
  nothing manufactures return out of a guess.
- Commits use imperative mood; branch with PRs; keep `ruff` clean.

## Questions

For any questions, please reach out:

- General &mdash; <micahreadmgmt@gmail.com>
- Phygital DevOps business &mdash; <micah@lyricshow.live>

---

### Created by

Micah Read

<pre align="center"><font size="1">
 .d88888b. 8888888b. 888    8888888888888888     8888888       d8888d8b 
d88P" "Y88b888   Y88b888    888888       888       888        d8888888P 
888     888888    888888    888888       888       888       d88P8888P  
888     888888   d88P88888888888888888   888       888      d88P 888"   
888     8888888888P" 888    888888       888       888     d88P  888    
888     888888       888    888888       888       888    d88P   888    
Y88b. .d88P888       888    888888       888       888   d8888888888    
 "Y88888P" 888       888    8888888888888888888888888888d88P     888    
                                                                        
                                                                        
                                                                        
 .d8888b.    888    d8P 8888888888Y88b   d88P 
d88P  Y88b   888   d8P  888        Y88b d88P  
Y88b.        888  d8P   888         Y88o88P   
 "Y888b.     888d88K    8888888      Y888P    
    "Y88b.   8888888b   888           888     
      "888   888  Y88b  888           888     
Y88b  d88P   888   Y88b 888           888     
 "Y8888P"    888    Y88b8888888888    888     
                                              
                                              
                                              
</font></pre>

<!-- Animated Wave Footer -->
<p align="center">
  <img src="https://capsule-render.vercel.app/api?type=waving&color=0:0B1E2A,50:17C3DE,100:0B1E2A&height=120&section=footer" width="100%" alt="Footer" />
</p>
