# Dormant: Apify / Hotel-Crawl Pipeline

These files are the original hotel-crawling pipeline. They are **intentionally dormant** —
separated from the active diamond-finder so the live codebase stays clean and unambiguous.
Nothing here runs automatically or is imported by any active script.

## What's here

| File | Original role |
|---|---|
| `hunt.py` | Apify crawl + dual statistical detector + weekly email digest |
| `baseline_sampler.py` | Daily light crawl to fill `state/baselines.json` with price medians |
| `patterns.json` | Seeded recurring price-window priors used by the old planner prompt |
| `workflows/manual-hunt.yml` | GitHub Actions: crawl named cities on demand |
| `workflows/signals.yml` | GitHub Actions: signals-only run, no crawl or email |

## Why dormant, not deleted

The Apify pipeline represents a meaningful amount of design work (dual detector, robust
z-score, seasonal baselines) that may be worth resurrecting if the LLM-only diamond finder
proves insufficient. Keeping the code archived avoids losing that context.

## Re-enabling requires deliberate, non-trivial work

This is not a "plug back in" operation. At minimum you would need to:

1. Restore hunt-only config knobs removed from `config.py` (see git history):
   `ADULTS`, `CHILDREN`, `ROOMS`, `CHILDREN_AGES`, `PROXY_GEO`, `CURRENCY`,
   `MIN_REVIEW_SCORE`, `MIN_REVIEW_COUNT`, `BASELINE_MIN_REVIEWS`,
   `OUTLIER_Z`, `MIN_EUR_BELOW`, `MARKET_DROP_PCT`, `LLM_CONFIDENCE`, `MIN_PEERS`,
   `DIGEST_WEEKDAY`, `MAX_DIGEST_ITEMS`, `SEEN_TTL_DAYS`,
   `APIFY_ACTOR`, `SCRAPE_MAX_HUNT`, `SCRAPE_MAX_BASE`, `MODEL_PLANNER`, `MODEL_FILTER`
2. Restore `scrape()` and `normalize()` in `common.py` (also removed; see git history)
3. Obtain and configure an Apify subscription and `APIFY_TOKEN` secret
4. Decide how `hunt.py` and `find_city_anomalies.py` relate to each other in the new design
5. Update `daily.yml` to run `baseline_sampler.py` and `hunt.py`

Treat restoration as a new feature, not a rollback.
