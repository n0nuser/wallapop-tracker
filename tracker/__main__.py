import argparse, sys
from tracker import collector, dashboard, score


def main(argv=None):
    p = argparse.ArgumentParser(prog="tracker")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="collect once")
    r.add_argument("--db", default="tracker.db")
    r.add_argument("--targets", default="targets.toml")
    r.add_argument("--replay", metavar="DIR", help="read fixtures from DIR instead of the network")

    rep = sub.add_parser("report", help="print bargain thresholds and today's bargains per target")
    rep.add_argument("--db", default="tracker.db")
    rep.add_argument("--targets", default="targets.toml")

    sel = sub.add_parser("sellers", help="backfill seller reputation for every listing's seller")
    sel.add_argument("--db", default="tracker.db")
    sel.add_argument("--targets", default="targets.toml")
    sel.add_argument("--max", type=int, default=0, help="cap the number of sellers fetched (0 = all)")

    img = sub.add_parser("images", help="download the first image of each listing to a local cache")
    img.add_argument("--db", default="tracker.db")
    img.add_argument("--dir", default="images")
    img.add_argument("--max", type=int, default=0, help="cap the number of images downloaded (0 = all)")

    rx = sub.add_parser("reextract", help="recompute specs for stored listings after editing targets.toml")
    rx.add_argument("--db", default="tracker.db")
    rx.add_argument("--targets", default="targets.toml")

    sub.add_parser("daily", help="run, backfill sellers and images, rebuild dashboard.html")

    dash = sub.add_parser("dashboard", help="generate a self-contained dashboard.html")
    dash.add_argument("--db", default="tracker.db")
    dash.add_argument("--targets", default="targets.toml")
    dash.add_argument("--out", default="dashboard.html")
    dash.add_argument("--images-dir", default="images")

    a = p.parse_args(argv)
    if a.cmd == "run":
        return collector.run(a.db, a.targets, a.replay)
    if a.cmd == "report":
        return score.main(["--db", a.db, "--targets", a.targets])
    if a.cmd == "sellers":
        return 0 if collector.backfill_sellers(a.db, a.targets, max_checks=a.max) >= 0 else 1
    if a.cmd == "images":
        return 0 if collector.download_images(a.db, a.dir, max_images=a.max) >= 0 else 1
    if a.cmd == "daily":
        for step in (["run"], ["sellers"], ["images"], ["dashboard"]):
            if main(step):
                return 1
        return 0
    if a.cmd == "reextract":
        print(f"re-extracted {collector.reextract(a.db, a.targets)} listings")
        return 0
    if a.cmd == "dashboard":
        n = dashboard.generate(a.db, a.targets, a.out, a.images_dir)
        print(f"wrote {a.out} ({n} listings)")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
