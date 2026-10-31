"""Bargain & lowball scorer. Computed at read time from `observation`, never stored.

A listing's "current price" is the price of its latest `observation` row (falling back
to `listing.price_eur` when that observation carried no price), for listings whose
current `status` is `active`. Bargains are computed fresh on every call, so changing a
threshold in `targets.toml` needs no migration (ADR 0002).

Two bargain rules, selected by `bargain.stat` per target:

- `"p25"`: listings are grouped by the `bargain.group_by` specs fields; the median and
  p25 of each group's current prices are computed; a listing is a bargain when
  `price <= factor * median` (`factor` defaults to 0.85, editable as `bargain.factor`).
- any other value: the value names a derived specs field (see the target's
  `extract.derived`), and a listing is a bargain when `specs[stat] <= bargain.max`.

`reject_above_eur` overrides everything: a listing priced above it is never a bargain.
The lowball offer is `max(bargain_threshold, price * (1 - lowball_pct/100))` in `p25`
mode, and `price * (1 - lowball_pct/100)` in threshold mode.

Known gap: the extractor does not emit a `model` (or `brand`) field, so a `group_by`
that names `model` cannot form comparable groups yet. We degrade gracefully: if the
primary grouping field is absent from specs, everything collapses to one group per
target (see `group_info`).
"""
import json, sqlite3, statistics, sys, tomllib
from pathlib import Path

DEFAULT_FACTOR = 0.85


def p25(values):
    """Lower quartile of `values` (25th percentile), same method as statistics.quantiles."""
    return statistics.quantiles(sorted(values), n=4)[0]


def group_info(rows, group_by):
    """Group listings by the `group_by` specs fields that are actually present.

    Returns ``(key_fn, groups, fields)``:
    - ``fields`` — the `group_by` fields actually present in specs (empty when none are).
    - ``groups`` — maps a group key to the list of rows in that group.
    - ``key_fn`` — maps a row to its group key.

    Grouping only makes sense when the primary discriminator (``group_by[0]``) is present
    in specs; when it is missing we fall back to a single group holding every listing
    (one group per target), and ``fields`` is empty.
    """
    specs = [(r.get("specs") or {}) for r in rows]
    fields = [f for f in group_by if any(s.get(f) is not None for s in specs)]
    if not fields or group_by[0] not in fields:
        fields, key_fn = [], (lambda r: None)
    else:
        def key_fn(r):
            return tuple((r.get("specs") or {}).get(f) for f in fields)
    groups = {}
    for r in rows:
        groups.setdefault(key_fn(r), []).append(r)
    return key_fn, groups, fields


def _lowball(price, threshold, pct):
    if price is None:
        return None
    base = price * (1 - pct / 100.0)
    return round(max(threshold, base), 2) if threshold is not None else round(base, 2)


def _not_above(reject_above, price):
    """A listing priced above reject_above_eur is never a bargain."""
    return reject_above is None or price is None or price <= reject_above


def _score_threshold(b, reject_above, lowball_pct, rows):
    """Threshold mode: bargain iff specs[stat] <= max (stat = a derived specs field)."""
    stat = b.get("stat")
    max_val = b.get("max")
    out = []
    for r in rows:
        specs = r.get("specs") or {}
        price = r["price"]
        value = specs.get(stat)
        bargain = bool(value is not None and max_val is not None and value <= max_val
                       and _not_above(reject_above, price))
        out.append({"id": r["id"], "title": r["title"], "url": r["url"], "price": price,
                    "metric": value, "threshold_eur": None, "is_bargain": bargain,
                    "lowball_offer": _lowball(price, None, lowball_pct)})
    return out


def _score_p25(b, reject_above, lowball_pct, rows):
    factor = float(b.get("factor", DEFAULT_FACTOR))
    group_by = b.get("group_by", [])
    min_group = b.get("min_group_size", 1)
    key_fn, groups, fields = group_info(rows, group_by)
    stats = {}
    for key, grp in groups.items():
        prices = [r["price"] for r in grp if r["price"] is not None]
        if len(prices) < min_group:
            stats[key] = None
            continue
        med = statistics.median(prices)
        stats[key] = {"size": len(grp), "median": med, "p25": p25(prices),
                      "threshold_eur": factor * med}
    out = []
    for r in rows:
        st = stats.get(key_fn(r))
        threshold = st["threshold_eur"] if st else None
        bargain = bool(st and r["price"] is not None and r["price"] <= threshold
                       and _not_above(reject_above, r["price"]))
        out.append({"id": r["id"], "title": r["title"], "url": r["url"], "price": r["price"],
                    "metric": None, "threshold_eur": threshold, "is_bargain": bargain,
                    "lowball_offer": _lowball(r["price"], threshold, lowball_pct)})
    return out, stats, fields


