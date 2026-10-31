"""Collector: shell out to wallapop-cli, keep raw bodies, upsert listings, derive events.

Replay dir layout: <query with spaces as '-'>.<n>.json = CLI search page n (file absent = end of
walk); item_<id>.json = CLI `item show` body (file absent = exit 4, i.e. gone).
"""
import datetime as dt, json, math, os, sqlite3, subprocess, time, tomllib
from pathlib import Path
from tracker.extract import extract

UA_NOTICE = "wallapop rejected the wallapop-cli user agent"  # stderr note before the CLI's one-time browser-UA retry

SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_response (
  id INTEGER PRIMARY KEY, fetched_at TEXT NOT NULL, target TEXT NOT NULL,
  query TEXT NOT NULL,            -- search keywords, or 'item:<id>' for a detail call
  page INTEGER NOT NULL, http_status INTEGER NOT NULL, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS listing (
  id TEXT PRIMARY KEY, target TEXT NOT NULL, title TEXT NOT NULL, description TEXT, url TEXT NOT NULL,
  first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, created_at INTEGER, price_eur REAL,
  status TEXT NOT NULL,           -- active | missing | reserved | sold | removed
  seller_id TEXT, shipping INTEGER, location TEXT, distance_km REAL, specs TEXT,
  last_checked TEXT);             -- last `item show` call; oldest-checked-first picks the next detail checks
CREATE TABLE IF NOT EXISTS listing_query (
  listing_id TEXT NOT NULL REFERENCES listing(id), target TEXT NOT NULL, query TEXT NOT NULL,
  PRIMARY KEY (listing_id, target, query));
CREATE TABLE IF NOT EXISTS observation (
  id INTEGER PRIMARY KEY, listing_id TEXT NOT NULL REFERENCES listing(id), run_at TEXT NOT NULL,
  raw_id INTEGER REFERENCES raw_response(id), price_eur REAL, status TEXT NOT NULL,
  modified_at INTEGER, UNIQUE (listing_id, run_at));
CREATE TABLE IF NOT EXISTS seller (
  id TEXT PRIMARY KEY,            -- Wallapop seller hash (same value as listing.seller_id)
  name TEXT,
  rating_average REAL,            -- 0..5
  reviews INTEGER,                -- number of reviews received
  sold INTEGER,                   -- items sold
  published INTEGER,              -- items listed
  verified INTEGER,               -- 0/1
  seller_type TEXT,               -- individual | professional | ...
  location TEXT,                  -- seller's city
  registered_at TEXT,             -- ISO 8601; account age is a scam signal
  first_seen TEXT NOT NULL,
  last_checked TEXT);
"""
DEFAULTS = dict(lat=40.4168, lon=-3.7038, max_pages_per_query=5, max_requests_per_run=150,
                max_detail_checks_per_run=10, fetch_seller_info=True, max_seller_checks_per_run=20)
LIVE = ("active", "reserved", "missing")  # statuses a listing can hold while it may still be for sale


class Stop(Exception):
    """The run must end now (nothing gets marked missing/removed). `body` = a response we still got."""
    def __init__(self, msg, body=None):
        super().__init__(msg)
        self.body = body


class Blocked(Stop):
    """CLI exit 5, or the CLI's user-agent retry notice: wallapop is pushing back. Collector exit code 2."""


class Gone(Exception):
    """`item show` exit 4: not found."""


class Cli:
    """wallapop-cli via subprocess. Read-only commands, no credentials, no proxy, >= `gap` s between ANY two calls."""
    def __init__(self, gap=3.0, run=subprocess.run, sleep=time.sleep, clock=time.monotonic):
        self.gap, self.run, self.sleep, self.clock, self.last = gap, run, sleep, clock, None

    def _call(self, *args, gone=False):
        if self.last is not None:
            self.sleep(max(0.0, self.last + self.gap - self.clock()))
        env = {**os.environ, "WALLAPOP_NO_UPDATE_CHECK": "1"}
        env.pop("WALLAPOP_PROXY", None)
        try:
            p = self.run(["wallapop", *args, "--no-input"], capture_output=True, text=True, env=env, timeout=60)
        except subprocess.TimeoutExpired as e:
            raise Stop(f"wallapop timed out after {e.timeout}s") from e
        finally:
            self.last = self.clock()
        err = (p.stderr or "").strip()
        if p.returncode == 5:
            raise Blocked(f"wallapop exit 5 (blocked/rate limited): {err[:200]}")
        if UA_NOTICE in err:  # the CLI retried with a browser UA: we got data, but stop here
            raise Blocked(f"wallapop rejected its own user agent (CloudFront 403): {err[:200]}", body=p.stdout or None)
        if p.returncode == 4 and gone:
            raise Gone
        if p.returncode == 7:
            raise Stop(f"wallapop exit 7 (API changed): {err[:300]}")
        if p.returncode == 6:
            raise Stop(f"wallapop exit 6 (network): {err[:200]}")
        if p.returncode:
            raise Stop(f"wallapop exit {p.returncode}: {err[:200]}")
        return p.stdout

    def search(self, q, lat, lon, token):
        # ponytail: one page per process; the stored body is the CLI's stdout, not the wire bytes
        args = ["search", q, "--lat", str(lat), "--lng", str(lon), "--sort", "newest", "--pages", "1"]
        return self._call(*args, *(["--next", token] if token else []))

    def item(self, id):
        return self._call("item", "show", id, gone=True)

    def user(self, hash):
        """`wallapop user show` -> the seller profile JSON. Exit 4 (no such user) -> None."""
        try:
            return self._call("user", "show", hash, gone=True)
        except Gone:
            return None


class Replay:
    def __init__(self, d):
        self.d, self.n = Path(d), 0

    def search(self, q, lat, lon, token):
        self.n = 0 if token is None else self.n + 1
        f = self.d / f"{q.replace(' ', '-')}.{self.n}.json"
        return f.read_text() if f.exists() else None

    def item(self, id):
        f = self.d / f"item_{id}.json"
        if not f.exists():
            raise Gone(id)
        return f.read_text()

    def user(self, hash):
        f = self.d / f"seller_{hash}.json"
        if not f.exists():
            return None
        return f.read_text()


def _km(lat1, lon1, lat2, lon2):
    p = math.pi / 180
    a = math.sin((lat2 - lat1) * p / 2) ** 2 + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lon2 - lon1) * p / 2) ** 2
    return 12742 * math.asin(math.sqrt(a))


