import contextlib, copy, io, json, shutil, sqlite3, subprocess, tempfile, unittest
from pathlib import Path
from tracker import collector
from tracker.collector import Blocked, Cli, Gone, Replay, Stop

FX = Path(__file__).parent / "fixtures"
CLI = FX / "cli"  # neutral synthetic `wallapop` captures (see tests/fixtures/cli/)

# A generic target, mirroring targets.example.toml. The collector never sees product names.
TOML = """
[defaults]
lat = 40.4168
lon = -3.7038
max_pages_per_query = 5
max_requests_per_run = 50
max_detail_checks_per_run = {detail}
[targets.bikes]
queries = ["bicicletas"]
[targets.bikes.extract]
relevant = { include = ["bicicleta", "bici\\b"], exclude = ["casco", "rueda"] }
[targets.bikes.extract.fields]
frame = [["carbon", "carbono|carbon"], ["alu", "alumini?o"]]
[targets.bikes.extract.numbers]
size_cm = { patterns = [["(\\\\d{2})\\\\s?cm", 1.0]] }
[targets.bikes.extract.derived]
eur_per_cm = "price / size_cm"
"""
P1, P2 = (json.loads((CLI / f"bicicletas.{i}.json").read_text()) for i in (0, 1))
DROPPED, BUMPED, RISE, GONE_A, GONE_B = "bike1", "bike2", "bike3", "bike4", "bike5"


def edited_p1(drop_price=True, bump=True, remove=(GONE_A,)):
    """Run-2 first page: a price drop, a bump, a rise, and listings absent inside the walked window."""
    b = copy.deepcopy(P1)
    items = b["items"]
    byid = {i["hash"]: i for i in items}
    if drop_price:
        byid[DROPPED]["price"] -= 100
        byid[RISE]["price"] += 25                            # rise
    if bump:
        byid[BUMPED]["modified_at"] = "2026-10-31T18:00:00.000Z"   # same price, newer modified_at
    b["items"] = [i for i in items if i["hash"] not in remove]
    return b


