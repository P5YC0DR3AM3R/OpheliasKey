-- ---------------------------------------------------------------------------
-- Ophelia's Key — purchase intelligence schema
--
-- Conventions:
--   * All money is INTEGER minor units (cents). Never floats.
--   * All timestamps are ISO-8601 UTC strings ('2025-03-14T09:00:00Z').
--   * `raw_documents` is append-only and immutable; everything else is derived
--     and may be rebuilt from it by re-running the parsers.
-- ---------------------------------------------------------------------------

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- --- Layer 1: immutable raw capture ----------------------------------------

CREATE TABLE IF NOT EXISTS raw_documents (
    id            INTEGER PRIMARY KEY,
    source        TEXT    NOT NULL,          -- gmail | amazon_business | amazon_csv | plaid
    external_id   TEXT    NOT NULL,          -- message id / order id / transaction id
    content_hash  TEXT    NOT NULL,          -- sha256 of payload, for change detection
    content_type  TEXT    NOT NULL DEFAULT 'application/json',
    payload       BLOB    NOT NULL,          -- zlib-compressed original bytes
    occurred_at   TEXT,                      -- best-effort event time
    fetched_at    TEXT    NOT NULL,
    parsed_at     TEXT,                      -- NULL => awaiting parse
    parse_error   TEXT,
    UNIQUE (source, external_id, content_hash)
);
CREATE INDEX IF NOT EXISTS idx_raw_unparsed ON raw_documents (source, parsed_at)
    WHERE parsed_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_raw_occurred ON raw_documents (occurred_at);

-- --- Layer 2: normalized commerce ------------------------------------------

CREATE TABLE IF NOT EXISTS vendors (
    id             INTEGER PRIMARY KEY,
    canonical_name TEXT    NOT NULL UNIQUE,  -- 'West Marine'
    domain         TEXT,                     -- 'westmarine.com'
    kind           TEXT,                     -- marine | hardware | general | yard | service
    notes          TEXT
);

-- Alias table lets 'WESTMARINE #123 WATSONVILLE CA' (a card descriptor) and
-- 'orders@westmarine.com' (an email sender) both resolve to one vendor.
CREATE TABLE IF NOT EXISTS vendor_aliases (
    id         INTEGER PRIMARY KEY,
    vendor_id  INTEGER NOT NULL REFERENCES vendors(id) ON DELETE CASCADE,
    alias      TEXT    NOT NULL,
    alias_kind TEXT    NOT NULL,             -- email_domain | card_descriptor | display_name
    UNIQUE (alias, alias_kind)
);

CREATE TABLE IF NOT EXISTS boat_systems (
    id          INTEGER PRIMARY KEY,
    key         TEXT    NOT NULL UNIQUE,     -- 'propulsion'
    name        TEXT    NOT NULL,            -- 'Propulsion & Drivetrain'
    description TEXT,
    sort_order  INTEGER NOT NULL DEFAULT 100,
    is_capital  INTEGER NOT NULL DEFAULT 1   -- 0 => consumable/operating expense
);

CREATE TABLE IF NOT EXISTS orders (
    id                INTEGER PRIMARY KEY,
    source            TEXT    NOT NULL,
    external_order_id TEXT    NOT NULL,
    vendor_id         INTEGER REFERENCES vendors(id),
    ordered_at        TEXT,
    status            TEXT    NOT NULL DEFAULT 'unknown',  -- placed|shipped|delivered|cancelled|returned
    subtotal_cents    INTEGER,
    tax_cents         INTEGER,
    shipping_cents    INTEGER,
    discount_cents    INTEGER,
    total_cents       INTEGER NOT NULL DEFAULT 0,
    currency          TEXT    NOT NULL DEFAULT 'USD',
    raw_document_id   INTEGER REFERENCES raw_documents(id),
    -- Which vessel this order belongs to. A prior boat's invoices must never
    -- reach this boat's cost, reward or insurance figures.
    vessel            TEXT,
    reference         TEXT,                             -- invoice / statement number
    created_at        TEXT    NOT NULL,
    updated_at        TEXT    NOT NULL,
    UNIQUE (source, external_order_id)
);
CREATE INDEX IF NOT EXISTS idx_orders_ordered_at ON orders (ordered_at);
CREATE INDEX IF NOT EXISTS idx_orders_vendor ON orders (vendor_id);

