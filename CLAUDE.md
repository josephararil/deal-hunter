# CLAUDE.md

Project context for Claude Code. Read this before editing.

## What this is

A personal hotel deal-finder for someone based near Plovdiv, Bulgaria. It surfaces
great-value *trips* — not just lone underpriced hotels, but whole cities that are unusually
cheap right now — and emails a weekly digest. Runs entirely on free GitHub Actions; there is
no server and no real database, just JSON state files committed back by CI.

It is deliberately a **Pareto build**: small, flat, readable scripts over clever abstractions.
Keep it that way. If a change adds a framework, a class hierarchy, or a layer of indirection to
save a few lines, it's probably wrong for this repo. One great deal a year and less manual
Booking scrolling is the whole success criterion.

## Mental model: an LLM sandwich, three pipelines

```
baseline_sampler.py   → seasonal memory: median price per city|class|month
find_city_anomalies.py→ PLANNER (LLM + web search): "where should I look?" → city_signals
hunt.py               → deep crawl of FLAGGED cities + 2 detectors + harsh LLM filter → digest
```

- **Front (planner):** the LLM decides *where* deals likely are (recurring troughs + live
  shocks). Cheap. Output is useful on its own.
- **Middle (deterministic, no LLM):** crawling + outlier math + ranking. Scales, cheap, exact.
- **Back (harsh filter):** one LLM call judges the shortlist and rejects boring low-season
  non-deals.

The math finds candidates; the LLM judges them. Never push large row sets through the LLM for
detection — that's slower, costlier, and numerically worse than the median/MAD math already in
`hunt.detect()`.

## The two detectors in hunt.py (the core value)

1. **Cross-sectional outlier** — a hotel far below its same-class peers *in the same crawl*
   (robust z-score via median + MAD). Catches a "crazy manager." Needs `MIN_PEERS`.
2. **Below seasonal baseline** — a hotel well under the `city|class|month` norm from
   `state/baselines.json`. Catches market-wide drops (whole city cheap) and absolute bargains,
   and works even when nothing is a *relative* outlier.

Both feed one shortlist. **Qualify by anomaly, rank by absolute EUR saved per night** — never
by percentage. Absolute-EUR ranking is intentional: it favours luxury-for-cheap (€140 off a
5-star beats €50 off a budget place). Don't "fix" this to percentage.

## Critical invariants — do not break these

- **All model calls go through `common.llm()`.** It abstracts Anthropic vs Gemini, selected by
  the `LLM_PROVIDER` env var. Do not call provider HTTP endpoints directly from the pipelines.
- **The A→B handoff is via `state/city_signals.json`.** `find_city_anomalies.py` writes it;
  `hunt.py` reads it and only crawls cities with `"hunt": true`. Keep that contract intact —
  changing the signal schema means updating both ends.
- **State files in `state/` are CI-managed.** `baselines.json`, `city_signals.json`,
  `city_signals.md`, `seen.json`, `pending_digest.json` are committed by the workflows after
  each run. They are real state, not scratch — don't delete or .gitignore them. Seed values
  (`{}`, `[]`) are the empty starting point.
- **`MIN_REVIEW_SCORE = 8.0` is a hard floor**, never relaxed anywhere. It's the single most
  trusted heuristic. Star rating is only ever used for *grouping* (apples-to-apples), never as
  a filter.
- **Apify field names are actor-specific.** The keys in `common.scrape()` (input) and
  `common.normalize()` (output) must match the chosen actor's schema (`config.APIFY_ACTOR`). If
  results come back empty, this mapping is the first suspect, not the logic.
- **patterns.json entries are priors, not facts.** The planner confirms/rejects them with live
  search + baselines. Don't write logic that treats a pattern as a guaranteed truth.

## Files

| File | Role |
|---|---|
| `config.py` | All tunables: cities + night ranges, occupancy, thresholds, model roles |
| `common.py` | Shared helpers: `scrape`, `normalize`, `llm`, `parse_json_block`, state IO |
| `baseline_sampler.py` | Pipeline 0 — daily cheap median capture, no LLM |
| `find_city_anomalies.py` | Pipeline A — planner, writes `city_signals.{json,md}` |
| `hunt.py` | Pipeline B — dual detector + harsh filter + weekly email digest |
| `patterns.json` | Seeded recurring price-window priors (editable) |
| `state/*` | CI-managed JSON state (see invariants) |
| `.github/workflows/daily.yml` | baseline → signals → hunt, daily; auto-triggers B on flags |
| `.github/workflows/manual-hunt.yml` | crawl named cities on demand |
| `.github/workflows/signals.yml` | signals-only, manual, no crawl/email (the cheap button) |

## Providers

`common.llm(messages, model, max_tokens, want_search)` is the single entry point.
- `LLM_PROVIDER=anthropic` (default) → Messages API; `want_search` enables the `web_search` tool.
- `LLM_PROVIDER=gemini` → `generateContent`; `want_search` enables `google_search`.
- Model *roles* live in `config.py` (`MODEL_PLANNER`, `MODEL_FILTER`); Gemini equivalents are
  mapped in `common.GEMINI_MODELS`. Add new roles there, not as literals in pipelines.

Secrets/vars: `APIFY_TOKEN`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY` (secrets), `LLM_PROVIDER`
(repo variable), plus SMTP_* and EMAIL_* for the digest.

## Running & testing locally

```bash
pip install -r requirements.txt
export APIFY_TOKEN=... GEMINI_API_KEY=... LLM_PROVIDER=gemini   # or anthropic + ANTHROPIC_API_KEY
python baseline_sampler.py        # fills state/baselines.json
python find_city_anomalies.py     # writes state/city_signals.{json,md}
python hunt.py                    # crawls flagged cities; FORCE_DIGEST=1 to email now
```

To hunt specific cities regardless of signals: `HUNT_CITIES="Antalya, Turkey, Vienna, Austria" python hunt.py`.

When changing detection or parsing logic, stub `common.scrape` and `common.llm` and run the
pipelines offline with a planted outlier to confirm behaviour before touching real APIs. There
is no test suite by design; a throwaway sim script is the expected way to verify.

## Known trade-offs (accepted — don't "fix" without asking)

- **Cold start:** market-drop detection is weak until `baselines.json` fills (a few weeks); the
  planner's reasoning carries it meanwhile. A louder, less precise first month is expected.
- **Weekly digest vs ephemerality:** crawl horizons are 10/17/24 days so weekday finds survive
  to the Sunday digest; sub-3-day fire-sales are intentionally out of scope.
- **Gemini + live search:** Gemini uses `google_search`, not Anthropic's `web_search`; the
  abstraction handles it, but search quality/behaviour differs between providers.
- **Board type** (all-inclusive) is unreliable from some actors; the final LLM is the backstop.

## Out of scope (Phase 2 — do not start without a request)

- **Flights** for fly-to cities (only surface a hotel when a cheap flight exists in-window).
- **Travel packages** (operators dumping unsold flight+hotel charters). Same architecture, new
  sources; ship and live with the hotel system first.

## Style

Flat functions, plain stdlib + `requests`, clear names, short modules. Match the existing tone
of the code. Prefer editing in place over adding files. State assumptions in comments where a
choice isn't obvious (e.g. why a threshold is set where it is). No emoji in code; the digest
HTML and `city_signals.md` may use them.
