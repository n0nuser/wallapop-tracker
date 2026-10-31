# proto/ — dashboard prototype

Throwaway comparison of two dashboard approaches; the decision is in
[`docs/adr/0003-dashboard.md`](../docs/adr/0003-dashboard.md).

- `make_dashboard.py` — **approach B (chosen)**: stdlib `sqlite3` + a string template
  → `dashboard.html`. All filters live in-page; inline-SVG price-history sparkline.
  Run: `python3 proto/make_dashboard.py` (reads `tracker.db`).
- `datasette-metadata.json` — **approach A (rejected)**: Datasette canned queries.
  Serve with a throwaway venv, e.g.
  `datasette tracker.db --metadata proto/datasette-metadata.json`.
- `seed_demo.py` — synthetic prior-day run so sparklines / price-dropped / new-today
  have more than one point (a single run stores one observation per listing).

To rebuild a demo DB from the saved fixtures:

```sh
python3 -m tracker run --replay tests/fixtures/cli --db tracker.db
python3 proto/seed_demo.py          # adds synthetic prior-day history
python3 proto/make_dashboard.py     # proto/dashboard.html
```

`tracker.db`, `proto-demo.db` and `dashboard.html` are generated (ignored).
