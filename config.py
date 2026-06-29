"""Configuration for the diamond-finder pipeline. Edit freely."""

import os

# ── City list ───────────────────────────────────────────────────────────────
# Cities the diamond finder uses as a search anchor. The LLM receives these as
# the preferred destinations but can extend to nearby or thematically related
# places when a confirmed opportunity exists (e.g. a cruise from a regional port).
#
# Values are (min_nights, max_nights). The diamond finder uses only the city
# names (CITIES.keys()); the night-range values are kept for reference (and
# will be used by the Apify grounding layer when it is wired in).
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

# Cities grouped by transit tier — used to build the structured city block in
# FIND_PROMPT. Keeps country labels out of the individual city names so the LLM
# receives a clean, hierarchy-aware list instead of a flat comma-separated mess.
CITY_TIER_GROUPS = [
    ("Tier 1 — Drive ≤3h from Plovdiv or Direct PDV Flight", [
        ("Bulgaria", [
            "Asenovgrad", "Chepelare", "Velingrad", "Bansko", "Pamporovo", "Borovets", "Hisarya", 
            "Banya", "Burgas", "Sozopol", "Nessebar"
        ]),
        ("Greece (Drive)", [
            "Kavala", "Alexandroupoli", "Thassos"
        ]),
        ("Turkey (Drive)", [
            "Edirne", "Kirklareli"
        ]),
        ("Direct Flights from PDV", [
            "London", "Birmingham", "Manchester", "Milan", "Bratislava"
        ])
    ]),
    ("Tier 2 — Low-cost/Direct Flight from SOF", [
        ("Turkey", ["Istanbul", "Antalya", "Bodrum"]),
        ("Italy", ["Rome", "Bari", "Naples", "Bologna", "Venice"]),
        ("Greece", ["Athens", "Thessaloniki", "Chania", "Corfu"]),
        ("Central Europe", ["Vienna", "Budapest", "Prague", "Bratislava"]),
        ("Spain", ["Barcelona", "Madrid", "Valencia", "Malaga"]),
        ("Cyprus", ["Larnaca", "Paphos"]),
        ("Malta", ["Valetta"]),
        ("Germany", ["Munich", "Frankfurt", "Memmingen", "Berlin"]),
        ("France", ["Paris", "Nice"]),
        ("United Kingdom", ["London", "Bristol", "Edinburgh"])
    ]),
]


def cities_prompt_text():
    """Return cities formatted as a structured tier block for the find prompt."""
    lines = []
    for tier, regions in CITY_TIER_GROUPS:
        lines.append(f"{tier}:")
        for region, cities in regions:
            lines.append(f"  {region}: {', '.join(cities)}")
    return "\n".join(lines)


# ── LLM models ──────────────────────────────────────────────────────────────
# Per-stage model roles. Values are canonical Anthropic model names; Gemini
# equivalents are looked up in GEMINI_MODEL_MAP below.
MODEL_FIND    = "claude-haiku-4-5-20251001"  # Stage 1: fast + web-search capable
MODEL_SKEPTIC = "claude-sonnet-4-6"          # Stage 2: stronger reasoning
MODEL_VERIFY  = "claude-sonnet-4-6"          # Stage 3: strong + search-capable

# Maps Anthropic model names (canonical keys) to Gemini equivalents.
# Used when LLM_PROVIDER=gemini. Add a new entry here whenever a new model role
# is added; never hard-code Gemini model names anywhere else.
GEMINI_MODEL_MAP = {
    "claude-haiku-4-5-20251001": "gemini-3.5-flash",
    "claude-sonnet-4-6":         "gemini-3.1-pro-preview",
}

# Optional per-stage provider overrides. None = use the global LLM_PROVIDER env var.
# Set to "anthropic" or "gemini" to run a specific stage on a different provider.
PROVIDER_FIND    = None
PROVIDER_SKEPTIC = None
PROVIDER_VERIFY  = None

# ── LLM token budgets ────────────────────────────────────────────────────────
# Stage 1 (find) needs more room: it receives web-search grounding text and must
# reason across many candidate types before outputting a scored JSON list.
MAX_TOKENS_FIND    = 8000

# Stage 2 (skeptic) is a compact verdict-only call: keep/kill + one sentence per candidate.
# Must be large enough to absorb thinking-model overhead (gemini-3.5-flash uses ~2k tokens
# for internal reasoning before writing output — 2000 was not enough).
MAX_TOKENS_SKEPTIC = 8000