def _ms(iso):
    """CLI ISO time -> epoch ms. `item show` reports created_at as 0001-01-01 (unknown): None."""
    return None if not iso or iso.startswith("0001") else round(dt.datetime.fromisoformat(iso).timestamp() * 1000)


def status_of(it):
    """Listing status from a CLI search item or `item show` body."""
    return "sold" if it.get("sold") else "removed" if it.get("expired") else "reserved" if it.get("reserved") else "active"


def parse_row(it, origin):
    """One CLI search item -> the fields the collector stores."""
    loc = it.get("location") or {}
    lat, lon = loc.get("lat"), loc.get("lng")
    price = it.get("price")
    return {
        "id": it["hash"], "title": it.get("title") or "", "description": it.get("description"),
        "price": None if price is None else float(price), "shipping": bool(it.get("shippable")),
        "seller_id": it.get("seller_hash"), "city": loc.get("city"), "url": it["url"],
        "distance_km": round(_km(*origin, lat, lon), 1) if lat is not None and lon is not None else None,
        "created_at": _ms(it.get("created_at")), "modified_at": _ms(it.get("modified_at")), "status": status_of(it),
    }


def run(db="tracker.db", targets="targets.toml", replay=None, fetch=None, now=None):
    cfg = tomllib.loads(Path(targets).read_text())
    d = {**DEFAULTS, **cfg.get("defaults", {})}
    now = now or dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds")
    fetch = fetch or (Replay(replay) if replay else Cli(max(3.0, float(d.get("min_delay_s", 3)))))
    origin = (d["lat"], d["lon"])
    con = sqlite3.connect(db)
    con.executescript(SCHEMA)
    s = dict(requests=0, pages=0, new=0, updated=0, missing=0, removed=0, sold=0, alive=0, drops=0, rises=0, bumps=0, sellers=0)
    stop, capped, seen, seen_sellers, walked = None, False, set(), set(), []

    def store_raw(target, query, page, status, text):
        rid = con.execute("INSERT INTO raw_response(fetched_at,target,query,page,http_status,body) VALUES(?,?,?,?,?,?)",
                          (now, target, query, page, status, text)).lastrowid
        con.commit()  # raw bodies are evidence: whatever fails later, they stay
        return rid

    def get(target, query, page, fn, *a):
        """One fetch: counts the request, stores the raw body (even one that came with a block). -> (text, raw_id)"""
        s["requests"] += 1
        try:
            text = fn(*a)
        except Stop as e:
            if e.body:
                store_raw(target, query, page, 200, e.body)
            raise
        if text is None:  # replay: end of walk, nothing was fetched
            s["requests"] -= 1
            return None, None
        return text, store_raw(target, query, page, 200, text)

    def specs_json(tname, title, desc, price, detail=None):
        return json.dumps(extract(cfg["targets"].get(tname) or {}, title, desc, detail=detail, price=price), ensure_ascii=False)

    def upsert(tname, q, got):
        """Rows of one finished query -> listing + observation (committed per query)."""
        for r, raw in got:
            con.execute("INSERT OR IGNORE INTO listing_query VALUES(?,?,?)", (r["id"], tname, q))
            if r["id"] in seen:
                continue
            seen.add(r["id"])
            prev = con.execute("SELECT price_eur,title,description,target FROM listing WHERE id=?", (r["id"],)).fetchone()
            last = con.execute("SELECT price_eur,modified_at FROM observation WHERE listing_id=? ORDER BY id DESC LIMIT 1",
                               (r["id"],)).fetchone()
            loc = r["city"] or ""
            if prev is None:
                con.execute("INSERT INTO listing VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL)",
                            (r["id"], tname, r["title"], r["description"], r["url"], now, now, r["created_at"], r["price"],
                             r["status"], r["seller_id"], int(r["shipping"]), loc, r["distance_km"],
                             specs_json(tname, r["title"], r["description"], r["price"])))
                s["new"] += 1
            else:
                specs_sql, args = "", [r["title"], r["description"], r["price"], now, r["distance_km"], r["url"],
                                       r["seller_id"], int(r["shipping"]), loc, r["status"]]
                if prev[:3] != (r["price"], r["title"], r["description"]):  # re-extract on any change; price feeds derived values
                    specs_sql = ",specs=?"
                    args.append(specs_json(prev[3], r["title"], r["description"], r["price"]))  # the listing's own target
                con.execute("UPDATE listing SET title=?,description=?,price_eur=?,last_seen=?,distance_km=?,url=?,seller_id=?,"
                            f"shipping=?,location=?,status=?{specs_sql} WHERE id=?", (*args, r["id"]))
                old_price, old_mod = last if last else (prev[0], None)
                if r["price"] is not None and old_price is not None and r["price"] < old_price:
                    s["drops"] += 1
                    s["updated"] += 1
                elif r["price"] is not None and old_price is not None and r["price"] > old_price:
                    s["rises"] += 1
                    s["updated"] += 1
                elif old_mod is not None and r["modified_at"] != old_mod:
                    s["bumps"] += 1
                    s["updated"] += 1
            con.execute("INSERT INTO observation(listing_id,run_at,raw_id,price_eur,status,modified_at) VALUES(?,?,?,?,?,?)",
                        (r["id"], now, raw, r["price"], r["status"], r["modified_at"]))
        con.commit()

    # 1. walk: one CLI call per page, following next_page up to the per-query and per-run caps; upsert per query
    for tname, t in cfg["targets"].items():
        for q in t["queries"]:
            token, got = None, []
            for page in range(d["max_pages_per_query"]):
                if s["requests"] >= d["max_requests_per_run"]:
                    capped = True
                    break
                try:
                    text, raw = get(tname, q, page, fetch.search, q, d["lat"], d["lon"], token)
                except Stop as e:
                    stop = e
                    break
                if text is None:
                    break
                s["pages"] += 1
                body = json.loads(text)
                rows = [parse_row(it, origin) for it in body["items"]]
                got += [(r, raw) for r in rows]
                for r in rows:
                    if r["seller_id"]:
                        seen_sellers.add(r["seller_id"])
                token = body.get("next_page")
                if not token:
                    break
            upsert(tname, q, got)  # on a stop this keeps the pages we did get
            if stop:
                break
            walked.append((tname, q, min((r["created_at"] for r, _ in got if r["created_at"]), default=None)))
        if stop:
            break
    if capped:
        print(f"request cap ({d['max_requests_per_run']}) hit; stopped walking")

    # 1b. seller reputation for sellers seen this run but not yet stored (scam signals).
    #     Fetched once per seller on first sight, gated by config and capped per run.
    if not stop and d.get("fetch_seller_info", True):
        existing = {r[0] for r in con.execute("SELECT id FROM seller")}
        todo = sorted(h for h in seen_sellers if h and h not in existing)
        budget = max(0, min(int(d.get("max_seller_checks_per_run", 20)), d["max_requests_per_run"] - s["requests"]))
        for h in todo[:budget]:
            s["requests"] += 1
            try:
                text = fetch.user(h)
            except Stop as e:
                stop = e
                break
            if not text:  # no such seller (or replay has no fixture): record a stub so we don't retry every run
                con.execute("INSERT OR REPLACE INTO seller(id,first_seen,last_checked) VALUES(?,?,?)", (h, now, now))
                continue
            try:
                u = json.loads(text)
            except json.JSONDecodeError:
                continue
            stats = u.get("stats") or {}
            loc = u.get("location") or {}
            con.execute(
                "INSERT OR REPLACE INTO seller(id,name,rating_average,reviews,sold,published,verified,seller_type,location,registered_at,first_seen,last_checked) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (h, u.get("name"), stats.get("rating_average"), stats.get("reviews"), stats.get("sold"),
                 stats.get("published"), int(bool(u.get("verified"))), u.get("seller_type"),
                 loc.get("city"), u.get("registered_at"), now, now))
            s["sellers"] += 1
        con.commit()

    # 2. missing: previously returned by this query, created inside the window we walked, not returned now.
    #    Absence outside the window proves nothing. Skipped entirely on a stop.
    cands = {}  # id -> (target, last_checked)
    if not stop:
        for tname, q, low in walked:
            if low is None:
                continue
            for lid, lc in con.execute(
                    "SELECT l.id,l.last_checked FROM listing_query lq JOIN listing l ON l.id=lq.listing_id "
                    f"WHERE lq.target=? AND lq.query=? AND l.created_at>=? AND l.status IN {LIVE}", (tname, q, low)):
                if lid not in seen:
                    cands[lid] = (tname, lc)

    # 3. confirm via `item show` (exit 4 = removed; sold/expired/reserved flags), oldest-checked first, capped.
    #    Outcomes are applied only if the run was not stopped: a block records no missing/removed.
    budget = max(0, min(d["max_detail_checks_per_run"], d["max_requests_per_run"] - s["requests"]))
    outs = []  # (id, status, raw_id, detail body or None, checked)
    for lid in sorted(cands, key=lambda i: (cands[i][1] or "", i)):
        if budget <= 0:
            outs.append((lid, "missing", None, None, False))
            continue
        budget -= 1
        try:
            text, raw = get(cands[lid][0], f"item:{lid}", 0, fetch.item, lid)
            detail = json.loads(text)
            outs.append((lid, status_of(detail), raw, detail, True))
        except Gone:
            outs.append((lid, "removed", store_raw(cands[lid][0], f"item:{lid}", 0, 404, ""), None, True))
        except Stop as e:
            stop = e
            break
    if not stop:
        for lid, st, raw, detail, checked in outs:
            price, mod, tname, title, desc = con.execute(
                "SELECT price_eur,(SELECT modified_at FROM observation WHERE listing_id=? ORDER BY id DESC LIMIT 1),target,title,description "
                "FROM listing WHERE id=?", (lid, lid)).fetchone()
            con.execute("UPDATE listing SET status=?,last_checked=COALESCE(?,last_checked) WHERE id=?", (st, now if checked else None, lid))
            if detail:  # T4: the CLI item JSON feeds extract (condition)
                con.execute("UPDATE listing SET specs=? WHERE id=?", (specs_json(tname, title, desc, price, detail), lid))
            con.execute("INSERT INTO observation(listing_id,run_at,raw_id,price_eur,status,modified_at) VALUES(?,?,?,?,?,?)",
                        (lid, now, raw, price, st, mod))
            s[st if st in ("removed", "missing", "sold") else "alive"] += 1
        con.commit()

    if stop:
        print(f"{'BLOCKED' if isinstance(stop, Blocked) else 'STOPPED'}: {stop}. Run ended; no missing/removed recorded.")
    print(f"run {now}: requests={s['requests']} pages={s['pages']} new={s['new']} updated={s['updated']} "
          f"(price drops={s['drops']}, rises={s['rises']}, bumps={s['bumps']}) missing={s['missing']} removed={s['removed']} "
          f"sold={s['sold']} alive={s['alive']} sellers={s['sellers']}")
    con.close()
    return 2 if isinstance(stop, Blocked) else 1 if stop else 0
