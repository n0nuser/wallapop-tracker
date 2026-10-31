# ADR 0001: Stack

Status: accepted

## Decision

- **Python 3 stdlib + SQLite.** No framework, no ORM. Third-party dependencies need maintainer approval.
- **uv** runs everything (`uv run tracker ...`).
- **CLI subcommands.** `tracker run` fetches all queries, upserts listings and appends observations. `tracker report` prints bargains and threshold stats. A systemd timer or cron calls `tracker run` daily.
- **One config file**, `targets.toml`. Queries, reject-above, lowball percentage, bargain rule and extraction keywords live there, so adding a target means editing that file. Sketch: [`docs/targets.example.toml`](../targets.example.toml).
- **Rule-first extraction.** Regex and keyword dictionaries per target pull specs from title and description. The LLM pass is off.

## Why

The job is one daily fetch of a few dozen queries into a local file. SQLite and the stdlib cover it with nothing to install or patch on a server. A single config file keeps targets declarative.

## Revisit

- Turn on a cached Haiku pass if rule recall drops below about 80% on a hand-checked sample of listings.
- Add a dependency only when the stdlib version is clearly worse (for example, if HTTP handling needs it; decided after API discovery).

## Out of scope here

Dashboard choice and anti-bot posture get their own ADRs.