# Stage 3 (verify) does focused web search per surviving deal + structured output.
MAX_TOKENS_VERIFY = 8000

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

# ── Price ceilings ───────────────────────────────────────────────────────────
# A candidate priced above its country ceiling is, by definition, not a diamond
# regardless of star rating or framed discount. It is logged but NEVER emailed.
PRICE_CEILING_EUR = {"Bulgaria": 100, "Turkey": 100}
DEFAULT_PRICE_CEILING_EUR = 130  # rest of Europe (~+30%)


def get_price_ceiling(destination):
    """Return the applicable per-night price ceiling (EUR) for a destination string.
    Matches country names as substrings; falls back to DEFAULT_PRICE_CEILING_EUR."""
    dest_lower = (destination or "").lower()
    for country, ceiling in PRICE_CEILING_EUR.items():
        if country.lower() in dest_lower:
            return ceiling
    return DEFAULT_PRICE_CEILING_EUR


# ── Hotel grounding ─────────────────────────────────────────────────────────
HOTEL_PROVIDER      = os.environ.get("HOTEL_PROVIDER", "xotelo").strip().lower()
XOTELO_BASE_URL     = os.environ.get("XOTELO_BASE_URL", "https://data.xotelo.com/api")  # free, key-less
HOTEL_ADULTS        = 2
HOTEL_ROOMS         = 1
HOTEL_CHILDREN_AGES = [4]
HOTEL_CURRENCY      = "EUR"   # ALWAYS request EUR from /rates — Xotelo defaults to USD
HOTEL_HTTP_TIMEOUT  = 20
HOTEL_MAPPING = {
    # Optional override/cache for known or ambiguous hotels (skips /search):
    # "kempinski grand arena": {"key": "g304100-d556553", "name": "Kempinski Hotel Grand Arena Bansko"},
}


# ── LLM prompts ─────────────────────────────────────────────────────────────
# Placeholders filled at runtime by find_city_anomalies.py:
#   FIND_PROMPT    → {today}, {cities}, {memory}
#   SKEPTIC_PROMPT → {today}, {min_score}, {candidates}, {memory}
#   VERIFY_PROMPT  → {today}, {candidate}, {memory}
# Use {{...}} for literal braces in the JSON schema examples (Python .format() escaping).

FIND_PROMPT = """Today is {today}. You are a pragmatic, data-driven Travel Arbitrage Analyst. Your job is to perform live web searches to find 3-5 concrete, actionable travel opportunities for a family of 3 (2 adults, 1 child aged 4) based in Plovdiv, Bulgaria.

Your objective is to find high-utility value plays where a premium experience or location drops dramatically in price while maintaining high utility and comfort for a 4-year-old.

---

### PRIOR CORRECTIONS (from past pipeline runs — treat as ground truth; do not repeat past hallucinations)
{memory}

---

### SCORING CALIBRATION (Threshold: >= 80 Triggers an Email Alert)

Your scoring dictates the pipeline routing. A score of 80 or above means the deal is so strong it warrants immediately emailing the user. Be conservative.

- **Score 90-100 (Absolute No-Brainer):** Rare, highly actionable, massive price-to-utility disconnect. Zero logistical flaws.
- **Score 80-89 (High-Value Play):** Clearly above-average value with solid live evidence. Either a low-friction local play (Tier 1) with an excellent discount, or a higher-friction play (Tier 2) with a discount so steep it completely offsets the transit hassle.
- **Score 60-79 (Log Only - No Email):** Interesting low-season or standard budget pricing, but transit friction or utility loss doesn't justify interrupting the user.
- **Score Below 60:** Weak deals or unverified data included purely for logging purposes.

---

### GEOGRAPHIC & LOGISTICAL HIERARCHY

1. Tier 1: Local High Utility (Drive <= 3 hours from Plovdiv OR flights from Plovdiv Airport [PDV])
   - Baseline: Low transit friction for a 4-year-old. 
   - Evaluation: If an elite domestic spa resort (e.g., Velingrad, Bansko) or regional destination drops to entry-level pricing while keeping family infrastructure fully open, score it **80-95**.

2. Tier 2: High-Friction Transit (Flights from Sofia Airport [SOF] OR Drives > 3 hours)
   - Baseline: High transit friction for a 4-year-old. 
   - Evaluation: Standard discounts or normal cheap flights from SOF must be scored **below 80**. To cross the **80+ email threshold**, a Sofia transit option must offer a staggering price drop on a premium experience (e.g., a 5-star Antalya resort collapsing to €100/night with active indoor kid facilities).

Target destinations, grouped by transit tier (aligns with the scoring hierarchy above):
{cities}

---

### HUNTING CATEGORIES
Perform active web searches across these specific categories:
- Premium Off-Season Troughs: 4 to 5-star family resorts with massive predictable price drops post-holidays or between seasons where indoor/kids infrastructure remains fully open.
- Regional Cruises: Family-friendly itineraries departing from Istanbul, Athens (Piraeus), or Thessaloniki.
- Flight Error/Sale Fares: Confirmed active low fares from SOF or PDV.
- Package Dumps: Last-minute unsold flight+hotel bundles.

---

### SEARCH & VERIFICATION RULES
- YOU MUST USE THE WEB SEARCH TOOL. Every candidate must be backed by real pricing found on the live web within the last 48 hours.
- Never invent prices, hotel names, or flight availability. If you cannot find live, verifiable data for a target city, emit an empty list for that city.
- Check child-safety/amenities: Ensure any off-season resort has an operating indoor heated pool, kids' area, or relevant infrastructure active *during* the specified travel window.

---

### OUTPUT FORMAT
Return JSON only. Do not include markdown formatting or wrappers like ```json. Output a single JSON object matching the schema below.

Field notes:
- est_price_eur: your best estimate of the typical per-night price in EUR for this deal at this window — a single number (not a range, not a string). Used for price gating downstream.

JSON Schema:
{{
  "candidates": [
    {{
      "destination": "City, resort name, or cruise line/route",
      "score": 82,
      "type": "hotel",
      "window": "Specific exact dates or tight window (e.g., Jan 10-17)",
      "est_price_eur": 79,
      "reason": "Cite live prices found via search. Quantify the utility vs. price play (e.g., peak price vs current live price). State why it fits a 4-year-old.",
      "confidence": "high"
    }}
  ]
}}

If no verifiable deals meet these criteria today, return `{{"candidates": []}}`."""


