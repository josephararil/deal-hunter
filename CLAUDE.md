# CLAUDE.md

Guidance for Claude Code when working in this repository.

## What this is

A personal travel deal-finder for a family of 3 (2 adults + 4-year-old) based near Plovdiv,
Bulgaria. It runs daily on free GitHub Actions, emails immediately when something genuinely
exceptional is found, and is silent the rest of the time. No server, no real database — just
JSON state files committed back by CI.

It is a deliberate **Pareto build**: small, flat, readable scripts over clever abstractions.
If a change adds a framework, a class hierarchy, or a layer of indirection to save a few
lines, it's probably wrong for this repo. One genuine find a year justifies the whole thing.

## Active product: the Diamond Finder

`find_city_anomalies.py` is the only script that runs automatically (daily via `daily.yml`).
It is self-contained: no baseline data; Stage-3 grounding uses Booking.com (apidojo)
live rates, falling back to LLM concierge on any failure.

```
find_city_anomalies.py
  │
  ├─ Memory load — state/memory.json
  │    Baselines (realistic prices from past verifications) + outcome ledger
  │    (past corrections and kills). Injected as {memory} into all three stage prompts.
  │
  ├─ Stage 1 · FIND (llm, want_search=True, model=MODEL_FIND)
  │    Score candidates 0–100. Each candidate includes est_price_eur (structured number —
  │    NOT extracted from prose). Anchored to CITIES but can extend to nearby destinations.
  │
  ├─ Gate — FIND score >= STAGE1_MIN_SCORE (triage only). NO price filter: there is no hard
  │    price ceiling anywhere in the pipeline; price is handled by the deterministic scorer.
  │
  ├─ Stage 2 · GROUND (ground_deal seam) — one call per gate survivor (BEFORE scoring)
  │    Primary: `providers.ground_api()` — Booking.com (apidojo) live rates, no LLM call.
  │    Fallback: `_ground_llm` (want_search=True, model=MODEL_VERIFY) — LLM concierge.
  │    Returns verdict: confirm | correct | kill, plus options[], how_to_book, grounding,
  │    assistant_summary, confidence. A kill drops the candidate here. A confirm/correct
  │    merges the REAL price onto the candidate and forwards it to the scorer — UNLESS a
  │    DATA-QUALITY guard blocks it (confidence=low or grounded dates out of window; NOT
  │    price). Blocked entries are logged in city_signals.md and never reach the scorer.
  │    Grounding is swappable: `HOTEL_PROVIDER=""` forces LLM-only. Same
  │    `ground_deal(diamond, mem_text, today)` signature.
  │
  ├─ Stage 3 · SCORE (llm scorer + deterministic modifiers, model=MODEL_SKEPTIC)
  │    The LLM returns a 0–100 DESIRABILITY score per grounded candidate (price held
  │    neutral — it is told the pipeline handles price). The pipeline then applies
  │    deterministic modifiers (config.compute_final_score):
  │      final = clamp(0,100, llm_score + price_adj + transit_adj)
  │      price_adj = min(PRICE_BONUS_CAP, PRICE_SCORE_WEIGHT*(1 - grounded_ppn/par))
  │                  (bonus capped, penalty UNCAPPED — overpriced deals sink on their own)
  │      transit_adj = ±TRANSIT_TIER1_BONUS (drivable Tier-1 vs fly Tier-2)
  │    tier = diamond (final>=DIAMOND_SCORE_THRESHOLD) | good (>=GOOD_SCORE_THRESHOLD) | skip.
  │    The LLM never vetoes or tiers — a low final score is dropped by default, but every
  │    score is recorded. Same €85 can be a diamond for a standout (high llm_score) and a
  │    skip for an ordinary place.
  │
  ├─ Memory write — state/memory.json + state/memory.md
  │    Every run: record_outcome per gate survivor with llm_score + final_score and a verdict
  │    of its tier (diamond/good/skip), or "kill" (grounding kill) / "blocked" (guard).
  │    record_baseline for every grounded confirm/correct that is high-confidence + in-window
  │    (even skips — the price is real). prune() + save().
  │
  ├─ Anti-spam gate — state/signals_seen.json
  │    Keyed by destination+window, SIGNAL_TTL_DAYS TTL. Prevents repeat emails.
  │    Only diamond/good picks reach this gate.
  │
  ├─ Email (common.send_email) — a short digest whenever any diamond/good pick is new
  │    One email per run, max MAX_EMAILS_PER_RUN picks (diamonds sorted first). Each item
  │    shows its tier badge, live all-in price, a "typically ~€X/night" comparison from
  │    PRIOR baselines, a child-price caveat for hotels, and the booking link.
  │    Conscience note in the email if monthly count >= 8.
  │
  └─ Always writes
       state/city_signals.json  — all Stage 1 candidates + full score breakdown (hunt: false)
       state/city_signals.md    — human-readable log with grounding + score/tier breakdown
       state/signals_seen.json  — updated TTL state
       state/memory.json        — baselines + outcome ledger (updated every run)
       state/memory.md          — human-readable memory digest
```