CREATE TABLE IF NOT EXISTS line_items (
    id               INTEGER PRIMARY KEY,
    order_id         INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    line_no          INTEGER NOT NULL DEFAULT 0,
    description      TEXT    NOT NULL,
    sku              TEXT,
    asin             TEXT,
    url              TEXT,
    quantity         REAL    NOT NULL DEFAULT 1,
    unit_price_cents INTEGER,
    total_cents      INTEGER NOT NULL DEFAULT 0,
    system_id        INTEGER REFERENCES boat_systems(id),
    classified_by    TEXT,                   -- rule | llm | manual
    classify_conf    REAL,                   -- 0..1
    classified_at    TEXT,
    -- Is this line part of the project at all? Kept separate from system_id:
    -- an item can be confidently 'boat' while its system is still unknown.
    relevance        TEXT,                   -- boat | personal | ambiguous
    relevance_by     TEXT,                   -- rule | llm | manual
    relevance_conf   REAL,
    relevance_note   TEXT,
    -- NULL derives from the system; 1 or 0 forces inclusion or exclusion from
    -- the insurance schedule.
    insurable        INTEGER,
    UNIQUE (order_id, line_no)
);
CREATE INDEX IF NOT EXISTS idx_items_system ON line_items (system_id);
CREATE INDEX IF NOT EXISTS idx_items_unclassified ON line_items (system_id) WHERE system_id IS NULL;

CREATE TABLE IF NOT EXISTS shipments (
    id              INTEGER PRIMARY KEY,
    order_id        INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    carrier         TEXT,
    tracking_number TEXT,
    shipped_at      TEXT,
    delivered_at    TEXT,
    status          TEXT,
    UNIQUE (order_id, tracking_number)
);

-- Returns and refunds are first-class: unresolved refunds are a real risk
-- signal, and net spend is meaningless without them.
CREATE TABLE IF NOT EXISTS refunds (
    id           INTEGER PRIMARY KEY,
    order_id     INTEGER REFERENCES orders(id) ON DELETE CASCADE,
    line_item_id INTEGER REFERENCES line_items(id) ON DELETE SET NULL,
    kind         TEXT    NOT NULL,           -- return | refund | cancellation | price_adjust
    amount_cents INTEGER NOT NULL,
    occurred_at  TEXT,
    status       TEXT,                       -- requested | in_transit | completed
    reason       TEXT
);

-- Warranty / return-window tracking. An expiring return window on a $2k part
-- that was never installed is exactly the risk this project should surface.
CREATE TABLE IF NOT EXISTS item_windows (
    id             INTEGER PRIMARY KEY,
    line_item_id   INTEGER NOT NULL REFERENCES line_items(id) ON DELETE CASCADE,
    window_kind    TEXT    NOT NULL,         -- return | warranty
    expires_at     TEXT    NOT NULL,
    notes          TEXT,
    UNIQUE (line_item_id, window_kind)
);

-- --- Layer 3: money movement (Plaid) ---------------------------------------

CREATE TABLE IF NOT EXISTS accounts (
    id           INTEGER PRIMARY KEY,
    plaid_account_id TEXT UNIQUE,
    institution  TEXT,
    name         TEXT,
    mask         TEXT,
    subtype      TEXT
);

CREATE TABLE IF NOT EXISTS transactions (
    id                   INTEGER PRIMARY KEY,
    plaid_transaction_id TEXT    NOT NULL UNIQUE,
    account_id           INTEGER REFERENCES accounts(id),
    posted_at            TEXT,
    authorized_at        TEXT,
    amount_cents         INTEGER NOT NULL,   -- positive = money out
    merchant_name        TEXT,
    name                 TEXT,
    pending              INTEGER NOT NULL DEFAULT 0,
    plaid_category       TEXT,
    currency             TEXT    NOT NULL DEFAULT 'USD',
    vendor_id            INTEGER REFERENCES vendors(id),
    raw_document_id      INTEGER REFERENCES raw_documents(id)
);
CREATE INDEX IF NOT EXISTS idx_txn_posted ON transactions (posted_at);

-- Links what-was-bought (orders) to what-actually-left-the-account (transactions).
CREATE TABLE IF NOT EXISTS reconciliations (
    id             INTEGER PRIMARY KEY,
    order_id       INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    transaction_id INTEGER NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
    confidence     REAL    NOT NULL,
    method         TEXT    NOT NULL,         -- exact | amount_date | manual
    matched_at     TEXT    NOT NULL,
    UNIQUE (order_id, transaction_id)
);

-- --- Layer 4: planning & analysis ------------------------------------------

CREATE TABLE IF NOT EXISTS budget_lines (
    id            INTEGER PRIMARY KEY,
    system_id     INTEGER NOT NULL REFERENCES boat_systems(id),
    planned_cents INTEGER NOT NULL,
    phase         TEXT,
    notes         TEXT,
    UNIQUE (system_id, phase)
);

