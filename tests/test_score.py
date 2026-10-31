import io, json, shutil, sqlite3, tempfile, unittest
from pathlib import Path
from tracker import score
from tracker.collector import SCHEMA

# Generic example config mirroring targets.example.toml (no product knowledge in code).
CONFIG = """
[targets.bikes]
reject_above_eur = 800
lowball_pct = 10
bargain = { stat = "p25", group_by = ["model", "condition"], min_group_size = 2, factor = 0.85 }
queries = ["bicicleta"]

[targets.appliances]
reject_above_eur = 300
lowball_pct = 15
bargain = { stat = "price_per_unit", max = 120 }
queries = ["lavavajillas"]
"""


def listing(lid, title, price, specs=None, url=None, target="bikes"):
    return {"id": lid, "title": title, "price": price,
            "url": url or f"https://es.wallapop.com/item/{lid}",
            "specs": specs or {}}


def p25_cfg(factor=0.85, min_group=1, reject=None, pct=15, group_by=("model", "condition")):
    return {"bargain": {"stat": "p25", "group_by": list(group_by), "min_group_size": min_group,
                        "factor": factor},
            "reject_above_eur": reject, "lowball_pct": pct}


class Quartile(unittest.TestCase):
    def test_p25_known(self):
        self.assertEqual(score.p25([100, 200, 300, 400, 500]), 150.0)
        self.assertEqual(score.p25([1, 2, 3, 4]), 1.25)
        self.assertEqual(score.p25([50, 60, 70]), 50.0)
        self.assertEqual(score.p25([7]), 7.0)


class ThresholdBargain(unittest.TestCase):
    def cfg(self):
        return {"bargain": {"stat": "price_per_unit", "max": 120},
                "reject_above_eur": 300, "lowball_pct": 15}

    def test_rule(self):
        rows = [
            listing("a", "Lavadora 2u", 200.0, {"price_per_unit": 100.0}, target="appliances"),  # bargain
            listing("b", "Lavadora", 150.0, {"price_per_unit": 150.0}, target="appliances"),     # too pricey per unit
            listing("c", "Lavavajillas", 400.0, {"price_per_unit": 100.0}, target="appliances"), # over reject_above
            listing("d", "Lavadora", 100.0, {"price_per_unit": None}, target="appliances"),      # not assessable
        ]
        by = {l["id"]: l for l in score.score_target(self.cfg(), rows)["listings"]}
        self.assertTrue(by["a"]["is_bargain"])
        for x in "bcd":
            self.assertFalse(by[x]["is_bargain"], x)
        self.assertEqual(by["a"]["metric"], 100.0)

    def test_lowball(self):
        rows = [listing("a", "Lavadora", 200.0, {"price_per_unit": 100.0}, target="appliances")]
        by = {l["id"]: l for l in score.score_target(self.cfg(), rows)["listings"]}
        self.assertEqual(by["a"]["lowball_offer"], 170.0)  # 200 * 0.85

    def test_min_field_floor(self):
        cfg = {"bargain": {"stat": "eur_per_tb", "max": 18, "min_field": "total_tb", "min_value": 12},
               "reject_above_eur": 400, "lowball_pct": 10}
        rows = [
            listing("a", "14TB drive", 210.0, {"eur_per_tb": 15.0, "total_tb": 14.0}, target="appliances"),  # bargain
            listing("b", "8TB drive", 120.0, {"eur_per_tb": 15.0, "total_tb": 8.0}, target="appliances"),    # below floor
        ]
        by = {l["id"]: l for l in score.score_target(cfg, rows)["listings"]}
        self.assertTrue(by["a"]["is_bargain"])
        self.assertFalse(by["b"]["is_bargain"])

    def test_irrelevant_listings_skipped(self):
        rows = [
            listing("1", "motor", 5.0, {"relevant": False}),
            listing("2", "drone", 100.0, {"relevant": True, "model": "x", "condition": "new"}),
        ]
        s = score.score_target(p25_cfg(), rows)
        self.assertEqual({l["id"] for l in s["listings"]}, {"2"})  # the part is excluded


