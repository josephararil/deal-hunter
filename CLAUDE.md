# CLAUDE.md

Guidance for Claude Code when working in this repository.

## What this is

A personal travel deal-finder for a family of 3 (2 adults + 4-year-old) based near Plovdiv,
Bulgaria. It runs weekly on free GitHub Actions, emails immediately when something genuinely
exceptional is found, and is silent the rest of the time. No server, no real database — just
JSON state files committed back by CI.

It is a deliberate **Pareto build**: small, flat, readable scripts over clever abstractions.
If a change adds a framework, a class hierarchy, or a layer of indirection to save a few
lines, it's probably wrong for this repo. One genuine find a year justifies the whole thing.

## Active product: the Diamond Finder

`find_city_anomalies.py` is the only script that runs automatically (every Monday via `daily.yml`).
It is self-contained: no baseline data; Stage-3 grounding uses Booking.com (apidojo)
live rates, falling back to LLM concierge on any failure.

```
find_city_anomalies.py
  │
  ├─ Memory load — state/memory.json
  │    Baselines (realistic prices from past verifications) + outcome ledger
  │    (past corrections and kills). Injected as {memory} into all three stage prompts.
  │
  ├─ Trips load — state/trips.json (read-only; see "Trip log" below)
  │    trips.load() + trips.valid_trips() parse and validate the user's hand-maintained
  │    booking log. trips.price_anchors() (keyed via memory.baseline_key) REPLACES the
  │    matching baseline line in the memory summary with a "PAID €X/night" line — a real
  │    booking is stronger evidence than any past LLM verification. trips.summarize_for_prompt()
  │    renders a bounded trips block injected into both FIND_PROMPT and SKEPTIC_PROMPT so the
  │    scorer can calibrate against what this family actually pays and chooses.
  │
  ├─ Anti-spam state load — state/signals_seen.json (load_seen + prune_seen, at the TOP of main)
  │    Loaded before FIND so FIND can be told what was already emailed (see Invariant R).
  │
  ├─ Stage 1 · FIND (L.call_llm, want_search=True)
  │    Score candidates 0–100. Each candidate includes est_price_eur (structured number —
  │    NOT extracted from prose). Anchored to CITIES but can extend to nearby destinations.
  │    Named hotel stays only (cruises / flight-only fares / packages can't be live-priced).
  │    FIND gets its OWN memory block: corrections only (verdicts correct/kill/hallucinated/
  │    skeptic_kill/blocked), no baselines, no past good/skip rows — those taught it to
  │    re-propose the same Velingrad/Bansko hotels. It also gets {recently_emailed}: the
  │    properties emailed in the last 90 days (recently_emailed_text), with a do-not-repeat
  │    instruction. Spain is excluded from FIND (no PDV-Spain route; Spain gets its own sweep).
  │
  ├─ Gate — FIND score >= STAGE1_MIN_SCORE (triage only). NO price filter: there is no hard
  │    price ceiling anywhere in the pipeline; price is handled by the deterministic scorer.
  │    Then a PER-CITY CAP: only the highest-FIND-score survivor per city is grounded; the
  │    rest are dropped like below-threshold candidates (not grounded, not ledgered).
  │
  ├─ Stage 2 · GROUND (ground_deal seam) — one call per gate survivor (BEFORE scoring)
  │    Primary: `providers.ground_api()` — Booking.com (apidojo) live rates, no LLM call.
  │    Fallback: `_ground_llm` (L.call_llm, want_search=True) — LLM concierge.
  │    Returns verdict: confirm | correct | kill, plus options[], how_to_book, grounding,
  │    assistant_summary, confidence. A kill drops the candidate here. A confirm/correct
  │    merges the REAL price onto the candidate and forwards it to the scorer — UNLESS a
  │    DATA-QUALITY guard blocks it (confidence=low, grounded dates out of window, or the
  │    Invariant Q sanity check: live price > PRICE_SANITY_MULTIPLE x FIND's own estimate =
  │    a listing mismatch). Blocked entries are logged in city_signals.md and the email's
  │    dropped footer and never reach the scorer. Each grounded candidate carries
  │    grounding_method ("apidojo" | "llm") forward; the run prints/emails the live-vs-LLM
  │    price count.
  │    Grounding is swappable: `HOTEL_PROVIDER=""` forces LLM-only. Same
  │    `ground_deal(diamond, mem_text, today)` signature.
  │
  ├─ Stage 3 · SCORE (L.call_llm scorer + deterministic modifiers)
  │    The LLM returns a 0–100 DESIRABILITY score per grounded candidate (price held
  │    neutral — it is told the pipeline handles price). It ALSO emits normal_price_eur —
  │    its honest estimate of the property's OWN typical rate — as the reference the discount
  │    is measured against. For a destination that requires a FLIGHT, it additionally emits
  │    flight_cost_eur_total + ground_transport_eur (round-trip, whole family of 3; 0 for a
  │    straightforward drive) — find_city_anomalies.py folds these into an ALL-IN effective
  │    per-night price (hotel total + flight + ground, divided by nights) BEFORE the discount
  │    is computed, so a cheap hotel reached by an expensive flight is priced as an expensive
  │    trip, not a cheap one. The scorer must NOT also deduct desirability for the flight's
  │    MONEY cost (only for its non-monetary friction: time, transfers, jetlag with a toddler)
  │    to avoid double-counting. The pipeline then applies deterministic modifiers
  │    (config.compute_final_score):
  │      final = clamp(0,100, llm_score + price_adj + transit_adj)
  │      discount  = 1 - effective_ppn/normal_price   (effective_ppn = grounded_ppn for a drive;
  │                  hotel+flight+ground all-in per night for a fly destination. If the LLM gave
  │                  no usable normal_price_eur the pipeline stays NEUTRAL — discount=0.0,
  │                  price_adj=0 — it no longer substitutes the regional par; see Invariant P)
  │      price_adj = min(PRICE_BONUS_CAP, PRICE_SCORE_WEIGHT*discount)
  │                  (bonus capped, penalty UNCAPPED — overpriced deals sink on their own)
  │      transit_adj = ±TRANSIT_TIER1_BONUS (drivable Tier-1 vs fly Tier-2) — a small TIME/HASSLE
  │                  nudge only; the money cost of flying is now priced into price_adj above.
  │    Desirability itself is calibrated so "nearby, easy, nothing special" lands ~45-55, not
  │    ~80+ — 80+ is reserved for a genuine standout property/experience, not merely low friction.
  │    tier (config.tier_for): DIAMOND needs ALL of final>=DIAMOND_SCORE_THRESHOLD,
  │    llm_score>=DIAMOND_MIN_LLM_SCORE, AND discount>=DIAMOND_MIN_DISCOUNT — so an ordinary
  │    local weekend, however cheap, is at most GOOD (>=GOOD_SCORE_THRESHOLD), else skip.
  │    "good" additionally needs discount>=GOOD_MIN_DISCOUNT (10%): a nice hotel at its usual
  │    price is a skip. REFERENCE PRICE (Invariant H2): with >=REF_MIN_SAMPLES real Booking.com
  │    samples for the property+season (M.baseline_samples, merged across word orders and across
  │    FIND names via the Booking.com name), their median replaces normal_price_eur; with 1-2
  │    samples, min(llm, median); with none this month but some in other months,
  │    min(llm, any-month median) (ref_source history_any). CAPS after tier_for (Invariant U,
  │    then N, then F): a non-apidojo price → skip + NOTE_UNVERIFIED; a diamond/good with no
  │    PRIOR live price in any month → skip + NOTE_FIRST; a "good" that is not is_low_friction →
  │    skip + NOTE_FAR (far trips are diamond-only). Notes render in the email.
  │    The LLM never vetoes or tiers — a low final score is dropped by default, but every
  │    score is recorded. Same €85 can be a diamond for a standout (high llm_score) and a
  │    skip for an ordinary place. The scorer also emits a descriptive DOSSIER per candidate
  │    (`about` = what the property/location is + amenities + what a family does there;
  │    `value_case` = why the grounded price is/isn't a deal vs normal rates & alternatives),
  │    surfaced verbatim in the email so a human can judge an unfamiliar property. Descriptive
  │    only — must not move the score.
  │
  │    SCORED/UNSCORED SPLIT (Invariant Z): if the Stage-3 call fails outright, or returns
  │    with no usable numeric score for a given grounded candidate, that candidate becomes an
  │    `unscored` entry — tier "unscored", every numeric field (llm_score, final_score,
  │    price_adj, transit_adj, discount, normal_price_eur) explicitly None — instead of a
  │    fabricated score. It still carries its real grounded price (grounding already
  │    succeeded); only the judgement is missing. `scored_all` (diamond/good/skip) and
  │    `unscored` are tracked as separate lists throughout `main()` and both flow to the
  │    email, city_signals.json/md, and the ledger — never merged into one silently-degraded
  │    "scored" bucket.
  │
  ├─ Memory write — state/memory.json + state/memory.md
  │    Every run: record_outcome per gate survivor with llm_score + final_score and a verdict
  │    of its tier (diamond/good/skip), or "kill" (grounding kill) / "blocked" (guard) /
  │    "unscored" (grounded fine, Stage 3 gave no usable score — never coerced to "skip").
  │    record_baseline for every grounded confirm/correct that is high-confidence, in-window,
  │    AND apidojo-sourced (Invariant B) — even skips/unscored, since the live price is real
  │    regardless of the desirability verdict. Keeps a rolling median of up to
  │    MAX_BASELINE_SAMPLES real samples per property+season key rather than overwriting on
  │    every verification. prune() + save().
  │
  ├─ Anti-spam gate (Invariant R) — state/signals_seen.json "props"
  │    One entry per PROPERTY, keyed by M.identity_key (sorted characters of identity(), so
  │    word order doesn't matter): {date, tier, price_eur, name}. EVERY scored/unscored
  │    candidate passes is_new(): new if never emailed, or REALERT_DAYS (90) have passed, or a
  │    higher tier, or its live price is >= REALERT_PRICE_DROP (15%) below the last emailed
  │    price. No month or window in the key. The legacy "seen" map converts itself on load
  │    (seed_props_from_legacy).
  │
  ├─ Email (common.send_email) — an honest daily digest of EVERY scored candidate
  │    One email per run, fired whenever ≥1 scored candidate is new/tier-changed (any tier,
  │    incl. skip). Items grouped diamond→good→skip; each shows its tier badge, score
  │    breakdown (llm · price · transit = final), live all-in price, the scorer's `about`
  │    description + a "Why it's a deal" value-case callout, a "typically ~€X/night"
  │    comparison from PRIOR baselines, a child-price caveat for hotels, and the booking link.
  │    MAX_EMAILS_PER_RUN caps only the actionable diamond/good picks; skips are context and
  │    always shown in full. A "seen & dropped" footer lists that run's grounding kills /
  │    guard blocks (destination + reason) so the digest reflects everything the pipeline
  │    looked at. Conscience note if monthly count >= 8.
  │
  │    DEGRADED PATH: whenever ANY candidate is unscored, the digest gets a separate "Priced
  │    but not scored (pipeline degraded)" section — no tier badge, no score, just the live
  │    grounded price and options — kept entirely apart from the scored table so an unscored
  │    entry can never be mistaken for a judged one. The subject line is prefixed "⚠ " (or, if
  │    nothing scored at all, replaced outright with "pipeline degraded — N priced but
  │    unscored"). A run-health footer is now ALWAYS present in every email (not only degraded
  │    ones) — provider/models, per-stage status (stage1/2/3 ok/failed/partial + reason),
  │    found/grounded/scored/unscored/dropped/emailed counts, a "Prices: N live Booking.com ·
  │    M LLM estimates" line, tier-mix by transit tier, and LLM budget seconds used — so a
  │    quiet day and a degraded day are never visually indistinguishable.
  │
  └─ Always writes
       state/city_signals.json  — all Stage 1 candidates + full score breakdown (hunt: false)
       state/city_signals.md    — human-readable log with grounding + score/tier breakdown
       state/signals_seen.json  — updated re-alert state (props)
       state/memory.json        — baselines + outcome ledger (updated every run)
       state/memory.md          — human-readable memory digest
       state/deals_history.json — one record per emailed deal (diamond/good/skip), for web/
```