## Files — active pipeline

| File | Role |
|---|---|
| `config.py` | City list + diamond-finder knobs; per-stage model roles (`MODEL_FIND/SKEPTIC/VERIFY`); per-stage provider overrides; prompts |
| `common.py` | `llm()`, `send_email()`, `parse_json_block()`, state IO |
| `memory.py` | `load()`/`save()`; `record_baseline()`/`record_outcome()`/`prune()`; `summarize_for_prompt()` |
| `find_city_anomalies.py` | The diamond finder — runs daily, emails a tiered diamond/good digest |
| `providers.py` | Booking.com (apidojo) Stage-2 grounding: `ground_api()`, `resolve_hotel()`, `price()`, `list_properties()` |
| `.github/workflows/daily.yml` | Runs the diamond finder at 06:00 UTC; commits `state/` |
| `state/city_signals.json` | Latest Stage 1 output (machine-readable) |
| `state/city_signals.md` | Stage 1–3 output (human-readable log with Stage 3 verification outcomes) |
| `state/signals_seen.json` | Anti-spam TTL memory: `destination\|window → date_emailed`, monthly count |
| `state/memory.json` | Price baselines + outcome ledger (grows every run, pruned at 200 entries / 180 days) |
| `state/memory.md` | Human-readable digest of memory.json |

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

- **All LLM calls go through `common.llm()`.** Abstracts Anthropic vs Gemini via
  `LLM_PROVIDER`. Do not call provider HTTP endpoints directly.
- **All email goes through `common.send_email()`.** Single SMTP path. No duplication.
- **State files in `state/` are CI-managed.** `city_signals.json`, `city_signals.md`,
  `signals_seen.json`, `memory.json`, `memory.md` are committed after each run.
  They are real state, not scratch. Seed values: `{}` / `{"seen":{}, "monthly_count":{}}` /
  `{"baselines": {}, "ledger": []}`.
- **Grounding runs BEFORE scoring.** Stage 2 grounds live prices; Stage 3 scores those live
  prices. Core design decision — the scorer must never grade a Stage-1 *estimate*. Preserve
  this if you touch the pipeline order.
- **The final tier is deterministic, NOT the LLM's call.** The Stage-3 LLM returns only a
  0–100 `score` (nightly hotel price held neutral — the prompt tells it so). The pipeline
  computes `final = llm_score + price_adj + transit_adj` (`config.compute_final_score`) and
  derives the tier via `tier_for_score`. Do not let the LLM emit a tier or a veto — that was
  deliberately removed so scores stay comparable and every one is recorded for tuning.
- **The LLM score is NET FAMILY VALUE DELIVERED, not luxury/prestige** (the numerator; the
  price modifier is the denominator). A modest low-friction local break can outscore a
  glamorous far-flung one. Attraction is one modest input for a 4-year-old. Because flights
  are out of scope and NOT in the grounded hotel price, the scorer is the only stage that can
  weigh flight cost/hassle — so a no-direct-PDV destination is penalised in the score itself,
  not just by the small transit nudge. If you ever add flight data, revisit this.
