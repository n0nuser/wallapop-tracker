"""Seed a synthetic prior-day run so sparklines and the price-drop / new-today
filters have more than one data point to show. Throwaway demo aid for the
dashboard prototype; the fixture replay alone stores a single observation
per listing (one run), which would render every sparkline as one dot.

Run once:  python3 proto/seed_demo.py   (copies tracker.db -> proto-demo.db)
"""
import datetime as dt
import shutil
import sqlite3
from pathlib import Path

SRC, DST = Path("tracker.db"), Path("proto-demo.db")
shutil.copy(SRC, DST)
con = sqlite3.connect(DST)

rows = con.execute("SELECT id, price_eur, first_seen FROM listing ORDER BY id").fetchall()

# Deterministic deltas: a few drops, a few rises, mostly unchanged, so the
# price-drop filter and the sparkline slope are visible.
deltas = [0.90, 1.00, 1.00, 1.10, 1.00, 0.85, 1.00, 1.05, 1.00,
          1.00, 0.95, 1.00, 1.12, 1.00, 1.00, 1.00, 1.08, 1.00, 0.92]

# Back-date first_seen for a handful so "new today" is a real discriminator.
not_new_today = {4, 5, 6, 9, 13, 16}
yesterday = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1)
three_days = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=3)
run_at = yesterday.isoformat(timespec="milliseconds")

for i, (lid, price, first_seen) in enumerate(rows):
    if i in not_new_today:
        con.execute("UPDATE listing SET first_seen=? WHERE id=?",
                    (three_days.isoformat(timespec="milliseconds"), lid))
    if price is not None:
        prev = round(price * deltas[i], 2)
        con.execute(
            "INSERT INTO observation(listing_id, run_at, price_eur, status, modified_at) "
            "VALUES(?,?,?,?,?)",
            (lid, run_at, prev, "active", None))

con.commit()
n = len(rows)
print(f"seeded {n} prior observations and back-dated {len(not_new_today)} first_seen -> {DST}")
