# Deal Hunter

A quiet pipeline that hunts for hotels priced as **statistical outliers against their own class** — the deliberate underpricing that happens when a manager dumps inventory (underbooking, post-holiday troughs, charter overstock). It ranks finds by **absolute EUR saved per night**, runs one harsh LLM filter so you aren't spammed, and **emails you a weekly digest** of only the genuinely special finds.

It's a single Python script on a free GitHub Actions cron. No database, no server.

## How it works

```
daily cron → deal_hunter.py
   ├─ for each (city × check-in anchor): pick a random trip length in the city's range,
   │                                     then scrape hotels via Apify
   ├─ drop anything under an 8.0 review score (hard floor)
   ├─ group by star class, find robust outliers within each group   ← apples to apples
   ├─ keep only outliers >= EUR 40/night below their peers
   ├─ rank by absolute EUR saved (luxury-for-cheap rises to the top)
   ├─ ONE harsh Claude call → keep only the truly exceptional, favouring all-inclusive
   ├─ accumulate the week's fresh finds (dedup via seen.json)
   └─ on digest day → email the top finds, then clear the queue
```

Core idea: **qualify by statistical anomaly, rank by absolute money saved.** A robust
z-score asks "did someone deliberately underprice this?"; the EUR-below-peers figure asks
"how much do I care?". Ranking by absolute EUR favours a EUR 140/night saving on a luxury
hotel over a EUR 50/night saving on a budget one.

## Weekly digest, daily crawl

The crawl runs **every day** so ephemeral deals get caught, but the email goes out **once
a week** (`DIGEST_WEEKDAY`, default Sunday). To stop a weekday find from expiring before
the digest lands, the near-term search horizons are 10/17/24 days out. Truly short-fuse
"in 3 days" dumps are intentionally out of scope under a weekly cadence.

## Trip length per city

`CITIES` maps each city to `(min_nights, max_nights)`. Each run picks a random length in
range, so over time you sample many trip lengths and surface the best deal at any of them,
instead of hardcoding one. Minimums encode "worth the trip" (e.g. Edirne 2–3, Vienna 2–7,
Istanbul 3–6). Edit freely.

## Setup

1. **Create the repo** and push these files.
2. **Add secrets** under *Settings → Secrets and variables → Actions*:

   | Secret | What |
   |---|---|
   | `APIFY_TOKEN` | apify.com → Settings → API tokens |
   | `ANTHROPIC_API_KEY` | console.anthropic.com |
   | `SMTP_HOST` | e.g. `smtp.gmail.com` |
   | `SMTP_PORT` | `587` |
   | `SMTP_USER` | your sending address |
   | `SMTP_PASS` | app password (Gmail: enable 2FA → create an App Password) |
   | `EMAIL_TO` | where the digest goes (defaults to `SMTP_USER`) |
   | `EMAIL_FROM` | sender (defaults to `SMTP_USER`) |

3. **Enable Actions**, then test: Actions tab → *deal-hunter* → *Run workflow* →
   tick **force_digest** to send a test email immediately.

> Gmail works fine via an App Password. Any SMTP provider (Fastmail, your own, etc.) works too.

## The one fiddly step: map the actor fields

Every Apify Booking actor names its fields slightly differently. Before the first real run,
run your chosen actor once by hand and confirm:
- **Input** names in `scrape()` (`search`, `checkIn`, `children`, `childrenAges`, …)
- **Output** keys in `normalize()` (`price`, `reviewScore`, `reviewsCount`, `stars`, `mealPlan`, …)

Rename to match. That's the only integration work.

## Tuning for silence

| Knob | Default | Effect |
|---|---|---|
| `OUTLIER_Z` | −3.5 | More negative = only flag more extreme outliers |
| `MIN_EUR_BELOW` | 40.0 | Raise to ignore small absolute savings |
| `LLM_CONFIDENCE` | 0.75 | Raise toward 0.9 if junk gets through |
| `MAX_DIGEST_ITEMS` | 10 | Cap the weekly email length |
| `DIGEST_WEEKDAY` | 6 (Sun) | Day the digest is sent |
| `MIN_REVIEW_SCORE` | 8.0 | The hard floor; don't lower it |

## Cost

Claude is the cheap part (cents/day). **Apify is the cost** (pay-per-result + residential
proxy). Levers:
- Set an **Apify spend limit** in the console.
- **Rotate cities by weekday** to cut spend ~7×. Near the top of `main()`:
  ```python
  cities = list(CITIES.items())
  cities = [c for i, c in enumerate(cities) if i % 7 == dt.date.today().weekday()]
  for city, (lo, hi) in cities:
  ```

## Maintenance

- Update the seasonal dates in `checkin_anchors()` each January.
- `seen.json` and `pending_digest.json` are committed automatically; leave them in the repo.

See `docs/PLAN.md` for design rationale, trade-offs, and the Phase-2 roadmap (travel packages).
