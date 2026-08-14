# CLAUDE.md

Guidance for Claude Code when working in this repository.

## What this is

A personal travel deal-finder for a family of 3 (2 adults + 4-year-old) based near Plovdiv,
Bulgaria. It runs every 3 days on free GitHub Actions, emails immediately when something genuinely
exceptional is found, and is silent the rest of the time. No server, no real database — just
JSON state files committed back by CI.

It is a deliberate **Pareto build**: small, flat, readable scripts over clever abstractions.
If a change adds a framework, a class hierarchy, or a layer of indirection to save a few
lines, it's probably wrong for this repo. One genuine find a year justifies the whole thing.

## Active product: the Diamond Finder

`find_city_anomalies.py` is the only script that runs automatically (every 3 days via `daily.yml`).
It is self-contained: no baseline data; Stage-3 grounding uses Booking.com (apidojo)
live rates, falling back to LLM concierge on any failure.

```
find_city_anomalies.py
  │
  ├─ Memory load — state/memory.json
  │    Baselines (realistic prices from past verifications) + outcome ledger
  │    (past corrections and kills). Injected as {memory} into all three stage prompts.
  │
  ├─ Stage 1 · FIND (L.call_llm, want_search=True)
  │    Score candidates 0–100. Each candidate includes est_price_eur (structured number —
  │    NOT extracted from prose). Anchored to CITIES but can extend to nearby destinations.
  │
  ├─ Gate — FIND score >= STAGE1_MIN_SCORE (triage only). NO price filter: there is no hard
  │    price ceiling anywhere in the pipeline; price is handled by the deterministic scorer.
  │
  ├─ Stage 2 · GROUND (ground_deal seam) — one call per gate survivor (BEFORE scoring)
  │    Primary: `providers.ground_api()` — Booking.com (apidojo) live rates, no LLM call.
  │    Fallback: `_ground_llm` (L.call_llm, want_search=True) — LLM concierge.
  │    Returns verdict: confirm | correct | kill, plus options[], how_to_book, grounding,
  │    assistant_summary, confidence. A kill drops the candidate here. A confirm/correct
  │    merges the REAL price onto the candidate and forwards it to the scorer — UNLESS a
  │    DATA-QUALITY guard blocks it (confidence=low or grounded dates out of window; NOT
  │    price). Blocked entries are logged in city_signals.md and never reach the scorer.
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
  │                  hotel+flight+ground all-in per night for a fly destination. Falls back to
  │                  regional par if the LLM gave no usable normal_price_eur)
  │      price_adj = min(PRICE_BONUS_CAP, PRICE_SCORE_WEIGHT*discount)
  │                  (bonus capped, penalty UNCAPPED — overpriced deals sink on their own)
  │      transit_adj = ±TRANSIT_TIER1_BONUS (drivable Tier-1 vs fly Tier-2) — a small TIME/HASSLE
  │                  nudge only; the money cost of flying is now priced into price_adj above.
  │    Desirability itself is calibrated so "nearby, easy, nothing special" lands ~45-55, not
  │    ~80+ — 80+ is reserved for a genuine standout property/experience, not merely low friction.
  │    tier (config.tier_for): DIAMOND needs ALL of final>=DIAMOND_SCORE_THRESHOLD,
  │    llm_score>=DIAMOND_MIN_LLM_SCORE, AND discount>=DIAMOND_MIN_DISCOUNT — so an ordinary
  │    local weekend, however cheap, is at most GOOD (>=GOOD_SCORE_THRESHOLD), else skip.
  │    The LLM never vetoes or tiers — a low final score is dropped by default, but every
  │    score is recorded. Same €85 can be a diamond for a standout (high llm_score) and a
  │    skip for an ordinary place. The scorer also emits a descriptive DOSSIER per candidate
  │    (`about` = what the property/location is + amenities + what a family does there;
  │    `value_case` = why the grounded price is/isn't a deal vs normal rates & alternatives),
  │    surfaced verbatim in the email so a human can judge an unfamiliar property. Descriptive
  │    only — must not move the score.
  │
  ├─ Memory write — state/memory.json + state/memory.md
  │    Every run: record_outcome per gate survivor with llm_score + final_score and a verdict
  │    of its tier (diamond/good/skip), or "kill" (grounding kill) / "blocked" (guard).
  │    record_baseline for every grounded confirm/correct that is high-confidence + in-window
  │    (even skips — the price is real). prune() + save().
  │
  ├─ Anti-spam gate — state/signals_seen.json
  │    Keyed by PROPERTY-IDENTITY (hotel_name→city→label, punctuation-stripped) + coarse
  │    SEASON (M.season_key of the window) + TIER, SIGNAL_TTL_DAYS TTL. EVERY scored candidate
  │    (diamond/good/skip) passes through this gate — a candidate is "new" if that
  │    property+season has not been emailed at that tier within the TTL. Season (not the exact
  │    window) and a normalised identity (not FIND's drifting free-text label) mean the same
  │    hotel with shifting dates/names no longer re-alerts; a TIER CHANGE (skip→good when the
  │    price drops) still re-notifies. A single WILDCARD (the best non-local find) can bypass
  │    suppression to keep variety visible.
  │
  ├─ Email (common.send_email) — an honest daily digest of EVERY scored candidate
  │    One email per run, fired whenever ≥1 scored candidate is new/tier-changed (any tier,
  │    incl. skip). Items grouped diamond→good→skip; each shows its tier badge, score
  │    breakdown (llm · price · transit = final), live all-in price, the scorer's `about`
  │    description + a "Why it's a deal" value-case callout, a "typically ~€X/night"
  │    comparison from PRIOR baselines, a child-price caveat for hotels, and the booking link.
  │    MAX_EMAILS_PER_RUN caps only the actionable diamond/good picks; skips are context and
  │    always shown in full. One item may carry a 🃏 WILDCARD badge — the best non-local find
  │    (fly/far destination or non-hotel), surfaced regardless of tier so the digest visibly
  │    reaches beyond the usual drivable towns. A "seen & dropped" footer lists that run's grounding kills /
  │    guard blocks (destination + reason) so the digest reflects everything the pipeline
  │    looked at. Conscience note if monthly count >= 8.
  │
  └─ Always writes
       state/city_signals.json  — all Stage 1 candidates + full score breakdown (hunt: false)
       state/city_signals.md    — human-readable log with grounding + score/tier breakdown
       state/signals_seen.json  — updated TTL state
       state/memory.json        — baselines + outcome ledger (updated every run)
       state/memory.md          — human-readable memory digest
       state/deals_history.json — one record per emailed deal (diamond/good/skip), for web/
```

