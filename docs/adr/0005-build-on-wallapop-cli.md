# ADR 0005: Build the collector on wallapop-cli

Status: accepted

## Decision

The collector fetches listings by shelling out to `wallapop` (wallapop-cli, v0.4.0, from https://github.com/Microck/wallapop-cli) instead of talking to Wallapop's HTTP API directly. It uses three read-only commands: `wallapop search`, `wallapop item show` and `wallapop user show` (seller reputation). No login, no token.

## Why

wallapop-cli already implements the API's endpoints, request shape, pagination and error handling, and it is maintained outside this repo. Reimplementing those in our own client meant keeping our own token, pagination and anti-bot code in step with an API we do not control. `search` and `item show` need no credentials, so we add no secrets.

## What it replaces

- `tracker/client.py`: our `urllib` client, the pagination-token handling and the block detection. Deleted.
- 404-only removal detection: a listing is now gone either by `item show` exit 4 (not found) or by the `sold`, `reserved` or `expired` flags in the item body.

## What we keep

The store (ADR 0002), the append-only history, the rule-based extractor and the future scorer are unchanged. The collector still enforces its own pacing between calls, because the CLI has none.

## How the guardrails map to the CLI

- **Read-only.** Only `search`, `item show` and `user show`. No `auth`, `chat`, `watch`, `favorite` or listing actions.
- **Pacing.** One page per process (`--pages 1`, then `--next <token>`), and at least 3 s between any two CLI calls.
- **No proxy.** `WALLAPOP_PROXY` is removed from the environment.
- **Honest User-Agent.** The CLI sends `wallapop-cli/0.4.0`. After a CloudFront 403 it retries once per process with a browser User-Agent and prints a notice on stderr. The collector treats that notice as a block: it stores the body it already got, stops the run and exits with a block code, so the retry never turns into continued scraping.
- **Stop codes.** CLI exit 5 (blocked or rate limited) stops the run and records no missing or removed. Exit 7 (API changed) and exit 6 (network) also stop the run, keeping what was already stored.

## `watch` was considered and rejected

The CLI ships a `watch` command that tracks listings over time. We do not use it: the tracker needs the raw response bodies and the full observation history, which `watch` does not expose. We keep `raw_response` immutable and append to `observation`.

## Consequences

- Fixes to Wallapop's API land in the CLI, and we pick them up by upgrading it.
- `raw_response.http_status` now records the CLI's result rather than a wire HTTP status (200 for a successful call, 404 for a listing the CLI reported gone). `raw_response.body` is the CLI's stdout JSON, not the wire bytes.
- `docs/api.md` becomes background reference: it records the API that the CLI implements.