- **The scoring knobs live in config, nowhere else** (`DIAMOND_PAR_EUR`/`DEFAULT_DIAMOND_PAR_EUR`,
  `PRICE_SCORE_WEIGHT`, `PRICE_BONUS_CAP`, `TRANSIT_TIER1_BONUS`/`TIER2`, `DIAMOND_SCORE_THRESHOLD`,
  `GOOD_SCORE_THRESHOLD`). `par` is a reference, NOT a wall: a standout property (high llm_score)
  can be a diamond above par; an ordinary one cannot. The price bonus is capped; the penalty is
  UNCAPPED, which is *why there is no hard price ceiling* — overpriced deals sink to skip on their own.
- **`STAGE1_MIN_SCORE = 80`** is the gate into grounding — pure triage on FIND's estimate to
  bound grounding cost. NO price filter at the gate.
- **FIND scoring is triage; the scorer is authoritative.** FIND's score only decides who gets
  grounded. The Stage-3 scorer re-scores from the real price + full context; its score (plus
  modifiers) is what tiers the deal.
- **Gemini token budgets carry thinking-token headroom.** `maxOutputTokens` caps hidden
  thinking + visible output combined; if it runs out mid-answer the JSON truncates
  (`finishReason=MAX_TOKENS`) and parses to nothing — indistinguishable from a quiet day.
  `common._gemini` warns on any non-STOP finishReason; `MAX_TOKENS_FIND/SKEPTIC/VERIFY`
  are set well above observed thinking usage (~3-4k). If you see the warning, raise them.
- **Grounding (Stage 2) only removes candidates, never adds them.** A grounding kill means the
  deal was hallucinated — it never reaches the scorer or email. `verdict: correct` (price was
  wrong) still forwards the corrected figures to the scorer; do not treat `correct` as a kill.
  Grounding no longer kills on price (no ceiling) — only data-quality guards (low confidence,
  dates out of window) block a grounded candidate from scoring.
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
  key `signals_seen.json` or `memory.json`; those stay keyed by `destination|window`/season
  to survive across runs. It appears in `city_signals.json` (regenerated each run) for
  traceability only.
- **The email is an honest tiered digest, not a rare "diamond-only" alarm.** It fires on
  any new diamond OR good pick, so near-daily email is expected and fine — the value is that
  each item is graded honestly (💎 vs 👍) with the live price, a baseline comparison, and
  caveats, so the user judges in seconds. A true diamond stays rare; good finds are common.
  A day with only skips (or nothing found) still sends nothing.
- **`city_signals.json` always has `hunt: false`.** The diamond finder does not trigger
  hotel crawls. The field exists for schema compatibility only.
- **Memory is written every run**, including silent days. `memory.py` functions must
  not be called with None memory dict; always `M.load()` first.

## Providers

`common.llm(messages, model, max_tokens, want_search, provider=None)` — single entry point.

