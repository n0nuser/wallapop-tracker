# Glossary

Terms used in code, config and the dashboard. One meaning each.

| Term | Meaning |
|---|---|
| **Target** | A thing we hunt for, defined in `targets.toml`. Owns its queries, extraction rules and bargain thresholds. Adding one is a config edit only. |
| **Query** | One search string run against Wallapop for a target. A target has several (one per brand or concept). |
| **Listing** | One Wallapop ad, identified by its Wallapop id. Deduplicated across queries and targets. Holds current state: price, status, seller, location, extracted specs. |
| **Observation** | One row per listing per run: the price, status and bump marker seen in that run. Append-only. This is the history. |
| **Raw response** | The unparsed body of one fetch, stored immutably with `fetched_at`, query and target. Lets us re-parse after an extractor fix without refetching. |
| **Price drop** | Current observation price is lower than the previous observation's price for the same listing. |
| **Bump** | The seller refreshed the ad so it moves up the results (its modified/created date changes) without a price change. Not a new listing. |
| **Removed / sold** | A listing absent from results (or flagged sold/reserved by Wallapop) in a run where its query was fully fetched. We cannot tell sold from deleted, so one status: `removed`, plus `sold` only when Wallapop says so. |
| **Comparable group** | Listings of one target considered the same product for pricing: same values for the fields in the target's `bargain.group_by` (e.g. model and condition). Bargain stats are computed within a group. |
| **Bargain** | A listing priced at or below its group's threshold — either below a percentile of the comparable group, or below a fixed cap on a target-specific derived value (see below). The threshold is editable per target. A suggestion, never an action. |
| **Reject-above** | Per-target price ceiling. Listings above it are hidden from the default dashboard view and never flagged. |
| **Lowball offer** | Suggested offer price: a set percentage under the asking price, floored at the bargain price. Display only; we never message sellers. |
| **Compatibility** | Whether a listing matches the requirements the target declares (for example a specific connector or protocol), computed by the target's `compatible` rules. Values: `ok`, `no`, `unknown`. |
| **Derived value** | A per-listing number computed from extracted specs and price by the target's `extract.derived` rules (for example price per TB, or price per cm). Used by fixed-threshold bargain rules. |