class P25Bargain(unittest.TestCase):
    def rows(self):
        return [
            listing("1", "Trek new", 100.0, {"model": "trek", "condition": "new"}),
            listing("2", "Trek new", 200.0, {"model": "trek", "condition": "new"}),
            listing("3", "Trek new", 300.0, {"model": "trek", "condition": "new"}),
            listing("4", "Trek new", 400.0, {"model": "trek", "condition": "new"}),
            listing("5", "Trek new", 500.0, {"model": "trek", "condition": "new"}),
            listing("6", "Orbea used", 50.0, {"model": "orbea", "condition": "used"}),
            listing("7", "Orbea used", 60.0, {"model": "orbea", "condition": "used"}),
            listing("8", "Orbea used", 70.0, {"model": "orbea", "condition": "used"}),
        ]

    def test_groups_by_model_and_condition(self):
        s = score.score_target(p25_cfg(), self.rows())
        by = {l["id"]: l for l in s["listings"]}
        # trek/new: median 300, p25 150, threshold 255 -> 100 and 200 are bargains
        self.assertTrue(by["1"]["is_bargain"]); self.assertTrue(by["2"]["is_bargain"])
        self.assertFalse(by["3"]["is_bargain"]); self.assertFalse(by["5"]["is_bargain"])
        # orbea/used: median 60, threshold 51 -> only 50
        self.assertTrue(by["6"]["is_bargain"]); self.assertFalse(by["7"]["is_bargain"])
        g = {tuple(x["key"]): x for x in s["groups"]}
        self.assertEqual(len(g), 2)
        self.assertEqual((g[("trek", "new")]["median"], g[("trek", "new")]["p25"],
                          g[("trek", "new")]["threshold_eur"]), (300.0, 150.0, 255.0))
        self.assertEqual(g[("orbea", "used")]["median"], 60.0)
        self.assertIsNone(s["note"])

    def test_fallback_one_group_when_model_missing(self):
        rows = [
            listing("1", "a", 100.0, {"condition": "new"}),
            listing("2", "b", 200.0, {"condition": "used"}),
            listing("3", "c", 300.0, {"condition": "new"}),
            listing("4", "d", 400.0, {"condition": None}),
        ]
        s = score.score_target(p25_cfg(), rows)
        self.assertEqual(len(s["groups"]), 1)
        self.assertIsNone(s["groups"][0]["key"])
        # one group over all listings: median 250, p25 125, threshold 212.5
        self.assertEqual((s["groups"][0]["median"], s["groups"][0]["p25"],
                          s["groups"][0]["threshold_eur"]), (250.0, 125.0, 212.5))
        self.assertIn("fell back", s["note"])
        by = {l["id"]: l for l in s["listings"]}
        self.assertTrue(by["1"]["is_bargain"]); self.assertTrue(by["2"]["is_bargain"])
        self.assertFalse(by["3"]["is_bargain"]); self.assertFalse(by["4"]["is_bargain"])

    def test_min_group_size_suppresses_small_groups(self):
        rows = [listing("1", "solo", 100.0, {"model": "trek", "condition": "new"}),
                listing("2", "pair", 90.0, {"model": "orbea", "condition": "used"}),
                listing("3", "pair", 110.0, {"model": "orbea", "condition": "used"}),
                listing("4", "trio", 200.0, {"model": "bh", "condition": "new"}),
                listing("5", "trio", 300.0, {"model": "bh", "condition": "new"}),
                listing("6", "trio", 400.0, {"model": "bh", "condition": "new"})]
        s = score.score_target(p25_cfg(min_group=2), rows)
        keys = {tuple(g["key"]) for g in s["groups"]}
        self.assertIn(("orbea", "used"), keys)
        self.assertIn(("bh", "new"), keys)
        self.assertNotIn(("trek", "new"), keys)         # single-listing group is dropped
        by = {l["id"]: l for l in s["listings"]}
        self.assertFalse(by["1"]["is_bargain"])         # group too small -> no threshold
        self.assertFalse(by["2"]["is_bargain"])         # median 100 -> threshold 85, 90 is not a bargain
        self.assertTrue(by["4"]["is_bargain"])          # median 300 -> threshold 255, 200 is a bargain

    def test_reject_above_never_bargain(self):
        rows = [listing(str(i), f"p{i}", 100.0 * i, {"model": "m"}) for i in range(1, 6)]
        s = score.score_target(p25_cfg(reject=150), rows)
        by = {l["id"]: l for l in s["listings"]}
        self.assertTrue(by["1"]["is_bargain"])          # 100 <= 150 and <= 255
        self.assertFalse(by["2"]["is_bargain"])         # 200 > reject_above (though <= 255)

    def test_lowball_offer(self):
        s = score.score_target(p25_cfg(group_by=("model",)), self.rows())
        by = {l["id"]: l for l in s["listings"]}
        # trek/new threshold 255; lowball = max(255, price * 0.85)
        self.assertEqual(by["5"]["lowball_offer"], 425.0)   # max(255, 425)
        self.assertEqual(by["2"]["lowball_offer"], 255.0)   # max(255, 170)