def active_listings(con, target):
    """Active listings of `target` with their current price = latest observation's price."""
    rows = []
    for lid, title, url, price_eur, last_price, specs in con.execute(
            "SELECT l.id, l.title, l.url, l.price_eur, "
            "(SELECT o.price_eur FROM observation o WHERE o.listing_id = l.id "
            " ORDER BY o.id DESC LIMIT 1), l.specs "
            "FROM listing l WHERE l.target = ? AND l.status = 'active'", (target,)):
        price = last_price if last_price is not None else price_eur
        if price is None:
            continue
        rows.append({"id": lid, "title": title, "url": url, "price": price,
                     "specs": json.loads(specs) if specs else {}})
    return rows


def score_target(target_cfg, rows):
    """Score one target's active listings. Returns a summary dict (see `render_target`)."""
    b = target_cfg.get("bargain", {})
    reject_above = target_cfg.get("reject_above_eur")
    lowball_pct = float(target_cfg.get("lowball_pct", 0))
    stat = b.get("stat")
    if stat == "p25":
        listings, stats, fields = _score_p25(b, reject_above, lowball_pct, rows)
        factor = float(b.get("factor", DEFAULT_FACTOR))
        group_by = b.get("group_by", [])
        note = ("primary grouping field not extracted; group_by=%s fell back to one group "
                "per target" % (group_by,)) if not fields else None
        groups = [{"key": k, **v} for k, v in stats.items() if v]
        return {"stat": stat,
                "thresholds": {"factor": factor, "group_by": b.get("group_by"),
                               "min_group_size": b.get("min_group_size"),
                               "reject_above_eur": reject_above, "lowball_pct": target_cfg.get("lowball_pct")},
                "groups": groups, "fields": fields,
                "listings": listings, "note": note}
    if stat:
        return {"stat": stat,
                "thresholds": {"max": b.get("max"), "reject_above_eur": reject_above,
                               "lowball_pct": target_cfg.get("lowball_pct")},
                "groups": [], "fields": [],
                "listings": _score_threshold(b, reject_above, lowball_pct, rows), "note": None}
    return {"stat": stat, "thresholds": {}, "groups": [], "fields": [],
            "listings": [], "note": "unknown bargain.stat"}


def _fmt(v):
    return "—" if v is None else f"{v:,.2f}".rstrip("0").rstrip(".")


def render_target(name, score):
    """One target's report as text."""
    lines = [f"== target: {name} ==", f"bargain.stat = {score['stat']}"]
    for k, v in score["thresholds"].items():
        lines.append(f"  {k} = {v}")
    if score["note"]:
        lines.append(f"  note: {score['note']}")
    for g in score["groups"]:
        if g["key"] is None:
            label = "all listings"
        else:
            label = ", ".join(f"{f}={v}" for f, v in zip(score["fields"], g["key"]))
        lines.append(f"  group [{label}]: n={g['size']} median={_fmt(g['median'])} "
                     f"p25={_fmt(g['p25'])} threshold={_fmt(g['threshold_eur'])}")
    bargains = [l for l in score["listings"] if l["is_bargain"]]
    lines.append(f"bargains: {len(bargains)}")
    for l in sorted(bargains, key=lambda x: x["price"]):
        metric = f"  {score['stat']}={_fmt(l['metric'])}" if l["metric"] is not None else ""
        lines.append(f"  - {l['title']}  €{_fmt(l['price'])}{metric}  lowball={_fmt(l['lowball_offer'])}  {l['url']}")
    return "\n".join(lines)


def report(db, targets="targets.toml", out=sys.stdout):
    """Read the DB + targets.toml and print, per target, thresholds and today's bargains."""
    cfg = tomllib.loads(Path(targets).read_text())
    con = sqlite3.connect(db)
    try:
        for name, tcfg in cfg.get("targets", {}).items():
            rows = active_listings(con, name)
            print(render_target(name, score_target(tcfg, rows)), file=out)
    finally:
        con.close()


def main(argv=None):
    import argparse
    p = argparse.ArgumentParser(prog="tracker report")
    p.add_argument("--db", default="tracker.db")
    p.add_argument("--targets", default="targets.toml")
    a = p.parse_args(argv)
    report(a.db, a.targets)
    return 0