SKEPTIC_PROMPT = """You are a pragmatic, high-utility Travel Value Analyst. Your job is to filter daily travel alerts and identify high-value plays/arbitrage opportunities for a young family. 

Your objective is to maintain a strict 5-10% acceptance rate (roughly 2-3 kept candidates per month out of 100+ inputs). You achieve this not by hunting for pricing glitches, but by ruthlessly eliminating options that lack massive, undeniable value or fail basic family logistics.

Today is {today}.
Target Demographics & Logistics:
- Party: Family of 3 (2 adults, 1 child aged 4). 
- Location Base: Plovdiv, Bulgaria.
- Transit Limits: Departure strictly from Plovdiv Airport (PDV), Sofia Airport (SOF), or a reasonable drive from Plovdiv.
- Currency: EUR.

Input Candidates (scored >= {min_score}/100 in preliminary filtering):
{candidates}

---

### PRIOR VERIFIED PRICES (from past pipeline runs — use for absolute-value calibration)
{memory}

---

### CONCRETE EXAMPLES FOR CALIBRATION

#### EXAMPLE 1: ANATOMY OF A "KEEP" (Off-Season High Utility)
- **Candidate:** Antalya, Turkey. 5-Star All-Inclusive Resort, mid-January after the NYE peak but before the spring break.
- **Price:** €100/night (Down from €400/night peak/NYE).
- **Analysis:** This matches predictable, standard historical low-season pricing for January. However, it is a KEEP. The weather is too cold for the beach, but the resort offers indoor heated pools, operating kids' clubs, and unlimited premium dining. The utility drop is only 20% and requires additional logistics/flights, but the price drop is 75%. This is an exceptional utility-to-price play for a 4-year-old.

#### EXAMPLE 2: ANATOMY OF A "KEEP" (Drive-Distance Luxury Play)
- **Candidate:** Bansko or Velingrad, Bulgaria. Luxury Spa Hotel, late October (Inter-season).
- **Price:** €80/night (Down from €250/night peak ski/winter season).
- **Analysis:** This is a KEEP. While there is no skiing in October, the indoor thermal pools, children's play areas, and massive price drop unlock a premium weekend getaway with zero flight logistics.

#### EXAMPLE 3: ANATOMY OF A "KILL" (The Low-Season Trap)
- **Candidate:** Sunny Beach, Bulgaria. 4-Star Beachfront Hotel, October.
- **Price:** €40/night (Down from €150/night July peak).
- **Analysis:** This is a KILL. While incredibly cheap, the utility drops to near zero: outdoor pools are freezing, kids' entertainment is completely closed, and the town is a ghost town. The price drop does not compensate for the complete loss of family utility.

#### EXAMPLE 4: ANATOMY OF A "KILL" (The Toddler Tax)
- **Candidate:** 7-Night Mediterranean Cruise (Athens to Venice/Ravenna) on Norwegian Pearl.
- **Price:** $449/person ($64/night) due to a last-minute cancellation sale.
- **Analysis:** This is a KILL. It is clearly an outstanding deal on paper. However, the open-jaw itinerary (Athens to Ravenna) creates a logistical and financial nightmare for a family with a 4-year-old, as flights from and back to Bulgaria will wipe out any cruise savings and then some.

#### EXAMPLE 5: ANATOMY OF A "KILL" (The Absolute-Value Trap)
- **Candidate:** Arte Spa & Park, Velingrad, Bulgaria. 4-star thermal spa resort.
- **Price:** €165/night (framed as "35% off peak" from €255/night).
- **Analysis:** This is a KILL. €165/night is a normal-to-high absolute price for a spa hotel in a Bulgarian spa town. Velingrad has many excellent thermal spa hotels at €60–120/night. The "discount from peak" framing is irrelevant — in absolute terms, €165/night for this market buys nothing exceptional. The family could stay at a comparable Velingrad spa property for €80–100/night. When the absolute price is unremarkable to anyone who knows the regional market, the relative discount is a fiction. Kill it.

---

### EVALUATION PROTOCOL

You must KEEP a candidate if it represents a High-Utility Value Play. This is defined as either:
- A predictable or seasonal price drop where the price plummets dramatically (e.g., peak €400 down to off-peak €100), but the core utility remains high for a family. 
- A rare, verifiable, time-sensitive opportunity where the price drop is massive and the logistics are manageable for a family with a 4-year-old.

You must ruthlessly KILL a candidate if it triggers any of the following:

1. The "False Value" Low-Season Trap:
   - A low price where the drop in utility matches or exceeds the drop in price. (e.g., a cheap waterpark resort when the waterparks are closed, or an outdoor beach holiday during a freezing/monsoon month with zero indoor amenities).
2. The "Toddler Tax" & Logistics Flaw:
   - Destinations requiring >4 hours total transit time (door-to-door from Plovdiv) or budget flights where unavoidable extras (cabin bags, family seat selection) kill any savings.
   - Places with steep topography, zero child-friendly infrastructure, or heavy logistical friction.
3. Hidden Cost Creep:
   - Cheap flights paired with predatory local accommodation rates, or a cheap hotel in a region where basic dining and transit costs erase the savings.
4. The Absolute-Value Floor:
   - Beyond any framed relative discount, ask: *"Is this price genuinely exceptional in absolute terms for what it is, to someone who knows the regional market?"*
   - A normal or high rate for an ordinary property in a cheap region (e.g., ~€165/night for a 4-star spa hotel in a Bulgarian spa town such as Velingrad or Hisarya) is a KILL, regardless of any claimed discount or peak-price anchoring.
   - The deal must be cheap in absolute terms for its category and geography, not just relatively cheap compared to a cherry-picked peak price.

---

### OUTPUT FORMAT
Return JSON only. Do not include markdown formatting or wrappers like ```json. Output a single JSON array containing one object per input candidate, maintaining the exact input order. 

JSON Schema:
[
  {{
    "destination": "Exact string from input",
    "verdict": "kill",
    "why": "One direct sentence highlighting the specific logistical flaw or why the price drop doesn't justify the loss in seasonal utility.",
    "red_flags": "Specific hidden cost or logistical risk to verify before booking."
  }},
  {{
    "destination": "Exact string from input",
    "verdict": "keep",
    "why": "One direct sentence quantifying the massive value play (e.g., premium amenities/infrastructure unlocked at entry-level pricing).",
    "red_flags": "What must be double-checked immediately (e.g., confirm indoor pool heating or kids club off-season hours)."
  }}
]

Remember: An empty or all-kill batch for today's run is the standard statistical outcome. Keep only the highest utility-to-price plays."""