## Files — active pipeline

| File | Role |
|---|---|
| `config.py` | City list + diamond-finder knobs; per-stage provider overrides; token budgets; prompts. **Names no model** — the `LLM models` and `Run resilience` blocks are deliberately empty and point at the `llm-chain` package |
| `common.py` | `llm()`, `send_email()`, `parse_json_block()`, state IO |
| `memory.py` | `load()`/`save()`; `record_baseline()`/`record_outcome()`/`prune()`; `summarize_for_prompt()`; `identity_key()`/`canonical()`/`baseline_samples(any_season=False)` (word-order-proof property key + merged price history); `booking_key()`/`entry_booking_name()` (match history across FIND names by the Booking.com name) |
| `find_city_anomalies.py` | The diamond finder — runs weekly, emails a digest of every scored candidate (diamond/good/skip) + unscored/dropped sections |
| `providers.py` | Booking.com (apidojo) Stage-2 grounding: `ground_api()`, `resolve_hotel()`, `price()`, `list_properties(order_by=None)`, `card_currency()`. Non-EUR cards are refused (price raises → LLM fallback; alternatives skip them). `FAILURES` collects each fallback reason for the health footer. `_to_stage3` returns `booking_name` (the priced card's name); `_to_stage3_alternatives` does not |
| `trips.py` | Read-only user-maintained trip log: `load()`/`valid_trips()`/`price_anchors()`/`summarize_for_prompt()`/`backtest()`/`paste_snippet()` — see "Trip log" below |
| `state/trips.json` | Hand-edited by the user (booked trips); the pipeline reads it but never writes it (Invariant T) |
| `tools/migrate_memory.py` | One-off, already-run, idempotent migration script — the reviewable record of the 2026-08-10 state repair (baseline provenance purge, re-key, ledger revert, `trips.json` seed) |
| `.github/workflows/daily.yml` | Runs the diamond finder at 06:00 UTC every Monday (`cron: "0 6 * * 1"`); commits `state/`; `timeout-minutes: 20` |
| `state/city_signals.json` | Latest Stage 1 output (machine-readable) |
| `state/city_signals.md` | Stage 1–3 output (human-readable log with Stage 3 verification outcomes) |
| `state/signals_seen.json` | Anti-spam re-alert memory: `props` = `identity_key → {date, tier, price_eur, name}` (price_eur is EUR/night), monthly + degraded counts |
| `state/memory.json` | Price baselines + outcome ledger (grows every run, pruned at 200 entries / 180 days) |
| `state/memory.md` | Human-readable digest of memory.json |
| `state/deals_history.json` | One dossier record per emailed deal (diamond/good/skip) — full about/value_case/options/red_flags, not just scores. Appended every run a digest is sent, pruned at `DEALS_HISTORY_MAX_ENTRIES`/`DEALS_HISTORY_MAX_DAYS` (config.py). Data source for `web/` |
| `web/` | Standalone React (Vite) app to browse `deals_history.json` — see "Web UI" below |
| `spain.py` | Spain flight + hotel sweep: Ryanair fare calendar, Booking.com hotels for the 2 cheapest windows, same-dates ratio — see "Spain sweep" below |
| `test_spain.py` | Offline tests for `spain.py` (fare/combos/windows/ratio arithmetic, caching, per-city failure isolation, Invariant SP) |
| `state/spain_signals.json` | CI-managed Spain sweep log: per-city status + every priced window, rewritten each run |

## Web UI (`web/`)

A static React app for browsing every deal that has ever been emailed, in one place,
instead of digging through daily emails. It reads `state/deals_history.json` only — it has
no server and does not call the pipeline or any LLM.

- **Design** (`src/App.jsx` + `src/index.css`): a responsive, editorial card gallery with a
  slide-in detail drawer. No CSS framework or UI library — one hand-crafted `index.css`
  design system (CSS custom properties, light + dark themes with a manual toggle persisted to
  `localStorage`, `prefers-color-scheme` default, `prefers-reduced-motion` respected). Cards
  show a per-tier accent, an SVG score ring, live price and the scorer's blurb; the drawer
  shows the full dossier (score breakdown, value case, availability + Book links, caveats,
  grounding). Fonts are Fraunces (display) + Inter (UI), loaded from Google Fonts in
  `index.html` — the only external network dependency the page has.
- `prettyWindow()` in `App.jsx` reformats FIND's ISO window (`2026-09-10 - 2026-09-13`) for
  display; it splits **only on a space-padded hyphen** (`/\s+-\s+/`) so the hyphens inside
  ISO dates are preserved, and falls back to the raw string for any non-ISO window.

- **The deployed build fetches `deals_history.json` at page-load time straight from GitHub's
  raw content URL** (`raw.githubusercontent.com/josephararil/deal-hunter/main/state/...`,
  the repo is public) — not a bundled copy. So the site is always current the moment `daily.yml`
  commits fresh state; it only needs rebuilding/redeploying when the UI *code* changes, never
  when the data changes.
- Local dev fetches `/data.json` instead (`import.meta.env.DEV` switch in `src/App.jsx`).
  `npm run dev --prefix web` syncs the latest `state/deals_history.json` into
  `web/public/data.json` first (`web/scripts/sync-data.mjs`) then starts Vite.
- `web/wrangler.toml` deploys `web/dist/` as static assets on Cloudflare Workers
  (`npm run build --prefix web && npm run deploy --prefix web`, or `wrangler deploy` from
  `web/`) — no Worker script, pure static hosting, no CI redeploy step needed.
- Since `deals_history.json` is only appended to when a digest is actually emailed, the UI
  shows exactly "everything that made it to the email" — nothing more, nothing less.

## Spain sweep (`spain.py`)

The family plans to move to Spain (Elche/Alicante, Valencia), so a genuinely cheap Spain flight
+ stay is worth the 1.5h drive to Sofia airport where any other flight destination needs a
diamond. Plovdiv (PDV) has no Spain route. Spain is therefore NOT proposed by FIND; `spain.py`
runs its own deterministic sweep every run (`main()`, before `M.save`), and the LLM only writes
the descriptive dossier afterwards.

- **Flights first.** Ryanair's public, keyless fare calendar
  (`RYANAIR_FARES_URL`, one call per route-month, ~56 calls a run) gives every day's cheapest
  one-way fare SOF⇄city across `SPAIN_LEAD_DAYS`..`SPAIN_HORIZON_DAYS`. Round-trip combos are
  built for `SPAIN_NIGHTS` (3/4/5), and the city's whole round-trip fare distribution is known
  from those few calls. A city with no fare days is "no direct Ryanair flights"; fewer than
  `SPAIN_MIN_COMBOS` combos is a "thin calendar".
- **Hotels only for the cheapest windows.** `pick_windows` takes the `SPAIN_WINDOWS_PER_CITY`
  (2) cheapest non-overlapping combos; each gets one Booking.com list call
  (`P.list_properties(..., order_by="popularity")`). Qualifying hotels: EUR-quoted, ≥3★,
  review ≥8.0; fewer than `SPAIN_MIN_HOTEL_CARDS` (6) skips the window.
- **The ratio** (same dates, same run, no seasonal history needed):
  `ratio = (best ppn × nights + 3 × round-trip fare) / (p25 ppn × nights + 3 × median round-trip fare)`.
  Hotel reference = 25th percentile of the qualifying hotels for that exact window; flight
  reference = median of all the city's round-trip combos. 3 passengers because Ryanair charges
  a 4-year-old the adult fare. good ≤ `SPAIN_GOOD_RATIO` (0.55), diamond ≤ `SPAIN_DIAMOND_RATIO`
  (0.45). Calibrated on the user's own example ("an average 4 nights + flight in Valencia is
  ~€1,200 all in; €800 is not a deal, €500 is"): a €35 return fare + a promo hotel lands at
  0.532 (good); an ordinary cheap-flight week ~0.745 (nothing).
- **Fixed costs are shown, not scored.** `SPAIN_BAGS_EUR` (80) + `SPAIN_GROUND_EUR` (90: SOF
  drive, parking, transfer) are added to the all-in total in the email but kept out of the
  ratio, because they are the same on every trip and would only dilute it.
- **Caching:** `mem["spain_dest"]` (in `memory.json`) caches each city's resolved Booking.com
  ref, so `resolve_hotel` runs once per city ever.
- **Re-alert:** a Spain deal goes through the same `is_new` rule, keyed
  `"spain:" + identity_key` and compared on the all-in trip total (`realert_price_eur`). The
  prefix keeps EUR/trip apart from the main pipeline's EUR/night.
- **Email:** a "🇪🇸 Spain — flight + hotel deals" section above the main items (HTML and text),
  with Book hotel / Book flights links. Subject precedence: main diamond > Spain deal > main
  good. The health footer carries a "Spain sweep: N/7 cities with Ryanair flights from SOF ·
  W windows priced · D deal(s) · best <city> at X% of typical" line (or "off" / "failed: …").
- **Tuning log:** `state/spain_signals.json` holds every city's status and every priced window
  (any tier), so the 0.55/0.45 thresholds can be re-tuned from real runs.
- **Coverage gap (accepted):** Ryanair only; Wizz Air fares are invisible (no keyless source).
- **Not in `deals_history.json` / web UI:** a Spain deal's flight + hotel shape does not fit the
  current card; adding it is a web/ change of its own.
- `SPAIN_SWEEP=0` (repo variable) disables the sweep; the footer then reads "Spain sweep: off".
- **Unverified at build time:** whether Ryanair's calendar and apidojo's `order_by=popularity`
  accept requests from GitHub Actions IPs. Check the footer line on the first real run:
  `0/7` with "flight lookup failed" everywhere means Ryanair blocks Actions.

## Hotel grounding seam (Booking.com / apidojo)

The active Stage-2 grounding implementation lives in `providers.py`.
`ground_api(diamond, mem_text, today)` fetches live nightly rates from the Booking.com
RapidAPI (apidojo host), fuzzy-matches the named hotel in the result cards, and returns a
Stage-3 result dict. It falls back to `_ground_llm` (LLM concierge + web search) on any
failure (no API key, HTTP error, hotel not found in listing, unparseable window).

**Resolution strategy:**

1. **`HOTEL_MAPPING`** (in `config.py`): checked first; bypasses `/locations/auto-complete`
   for known/ambiguous properties. Add entries here for hotels whose name is ambiguous.

2. **`/locations/auto-complete`**: for hotel/landmark queries, picks the first matching
   landmark or hotel entry (token-set fuzzy match). For queries that only resolve to a city,
   falls back to `search_type=city`.

3. **`/properties/v2/list`**: fetches property cards with `order_by=distance` for specific
   hotel/landmark results (closest match first) or `order_by=price` for city-wide searches.
   Reads `composite_price_breakdown.gross_amount_per_night.value` as EUR per-night.

4. **Fuzzy matching**: token-set subset match after stripping noise words (hotel, resort,
   spa…). Returns `None` — triggering the LLM fallback — if no property card name matches.

The grounding seam in `find_city_anomalies.py`:

```python
# resolved at import time; returns ground_api (apidojo) or _ground_llm
ground_deal = _resolve_ground_deal()

# to force LLM-only: set HOTEL_PROVIDER="" (repo variable or env)
# HOTEL_PROVIDER="" python find_city_anomalies.py
```

`ground_deal(diamond, mem_text, today)` is called once per Stage-1 gate survivor
(before the skeptic). Both providers return the same grounding result schema.

## Run health and degraded runs

The pipeline bounds its own wall-clock time and degrades explicitly rather than either
outlasting CI's job timeout or silently pretending a bad run was a quiet one.

- **The wall-clock budget is `LLM_TOTAL_BUDGET_SECONDS` (660s), enforced inside `llm_chain`**
  and set in `daily.yml`. It replaces `common.set_run_deadline(C.RUN_BUDGET_SECONDS)`; the
  660 figure is deliberately kept rather than taking `llm_chain`'s own 1200 default, which
  would exceed CI's `timeout-minutes: 20` on its own. `main()` calls `L.reset_budget()` at the
  top. One real improvement: the clock now starts at the **first `call_llm`** rather than at
  import, so state loading and apidojo grounding no longer eat into the LLM budget.
- **The one-shot `GEMINI_FALLBACK_MODEL_MAP` hop is now a full chain**, `LLM_MODEL_CHAIN`.
  Now `flash-latest → 3.1-flash-lite` (pro dropped 2026-10-15), and `llm_chain` retries the
  current model on a transient status and then *advances*, repeatedly, instead of hopping
  once and giving up. `LLM_ATTEMPTS_PER_MODEL=3` + `LLM_BACKOFF_SECONDS=5,15` reproduce the
  old `_MAX_RETRIES`/`_RETRY_DELAYS` policy exactly. The old retry **jitter** was dropped
  deliberately: jitter desynchronises many concurrent clients backing off in lockstep, and
  this is a single serial job with one caller.
- **`STAGE_RESULTS` lives in `common.py`, not `find_city_anomalies.py`.** CI runs
  `python find_city_anomalies.py`, so that module is `__main__`, while `providers.py` does a
  lazy `import find_city_anomalies as fa` to reach `_ground_llm` — Python therefore builds
  **two module objects with separate globals**. A list defined in `find_city_anomalies` would
  collect FIND/SKEPTIC in one copy and every VERIFY in the other, and the health footer would
  silently under-report. `common` is imported under the same name by both copies, so the list
  there is genuinely shared. Regression test: Case 10 in `test_stub.py`.
- **Falling back is not degrading.** `LLMResult.fell_back=True` with `ok=True` means the
  chain advanced past a shedding model and got a real answer — the run is healthy. Only
  `ok=False` (chain exhausted) feeds `stage1_failed`/`scorer_stage_failed`. Never report a
  fallback as a warning in the email.
- Every email — not only degraded ones — now carries a permanent health footer
  (`_health_footer_html`/`_health_footer_text`): provider + the models that **actually
  served** this run (read from `STAGE_RESULTS`, not a static config string — with a chain the
  two can differ), per-stage status
  (`stage1`/`stage2`/`stage3` → ok/failed/partial + a `reason` string when something went
  wrong), found/grounded/scored/unscored/dropped/emailed counts, tier-mix by transit tier
  (Tier-1 local vs Tier-2 fly), and LLM budget seconds used. This is what makes a degraded run
  visibly different from an ordinary quiet day, which was exactly the blind spot the
  2026-08-10 outage exploited.
- **A run with zero live prices is flagged.** The footer's "Prices: N live Booking.com · M LLM
  estimates" line gains "⚠ Booking.com returned nothing this run; every price is an estimate"
  when something was grounded but nothing came from apidojo, plus "(first error: …)" from
  `providers.FAILURES` (cleared per run in `main()`; read via `sys.modules` so the LLM-only
  path never imports providers). `providers._get` keeps RapidAPI's response body in its
  HTTPError. Four runs (16 Sep, 25 Sep, 4 Oct, 12 Oct 2026) had zero live prices and nothing
  said so; CI logs that would have said why had expired, which is why the reason is in the email.

## Trip log

`state/trips.json` is a personal, hand-maintained log of trips this family has actually
booked — the strongest calibration signal available, stronger than any LLM guess, because
it's a real paid price and a real choice made.

- **Schema** — each entry needs `hotel_name`, `city`, `country`, `checkin`/`checkout` (ISO
  dates), `total_paid` (the TOTAL for the whole stay, not per-night), `currency`. Optional:
  `party`, `booked_via`, `rating`, `notes`.
- **Read-only from the pipeline's side (Invariant T).** The user edits this file by hand; no
  pipeline code ever writes it. Each digest email includes a ready-to-paste JSON snippet
  (`trips.paste_snippet(item)`) for any priced item with grounded dates and options, so
  logging a booking after the fact is low-friction — copy, paste, edit the total once you
  know it, done.
- **`trips.py` functions** (read the file for exact behavior before changing any of it):
  - `load()` — reads `state/trips.json`, never raises; missing file or malformed JSON both
    quietly fall back to `{"trips": []}` (a warning is printed for malformed JSON only).
  - `valid_trips(data)` — validates each entry against the required fields above, drops
    invalid entries with a one-line warning, and derives `nights` and (EUR only)
    `price_per_night_eur`.
  - `price_anchors(trips)` — for EUR trips only, maps `memory.baseline_key`-compatible keys
    to `{price_per_night_eur, hotel_name, checkin}`; these REPLACE the matching baseline line
    in `memory.summarize_for_prompt`'s output with a "PAID" line.
  - `summarize_for_prompt(trips)` — a compact, bounded (`MAX_TRIPS_IN_PROMPT`) text block,
    most-recent-first, injected into `FIND_PROMPT` and `SKEPTIC_PROMPT` as `{trips}`.
  - `backtest(trips, memory)` — cross-references logged trips against the outcome ledger and
    returns counts of `picked` (pipeline had scored it diamond/good), `skipped` (seen but not
    surfaced), and `unseen` (never appeared in the ledger at all).
  - `paste_snippet(item)` — the ready-to-paste JSON snippet described above.
- **No FX conversion.** A non-EUR trip still contributes preference/novelty context via
  `summarize_for_prompt` (the LLM sees what was booked, where, and for how much in its own
  currency) but produces no price anchor — `price_anchors` only emits an anchor when
  `currency == "EUR"`.
- **Back-test line in the digest.** When there is at least one valid logged trip, the email
  includes a one-line summary (`_backtest_note`) — "Of N trip(s) you've logged, the pipeline
  had scored P as diamond/good, S as skip, and never saw U." — comparing what the pipeline
  actually scored those properties against what the user booked.

## Critical invariants — do not break these

- **All LLM calls go through `llm_chain.call_llm()`** (`import llm_chain as L`), from the
  `llm-chain` package pinned at `@v1` in `requirements.txt`. Do not call provider HTTP
  endpoints directly, and do not reintroduce an LLM path in `common.py` — its LLM half was
  deleted precisely to remove the duplicated plumbing. `call_llm` takes a plain **string**
  prompt (not a messages list), returns `LLMResult`, and **never raises on a provider
  failure**; callers branch on `.ok`.
- **No model name appears in this repo's Python.** `grep -rn "claude-\|gemini-" --include=*.py .`
  must return nothing. Model names live only in `LLM_MODEL_CHAIN`/`LLM_SEARCH_MODEL_CHAIN`.
  Those two carry an explicit `|| '<default>'` in `daily.yml` rather than falling through to
  `llm_chain`'s own defaults, which are flash-based and tuned for a different repo — falling
  through would silently change the reasoning model this repo chose. That is the one
  place a model name is written down, and it is YAML, not code.
- **All email goes through `common.send_email()`.** Single SMTP path. No duplication.
- **State files in `state/` are CI-managed.** `city_signals.json`, `city_signals.md`,
  `signals_seen.json`, `memory.json`, `memory.md`, `deals_history.json` are committed after
  each run. They are real state, not scratch. Seed values: `{}` / `{"props":{}, "monthly_count":{}}` /
  `{"baselines": {}, "ledger": []}` / `{"entries": []}`.
- **(T, trips are user-owned) No code path writes, creates, reorders, or reformats
  `state/trips.json` after it's seeded.** `trips.py` reads it via `common.load_json` and never
  calls `save_json` (or anything else that writes the path) — the whole module docstring says
  so. The pipeline already commits everything under `state/` every run, so a write here would
  silently persist and could clobber a hand edit the user made between runs. If you ever need
  the pipeline to record something trip-related, put it in `memory.json`, not `trips.json`.
- **`deals_history.json` is appended to, never overwritten, and only from `to_email`** (the
  exact list the digest renders) — so it stays an honest "everything that made it to the
  email" record for `web/`. Do not populate it from `scored_all` or any pre-anti-spam-gate
  list; that would show deals the user was never actually notified about.
- **(H, history is what was actually judged) `deals_history.json` entries are only ever built
  from scored tiers (diamond/good/skip) — never `unscored`.** An unscored entry has a `tier`
  string (`"unscored"`) and would render in the web UI exactly like a real judged deal, just
  with a missing score — indistinguishable from data loss to anyone browsing `web/`. Keep
  `unscored`/`unscored_to_email` out of `build_history_entries` input.
- **Grounding runs BEFORE scoring.** Stage 2 grounds live prices; Stage 3 scores those live
  prices. Core design decision — the scorer must never grade a Stage-1 *estimate*. Preserve
  this if you touch the pipeline order.
- **The final tier is deterministic, NOT the LLM's call.** The Stage-3 LLM returns a 0–100
  `score` for scoring (nightly hotel price held neutral — the prompt tells it so), one numeric
  `normal_price_eur` reference (see next invariant), plus purely-descriptive
  `why`/`about`/`value_case`/`red_flags` prose for the human. The pipeline computes
  `final = llm_score + price_adj + transit_adj` (`config.compute_final_score`) and derives the
  tier via `config.tier_for(final, llm_score, discount)`. Do not let the LLM emit a tier or a
  veto — that was deliberately removed so scores stay comparable and every one is recorded for
  tuning. The descriptive fields (`about` = what the place is; `value_case` = why the price is
  a deal) must NEVER influence the numeric score or gate a deal — the prompt says so explicitly.
- **(Z, zero-is-not-missing) A missing LLM score must never become `0`, and a missing
  `normal_price_eur` must never fall back to a regional par.** Both substitutions used to
  produce a well-formed number that flowed through the rest of the pipeline as if it were a
  real judgement — that is exactly how the 2026-08-10 outage turned five good hotels into
  fabricated "skip" verdicts (llm_score coerced to 0) and produced a −166 penalty on a 5-star
  Hilton. The fix: `llm_val` is `None` unless the scorer returned an actual number
  (`find_city_anomalies.py`), and a candidate with no score becomes an explicit `unscored`
  entry (see the Stage 3 diagram above) rather than a fake tier. `compute_final_score` treats
  a missing/unusable `normal_price_eur` as NEUTRAL (`discount=0.0`, `price_adj=0`), not a par
  substitution (see Invariant P). `memory.summarize_for_prompt` also excludes any ledger row
  shaped exactly like the old bug (`llm_score == 0 and final_score == 0`) from the "recent
  outcomes" calibration text, so a pre-fix poisoned row can't keep teaching the model the same
  lie even if one somehow survives.
- **DIAMOND is a multi-gate, deliberately-rare tier.** `tier_for` promotes to diamond ONLY when
  all three hold: `final >= DIAMOND_SCORE_THRESHOLD`, `llm_score >= DIAMOND_MIN_LLM_SCORE` (a
  standout property, above the ~82 ordinary-local baseline), AND `discount >= DIAMOND_MIN_DISCOUNT`
  (a genuine steal vs the property's normal rate). This is the fix for "every local spa weekend
  became a diamond" — an ordinary or fairly-priced place is at most GOOD. Target cadence is a
  few diamonds per MONTH. Do not collapse this back to a single final-score threshold.
- **The price modifier measures a real discount vs the property's OWN normal price, not distance
  from a flat regional par.** The LLM's `normal_price_eur` is the reference; `discount = 1 -
  effective_ppn/normal_price` and `price_adj = min(PRICE_BONUS_CAP, PRICE_SCORE_WEIGHT*discount)`.
  A flat par cannot express "a 5-star that normally costs €170 dropping to €110 is a steal even
  though €110 is above the €80 regional floor" — that's why the reference is per-property. This
  is a narrow, deliberate exception to "price held fully neutral by the LLM": the LLM supplies a
  *reference* price (an input, like `est_price_eur` upstream) but still does NOT tier or apply
  the modifier. `normal_price_eur` MUST be honest — a fabricated high "normal" manufactures a
  false diamond; the prompt tells the LLM to set it at/below the grounded price when unsure.
  `DIAMOND_PAR_EUR`/`DEFAULT_DIAMOND_PAR_EUR` are now INFORMATIONAL ONLY — skeptic-prompt
  context plus log/digest display — and are never substituted into the scoring math. When the
  LLM gives no usable `normal_price_eur`, `compute_final_score` stays NEUTRAL (`discount=0.0`,
  `price_adj=0`) rather than falling back to par; substituting a regional budget floor for a
  specific property's normal rate was a category error that produced a −166 penalty on a
  5-star Hilton at €475.81 vs the €110 par (see Invariant Z above).
- **(B, baselines are evidence-only) `record_baseline` may be called only when the grounding
  result's `grounding_method == "apidojo"`, in addition to high confidence + in-window.**
  Never from the LLM concierge fallback, never from a missing/unknown provenance — fails
  closed. Before this, an LLM concierge could restate FIND's own price estimate back with
  false confidence, and it would be trusted as a real verified price; that is how 40 of 73
  baselines ended up unverified before the 2026-08-10 migration purged them. See
  `find_city_anomalies.main()`'s baseline-write call, which checks
  `r3.get("grounding_method", "llm") == "apidojo"` before ever calling `M.record_baseline`.
- **`effective_ppn` folds an LLM-estimated flight/ground-transport cost into the price used for
  discount math, for any destination that requires a flight.** `find_city_anomalies.py` reads
  the skeptic's `flight_cost_eur_total` + `ground_transport_eur` (round-trip, whole family of 3;
  0 for a straightforward drive), adds them to `grounded_total_eur`, and divides by nights —
  `effective_ppn = grounded_ppn` unchanged for a drive, `(hotel_total + flight + ground) / nights`
  for a fly destination. This is what fixed the "cheap hotel, expensive flight" blind spot: a
  €125/night Crete all-inclusive that actually costs the family ~€900 in flights becomes an
  ALL-IN ~€267/night trip, and is scored (and tiered/diamond-gated) against that real figure, not
  the bare hotel rate. `transit_adj` (±`TRANSIT_TIER1_BONUS`) is now a small TIME/HASSLE-only
  nudge — the MONEY side of flying is `price_adj`'s job via `effective_ppn`, not `transit_adj`'s.
  `SKEPTIC_PROMPT` explicitly tells the LLM not to also deduct desirability score for flight cost
  (only for non-monetary friction), to avoid double-counting the same burden twice. These two new
  fields are estimates, same honesty bar as `normal_price_eur` — this is still not live flight
  data (that remains out of scope; see below), just a heuristic that stops a flight-heavy trip
  from being scored as if it cost only the hotel rate.
- **Desirability is calibrated so "nearby, easy, and available" is NOT a high score.**
  `SKEPTIC_PROMPT`'s calibration anchors reserve 80+ desirability for a genuine standout property
  or experience; an unremarkable-but-pleasant regional hotel — decent pools, nothing distinctive,
  one of dozens of near-identical options — should land ~45-55, not ~80+. Before this, the prompt's
  own calibration example ("nearby 4-star spa, easy drive, pools open: ~82") anchored EVERY
  ordinary local weekend to a near-diamond-floor score regardless of how unremarkable the
  property actually was. If you edit these anchors, keep the "easy + open is a prerequisite, not
  what earns 80+" framing — the whole point is that low friction alone should not manufacture a
  high tier.
- **Par/transit lookups take a city+country string, never FIND's free-text label.**
  `find_city_anomalies._location(c)` builds `"City Country"` from FIND's structured fields;
  `get_diamond_par`/`transit_tier` substring-match country/city tokens. Passing the prose
  `destination` label (which usually omits the country) silently defaulted every Bulgarian deal
  to the €110 par — the original bug behind the phantom diamonds. Keep using `_location(c)`.
- **The LLM score is NET FAMILY VALUE DELIVERED, not luxury/prestige** (the numerator; the
  price modifier is the denominator). A modest low-friction local break can outscore a
  glamorous far-flung one. Attraction is one modest input for a 4-year-old. Because flights
  are out of scope and NOT in the grounded hotel price, the scorer is the only stage that can
  weigh flight cost/hassle — so a no-direct-PDV destination is penalised in the score itself,
  not just by the small transit nudge. If you ever add flight data, revisit this.
- **(P) The scoring knobs live in config, nowhere else** (`DIAMOND_PAR_EUR`/`DEFAULT_DIAMOND_PAR_EUR`
  [informational only — prompt context/display, never substituted into scoring math],
  `PRICE_SCORE_WEIGHT`, `PRICE_BONUS_CAP`, `TRANSIT_TIER1_BONUS`/`TIER2`,
  `DIAMOND_SCORE_THRESHOLD`, `GOOD_SCORE_THRESHOLD`, `DIAMOND_MIN_LLM_SCORE`, `DIAMOND_MIN_DISCOUNT`).
  The price bonus is capped; the penalty is UNCAPPED, which is *why there is no hard price ceiling* —
  overpriced deals sink to skip on their own. The est-price-gap flag (`EST_GAP_FLAG_MULTIPLE`,
  new) is part of this same invariant: when the live grounded price is far above FIND's
  original estimate, `est_gap_flag` is set and shown to the reader (`_est_gap_note`) — it never
  gates or drops a candidate on price, purely a display-only heads-up that the estimate was
  unreliable for this one.
- **`STAGE1_MIN_SCORE = 80`** is the gate into grounding — pure triage on FIND's estimate to
  bound grounding cost. NO price filter at the gate.
- **FIND scoring is triage; the scorer is authoritative.** FIND's score only decides who gets
  grounded. The Stage-3 scorer re-scores from the real price + full context; its score (plus
  modifiers) is what tiers the deal.
- **Gemini token budgets carry thinking-token headroom.** `maxOutputTokens` caps hidden
  thinking + visible output combined; if it runs out mid-answer the JSON truncates
  (`finishReason=MAX_TOKENS`) and parses to nothing — indistinguishable from a quiet day.
  `llm_chain` flags it as `LLMResult.truncated`; `MAX_TOKENS_FIND/SKEPTIC/VERIFY`
  are set well above observed thinking usage (~3-4k). If you see the warning, raise them.
- **Grounding (Stage 2) only removes candidates, never adds them.** A grounding kill means the
  deal is NOT REAL (hallucinated property, no availability in-window, no supporting evidence) —
  it never reaches the scorer or email. `verdict: correct` (price was wrong) still forwards the
  corrected figures to the scorer; do not treat `correct` as a kill. Grounding no longer kills
  on price (no ceiling) — only data-quality guards (low confidence, dates out of window) block a
  grounded candidate from scoring.
- **Grounding stays in its lane — price & bookability, NOT desirability.** A quality / seasonal /
  amenity concern (a pool closed for maintenance, a dead resort off-season, mediocre reviews)
  must NOT be a grounding kill — that is the scorer's job, and killing there hides the candidate
  from the digest entirely and duplicates the scorer. `VERIFY_PROMPT` instructs grounding to
  NOTE such concerns in `grounding`/`assistant_summary` (so the scorer weighs them) but still
  return confirm/correct with the real price. The apidojo path (`_decide_verdict`) already never
  kills on anything but non-resolution; keep the LLM fallback aligned with it.
- **Windows are parsed by `providers._extract_date_range`, which must track FIND's window
  format.** FIND emits full dates both sides ("17 July 2026 - 20 July 2026"); the parser handles
  that plus "DD Month - DD Month YYYY", "Month DD YYYY - …", and the short "Sep 10-14, 2026" /
  "10-14 Sep 2026" forms, the cross-month "Nov 25-Dec 2, 2026" form (and "Dec 28-Jan 2, 2027"
  across a year end), and returns None on a backwards range. A format the parser misses makes
  apidojo silently fail and every candidate fall to the (costlier, less price-accurate) LLM
  fallback — so if you change FIND's `window` wording, extend the parser and its regression tests
  (`test_providers.py`) in the same change.
- **Every scored candidate's breakdown is recorded** (`llm_score`, `final_score`, tier) in the
  ledger, `city_signals.json`, and the run log — deliberately, so a deal that scored 69 at €86
  and 74 at €79 keeps its history rather than being lost to a veto. Ledger verdicts:
  diamond/good/skip (scored), `kill` (grounding kill), `blocked` (guard).
- **Baselines are only written** when grounding confidence is "high", the grounded option
  dates fall within the candidate window (rough season_key match), AND
  `grounding_method == "apidojo"` (Invariant B) — recorded for every such grounded
  confirm/correct regardless of the tier (even a skip or unscored: the price is real).
  Low-confidence, out-of-window, or non-apidojo (LLM concierge) verifications produce
  unreliable or self-referential data — never stored. `record_baseline` takes a pre-built
  `key` from `memory.baseline_key(candidate)` (property identity + season), not raw
  destination/season args, and keeps a rolling median of up to `MAX_BASELINE_SAMPLES` real
  samples rather than overwriting on every verification.
- **The email's price comparison uses the PRIOR-run baseline snapshot** (`prior_baselines`,
  captured right after `M.load()`), not the live `mem` — otherwise a deal is compared against
  the very price this run just recorded for it ("about the usual" for everything).
- **`est_price_eur`** is a structured numeric field emitted by Stage 1 for each candidate.
  It is FIND's estimate — used only for `claimed_price` in memory and the grounding
  confirm/correct comparison (never for tiering; the grounded price drives the score). Never
  use `_extract_price()` from prose for it.
- **`deal_id` is a run-local correlation key, not a persistent id.** `find_city_anomalies.py`
  assigns it (1-based) Python-side right after Stage 1 parses — never trusting the LLM to
  mint it. The scorer echoes it back so scores merge onto grounded candidates by id, not by
  fragile destination-string matching (`_match_candidate`, with a destination fallback).
  It only correlates within one run — candidate #1 today ≠ #1 tomorrow — so it must NEVER
  key `signals_seen.json` or `memory.json`; those stay keyed by durable identity across runs
  (`memory.json` by `identity|season`; `signals_seen.json` by `M.identity_key` via
  `realert_key`). It appears in `city_signals.json`
  (regenerated each run) for traceability only.
- **The email is an honest digest of EVERY scored candidate, not a diamond/good-only alarm.**
  It shows diamonds, good finds AND skips — each with its full score breakdown — so the user
  builds a mental model of what the pipeline sees and why, and can human-override a
  low-scored deal (e.g. a Rome skip that's useful if they were going anyway). It fires
  whenever ≥1 scored candidate is new per the re-alert rule (Invariant R, any tier).
  Only diamond/good picks are capped by MAX_EMAILS_PER_RUN; skips are always shown. Grounding
  kills / guard blocks appear in a
  compact "seen & dropped" footer (no email is sent purely for a kill). A day with nothing
  new (or nothing found) still sends nothing.
- **(S, identity keys stay byte-stable) `memory.identity()` reproduces the original
  `find_city_anomalies._identity()` byte-for-byte** — it was moved verbatim into `memory.py`,
  not reimplemented, including its `"&"` → `"and"` normalization. `find_city_anomalies.py`
  now aliases `_identity = M.identity` so baselines and anti-spam share one derivation
  (`_identity = M.identity` near the top of the file). A behavioral drift here would orphan
  every existing baseline key and every `signals_seen` prop — do not "clean up" this
  function's body without checking every existing key still matches. **Word-order merging is
  layered on top, never inside:** `M.canonical()` / `M.identity_key()` sort the characters of
  `identity()` (so "Hotel Arte SPA & Park" and "Arte SPA and Park Hotel" collide, and old
  squashed keys can be converted), and `M.baseline_samples()` merges price history across
  such variants at read time. Stored keys are never rewritten.
- **(R, re-alert) A property is re-emailed only per `is_new`:** never emailed, or
  `REALERT_DAYS` (90) since it was, or a higher tier, or a live price `REALERT_PRICE_DROP`
  (15%) below the last emailed price. Never key anti-spam on month, season or exact window
  again — that re-alerted the same hotel every month and on every good↔diamond flip. Looks
  correct if broken: the digest still sends, just with the same hotels. Tests: Cases 11–13.
- **(H2, history beats guess) With ≥ `REF_MIN_SAMPLES` real Booking.com samples
  (`M.baseline_samples(prior_baselines, c)`), their median is the discount reference and the
  scorer's `normal_price_eur` is ignored;** with 1–2 samples, `min(llm, median)` (median alone
  if the LLM gave none). With no same-month sample but samples from other months
  (`baseline_samples(..., any_season=True)`), `min(llm, any-month median)`: other months may
  only LOWER the reference, because seasons differ and a summer median must not inflate a winter
  one. For a fly destination with a history reference the same per-night
  flight cost is added to the reference, so the discount compares like with like. The reference
  actually used is stored as `normal_price_eur` with `ref_source`
  (`history`/`history_any`/`llm`/`none`).
  **Samples match by Booking.com name as well as FIND's name.** FIND names one hotel many ways
  ("Saint Ivan Rilski SPA Resort" vs "SPA Resort St Ivan Rilski"), so `baseline_samples` also
  matches when `M.booking_key(d["booking_name"])` equals `M.booking_key(M.entry_booking_name(b))`.
  `booking_name` is the priced card's name (`providers._to_stage3`, threaded onto the grounded
  candidate and stored by `record_baseline`); legacy entries without it fall back to the name
  parsed from their "Verified X, N-star for …" note. `booking_key` sorts characters after a
  token alias map (`_NAME_ALIASES`, only `st`→`saint`; extend only with evidence). Stored keys
  never change (Invariant S); this widening is read-time only.
  Looks correct if broken: a fake 15% discount yields a plausible "good" (Lucky Bansko was €122
  on all 8 live checks yet got +12 bonuses from a guessed €140 "usual"); on 2026-10-15 Saint Ivan
  Rilski was a 💎 "27% below €220" while a real €175.61 sat under another name and month (8.2%).
  Test: Case 16.
- **(N, no history, no pick) A diamond/good with no PRIOR live Booking.com price for the
  property in any month becomes skip with `NOTE_FIRST`.** "Prior" means `prior_baselines` only:
  this run's own price never counts, or the gate would be vacuous (Invariant U already requires
  a live price). Order is U, then N, then F; the first that fires sets the note. A later run
  emails the hotel again if it then rates good or diamond (Invariant R: higher tier). Looks
  correct if broken: a guessed "usual" produces a plausible "good" (Hotel Perelik, 2026-10-15,
  18% off a guessed €230). Test: Case 16b.
- **(Q, price sanity) A grounded price > `PRICE_SANITY_MULTIPLE` (4×) FIND's own
  `est_price_eur` is a data-quality block, never scored.** It is not a price ceiling — it
  compares against FIND's estimate, not an absolute. `EST_GAP_FLAG_MULTIPLE` (1.4×,
  display-only) still flags smaller gaps. Motivating case: a €1,171.63/night Varna listing vs a
  €75 estimate (15.6×).
- **(U, unverified) A candidate whose `grounding_method != "apidojo"` cannot be diamond/good**
  → skip with `NOTE_UNVERIFIED`. Applied before N and F, so the note shown is the unverified one. An
  LLM-estimated price cannot prove a deal; this also makes a run-wide Booking.com outage
  quiet instead of spammy.
- **(SP, Spain arithmetic is the pipeline's) The LLM never sets or changes any Spain number or
  tier.** `spain.add_dossiers` copies only `about`/`value_case`/`red_flags` from the LLM reply
  onto a deal; every price, ratio and tier is computed in `spain.evaluate_window`. Looks correct
  if broken: an LLM-"corrected" ratio would still render plausibly. Test: `test_spain.py`
  (a dossier stub returning a bogus `ratio` leaves 0.531 intact).
- **(F, friction) A "good" that is not `C.is_low_friction(_location(c))` becomes skip with
  `NOTE_FAR`;** diamonds pass. Low friction = Bulgaria or a listed Greek/Turkish border town
  (`DRIVE_COUNTRIES`/`DRIVE_CITY_TOKENS`). Anything else means a 1.5h drive to Sofia airport
  with a 4-year-old, which the family does only for a diamond. Plovdiv (PDV) has no Spain
  route, so Spain was removed from FIND's PDV group.
- **`city_signals.json` always has `hunt: false`.** The diamond finder does not trigger
  hotel crawls. The field exists for schema compatibility only.
- **Memory is written every run**, including silent days. `memory.py` functions must
  not be called with None memory dict; always `M.load()` first.

## The LLM layer (`llm-chain`)

All LLM calls go through the **`llm-chain`** package — `import llm_chain as L; L.call_llm(...)`.
It is a shared dependency (`llm-chain @ git+https://github.com/josephararil/llm-chain@v1`),
also used by the sibling `weekly-concierge` repo. It is **not a file in this repo**.

```python
L.call_llm(prompt, *, stage="", max_tokens=4000, want_search=False,
           search_prompt=None, search_preamble=None, response_schema=None,
           provider=None, web_search_max_uses=6) -> LLMResult
```

`prompt` is a plain **string**, not a messages list. Returns
`LLMResult(text, ok, model, provider, fell_back, grounded, truncated, attempts, error)`
and **never raises on a provider failure** — callers branch on `.ok`.

**Why.** `common.llm()` retried one model with no cross-model fallback. On 2026-08-14
`weekly-concierge` emailed a blank report because a model returned 503 on 13 of 14 calls
while another served 3/3 on the same key in the same window. `llm_chain` retries the same
model on a transient status, then **advances to the next model in the chain**. This repo's
own one-shot `GEMINI_FALLBACK_MODEL_MAP` hop was a partial version of the same idea.

- **Chain, not per-stage pinning.** Every stage starts at `LLM_MODEL_CHAIN[0]` and advances
  on failure; no model is bound to a task. `MODEL_FIND/SKEPTIC/VERIFY` are gone — all three
  held the same value, so they expressed a distinction that did not exist.
- **No name mapping.** Names reach the API verbatim. The old `GEMINI_MODEL_MAP` used
  `.get(model, <fallback>)`, so a typo'd name silently resolved to a different model and the
  log named one that never served the call. Now a wrong name 404s and advances, visibly.
- **Every knob is an `LLM_*` env var read at call time**, so the chain is retuned from repo
  variables with no code change. `daily.yml` passes all eleven through; a knob **missing**
  from that env block fails **silently**.
- **401/403 fail fast** without trying another model, by design — retrying an auth error
  across N models yields a long run reporting "all models unavailable", indistinguishable
  from a real outage. **400 advances but is never retried**: a malformed `response_schema`
  returns 400 deterministically on every model.
- `python -m llm_chain` prints the resolved config, every model the key can list, and a ping.

**Call sites** (all in `find_city_anomalies.py`): FIND, SKEPTIC, and VERIFY inside
`_ground_llm`. Each is wrapped in `_stage_llm(name, res)`, which records `(stage, LLMResult)`
into `STAGE_RESULTS` (cleared per run) and logs the serving model. An exhausted chain is
raised as a `RuntimeError` into the **existing** `stage1_failed`/`scorer_stage_failed`
machinery rather than a parallel reporting path — so the degraded-email behaviour built in
PR #17 is unchanged.

**Gemini search/reasoning split** (behaviour unchanged, now inside `llm_chain`):
`want_search=True` runs two calls. Search runs on `LLM_SEARCH_MODEL_CHAIN` (lite tier) with
`google_search` — the only tier that survives Google's grounding gateway; flagships time out
~99% of the time with it attached. Reasoning then runs on `LLM_MODEL_CHAIN` with **no tools**
and the `responseSchema` (the two conflict, the other reason for the split). Stage 1 passes
`SEARCH_PROMPT` via `search_prompt=`; `SEARCH_RESULTS_PREAMBLE` goes via `search_preamble=`
and frames leads as a **seed, not a fence**. If search fails, reasoning proceeds
knowledge-only. On Anthropic the flagship searches inline via `FIND_PROMPT`, whose
`{search_directive}` slot is filled from `L.resolved_provider(C.PROVIDER_FIND)`.

- Per-stage provider overrides `PROVIDER_FIND/SKEPTIC/VERIFY` in `config.py` (all `None`)
  pass through as `provider=`.
- `response_schema` (Gemini only): `STAGE1/2/3_RESPONSE_SCHEMA` in `config.py`.

## Required secrets / variables

| Name | Type | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | secret | Anthropic LLM calls |
| `GEMINI_API_KEY` | secret | Gemini LLM calls |
| `LLM_PROVIDER` | repo variable | `"anthropic"` or `"gemini"` |
| `LLM_MODEL_CHAIN` | repo variable | Reasoning fallback chain, comma-separated. **Defaults in `daily.yml`** to `gemini-flash-latest,gemini-3.1-flash-lite` (no pro: not needed for this workload) — do NOT let this fall through to the package default |
| `LLM_SEARCH_MODEL_CHAIN` | repo variable | Search chain (lite tier only). Defaults to `gemini-3.1-flash-lite,gemini-flash-latest` |
| `LLM_ATTEMPTS_PER_MODEL` | repo variable | Retries per model before advancing. Default 3. Prefer LOW — the chain is the resilience mechanism, the retry loop is only for a genuine blip |
| `LLM_BACKOFF_SECONDS` | repo variable | Backoff ladder. Default `5,15` |
| `LLM_TOTAL_BUDGET_SECONDS` | repo variable | Wall-clock ceiling across all LLM calls. Default **660**, not the package's 1200 — 1200 exceeds `timeout-minutes: 20` |
| `LLM_TIMEOUT_SECONDS` | repo variable | Per-request HTTP timeout |
| `LLM_RETRY_STATUSES` | repo variable | Statuses retried on the same model |
| `LLM_ADVANCE_STATUSES` | repo variable | Statuses that advance to the next model. 401/403 in neither — auth errors fail fast |
| `LLM_RETRY_AFTER_CAP` | repo variable | Cap on an honoured `Retry-After` |
| `LLM_ANTHROPIC_MODEL` | repo variable | Model used when `LLM_PROVIDER=anthropic` |
| `SMTP_HOST` | secret | Email delivery |
| `SMTP_PORT` | secret | Email delivery (default 587) |
| `SMTP_USER` | secret | Email delivery |
| `SMTP_PASS` | secret | Email delivery |
| `EMAIL_TO` | secret | Recipient (defaults to SMTP_USER) |
| `EMAIL_FROM` | secret | Sender (defaults to SMTP_USER) |
| `RAPIDAPI_KEY` | secret | Booking.com (apidojo) hotel grounding via RapidAPI (`providers.py`) |
| `BOOKING_RAPIDAPI_HOST` | repo variable | RapidAPI host; default `apidojo-booking-v1.p.rapidapi.com` |
| `HOTEL_PROVIDER` | repo variable | `"apidojo"` (default) or `""` to force LLM-only grounding |
| `SPAIN_SWEEP` | repo variable | Optional. `0` disables the Spain sweep; unset or anything else runs it |

## Running locally

```bash
pip install -r requirements.txt
export GEMINI_API_KEY=...  LLM_PROVIDER=gemini
# or: export ANTHROPIC_API_KEY=...  LLM_PROVIDER=anthropic
python find_city_anomalies.py   # writes state/; emails if diamonds found + SMTP vars set
```

To test without sending email, leave SMTP vars unset — the `try/except` around the send
catches the `KeyError` and prints the error without crashing.

To test the three-stage gate offline: stub `llm_chain.call_llm` (see `_as_chain` in
`test_stub.py`, which adapts an old-style string-returning stub into an `LLMResult`) for each
stage (including a `correct` and a `kill` case for Stage 3), then run the script and
inspect `state/city_signals.md`, `state/signals_seen.json`, and `state/memory.json`.

## Known trade-offs (accepted — don't "fix" without asking)

- **No price data.** The diamond finder is pure LLM reasoning + web search. It can miss
  deals that don't appear in search results, and can hallucinate if search is weak. The
  three-stage gate and self-improving memory exist to compensate.
- **Gemini + search:** `google_search` quality and behaviour differ from Anthropic's
  `web_search`, and grounding runs on a separate lite chain (`LLM_SEARCH_MODEL_CHAIN`) because
  flagship models time out on Google's grounding gateway. If the search call fails, the
  flagship reasoning step still runs — just from prior knowledge rather than live data.
- **90-day re-alert window:** a great deal that persists for months is emailed once and then
  only again if it gets 15% cheaper or reaches a higher tier. Acceptable given the "rare,
  act-now" framing. Legacy-seeded props have `price_eur: null`, so the price-drop path for
  them is off until they are emailed again.
- **A first sighting can't be a pick (Invariant N).** A hotel the pipeline has never priced live
  is at most a skip, however good it looks, because nothing proves the discount. Accepted by
  Joseph on 2026-10-15. The cost: a real one-off deal on a never-seen hotel is shown as a skip,
  and is emailed as a pick only if a later run sees it again at a good price.
- **Family-only scope.** Destinations that require arduous travel or are poor fits for a
  4-year-old are excluded by the skeptic prompt. This is intentional, not a filter bug.
- **Tier-2 (fly-from-SOF / long-drive) variety was investigated and deliberately deferred, not
  fixed.** Measured over 95 historical scored ledger rows: Tier-2's median `price_adj` was
  actually HIGHER than Tier-1's (+9 vs +3) — so Tier-2 is not losing on the price modifier.
  It loses because 12 of 18 Tier-2 skips already scored below the GOOD threshold on
  desirability (`llm_score`) alone, before any price/transit modifier — i.e. the effect is
  prompt-driven (the skeptic's calibration), not a price-modifier bug. This is why no scoring
  weights were touched in this change — the diagnosis pointed at the skeptic prompt's
  calibration, and changing `PRICE_SCORE_WEIGHT`/`TRANSIT_TIER2_BONUS`/etc. would have
  disturbed the working Tier-1 path based on a false diagnosis.

## Out of scope (do not start without an explicit request)

- **Live flight data integration** — Ryanair's fare calendar is used for the Spain sweep only;
  flight data for other destinations remains out of scope (a real flight-price API/search, or
  surfacing a hotel only when a cheap flight exists in-window). The skeptic's `flight_cost_eur_total`/`ground_transport_eur`
  (see Stage 3 above) are an LLM *estimate* folded into scoring, not live flight data — that
  distinction stays intentional; don't casually upgrade one into the other.
- **Package operators** — scrape Bulgarian-market charter operators for unsold allocations.

## Style

Flat functions, plain stdlib + `requests`, clear names, short modules. Match the existing
tone. Prefer editing in place over adding files. Comment only the non-obvious (a hidden
constraint, a threshold's rationale, a workaround). No emoji in code; `city_signals.md`
and email HTML may use them.
