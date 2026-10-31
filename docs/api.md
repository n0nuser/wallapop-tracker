# Wallapop internal API (observed 2026-10-31)

> Background reference. The collector no longer calls this API directly — it shells out to `wallapop` (wallapop-cli), which implements these endpoints (see ADR 0005). This document records the API shape for the fixtures and for understanding the CLI's behaviour.

No public API exists. The web app at es.wallapop.com calls `api.wallapop.com`. Everything below was read from the browser network tab and re-checked with plain `urllib`. Four live requests were made, all HTTP 200.

## Search

`GET https://api.wallapop.com/api/v3/search/section`

First page query params:

| Param | Value | Notes |
|---|---|---|
| `keywords` | free text | |
| `latitude`, `longitude` | e.g. `40.4168`, `-3.7038` | Honoured by the API. The web page ignores them from the URL and sends its own default location, so pass them to the API directly. |
| `order_by` | `newest` | Other values not tested. |
| `source` | `search_box` | The web app sends `deep_link` or `search_box`. Both work. |
| `search_country` | `ES` | |
| `search_section`, `section_type` | `organic_search_results` | |

Not tested: `category_id`, price filters, radius (`distance_in_km`).

Response: `{"data": {"section": {"type", "items": [...]}, "tracking": ...}, "meta": {"next_page": "<token>"}}`.

## Pagination

Take `meta.next_page` and call the same endpoint with only `?next_page=<token>`. The token is a signed JWT (HS256). Its payload holds the original params plus `step`, `pagination_date` and `exp` (about 10 minutes).

Pages are **time windows, not fixed sizes**. Each step moves `pagination_date` back one day (then a week), so a page can hold 0 to ~17 items. An empty page still returns a `next_page`. Observed: the first page of one search had 0 items and the third had 12. A client must follow tokens until it has enough items or `next_page` is missing, not stop at the first empty page. The web app fetches about 10 steps in a burst on page load.

## Item detail

`GET https://api.wallapop.com/api/v3/items/{id}` (id from the search row). Adds `type_attributes.condition` (e.g. `good`), brand, `counters` (views, favourites), `location.approximated`, `price.cash.amount` and a `share_url`. Prices nest differently: `price.cash.amount` here, `price.amount` in search. Condition is the only useful field missing from search; fetch details only for listings that pass the cheap filters.

## Headers

Sent by the browser: `User-Agent`, `Accept`, `Accept-Language`, `Referer`, `X-DeviceOS: 0`, plus tracking ids (`X-DeviceId`, `MPID`, `X-AppVersion`). The tracking ids are not needed: requests with a browser UA, `Accept`, `Accept-Language`, `X-DeviceOS` and `Referer` returned 200. No cookies, no login.

## Search item fields

`id`, `user_id`, `title`, `description` (full text), `category_id`, `price{amount,currency}`, `images[]`, `reserved{flag}`, `location{latitude,longitude,postal_code,city,region,region2,country_code}`, `shipping{item_is_shippable,user_allows_shipping}`, `bump{type}`, `web_slug`, `created_at` / `modified_at` (epoch ms), `taxonomy[]`, `is_refurbished`, `has_warranty`, `is_top_profile`.

Missing from search: distance (computed as haversine from the query point), seller rating, sold flag. Sold or removed listings simply stop appearing; the collector marks such listings `missing` and confirms via `item show` (see ADR 0002). Item URL: `https://es.wallapop.com/item/{web_slug}`.

## Observed limits

- No 403, 429 or captcha seen. No rate-limit headers (`Cache-Control: no-cache`, served via CloudFront).
- Not stress-tested on purpose. Policy is in [ADR 0004](adr/0004-anti-bot-posture.md).

## Fixtures

`tests/fixtures/search_*.json` are unmodified bodies. The `p<N>` suffix is the step number inside that capture. Most come from browser captures (same endpoint, same responses); `item_*.json` are live detail responses. `expected_*.json` pins the parser output.