VERIFY_PROMPT = """Today is {today}. You are a Personal Travel Concierge with live web-search access. One travel deal has survived a two-stage expert filter. Your job is to ground it in reality: find real prices at specific bookable dates, a real booking path, and produce an honest assistant-style summary.

Candidate deal to verify:
{candidate}

Prior price memory (corrections and baselines from past runs):
{memory}

---

### YOUR TASK

1. **Web-search the actual current price** for 1–3 SPECIFIC bookable sub-windows inside the deal's stated travel window — e.g., "Aug 8–10", "Aug 22–24" — not a month-wide minimum. Report price per night and total for each concrete window you check.
2. **Provide a booking path**: a direct URL if bookable online (booking.com, the property's own site, a tour operator); if not bookable online, explain how and where to book and cite the source that grounds the price (article, operator page, phone number).
3. **Critically re-check the claimed price.** If reality contradicts it (e.g., quoted at €72–95/night but Aug weekends are actually €186), set verdict=correct with the corrected figures. If the corrected price makes the deal unexceptional, set verdict=kill.
4. Write an `assistant_summary` in personal-assistant tone (1–3 sentences): what you found, what specific dates, what price, and the booking path.

### RULES
- Search for specific date windows — not a monthly minimum. A €72/night rate valid only for a single midweek Tuesday is not a family deal.
- If live search returned no useful price data (tool unavailable or search results empty), set confidence=low. Do NOT fabricate prices, dates, or booking URLs in that case.
- Never invent booking URLs. Include real URLs you actually found; set booking_url to null if you did not find one.
- If no live search was available (search tool absent or rejected), clearly flag this in assistant_summary and set confidence=low.

### OUTPUT
Return a single JSON object only. No markdown fences, no extra commentary outside the JSON.

{{
  "destination": "exact destination string from the candidate input",
  "verdict": "confirm",
  "options": [
    {{
      "dates": "Aug 8-10, 2026",
      "nights": 2,
      "price_per_night_eur": 79,
      "total_eur": 158,
      "booking_url": "https://www.booking.com/hotel/...",
      "source": "booking.com live search {today}"
    }}
  ],
  "how_to_book": "Book at the URL above. Alternatively call the property at +359 XX XXX XXXX.",
  "grounding": "What live evidence supports this: URLs searched, what was actually found, dates checked.",
  "assistant_summary": "I searched Regnum Bansko for Aug 8–10 and found Standard Rooms at €79/night (€158 total for 2 nights). Book directly at: https://...",
  "confidence": "high"
}}

verdict: confirm (deal is real and price holds as stated) | correct (deal exists but at different price or dates than claimed) | kill (hallucination, real price is unremarkable, or no supporting evidence found)
confidence: high (live search confirmed specific price and dates) | medium (indirect evidence, e.g. rate cards or press) | low (no live data available — do not fabricate)"""


