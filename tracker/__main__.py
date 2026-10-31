import argparse, sys
from tracker import collector, score

p = argparse.ArgumentParser(prog="tracker")
sub = p.add_subparsers(dest="cmd", required=True)
r = sub.add_parser("run", help="collect once")
r.add_argument("--db", default="tracker.db")
r.add_argument("--targets", default="targets.toml")
r.add_argument("--replay", metavar="DIR", help="read fixtures from DIR instead of the network")
rep = sub.add_parser("report", help="print bargain thresholds and today's bargains per target")
rep.add_argument("--db", default="tracker.db")
rep.add_argument("--targets", default="targets.toml")
a = p.parse_args()
if a.cmd == "run":
    sys.exit(collector.run(a.db, a.targets, a.replay))
sys.exit(score.main(["--db", a.db, "--targets", a.targets]))
