# Design Notes & Roadmap

## The problem with v1, and the fix
The first design did cross-sectional outlier detection only: flag a hotel far below its
same-class peers in the same crawl. That catches a "crazy manager" but is blind to the bigger,
more common prize — a *whole city/class* dropping (Antalya post-NYE, Milan post-fashion-week,
a destination after a shock). When everything drops together the median drops with it, so
nothing is a relative outlier and the system goes quiet exactly when the deals are best.

Two additions fix it, and they're the same fix from two ends:
1. A **seasonal baseline** (city|class|month medians) gives an external reference, so "cheap
   even for this season" becomes measurable — that's the market-wide-drop and absolute-bargain
   detector.
2. An **LLM planner at the front** supplies that reference before the baseline matures, using
   recurring-pattern knowledge + live web search, and steers the expensive crawl to where the
   deals likely are.

## The LLM sandwich
- **Front (Pipeline A):** Claude reasons about *where* to look — recurring troughs and live
  shocks — and ranks cities. Cheap, runs daily, valuable on its own as a "go look here" memo.
- **Middle (deterministic):** crawling + both detectors + absolute-EUR ranking. No LLM; scales
  to thousands of rows; numerically reliable.
- **Back (Pipeline B filter):** one harsh Claude call judges the shortlist against seasonal
  context and rejects boring low-season non-deals.

Why not let the LLM do detection over all the rows? It's worse at precise numeric outlier
detection than one line of math, and far more expensive. Math scans; the LLM judges.

## Split into three scripts, auto-triggered
- `baseline_sampler.py` — continuous cheap memory. Separated from hunting because *recording a
  median* needs a light scrape and no LLM, while *finding bookable rooms* needs deep crawls and
  judgment. Keeping the cheap part always-on means the baseline matures even though the
  expensive part runs rarely.
- `find_city_anomalies.py` (A) — the planner. Output is independently useful (the reminder/
  teacher Marti asked for): even with B disabled, it tells you which city to manually search.
- `hunt.py` (B) — auto-triggered only on the cities A flags (`hunt: true`), or manually via the
  `manual-hunt` workflow. Cost scales with real signals, not with the city list.

## Reminders vs anomalies
A drop that recurs every year (shoulder-season coast) isn't an anomaly — it's a known pattern.
Pipeline A tags those `reminder` (heads-up, go do your manual thing) and tags genuine current
oddities `anomaly` (worth a deep crawl). Both appear in `city_signals.md`; only `hunt: true`
ones trigger B.

## Qualify by anomaly, rank by absolute EUR
Detection qualifies a candidate (statistical outlier OR below seasonal norm); ranking is always
by absolute EUR saved per night, which favours luxury-for-cheap (€140 off a 5-star beats €50
off a budget place) — exactly the priority Joseph specified.

## patterns.json are priors, not facts
The Antalya example is instructive: New Year is a price *peak*; the drop is the days *after*.
Seasonal knowledge is directionally reliable but the LLM's absolute price memory is weak and
frozen at its cutoff. So patterns carry `confidence`, and the planner is told to confirm/reject
them with live search and baseline data rather than trust them blindly. Soft expectations steer;
the deterministic detectors and the maturing baseline are the spam-proof backbone.

## Accepted trade-offs (Pareto)
- **Cold start:** market-drop detection is fuzzy until the baseline fills (a few weeks). A
  louder, less precise first month was an accepted choice.
- **Board type** unreliable from some actors → the final LLM catches room-only-posing-as-more.
- **Thin crawls / classes** under `MIN_PEERS` skip the cross-sectional detector but can still
  fire the baseline detector once a norm exists.
- **Session/geo pricing** pinned via EUR + BG proxy.
- **Weekly digest vs ephemerality:** crawl horizons are 10/17/24 days so a weekday find
  survives to the Sunday digest; sub-3-day fire-sales are out of scope under a weekly cadence.

## Parked / Day-2
1. **Flights** for fly-to cities — only surface a hotel when a cheap flight exists in-window.
   Deferred; revisit if you keep seeing fly-to deals you can't act on.
2. **Urgent override** — same-day email for an outrageous short-fuse find, alongside the weekly
   digest, if weekly turns out to miss too much.
3. **Phase 2 — travel packages** (operators dumping unsold flight+hotel charters near
   departure): the richest vein, hardest to source (no aggregator; scattered across operator
   sites, JS-heavy). Same architecture pointed at 3-5 Bulgarian-market operators, near-term
   departures, ranked by per-person-per-night vs the operator's norm or DIY equivalent. Ship
   the hotel system first, live with it a month, then add this.