class Store(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.db = str(self.tmp / "t.db")

    def cfg(self, detail=0, text=None):
        p = self.tmp / "targets.toml"
        p.write_text((text or TOML).replace("{detail}", str(detail)))
        return str(p)

    def replay(self, name, pages, query="bicicletas"):
        d = self.tmp / name
        d.mkdir()
        for i, body in enumerate(pages):
            (d / f"{query}.{i}.json").write_text(body if isinstance(body, str) else json.dumps(body))
        return d

    def q(self, sql, *a):
        return sqlite3.connect(self.db).execute(sql, a).fetchall()

    def go(self, d, now, detail=0, fetch=None, text=None):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = collector.run(self.db, self.cfg(detail, text), replay=None if fetch else str(d), fetch=fetch, now=now)
        return rc, out.getvalue()

    def run1(self, detail=0):
        rc, _ = self.go(self.replay("r1", [P1, P2]), "2026-10-30T10:00:00.000+00:00", detail)
        self.assertEqual(rc, 0)

    def status(self, lid):
        return self.q("select status from listing where id=?", lid)[0][0]

    def item_fixture(self):
        return json.loads((CLI / "item_bike1.json").read_text())

    # --- store behaviour ---

    def test_two_runs_counters(self):
        self.run1()
        self.assertEqual(self.q("select count(*) from listing")[0][0], 8)   # 6 on page 1, 2 on page 2
        rc, out = self.go(self.replay("r2", [edited_p1()]), "2026-10-31T10:00:00.000+00:00")
        self.assertEqual(rc, 0)
        self.assertIn("price drops=1, rises=1, bumps=1", out)
        self.assertIn("new=0 updated=3", out)
        self.assertIn("missing=1 removed=0", out)
        st = dict(self.q("select id, status from listing"))   # run 2 walked only page 1: page-2 listings are outside the window
        self.assertEqual(st[GONE_A], "missing")
        self.assertEqual(sum(v == "missing" for v in st.values()), 1)
        self.assertEqual(self.q("select count(*) from listing")[0][0], 8)
        self.assertEqual(self.q("select price_eur from observation where listing_id=? order by id", DROPPED), [(300.0,), (200.0,)])
        self.assertEqual(self.q("select first_seen,last_seen from listing where id=?", DROPPED),
                         [("2026-10-30T10:00:00.000+00:00", "2026-10-31T10:00:00.000+00:00")])
        self.assertEqual(self.q("select last_seen from listing where id='bike7'"), [("2026-10-30T10:00:00.000+00:00",)])
        self.assertEqual(self.q("select count(*) from observation")[0][0], 8 + 5 + 1)  # run 2: 5 returned + 1 missing
        self.assertEqual(self.q("select count(*) from raw_response")[0][0], 3)

    def test_removed_sold_reserved_alive(self):
        self.run1()
        gone = [GONE_A, GONE_B]
        d = self.replay("r2", [edited_p1(remove=gone)])
        item = self.item_fixture()
        for lid, flags in ((GONE_A, {"sold": True}), (GONE_B, {"reserved": True})):  # alive-with-flags; others fall to 404
            (d / f"item_{lid}.json").write_text(json.dumps({**item, "hash": lid, **flags}))
        rc, out = self.go(d, "2026-10-31T10:00:00.000+00:00", detail=5)
        self.assertEqual((self.status(GONE_A), self.status(GONE_B)), ("sold", "reserved"))
        self.assertIn("sold=1", out)
        # a 404 and an expired item both end as removed; an item with no flags goes back to active
        d = self.replay("r3", [edited_p1(remove=[BUMPED, RISE, GONE_B])])
        (d / f"item_{BUMPED}.json").write_text(json.dumps({**item, "hash": BUMPED, "expired": True}))
        (d / f"item_{GONE_B}.json").write_text(json.dumps(item))
        rc, out = self.go(d, "2026-11-01T10:00:00.000+00:00", detail=9)
        self.assertEqual(self.status(RISE), "removed")  # no item file = exit 4
        self.assertEqual(self.status(BUMPED), "removed")
        self.assertEqual(self.status(GONE_B), "active")
        self.assertIn("alive=1", out)
        self.assertEqual(self.q("select http_status, body from raw_response where query='item:bike3'"), [(404, "")])

    def test_alive_confirmation_feeds_extract_condition(self):
        self.run1()
        d = self.replay("r2", [edited_p1()])
        shutil.copy(CLI / "item_bike1.json", d / f"item_{GONE_A}.json")  # item show says condition "good"
        self.go(d, "2026-10-31T10:00:00.000+00:00", detail=5)
        self.assertEqual(self.status(GONE_A), "active")  # confirmed alive: back to active, not stuck on missing
        self.assertEqual(json.loads(self.q("select specs from listing where id=?", GONE_A)[0][0])["condition"], "used")

    def test_detail_checks_oldest_checked_first(self):
        self.run1()
        a, b = sorted([GONE_A, GONE_B])  # a is checked first on a tie; a is alive, b is gone
        item = (CLI / "item_bike1.json").read_text()
        def r(n):
            d = self.replay(n, [edited_p1(remove=[a, b])])
            (d / f"item_{a}.json").write_text(item)
            return d
        self.go(r("r2"), "2026-10-31T10:00:00.000+00:00", detail=1)
        self.assertEqual((self.status(a), self.status(b)), ("active", "missing"))
        # a is still absent from search, so it is a candidate again: without last_checked it would starve b forever
        self.go(r("r3"), "2026-11-01T10:00:00.000+00:00", detail=1)
        self.assertEqual(self.status(b), "removed")

    def test_request_cap(self):
        cfg = self.cfg()
        Path(cfg).write_text(Path(cfg).read_text().replace("max_requests_per_run = 50", "max_requests_per_run = 1"))
        with contextlib.redirect_stdout(io.StringIO()):
            collector.run(self.db, cfg, str(self.replay("r1", [P1, P2])), now="2026-10-30T10:00:00.000+00:00")
        self.assertEqual(self.q("select count(*) from raw_response")[0][0], 1)

    def test_derived_value_recomputed_on_price_change(self):
        # bikes target derives eur_per_cm = price / size_cm, and size_cm comes from "54 cm" in bike2's description
        self.run1()
        specs = lambda: json.loads(self.q("select specs from listing where id='bike2'")[0][0])
        self.assertAlmostEqual(specs()["eur_per_cm"], 500 / 54)
        b = json.loads((CLI / "bicicletas.0.json").read_text())
        next(i for i in b["items"] if i["hash"] == "bike2")["price"] = 270
        self.go(self.replay("r2", [b]), "2026-10-31T10:00:00.000+00:00")
        self.assertAlmostEqual(specs()["eur_per_cm"], 270 / 54)

    # --- seller reputation ---

    def test_seller_info_stored(self):
        d = self.replay("r1", [P1, P2])
        shutil.copy(CLI / "seller_s1.json", d / "seller_s1.json")
        self.go(d, "2026-10-30T10:00:00.000+00:00")
        row = self.q("SELECT name, rating_average, reviews, verified, seller_type, registered_at, sold, published "
                     "FROM seller WHERE id='s1'")[0]
        self.assertEqual(row, ("Bici Outlet", 4.9, 23, 1, "professional", "2020-01-15T10:00:00Z", 45, 50))
        # sellers without a fixture get a stub row (checked, no data) so they aren't retried every run
        self.assertEqual(self.q("SELECT count(*) FROM seller")[0][0], 8)
        self.assertEqual(self.q("SELECT count(*) FROM seller WHERE name IS NULL")[0][0], 7)

    def test_seller_info_disabled(self):
        text = TOML.replace("max_detail_checks_per_run = {detail}",
                            "max_detail_checks_per_run = {detail}\nfetch_seller_info = false")
        d = self.replay("r1", [P1, P2])
        shutil.copy(CLI / "seller_s1.json", d / "seller_s1.json")
        self.go(d, "2026-10-30T10:00:00.000+00:00", text=text)
        self.assertEqual(self.q("SELECT count(*) FROM seller")[0][0], 0)

    def test_seller_cap(self):
        text = TOML.replace("max_detail_checks_per_run = {detail}",
                            "max_detail_checks_per_run = {detail}\nmax_seller_checks_per_run = 1")
        d = self.replay("r1", [P1, P2])
        shutil.copy(CLI / "seller_s1.json", d / "seller_s1.json")
        self.go(d, "2026-10-30T10:00:00.000+00:00", text=text)
        self.assertEqual(self.q("SELECT count(*) FROM seller")[0][0], 1)  # cap of 1 -> only one seller checked

    def test_raw_kept_and_history_survives_unexpected_error(self):
        class Boom(Replay):
            def search(self, q, lat, lon, token):
                text = super().search(q, lat, lon, token)
                if self.n == 1:
                    return "not json"   # json.loads raises out of run()
                return text
        self.replay("r1", [P1, P2])
        with self.assertRaises(ValueError):
            self.go(None, "2026-10-30T10:00:00.000+00:00", fetch=Boom(self.tmp / "r1"))
        self.assertEqual(self.q("select count(*) from raw_response")[0][0], 2)  # both bodies survived

    # --- stops: nothing is marked missing/removed, exit code says why ---

    def stopper(self, exc, after=0, item=False, pages=None):
        """Replay that raises `exc` on the (after+1)-th search call, or on any item call."""
        class F(Replay):
            calls = 0
            def search(me, *a):
                me.calls += 1
                if not item and me.calls > after:
                    raise exc
                return super().search(*a)
            def item(me, id):
                if item:
                    raise exc
                return super().item(id)
        return F(self.replay(f"blk{id(exc)}{self.tmp.name}{len(list(self.tmp.iterdir()))}", pages if pages is not None else [edited_p1(remove=[GONE_A, GONE_B]), P2]))

    def assertNothingLost(self):
        self.assertEqual(self.q("select count(*) from listing where status in ('missing','removed')")[0][0], 0)
        self.assertEqual(self.q("select count(*) from observation where status in ('missing','removed')")[0][0], 0)

    def test_block_on_first_request(self):
        self.run1()
        rc, out = self.go(None, "2026-10-31T10:00:00.000+00:00", detail=5, fetch=self.stopper(Blocked("exit 5")))
        self.assertEqual(rc, 2)
        self.assertIn("BLOCKED", out)
        self.assertNothingLost()

    def test_block_on_later_page(self):
        self.run1()
        rc, _ = self.go(None, "2026-10-31T10:00:00.000+00:00", detail=5, fetch=self.stopper(Blocked("exit 5"), after=1))
        self.assertEqual(rc, 2)
        self.assertEqual(self.q("select count(*) from raw_response where fetched_at like '2026-10-31%'")[0][0], 1)  # page 1 kept
        self.assertEqual(self.q("select count(*) from observation where run_at like '2026-10-31%'")[0][0], 4)    # and its rows stored
        self.assertNothingLost()

    def test_block_during_detail_checks(self):
        self.run1()
        rc, out = self.go(None, "2026-10-31T10:00:00.000+00:00", detail=5, fetch=self.stopper(Blocked("exit 5"), after=99, item=True))
        self.assertEqual(rc, 2)
        self.assertNothingLost()  # two candidates were dropped from search and never marked missing
        self.assertEqual(self.status(GONE_A), "active")

    def test_exit7_and_exit6_stop_without_block_code(self):
        self.run1()
        for msg in ("exit 7 (API changed)", "exit 6 (network)"):
            rc, out = self.go(None, "2026-10-31T10:00:00.000+00:00", detail=5, fetch=self.stopper(Stop(msg)))
            self.assertEqual(rc, 1)
            self.assertIn(msg, out)
            self.assertNothingLost()

    def test_ua_retry_notice_is_a_block(self):
        calls = []
        def fake(cmd, **kw):
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, json.dumps(P1), "note: wallapop rejected the wallapop-cli user agent; retrying once\n")
        rc, out = self.go(None, "2026-10-31T10:00:00.000+00:00", fetch=Cli(run=fake, sleep=lambda s: None))
        self.assertEqual(rc, 2)
        self.assertEqual(len(calls), 1)                                       # stopped after the first call
        self.assertEqual(self.q("select count(*) from raw_response")[0][0], 1)  # the body we got is kept
        self.assertEqual(self.q("select count(*) from listing")[0][0], 0)


