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
  ├─ Memory load — state/memory.json
  │    Baselines (realistic prices from past verifications) + outcome ledger
  │    (past corrections and kills). Injected as {memory} into all three stage prompts.
  │
  ├─ Stage 1 (llm, want_search=True, model=MODEL_FIND)
  │    Score candidates 0–100 across: hotels, resort closeouts, post-event collapses,
  │    cruises, flight fares, package dumps, currency plays — anchored to CITIES but
  │    can extend to nearby destinations if a real opportunity exists.
  │
  ├─ Stage 2 (llm, want_search=False, model=MODEL_SKEPTIC) — candidates >= STAGE1_MIN_SCORE only
  │    Hostile skeptic reviewer. Returns keep/kill + why + red_flags.
  │    Checks relative discount AND absolute-value floor (see SKEPTIC_PROMPT Example 5).
  │    Most candidates should die here. Silence is correct.
  │
  ├─ Stage 3 (ground_deal seam, want_search=True, model=MODEL_VERIFY) — one call per Stage-2 survivor
  │    Concierge/verifier. Web-searches real prices at SPECIFIC bookable date windows
  │    (not month-wide minimums), corrects or kills hallucinations.
  │    Returns verdict: confirm | correct | kill, plus options[], how_to_book, grounding,
  │    assistant_summary, confidence. Merges verified fields onto surviving diamonds.
  │    Grounding is swappable: currently uses LLM concierge; Apify slots in here
  │    behind the same ground_deal signature (see verify_apify.py; credits renew 2026-07-26).
  │
  ├─ Memory write — state/memory.json + state/memory.md
  │    Every run (including silent days): record_baseline from verified prices,
  │    record_outcome for all Stage-3 candidates. Then prune() and save().
  │
  ├─ Anti-spam gate — state/signals_seen.json
  │    Keyed by destination+window, 30-day TTL. Prevents repeat emails.
  │    Only Stage-3 survivors (confirm/correct) reach this gate.
  │
  ├─ Email (common.send_email) — only if new diamonds survive all three stages
  │    One email per run, max MAX_EMAILS_PER_RUN diamonds.
  │    Conscience note in the email if monthly count >= 3.
  │
  └─ Always writes
       state/city_signals.json  — all Stage 1 candidates (hunt: false always)
       state/city_signals.md    — human-readable log with Stage 3 outcomes; useful even on silent days
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
| `find_city_anomalies.py` | The diamond finder — runs daily, emails on exceptional finds |
| `verify_apify.py` | Layer-3 Apify grounding stub (NOT YET WIRED — credits renew 2026-07-26) |
| `.github/workflows/daily.yml` | Runs the diamond finder at 06:00 UTC; commits `state/` |
| `state/city_signals.json` | Latest Stage 1 output (machine-readable) |
| `state/city_signals.md` | Stage 1–3 output (human-readable log with Stage 3 verification outcomes) |
| `state/signals_seen.json` | Anti-spam TTL memory: `destination\|window → date_emailed`, monthly count |
| `state/memory.json` | Price baselines + outcome ledger (grows every run, pruned at 200 entries / 180 days) |
| `state/memory.md` | Human-readable digest of memory.json |

## Apify grounding seam (future Phase 2)

`verify_apify.py` holds the NOT-yet-wired Layer-3 Apify grounding implementation.
Apify free credits renew **2026-07-26**. Do not call it before then.

The attach point in `find_city_anomalies.py` is the module-level seam:

```python
# current
ground_deal = _ground_llm

# to switch to Apify after 2026-07-26:
from verify_apify import apify_ground
ground_deal = apify_ground
```

`ground_deal(diamond, mem_text, today)` is called once per Stage-2 survivor.
Both implementations return the same Stage-3 result schema so the rest of the
pipeline is unchanged. `verify_apify.apify_ground()` documents the TODO steps
for parsing the LLM-generated `window` string into concrete Apify date calls.

Required secret: `APIFY_TOKEN` in GitHub repo secrets (already in the secret store).

## Critical invariants — do not break these

- **All LLM calls go through `common.llm()`.** Abstracts Anthropic vs Gemini via
  `LLM_PROVIDER`. Do not call provider HTTP endpoints directly.
- **All email goes through `common.send_email()`.** Single SMTP path. No duplication.
- **State files in `state/` are CI-managed.** `city_signals.json`, `city_signals.md`,
  `signals_seen.json`, `memory.json`, `memory.md` are committed after each run.
  They are real state, not scratch. Seed values: `{}` / `{"seen":{}, "monthly_count":{}}` /
  `{"baselines": {}, "ledger": []}`.
- **`STAGE1_MIN_SCORE = 80`** is the Stage 2 gate. Raise to make email rarer. Lower
  cautiously — it lets more through the hostile skeptic.
- **Stage 3 only removes candidates, never adds them.** A Stage-3 kill means the deal
  was hallucinated or unremarkable after live verification — it never triggers email.
- **Stage 3 `verdict: correct`** means the deal exists but the price was wrong; the
  corrected figures are emailed. `verdict: kill` means even the corrected reality doesn't
  justify the email. Both must be handled; do not treat `correct` as a kill.
- **Silence is the intended outcome most days.** Don't treat low email volume as a bug.
  Only investigate if the prompts demonstrably fail to surface known real opportunities.
- **`city_signals.json` always has `hunt: false`.** The diamond finder does not trigger
  Apify crawls. The field exists for schema compatibility only.
- **Memory is written every run**, including silent days. `memory.py` functions must
  not be called with None memory dict; always `M.load()` first.

## Providers

`common.llm(messages, model, max_tokens, want_search, provider=None)` — single entry point.

- `LLM_PROVIDER=anthropic` (default): Messages API; `want_search` → `web_search` tool.
- `LLM_PROVIDER=gemini`: `generateContent`; `want_search` → `google_search` tool.
  Degrades gracefully if Gemini rejects the search tool (retries without it).
- Per-stage model roles are in `config.py` as `MODEL_FIND`, `MODEL_SKEPTIC`, `MODEL_VERIFY`.
  Gemini equivalents are mapped in `GEMINI_MODEL_MAP`. Add new roles there, never as
  literals in pipeline code.
- Optional per-stage provider overrides: `PROVIDER_FIND / PROVIDER_SKEPTIC / PROVIDER_VERIFY`
  (all default to `None` = use global `LLM_PROVIDER`).

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
| `APIFY_TOKEN` | secret | Apify grounding — not used until 2026-07-26 |

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
  `web_search`. If Gemini rejects the tool the call retries without search — Stage 1 still
  runs, just from prior knowledge rather than live data.
- **30-day TTL:** a great deal that persists for more than a month will be suppressed after
  the first email. Acceptable given the "rare, act-now" framing.
- **Family-only scope.** Destinations that require arduous travel or are poor fits for a
  4-year-old are excluded by the skeptic prompt. This is intentional, not a filter bug.

## Out of scope (do not start without an explicit request)

- **Apify/hotel crawling** — `verify_apify.py` has the stub; credits renew 2026-07-26.
  Requires wiring `ground_deal = apify_ground` and implementing the TODO in `apify_ground()`.
- **Flight data integration** — surface a hotel only when a cheap flight exists in-window.
- **Package operators** — scrape Bulgarian-market charter operators for unsold allocations.

## Style

Flat functions, plain stdlib + `requests`, clear names, short modules. Match the existing
tone. Prefer editing in place over adding files. Comment only the non-obvious (a hidden
constraint, a threshold's rationale, a workaround). No emoji in code; `city_signals.md`
and email HTML may use them.