- `LLM_PROVIDER=anthropic` (default): Messages API; `want_search` → `web_search` tool.
- `LLM_PROVIDER=gemini`: `generateContent` API. When `want_search=True`, search and
  reasoning are **split across two calls** (`_gemini` / `_gemini_search` in `common.py`):
  1. **Search** runs on `GEMINI_SEARCH_MODEL` (config; default `gemini-3.1-flash-lite`)
     with the `{"google_search": {}}` tool. This is the only Gemini tier that survives
     Google's grounding gateway — flagship models (`flash-latest`/`pro-latest`) time out
     ~99% of the time when `google_search` is attached. The search step optimizes for
     **fresh, varied leads, not accuracy**: Stage 1 passes a dedicated `SEARCH_PROMPT`
     (lead-generation brief) via the `search_prompt` arg; other stages fall back to
     wrapping the stage text in a generic search directive.
  2. **Reasoning** runs on the mapped flagship model with **no tools** (and the
     `responseSchema`, if any). The grounded leads from step 1 are framed by
     `SEARCH_RESULTS_PREAMBLE` (injected via `.replace`, so leads with braces are safe)
     and prepended to the stage prompt. The preamble treats the leads as a **seed, not a
     fence** — the reasoner also draws on its own knowledge and must not return an empty
     answer just because leads are thin. If the search call fails it returns `""` and
     reasoning proceeds knowledge-only — graceful degradation.
  This split also keeps `responseSchema` off the search call (the two features conflict).
  `SEARCH_PROMPT` / `SEARCH_RESULTS_PREAMBLE` are Gemini-only; on Anthropic the flagship
  searches inline via `FIND_PROMPT`. `FIND_PROMPT`'s `{search_directive}` slot keeps it
  honest per provider: Anthropic gets `SEARCH_DIRECTIVE_ANTHROPIC` (forceful "use your
  web_search tool"); Gemini gets `""` (the preamble owns its framing), so no model ever
  reads a tool instruction that is false for it. Filled in `find_city_anomalies.py` via
  `common.resolved_provider(C.PROVIDER_FIND)`.
- Per-stage model roles are in `config.py` as `MODEL_FIND`, `MODEL_SKEPTIC`, `MODEL_VERIFY`.
  Gemini equivalents are mapped in `GEMINI_MODEL_MAP`; the search model is
  `GEMINI_SEARCH_MODEL`. Three Gemini models total — search (lite), Find (`flash-latest`),
  Skeptic+Verify (`pro-latest`). Add new roles there, never as literals in pipeline code.
- Optional per-stage provider overrides: `PROVIDER_FIND / PROVIDER_SKEPTIC / PROVIDER_VERIFY`
  (all default to `None` = use global `LLM_PROVIDER`).
- `response_schema` (Gemini only): JSON Schema passed as `response_format` to constrain
  output to valid JSON. Schemas for all three stages live in `config.py` as
  `STAGE1/2/3_RESPONSE_SCHEMA`. Anthropic path ignores these (prompt engineering suffices).

## Required secrets / variables

| Name | Type | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | secret | Anthropic LLM calls |
| `GEMINI_API_KEY` | secret | Gemini LLM calls |
| `LLM_PROVIDER` | repo variable | `"anthropic"` or `"gemini"` |
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

To test the three-stage gate offline: stub `common.llm` to return canned JSON for each
stage (including a `correct` and a `kill` case for Stage 3), then run the script and
inspect `state/city_signals.md`, `state/signals_seen.json`, and `state/memory.json`.

## Known trade-offs (accepted — don't "fix" without asking)

- **No price data.** The diamond finder is pure LLM reasoning + web search. It can miss
  deals that don't appear in search results, and can hallucinate if search is weak. The
  three-stage gate and self-improving memory exist to compensate.
- **Gemini + search:** `google_search` quality and behaviour differ from Anthropic's
  `web_search`, and grounding runs on a separate lite model (`GEMINI_SEARCH_MODEL`) because
  flagship models time out on Google's grounding gateway. If the search call fails, the
  flagship reasoning step still runs — just from prior knowledge rather than live data.
- **30-day TTL:** a great deal that persists for more than a month will be suppressed after
  the first email. Acceptable given the "rare, act-now" framing.
- **Family-only scope.** Destinations that require arduous travel or are poor fits for a
  4-year-old are excluded by the skeptic prompt. This is intentional, not a filter bug.

## Out of scope (do not start without an explicit request)

- **Flight data integration** — surface a hotel only when a cheap flight exists in-window.
- **Package operators** — scrape Bulgarian-market charter operators for unsold allocations.

## Style

Flat functions, plain stdlib + `requests`, clear names, short modules. Match the existing
tone. Prefer editing in place over adding files. Comment only the non-obvious (a hidden
constraint, a threshold's rationale, a workaround). No emoji in code; `city_signals.md`
and email HTML may use them.