## Files — active pipeline

| File | Role |
|---|---|
| `config.py` | City list + diamond-finder knobs; per-stage provider overrides; token budgets; prompts. **Names no model** — the `# LLM models` block is deliberately empty and points at the `llm-chain` package |
| `common.py` | `send_email()`, `parse_json_block()`, state IO. **No LLM code** — that lives in the `llm-chain` package |
| `memory.py` | `load()`/`save()`; `record_baseline()`/`record_outcome()`/`prune()`; `summarize_for_prompt()` |
| `find_city_anomalies.py` | The diamond finder — runs every 3 days, emails a digest of every scored candidate (diamond/good/skip) + a dropped footer |
| `providers.py` | Booking.com (apidojo) Stage-2 grounding: `ground_api()`, `resolve_hotel()`, `price()`, `list_properties()` |
| `.github/workflows/daily.yml` | Runs the diamond finder at 06:00 UTC every 3 days (`cron: "0 6 */3 * *"`); commits `state/` |
| `state/city_signals.json` | Latest Stage 1 output (machine-readable) |
| `state/city_signals.md` | Stage 1–3 output (human-readable log with Stage 3 verification outcomes) |
| `state/signals_seen.json` | Anti-spam TTL memory: `property-identity\|season\|tier → date_emailed`, monthly count |
| `state/memory.json` | Price baselines + outcome ledger (grows every run, pruned at 200 entries / 180 days) |
| `state/memory.md` | Human-readable digest of memory.json |
| `state/deals_history.json` | One dossier record per emailed deal (diamond/good/skip) — full about/value_case/options/red_flags, not just scores. Appended every run a digest is sent, pruned at `DEALS_HISTORY_MAX_ENTRIES`/`DEALS_HISTORY_MAX_DAYS` (config.py). Data source for `web/` |
| `web/` | Standalone React (Vite) app to browse `deals_history.json` — see "Web UI" below |

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

## Critical invariants — do not break these