class CurrentPrice(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.db = str(self.tmp / "t.db")
        con = sqlite3.connect(self.db)
        con.executescript(SCHEMA)
        con.commit()
        con.close()

    def con(self):
        return sqlite3.connect(self.db)

    def add_listing(self, lid, price_eur, specs, target="bikes", status="active"):
        with self.con() as con:
            con.execute("INSERT INTO listing (id,target,title,url,first_seen,last_seen,"
                        "price_eur,status,specs) VALUES (?,?,?,?,?,?,?,?,?)",
                        (lid, target, f"title {lid}", f"https://es.wallapop.com/item/{lid}",
                         "2026-10-30T10:00:00+00:00", "2026-10-30T10:00:00+00:00",
                         price_eur, status, json.dumps(specs)))

    def add_obs(self, lid, price, status="active", run="2026-10-30T10:00:00+00:00"):
        with self.con() as con:
            con.execute("INSERT INTO observation (listing_id,run_at,price_eur,status) "
                        "VALUES (?,?,?,?)", (lid, run, price, status))

    def test_current_price_is_latest_observation(self):
        self.add_listing("a", 100.0, {})
        self.add_obs("a", 100.0)
        self.add_obs("a", 80.0, run="2026-10-31T10:00:00+00:00")
        self.add_listing("b", 90.0, {})                       # no observation -> listing price
        self.add_listing("c", 70.0, {})                       # latest obs has NULL price -> listing price
        self.add_obs("c", None)
        self.add_listing("d", 60.0, {}, status="sold")        # not active -> excluded
        with self.con() as con:
            got = {l["id"]: l["price"] for l in score.active_listings(con, "bikes")}
        self.assertEqual(got, {"a": 80.0, "b": 90.0, "c": 70.0})


class Report(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.db = str(self.tmp / "t.db")
        con = sqlite3.connect(self.db)
        con.executescript(SCHEMA)
        con.commit()
        con.close()
        self.targets = str(self.tmp / "targets.toml")
        (self.tmp / "targets.toml").write_text(CONFIG)

    def add(self, lid, target, price, specs, status="active", obs_prices=()):
        with sqlite3.connect(self.db) as con:
            con.execute("INSERT INTO listing (id,target,title,url,first_seen,last_seen,"
                        "price_eur,status,specs) VALUES (?,?,?,?,?,?,?,?,?)",
                        (lid, target, f"title {lid}", f"https://es.wallapop.com/item/{lid}",
                         "2026-10-30T10:00:00+00:00", "2026-10-30T10:00:00+00:00",
                         price, status, json.dumps(specs)))
            for i, p in enumerate(obs_prices):
                con.execute("INSERT INTO observation (listing_id,run_at,price_eur,status) "
                            "VALUES (?,?,?,?)", (lid, f"2026-10-3{i}T10:00:00+00:00", p, "active"))

    def test_report_appliances_prints_thresholds_and_bargains(self):
        self.add("a", "appliances", 200.0, {"price_per_unit": 100.0})
        self.add("b", "appliances", 150.0, {"price_per_unit": 150.0})
        out = io.StringIO()
        score.report(self.db, self.targets, out=out)
        text = out.getvalue()
        self.assertIn("target: appliances", text)
        self.assertIn("max = 120", text)
        self.assertIn("bargains: 1", text)
        self.assertIn("title a", text)
        self.assertIn("price_per_unit=100", text)
        self.assertIn("https://es.wallapop.com/item/a", text)

    def test_report_bikes_notes_model_gap(self):
        self.add("x", "bikes", 90.0, {"condition": "new"})
        self.add("y", "bikes", 120.0, {"condition": "used"})
        out = io.StringIO()
        score.report(self.db, self.targets, out=out)
        text = out.getvalue()
        self.assertIn("target: bikes", text)
        self.assertIn("factor = 0.85", text)
        self.assertIn("fell back", text)             # model gap surfaced in the report


if __name__ == "__main__":
    unittest.main()