# ── Response schemas (Gemini response_format) ────────────────────────────────
# Passed to _gemini() via llm(response_schema=...) to constrain output to valid
# JSON. The Anthropic path ignores these — prompt engineering suffices there.
# Keep in sync with the JSON schemas in FIND_PROMPT / SKEPTIC_PROMPT / VERIFY_PROMPT.

STAGE1_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "destination":   {"type": "string"},
                    "score":         {"type": "integer"},
                    "type":          {"type": "string"},
                    "window":        {"type": "string"},
                    "est_price_eur": {"type": "number"},
                    "reason":        {"type": "string"},
                    "confidence":    {"type": "string"},
                },
                "required": ["destination", "score", "type", "window", "est_price_eur", "reason", "confidence"],
            },
        },
    },
    "required": ["candidates"],
}

STAGE2_RESPONSE_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "destination": {"type": "string"},
            "verdict":     {"type": "string", "enum": ["keep", "kill"]},
            "why":         {"type": "string"},
            "red_flags":   {"type": "string"},
        },
        "required": ["destination", "verdict", "why", "red_flags"],
    },
}

STAGE3_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "destination":       {"type": "string"},
        "verdict":           {"type": "string", "enum": ["confirm", "correct", "kill"]},
        "options": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "dates":               {"type": "string"},
                    "nights":              {"type": "integer"},
                    "price_per_night_eur": {"type": "number"},
                    "total_eur":           {"type": "number"},
                    "booking_url":         {"type": "string"},
                    "source":              {"type": "string"},
                },
                "required": ["dates", "nights", "price_per_night_eur", "total_eur", "source"],
            },
        },
        "how_to_book":       {"type": "string"},
        "grounding":         {"type": "string"},
        "assistant_summary": {"type": "string"},
        "confidence":        {"type": "string", "enum": ["high", "medium", "low"]},
    },
    "required": ["destination", "verdict", "options", "how_to_book", "grounding", "assistant_summary", "confidence"],
}
