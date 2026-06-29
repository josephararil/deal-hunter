# Deal Hunter

Finds genuinely exceptional hotel travel windows for a family of 3 (2 adults + child aged 4)
based near Plovdiv, Bulgaria. One script, three LLM stages, live Booking.com rate verification.
Runs daily on free GitHub Actions. Emails immediately when something is found; silent otherwise.

## How it works

```
find_city_anomalies.py   (daily, three-stage LLM gate)
   │
   ├─ Stage 1 — find (web search)
   │     Scores hotel/resort/flight/cruise candidates 0–100.
   │     est_price_eur per candidate → ceiling gate (Bulgaria/Turkey €100; rest €130).
   │
   ├─ Stage 2 — skeptic (no search, hostile reviewer)
   │     Forwards only score ≥ 80 AND under-ceiling candidates.
   │     Default outcome is silence. Most candidates die here.
   │
   └─ Stage 3 — verify (Booking.com live rates → LLM fallback)
         providers.ground_api() fetches live nightly rates from Booking.com (apidojo RapidAPI).
         Fuzzy-matches the named hotel; falls back to LLM concierge + web search on any failure.
         verdict: confirm | correct | kill.
         confirm/correct → email (if confidence ≥ medium, grounded price ≤ ceiling, dates in window).
         kill → silence.
```

State files (`state/`) are committed back by CI after each run — no external database.

## Setup

1. Push this repo to GitHub.
2. Add secrets and variables under *Settings → Secrets and variables → Actions*:

   **Secrets** (encrypted):

   | Secret | What |
   |---|---|
   | `ANTHROPIC_API_KEY` | console.anthropic.com — required if `LLM_PROVIDER=anthropic` |
   | `GEMINI_API_KEY` | aistudio.google.com/apikey — required if `LLM_PROVIDER=gemini` |
   | `RAPIDAPI_KEY` | RapidAPI key for Booking.com (apidojo) hotel grounding |
   | `SMTP_HOST` / `SMTP_PORT` | e.g. `smtp.gmail.com` / `587` |
   | `SMTP_USER` / `SMTP_PASS` | sending address + app password (Gmail: 2FA → App Password) |
   | `EMAIL_TO` / `EMAIL_FROM` | recipient / sender (both default to `SMTP_USER`) |

   **Variables** (plain text, *Variables* tab in the same page):

   | Variable | Default | Effect |
   |---|---|---|
   | `LLM_PROVIDER` | `anthropic` | `anthropic` or `gemini` |
   | `HOTEL_PROVIDER` | `apidojo` | Set to `""` to force LLM-only Stage-3 (no Booking.com calls) |
   | `BOOKING_RAPIDAPI_HOST` | `apidojo-booking-v1.p.rapidapi.com` | Override the RapidAPI host |

3. Enable Actions. Test via *Actions → daily → Run workflow*.

> **LLM web search:** with `LLM_PROVIDER=anthropic`, Stage 1 and the LLM fallback use the
> Anthropic `web_search` tool — enable it in the console if needed. With `LLM_PROVIDER=gemini`
> it uses `google_search`; if Gemini rejects the tool, the call retries without search.

## Running locally

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=...  LLM_PROVIDER=anthropic  RAPIDAPI_KEY=...
# or: export GEMINI_API_KEY=...  LLM_PROVIDER=gemini  RAPIDAPI_KEY=...
python find_city_anomalies.py   # writes state/; emails if diamonds found + SMTP vars set
```

To skip Booking.com calls (LLM-only Stage 3):
```bash
HOTEL_PROVIDER="" python find_city_anomalies.py
```

To run offline unit tests for the grounding provider:
```bash
python test_providers.py           # apidojo: monkey-patched, no network
HOTEL_PROVIDER="" python test_stub.py  # full pipeline: LLM-only, stub llm()
```

## Tuning (config.py)

| Knob | Default | Effect |
|---|---|---|
| `STAGE1_MIN_SCORE` | 80 | Minimum score to forward to Stage 2. Raise to reduce email frequency. |
| `MAX_EMAILS_PER_RUN` | 3 | Cap on diamonds per email. |
| `SIGNAL_TTL_DAYS` | 30 | Anti-spam TTL per destination+window pair. |
| `PRICE_CEILING_EUR` | BG/TR: €100, rest: €130 | Hard per-night ceiling; over-ceiling deals are never emailed. |

## Cost

LLM calls are cheap at this volume (a few per day, Claude Haiku/Sonnet or Gemini).
Booking.com rate lookups via RapidAPI are one call per Stage-2 survivor (rare — typically
0–1 per day). The LLM fallback fires on any API failure.

See `CLAUDE.md` for full design rationale, pipeline invariants, and grounding seam details.
