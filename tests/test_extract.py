import tomllib, unittest
from tracker.extract import extract

# Generic example targets, mirroring targets.example.toml. They prove the extractor is
# a generic engine: every target-specific rule lives in config, never in Python.
BIKES = """
[targets.bikes]
queries = ["bicicleta carretera"]
[targets.bikes.extract]
relevant = { include = ["bicicleta", "bici\\b"], exclude = ["casco", "rueda"] }
[targets.bikes.extract.fields]
frame = [["carbon", "carbono|carbon"], ["alu", "alumini?o"], ["acero", "acero|steel"]]
[targets.bikes.extract.numbers]
size_cm = { patterns = [["(\\\\d{2})\\\\s?cm", 1.0]] }
[targets.bikes.extract.derived]
eur_per_cm = "price / size_cm"
"""

APPLIANCES = """
[targets.appliances]
queries = ["lavavajillas"]
[targets.appliances.extract]
relevant = { include = ["lavavajillas", "lavadora", "frigorifico"], exclude = ["pieza", "repuesto", "motor"] }
[targets.appliances.extract.numbers]
units = { patterns = [["(\\\\d)\\\\s?(?:unidades?|uds?)", 1.0]], default = 1 }
[targets.appliances.extract.derived]
price_per_unit = "price / units"
"""

CFG = {**tomllib.loads(BIKES)["targets"], **tomllib.loads(APPLIANCES)["targets"]}


class Bikes(unittest.TestCase):
    def test_frame(self):
        self.assertEqual(extract(CFG["bikes"], "Bicicleta de carbono", "")["frame"], "carbon")
        self.assertEqual(extract(CFG["bikes"], "Bicicleta de aluminio", "")["frame"], "alu")
        self.assertEqual(extract(CFG["bikes"], "Bicicleta de acero", "")["frame"], "acero")
        self.assertIsNone(extract(CFG["bikes"], "Bicicleta", "")["frame"])

    def test_relevant(self):
        self.assertTrue(extract(CFG["bikes"], "Bicicleta de carretera", "")["relevant"])
        self.assertFalse(extract(CFG["bikes"], "Casco de bici", "")["relevant"])
        self.assertFalse(extract(CFG["bikes"], "Rueda de bici", "")["relevant"])

    def test_size_and_derived(self):
        got = extract(CFG["bikes"], "Bicicleta", "Talla 54 cm", price=540.0)
        self.assertEqual(got["size_cm"], 54.0)
        self.assertAlmostEqual(got["eur_per_cm"], 10.0)
        # the derived value needs a price
        self.assertIsNone(extract(CFG["bikes"], "Bicicleta", "Talla 54 cm")["eur_per_cm"])

    def test_condition_from_text(self):
        self.assertEqual(extract(CFG["bikes"], "Bicicleta", "Como nuevo")["condition"], "like_new")
        self.assertEqual(extract(CFG["bikes"], "Bicicleta", "Usada, signos de uso")["condition"], "used")

    def test_condition_from_detail(self):
        # wallapop item show reports condition as a key; it maps onto the same labels
        self.assertEqual(extract(CFG["bikes"], "x", "y", detail={"condition": "good"})["condition"], "used")
        self.assertEqual(extract(CFG["bikes"], "x", "y", detail={"condition": "as_good_as_new"})["condition"], "like_new")
        self.assertIsNone(extract(CFG["bikes"], "x", "y", detail={})["condition"])  # no detail, no condition word in text


class Appliances(unittest.TestCase):
    def test_units(self):
        got = extract(CFG["appliances"], "Lavavajillas", "Vendo 2 unidades", price=240.0)
        self.assertEqual(got["units"], 2.0)
        self.assertAlmostEqual(got["price_per_unit"], 120.0)
        self.assertEqual(extract(CFG["appliances"], "Lavadora", "")["units"], 1.0)  # default

    def test_relevant(self):
        self.assertTrue(extract(CFG["appliances"], "Lavadora 8kg", "")["relevant"])
        self.assertFalse(extract(CFG["appliances"], "Motor de lavadora", "")["relevant"])
        self.assertFalse(extract(CFG["appliances"], "Pieza de frigorifico", "")["relevant"])


if __name__ == "__main__":
    unittest.main()


class PriceFloor(unittest.TestCase):
    def test_min_price_eur_marks_cheap_listings_irrelevant(self):
        cfg = {"min_price_eur": 80}
        self.assertFalse(extract(cfg, "Pixel 9a", "", price=15)["relevant"])
        self.assertTrue(extract(cfg, "Pixel 9a", "", price=80)["relevant"])
        self.assertTrue(extract(cfg, "Pixel 9a", "")["relevant"])  # unknown price: keep