class CliFetcher(unittest.TestCase):
    def cli(self, rc=0, out="{}", err=""):
        self.calls, self.now, self.sleeps = [], [100.0], []
        def run(cmd, **kw):
            self.calls.append((cmd, kw["env"], self.now[0]))
            self.now[0] += 0.5
            return subprocess.CompletedProcess(cmd, rc, out, err)
        def sleep(s):
            self.sleeps.append(s)
            self.now[0] += s
        return Cli(gap=3.0, run=run, sleep=sleep, clock=lambda: self.now[0])

    def test_flags_env_and_pacing(self):
        c = self.cli()
        c.search("bicicletas", 40.4168, -3.7038, None)
        c.search("bicicletas", 40.4168, -3.7038, "TOK")
        c.item("abc123")
        cmds = [x[0] for x in self.calls]
        self.assertEqual(cmds[0], ["wallapop", "search", "bicicletas", "--lat", "40.4168", "--lng", "-3.7038",
                                   "--sort", "newest", "--pages", "1", "--no-input"])
        self.assertEqual(cmds[1][-3:], ["--next", "TOK", "--no-input"])
        self.assertEqual(cmds[2], ["wallapop", "item", "show", "abc123", "--no-input"])
        for _, env, _ in self.calls:
            self.assertEqual(env["WALLAPOP_NO_UPDATE_CHECK"], "1")
            self.assertNotIn("WALLAPOP_PROXY", env)
        starts = [t for _, _, t in self.calls]
        self.assertTrue(all(b - a >= 3.0 for a, b in zip(starts, starts[1:])), starts)  # >= 3 s between ANY two calls
        self.assertEqual(len(self.sleeps), 2)                                            # none before the first

    def test_exit_codes(self):
        for rc, exc in ((5, Blocked), (7, Stop), (6, Stop), (1, Stop), (3, Stop)):
            with self.subTest(rc), self.assertRaises(exc) as cm:
                self.cli(rc=rc, err="boom").search("q", 1, 2, None)
            self.assertEqual(type(cm.exception) is Blocked, rc == 5)
        with self.assertRaises(Gone):
            self.cli(rc=4).item("abc")
        with self.assertRaises(Stop):  # exit 4 on a search is not "gone"
            self.cli(rc=4).search("q", 1, 2, None)

    def test_ua_notice_is_blocked_and_carries_body(self):
        with self.assertRaises(Blocked) as cm:
            self.cli(out='{"items":[]}', err="wallapop rejected the wallapop-cli user agent").item("abc")
        self.assertEqual(cm.exception.body, '{"items":[]}')

    def test_user_command(self):
        c = self.cli(out='{"hash":"s1","name":"Bici Outlet","stats":{"rating_average":4.9}}')
        self.assertEqual(c.user("s1"), '{"hash":"s1","name":"Bici Outlet","stats":{"rating_average":4.9}}')
        self.assertEqual(self.calls[0][0], ["wallapop", "user", "show", "s1", "--no-input"])
        self.assertIsNone(self.cli(rc=4).user("ghost"))  # exit 4 (no such user) -> None, not an exception


if __name__ == "__main__":
    unittest.main()
