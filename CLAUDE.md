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
It is self-contained: no Apify, no baseline data, no external crawlers.

```
find_city_anomalies.py
  │
  ├─ Stage 1 (llm, want_search=True)
  │    Score candidates 0–100 across: hotels, resort closeouts, post-event collapses,
  │    cruises, flight fares, package dumps, currency plays — anchored to CITIES but
  │    can extend to nearby destinations if a real opportunity exists.
  │
  ├─ Stage 2 (llm, want_search=False) — only candidates scoring >= STAGE1_MIN_SCORE
  │    Hostile skeptic reviewer. Returns keep/kill + why + red_flags.
  │    Most candidates should die here. Silence is correct.
  │
  ├─ Anti-spam gate — state/signals_seen.json
  │    Keyed by destination+window, 30-day TTL. Prevents repeat emails.
  │
  ├─ Email (common.send_email) — only if new diamonds survive
  │    One email per run, max MAX_EMAILS_PER_RUN diamonds.
  │    Conscience note in the email if monthly count >= 3.
  │
  └─ Always writes
       state/city_signals.json  — all Stage 1 candidates (hunt: false always)
       state/city_signals.md    — human-readable log; useful even on silent days
       state/signals_seen.json  — updated TTL state
```

## Files — active pipeline

| File | Role |
|---|---|
| `config.py` | City list + diamond-finder knobs |
| `common.py` | `llm()`, `send_email()`, `parse_json_block()`, state IO |
| `find_city_anomalies.py` | The diamond finder — runs daily, emails on exceptional finds |
| `.github/workflows/daily.yml` | Runs the diamond finder at 06:00 UTC; commits `state/` |
| `state/city_signals.json` | Latest Stage 1 output (machine-readable) |
| `state/city_signals.md` | Latest Stage 1 output (human-readable log) |
| `state/signals_seen.json` | Anti-spam TTL memory: `destination\|window → date_emailed`, monthly count |

## Files — dormant Apify pipeline

`_dormant/` contains the original hotel-crawling pipeline (Apify scraper, statistical
detectors, weekly digest). It does **not** run, is **not** imported, and is kept only as
a reference for a possible future Phase 2. See `_dormant/README.md` for what restoration
requires. Do not reference or revive any of it without an explicit request.

## Critical invariants — do not break these

- **All LLM calls go through `common.llm()`.** Abstracts Anthropic vs Gemini via
  `LLM_PROVIDER`. Do not call provider HTTP endpoints directly.
- **All email goes through `common.send_email()`.** Single SMTP path. No duplication.
- **State files in `state/` are CI-managed.** `city_signals.json`, `city_signals.md`,
  `signals_seen.json` are committed after each run. They are real state, not scratch.
  Seed values: `{}` / `{"seen":{}, "monthly_count":{}}`.
- **`STAGE1_MIN_SCORE = 80`** is the Stage 2 gate. Raise to make email rarer. Lower
  cautiously — it lets more through the hostile skeptic.
- **Silence is the intended outcome most days.** Don't treat low email volume as a bug.
  Only investigate if the prompts demonstrably fail to surface known real opportunities.
- **`city_signals.json` always has `hunt: false`.** The diamond finder does not trigger
  Apify crawls. The field exists for schema compatibility only.

## Providers

`common.llm(messages, model, max_tokens, want_search)` — single entry point.

- `LLM_PROVIDER=anthropic` (default): Messages API; `want_search` → `web_search` tool.
- `LLM_PROVIDER=gemini`: `generateContent`; `want_search` → `google_search` tool.
  Degrades gracefully if Gemini rejects the search tool (retries without it).
- Model role lives in `config.py` as `MODEL_DIAMOND`. Gemini equivalent is mapped in
  `common.GEMINI_MODELS`. Add new roles there, never as literals in pipeline code.

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

## Running locally

```bash
pip install -r requirements.txt
export GEMINI_API_KEY=...  LLM_PROVIDER=gemini
# or: export ANTHROPIC_API_KEY=...  LLM_PROVIDER=anthropic
python find_city_anomalies.py   # writes state/; emails if diamonds found + SMTP vars set
```

To test without sending email, leave SMTP vars unset — the `try/except` around the send
catches the `KeyError` and prints the error without crashing.

To test the two-stage gate offline: stub `common.llm` to return canned JSON, then run the
script and inspect `state/city_signals.md` and `state/signals_seen.json`.

## Known trade-offs (accepted — don't "fix" without asking)

- **No price data.** The diamond finder is pure LLM reasoning + web search. It can miss
  deals that don't appear in search results, and can hallucinate if search is weak. The
  two-stage gate exists to compensate.
- **Gemini + search:** `google_search` quality and behaviour differ from Anthropic's
  `web_search`. If Gemini rejects the tool the call retries without search — Stage 1 still
  runs, just from prior knowledge rather than live data.
- **30-day TTL:** a great deal that persists for more than a month will be suppressed after
  the first email. Acceptable given the "rare, act-now" framing.
- **Family-only scope.** Destinations that require arduous travel or are poor fits for a
  4-year-old are excluded by the skeptic prompt. This is intentional, not a filter bug.

## Out of scope (do not start without an explicit request)

- **Apify/hotel crawling** — see `_dormant/`. Requires Apify budget and significant
  restoration work. Not a near-term goal.
- **Flight data integration** — surface a hotel only when a cheap flight exists in-window.
- **Package operators** — scrape Bulgarian-market charter operators for unsold allocations.

## Style

Flat functions, plain stdlib + `requests`, clear names, short modules. Match the existing
tone. Prefer editing in place over adding files. Comment only the non-obvious (a hidden
constraint, a threshold's rationale, a workaround). No emoji in code; `city_signals.md`
and email HTML may use them.
