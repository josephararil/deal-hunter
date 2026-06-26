# Deal Hunter

Finds great-value hotel trips from cities reachable from Plovdiv — not just lone underpriced
hotels, but whole cities that are unusually cheap right now. Three small pipelines, an
**LLM sandwich**: Claude steers the search at the front, deterministic math does the heavy
filtering in the middle, Claude does the final harsh judgment at the end. Runs on free
GitHub Actions. Emails a weekly digest.

## The three pipelines

```
baseline_sampler.py   (daily, cheap, no LLM)
   └─ light price sample per city → rolling median per city|class|month → state/baselines.json
        This is the seasonal memory: "what does a 5-star in Burgas normally cost in July?"

find_city_anomalies.py   (daily, cheap — Pipeline A, "where should I look?")
   └─ ONE Claude call + web search, given today's date + pattern priors + baselines
   └─ outputs ranked city signals → state/city_signals.{json,md}
        Two kinds: 🔥 anomaly (something unusual is happening now) and 📌 reminder
        (a recurring good window is open). Useful on its own — tells you where to look
        even if you never run the heavy crawl.

hunt.py   (daily, only on flagged cities — Pipeline B, "find the actual rooms")
   └─ deep-crawls the cities Pipeline A flagged for hunting
   └─ TWO detectors: cross-sectional outlier (crazy manager) + below-seasonal-norm
      (market-wide drop & absolute bargains)
   └─ ONE harsh Claude call vets the shortlist, told why the city was flagged
   └─ accumulates → weekly email digest
```

The auto-trigger means B only spends money on cities A flagged — usually none to a few a day,
not all 34.

## What it catches now (vs the first version)

The earlier design only caught the "crazy manager" — one hotel far below its neighbours. It
went silent exactly when a *whole city* dropped (Antalya after New Year, Milan after fashion
week), because if everything drops together, nothing is a relative outlier. This version adds:
- **Market-wide drops**, via the seasonal baseline (a Hilton at €50 in a city whose 5-star
  norm is €160 gets flagged even if the Hyatt next door is €55).
- **Absolute bargains**, ranked by EUR saved so luxury-for-cheap rises to the top.
- **City reminders**, so even with the heavy crawl off, you learn *where* to go look manually.

## Setup

1. Push this repo to GitHub.
2. Add secrets under *Settings → Secrets and variables → Actions*:

   | Secret | What |
   |---|---|
   | `APIFY_TOKEN` | apify.com → API tokens |
   | `ANTHROPIC_API_KEY` | console.anthropic.com |
   | `SMTP_HOST` / `SMTP_PORT` | e.g. `smtp.gmail.com` / `587` |
   | `SMTP_USER` / `SMTP_PASS` | sending address + app password (Gmail: 2FA → App Password) |
   | `EMAIL_TO` / `EMAIL_FROM` | recipient / sender (both default to `SMTP_USER`) |

3. Enable Actions. Test:
   - **daily** workflow → *Run workflow* → tick `force_digest` to email immediately.
   - **manual-hunt** workflow → enter cities (e.g. `Antalya, Turkey`) to crawl on demand.

> The web-search step in Pipeline A uses the Anthropic API's `web_search` tool. If your
> account needs it enabled, do that in the console.

## The one fiddly step: map the Apify actor fields

Each Booking actor names fields differently. Run your chosen actor once by hand and confirm
the input keys in `common.scrape()` and the output keys in `common.normalize()`
(`price`, `reviewScore`, `reviewsCount`, `stars`, `mealPlan`…). Rename to match. That's the
only integration work; everything downstream is source-agnostic.

## Cold start

For the first few weeks `baselines.json` is sparse, so the market-drop detector leans on
Pipeline A's reasoning (pattern priors + live search) rather than measured norms. As the
baseline fills, market-drop detection gets sharper and more data-driven. Expect a louder,
less precise first month — that's expected and was a deliberate choice.

## Tuning (config.py)

| Knob | Default | Effect |
|---|---|---|
| `OUTLIER_Z` | −3.5 | Crazy-manager strictness (more negative = stricter) |
| `MARKET_DROP_PCT` | 0.25 | How far below seasonal norm counts as a market drop |
| `MIN_EUR_BELOW` | 40 | Min absolute EUR/night saving to qualify |
| `LLM_CONFIDENCE` | 0.7 | Final-filter strictness |
| `MAX_DIGEST_ITEMS` | 12 | Weekly email length cap |
| `DIGEST_WEEKDAY` | 6 (Sun) | Day the digest sends |
| `MIN_REVIEW_SCORE` | 8.0 | Hard floor — don't lower |

## Cost

Claude is cheap (a couple of calls a day). **Apify dominates** (pay-per-result + residential
proxy). The architecture already controls it: the baseline sampler uses light crawls, and the
deep crawl only fires on flagged cities. Set an Apify spend limit in the console as a backstop.
The seasonal dates in `hunt.checkin_anchors()` and `patterns.json` need a yearly refresh.

## patterns.json

Seeded with recurring price-window priors for your cities plus a few extras (Paris August,
Dubai summer, etc.). These are **priors, not facts** — the planner confirms or rejects them
with live search and baseline data. Prune and add freely.

See `docs/PLAN.md` for design rationale and the Phase-2 roadmap.
