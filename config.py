"""Configuration for the diamond-finder pipeline. Edit freely."""

# ── City list ───────────────────────────────────────────────────────────────
# Cities the diamond finder uses as a search anchor. The LLM receives these as
# the preferred destinations but can extend to nearby or thematically related
# places when a confirmed opportunity exists (e.g. a cruise from a regional port).
#
# Values are (min_nights, max_nights). The diamond finder uses only the city
# names (CITIES.keys()); the night-range values are kept for reference and are
# used by the dormant Apify/hunt pipeline in _dormant/.
CITIES = {
    # --- Bulgaria, by car (<3h) ---
    "Asenovgrad, Bulgaria": (1, 3),   "Banya, Bulgaria": (2, 4),
    "Bansko, Bulgaria": (2, 7),       "Burgas, Bulgaria": (2, 7),
    "Chiflik, Bulgaria": (2, 4),      "Hisarya, Bulgaria": (2, 4),
    "Koprivshtitsa, Bulgaria": (1, 3), "Nessebar, Bulgaria": (2, 7),
    "Pazardzhik, Bulgaria": (1, 3),   "Pamporovo, Bulgaria": (2, 7),
    "Smolyan, Bulgaria": (2, 5),      "Sofia, Bulgaria": (1, 4),
    "Sozopol, Bulgaria": (2, 7),      "Stara Zagora, Bulgaria": (1, 3),
    "Veliko Tarnovo, Bulgaria": (2, 4),
    # --- Greece, by car ---
    "Alexandroupoli, Greece": (2, 5), "Kavala, Greece": (2, 5),
    "Komotini, Greece": (2, 4),       "Xanthi, Greece": (2, 4),
    # --- Turkey ---
    "Edirne, Turkey": (2, 3),         "Istanbul, Turkey": (3, 6),
    # --- by low-cost flight (min >= 2: a 1-night trip isn't worth the flight) ---
    "Bari, Italy": (3, 7),    "Milan, Italy": (2, 7),   "Naples, Italy": (3, 7),
    "Rome, Italy": (3, 7),    "Bratislava, Slovakia": (2, 7), "Vienna, Austria": (2, 7),
    "Athens, Greece": (3, 7), "Budapest, Hungary": (2, 7),   "Krakow, Poland": (3, 7),
    "Belgrade, Serbia": (2, 5), "London, United Kingdom": (3, 7),
    "Birmingham, United Kingdom": (3, 7), "Manchester, United Kingdom": (3, 7),
}

# ── LLM models ──────────────────────────────────────────────────────────────
# Model used for both Stage 1 (find + web search) and Stage 2 (skeptic).
# This is an Anthropic model name; the Gemini equivalent lives in GEMINI_MODEL_MAP.
MODEL_DIAMOND = "claude-sonnet-4-6"

# Maps Anthropic model names (the canonical identifiers used throughout the code)
# to their Gemini equivalents. Used when LLM_PROVIDER=gemini. Add a new entry
# here whenever a new model role is added to config; never hard-code Gemini model
# names anywhere else.
GEMINI_MODEL_MAP = {
    "claude-sonnet-4-6": "gemini-3.5-pro",
}

# ── LLM token budgets ────────────────────────────────────────────────────────
# Stage 1 (find) needs more room: it receives web-search grounding text and must
# reason across many candidate types before outputting a scored JSON list.
MAX_TOKENS_FIND    = 4000

# Stage 2 (skeptic) is a compact verdict-only call: keep/kill + one sentence per candidate.
MAX_TOKENS_SKEPTIC = 2000

# ── Web search ───────────────────────────────────────────────────────────────
# Maximum number of individual web-search tool uses allowed in a single Stage 1
# call (Anthropic provider only; Gemini's google_search has no per-call cap).
# Higher values improve grounding quality but increase latency and token cost.
WEB_SEARCH_MAX_USES = 6

# ── Diamond finder gate ──────────────────────────────────────────────────────
# Minimum score (0–100) a Stage 1 candidate must reach to be forwarded to the
# skeptic. Candidates below this threshold still appear in city_signals.md but
# never trigger email. Raise this to make email rarer; lower with caution.
STAGE1_MIN_SCORE = 80

# Maximum number of diamonds included in the single email sent per run. Because
# surviving Stage 2 is rare, this cap is almost never reached in practice.
MAX_EMAILS_PER_RUN = 3

# Days before the same destination+window pair can trigger another email.
# Prevents daily spam about a deal that persists for weeks.
SIGNAL_TTL_DAYS = 30

