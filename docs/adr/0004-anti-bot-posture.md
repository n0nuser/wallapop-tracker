# ADR 0004: Anti-bot posture

Status: accepted

## Context

Wallapop has no public API. The collector reads it through `wallapop` (wallapop-cli), which talks to the internal search and item JSON endpoints. We expect rate limits and bot checks, and we want to stay a quiet, read-only client.

## Decision

Rules for every collector:

- Read-only. Public search and item endpoints only. No login, no messaging sellers, no buying.
- The CLI's honest User-Agent (`wallapop-cli/0.4.0`). A browser User-Agent is only used by the CLI's own one-time fallback, and we treat that as a block (see below).
- At least 3 s between any two CLI calls, enforced by the collector (the CLI has no pacing of its own).
- On a block, stop. The run ends and records no missing or removed. A block is CLI exit 5 (blocked or rate limited), or the CLI's stderr notice that it retried once with a browser User-Agent after a CloudFront 403.
- No evasion: no fingerprint spoofing, no proxies (`WALLAPOP_PROXY` is stripped), no challenge solvers (FlareSolverr is out).
- If blocked, report it and wait for a human decision.

## What we observed

During discovery we made 4 live `urllib` requests, spaced 4 s apart. All returned 200 with no challenge. Headers that mattered: none in particular. A browser User-Agent, `Accept`, `Accept-Language`, `Referer` and `X-DeviceOS: 0` were enough; the browser's tracking ids were not needed. We saw no rate-limit headers. We have not found the threshold, because finding it means getting blocked. This discovery now flows through wallapop-cli (ADR 0005); the observations are the API record in `docs/api.md`.

## Request budget

Each query is followed for at most `max_pages_per_query = 5` pages, plus up to `max_detail_checks_per_run = 10` item checks for listings that went missing. `max_requests_per_run = 150` is a hard stop; the run logs it and ends cleanly. At the 3 s gap a full run takes a few minutes. Pagination steps are date windows (a day, then a week), so 5 pages reach back days to a couple of weeks, enough for a daily run. A normal page view in the web app makes about 10 search calls on its own.

Live check: one query, 2 pages, both HTTP 200.

## Consequences

- A block ends the daily run and is surfaced to the user, not worked around.
- Pagination tokens expire after about 10 minutes, so each query's pages are fetched in one sitting.
- Item-detail calls are limited to confirming listings that went missing, to keep the request count low.
- The tracking ids the browser sends are not used, so we look like an anonymous client, not a spoofed browser.
