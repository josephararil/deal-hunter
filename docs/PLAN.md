# Design Notes & Roadmap

## Cross-sectional, not time-series
The baseline is the peer group of comparable hotels *in the same crawl*, not a stored price
history. When you manually filter a country to "5-star, 8+, pool" and scroll for the one
that's too cheap for its rating, the other hotels on screen ARE the baseline. This makes the
system stateless — no database, just a script.

## Qualify by anomaly, rank by absolute EUR
- **"Did a manager deliberately underprice this?"** → robust z-score (median + MAD) within
  the star class. MAD instead of standard deviation so a few weird listings don't poison the
  baseline. z ≤ −3.5 = absurdly cheap for its peers.
- **"How much do I care?"** → absolute EUR below the peer median. This is the ranking key and
  deliberately favours luxury-for-cheap: EUR 140/night off the InterContinental beats EUR 50
  off a budget place, even at a lower percentage. The LLM also leans on brand knowledge — if
  it recognises a property whose true rate is far above the local median, the real saving is
  bigger than the raw number.

The 8.0 review score is the only hard *filter*. Star rating is used purely for grouping, so a
genuinely good 3-star find still surfaces within the 3-star group.

## All-inclusive bias
All-inclusive properties carry the highest fixed costs and run the deepest, most genuine
fire-sales, so they surface naturally — and the LLM prompt is told to favour them as more
likely to be real. (Board type isn't perfectly reported by every actor, so this is a soft
nudge, not a hard rule.)

## Randomized trip length
Each city has a `(min, max)` nights range; each run picks a random length. Over time this
samples many lengths and finds the best deal at any of them, instead of hardcoding one.
Minimums encode "worth the trip" — longer for far places (Istanbul 3+, UK 3+), short for
nearby towns.

## Weekly digest vs ephemeral deals (the real trade-off)
A weekly email is calmer than real-time pings, but a deal found mid-week for a stay "in 4
days" would be dead by digest time. Mitigation: the crawl runs daily and near-term horizons
are pushed to 10/17/24 days, so weekday finds survive to the digest. Genuinely short-fuse
dumps (stay in 1–3 days) are out of scope under a weekly cadence. If that turns out to cost
real money, the Day-2 lever is an immediate "urgent" email for anything above a high absolute
threshold, alongside the weekly digest.

## Why a single harsh LLM call
Cheap math is good at "which numbers are outliers" and bad at "is this a real, bookable,
non-scammy deal." The LLM is the reverse. Math scans thousands of rows; one batched Claude
call does the scam-review on the handful of survivors — the step that eats your time
manually. The prompt rejects by default; silence is the expected output most days.

## Accepted trade-offs (Pareto)
- **Board type** unreliable → a room-only rate can masquerade as a steal; LLM catches most.
- **Thin crawls** — towns with fewer than `MIN_PEERS` hotels in a class are skipped rather
  than guessed. (Future: fall back to the LLM's absolute prior when there's no peer group.)
- **Star data** occasionally wrong on Booking; review floor + LLM backstop absorb most of it.
- **Session/geo pricing** pinned via EUR + BG proxy; don't change casually.

## Open questions parked for later
1. **Flights** for fly-to cities — deferred to Day 2; revisit if you keep seeing Milan/UK
   hotel deals you don't act on because flights are expensive.
2. **Urgent override** — same-day email for outrageous short-fuse finds, if the weekly
   cadence misses too much.

## Phase 2 — Travel packages
The richest arbitrage (operators dumping unsold flight+hotel charters near departure) is the
hardest to source: no aggregator, inventory scattered across operator sites (TUI, Coral, Anex,
Bulgarian-market operators), much of it JS-heavy. Approach when ready: a separate but
architecturally identical pipeline — pick 3–5 operators serving Bulgaria, scrape their
package listings on near-term departures, flag packages whose per-person-per-night cost is
far below the operator's normal pricing or the DIY flight+hotel equivalent. JS-heavy sites
may need a browser agent (Claude in Chrome or a Playwright MCP). Crawl → outlier → harsh LLM
filter → digest, pointed at different sources. Ship the hotel hunter first, live with it a
month, then bolt this on.