- **All LLM calls go through `llm_chain.call_llm()`** (`import llm_chain as L`). Do not call
  provider HTTP endpoints directly, and do not reintroduce an LLM path in `common.py` — its
  LLM half was deleted in the llm-chain migration precisely to remove the duplicated plumbing.
  `common.py` now owns `send_email()`, state IO and `parse_json_block()` only.
- **No model name appears anywhere in this repo.** `grep -rn "claude-\|gemini-" --include=*.py .`
  must return nothing. Models live in `LLM_MODEL_CHAIN` / `LLM_SEARCH_MODEL_CHAIN` repo
  variables, resolved by `llm_chain`. The `# LLM models` block in `config.py` is deliberately
  empty and points there.
- **Falling back is not failing.** `LLMResult.fell_back=True` with `ok=True` means the chain
  advanced past a shedding model and got a real answer. Never surface that as degradation or
  a warning in the email; only `ok=False` is a failed stage.
- **All email goes through `common.send_email()`.** Single SMTP path. No duplication.
- **State files in `state/` are CI-managed.** `city_signals.json`, `city_signals.md`,
  `signals_seen.json`, `memory.json`, `memory.md`, `deals_history.json` are committed after
  each run. They are real state, not scratch. Seed values: `{}` / `{"seen":{}, "monthly_count":{}}` /
  `{"baselines": {}, "ledger": []}` / `{"entries": []}`.
- **`deals_history.json` is appended to, never overwritten, and only from `to_email`** (the
  exact list the digest renders) — so it stays an honest "everything that made it to the
  email" record for `web/`. Do not populate it from `scored_all` or any pre-anti-spam-gate
  list; that would show deals the user was never actually notified about.
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
  `DIAMOND_PAR_EUR`/`DEFAULT_DIAMOND_PAR_EUR` remain ONLY as the fallback reference when the LLM
  gave no usable `normal_price_eur`.
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
- **The scoring knobs live in config, nowhere else** (`DIAMOND_PAR_EUR`/`DEFAULT_DIAMOND_PAR_EUR`
  [fallback reference only], `PRICE_SCORE_WEIGHT`, `PRICE_BONUS_CAP`, `TRANSIT_TIER1_BONUS`/`TIER2`,
  `DIAMOND_SCORE_THRESHOLD`, `GOOD_SCORE_THRESHOLD`, `DIAMOND_MIN_LLM_SCORE`, `DIAMOND_MIN_DISCOUNT`).
  The price bonus is capped; the penalty is UNCAPPED, which is *why there is no hard price ceiling* —
  overpriced deals sink to skip on their own.
- **`STAGE1_MIN_SCORE = 80`** is the gate into grounding — pure triage on FIND's estimate to
  bound grounding cost. NO price filter at the gate.
- **FIND scoring is triage; the scorer is authoritative.** FIND's score only decides who gets
  grounded. The Stage-3 scorer re-scores from the real price + full context; its score (plus
  modifiers) is what tiers the deal.
- **Gemini token budgets carry thinking-token headroom.** `maxOutputTokens` caps hidden
  thinking + visible output combined; if it runs out mid-answer the JSON truncates
  (`finishReason=MAX_TOKENS`) and parses to nothing — indistinguishable from a quiet day.
  `llm_chain` flags that case as `LLMResult.truncated`; `MAX_TOKENS_FIND/SKEPTIC/VERIFY`
  are set well above observed thinking usage (~3-4k). If you see it flagged, raise them.
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
  "10-14 Sep 2026" forms, and returns None on a backwards range. A format the parser misses makes
  apidojo silently fail and every candidate fall to the (costlier, less price-accurate) LLM
  fallback — so if you change FIND's `window` wording, extend the parser and its regression tests
  (`test_providers.py`) in the same change.
- **Every scored candidate's breakdown is recorded** (`llm_score`, `final_score`, tier) in the
  ledger, `city_signals.json`, and the run log — deliberately, so a deal that scored 69 at €86
  and 74 at €79 keeps its history rather than being lost to a veto. Ledger verdicts:
  diamond/good/skip (scored), `kill` (grounding kill), `blocked` (guard).
- **Baselines are only written** when grounding confidence is "high" AND the grounded option
  dates fall within the candidate window (rough season_key match) — recorded for every such
  grounded confirm/correct regardless of the tier (even a skip: the price is real).
  Low-confidence or out-of-window verifications produce unreliable data — never stored.
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
  (`memory.json` by `destination|season`; `signals_seen.json` by `property-identity|season|tier`
  via `seen_key`). It appears in `city_signals.json`
  (regenerated each run) for traceability only.