-- Project-level facts needed for reward/ROI analysis.
CREATE TABLE IF NOT EXISTS project_meta (
    key        TEXT PRIMARY KEY,
    value      TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sync_state (
    source       TEXT PRIMARY KEY,
    cursor       TEXT,
    last_run_at  TEXT,
    last_status  TEXT,
    detail       TEXT
);

-- --- Convenience views ------------------------------------------------------

-- Net spend per line item after allocated refunds.
CREATE VIEW IF NOT EXISTS v_spend_by_system AS
SELECT
    bs.key                        AS system_key,
    bs.name                       AS system_name,
    bs.sort_order                 AS sort_order,
    COUNT(DISTINCT li.order_id)   AS order_count,
    COUNT(li.id)                  AS item_count,
    COALESCE(SUM(li.total_cents), 0) AS gross_cents
FROM boat_systems bs
LEFT JOIN line_items li ON li.system_id = bs.id
GROUP BY bs.id;

CREATE VIEW IF NOT EXISTS v_unclassified AS
SELECT li.id, li.description, li.total_cents, o.ordered_at, v.canonical_name AS vendor
FROM line_items li
JOIN orders o ON o.id = li.order_id
LEFT JOIN vendors v ON v.id = o.vendor_id
WHERE li.system_id IS NULL;

CREATE VIEW IF NOT EXISTS v_review_queue AS
SELECT li.id, li.description, li.total_cents, li.relevance, li.relevance_conf,
       li.relevance_note, bs.key AS system_key, v.canonical_name AS vendor,
       o.ordered_at
FROM line_items li
JOIN orders o ON o.id = li.order_id
LEFT JOIN vendors v ON v.id = o.vendor_id
LEFT JOIN boat_systems bs ON bs.id = li.system_id
WHERE li.relevance IS NULL OR li.relevance = 'ambiguous'
   OR (li.relevance = 'boat' AND li.system_id IS NULL);

-- --- Layer 5: reward inputs -------------------------------------------------
-- Labor performed rather than paid for. This is the one component of return
-- that is genuinely dollar-for-dollar, so it is recorded rather than estimated.

CREATE TABLE IF NOT EXISTS labor_log (
    id           INTEGER PRIMARY KEY,
    system_id    INTEGER REFERENCES boat_systems(id),
    hours        REAL    NOT NULL,
    description  TEXT,
    performed_at TEXT,
    rate_cents   INTEGER,          -- override the default yard rate for this entry
    logged_at    TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_labor_system ON labor_log (system_id);

-- Nights actually spent aboard. Use-value is the real return on a liveaboard
-- refit, and it cannot be estimated from purchase data.
CREATE TABLE IF NOT EXISTS usage_log (
    id         INTEGER PRIMARY KEY,
    nights     INTEGER NOT NULL,
    start_date TEXT,
    end_date   TEXT,
    location   TEXT,
    note       TEXT,
    logged_at  TEXT    NOT NULL
);

-- Shows performed aboard and streamed. The studio's return is modeled from
-- declared conversion rates until this table has rows; once it does, observed
-- viewers and installs replace the assumed inputs. A NULL count means nobody
-- wrote the number down, which is not the same as zero.
-- A competition night is a show of kind 'competition' with a crowd on the rear
-- dock; `attendees` is that crowd. It is kept apart from unique_viewers because
-- someone standing at the pier in front of the captions is not a stream viewer
-- and converts on a different rate. NULL attendees means not counted.

CREATE TABLE IF NOT EXISTS show_log (
    id                  INTEGER PRIMARY KEY,
    performed_at        TEXT,            -- YYYY-MM-DD
    kind                TEXT    NOT NULL DEFAULT 'set',  -- set | competition
    platform            TEXT,            -- youtube | twitch | kick | facebook | multi | ...
    title               TEXT,
    duration_minutes    INTEGER,
    peak_viewers        INTEGER,
    unique_viewers      INTEGER,
    attendees           INTEGER,         -- on the rear dock and swim platform; NULL = not counted
    installs_attributed INTEGER,         -- traced to this show: store analytics or promo code
    note                TEXT,
    logged_at           TEXT    NOT NULL
);

-- --- Layer 6: committed work ------------------------------------------------
-- Work authorized or scheduled but not yet invoiced. Kept out of `orders` by
-- construction so it can never leak into spend, while still answering the
-- question spend alone cannot: what is this going to cost from here.

CREATE TABLE IF NOT EXISTS commitments (
    id             INTEGER PRIMARY KEY,
    vendor_id      INTEGER REFERENCES vendors(id),
    system_id      INTEGER REFERENCES boat_systems(id),
    description    TEXT    NOT NULL,
    estimate_cents INTEGER,               -- NULL = genuinely unknown, never 0
    scheduled_for  TEXT,
    reference      TEXT,
    status         TEXT    NOT NULL DEFAULT 'open',  -- open | invoiced | cancelled
    vessel         TEXT,
    note           TEXT,
    created_at     TEXT    NOT NULL,
    UNIQUE (reference, description)
);
CREATE INDEX IF NOT EXISTS idx_commitments_open ON commitments (status)
    WHERE status = 'open';
