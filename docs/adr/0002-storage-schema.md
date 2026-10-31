# ADR 0002: Storage schema

Status: accepted (API-discovery TBCs resolved)

## Decision

One SQLite file. History is append-only; nothing is overwritten except the "current state" columns on `listing`.

- `raw_response`: immutable. One row per fetch.
- `listing`: one row per Wallapop id, holding current state.
- `observation`: append-only, one row per listing per run. Price drops, bumps and removals are derived from it.

```sql
CREATE TABLE raw_response (
  id          INTEGER PRIMARY KEY,
  fetched_at  TEXT NOT NULL,          -- UTC ISO 8601
  target      TEXT NOT NULL,
  query       TEXT NOT NULL,          -- search keywords, or 'item:<id>' for a detail call
  page        INTEGER NOT NULL,       -- step within the query's walk
  http_status INTEGER NOT NULL,       -- 404 rows have an empty body (item gone)
  body        TEXT NOT NULL           -- response JSON, never edited
);

CREATE TABLE listing (
  id          TEXT PRIMARY KEY,       -- Wallapop id, a short string like 'nz085o902r6o'
  target      TEXT NOT NULL,          -- first target that saw it
  title       TEXT NOT NULL,
  description TEXT,
  url         TEXT NOT NULL,
  first_seen  TEXT NOT NULL,
  last_seen   TEXT NOT NULL,
  created_at  INTEGER,                -- ad creation, epoch ms; bounds the missing-detection window
  price_eur   REAL,                   -- current price
  status      TEXT NOT NULL,          -- active | missing | reserved | sold | removed
  seller_id   TEXT,
  shipping    INTEGER,                -- 0/1: shippable and seller allows it
  location    TEXT,                   -- "city, region"
  distance_km REAL,                   -- haversine from the configured origin
  specs       TEXT,                   -- JSON from extract(); condition and brand come from `item show`
  last_checked TEXT                   -- last `item show` call; oldest-checked-first drives the detail checks
);

CREATE TABLE listing_query (          -- which queries returned a listing
  listing_id TEXT NOT NULL REFERENCES listing(id),
  target TEXT NOT NULL, query TEXT NOT NULL,
  PRIMARY KEY (listing_id, target, query)
);

CREATE TABLE observation (
  id          INTEGER PRIMARY KEY,
  listing_id  TEXT NOT NULL REFERENCES listing(id),
  run_at      TEXT NOT NULL,
  raw_id      INTEGER REFERENCES raw_response(id),
  price_eur   REAL,
  status      TEXT NOT NULL,          -- active | missing | reserved | sold | removed
  modified_at INTEGER,                -- ad's own modified_at (epoch ms); a change at the same price is a bump
  UNIQUE (listing_id, run_at)
);
```

## Rules

- **Specs as JSON, not columns,** until the extractors settle. Promote a field to a column when a dashboard filter needs it.
- **Price drop** = `observation.price_eur` lower than the previous observation for that listing.
- **Absence is not removal.** Search is sorted by `created_at`, pages are date windows, and search has no sold flag. A listing becomes `missing` only if a query that returned it before walked past its `created_at` this run and did not return it. Listings older than the walked window are left alone.
- **Removed** = a `missing` listing that `item show` reports as gone: exit 4 (not found) or the `sold`, `reserved` or `expired` flags in its body. Detail checks are capped per run and go oldest-checked first. A listing confirmed alive by `item show` returns to `active` (or `reserved`).
- A run that hits a block (CLI exit 5 or the user-agent retry notice) records no `missing` or `removed`.
- **Price rise, price drop, bump:** compared with the listing's previous observation. A bump is a changed `modified_at` at the same price.
- **Bargain, comparable group and lowball** are computed at read time from `observation`, not stored, so changing a threshold needs no migration.

## Consequences

`raw_response` holds the response JSON as re-serialised by the client (same content, not the wire bytes). Re-parsing after an extractor fix replays it. Disk grows by one body per fetch; prune old raw bodies only by explicit decision.