- **The email is an honest digest of EVERY scored candidate, not a diamond/good-only alarm.**
  It shows diamonds, good finds AND skips — each with its full score breakdown — so the user
  builds a mental model of what the pipeline sees and why, and can human-override a
  low-scored deal (e.g. a Rome skip that's useful if they were going anyway). It fires
  whenever ≥1 scored candidate is new or has changed tier since the last email (any tier).
  Anti-spam TTL is keyed `property-identity|season|tier` (see `seen_key`), so a recurring
  same-property skip stays quiet even as its label/exact dates drift, but a skip→good upgrade
  re-notifies. One best non-local find may carry a 🃏 wildcard badge (may bypass suppression).
  Only diamond/good picks are capped by MAX_EMAILS_PER_RUN; skips are always shown. Grounding
  kills / guard blocks appear in a
  compact "seen & dropped" footer (no email is sent purely for a kill). A day with nothing
  new (or nothing found) still sends nothing.
- **`city_signals.json` always has `hunt: false`.** The diamond finder does not trigger
  hotel crawls. The field exists for schema compatibility only.
- **Memory is written every run**, including silent days. `memory.py` functions must
  not be called with None memory dict; always `M.load()` first.

## The LLM layer (`llm-chain`)

All LLM calls go through the **`llm-chain`** package — `import llm_chain as L; L.call_llm(...)`.
It is a shared dependency (`llm-chain @ git+https://github.com/josephararil/llm-chain@v1` in
`requirements.txt`), also used by the sibling `weekly-concierge` repo. It is **not a file in
this repo**, and it is **the only place a model name exists** for this project.

```python
L.call_llm(prompt, *, stage="", max_tokens=4000, want_search=False,
           search_prompt=None, search_preamble=None, response_schema=None,
           provider=None, web_search_max_uses=6) -> LLMResult
```

`prompt` is a plain **string** (one user message), not a messages list. It returns
`LLMResult(text, ok, model, provider, fell_back, grounded, truncated, attempts, error)`
and **never raises on a provider failure** — callers branch on `.ok`.

**Why it exists.** The previous `common.llm()` retried a single model roughly four times
over ~14 seconds and had no cross-model fallback. On 2026-08-14 `weekly-concierge` emailed
a blank report because one model returned 503 on 13 of 14 calls while a different model
served 3/3 on the same key in the same window. `llm_chain` retries the same model on a
transient status, then **advances to the next model in the chain**.

- **Chain, not per-stage pinning.** Every stage starts at `LLM_MODEL_CHAIN[0]` and advances
  on failure. No model is bound to a task. Per-stage model roles (`MODEL_FIND/SKEPTIC/VERIFY`)
  were deleted — all three had the same value, so collapsing them lost nothing.
- **No name mapping.** Model names reach the API verbatim. The old `GEMINI_MODEL_MAP` used
  `.get(model, <fallback>)`, so a typo'd name silently resolved to a different model and the
  log named one that never served the call. Now a wrong name 404s and advances, visibly.
- **Every knob is an `LLM_*` env var read at call time** — tune the chain from GitHub repo
  variables with no code change. `daily.yml` passes all eleven through whether or not the
  variable is set; a knob **missing** from that env block fails **silently**.
- `python -m llm_chain` prints the resolved config, every model the key can list, and one
  live ping.

**Gemini search/reasoning split** (unchanged in behaviour, now implemented inside
`llm_chain`): `want_search=True` runs two calls. Search runs on `LLM_SEARCH_MODEL_CHAIN`
(lite tier) with the `google_search` tool — the only tier that survives Google's grounding
gateway; flagship models time out ~99% of the time with `google_search` attached. Reasoning
then runs on `LLM_MODEL_CHAIN` with **no tools** and the `responseSchema` (the two features
conflict, which is the other reason for the split). Stage 1 passes its lead-generation
`SEARCH_PROMPT` via `search_prompt=`; `SEARCH_RESULTS_PREAMBLE` is passed via
`search_preamble=` and frames the leads as a **seed, not a fence**. If search fails, reasoning
proceeds knowledge-only. On Anthropic the flagship searches inline via `FIND_PROMPT`, whose
`{search_directive}` slot is filled from `L.resolved_provider(C.PROVIDER_FIND)`.

- Optional per-stage provider overrides: `PROVIDER_FIND / PROVIDER_SKEPTIC / PROVIDER_VERIFY`
  in `config.py` (all `None` = use global `LLM_PROVIDER`), passed through as `provider=`.
- `response_schema` (Gemini only): schemas live in `config.py` as `STAGE1/2/3_RESPONSE_SCHEMA`.

**Budget.** `LLM_TOTAL_BUDGET_SECONDS` (default 1200) is a hard wall-clock ceiling across
every LLM call in the process; the clock starts at the **first** `call_llm`, not at import,
so state loading and hotel grounding do not eat the LLM budget. `main()` calls
`L.reset_budget()` at the top. `daily.yml` is `timeout-minutes: 30` — 20 minutes of LLM
budget plus headroom for apidojo grounding and SMTP.

**Reporting.** `find_city_anomalies._record_stage()` accumulates `(stage, LLMResult)` into
`STAGE_RESULTS` at each of the three call sites and prints which model actually served each
stage at `RUN COMPLETE`. A stage that **fell back** to a later model **succeeded** — that is
the chain working, and is deliberately never reported as failure or degradation. Only a stage
with no usable answer from any model is `[DEGRADED]`.

## Required secrets / variables

| Name | Type | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | secret | Anthropic LLM calls |
| `GEMINI_API_KEY` | secret | Gemini LLM calls |
| `LLM_PROVIDER` | repo variable | `"anthropic"` or `"gemini"` |
| `LLM_MODEL_CHAIN` | repo variable | Reasoning fallback chain, comma-separated. Unset = `llm_chain` default |
| `LLM_SEARCH_MODEL_CHAIN` | repo variable | Search (google_search) fallback chain. Lite tier only |
| `LLM_ATTEMPTS_PER_MODEL` | repo variable | Retries per model before advancing. Prefer LOW — the chain is the resilience mechanism, the retry loop is only for a genuine blip |
| `LLM_BACKOFF_SECONDS` | repo variable | Backoff ladder between attempts |
| `LLM_TIMEOUT_SECONDS` | repo variable | Per-request HTTP timeout |
| `LLM_RETRY_STATUSES` | repo variable | Statuses retried on the same model |
| `LLM_ADVANCE_STATUSES` | repo variable | Statuses that advance to the next model. 401/403 are in neither — auth errors fail fast by design |
| `LLM_TOTAL_BUDGET_SECONDS` | repo variable | Wall-clock ceiling across all LLM calls (default 1200) |
| `LLM_RETRY_AFTER_CAP` | repo variable | Cap on an honoured `Retry-After` header |
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

## Running locally

```bash
pip install -r requirements.txt
export GEMINI_API_KEY=...  LLM_PROVIDER=gemini
# or: export ANTHROPIC_API_KEY=...  LLM_PROVIDER=anthropic
python find_city_anomalies.py   # writes state/; emails if diamonds found + SMTP vars set
```

To test without sending email, leave SMTP vars unset — the `try/except` around the send
catches the `KeyError` and prints the error without crashing.

To test the three-stage gate offline: stub `llm_chain.call_llm` to return an `LLMResult`
carrying canned JSON for each
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
- **30-day TTL:** a great deal that persists for more than a month will be suppressed after
  the first email. Acceptable given the "rare, act-now" framing.
- **Family-only scope.** Destinations that require arduous travel or are poor fits for a
  4-year-old are excluded by the skeptic prompt. This is intentional, not a filter bug.

## Out of scope (do not start without an explicit request)

- **Live flight data integration** — a real flight-price API/search, or surfacing a hotel only
  when a cheap flight exists in-window. The skeptic's `flight_cost_eur_total`/`ground_transport_eur`
  (see Stage 3 above) are an LLM *estimate* folded into scoring, not live flight data — that
  distinction stays intentional; don't casually upgrade one into the other.
- **Package operators** — scrape Bulgarian-market charter operators for unsold allocations.

## Style

Flat functions, plain stdlib + `requests`, clear names, short modules. Match the existing
tone. Prefer editing in place over adding files. Comment only the non-obvious (a hidden
constraint, a threshold's rationale, a workaround). No emoji in code; `city_signals.md`
and email HTML may use them.