# ── LLM prompts ─────────────────────────────────────────────────────────────
# Placeholders filled at runtime by find_city_anomalies.py:
#   FIND_PROMPT    → {today}, {cities}
#   SKEPTIC_PROMPT → {today}, {min_score}, {candidates}
# Use {{...}} for literal braces in the JSON schema examples (Python .format() escaping).

FIND_PROMPT = """Today is {today}. You are a travel-arbitrage analyst helping a family of 3 \
(2 adults + a 4-year-old) based near Plovdiv, Bulgaria find genuine, actionable travel value \
right now. They have access to Plovdiv Airport (PDV) and Sofia airport (SOF) for flights, and can happily drive to destinations \
within ~3 hours (Bulgarian towns, Turkish/Greek). More than 3 hours is fine if the destination is genuinely exceptional. \
They are not looking for vague "discounts" or normal low-season pricing — they want concrete, verifiable, unusual value that a \
savvy traveller would act on today.


Hunt across ALL of these categories:
- Hotels/resorts: post-event price collapses, aggressive launch pricing, market-wide drops
- Seasonal resort closeouts: end-of-season fire sales with concrete prices, not vague "discounts"
- Post-event collapses: conventions, festivals, sporting events just ended, leaving unsold rooms
- Cruises: family-friendly itineraries departing from Istanbul, Athens, Thessaloniki, or other \
  ports reachable from Sofia — Eastern Med, Black Sea, Adriatic
- Flight error or sale fares: published (not expired) from SOF or nearby airports (OHD, VAR, BOJ, \
  SKP) to genuinely interesting destinations at dramatically below-normal prices
- Holiday package dumps: operators offloading unsold flight+hotel packages cheap
- Currency-driven cheapness: destinations where EUR goes significantly further than usual right now \
  due to recent currency moves

Anchor cities (strongly prefer these; extend to nearby or thematically related only if a real, \
confirmed opportunity exists): {cities}

Rules:
- USE WEB SEARCH. Every reason must cite something findable in the last 2-3 weeks.
- Do not invent prices, events, or sale windows. If you cannot confirm with search, omit it.
- Family-friendly is a hard requirement: the 4-year-old must be welcome and the destination sane \
  for a young child.
- Score 0-100 for how exceptional and actionable this is RIGHT NOW:
    90-100: rare, verifiable, time-sensitive — a savvy traveller would book today
    80-89:  clearly above-average deal with solid search evidence
    60-79:  interesting but uncertain or moderate value
    below 60: still include if you found something, so it appears in the MD log
- Include ALL candidates you found with evidence; lower-scoring ones appear in the daily log even \
  if they won't trigger email.

Return ONLY a JSON object (no prose, no markdown):
{{"candidates": [
  {{"destination": <city, cruise route, or package description>,
    "score": <0-100 integer>,
    "type": <"hotel"|"resort_closeout"|"post_event"|"cruise"|"flight_fare"|"package_dump"|"currency_arbitrage">,
    "window": <when to travel or book — specific dates preferred over vague ranges>,
    "reason": <2-3 sharp sentences; cite what search found; name prices, events, and sources>,
    "confidence": <"high"|"medium"|"low">}}
]}}
Order by score descending. Return an empty candidates list if nothing stands out."""


SKEPTIC_PROMPT = """You are a ruthlessly skeptical senior travel expert. Your job is to REJECT \
candidates unless they are genuinely exceptional — the kind of deal a frequent traveller who has \
seen hundreds of "deals" would immediately act on.

Today is {today}. Traveller profile: family of 3 (2 adults + 4-year-old), Plovdiv base, EUR \
spender, flies from Sofia (SOF), can drive ~3h. These candidates scored >= {min_score}/100 in a \
search pass. Most should still fail your review.

KILL if any one of these is true:
- The cheapness is just normal low-season pricing with nothing unusual about it
- Evidence is vague ("prices are lower") without a concrete price or verifiable source
- The saving is modest (< 30% off a reasonable normal rate for this destination)
- Window is too short to act on realistically (less than 72 hours from today)
- Poor fit for a 4-year-old (party destination, adults-only resort, remote or arduous travel)
- The flight connection required is long enough to erode the value for a short trip

KEEP only if: verifiable evidence, meaningful saving, realistic booking window, family-friendly.

Candidates to review:
{candidates}

Return ONLY a JSON array, one object per candidate, same order as input:
[{{"destination": <exact string from input>,
   "verdict": "keep"|"kill",
   "why": <one sharp sentence — keep: the specific thing that makes it genuinely exceptional; \
kill: the single fatal flaw>,
   "red_flags": <what to verify before booking; empty string if none>}}]
Be ruthless. An empty or mostly-kill result is correct most of the time."""
